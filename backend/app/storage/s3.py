"""S3-compatible storage (MinIO in Compose, S3 in production)."""

from __future__ import annotations

import io
import tempfile
from pathlib import Path
from typing import BinaryIO

from app.storage.base import ObjectExistsError, ObjectNotFoundError, StoredObject, Storage


class S3Storage(Storage):
    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        region: str = "us-east-1",
        ensure_bucket: bool = True,
    ) -> None:
        import boto3  # imported lazily so local dev needs no AWS dependency at import time

        self.bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
        )
        self._temp_dir = Path(tempfile.gettempdir()) / "ldai-s3-cache"
        self._temp_dir.mkdir(parents=True, exist_ok=True)
        if ensure_bucket:
            self._ensure_bucket()

    def _ensure_bucket(self) -> None:
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self.bucket)
        except ClientError:
            # MinIO starts empty; creating on first use keeps Compose one command.
            self._client.create_bucket(Bucket=self.bucket)

    def put(
        self,
        key: str,
        data: bytes | BinaryIO,
        *,
        content_type: str | None = None,
        overwrite: bool = False,
    ) -> StoredObject:
        if not overwrite and self.exists(key):
            raise ObjectExistsError(f"refusing to overwrite {key}")
        body = data if isinstance(data, bytes) else data.read()
        extra = {"ContentType": content_type} if content_type else {}
        self._client.put_object(Bucket=self.bucket, Key=key, Body=body, **extra)
        return StoredObject(key=key, size_bytes=len(body), content_type=content_type)

    def get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            raise ObjectNotFoundError(key) from exc
        return response["Body"].read()

    def open(self, key: str) -> BinaryIO:
        return io.BytesIO(self.get(key))

    def exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError:
            return False
        return True

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)

    def local_path(self, key: str) -> str:
        """Materialise the object so PyMuPDF and friends can open it by path."""
        target = self._temp_dir / key.replace("/", "_")
        if not target.exists():
            target.write_bytes(self.get(key))
        return str(target)
