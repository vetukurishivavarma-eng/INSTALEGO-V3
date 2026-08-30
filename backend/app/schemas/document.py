"""API payloads for documents and their extracted content."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import DocumentStatus, DocumentType


class DocumentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    case_id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    page_count: int
    status: DocumentStatus
    document_type: DocumentType
    document_subtype: str | None = None
    classification_confidence: float = 0.0
    classification_reason: str | None = None
    is_readable: bool = True
    quality_status: str | None = None
    quality_flags: list[str] = Field(default_factory=list)
    quality_notes: str | None = None
    error_code: str | None = None
    error_detail: str | None = None
    created_at: datetime


class PageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    width: float | None = None
    height: float | None = None
    char_count: int = 0
    has_text_layer: bool = False
    ocr_used: bool = False
    ocr_confidence: float | None = None
    has_image: bool = False


class FieldValueOut(BaseModel):
    """An extracted value with the citation that supports it."""

    model_config = ConfigDict(from_attributes=True)

    field_name: str
    original_value: str | None = None
    normalized_value: str | None = None
    confidence: float = 0.0
    page_number: int | None = None
    source_text: str | None = None
    bbox: list[float] | None = None
    document_id: str
    document_type: str | None = None


class DocumentDetail(DocumentOut):
    pages: list[PageOut] = Field(default_factory=list)
    fields: list[FieldValueOut] = Field(default_factory=list)


class UploadResult(BaseModel):
    """Per-file outcome, so a bad file in a batch does not hide the good ones."""

    filename: str
    document_id: str | None = None
    accepted: bool
    error_code: str | None = None
    error_detail: str | None = None
    duplicate_of: str | None = None


class UploadResponse(BaseModel):
    case_id: str
    accepted: int
    rejected: int
    results: list[UploadResult] = Field(default_factory=list)
    analysis_queued: bool = False


class DocumentContent(BaseModel):
    """Page text for the viewer, kept separate from the metadata payload."""

    document_id: str
    page_number: int
    text: str = ""
    tables: list[dict[str, Any]] = Field(default_factory=list)
