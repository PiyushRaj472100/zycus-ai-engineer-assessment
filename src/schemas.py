"""Pydantic schemas for request and response validation."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ProductArea(str, Enum):
    # DataBridge Pro
    DATA_INGESTION = "Data Ingestion"
    SCHEMA_MANAGEMENT = "Schema Management"
    PIPELINE_MONITORING = "Pipeline Monitoring"
    CONNECTORS = "Connectors"
    API = "API"
    # CloudSync
    FILE_SYNC = "File Sync"
    CONFLICT_RESOLUTION = "Conflict Resolution"
    PERMISSIONS = "Permissions"
    BANDWIDTH_LIMITS = "Bandwidth Limits"
    INTEGRATIONS = "Integrations"
    # AnalyticsHub
    DASHBOARD = "Dashboard"
    REPORTS = "Reports"
    DATA_SOURCES = "Data Sources"
    ALERTS = "Alerts"
    EXPORTS = "Exports"
    # SecureVault
    AUTHENTICATION = "Authentication"
    ENCRYPTION = "Encryption"
    AUDIT_LOGS = "Audit Logs"
    KEY_MANAGEMENT = "Key Management"
    SSO = "SSO"
    # WorkflowEngine
    TRIGGERS = "Triggers"
    ACTIONS = "Actions"
    SCHEDULING = "Scheduling"
    ERROR_HANDLING = "Error Handling"
    TEMPLATES = "Templates"


class IssueCategory(str, Enum):
    BUG = "Bug"
    FEATURE_REQUEST = "Feature Request"
    HOW_TO = "How-To"
    PERFORMANCE = "Performance"
    BILLING = "Billing"
    INTEGRATION = "Integration"
    ONBOARDING = "Onboarding"
    DATA_LOSS = "Data Loss"


class UrgencyTier(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"
    P4 = "P4"


# ---------------------------------------------------------------------------
# API request
# ---------------------------------------------------------------------------

class TicketRequest(BaseModel):
    """Incoming ticket payload."""

    subject: str
    body: str

    @model_validator(mode="after")
    def check_not_empty(self) -> "TicketRequest":
        if not self.subject.strip():
            raise ValueError("subject must not be empty")
        if not self.body.strip():
            raise ValueError("body must not be empty")
        return self


# ---------------------------------------------------------------------------
# API response
# ---------------------------------------------------------------------------

class TriageResponse(BaseModel):
    """Structured triage result returned to the caller."""

    product_area: Optional[ProductArea] = None
    issue_category: IssueCategory
    urgency: UrgencyTier
    reasoning: str
    known_issue: bool
    knowledge_base_document: Optional[str] = None
    knowledge_base_reference: Optional[str] = None
    recommended_responder_team: str
    first_response: str

    @model_validator(mode="after")
    def kb_fields_consistent(self) -> "TriageResponse":
        """When known_issue is False the KB fields must be null."""
        if not self.known_issue:
            self.knowledge_base_document = None
            self.knowledge_base_reference = None
        return self


# ---------------------------------------------------------------------------
# Task 2: Account Health Summariser Schemas
# ---------------------------------------------------------------------------

class SeverityTier(str, Enum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"


class EvidenceItem(BaseModel):
    """Evidence supporting an identified risk."""
    ticket_id: Optional[str] = None
    quote: Optional[str] = None


class RiskItem(BaseModel):
    """An identified risk or escalation flag for the account."""
    risk: str
    severity: SeverityTier
    reason: str
    evidence: Optional[EvidenceItem] = None


class AccountHealthRequest(BaseModel):
    """Incoming request to summarize account health for TAM QBR prep."""
    account_id: str

    @model_validator(mode="after")
    def validate_account_id(self) -> "AccountHealthRequest":
        self.account_id = self.account_id.strip()
        if not self.account_id:
            raise ValueError("account_id must not be empty")
        return self


class AccountHealthResponse(BaseModel):
    """Structured TAM brief returned to the caller."""
    account_id: str
    company: str
    executive_summary: str
    open_risks: list[RiskItem]
    talking_points: list[str]
