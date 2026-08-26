"""Task 2: TAM Account Health Summariser.

Loads account and ticket history, detects account-health and churn signals,
and uses Gemini to synthesize a concise, actionable Technical Account Manager (TAM)
brief for QBR preparation with verified verbatim ticket evidence.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from google import genai
from google.genai import types as genai_types

from .config import settings
from .schemas import (
    AccountHealthResponse,
    EvidenceItem,
    RiskItem,
    SeverityTier,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data Loading & Caching
# ---------------------------------------------------------------------------

_ACCOUNTS_CACHE: Optional[Tuple[float, Dict[str, Dict[str, Any]]]] = None
_TICKETS_CACHE: Optional[Tuple[float, List[Dict[str, Any]]]] = None


def load_accounts(accounts_path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """Load accounts.json into an account_id -> account_dict map."""
    global _ACCOUNTS_CACHE
    path = accounts_path or settings.accounts_path
    mtime = path.stat().st_mtime if path.exists() else 0.0

    if _ACCOUNTS_CACHE is not None and _ACCOUNTS_CACHE[0] == mtime:
        return _ACCOUNTS_CACHE[1]

    if not path.exists():
        logger.warning("Accounts file not found at %s", path)
        return {}

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    account_map = {acc["account_id"]: acc for acc in data if "account_id" in acc}
    _ACCOUNTS_CACHE = (mtime, account_map)
    return account_map


def load_tickets(tickets_path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Load tickets.json."""
    global _TICKETS_CACHE
    path = tickets_path or settings.tickets_path
    mtime = path.stat().st_mtime if path.exists() else 0.0

    if _TICKETS_CACHE is not None and _TICKETS_CACHE[0] == mtime:
        return _TICKETS_CACHE[1]

    if not path.exists():
        logger.warning("Tickets file not found at %s", path)
        return []

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    _TICKETS_CACHE = (mtime, data)
    return data


