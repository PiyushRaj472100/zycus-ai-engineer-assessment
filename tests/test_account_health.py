"""Tests for Task 2: TAM Account Health Summariser."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from src.account_health import (
    detect_account_signals,
    get_account,
    get_account_tickets,
    get_dataset_max_ticket_date,
    summarize_account_health,
    validate_and_sanitize_brief,
)
from src.main import app
from src.schemas import (
    AccountHealthResponse,
    EvidenceItem,
    RiskItem,
    SeverityTier,
)


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# Unit tests: Data loading & filtering
# ---------------------------------------------------------------------------

def test_get_account_existing():
    acc = get_account("ACC-3336")
    assert acc is not None
    assert acc["account_id"] == "ACC-3336"
    assert acc["company"] == "Omni Consumer Products"
    assert acc["health_status"] == "At Risk"


def test_get_account_nonexistent():
    acc = get_account("ACC-NONEXISTENT-999")
    assert acc is None


def test_get_account_tickets_90_day_filter():
    # Ticket TKT-10293 is on 2026-05-20 (within 90 days of max date 2026-05-22)
    tickets = get_account_tickets("ACC-3336")
    assert len(tickets) == 1
    assert tickets[0]["ticket_id"] == "TKT-10293"


def test_get_account_tickets_older_than_90_days():
    # Two tickets in dataset are older than 90 days: TKT-10235 and TKT-10402 (2026-02-20)
    # TKT-10235 has account_id ACC-6467
    ref_date = datetime(2026, 5, 22, 0, 23, 32, tzinfo=timezone.utc)
    tickets = get_account_tickets("ACC-6467", reference_date=ref_date)
    # Since TKT-10235 is 91 days prior, it should be excluded
    assert len(tickets) == 0


def test_get_account_tickets_deterministic_sort():
    mock_tickets = [
        {"account_id": "ACC-TEST", "ticket_id": "TKT-1", "created_at": "2026-05-01T10:00:00Z"},
        {"account_id": "ACC-TEST", "ticket_id": "TKT-2", "created_at": "2026-05-10T10:00:00Z"},
        {"account_id": "ACC-TEST", "ticket_id": "TKT-3", "created_at": "2026-05-05T10:00:00Z"},
    ]
    with patch("src.account_health.load_tickets", return_value=mock_tickets):
        res = get_account_tickets("ACC-TEST", reference_date=datetime(2026, 5, 15, tzinfo=timezone.utc))
        assert len(res) == 3
        # Should sort descending by created_at
        assert [t["ticket_id"] for t in res] == ["TKT-2", "TKT-3", "TKT-1"]


# ---------------------------------------------------------------------------
# Unit tests: Signal detection
# ---------------------------------------------------------------------------

def test_detect_account_signals():
    account = {
        "account_id": "ACC-3336",
        "company": "Omni Consumer Products",
        "health_status": "At Risk",
        "usage_trend": "Inactive",
        "arr_usd": 500000,
        "seats_licensed": 1000,
        "seats_active": 800,
        "nps_score": 4,
        "open_tickets": 5,
        "p1_tickets_last_30d": 1,
        "escalation_notes": ["Executive escalation regarding latency"],
    }
    tickets = [
        {
            "ticket_id": "TKT-101",
            "urgency": "P1",
            "category": "Bug",
            "status": "In Progress",
            "satisfaction_score": 1,
            "subject": "System crash",
        },
        {
            "ticket_id": "TKT-102",
            "urgency": "P3",
            "category": "How-To",
            "status": "Closed",
            "satisfaction_score": 5,
            "subject": "Config question",
        },
    ]
    signals = detect_account_signals(account, tickets)
    assert signals["account_health_status"] == "At Risk"
    assert signals["license_utilization_pct"] == 80.0
    assert signals["recent_tickets_count_90d"] == 2
    assert signals["urgency_counts"]["P1"] == 1
    assert len(signals["p1_p2_tickets"]) == 1
    assert len(signals["low_csat_tickets"]) == 1
    assert len(signals["unresolved_tickets"]) == 1


# ---------------------------------------------------------------------------
# Unit tests: Quote validation & sanitization
# ---------------------------------------------------------------------------

def test_validate_and_sanitize_brief_verbatim_quote():
    account = {"account_id": "ACC-3336", "company": "Omni Consumer Products"}
    tickets = [
        {
            "ticket_id": "TKT-10293",
            "subject": "DataBridge Pro running extremely slowly",
            "body": "Page loads are taking 119+ seconds and API operations are timing out.",
        }
    ]
    raw_data = {
        "account_id": "ACC-3336",
        "company": "Omni Consumer Products",
        "executive_summary": "Account is at risk due to critical performance issues with DataBridge Pro.",
        "open_risks": [
            {
                "risk": "Severe Performance Degradation",
                "severity": "High",
                "reason": "API operations are timing out for users.",
                "evidence": {
                    "ticket_id": "TKT-10293",
                    "quote": "Page loads are taking 119+ seconds and API operations are timing out.",
                },
            }
        ],
        "talking_points": ["Discuss resolution of DataBridge Pro timeout issue."],
    }
    brief = validate_and_sanitize_brief(raw_data, account, tickets)
    assert isinstance(brief, AccountHealthResponse)
    assert len(brief.open_risks) == 1
    assert brief.open_risks[0].evidence is not None
    assert brief.open_risks[0].evidence.ticket_id == "TKT-10293"
    assert brief.open_risks[0].evidence.quote == "Page loads are taking 119+ seconds and API operations are timing out."


def test_validate_and_sanitize_brief_hallucinated_quote_is_cleaned():
    account = {"account_id": "ACC-3336", "company": "Omni Consumer Products"}
    tickets = [
        {
            "ticket_id": "TKT-10293",
            "subject": "DataBridge Pro running extremely slowly",
            "body": "Page loads are taking 119+ seconds and API operations are timing out.",
        }
    ]
    raw_data = {
        "account_id": "ACC-3336",
        "company": "Omni Consumer Products",
        "executive_summary": "Account summary.",
        "open_risks": [
            {
                "risk": "Fabricated Risk",
                "severity": "High",
                "reason": "Customer is planning to cancel contract tomorrow.",
                "evidence": {
                    "ticket_id": "TKT-10293",
                    "quote": "We will cancel our contract tomorrow if not fixed.",
                },
            }
        ],
        "talking_points": ["Point 1"],
    }
    brief = validate_and_sanitize_brief(raw_data, account, tickets)
    # The quote does NOT exist in ticket body, so it must be sanitized to None to avoid fake evidence
    assert brief.open_risks[0].evidence.quote is None


# ---------------------------------------------------------------------------
# API Integration Tests
# ---------------------------------------------------------------------------

def test_api_account_health_not_found(client: TestClient):
    resp = client.post("/account-health", json={"account_id": "ACC-NONEXISTENT"})
    assert resp.status_code == 404
    assert "not found" in resp.json()["detail"].lower()


def test_api_account_health_empty_account_id(client: TestClient):
    resp = client.post("/account-health", json={"account_id": "  "})
    assert resp.status_code == 422


def test_api_account_health_mocked_llm(client: TestClient):
    mock_llm_json = json.dumps({
        "account_id": "ACC-3336",
        "company": "Omni Consumer Products",
        "executive_summary": (
            "Omni Consumer Products is currently an At Risk account with declining usage trends. "
            "They experienced a significant performance degradation ticket on DataBridge Pro within the last 90 days. "
            "With an upcoming renewal and executive escalations, proactive engagement is needed to ensure retention."
        ),
        "open_risks": [
            {
                "risk": "Severe Performance Degradation on DataBridge Pro",
                "severity": "High",
                "reason": "198 users experienced severe API timeouts and page latency.",
                "evidence": {
                    "ticket_id": "TKT-10293",
                    "quote": "Page loads are taking 119+ seconds and API operations are timing out.",
                },
            },
            {
                "risk": "Impending Contract Renewal Under Escalation",
                "severity": "High",
                "reason": "Decision maker considering competing vendor evaluation.",
                "evidence": {
                    "ticket_id": None,
                    "quote": None,
                },
            }
        ],
        "talking_points": [
            "Review resolution and latency remediation for DataBridge Pro.",
            "Address competing vendor evaluation with VP Technology Quinn Wilson.",
            "Establish roadmap and health benchmarks ahead of upcoming renewal.",
        ],
    })

    with patch("src.account_health._call_gemini_account_health", return_value=mock_llm_json):
        resp = client.post("/account-health", json={"account_id": "ACC-3336"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["account_id"] == "ACC-3336"
        assert data["company"] == "Omni Consumer Products"
        assert "executive_summary" in data
        assert len(data["open_risks"]) == 2
        assert data["open_risks"][0]["evidence"]["ticket_id"] == "TKT-10293"
        assert data["open_risks"][0]["evidence"]["quote"] == "Page loads are taking 119+ seconds and API operations are timing out."
        assert len(data["talking_points"]) == 3


def test_task1_triage_unaffected(client: TestClient):
    """Verify that POST /triage still functions properly without interference from Task 2."""
    mock_triage_json = json.dumps({
        "product_area": "API",
        "issue_category": "Performance",
        "urgency": "P2",
        "reasoning": "High latency impacting users.",
        "known_issue": True,
        "knowledge_base_document": "products/databridge-pro.md",
        "knowledge_base_reference": "API Performance",
        "recommended_responder_team": "DataBridge Pro Engineering",
        "first_response": "Thank you for reaching out. We are investigating the API latency.",
    })

    with patch("src.triage._call_gemini", return_value=mock_triage_json):
        resp = client.post(
            "/triage",
            json={
                "subject": "DataBridge Pro API timeout",
                "body": "Our API calls to DataBridge Pro are timing out after 60 seconds.",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["issue_category"] == "Performance"
        assert data["urgency"] == "P2"
