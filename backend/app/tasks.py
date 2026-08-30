"""Background job dispatch.

Two backends behind one function. ``arq`` enqueues to the Redis-backed worker,
which is what Compose and production run. ``inline`` executes in-process, which
is what makes the pipeline runnable and testable on a machine with no Redis —
and it is honest about the tradeoff: the request blocks for the duration.

The API never calls the pipeline directly; it calls ``enqueue_analysis``.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.config import settings

logger = logging.getLogger(__name__)

ANALYSIS_TASK = "run_case_analysis"


def enqueue_analysis(case_id: UUID | str, *, actor: str = "api") -> dict[str, Any]:
    """Schedule analysis for a case. Returns what the API should report."""
    if settings.TASK_QUEUE_BACKEND == "arq":
        return _enqueue_arq(case_id, actor=actor)
    return _run_inline(case_id, actor=actor)


def _enqueue_arq(case_id: UUID | str, *, actor: str) -> dict[str, Any]:
    import asyncio

    from arq import create_pool
    from arq.connections import RedisSettings

    async def submit() -> str | None:
        pool = await create_pool(RedisSettings.from_dsn(settings.REDIS_URL))
        try:
            job = await pool.enqueue_job(ANALYSIS_TASK, str(case_id), actor)
            return job.job_id if job else None
        finally:
            await pool.close()

    try:
        job_id = asyncio.run(submit())
    except Exception as exc:  # noqa: BLE001
        # Failing loudly matters here: a silently dropped job looks to the user
        # exactly like a case that is still processing, forever.
        logger.exception("could not enqueue analysis for case %s", case_id)
        return {"queued": False, "backend": "arq", "error": f"{type(exc).__name__}: {exc}"}

    return {"queued": True, "backend": "arq", "job_id": job_id}


def _run_inline(case_id: UUID | str, *, actor: str) -> dict[str, Any]:
    from app.db import SessionLocal
    from app.workflows.analysis_workflow import run_analysis

    db = SessionLocal()
    try:
        result = run_analysis(db, case_id, actor=actor)
        db.commit()
    except Exception as exc:  # noqa: BLE001
        db.rollback()
        logger.exception("inline analysis failed for case %s", case_id)
        return {"queued": False, "backend": "inline", "error": f"{type(exc).__name__}: {exc}"}
    finally:
        db.close()

    return {
        "queued": True,
        "backend": "inline",
        "status": str(result.status),
        "discrepancies": result.discrepancies,
        "llm_calls": result.llm_calls,
    }
