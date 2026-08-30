"""Per-document processing: parse, transcribe, classify, extract, normalise.

Each document is handled independently and defensively. A file that cannot be
parsed marks itself failed and the case carries on with the rest, because one
unreadable payslip should not discard the identity documents that parsed
perfectly.

The order is what keeps the token cost down: parse first so the model is only
shown pages that exist, classify next so extraction knows what to ask for, and
extract last against a named field list.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy.orm import Session

from app.agents.document_classifier import (
    LOW_CLASSIFICATION_CONFIDENCE,
    DocumentClassifierAgent,
)
from app.agents.document_extractor import DocumentExtractorAgent, canonical_field_for
from app.extraction import ParsedDocument, ParsingError, parse_document
from app.extraction.ocr import get_ocr_engine
from app.llm.client import LLMError
from app.models.document import Document
from app.models.enums import (
    DocumentQualityStatus,
    DocumentStatus,
    DocumentType,
    ErrorCode,
    QualityFlag,
)
from app.models.extraction import Extraction, FieldValue
from app.schemas.extraction import ExtractionResult
from app.services import document_service
from app.utils.normalize import normalize_field

logger = logging.getLogger(__name__)


@dataclass
class DocumentOutcome:
    """What processing one document produced, successfully or otherwise."""

    document_id: str
    filename: str
    ok: bool
    document_type: str = DocumentType.UNKNOWN
    error_code: str | None = None
    error_detail: str | None = None
    field_count: int = 0
    models_used: set[str] = field(default_factory=set)
    prompt_versions: set[str] = field(default_factory=set)


def process_document(db: Session, document: Document) -> DocumentOutcome:
    """Run the whole per-document pipeline, recording failures on the row."""
    outcome = DocumentOutcome(
        document_id=str(document.id), filename=document.filename, ok=False
    )

    # ---------------------------------------------------------------- parse
    document.status = DocumentStatus.PARSING
    db.add(document)
    db.flush()

    try:
        path = document_service.local_path(document)
        parsed = parse_document(path, filename=document.filename)
    except ParsingError as exc:
        return _fail(db, document, outcome, exc.code, str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("parsing failed for %s", document.filename)
        return _fail(db, document, outcome, ErrorCode.PARSING_FAILED,
                     f"{type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ ocr
    try:
        _transcribe_pages(parsed)
    except Exception as exc:  # noqa: BLE001 - a failed OCR pass is not a failed document
        logger.warning("transcription failed for %s: %s", document.filename, type(exc).__name__)
        parsed.quality_flags.append(QualityFlag.LOW_OCR_CONFIDENCE)

    document_service.persist_pages(db, document, parsed)
    document.status = DocumentStatus.PARSED
    db.add(document)
    db.flush()

    # ------------------------------------------------------------- classify
    document.status = DocumentStatus.CLASSIFYING
    db.add(document)
    db.flush()

    try:
        classification_run = DocumentClassifierAgent().classify(
            parsed, filename=document.filename
        )
    except LLMError as exc:
        return _fail(db, document, outcome, exc.code, str(exc))

    classification = classification_run.data
    if classification.confidence < LOW_CLASSIFICATION_CONFIDENCE:
        document.quality_flags = sorted(
            set(document.quality_flags or []) | {QualityFlag.LOW_CLASSIFICATION_CONFIDENCE}
        )
    document.document_type = str(classification.document_type)
    document.document_subtype = classification.subtype or None
    document.classification_confidence = classification.confidence
    document.classification_reason = classification.reason
    document.is_readable = document.is_readable and classification.is_readable
    document.status = DocumentStatus.CLASSIFIED
    db.add(document)
    db.flush()

    outcome.document_type = str(classification.document_type)
    outcome.models_used.add(classification_run.model)
    outcome.prompt_versions.add(classification_run.prompt_version)

    # -------------------------------------------------------------- extract
    document.status = DocumentStatus.EXTRACTING
    db.add(document)
    db.flush()

    try:
        extraction_run = DocumentExtractorAgent().extract(parsed, classification)
    except LLMError as exc:
        return _fail(db, document, outcome, exc.code, str(exc))

    extraction = extraction_run.data
    record = Extraction(
        case_id=document.case_id,
        document_id=document.id,
        agent="DocumentExtractorAgent",
        model_name=extraction_run.model,
        prompt_version=extraction_run.prompt_version,
        document_type=str(classification.document_type),
        requested_fields=[item.field for item in extraction.fields],
        payload=extraction.model_dump(),
        pages_used=classification.pages_relevant,
        prompt_tokens=extraction_run.prompt_tokens,
        completion_tokens=extraction_run.completion_tokens,
        latency_ms=extraction_run.latency_ms,
        attempts=extraction_run.attempts,
    )
    db.add(record)
    db.flush()

    outcome.field_count = _persist_field_values(db, document, record, extraction)
    outcome.models_used.add(extraction_run.model)
    outcome.prompt_versions.add(extraction_run.prompt_version)

    document.status = DocumentStatus.EXTRACTED
    document.quality_status = _quality_status(document, parsed)
    document.quality_notes = "; ".join(
        note for page in parsed.pages for note in page.notes
    ) or None
    db.add(document)
    db.flush()

    outcome.ok = True
    return outcome


def _transcribe_pages(parsed: ParsedDocument) -> None:
    """Fill in text for pages that have none, using OCR or the vision model."""
    pending = [page for page in parsed.pages if page.needs_ocr and page.image_bytes]
    if not pending:
        return

    engine = get_ocr_engine()
    for page in pending:
        result = engine.transcribe(page.image_bytes)
        page.text = result.text
        page.ocr_used = True
        page.ocr_confidence = result.confidence
        if result.notes:
            page.notes.extend(result.notes)
        if result.is_low_confidence:
            parsed.quality_flags.append(QualityFlag.LOW_OCR_CONFIDENCE)
        # The page keeps needs_ocr=True so the extractor still sends the image
        # alongside the transcription; a transcript is a lossy view of a scan.


def _persist_field_values(
    db: Session,
    document: Document,
    extraction: Extraction,
    result: ExtractionResult,
) -> int:
    """Flatten the extraction into queryable rows, normalising as we go.

    Normalisation happens here, in Python, and never overwrites the original:
    both forms are stored on the row.
    """
    stored = 0
    for item in result.fields:
        if not item.is_present:
            continue

        canonical = canonical_field_for(item.field) or item.field
        normalized = normalize_field(canonical, item.value)

        db.add(
            FieldValue(
                case_id=document.case_id,
                document_id=document.id,
                extraction_id=extraction.id,
                field_name=canonical,
                original_value=item.value,
                normalized_value=normalized.normalized,
                confidence=item.confidence,
                page_number=item.source.page or None,
                source_text=item.source.text or None,
                bbox=item.source.bbox or None,
                document_type=document.document_type,
            )
        )
        stored += 1

    db.flush()
    return stored


def _quality_status(document: Document, parsed: ParsedDocument) -> str:
    """Roll page-level observations into one status.

    Never says fraud. The strongest thing it can say is that something could
    not be verified.
    """
    flags = set(document.quality_flags or [])
    if not parsed.is_readable or QualityFlag.UNREADABLE in flags:
        return DocumentQualityStatus.UNABLE_TO_VERIFY
    if (
        QualityFlag.UNCLEAR_IMAGE in flags
        or QualityFlag.LOW_OCR_CONFIDENCE in flags
        or QualityFlag.LOW_CLASSIFICATION_CONFIDENCE in flags
    ):
        return DocumentQualityStatus.REVIEW_REQUIRED
    if QualityFlag.DUPLICATE_DOCUMENT in flags:
        return DocumentQualityStatus.POTENTIAL_ISSUE
    if document.document_type == DocumentType.UNKNOWN:
        return DocumentQualityStatus.REVIEW_REQUIRED
    return DocumentQualityStatus.NO_ISSUE_OBSERVED


def _fail(
    db: Session,
    document: Document,
    outcome: DocumentOutcome,
    code: str,
    detail: str,
) -> DocumentOutcome:
    document.status = DocumentStatus.FAILED
    document.error_code = str(code)
    document.error_detail = detail[:2000]
    document.is_readable = False
    document.quality_status = DocumentQualityStatus.UNABLE_TO_VERIFY
    db.add(document)
    db.flush()

    outcome.ok = False
    outcome.error_code = str(code)
    outcome.error_detail = detail
    logger.warning("document %s failed: %s (%s)", document.filename, code, detail[:200])
    return outcome


def page_texts_for(db: Session, case_id: UUID) -> dict[str, str]:
    """Page text keyed "document_id:page", used by the evidence verifier."""
    from app.models.document import Document as DocumentModel

    texts: dict[str, str] = {}
    documents = db.query(DocumentModel).filter(DocumentModel.case_id == case_id).all()
    for document in documents:
        for page in document.pages:
            if page.text:
                texts[f"{document.id}:{page.page_number}"] = page.text
    return texts
