"""FastAPI application.

Routes are thin: they validate, delegate to a service, and shape a response.
Business logic lives in services, workflows and rules, which is what lets the
worker run the same pipeline with no HTTP layer involved at all.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import analysis, auth, cases, documents, health, reports
from app.config import settings
from app.logging_config import configure_logging
from app.services.document_service import UploadRejected

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):  # noqa: ANN201
    configure_logging()
    logger.info(
        "starting (env=%s, storage=%s, queue=%s, model=%s%s)",
        settings.ENVIRONMENT,
        settings.STORAGE_BACKEND,
        settings.TASK_QUEUE_BACKEND,
        settings.LLM_MODEL,
        ", MOCKED" if settings.LLM_USE_MOCK else "",
    )

    if settings.is_production:
        # Two configurations that are merely inconvenient in development and
        # unacceptable in production. Failing at startup beats discovering
        # either one from a report.
        if settings.LLM_USE_MOCK:
            raise RuntimeError("LLM_USE_MOCK must be false in production")
        if not settings.AUTH_ENABLED:
            raise RuntimeError("AUTH_ENABLED must be true in production")

    if settings.DATABASE_URL.startswith("sqlite"):
        # Convenience for local runs; Postgres deployments are migrated with
        # Alembic instead.
        from app.db import init_db

        init_db()
        logger.info("sqlite schema ensured")

    yield
    logger.info("shutting down")


app = FastAPI(
    title="Legal Document AI",
    description=(
        "Banking legal document analysis and discrepancy detection. "
        "A decision-support system for manual review, not an autonomous decision maker."
    ),
    version=settings.ANALYSIS_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://localhost:3000", "http://localhost:4173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(UploadRejected)
async def upload_rejected_handler(request: Request, exc: UploadRejected) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={"error_code": str(exc.code), "detail": exc.detail},
    )


@app.exception_handler(Exception)
async def unhandled_handler(request: Request, exc: Exception) -> JSONResponse:
    # The message is logged in full but never returned: an exception string can
    # carry a file path or a fragment of a document.
    logger.exception("unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error_code": "INTERNAL_ERROR", "detail": "an unexpected error occurred"},
    )


app.include_router(health.router, prefix=settings.API_PREFIX)
app.include_router(auth.router, prefix=settings.API_PREFIX)
app.include_router(cases.router, prefix=settings.API_PREFIX)
app.include_router(documents.router, prefix=settings.API_PREFIX)
app.include_router(analysis.router, prefix=settings.API_PREFIX)
app.include_router(reports.router, prefix=settings.API_PREFIX)


@app.get("/")
def root() -> dict[str, str]:
    return {
        "service": "legal-document-ai",
        "docs": "/docs",
        "health": f"{settings.API_PREFIX}/health",
    }
