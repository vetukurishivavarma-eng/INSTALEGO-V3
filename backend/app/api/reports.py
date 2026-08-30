"""Report endpoints: generate, list, read and download."""

from __future__ import annotations

import io
import logging

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.dependencies import CaseDep, CurrentPrincipal, DbSession
from app.models.enums import AuditAction, CaseStatus
from app.models.report import Report
from app.schemas.report import ReportOut, ReportRequest
from app.services import audit_service
from app.storage import ObjectNotFoundError, get_storage
from app.workflows.report_workflow import generate_report

logger = logging.getLogger(__name__)

router = APIRouter(tags=["reports"])

MEDIA_TYPES = {
    "pdf": "application/pdf",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


@router.post("/cases/{case_id}/reports/generate", response_model=ReportOut)
def create_report(
    case: CaseDep,
    payload: ReportRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> ReportOut:
    if case.status in {CaseStatus.CREATED, CaseStatus.UPLOADING, CaseStatus.PROCESSING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="analysis must finish before a report can be generated",
        )

    report = generate_report(
        db,
        case.id,
        template_id=payload.template_id,
        actor=principal.subject,
    )
    return _to_out(report)


@router.get("/cases/{case_id}/reports", response_model=list[ReportOut])
def list_reports(case: CaseDep, db: DbSession) -> list[ReportOut]:
    rows = db.scalars(
        select(Report).where(Report.case_id == case.id).order_by(Report.created_at.desc())
    ).all()
    return [_to_out(report) for report in rows]


@router.get("/reports/{report_id}", response_model=ReportOut)
def get_report(report_id: str, db: DbSession, principal: CurrentPrincipal) -> ReportOut:
    report = _load(db, report_id, principal)
    return _to_out(report)


@router.get("/reports/{report_id}/download/{fmt}")
def download_report(
    report_id: str, fmt: str, db: DbSession, principal: CurrentPrincipal
) -> StreamingResponse:
    if fmt not in MEDIA_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="format must be pdf or docx"
        )

    report = _load(db, report_id, principal)
    key = report.pdf_path if fmt == "pdf" else report.docx_path
    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"this report has no {fmt} rendering",
        )

    try:
        content = get_storage().get(key)
    except ObjectNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="the rendered report is missing"
        ) from exc

    audit_service.record(
        db,
        action=AuditAction.REPORT_DOWNLOADED,
        case_id=report.case_id,
        actor=principal.subject,
        entity_type="report",
        entity_id=str(report.id),
        details={"format": fmt},
    )

    filename = f"{report.case_id}-report.{fmt}"
    return StreamingResponse(
        io.BytesIO(content),
        media_type=MEDIA_TYPES[fmt],
        headers={"content-disposition": f'attachment; filename="{filename}"'},
    )


def _load(db: DbSession, report_id: str, principal: CurrentPrincipal) -> Report:
    from uuid import UUID

    from app.api.auth import authorise_case

    try:
        report = db.get(Report, UUID(str(report_id)))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="malformed report id"
        ) from exc

    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="report not found")
    authorise_case(report.case, principal)
    return report


def _to_out(report: Report) -> ReportOut:
    return ReportOut(
        id=str(report.id),
        case_id=str(report.case_id),
        bank_id=report.bank_id,
        template_id=report.template_id,
        status=report.status,
        overall_status=report.overall_status,
        qa_passed=report.qa_passed,
        qa_errors=list(report.qa_errors or []),
        report_json=report.report_json or {},
        has_docx=bool(report.docx_path),
        has_pdf=bool(report.pdf_path),
        created_at=report.created_at,
    )
