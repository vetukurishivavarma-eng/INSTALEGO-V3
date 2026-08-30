"""Filesystem-backed storage for development and tests."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import BinaryIO

from app.storage.base import ObjectExistsError, ObjectNotFoundError, StoredObject, Storage


class LocalStorage(Storage):
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, key: str) -> Path:
        # Keys arrive from database rows; a traversal attempt must not escape
        # the storage root even if one is ever stored.
        path = (self.root / key).resolve()
        if not str(path).startswith(str(self.root)):
            raise ValueError(f"key escapes the storage root: {key}")
        return path

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str | None = None,
        overwrite: bool = False,
    ) -> StoredObject:
        path = self._resolve(key)
        if path.exists() and not overwrite:
            raise ObjectExistsError(f"refusing to overwrite {key}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, bytes):
            path.write_bytes(data)
        else:
            with open(path, "wb") as handle:
                shutil.copyfileobj(data, handle)
        return StoredObject(key=key, size_bytes=path.stat().st_size, content_type=content_type)

    def get(self, key: str) -> bytes:
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        return path.read_bytes()

    def open(self, key: str) -> BinaryIO:
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        return open(path, "rb")

    def exists(self, key: str) -> bool:
        return self._resolve(key).exists()

    def delete(self, key: str) -> None:
        path = self._resolve(key)
        if path.exists():
            path.unlink()

    def local_path(self, key: str) -> str:
        path = self._resolve(key)
        if not path.exists():
            raise ObjectNotFoundError(key)
        return str(path)
