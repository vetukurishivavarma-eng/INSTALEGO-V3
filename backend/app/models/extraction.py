"""Raw agent output and the atomic field values distilled from it.

``Extraction`` keeps one agent run per document — what was asked, which model
and prompt answered, what came back. ``FieldValue`` is the flattened, queryable
form the rule engine actually reads, one row per extracted field, each carrying
its own page-level evidence.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import GUID, Base, JSONType
from app.models.base import TimestampMixin, UUIDMixin

if TYPE_CHECKING:
    from app.models.document import Document


class Extraction(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "extractions"

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )

    agent: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    document_type: Mapped[str | None] = mapped_column(String(48))
    requested_fields: Mapped[list[Any]] = mapped_column(JSONType, default=list)

    # Validated agent payload. Never rendered into a report directly.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)

    pages_used: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error_detail: Mapped[str | None] = mapped_column(Text)

    document: Mapped["Document"] = relationship(back_populates="extractions")


class FieldValue(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "field_values"

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    extraction_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID(), ForeignKey("extractions.id", ondelete="SET NULL")
    )

    field_name: Mapped[str] = mapped_column(String(96), nullable=False, index=True)
    # The document's own wording, preserved byte for byte.
    original_value: Mapped[str | None] = mapped_column(Text)
    # Comparison form produced by Python normalisation, never by the model.
    normalized_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)

    page_number: Mapped[int | None] = mapped_column(Integer)
    source_text: Mapped[str | None] = mapped_column(Text)
    bbox: Mapped[list[Any] | None] = mapped_column(JSONType)
    document_type: Mapped[str | None] = mapped_column(String(48))


Index("ix_field_values_case_field", FieldValue.case_id, FieldValue.field_name)
