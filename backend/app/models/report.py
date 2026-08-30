"""Generated reports and the QA verdict attached to each one."""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import GUID, Base, JSONType
from app.models.base import TimestampMixin, UUIDMixin
from app.models.enums import ReportStatus

if TYPE_CHECKING:
    from app.models.case import Case


class Report(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "reports"

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    bank_id: Mapped[str] = mapped_column(String(64), nullable=False)
    template_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default=ReportStatus.PENDING)

    # Both the canonical analysis the report was rendered from and the mapped
    # bank-schema payload are stored, so a report can be re-rendered or
    # re-checked later without re-running the pipeline.
    analysis_snapshot: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    overall_status: Mapped[str | None] = mapped_column(String(32))
    docx_path: Mapped[str | None] = mapped_column(String(1024))
    pdf_path: Mapped[str | None] = mapped_column(String(1024))

    qa_passed: Mapped[bool | None] = mapped_column(Boolean)
    qa_errors: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    regenerated_count: Mapped[int] = mapped_column(Integer, default=0)

    analysis_version: Mapped[str | None] = mapped_column(String(32))
    model_name: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    rules_version: Mapped[str | None] = mapped_column(String(64))
    generated_by: Mapped[str | None] = mapped_column(String(128))
    error_detail: Mapped[str | None] = mapped_column(Text)

    case: Mapped["Case"] = relationship(back_populates="reports")
