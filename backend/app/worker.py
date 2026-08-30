"""The arq worker process.

Run with: arq app.worker.WorkerSettings

Each job opens its own database session and commits once. A job that raises is
retried by arq; the pipeline is written so a re-run replaces its own previous
output rather than duplicating it, which is what makes that retry safe.
"""

from __future__ import annotations

import logging

from arq import func
from arq.connections import RedisSettings

from app.config import settings
from app.db import SessionLocal
from app.logging_config import configure_logging
from app.tasks import ANALYSIS_TASK
from app.workflows.analysis_workflow import run_analysis
from app.workflows.report_workflow import generate_report

logger = logging.getLogger(__name__)


async def run_case_analysis(ctx: dict, case_id: str, actor: str = "worker") -> dict:  # noqa: ARG001
    db = SessionLocal()
    try:
        result = run_analysis(db, case_id, actor=actor)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    return {
        "case_id": result.case_id,
        "status": str(result.status),
        "discrepancies": result.discrepancies,
        "high_severity": result.high_severity,
        "llm_calls": result.llm_calls,
    }


async def run_report_generation(ctx: dict, case_id: str, actor: str = "worker") -> dict:  # noqa: ARG001
    db = SessionLocal()
    try:
        report = generate_report(db, case_id, actor=actor)
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"report_id": str(report.id), "status": report.status}


async def startup(ctx: dict) -> None:  # noqa: ARG001
    configure_logging()
    logger.info(
        "worker ready (model=%s, storage=%s)", settings.LLM_MODEL, settings.STORAGE_BACKEND
    )


class WorkerSettings:
    functions = [
        func(run_case_analysis, name=ANALYSIS_TASK),
        func(run_report_generation, name='run_report_generation'),
    ]
    on_startup = startup
    redis_settings = RedisSettings.from_dsn(settings.REDIS_URL)
    # Document analysis is minutes of work, not seconds.
    job_timeout = 1800
    max_tries = 2
    keep_result = 3600
