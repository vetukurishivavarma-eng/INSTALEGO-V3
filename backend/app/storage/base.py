"""Object storage interface.

Two implementations exist: the local filesystem for development and an
S3-compatible backend (MinIO in Compose, S3 in production). Callers only ever
see keys, never paths, so the two are interchangeable.

Originals are immutable. ``put`` refuses to overwrite an existing key unless
explicitly told otherwise, which is the storage-level half of the guarantee
that an uploaded document is never modified.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import BinaryIO


class StorageError(RuntimeError):
    pass


class ObjectExistsError(StorageError):
    pass


class ObjectNotFoundError(StorageError):
    pass


@dataclass(frozen=True)
class StoredObject:
    key: str
    size_bytes: int
    content_type: str | None = None


class Storage(abc.ABC):
    """Content-addressed-ish blob store keyed by opaque strings."""

    @abc.abstractmethod
    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str | None = None,
        overwrite: bool = False,
    ) -> StoredObject: ...

    @abc.abstractmethod
    def get(self, key: str) -> bytes: ...

    @abc.abstractmethod
    def open(self, key: str) -> BinaryIO: ...

    @abc.abstractmethod
    def exists(self, key: str) -> bool: ...

    @abc.abstractmethod
    def delete(self, key: str) -> None: ...

    @abc.abstractmethod
    def local_path(self, key: str) -> str:
        """A real filesystem path for libraries that cannot take a stream.

        S3-backed implementations materialise a temporary copy; callers must
        treat the result as read-only and short-lived.
        """

    @staticmethod
    def document_key(case_id: str, document_id: str, filename: str) -> str:
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        return f"cases/{case_id}/documents/{document_id}/original.{suffix}"

    @staticmethod
    def page_image_key(case_id: str, document_id: str, page_number: int) -> str:
        return f"cases/{case_id}/documents/{document_id}/pages/{page_number:04d}.png"

    @staticmethod
    def report_key(case_id: str, report_id: str, extension: str) -> str:
        return f"cases/{case_id}/reports/{report_id}.{extension}"
