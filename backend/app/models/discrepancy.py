"""Findings, from deterministic candidate through to reviewed final flag.

The audit chain lives on this row: which rule proposed it, what the reasoning
agent decided, what the verifier concluded, and what a human did about it.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import GUID, Base, JSONType
from app.models.base import TimestampMixin, UUIDMixin
from app.models.enums import DiscrepancyClassification, ReviewDecision

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.evidence import Evidence


class Discrepancy(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "discrepancies"

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Stable per-case label (D001, D002) used in reports and in the UI.
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    field: Mapped[str | None] = mapped_column(String(96))

    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    classification: Mapped[str] = mapped_column(
        String(32), nullable=False, default=DiscrepancyClassification.POTENTIAL
    )
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    explanation: Mapped[str | None] = mapped_column(Text)
    recommended_action: Mapped[str | None] = mapped_column(Text)

    # --- provenance -------------------------------------------------------
    origin: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_id: Mapped[str | None] = mapped_column(String(96))
    comparison_method: Mapped[str | None] = mapped_column(String(32))
    similarity: Mapped[float | None] = mapped_column(Float)

    # What the deterministic layer proposed, before any model saw it.
    candidate_payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    # What the reasoning agent returned, verbatim after schema validation.
    reasoner_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    # What the evidence verifier returned for HIGH or ambiguous findings.
    verification_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    verified: Mapped[bool] = mapped_column(Boolean, default=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    suppressed_reason: Mapped[str | None] = mapped_column(Text)

    model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    rules_version: Mapped[str | None] = mapped_column(String(64))

    # --- human review -----------------------------------------------------
    review_decision: Mapped[str] = mapped_column(
        String(24), nullable=False, default=ReviewDecision.PENDING
    )
    reviewed_by: Mapped[str | None] = mapped_column(String(128))
    review_note: Mapped[str | None] = mapped_column(Text)

    case: Mapped["Case"] = relationship(back_populates="discrepancies")
    evidence: Mapped[list["Evidence"]] = relationship(
        back_populates="discrepancy", cascade="all, delete-orphan", passive_deletes=True
    )


Index("ix_discrepancies_case_severity", Discrepancy.case_id, Discrepancy.severity)
