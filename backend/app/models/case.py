"""A case is exactly one applicant and the documents filed for them."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import TimestampMixin, UUIDMixin
from app.models.enums import CaseStatus

if TYPE_CHECKING:
    from app.models.applicant import ApplicantProfile
    from app.models.discrepancy import Discrepancy
    from app.models.document import Document
    from app.models.report import Report
    from app.models.validation import ValidationResult


class Case(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "cases"

    # Human-facing reference (CASE-2026-00001) distinct from the surrogate key.
    case_ref: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    bank_id: Mapped[str] = mapped_column(String(64), nullable=False, default="default")
    applicant_name_hint: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=CaseStatus.CREATED)
    current_step: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(48))
    error_detail: Mapped[str | None] = mapped_column(Text)

    # Frozen at analysis time so a stored result stays explainable after the
    # code, prompts or rule files move on.
    analysis_version: Mapped[str | None] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(128))
    rules_version: Mapped[str | None] = mapped_column(String(64))

    created_by: Mapped[str | None] = mapped_column(String(128))

    documents: Mapped[list["Document"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    profile: Mapped["ApplicantProfile | None"] = relationship(
        back_populates="case", cascade="all, delete-orphan", uselist=False, passive_deletes=True
    )
    discrepancies: Mapped[list["Discrepancy"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    validations: Mapped[list["ValidationResult"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )
    reports: Mapped[list["Report"]] = relationship(
        back_populates="case", cascade="all, delete-orphan", passive_deletes=True
    )


Index("ix_cases_status_created", Case.status, Case.created_at)
