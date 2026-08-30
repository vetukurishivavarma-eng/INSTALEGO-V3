"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.api.auth import Principal, authorise_case, current_principal
from app.db import get_db
from app.models.case import Case
from app.models.document import Document
from app.services import case_service

DbSession = Annotated[Session, Depends(get_db)]
CurrentPrincipal = Annotated[Principal, Depends(current_principal)]


def get_case_or_404(
    case_id: Annotated[UUID, Path(description="case identifier")],
    db: DbSession,
    principal: CurrentPrincipal,
) -> Case:
    """Load a case the caller is allowed to see, or 404."""
    try:
        case = case_service.get_case(db, case_id)
    except case_service.CaseNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case not found") from exc
    return authorise_case(case, principal)


def get_document_or_404(
    document_id: Annotated[UUID, Path(description="document identifier")],
    db: DbSession,
    principal: CurrentPrincipal,
) -> Document:
    """Load a document, checking access through its owning case."""
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="document not found")
    authorise_case(document.case, principal)
    return document


CaseDep = Annotated[Case, Depends(get_case_or_404)]
DocumentDep = Annotated[Document, Depends(get_document_or_404)]
