"""Data contracts for the e-filing API, the case document and the events.

Requirement IDs (REQ-xx) point to docs/requirements.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator

SCHEMA_VERSION = 2  # REQ-09: every stored case carries its schema version


class PartyRole(str, Enum):
    claimant = "claimant"
    respondent = "respondent"


class FilerType(str, Enum):
    citizen = "citizen"
    lawyer = "lawyer"


class CaseStatus(str, Enum):
    filed = "filed"                 # accepted by the API, waiting for triage
    triaged = "triaged"             # category and priority set
    fee_assessed = "fee_assessed"   # filing fee calculated, waiting for payment
    assigned = "assigned"           # sent to a registrar's queue
    rejected = "rejected"


class Party(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: PartyRole
    national_id: Optional[str] = Field(default=None, pattern=r"^\d{13}$",
                                       description="Optional. 13 digits when given.")


class Document(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    kind: Literal["plaint", "evidence", "power_of_attorney", "id_copy", "other"]
    pages: int = Field(ge=1, le=2000)


class CaseFilingRequest(BaseModel):
    """Body of POST /v1/cases (REQ-01, REQ-02)."""
    filer_type: FilerType
    filer_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    bar_license_no: Optional[str] = Field(
        default=None, description="Required when filer_type is lawyer (REQ-03).")
    summary: str = Field(min_length=20, max_length=2000,
                         description="Short description of the dispute.")
    claim_amount: float = Field(ge=0, description="Amount claimed, in local currency. 0 if not monetary.")
    parties: list[Party] = Field(min_length=2)
    documents: list[Document] = Field(min_length=1)

    @field_validator("parties")
    @classmethod
    def needs_both_sides(cls, parties: list[Party]) -> list[Party]:
        roles = {p.role for p in parties}
        if roles != {PartyRole.claimant, PartyRole.respondent}:
            raise ValueError("at least one claimant and one respondent are required")
        return parties

    @field_validator("documents")
    @classmethod
    def needs_plaint(cls, docs: list[Document]) -> list[Document]:
        if not any(d.kind == "plaint" for d in docs):
            raise ValueError("a plaint document is required")
        return docs


class CaseView(BaseModel):
    """What GET /v1/cases/{case_number} returns."""
    case_number: str
    status: CaseStatus
    filer_type: FilerType
    summary: str
    claim_amount: float
    parties: list[Party]
    category: Optional[str] = None
    priority: Optional[str] = None
    fee: Optional[float] = None
    registrar_queue: Optional[str] = None
    history: list[dict] = []
    schema_version: int = SCHEMA_VERSION


class FilingReceipt(BaseModel):
    case_number: str
    status: CaseStatus
    status_url: str


class ErrorBody(BaseModel):
    """One error shape for every 4xx/5xx (REQ-04)."""
    code: str
    message: str
    details: list[dict] = []


class Event(BaseModel):
    """Envelope for every message on the queue (REQ-06)."""
    event_type: Literal["case.filed", "case.triaged", "case.fee_assessed", "case.assigned"]
    case_number: str
    event_version: int = 1
    occurred_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    attempt: int = 1
    payload: dict = {}
