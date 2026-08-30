"""Case lifecycle: creation, listing, status transitions.

A case is one applicant. Its status is the workflow's public progress
indicator, and every transition is written through this module so that the
audit trail and the status field can never disagree.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.case import Case
from app.models.discrepancy import Discrepancy
from app.models.document import Document
from app.models.enums import AuditAction, CaseStatus, Severity
from app.schemas.case import CaseCreate, CaseOut, CaseStatusOut
from app.services import audit_service

logger = logging.getLogger(__name__)

# Rough share of the pipeline complete at each step, for the progress bar.
STEP_PROGRESS: dict[str, float] = {
    "created": 0.0,
    "uploading": 0.05,
    "parsing": 0.15,
    "classifying": 0.30,
    "extracting": 0.50,
    "normalising": 0.60,
    "building_profile": 0.68,
    "running_rules": 0.76,
    "reasoning": 0.85,
    "verifying_evidence": 0.90,
    "building_analysis": 0.94,
    "generating_report": 0.97,
    "qa": 0.99,
    "finalised": 1.0,
}


class CaseNotFoundError(LookupError):
    pass


def next_case_ref(db: Session) -> str:
    """Sequential, human-quotable reference: CASE-2026-00001."""
    year = datetime.now(UTC).year
    prefix = f"CASE-{year}-"
    highest = db.scalar(
        select(func.max(Case.case_ref)).where(Case.case_ref.like(f"{prefix}%"))
    )
    sequence = 1
    if highest:
        try:
            sequence = int(str(highest).rsplit("-", 1)[-1]) + 1
        except ValueError:
            sequence = 1
    return f"{prefix}{sequence:05d}"


def create_case(db: Session, payload: CaseCreate, *, actor: str = "system") -> Case:
    case = Case(
        case_ref=payload.case_ref or next_case_ref(db),
        bank_id=payload.bank_id or "default",
        applicant_name_hint=payload.applicant_name,
        status=CaseStatus.CREATED,
        created_by=actor,
    )
    db.add(case)
    db.flush()

    audit_service.record(
        db,
        action=AuditAction.CASE_CREATED,
        case_id=case.id,
        actor=actor,
        entity_type="case",
        entity_id=str(case.id),
        details={"bank_id": case.bank_id, "case_ref": case.case_ref},
    )
    return case


def get_case(db: Session, case_id: UUID | str) -> Case:
    case = db.get(Case, UUID(str(case_id)))
    if case is None:
        raise CaseNotFoundError(f"case {case_id} was not found")
    return case


def list_cases(db: Session, *, limit: int = 50, offset: int = 0) -> tuple[list[Case], int]:
    total = db.scalar(select(func.count()).select_from(Case)) or 0
    statement = select(Case).order_by(Case.created_at.desc()).limit(limit).offset(offset)
    return list(db.scalars(statement)), total


def delete_case(db: Session, case_id: UUID | str, *, actor: str = "system") -> None:
    case = get_case(db, case_id)
    # The audit row is written first and survives the cascade, since its own
    # case_id foreign key is what gets cleared, not the row.
    audit_service.record(
        db,
        action=AuditAction.CASE_DELETED,
        case_id=None,
        actor=actor,
        entity_type="case",
        entity_id=str(case.id),
        details={"case_ref": case.case_ref},
    )
    db.delete(case)
    db.flush()


def set_status(
    db: Session,
    case: Case,
    status: CaseStatus,
    *,
    step: str | None = None,
    error_code: str | None = None,
    error_detail: str | None = None,
    actor: str = "worker",
) -> Case:
    case.status = str(status)
    if step is not None:
        case.current_step = step
    if error_code is not None:
        case.error_code = error_code
        case.error_detail = error_detail
    elif status != CaseStatus.FAILED:
        case.error_code = None
        case.error_detail = None

    db.add(case)
    db.flush()

    audit_service.record(
        db,
        action=AuditAction.ANALYSIS_STEP,
        case_id=case.id,
        actor=actor,
        entity_type="case",
        entity_id=str(case.id),
        details={"status": str(status), "step": step, "error_code": error_code},
    )
    return case


def counts(db: Session, case_id: UUID | str) -> dict[str, int]:
    case_uuid = UUID(str(case_id))
    documents = db.scalar(
        select(func.count()).select_from(Document).where(Document.case_id == case_uuid)
    ) or 0

    rows = db.execute(
        select(Discrepancy.severity, func.count())
        .where(Discrepancy.case_id == case_uuid, Discrepancy.suppressed.is_(False))
        .group_by(Discrepancy.severity)
    ).all()
    by_severity = {str(severity): int(count) for severity, count in rows}

    return {
        "documents": documents,
        "high": by_severity.get(Severity.HIGH, 0),
        "medium": by_severity.get(Severity.MEDIUM, 0),
        "low": by_severity.get(Severity.LOW, 0),
    }


def to_out(db: Session, case: Case) -> CaseOut:
    summary = counts(db, case.id)
    latest_report = sorted(case.reports, key=lambda r: r.created_at, reverse=True)
    return CaseOut(
        id=str(case.id),
        case_ref=case.case_ref,
        bank_id=case.bank_id,
        applicant_name_hint=case.applicant_name_hint,
        status=case.status,
        current_step=case.current_step,
        error_code=case.error_code,
        error_detail=case.error_detail,
        document_count=summary["documents"],
        high_flags=summary["high"],
        medium_flags=summary["medium"],
        low_flags=summary["low"],
        overall_status=latest_report[0].overall_status if latest_report else None,
        created_at=case.created_at,
        updated_at=case.updated_at,
    )


def status_out(db: Session, case: Case) -> CaseStatusOut:
    documents = list(case.documents)
    processed = sum(1 for d in documents if d.status in {"EXTRACTED", "CLASSIFIED", "FAILED"})
    failed = sum(1 for d in documents if d.status == "FAILED")

    return CaseStatusOut(
        case_id=str(case.id),
        status=case.status,
        current_step=case.current_step,
        progress=STEP_PROGRESS.get(case.current_step or "created", 0.0),
        documents_total=len(documents),
        documents_processed=processed,
        documents_failed=failed,
        error_code=case.error_code,
        error_detail=case.error_detail,
        updated_at=case.updated_at,
    )
