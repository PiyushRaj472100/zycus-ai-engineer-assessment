"""
Triage logic: takes a ticket + retrieved KB context and calls Gemini to
produce a validated TriageResponse.

Uses the google-genai SDK (>= 1.0).
"""
from __future__ import annotations

import json
import logging
from typing import List

import google.genai as genai
import google.genai.types as genai_types

from .config import settings
from .retrieval import Chunk
from .schemas import IssueCategory, ProductArea, TriageResponse

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Responder routing table (deterministic — LLM cannot override this)
# ---------------------------------------------------------------------------

_RESPONDER_MAP: dict[str, str] = {
    IssueCategory.BILLING: "Billing Support",
    IssueCategory.ONBOARDING: "Onboarding Support",
    IssueCategory.INTEGRATION: "Integration Support",
    IssueCategory.PERFORMANCE: "Technical Support",
    IssueCategory.DATA_LOSS: "Data Recovery Team",
    IssueCategory.BUG: "Product Technical Support",
    IssueCategory.FEATURE_REQUEST: "Product Engineering",
    IssueCategory.HOW_TO: "Technical Support",
}


def _route_responder(category: IssueCategory) -> str:
    return _RESPONDER_MAP.get(category, "Technical Support")


# ---------------------------------------------------------------------------
# Controlled vocabulary strings for the prompt
# ---------------------------------------------------------------------------

_PRODUCT_AREA_LIST = "\n".join(f"- {a.value}" for a in ProductArea)

_CATEGORY_LIST = " | ".join(c.value for c in IssueCategory)

# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def _build_kb_context(chunks: List[Chunk]) -> str:
    if not chunks:
        return "No relevant knowledge-base content was found for this ticket."
    parts = []
    for chunk in chunks:
        parts.append(
            f"[KB: {chunk.source_file} — {chunk.heading}]\n{chunk.content}"
        )
    return "\n\n---\n\n".join(parts)


_SYSTEM_PROMPT = f"""\
You are a support-ticket triage assistant for a B2B SaaS platform.
Analyse the incoming ticket and return a structured JSON triage result.

═══════════════════════════════════════════════════════════
CONTROLLED VOCABULARY — you MUST use exact values from these lists.
═══════════════════════════════════════════════════════════

## Allowed product areas (use exact string for "product_area" field)
{_PRODUCT_AREA_LIST}

Product areas belong to specific products:
- DataBridge Pro: Data Ingestion, Schema Management, Pipeline Monitoring, Connectors, API
- CloudSync: File Sync, Conflict Resolution, Permissions, Bandwidth Limits, Integrations
- AnalyticsHub: Dashboard, Reports, Data Sources, Alerts, Exports
- SecureVault: Authentication, Encryption, Audit Logs, Key Management, SSO
- WorkflowEngine: Triggers, Actions, Scheduling, Error Handling, Templates

## Allowed issue categories (use exact string for "issue_category" field)
{_CATEGORY_LIST}

## Allowed urgency tiers (use exact string for "urgency" field)
P1 — critical, production stopped, business cannot operate
P2 — major impact, significant workaround needed
P3 — moderate impact, workaround available
P4 — low impact, cosmetic or minor question

═══════════════════════════════════════════════════════════
OUTPUT FORMAT — return ONLY this JSON object, no fences, no preamble
═══════════════════════════════════════════════════════════

{{
  "product_area": "<exact area from allowed list, or null if not applicable>",
  "issue_category": "<exact category from allowed list>",
  "urgency": "<P1|P2|P3|P4>",
  "reasoning": "<2–4 sentences grounded only in the ticket text and KB content>",
  "known_issue": <true|false>,
  "knowledge_base_document": "<source file path, or null>",
  "knowledge_base_reference": "<specific section or heading, or null>",
  "recommended_responder_team": "<team name>",
  "first_response": "<draft response — see strict rules below>"
}}

═══════════════════════════════════════════════════════════
STRICT RULES
═══════════════════════════════════════════════════════════

### product_area
- Must be one of the exact allowed area names above. Never invent or combine with a product name.
  ✓ CORRECT: "product_area": "Connectors"
  ✗ WRONG:   "product_area": "DataBridge Pro — Connectors"
- Set product_area to null when the ticket does not clearly belong to a specific product module.
  Billing, Onboarding, and cross-product authentication tickets frequently have no product area.
  Do NOT force a product area just to fill the field.
  ✓ CORRECT: Billing ticket → "product_area": null
  ✗ WRONG:   Billing ticket → "product_area": "Permissions"

### known_issue
- Set known_issue=true ONLY when the retrieved KB content describes the SAME
  error code, symptom, or problem pattern as the ticket.
  A vaguely related or broadly relevant document is NOT sufficient.
  ✓ Ticket mentions ERR_CONNECTION_TIMEOUT → KB has a section on ERR_CONNECTION_TIMEOUT → true
  ✓ Ticket is about seat billing charge → KB has a "How Seat Billing Works" section → true
  ✗ Ticket mentions data loss → KB has a general product section → false
- If known_issue=false, knowledge_base_document and knowledge_base_reference MUST be null.

### reasoning
- Base reasoning only on facts in the ticket and KB. Do not infer causes or actions
  not evidenced by the text.

### first_response
- Acknowledge the customer's reported problem using only their own words.
- Reference KB-supported next steps only if the KB clearly provides them.
- NEVER claim any of the following unless the system actually performed it:
    • that a ticket, feature request, investigation, escalation, or refund has been created or logged
    • that the team is investigating or has reproduced the issue
    • that a fix, credit, or timeline will be provided
    • internal actions ("our engineers are reviewing…", "we are escalating…", "I have logged…")
    • product capabilities or facts not present in the KB or ticket
- Use language that describes the customer's situation, not actions taken:
    ✓ "We understand your request for bulk import operations…"
    ✗ "I have logged this feature request with our product team…"
- Keep to 3–5 sentences. End with a clear ask for any information needed.
"""


