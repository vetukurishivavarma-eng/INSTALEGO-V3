"""The canonical analysis, the report payload, and the QA verdict.

The canonical analysis is the single structure everything downstream consumes.
Report generation reads this, never raw model output; the renderer reads the
mapped report JSON, never the analysis directly. Keeping that chain explicit is
what stops an unvalidated sentence from a model reaching a bank.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    DocumentQualityStatus,
    OverallStatus,
    RuleResult,
    Severity,
)
from app.schemas.applicant import ApplicantProfileSchema
from app.schemas.discrepancy import DiscrepancyOut


class DocumentSummary(BaseModel):
    """One uploaded document as the report describes it."""

    model_config = ConfigDict(from_attributes=True)

    document_id: str
    filename: str
    document_type: str
    subtype: str | None = None
    pages: int = 0
    classification_confidence: float = 0.0
    is_readable: bool = True
    status: str = ""
    quality_status: str = DocumentQualityStatus.NO_ISSUE_OBSERVED
    quality_flags: list[str] = Field(default_factory=list)
    quality_notes: str | None = None
    sha256: str = ""
    uploaded_at: datetime | None = None
    error_code: str | None = None


class ValidationSummary(BaseModel):
    """One rule evaluation, pass or fail, as run against this case."""

    model_config = ConfigDict(from_attributes=True)

    rule_id: str
    rule_category: str
    result: RuleResult
    field: str | None = None
    severity: Severity | None = None
    reason: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class MissingDocument(BaseModel):
    """A required document type with nothing supplied for it."""

    document_type: str
    severity: Severity = Severity.HIGH
    reason: str = ""
    required_by: str = ""


class AnalysisVersions(BaseModel):
    """Everything needed to reproduce or explain a result later."""

    analysis_version: str = ""
    model: str = ""
    prompt_version: str = ""
    rules_version: str = ""
    generated_at: datetime | None = None


class CanonicalAnalysis(BaseModel):
    """The one structure the report, the API and the UI all read."""

    model_config = ConfigDict(extra="ignore")

    case_id: str
    case_ref: str = ""
    bank_id: str = ""
    applicant: ApplicantProfileSchema = Field(default_factory=ApplicantProfileSchema)
    documents: list[DocumentSummary] = Field(default_factory=list)
    validations: list[ValidationSummary] = Field(default_factory=list)
    discrepancies: list[DiscrepancyOut] = Field(default_factory=list)
    missing_documents: list[MissingDocument] = Field(default_factory=list)
    document_quality: list[DocumentSummary] = Field(default_factory=list)
    final_status: OverallStatus = OverallStatus.REVIEW_REQUIRED
    overall_confidence: float = 0.0
    manual_review_required: bool = True
    versions: AnalysisVersions = Field(default_factory=AnalysisVersions)

    def high_severity(self) -> list[DiscrepancyOut]:
        return [d for d in self.discrepancies if d.severity == Severity.HIGH]

    def counts(self) -> dict[str, int]:
        return {
            "HIGH": sum(1 for d in self.discrepancies if d.severity == Severity.HIGH),
            "MEDIUM": sum(1 for d in self.discrepancies if d.severity == Severity.MEDIUM),
            "LOW": sum(1 for d in self.discrepancies if d.severity == Severity.LOW),
        }


class QAError(BaseModel):
    model_config = ConfigDict(extra="ignore")

    type: str = ""
    field: str = ""
    description: str = ""
    severity: Severity = Severity.MEDIUM


class QAResult(BaseModel):
    """Output of the final QA agent."""

    model_config = ConfigDict(extra="ignore")

    passed: bool = False
    errors: list[QAError] = Field(default_factory=list)
    requires_regeneration: bool = False

    def high_errors(self) -> list[QAError]:
        return [e for e in self.errors if e.severity == Severity.HIGH]


class ReportRequest(BaseModel):
    bank_id: str | None = None
    template_id: str | None = None
    regenerate: bool = False


class ReportOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    bank_id: str
    template_id: str
    status: str
    overall_status: str | None = None
    qa_passed: bool | None = None
    qa_errors: list[dict[str, Any]] = Field(default_factory=list)
    report_json: dict[str, Any] = Field(default_factory=dict)
    has_docx: bool = False
    has_pdf: bool = False
    created_at: datetime | None = None
