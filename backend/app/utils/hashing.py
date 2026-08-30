"""Content hashing for document identity and duplicate detection."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import BinaryIO

CHUNK = 1024 * 1024


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_stream(stream: BinaryIO) -> str:
    """Hash without holding the whole file in memory. Leaves the cursor at EOF."""
    digest = hashlib.sha256()
    while chunk := stream.read(CHUNK):
        digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: str | Path) -> str:
    with open(path, "rb") as handle:
        return sha256_stream(handle)
