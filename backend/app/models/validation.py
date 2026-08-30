"""Deterministic rule outcomes. One row per rule evaluation, always stored.

Passes are kept alongside failures: a report that can only show what went wrong
cannot show a reviewer what was actually checked.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import GUID, Base, JSONType
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.case import Case


class ValidationResult(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "validation_results"

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rule_id: Mapped[str] = mapped_column(String(96), nullable=False)
    rule_category: Mapped[str] = mapped_column(String(48), nullable=False)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    field: Mapped[str | None] = mapped_column(String(96))
    severity: Mapped[str | None] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(Text)
    evidence: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    rules_version: Mapped[str | None] = mapped_column(String(64))

    case: Mapped["Case"] = relationship(back_populates="validations")


Index("ix_validation_case_result", ValidationResult.case_id, ValidationResult.result)