def get_account(
    account_id: str,
    accounts_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Look up an account by its ID."""
    accounts = load_accounts(accounts_path)
    return accounts.get(account_id)


def _parse_iso_datetime(dt_str: str) -> Optional[datetime]:
    """Parse ISO datetime string into a timezone-aware UTC datetime."""
    try:
        clean_str = dt_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def get_dataset_max_ticket_date(tickets: List[Dict[str, Any]]) -> datetime:
    """Find the latest created_at timestamp in the tickets dataset."""
    max_dt = datetime.min.replace(tzinfo=timezone.utc)
    for t in tickets:
        created_at_str = t.get("created_at")
        if created_at_str:
            dt = _parse_iso_datetime(created_at_str)
            if dt and dt > max_dt:
                max_dt = dt
    if max_dt == datetime.min.replace(tzinfo=timezone.utc):
        return datetime.now(timezone.utc)
    return max_dt


def get_account_tickets(
    account_id: str,
    tickets_path: Optional[Path] = None,
    days: int = 90,
    reference_date: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """
    Retrieve and filter tickets for an account within the last 90 days.

    Sorts tickets deterministically by created_at descending and ticket_id ascending.
    """
    all_tickets = load_tickets(tickets_path)

    # Determine reference date for the 90-day window
    if reference_date is None:
        ref_dt = get_dataset_max_ticket_date(all_tickets)
    else:
        ref_dt = reference_date
        if ref_dt.tzinfo is None:
            ref_dt = ref_dt.replace(tzinfo=timezone.utc)
        else:
            ref_dt = ref_dt.astimezone(timezone.utc)

    cutoff = ref_dt - timedelta(days=days)

    account_tickets = []
    for t in all_tickets:
        if t.get("account_id") != account_id:
            continue
        created_at_str = t.get("created_at")
        if not created_at_str:
            continue
        dt = _parse_iso_datetime(created_at_str)
        if dt and dt > cutoff:
            account_tickets.append(t)

    # Deterministic sort: newest created_at first, then ticket_id
    account_tickets.sort(
        key=lambda item: (item.get("created_at", ""), item.get("ticket_id", "")),
        reverse=True,
    )
    return account_tickets


# ---------------------------------------------------------------------------
# Account Signals Detection
# ---------------------------------------------------------------------------

def detect_account_signals(
    account: Dict[str, Any],
    tickets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Extract deterministic account health and risk metrics."""
    signals: Dict[str, Any] = {
        "account_health_status": account.get("health_status", "Unknown"),
        "usage_trend": account.get("usage_trend", "Unknown"),
        "arr_usd": account.get("arr_usd"),
        "plan_tier": account.get("plan_tier"),
        "seats_licensed": account.get("seats_licensed", 0),
        "seats_active": account.get("seats_active", 0),
        "license_utilization_pct": 0.0,
        "nps_score": account.get("nps_score"),
        "days_since_last_login": account.get("last_login_days_ago"),
        "renewal_date": account.get("renewal_date"),
        "open_tickets_account_field": account.get("open_tickets", 0),
        "p1_tickets_last_30d_field": account.get("p1_tickets_last_30d", 0),
        "escalation_notes": account.get("escalation_notes") or [],
        "recent_tickets_count_90d": len(tickets),
        "urgency_counts": {},
        "category_counts": {},
        "p1_p2_tickets": [],
        "low_csat_tickets": [],
        "unresolved_tickets": [],
    }

    seats_lic = account.get("seats_licensed") or 0
    seats_act = account.get("seats_active") or 0
    if seats_lic > 0:
        signals["license_utilization_pct"] = round((seats_act / seats_lic) * 100, 1)

    for t in tickets:
        urg = t.get("urgency", "Unknown")
        cat = t.get("category", "Unknown")
        status = t.get("status", "Unknown")
        csat = t.get("satisfaction_score")

        signals["urgency_counts"][urg] = signals["urgency_counts"].get(urg, 0) + 1
        signals["category_counts"][cat] = signals["category_counts"].get(cat, 0) + 1

        if urg in ("P1", "P2"):
            signals["p1_p2_tickets"].append({
                "ticket_id": t.get("ticket_id"),
                "urgency": urg,
                "category": cat,
                "subject": t.get("subject"),
                "status": status,
                "created_at": t.get("created_at"),
            })

        if csat is not None and isinstance(csat, (int, float)) and csat <= 2:
            signals["low_csat_tickets"].append({
                "ticket_id": t.get("ticket_id"),
                "satisfaction_score": csat,
                "subject": t.get("subject"),
            })

        if status in ("Open", "In Progress", "Pending Customer"):
            signals["unresolved_tickets"].append({
                "ticket_id": t.get("ticket_id"),
                "status": status,
                "urgency": urg,
                "subject": t.get("subject"),
            })

    return signals


# ---------------------------------------------------------------------------
# LLM Prompt & Synthesis
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert Technical Account Management (TAM) Director.
Your task is to analyze an enterprise customer's account profile and their recent 90-day support ticket history, then generate a concise, highly actionable TAM QBR Brief.

You must output valid JSON strictly conforming to this structure:
{
  "account_id": "<account_id>",
  "company": "<company_name>",
  "executive_summary": "<3 to 5 concise sentences describing overall account health, synthesizing account metrics and ticket trends>",
  "open_risks": [
    {
      "risk": "<Concise risk title>",
      "severity": "High" | "Medium" | "Low",
      "reason": "<Specific reason explaining the signal or risk using cautious, factual language>",
      "evidence": {
        "ticket_id": "<TKT-XXXXX or null if account-level signal>",
        "quote": "<Exact verbatim quote from the ticket body or null if account-level signal>"
      }
    }
  ],
  "talking_points": [
    "<Actionable topic 1 for the TAM to discuss at the QBR>",
    "<Actionable topic 2>",
    "<Actionable topic 3>"
  ]
}

CRITICAL RULES — DO NOT OVER-INFER ACCOUNT DATA:
1. Treat account fields literally:
   - "last_login_days_ago" is an account-level field meaning the number of days since the account's last recorded login across the organization. Do NOT interpret it as the primary contact's individual login. Do NOT say "zero logins in X days". Do NOT infer individual-user behavior from account-level fields.
2. NPS score description:
   - Describe NPS simply as "NPS score" (or "recorded NPS score") unless the source explicitly provides when it was collected. Do not speculate on collection dates.
3. Do not infer operational stability merely because there are no recent tickets:
   - If there are zero or few recent tickets, do NOT claim or infer "operational stability" or "smooth day-to-day operations". Say "limited recent support activity" instead.
4. Renewal date evaluation and phrasing:
   - Do NOT automatically classify a passed renewal date as a Medium or High risk. Treat it as a Low-level review item or talking point unless other supplied data shows actual renewal/churn risk.
   - If "renewal_date" has passed relative to the execution date, say: "The recorded renewal date has passed; the TAM should confirm the current agreement status."
   - Do NOT imply that the customer failed to renew or is at risk without evidence.
   - Do NOT describe a renewal date as upcoming, ahead, or future unless it is actually in the future relative to the execution date.
   - "last_qbr_date" only tells us the previous QBR date, not a scheduled future one.
5. Severity calibration for account-level risks:
   - For account-level risks, do NOT assign Medium or High severity unless the supplied account or ticket evidence clearly supports that level.
   - Healthy accounts with negative signals such as low NPS, inactive usage, or login recency should normally be Low severity or a talking point unless additional evidence indicates material business risk.
6. Do not automatically classify a signal as a risk just because it exists:
   - Accounts with "Healthy" status and "Increasing" usage should NOT be artificially flagged as at risk. If an account is genuinely healthy and has no recent negative signals, "open_risks" can be empty ([]) or contain only genuine, evidence-backed items.
   - Low seat utilization is a potential exploratory talking point / discussion item, but do NOT classify it as a risk unless the data provides supporting negative evidence.
   - Synthesize NPS, usage trend, login recency, and seat utilization together holistically.
7. Do not invent customer objectives or business targets:
   - Do NOT invent customer objectives. Replace generic/speculative statements such as "Explore how <Product> is supporting their current infrastructure objectives" with evidence-based wording such as "Review current <Product> usage and identify any priorities or challenges for the next period."
   - Do NOT assume 100% seat utilization is required.
   - Do NOT assume a customer must expand integrations or adopt additional products.
   - Do NOT invent arbitrary adoption targets unless supported by the data.
8. Use cautious, professional language for inferred risks:
   - Prefer: "This may warrant TAM attention.", "This is a signal to investigate.", "This could indicate..."
   - Avoid alarmist language like "Critical risk" or "Severe disengagement" unless the supplied data clearly supports that severity.
9. Never turn an inference into a fact:
   - Strictly distinguish between account facts, ticket evidence, reasonable inferences, and recommended talking points.
10. Required 3 sections:
   - Executive Summary: 3 to 5 concise sentences synthesizing health status, ARR, utilization, and ticket history without copying raw field dumps.
   - Open Risks & Flagged Issues: meaningful risks and escalation signals with severity, reason, and evidence.
   - Recommended Talking Points: actionable topics derived from detected risks and account context.
11. Strict Evidence & Verbatim Quotes:
   - For ticket-derived churn/escalation risks, always provide the actual "ticket_id" and an EXACT, VERBATIM quote copied word-for-word from that ticket's body. NEVER fabricate, summarize, or paraphrase a quote.
   - If a risk is derived from account-level signals (e.g., escalation notes, NPS), set ticket_id to null and quote to null.
"""


def _build_tam_prompt(
    account: Dict[str, Any],
    tickets: List[Dict[str, Any]],
    signals: Dict[str, Any],
    execution_date: Optional[datetime] = None,
) -> str:
    """Build user message containing account context, signals, and tickets."""
    exec_dt = execution_date or datetime.now(timezone.utc)
    current_date_str = exec_dt.strftime("%Y-%m-%d")

    ticket_blocks = []
    for t in tickets:
        body_snippet = (t.get("body") or "").strip()
        ticket_blocks.append(
            f"--- Ticket {t.get('ticket_id')} ---\n"
            f"Subject: {t.get('subject')}\n"
            f"Product: {t.get('product')} ({t.get('product_area')})\n"
            f"Category: {t.get('category')} | Urgency: {t.get('urgency')} | Status: {t.get('status')}\n"
            f"Created: {t.get('created_at')} | CSAT: {t.get('satisfaction_score')}\n"
            f"Body:\n{body_snippet}\n"
        )

    tickets_str = "\n".join(ticket_blocks) if ticket_blocks else "No tickets in the last 90 days."

    account_json_str = json.dumps(account, indent=2)
    signals_json_str = json.dumps(signals, indent=2)

    return f"""\
CURRENT EXECUTION DATE: {current_date_str}

ACCOUNT DETAILS:
{account_json_str}

DERIVED ACCOUNT SIGNALS & METRICS:
{signals_json_str}

RECENT 90-DAY TICKET HISTORY ({len(tickets)} tickets):
{tickets_str}

Please generate the TAM account health brief following the exact JSON schema and strict rules.
"""


def _call_gemini_account_health(prompt: str) -> str:
    """Call Gemini with temperature=0.0 for deterministic TAM brief synthesis."""
    client = genai.Client(api_key=settings.require_api_key())
    config = genai_types.GenerateContentConfig(
        system_instruction=_SYSTEM_PROMPT,
        temperature=0.0,
        response_mime_type="application/json",
    )

    models_to_try = [settings.gemini_model]
    if "flash-lite" not in settings.gemini_model:
        models_to_try.append("gemini-3.5-flash-lite")

    _RETRYABLE = ("503", "UNAVAILABLE", "429", "RESOURCE_EXHAUSTED")
    max_retries = 2
    delay = 1.5

    last_exc: Optional[Exception] = None
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
# Quote Validation & Sanitization
# ---------------------------------------------------------------------------

def _normalize_text(s: str) -> str:
    """Normalize whitespace for fuzzy matching verbatim quotes."""
    return " ".join(s.split()).lower()


def validate_and_sanitize_brief(
    raw_data: Dict[str, Any],
    account: Dict[str, Any],
    tickets: List[Dict[str, Any]],
) -> AccountHealthResponse:
    """
    Validate and clean the LLM generated brief.

    Ensures:
    1. Direct quotes exist verbatim in the referenced ticket body/subject.
    2. Invalid or hallucinated quotes are stripped to preserve integrity.
    3. Proper Pydantic schema structure is returned.
    """
    ticket_map = {t["ticket_id"]: t for t in tickets if "ticket_id" in t}

    cleaned_risks: List[RiskItem] = []
    raw_risks = raw_data.get("open_risks") or []

    for r in raw_risks:
        if not isinstance(r, dict):
            continue
        risk_name = str(r.get("risk", "Identified Risk")).strip()
        sev_raw = str(r.get("severity", "Medium")).strip().capitalize()
        severity = SeverityTier.MEDIUM
        if sev_raw in ("High", "Medium", "Low"):
            severity = SeverityTier(sev_raw)

        reason = str(r.get("reason", "")).strip()

        evidence_raw = r.get("evidence")
        evidence_obj: Optional[EvidenceItem] = None

        if isinstance(evidence_raw, dict):
            t_id = evidence_raw.get("ticket_id")
            quote = evidence_raw.get("quote")

            if t_id and quote and isinstance(t_id, str) and isinstance(quote, str):
                t_id = t_id.strip()
                quote = quote.strip()
                source_ticket = ticket_map.get(t_id)
                if source_ticket:
                    body = source_ticket.get("body") or ""
                    subj = source_ticket.get("subject") or ""
                    if quote in body or quote in subj or _normalize_text(quote) in _normalize_text(body + " " + subj):
                        evidence_obj = EvidenceItem(ticket_id=t_id, quote=quote)
                    else:
                        logger.warning(
                            "Quote in risk %r not found verbatim in ticket %s; sanitizing quote.",
                            risk_name, t_id,
                        )
                        evidence_obj = EvidenceItem(ticket_id=t_id, quote=None)
                else:
                    logger.warning("Risk %r referenced unknown ticket_id %s; sanitizing evidence.", risk_name, t_id)
                    evidence_obj = None

        cleaned_risks.append(
            RiskItem(
                risk=risk_name,
                severity=severity,
                reason=reason,
                evidence=evidence_obj,
            )
        )

    raw_tp = raw_data.get("talking_points") or []
    talking_points = [str(tp).strip() for tp in raw_tp if str(tp).strip()]
    if not talking_points:
        talking_points = [
            f"Review account health status ({account.get('health_status', 'Unknown')}) and current usage trends.",
            f"Discuss renewal timeline (scheduled for {account.get('renewal_date', 'upcoming')}).",
            "Review resolution of recent support tickets and platform stability.",
        ]

    exec_summary = str(raw_data.get("executive_summary", "")).strip()
    if not exec_summary:
        company = account.get("company", "The customer")
        health = account.get("health_status", "Stable")
        renewal_str = f"scheduled renewal date of {account.get('renewal_date', 'N/A')}"
        exec_summary = (
            f"{company} is currently categorized as {health} with limited recent support activity "
            f"({len(tickets)} tickets in the last 90 days). "
            f"The account records {account.get('seats_active', 0)} active seats out of {account.get('seats_licensed', 0)} licensed. "
            f"The TAM should review current platform engagement and verify renewal status ({renewal_str})."
        )

    return AccountHealthResponse(
        account_id=account.get("account_id", raw_data.get("account_id", "")),
        company=account.get("company", raw_data.get("company", "")),
        executive_summary=exec_summary,
        open_risks=cleaned_risks,
        talking_points=talking_points,
    )


# ---------------------------------------------------------------------------
# Public Entry Point
# ---------------------------------------------------------------------------

def summarize_account_health(
    account_id: str,
    accounts_path: Optional[Path] = None,
    tickets_path: Optional[Path] = None,
    days: int = 90,
    reference_date: Optional[datetime] = None,
) -> AccountHealthResponse:
    """
    Generate a TAM account-health brief for an account.

    Raises:
        KeyError: if the account does not exist in accounts.json.
        RuntimeError: on LLM API or JSON parsing failure.
    """
    account = get_account(account_id, accounts_path=accounts_path)
    if account is None:
        raise KeyError(f"Account '{account_id}' not found in accounts dataset.")

    tickets = get_account_tickets(
        account_id,
        tickets_path=tickets_path,
        days=days,
        reference_date=reference_date,
    )

    signals = detect_account_signals(account, tickets)
    prompt = _build_tam_prompt(
        account,
        tickets,
        signals,
        execution_date=reference_date,
    )

    raw_text = _call_gemini_account_health(prompt)

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        logger.error("LLM returned non-JSON text: %r", raw_text)
        raise RuntimeError(f"LLM returned invalid JSON: {exc}") from exc

    return validate_and_sanitize_brief(data, account, tickets)
