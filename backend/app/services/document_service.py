"""Document ingestion and parse persistence.

Ingestion is deliberately strict and deliberately cheap. It validates the file,
hashes it, stores the original untouched and records the metadata — and then
stops. Nothing is parsed, classified or read inside the request; that all
happens in the worker, because a 200-page scan must not hold an HTTP connection
open.

The stored original is never written again. Page renders and reports get their
own keys.
"""

from __future__ import annotations

import logging
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.extraction import (
    ParsedDocument,
    content_matches_extension,
    guess_mime,
    is_supported,
    normalize_extension,
)
from app.models.case import Case
from app.models.document import Document, DocumentPage
from app.models.enums import AuditAction, DocumentStatus, ErrorCode, QualityFlag
from app.schemas.document import UploadResult
from app.services import audit_service
from app.storage import ObjectExistsError, Storage, get_storage
from app.utils.hashing import sha256_bytes

logger = logging.getLogger(__name__)


class UploadRejected(Exception):
    """A file that cannot be accepted, with the reason a user should see."""

    def __init__(self, code: ErrorCode, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def validate_upload(filename: str, content: bytes) -> None:
    """Extension, size and magic bytes, in that order."""
    if not filename or not filename.strip():
        raise UploadRejected(ErrorCode.UPLOAD_FAILED, "the file has no name")

    extension = normalize_extension(filename)
    if not is_supported(filename):
        raise UploadRejected(
            ErrorCode.UNSUPPORTED_FILE,
            f"{extension or 'this file type'} is not supported; "
            f"accepted types are {', '.join(sorted(settings.ALLOWED_EXTENSIONS))}",
        )

    if not content:
        raise UploadRejected(ErrorCode.CORRUPTED_FILE, "the file is empty")

    if len(content) > settings.MAX_UPLOAD_BYTES:
        limit_mb = settings.MAX_UPLOAD_BYTES / (1024 * 1024)
        raise UploadRejected(
            ErrorCode.UPLOAD_FAILED,
            f"the file is {len(content) / (1024 * 1024):.1f} MB, over the {limit_mb:.0f} MB limit",
        )

    if not content_matches_extension(content[:16], filename):
        # A renamed file is nearly always a user mistake, and catching it here
        # gives a clear message instead of a parser error later.
        raise UploadRejected(
            ErrorCode.CORRUPTED_FILE,
            f"the file contents do not look like {extension}; it may have been renamed",
        )


def find_duplicate(db: Session, case_id: UUID, digest: str) -> Document | None:
    return db.scalar(
        select(Document).where(Document.case_id == case_id, Document.sha256 == digest)
    )


def ingest(
    db: Session,
    case: Case,
    *,
    filename: str,
    content: bytes,
    actor: str = "system",
    storage: Storage | None = None,
) -> UploadResult:
    """Validate, store and record one uploaded file."""
    store = storage or get_storage()

    try:
        validate_upload(filename, content)
    except UploadRejected as rejection:
        logger.info("rejected upload %s: %s", filename, rejection.detail)
        return UploadResult(
            filename=filename,
            accepted=False,
            error_code=str(rejection.code),
            error_detail=rejection.detail,
        )

    digest = sha256_bytes(content)
    duplicate = find_duplicate(db, case.id, digest)

    document = Document(
        id=uuid4(),
        case_id=case.id,
        filename=filename,
        mime_type=guess_mime(filename),
        extension=normalize_extension(filename),
        size_bytes=len(content),
        sha256=digest,
        storage_path="",
        status=DocumentStatus.UPLOADED,
        uploaded_by=actor,
        quality_flags=[QualityFlag.DUPLICATE_DOCUMENT] if duplicate else [],
    )

    key = Storage.document_key(str(case.id), str(document.id), filename)
    try:
        store.put(key, content, content_type=document.mime_type)
    except ObjectExistsError:
        # The key contains a fresh UUID, so this means a genuine collision
        # rather than a re-upload; refusing is safer than overwriting.
        return UploadResult(
            filename=filename,
            accepted=False,
            error_code=str(ErrorCode.UPLOAD_FAILED),
            error_detail="a document is already stored under this key",
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("storage write failed for %s", filename)
        return UploadResult(
            filename=filename,
            accepted=False,
            error_code=str(ErrorCode.UPLOAD_FAILED),
            error_detail=f"the file could not be stored: {type(exc).__name__}",
        )

    document.storage_path = key
    db.add(document)
    db.flush()

    audit_service.record(
        db,
        action=AuditAction.DOCUMENT_UPLOADED,
        case_id=case.id,
        actor=actor,
        entity_type="document",
        entity_id=str(document.id),
        details={
            "filename": filename,
            "size_bytes": len(content),
            "sha256": digest,
            "duplicate_of": str(duplicate.id) if duplicate else None,
        },
    )

    return UploadResult(
        filename=filename,
        document_id=str(document.id),
        accepted=True,
        duplicate_of=str(duplicate.id) if duplicate else None,
    )


def persist_pages(db: Session, document: Document, parsed: ParsedDocument) -> list[DocumentPage]:
    """Write page rows and store any rendered page images.

    Renders are written under their own keys; the uploaded original is never
    touched.
    """
    store = get_storage()
    pages: list[DocumentPage] = []

    for parsed_page in parsed.pages:
        image_key = None
        if parsed_page.image_bytes:
            image_key = Storage.page_image_key(
                str(document.case_id), str(document.id), parsed_page.page_number
            )
            try:
                store.put(image_key, parsed_page.image_bytes,
                          content_type=parsed_page.image_media_type, overwrite=True)
            except Exception:  # noqa: BLE001 - a lost render is not a lost page
                logger.exception("could not store the page render for %s", image_key)
                image_key = None

        page = DocumentPage(
            document_id=document.id,
            page_number=parsed_page.page_number,
            width=parsed_page.width,
            height=parsed_page.height,
            text=parsed_page.text or None,
            char_count=parsed_page.char_count,
            has_text_layer=parsed_page.has_text_layer,
            ocr_used=parsed_page.ocr_used,
            ocr_confidence=parsed_page.ocr_confidence,
            image_path=image_key,
            tables=[
                {"rows": table.rows, "name": table.name} for table in parsed_page.tables
            ],
        )
        db.add(page)
        pages.append(page)

    document.page_count = parsed.page_count
    document.is_readable = parsed.is_readable
    existing_flags = set(document.quality_flags or [])
    document.quality_flags = sorted(existing_flags | set(parsed.quality_flags))
    db.add(document)
    db.flush()
    return pages


def get_document(db: Session, document_id: UUID | str) -> Document | None:
    return db.get(Document, UUID(str(document_id)))


def load_content(document: Document, storage: Storage | None = None) -> bytes:
    return (storage or get_storage()).get(document.storage_path)


def local_path(document: Document, storage: Storage | None = None) -> str:
    return (storage or get_storage()).local_path(document.storage_path)
