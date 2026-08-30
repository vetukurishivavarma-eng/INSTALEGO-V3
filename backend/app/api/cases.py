"""Case endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Query, Response, status

from app.dependencies import CaseDep, CurrentPrincipal, DbSession
from app.models.enums import AuditAction
from app.schemas.case import CaseCreate, CaseListOut, CaseOut, CaseStatusOut
from app.services import audit_service, case_service

router = APIRouter(prefix="/cases", tags=["cases"])


@router.post("", response_model=CaseOut, status_code=status.HTTP_201_CREATED)
def create_case(payload: CaseCreate, db: DbSession, principal: CurrentPrincipal) -> CaseOut:
    case = case_service.create_case(db, payload, actor=principal.subject)
    return case_service.to_out(db, case)


@router.get("", response_model=CaseListOut)
def list_cases(
    db: DbSession,
    principal: CurrentPrincipal,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> CaseListOut:
    cases, total = case_service.list_cases(db, limit=limit, offset=offset)
    visible = [case for case in cases if principal.can_read_case(case)]
    return CaseListOut(
        items=[case_service.to_out(db, case) for case in visible],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{case_id}", response_model=CaseOut)
def get_case(case: CaseDep, db: DbSession, principal: CurrentPrincipal) -> CaseOut:
    audit_service.record(
        db,
        action=AuditAction.CASE_VIEWED,
        case_id=case.id,
        actor=principal.subject,
        entity_type="case",
        entity_id=str(case.id),
    )
    return case_service.to_out(db, case)


@router.get("/{case_id}/status", response_model=CaseStatusOut)
def get_case_status(case: CaseDep, db: DbSession) -> CaseStatusOut:
    """Polled by the frontend while the pipeline runs."""
    return case_service.status_out(db, case)


@router.delete("/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(case: CaseDep, db: DbSession, principal: CurrentPrincipal) -> Response:
    case_service.delete_case(db, case.id, actor=principal.subject)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
