"""Storage backend selection."""

from __future__ import annotations

from functools import lru_cache

from app.config import settings
from app.storage.base import (
    ObjectExistsError,
    ObjectNotFoundError,
    Storage,
    StorageError,
    StoredObject,
)
from app.storage.local import LocalStorage


@lru_cache
def get_storage() -> Storage:
    if settings.STORAGE_BACKEND == "s3":
        from app.storage.s3 import S3Storage

        return S3Storage(
            bucket=settings.S3_BUCKET,
            endpoint_url=settings.S3_ENDPOINT,
            access_key=settings.S3_ACCESS_KEY,
            secret_key=settings.S3_SECRET_KEY,
            region=settings.S3_REGION,
        )
    return LocalStorage(settings.STORAGE_LOCAL_ROOT)


__all__ = [
    "LocalStorage",
    "ObjectExistsError",
    "ObjectNotFoundError",
    "Storage",
    "StorageError",
    "StoredObject",
    "get_storage",
]
