"""Pointers back into the source documents.

Evidence is what makes a flag answerable: which document, which page, which
words. A finding without at least one of these rows cannot be shown to a
reviewer, and the QA agent treats its absence as an error.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import GUID, Base, JSONType
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.discrepancy import Discrepancy


class Evidence(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "evidence"

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    discrepancy_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("discrepancies.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="SET NULL")
    )

    document_name: Mapped[str | None] = mapped_column(String(512))
    document_type: Mapped[str | None] = mapped_column(String(48))
    page_number: Mapped[int | None] = mapped_column(Integer)
    field: Mapped[str | None] = mapped_column(String(96))
    value: Mapped[str | None] = mapped_column(Text)
    snippet: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[list[Any] | None] = mapped_column(JSONType)

    discrepancy: Mapped["Discrepancy | None"] = relationship(back_populates="evidence")
