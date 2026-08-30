"""Contracts for candidate discrepancies and the agents that judge them.

The flow is deliberately one-directional. Python produces a CandidateDiscrepancy
from a rule or a comparison; the reasoning agent may only classify what it is
handed; the verifier may only confirm or retract. No agent is given the ability
to introduce a finding, which is what keeps the false-positive rate a property
of the rule set rather than of the model's mood.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    DiscrepancyClassification,
    EvidenceQuality,
    Severity,
    VerificationRecommendation,
)


class EvidenceRef(BaseModel):
    """A citation into a document, carried on every finding."""

    model_config = ConfigDict(extra="ignore")

    document_id: str = ""
    document_name: str = ""
    document_type: str = ""
    page: int = 0
    field: str = ""
    value: str = ""
    snippet: str = ""
    bbox: list[float] = Field(default_factory=list)


class CandidateDiscrepancy(BaseModel):
    """A deterministic finding, before any model has seen it.

    Produced by the rule engine or the comparison engine. ``needs_reasoning``
    marks the ambiguous ones worth spending a model call on; the rest are
    already decided and are only assessed if a bank policy asks for it.
    """

    model_config = ConfigDict(extra="ignore")

    type: str
    field: str = ""
    severity: Severity = Severity.MEDIUM
    rule_id: str = ""
    origin: str = "RULE_ENGINE"
    comparison_method: str = ""
    similarity: float | None = None
    summary: str = ""
    values: list[str] = Field(default_factory=list)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    needs_reasoning: bool = True
    # A deterministic verdict the model is not allowed to overturn silently,
    # e.g. a required document that is simply absent.
    deterministic: bool = False

    def compact(self) -> dict[str, Any]:
        """The token-lean form sent to the reasoning agent."""
        return {
            "type": self.type,
            "field": self.field,
            "summary": self.summary,
            "values": self.values,
            "comparison": self.comparison_method,
            "similarity": self.similarity,
        }


class DiscrepancyAssessment(BaseModel):
    """Output of the discrepancy reasoning agent."""

    model_config = ConfigDict(extra="ignore")

    classification: DiscrepancyClassification = DiscrepancyClassification.UNCERTAIN
    severity: Severity = Severity.MEDIUM
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    explanation: str = ""
    evidence: list[EvidenceRef] = Field(default_factory=list)
    recommended_action: str = ""

    @field_validator("classification", mode="before")
    @classmethod
    def _coerce_classification(cls, value: Any) -> Any:
        if isinstance(value, str):
            candidate = value.strip().upper().replace(" ", "_")
            if candidate in DiscrepancyClassification.__members__:
                return candidate
            return DiscrepancyClassification.UNCERTAIN
        return value

    @field_validator("severity", mode="before")
    @classmethod
    def _coerce_severity(cls, value: Any) -> Any:
        if isinstance(value, str):
            candidate = value.strip().upper()
            if candidate in Severity.__members__:
                return candidate
            return Severity.MEDIUM
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number > 1.0:
            number = number / 100.0
        return max(0.0, min(1.0, number))


class VerificationResult(BaseModel):
    """Output of the evidence verification agent."""

    model_config = ConfigDict(extra="ignore")

    verified: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    corrected_values: list[str] = Field(default_factory=list)
    evidence_quality: EvidenceQuality = EvidenceQuality.LOW
    reason: str = ""
    final_recommendation: VerificationRecommendation = VerificationRecommendation.MANUAL_REVIEW

    @field_validator("evidence_quality", mode="before")
    @classmethod
    def _coerce_quality(cls, value: Any) -> Any:
        if isinstance(value, str):
            candidate = value.strip().upper()
            if candidate in EvidenceQuality.__members__:
                return candidate
            return EvidenceQuality.LOW
        return value

    @field_validator("final_recommendation", mode="before")
    @classmethod
    def _coerce_recommendation(cls, value: Any) -> Any:
        if isinstance(value, str):
            candidate = value.strip().upper().replace(" ", "_")
            if candidate in VerificationRecommendation.__members__:
                return candidate
            return VerificationRecommendation.MANUAL_REVIEW
        return value

    @field_validator("confidence", mode="before")
    @classmethod
    def _clamp(cls, value: Any) -> float:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return 0.0
        if number > 1.0:
            number = number / 100.0
        return max(0.0, min(1.0, number))


class DiscrepancyOut(BaseModel):
    """A final flag as the API and the report see it."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    code: str
    type: str
    field: str | None = None
    severity: Severity
    classification: DiscrepancyClassification
    confidence: float
    explanation: str | None = None
    recommended_action: str | None = None
    origin: str
    rule_id: str | None = None
    verified: bool = False
    review_decision: str = "PENDING"
    evidence: list[EvidenceRef] = Field(default_factory=list)
