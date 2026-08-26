"""
Task 1 test suite.

Tests cover:
  - input validation (empty / missing fields)
  - retrieval (returns chunks, handles no-match gracefully)
  - schema validation (enum enforcement)
  - API endpoint integration (mocked LLM)
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

KB_PATH = Path("knowledge-base")

# A minimal valid triage payload that mock LLM calls will return.
_VALID_LLM_RESPONSE = {
    "product_area": "Connectors",
    "issue_category": "Bug",
    "urgency": "P2",
    "reasoning": (
        "The ticket reports ERR_CONNECTION_TIMEOUT on the Connectors pipeline, "
        "which matches a known DataBridge Pro error. Production impact on 47 "
        "users warrants P2 urgency."
    ),
    "known_issue": True,
    "knowledge_base_document": "products/databridge-pro.md",
    "knowledge_base_reference": "Data Ingestion — Common errors",
    "recommended_responder_team": "Product Technical Support",
    "first_response": (
        "Thank you for reaching out. We can see your DataBridge Pro Connectors "
        "pipeline is reporting ERR_CONNECTION_TIMEOUT. Please check your firewall "
        "allowlist and source availability as outlined in our documentation. "
        "Could you confirm the pipeline ID and any recent configuration changes?"
    ),
}

# ---------------------------------------------------------------------------
# Schema / enum tests
# ---------------------------------------------------------------------------

class TestSchemas:
    def test_valid_ticket_request(self):
        from src.schemas import TicketRequest

        t = TicketRequest(subject="Pipeline broken", body="Getting timeout errors.")
        assert t.subject == "Pipeline broken"

    def test_empty_subject_rejected(self):
        from src.schemas import TicketRequest

        with pytest.raises(ValidationError):
            TicketRequest(subject="   ", body="Some body text.")

    def test_empty_body_rejected(self):
        from src.schemas import TicketRequest

        with pytest.raises(ValidationError):
            TicketRequest(subject="Subject", body="")

    def test_valid_triage_response(self):
        from src.schemas import TriageResponse

        r = TriageResponse.model_validate(_VALID_LLM_RESPONSE)
        assert r.urgency.value == "P2"
        assert r.issue_category.value == "Bug"
        assert r.product_area.value == "Connectors"
        assert r.known_issue is True
        assert r.knowledge_base_document is not None

    def test_invalid_category_rejected(self):
        from src.schemas import TriageResponse

        bad = {**_VALID_LLM_RESPONSE, "issue_category": "UnknownCategory"}
        with pytest.raises(ValidationError):
            TriageResponse.model_validate(bad)

    def test_invalid_urgency_rejected(self):
        from src.schemas import TriageResponse

        bad = {**_VALID_LLM_RESPONSE, "urgency": "P5"}
        with pytest.raises(ValidationError):
            TriageResponse.model_validate(bad)

    def test_invalid_product_area_rejected(self):
        from src.schemas import TriageResponse

        bad = {**_VALID_LLM_RESPONSE, "product_area": "DataBridge Pro — Connectors"}
        with pytest.raises(ValidationError):
            TriageResponse.model_validate(bad)

    def test_null_product_area_allowed(self):
        """Billing/cross-product tickets may have no product area."""
        from src.schemas import TriageResponse

        data = {**_VALID_LLM_RESPONSE, "product_area": None, "issue_category": "Billing"}
        r = TriageResponse.model_validate(data)
        assert r.product_area is None
        assert r.issue_category.value == "Billing"

    def test_known_issue_false_clears_kb_fields(self):
        from src.schemas import TriageResponse

        data = {
            **_VALID_LLM_RESPONSE,
            "known_issue": False,
            "knowledge_base_document": "products/databridge-pro.md",
            "knowledge_base_reference": "Some section",
        }
        r = TriageResponse.model_validate(data)
        assert r.knowledge_base_document is None
        assert r.knowledge_base_reference is None


# ---------------------------------------------------------------------------
# Retrieval tests
# ---------------------------------------------------------------------------

class TestRetrieval:
    @pytest.fixture(autouse=True)
    def _skip_if_no_kb(self):
        if not KB_PATH.exists():
            pytest.skip("knowledge-base directory not found")

    def test_kb_loads_chunks(self):
        from src.retrieval import load_kb

        kb = load_kb(KB_PATH)
        assert kb.total_chunks > 0

    def test_retrieve_returns_relevant_chunks(self):
        from src.retrieval import load_kb

        kb = load_kb(KB_PATH)
        chunks = kb.retrieve("DataBridge Pro connection timeout pipeline error")
        assert len(chunks) > 0
        # At least one chunk should be from a DataBridge or performance doc.
        text_corpus = " ".join(
            c.source_file + " " + c.content for c in chunks
        ).lower()
        assert "databridge" in text_corpus or "pipeline" in text_corpus or "timeout" in text_corpus

    def test_retrieve_no_match_returns_empty(self):
        from src.retrieval import load_kb

        kb = load_kb(KB_PATH)
        # Pure gibberish — all stop words filtered, no token overlap.
        chunks = kb.retrieve("xyzzy frobnicator quux 99999")
        assert chunks == []

    def test_retrieve_empty_query(self):
        from src.retrieval import load_kb

        kb = load_kb(KB_PATH)
        chunks = kb.retrieve("")
        assert chunks == []


# ---------------------------------------------------------------------------
# API endpoint tests (LLM mocked at the _call_gemini level)
# ---------------------------------------------------------------------------

@pytest.fixture()
def client():
    """
    TestClient with `src.triage._call_gemini` patched to return canned JSON.
    This avoids any network calls or API-key checks.
    """
    with patch(
        "src.triage._call_gemini",
        return_value=json.dumps(_VALID_LLM_RESPONSE),
    ):
        from src.main import app
        with TestClient(app) as c:
            yield c


class TestAPI:
    def test_health_endpoint(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "kb_chunks" in data

    def test_triage_valid_ticket(self, client):
        payload = {
            "subject": "Unable to connect DataBridge Pro to Connectors",
            "body": (
                "Our production pipeline has been failing since yesterday. "
                "Error: ERR_CONNECTION_TIMEOUT after 30s. "
                "47 users affected. Version 3.1.2."
            ),
        }
        resp = client.post("/triage", json=payload)
        assert resp.status_code == 200
        data = resp.json()

        # Required fields present
        required = {
            "product_area", "issue_category", "urgency", "reasoning",
            "known_issue", "recommended_responder_team", "first_response",
        }
        assert required.issubset(data.keys())

        # Enum constraints — compound strings must not appear
        from src.schemas import IssueCategory, ProductArea, UrgencyTier
        assert data["issue_category"] in [c.value for c in IssueCategory]
        assert data["urgency"] in [u.value for u in UrgencyTier]
        # product_area can be null or a valid enum value
        if data["product_area"] is not None:
            assert data["product_area"] in [a.value for a in ProductArea]
            assert "—" not in data["product_area"]

    def test_triage_empty_subject_rejected(self, client):
        resp = client.post("/triage", json={"subject": "", "body": "Some body."})
        assert resp.status_code == 422

    def test_triage_empty_body_rejected(self, client):
        resp = client.post("/triage", json={"subject": "Subject", "body": "  "})
        assert resp.status_code == 422

    def test_triage_missing_fields_rejected(self, client):
        resp = client.post("/triage", json={"subject": "Only subject"})
        assert resp.status_code == 422

    def test_triage_known_issue_false_no_kb_refs(self, client):
        """When LLM returns known_issue=false, KB fields must be null in response."""
        no_match_response = {
            **_VALID_LLM_RESPONSE,
            "known_issue": False,
            "knowledge_base_document": None,
            "knowledge_base_reference": None,
        }
        with patch(
            "src.triage._call_gemini",
            return_value=json.dumps(no_match_response),
        ):
            resp = client.post(
                "/triage",
                json={"subject": "Some obscure question", "body": "Nothing matches this."},
            )
        assert resp.status_code == 200
        data = resp.json()
        assert data["known_issue"] is False
        assert data["knowledge_base_document"] is None
        assert data["knowledge_base_reference"] is None
