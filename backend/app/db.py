"""Database engine, session management and portable column types.

The same models must run on PostgreSQL (Compose, production) and on SQLite
(local development and the test suite), so UUID and JSON columns go through
type decorators that pick the native type when the dialect has one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

from sqlalchemy import CHAR, JSON, TypeDecorator, create_engine, event
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import settings


class GUID(TypeDecorator):
    """UUID column: native ``uuid`` on PostgreSQL, 36-char text elsewhere."""

    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PG_UUID(as_uuid=True))
        return dialect.type_descriptor(CHAR(36))

    def process_bind_param(self, value: Any, dialect) -> Any:  # noqa: ANN001
        if value is None:
            return None
        if not isinstance(value, uuid.UUID):
            value = uuid.UUID(str(value))
        return value if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value: Any, dialect) -> uuid.UUID | None:  # noqa: ANN001
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONType(TypeDecorator):
    """JSON column: ``JSONB`` on PostgreSQL, plain JSON elsewhere."""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):  # noqa: ANN001, ANN201
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType}


def _engine_kwargs() -> dict[str, Any]:
    if settings.DATABASE_URL.startswith("sqlite"):
        # check_same_thread=False: FastAPI's threadpool hands sessions between
        # threads, and the inline task backend runs the pipeline on one of them.
        return {"connect_args": {"check_same_thread": False}, "future": True}
    return {"pool_pre_ping": True, "pool_size": 10, "max_overflow": 20, "future": True}


engine: Engine = create_engine(settings.DATABASE_URL, **_engine_kwargs())
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, class_=Session)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record) -> None:  # noqa: ANN001
    """Foreign keys are off by default in SQLite; the cascades depend on them."""
    if settings.DATABASE_URL.startswith("sqlite"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def get_db() -> Iterator[Session]:
    """FastAPI dependency yielding a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    """Create tables from the models.

    Alembic owns schema changes in production; this exists so local dev and the
    test suite can stand a database up without a migration run.
    """
    from app import models  # noqa: F401  (import registers the mappers)

    Base.metadata.create_all(bind=engine)
