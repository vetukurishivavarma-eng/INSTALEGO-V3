"""Uploaded files and their per-page parse results.

The stored original is immutable: nothing in the pipeline writes back to the
object it was uploaded as. Derived artefacts (page renders, OCR text) live in
separate rows and separate storage keys.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import GUID, Base, JSONType
from app.models.base import TimestampMixin, UUIDMixin
from app.models.enums import DocumentStatus, DocumentType

if TYPE_CHECKING:
    from app.models.case import Case
    from app.models.extraction import Extraction


class Document(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "documents"

    case_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("cases.id", ondelete="CASCADE"), nullable=False, index=True
    )

    filename: Mapped[str] = mapped_column(String(512), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(128), nullable=False)
    extension: Mapped[str] = mapped_column(String(16), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    storage_path: Mapped[str] = mapped_column(String(1024), nullable=False)

    page_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=DocumentStatus.UPLOADED)

    document_type: Mapped[str] = mapped_column(String(48), default=DocumentType.UNKNOWN)
    document_subtype: Mapped[str | None] = mapped_column(String(96))
    classification_confidence: Mapped[float] = mapped_column(Float, default=0.0)
    classification_reason: Mapped[str | None] = mapped_column(Text)
    is_readable: Mapped[bool] = mapped_column(Boolean, default=True)

    # QualityFlag members, plus a rolled-up DocumentQualityStatus.
    quality_flags: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    quality_status: Mapped[str | None] = mapped_column(String(32))
    quality_notes: Mapped[str | None] = mapped_column(Text)

    error_code: Mapped[str | None] = mapped_column(String(48))
    error_detail: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String(128))

    case: Mapped["Case"] = relationship(back_populates="documents")
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentPage.page_number",
    )
    extractions: Mapped[list["Extraction"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentPage(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "document_pages"
    __table_args__ = (UniqueConstraint("document_id", "page_number", name="uq_page_per_document"),)

    document_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)

    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)

    text: Mapped[str | None] = mapped_column(Text)
    char_count: Mapped[int] = mapped_column(Integer, default=0)
    has_text_layer: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)

    # Rendered page image, present when the page needed the vision model.
    image_path: Mapped[str | None] = mapped_column(String(1024))
    tables: Mapped[list[Any]] = mapped_column(JSONType, default=list)

    document: Mapped["Document"] = relationship(back_populates="pages")


Index("ix_documents_case_type", Document.case_id, Document.document_type)
