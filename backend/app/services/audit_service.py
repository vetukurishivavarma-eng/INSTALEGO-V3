"""The audit trail.

Every write here is append-only and pre-masked. Nothing that reaches this table
may contain document content or an unmasked identifier, because an audit log is
the one place in the system that is deliberately never deleted.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.models.audit import AuditLog
from app.models.enums import AuditAction
from app.utils.text import mask_sensitive, truncate

logger = logging.getLogger(__name__)

# Keys whose values are masked before storage even inside nested payloads.
SENSITIVE_KEYS = {
    "pan", "aadhaar", "passport", "driving_license", "bank_account", "account_number",
    "phone", "email", "value", "original_value", "normalized_value", "text", "snippet",
}


def sanitise(payload: Any) -> Any:
    """Mask identifiers and clip long strings anywhere in a payload."""
    if isinstance(payload, dict):
        return {
            key: (mask_sensitive(str(value)) if key in SENSITIVE_KEYS and isinstance(value, str)
                  else sanitise(value))
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [sanitise(item) for item in payload]
    if isinstance(payload, str):
        return truncate(mask_sensitive(payload), 500)
    return payload


def record(
    db: Session,
    *,
    action: AuditAction | str,
    case_id: UUID | str | None = None,
    actor: str = "system",
    entity_type: str | None = None,
    entity_id: str | None = None,
    details: dict[str, Any] | None = None,
    model_name: str | None = None,
    prompt_version: str | None = None,
    rules_version: str | None = None,
) -> AuditLog:
    """Append one audit row. Never raises into the caller's path."""
    entry = AuditLog(
        case_id=UUID(str(case_id)) if case_id else None,
        actor=actor or "system",
        action=str(action),
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id else None,
        details=sanitise(details or {}),
        analysis_version=settings.ANALYSIS_VERSION,
        model_name=model_name,
        prompt_version=prompt_version,
        rules_version=rules_version,
    )
    db.add(entry)
    try:
        db.flush()
    except Exception:  # noqa: BLE001 - auditing must not break the operation
        logger.exception("failed to write an audit entry for %s", action)
        db.rollback()
    return entry


def trail(db: Session, case_id: UUID | str, *, limit: int = 200) -> list[AuditLog]:
    statement = (
        select(AuditLog)
        .where(AuditLog.case_id == UUID(str(case_id)))
        .order_by(AuditLog.created_at.asc())
        .limit(limit)
    )
    return list(db.scalars(statement))
