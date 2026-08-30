"""API payloads for cases."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import CaseStatus, OverallStatus


class CaseCreate(BaseModel):
    """A case is one applicant. The bank decides which rules and report apply."""

    bank_id: str = Field(default="default", max_length=64)
    applicant_name: str | None = Field(default=None, max_length=256)
    case_ref: str | None = Field(
        default=None,
        max_length=32,
        description="optional caller-supplied reference; generated when omitted",
    )


class CaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_ref: str
    bank_id: str
    applicant_name_hint: str | None = None
    status: CaseStatus
    current_step: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    document_count: int = 0
    high_flags: int = 0
    medium_flags: int = 0
    low_flags: int = 0
    overall_status: OverallStatus | None = None
    created_at: datetime
    updated_at: datetime


class CaseStatusOut(BaseModel):
    """Polled by the frontend while the workflow runs."""

    case_id: str
    status: CaseStatus
    current_step: str | None = None
    progress: float = 0.0
    documents_total: int = 0
    documents_processed: int = 0
    documents_failed: int = 0
    error_code: str | None = None
    error_detail: str | None = None
    updated_at: datetime | None = None


class CaseListOut(BaseModel):
    items: list[CaseOut]
    total: int
    limit: int
    offset: int
