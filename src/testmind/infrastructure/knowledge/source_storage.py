"""Private, atomic source storage with opaque generated object keys."""

from __future__ import annotations

import hashlib
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Protocol
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class StoredObject:
    key: str
    sha256: str
    size_bytes: int


class SourceStorage(Protocol):
    def save(self, upload_stream: BinaryIO, *, suffix: str) -> StoredObject: ...

    def open(self, key: str) -> BinaryIO: ...

    def delete(self, key: str) -> None: ...


class FilesystemSourceStorage:
    """Shared-volume adapter; production deployments should inject private S3 storage."""

    def __init__(self, root: str | Path, *, max_bytes: int = 25 * 1024 * 1024) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)
        self._max_bytes = max_bytes

    def save(self, upload_stream: BinaryIO, *, suffix: str) -> StoredObject:
        if suffix not in {".pdf", ".md", ".markdown", ".txt"}:
            raise ValueError("Unsupported source suffix")
        key = f"{uuid4().hex}{suffix}"
        target = self._resolve(key)
        fd, temp_name = tempfile.mkstemp(prefix=".upload-", dir=self._root)
        digest = hashlib.sha256()
        size = 0
        try:
            with os.fdopen(fd, "wb") as output:
                while block := upload_stream.read(1024 * 1024):
                    size += len(block)
                    if size > self._max_bytes:
                        raise ValueError("Upload exceeds configured size limit")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temp_name, target)
            return StoredObject(key=key, sha256=digest.hexdigest(), size_bytes=size)
        except Exception:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass
            raise

    def open(self, key: str) -> BinaryIO:
        return self._resolve(key).open("rb")

    def delete(self, key: str) -> None:
        self._resolve(key).unlink(missing_ok=True)

    def _resolve(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if Path(key).name != key or candidate.parent != self._root:
            raise ValueError("Invalid source object key")
        return candidate


class MinioSourceStorage:
    """Private S3-compatible object storage adapter using generated object names."""

    def __init__(self, client: object, *, bucket: str, max_bytes: int) -> None:
        self._client = client
        self._bucket = bucket
        self._max_bytes = max_bytes

    def save(self, upload_stream: BinaryIO, *, suffix: str) -> StoredObject:
        if suffix not in {".pdf", ".md", ".markdown", ".txt"}:
            raise ValueError("Unsupported source suffix")
        key = f"{uuid4().hex}{suffix}"
        digest = hashlib.sha256()
        size = 0
        with tempfile.TemporaryFile(mode="w+b") as staged:
            while block := upload_stream.read(1024 * 1024):
                size += len(block)
                if size > self._max_bytes:
                    raise ValueError("Upload exceeds configured size limit")
                digest.update(block)
                staged.write(block)
            staged.flush()
            os.fsync(staged.fileno())
            staged.seek(0)
            content_type = {
                ".pdf": "application/pdf",
                ".md": "text/markdown",
                ".markdown": "text/markdown",
                ".txt": "text/plain",
            }[suffix]
            self._client.put_object(
                self._bucket, key, staged, size, content_type=content_type
            )
        return StoredObject(key=key, sha256=digest.hexdigest(), size_bytes=size)

    def open(self, key: str) -> BinaryIO:
        self._validate_key(key)
        return _ObjectReader(self._client.get_object(self._bucket, key))

    def delete(self, key: str) -> None:
        self._validate_key(key)
        self._client.remove_object(self._bucket, key)

    @staticmethod
    def _validate_key(key: str) -> None:
        if not key or Path(key).name != key or key.startswith("."):
            raise ValueError("Invalid source object key")


class _ObjectReader:
    def __init__(self, response: object) -> None:
        self._response = response

    def read(self, size: int = -1) -> bytes:
        return self._response.read(size)

    def close(self) -> None:
        self._response.close()
        release = getattr(self._response, "release_conn", None)
        if release:
            release()

    def __enter__(self) -> _ObjectReader:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


def create_source_storage(config: object) -> SourceStorage:
    mode = config.knowledge_storage_mode
    if mode == "filesystem":
        from testmind.core.config import _ROOT_DIR

        root = Path(config.knowledge_source_dir)
        if not root.is_absolute():
            root = _ROOT_DIR / root
        return FilesystemSourceStorage(root, max_bytes=config.knowledge_max_upload_bytes)
    if mode == "minio":
        from minio import Minio

        client = Minio(
            config.minio_endpoint,
            access_key=config.minio_access_key,
            secret_key=config.minio_secret_key,
            secure=config.minio_secure,
        )
        return MinioSourceStorage(
            client,
            bucket=config.minio_bucket,
            max_bytes=config.knowledge_max_upload_bytes,
        )
    raise ValueError("Unsupported knowledge storage mode")
