"""Analysis endpoints: start a run, read the result, review flags."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.dependencies import CaseDep, CurrentPrincipal, DbSession
from app.models.discrepancy import Discrepancy
from app.models.enums import AuditAction, CaseStatus, ReviewDecision
from app.schemas.discrepancy import DiscrepancyOut
from app.schemas.report import CanonicalAnalysis
from app.services import audit_service
from app.workflows.analysis_workflow import build_analysis

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/cases", tags=["analysis"])


class AnalyzeResponse(BaseModel):
    case_id: str
    queued: bool
    backend: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)


class ReviewRequest(BaseModel):
    decision: ReviewDecision
    note: str | None = Field(default=None, max_length=2000)


@router.post("/{case_id}/analyze", response_model=AnalyzeResponse)
def start_analysis(case: CaseDep, db: DbSession, principal: CurrentPrincipal) -> AnalyzeResponse:
    """Queue the pipeline. Returns immediately unless the inline backend is on."""
    if not case.documents:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="the case has no documents to analyse",
        )

    audit_service.record(
        db,
        action=AuditAction.ANALYSIS_STARTED,
        case_id=case.id,
        actor=principal.subject,
        entity_type="case",
        entity_id=str(case.id),
        details={"documents": len(case.documents)},
    )
    # The session is committed before the job is dispatched so a worker on
    # another process sees the documents this request wrote.
    db.commit()

    from app.tasks import enqueue_analysis

    outcome = enqueue_analysis(case.id, actor=principal.subject)
    if not outcome.get("queued"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=outcome.get("error", "analysis could not be queued"),
        )

    return AnalyzeResponse(
        case_id=str(case.id),
        queued=True,
        backend=str(outcome.get("backend", "")),
        detail={k: v for k, v in outcome.items() if k not in {"queued", "backend"}},
    )


@router.get("/{case_id}/analysis", response_model=CanonicalAnalysis)
def get_analysis(case: CaseDep, db: DbSession) -> CanonicalAnalysis:
    """The canonical analysis: the one structure everything downstream reads."""
    if case.status in {CaseStatus.CREATED, CaseStatus.UPLOADING}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="this case has not been analysed yet",
        )
    return build_analysis(db, case.id)


@router.get("/{case_id}/discrepancies", response_model=list[DiscrepancyOut])
def list_discrepancies(
    case: CaseDep,
    db: DbSession,
    severity: str | None = Query(default=None),
    include_suppressed: bool = Query(default=False),
) -> list[DiscrepancyOut]:
    from app.workflows.analysis_workflow import _to_out

    statement = select(Discrepancy).where(Discrepancy.case_id == case.id)
    if not include_suppressed:
        statement = statement.where(Discrepancy.suppressed.is_(False))
    if severity:
        statement = statement.where(Discrepancy.severity == severity.upper())

    rows = db.scalars(statement.order_by(Discrepancy.code)).all()
    return [_to_out(row) for row in rows]


@router.post("/{case_id}/discrepancies/{code}/review", response_model=DiscrepancyOut)
def review_discrepancy(
    case: CaseDep,
    code: str,
    payload: ReviewRequest,
    db: DbSession,
    principal: CurrentPrincipal,
) -> DiscrepancyOut:
    """Record a human decision on one flag.

    The finding itself is never edited or deleted: the decision, who made it
    and their note are recorded alongside it, so the original machine output
    stays visible in the audit trail.
    """
    from app.workflows.analysis_workflow import _to_out

    discrepancy = db.scalar(
        select(Discrepancy).where(Discrepancy.case_id == case.id, Discrepancy.code == code)
    )
    if discrepancy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="finding not found")

    discrepancy.review_decision = str(payload.decision)
    discrepancy.reviewed_by = principal.subject
    discrepancy.review_note = payload.note
    db.add(discrepancy)
    db.flush()

    audit_service.record(
        db,
        action=AuditAction.FLAG_REVIEWED,
        case_id=case.id,
        actor=principal.subject,
        entity_type="discrepancy",
        entity_id=discrepancy.code,
        details={"decision": str(payload.decision), "note": payload.note},
    )
    return _to_out(discrepancy)


@router.get("/{case_id}/audit")
def get_audit_trail(case: CaseDep, db: DbSession) -> list[dict[str, Any]]:
    """The chain from upload to report, for a reviewer or an auditor."""
    return [
        {
            "at": entry.created_at.isoformat(),
            "actor": entry.actor,
            "action": entry.action,
            "entity_type": entry.entity_type,
            "entity_id": entry.entity_id,
            "details": entry.details,
            "analysis_version": entry.analysis_version,
            "model": entry.model_name,
            "prompt_version": entry.prompt_version,
            "rules_version": entry.rules_version,
        }
        for entry in audit_service.trail(db, case.id)
    ]
