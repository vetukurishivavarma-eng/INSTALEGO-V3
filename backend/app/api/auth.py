"""Authentication and authorisation.

Deliberately a seam rather than an implementation. The MVP ships with
``AUTH_ENABLED=false`` and a development principal, but every endpoint already
depends on ``current_principal`` and every case lookup already goes through
``authorise_case``. Adding a real identity provider later means replacing the
body of one function, not threading a user through forty call sites.

Turning authentication on in production is enforced at startup rather than
left to a deployment checklist.
"""

from __future__ import annotations

import hmac
import logging
from dataclasses import dataclass, field

from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.config import settings
from app.models.case import Case

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

DEV_PRINCIPAL_ID = "dev@local"


@dataclass(frozen=True)
class Principal:
    """Who is making the request."""

    subject: str
    roles: frozenset[str] = field(default_factory=lambda: frozenset({"reviewer"}))
    tenant: str | None = None

    @property
    def is_admin(self) -> bool:
        return "admin" in self.roles

    def can_read_case(self, case: Case) -> bool:
        # Single-tenant for now. When cases carry a tenant, this is the one
        # place that has to learn about it.
        if self.tenant is None:
            return True
        return case.bank_id == self.tenant


def current_principal(authorization: str | None = Header(default=None)) -> Principal:
    """Resolve the caller. Open in development, required in production."""
    if not settings.AUTH_ENABLED:
        if settings.is_production:
            # A production deployment with authentication off is a
            # misconfiguration, not a mode.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="authentication is disabled in a production environment",
            )
        return Principal(subject=DEV_PRINCIPAL_ID, roles=frozenset({"reviewer", "admin"}))

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="a bearer token is required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = authorization.split(" ", 1)[1].strip()
    # Placeholder verification. A real deployment swaps this for JWT
    # verification against the bank's identity provider; the comparison is
    # constant-time so the stub does not leak the secret by timing.
    if not hmac.compare_digest(token, settings.AUTH_SECRET):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="the token was not accepted"
        )
    return Principal(subject="service-token", roles=frozenset({"reviewer"}))


def authorise_case(case: Case, principal: Principal) -> Case:
    """Case-level access control, applied on every case-scoped route."""
    if not principal.can_read_case(case):
        # 404 rather than 403: whether a case exists is itself information.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="case not found")
    return case


@router.get("/me")
def whoami(principal: Principal = Depends(current_principal)) -> dict:
    return {
        "subject": principal.subject,
        "roles": sorted(principal.roles),
        "auth_enabled": settings.AUTH_ENABLED,
    }
