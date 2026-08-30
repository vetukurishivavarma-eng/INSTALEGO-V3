"""Health and configuration introspection.

``/health`` is the liveness probe and stays cheap. ``/health/ready`` actually
touches the database and storage, because a readiness probe that only proves
the process is running will happily route traffic to a container that cannot
reach Postgres.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from app.config import settings
from app.db import SessionLocal
from app.rules import load_rule_config, registered_rules
from app.storage import get_storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "environment": settings.ENVIRONMENT}


@router.get("/health/ready")
def readiness(response: Response) -> dict[str, Any]:
    checks: dict[str, Any] = {}

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        checks["database"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["database"] = f"error: {type(exc).__name__}"
    finally:
        db.close()

    try:
        get_storage().exists("healthcheck-probe")
        checks["storage"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["storage"] = f"error: {type(exc).__name__}"

    try:
        config = load_rule_config(settings.DEFAULT_BANK_ID)
        checks["rules"] = f"ok ({config.version})"
    except Exception as exc:  # noqa: BLE001
        checks["rules"] = f"error: {type(exc).__name__}"

    # The model server is not probed: a cold vLLM instance can take minutes to
    # load weights, and failing readiness for that would take the API down for
    # everything that does not need a model.
    checks["llm"] = "mock" if settings.LLM_USE_MOCK else settings.LLM_MODEL

    ready = all(not str(value).startswith("error") for value in checks.values())
    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {"ready": ready, "checks": checks}


@router.get("/health/config")
def configuration() -> dict[str, Any]:
    """Non-secret configuration, so a reviewer can see what produced a result."""
    return {
        "environment": settings.ENVIRONMENT,
        "analysis_version": settings.ANALYSIS_VERSION,
        "model": settings.LLM_MODEL,
        "llm_mock": settings.LLM_USE_MOCK,
        "storage_backend": settings.STORAGE_BACKEND,
        "task_queue": settings.TASK_QUEUE_BACKEND,
        "auth_enabled": settings.AUTH_ENABLED,
        "max_upload_mb": round(settings.MAX_UPLOAD_BYTES / (1024 * 1024), 1),
        "supported_extensions": sorted(settings.ALLOWED_EXTENSIONS),
        "rules": registered_rules(),
    }


@router.get("/banks")
def list_banks() -> list[dict[str, Any]]:
    """Selectable bank configurations, for the new-case form."""
    banks: list[dict[str, Any]] = []
    default = load_rule_config("default")
    banks.append(
        {
            "bank_id": "default",
            "name": "Default",
            "version": default.version,
            "required_documents": default.required_documents,
            "report_template": default.report_template,
        }
    )

    directory = settings.bank_config_dir
    if directory.exists():
        for path in sorted(directory.glob("*.yaml")):
            config = load_rule_config(path.stem)
            banks.append(
                {
                    "bank_id": path.stem,
                    "name": config.data.get("bank_name", path.stem),
                    "version": config.version,
                    "required_documents": config.required_documents,
                    "report_template": config.report_template,
                }
            )
    return banks
