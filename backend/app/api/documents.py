"""Document upload, listing and retrieval.

Upload accepts the batch, stores each file and returns a per-file result. A
rejected file does not fail the request: the caller needs to know which of the
six documents they dragged in were accepted, not that "the upload failed".
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, File, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import StreamingResponse

from app.dependencies import CaseDep, CurrentPrincipal, DbSession, DocumentDep
from app.models.enums import AuditAction, CaseStatus
from app.schemas.document import (
    DocumentContent,
    DocumentDetail,
    DocumentOut,
    FieldValueOut,
    PageOut,
    UploadResponse,
)
from app.services import audit_service, case_service, document_service
from app.storage import ObjectNotFoundError, get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["documents"])


@router.post("/cases/{case_id}/documents", response_model=UploadResponse)
async def upload_documents(
    case: CaseDep,
    db: DbSession,
    principal: CurrentPrincipal,
    files: list[UploadFile] = File(...),
    analyze: bool = Query(default=False, description="queue analysis once the batch is stored"),
) -> UploadResponse:
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="no files were supplied"
        )

    case_service.set_status(db, case, CaseStatus.UPLOADING, step="uploading",
                            actor=principal.subject)

    results = []
    for upload in files:
        content = await upload.read()
        results.append(
            document_service.ingest(
                db,
                case,
                filename=upload.filename or "unnamed",
                content=content,
                actor=principal.subject,
            )
        )

    accepted = sum(1 for result in results if result.accepted)
    queued = False

    if accepted and analyze:
        from app.tasks import enqueue_analysis

        # The uploads must be committed before analysis is handed off, because
        # the analysis does not share this transaction. The inline backend
        # opens its own session in this same thread, so on SQLite -- one writer
        # at a time -- it deadlocks against the locks this request still holds,
        # every time. On PostgreSQL it is a visibility race instead: the arq
        # worker is a separate process and can pick the job up before these
        # rows are readable. Committing first is what both need.
        db.commit()

        outcome = enqueue_analysis(case.id, actor=principal.subject)
        queued = bool(outcome.get("queued"))
        if not queued:
            logger.error("analysis could not be queued for case %s: %s", case.case_ref, outcome)
    else:
        case_service.set_status(db, case, CaseStatus.CREATED, step="uploading",
                                actor=principal.subject)

    return UploadResponse(
        case_id=str(case.id),
        accepted=accepted,
        rejected=len(results) - accepted,
        results=results,
        analysis_queued=queued,
    )


@router.get("/cases/{case_id}/documents", response_model=list[DocumentOut])
def list_documents(case: CaseDep) -> list[DocumentOut]:
    return [_to_out(document) for document in case.documents]


@router.get("/documents/{document_id}", response_model=DocumentDetail)
def get_document(document: DocumentDep, db: DbSession, principal: CurrentPrincipal) -> DocumentDetail:
    from sqlalchemy import select

    from app.models.extraction import FieldValue

    audit_service.record(
        db,
        action=AuditAction.DOCUMENT_VIEWED,
        case_id=document.case_id,
        actor=principal.subject,
        entity_type="document",
        entity_id=str(document.id),
    )

    values = db.scalars(
        select(FieldValue).where(FieldValue.document_id == document.id)
    ).all()

    return DocumentDetail(
        **_to_out(document).model_dump(),
        pages=[
            PageOut(
                page_number=page.page_number,
                width=page.width,
                height=page.height,
                char_count=page.char_count,
                has_text_layer=page.has_text_layer,
                ocr_used=page.ocr_used,
                ocr_confidence=page.ocr_confidence,
                has_image=bool(page.image_path),
            )
            for page in document.pages
        ],
        fields=[
            FieldValueOut(
                field_name=value.field_name,
                original_value=value.original_value,
                normalized_value=value.normalized_value,
                confidence=value.confidence,
                page_number=value.page_number,
                source_text=value.source_text,
                bbox=value.bbox,
                document_id=str(value.document_id),
                document_type=value.document_type,
            )
            for value in values
        ],
    )


@router.get("/documents/{document_id}/file")
def download_document(document: DocumentDep) -> StreamingResponse:
    """Stream the original, which the viewer renders."""
    import io

    try:
        content = document_service.load_content(document)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="the stored document could not be found",
        ) from exc

    return StreamingResponse(
        io.BytesIO(content),
        media_type=document.mime_type,
        headers={"content-disposition": f'inline; filename="{document.filename}"'},
    )


@router.get("/documents/{document_id}/pages/{page_number}", response_model=DocumentContent)
def get_page_text(document: DocumentDep, page_number: int) -> DocumentContent:
    for page in document.pages:
        if page.page_number == page_number:
            return DocumentContent(
                document_id=str(document.id),
                page_number=page_number,
                text=page.text or "",
                tables=page.tables or [],
            )
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="page not found")


@router.get("/documents/{document_id}/pages/{page_number}/image")
def get_page_image(document: DocumentDep, page_number: int) -> Response:
    """The rendered page, used to draw evidence highlights over a scan."""
    for page in document.pages:
        if page.page_number == page_number and page.image_path:
            try:
                content = get_storage().get(page.image_path)
            except ObjectNotFoundError as exc:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="page image not found"
                ) from exc
            return Response(content=content, media_type="image/png")
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND, detail="no rendered image for this page"
    )


def _to_out(document) -> DocumentOut:  # noqa: ANN001
    return DocumentOut(
        id=str(document.id),
        case_id=str(document.case_id),
        filename=document.filename,
        mime_type=document.mime_type,
        size_bytes=document.size_bytes,
        sha256=document.sha256,
        page_count=document.page_count,
        status=document.status,
        document_type=document.document_type,
        document_subtype=document.document_subtype,
        classification_confidence=document.classification_confidence,
        classification_reason=document.classification_reason,
        is_readable=document.is_readable,
        quality_status=document.quality_status,
        quality_flags=list(document.quality_flags or []),
        quality_notes=document.quality_notes,
        error_code=document.error_code,
        error_detail=document.error_detail,
        created_at=document.created_at,
    )