def _build_user_message(subject: str, body: str, kb_context: str) -> str:
    return (
        f"## Ticket\n\nSubject: {subject}\n\nBody:\n{body}\n\n"
        f"## Retrieved Knowledge-Base Context\n\n{kb_context}\n\n"
        "Return the JSON triage result. Use only the allowed vocabulary values."
    )


# ---------------------------------------------------------------------------
# Gemini call (google-genai SDK)
# ---------------------------------------------------------------------------

def _call_gemini(prompt: str) -> str:
    """Call Gemini and return the raw text response.

    Retries with exponential backoff on transient errors and falls back
    to gemini-3.5-flash-lite if the primary model encounters high demand.
    """
    import time

    client = genai.Client(api_key=settings.require_api_key())
    config = genai_types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        temperature=0.1,
        response_mime_type="application/json",
    )

    models_to_try = [settings.gemini_model]
    if "flash-lite" not in settings.gemini_model:
        models_to_try.append("gemini-3.5-flash-lite")

    _RETRYABLE = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")
    max_retries = 2
    delay = 1.5

    last_exc = None
    for model_name in models_to_try:
        for attempt in range(max_retries + 1):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=config,
                )
                return response.text
            except Exception as exc:
                last_exc = exc
                is_retryable = any(code in str(exc) for code in _RETRYABLE)
                if is_retryable and attempt < max_retries:
                    wait = delay * (2 ** attempt)
                    logger.warning(
                        "Gemini transient error on %s (attempt %d/%d), retrying in %.1fs: %s",
                        model_name, attempt + 1, max_retries, wait, exc,
                    )
                    time.sleep(wait)
                else:
                    break

    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Gemini API call failed: no models attempted or no response returned")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def triage_ticket(
    subject: str,
    body: str,
    kb_chunks: List[Chunk],
) -> TriageResponse:
    """
    Run LLM triage for *subject* / *body* given the pre-retrieved *kb_chunks*.

    Raises:
        RuntimeError: on LLM API failure or invalid response structure.
    """
    kb_context = _build_kb_context(kb_chunks)
    prompt = _build_user_message(subject, body, kb_context)

    try:
        raw_text = _call_gemini(prompt)
    except RuntimeError:
        raise
    except Exception as exc:
        logger.error("Gemini API call failed: %s", exc)
        raise RuntimeError(f"LLM API error: {exc}") from exc

    # Parse JSON response.
    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("LLM returned invalid JSON: %s", raw_text[:500])
        raise RuntimeError(f"LLM returned unparseable JSON: {exc}") from exc

    # Override the responder team with our deterministic routing — LLM cannot invent teams.
    try:
        category = IssueCategory(data.get("issue_category", ""))
    except ValueError:
        category = IssueCategory.BUG  # safe fallback

    data["recommended_responder_team"] = _route_responder(category)

    # Validate with Pydantic — enums reject any value not in the controlled vocabulary.
    try:
        return TriageResponse.model_validate(data)
    except Exception as exc:
        logger.error("TriageResponse validation failed: %s | data=%s", exc, data)
        raise RuntimeError(f"LLM response failed schema validation: {exc}") from exc
