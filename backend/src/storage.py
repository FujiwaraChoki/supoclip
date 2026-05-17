"""
File storage abstraction for clip artefacts.

Backend and worker may run on isolated filesystems (e.g. ECS Fargate tasks
without a shared volume). Storing the clip on the worker's local disk and
then trying to read it from the backend's local disk fails — they don't
share storage.

This module provides a thin wrapper that can transparently upload to S3
when `STORAGE_BUCKET` is set, and falls back to local-only behaviour
otherwise (preserving the docker-compose dev experience).

Convention: the value persisted in `generated_clips.file_path` is either:
    - a relative or absolute local path (legacy / dev mode), OR
    - an `s3://{bucket}/{key}` URI (when S3 storage is enabled)

`resolve()` accepts both shapes and returns a local `Path` that downstream
code (ffmpeg, FileResponse) can read.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from .utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

S3_URI_SCHEME = "s3://"


class FileStorage:
    """Pluggable storage for clip files.

    When `bucket` is set, writes go to S3 under `key_prefix/{filename}` and
    reads pull from S3 into `local_cache_dir`. When `bucket` is empty, all
    operations no-op against local paths — the file_path passed in is
    returned unchanged from `save()` and `resolve()` just wraps it in a Path.
    """

    def __init__(
        self,
        bucket: Optional[str],
        local_cache_dir: Path,
        key_prefix: str = "clips/",
    ):
        self.bucket = bucket
        self.local_cache_dir = local_cache_dir
        # Ensure key_prefix has a trailing slash so we can concat filenames directly.
        self.key_prefix = key_prefix if key_prefix.endswith("/") else f"{key_prefix}/"
        self._s3 = boto3.client("s3") if bucket else None

    @property
    def enabled(self) -> bool:
        return bool(self.bucket)

    async def save(self, local_path: Path, key: Optional[str] = None) -> str:
        """Upload a local file and return a storage URI.

        When S3 storage is enabled: uploads to `s3://{bucket}/{key_prefix}{key or filename}`
        and returns that URI. The caller may delete the local copy after this
        returns; reads will fetch from S3 transparently via `resolve()`.

        When disabled: returns `str(local_path)` unchanged — backward-compatible
        with the docker-compose path-on-shared-volume convention.

        Async because boto3's upload_file is blocking; offloading to a worker
        thread keeps the event loop responsive during multi-MB uploads.
        """
        if not self.enabled or self._s3 is None:
            return str(local_path)
        if not local_path.exists():
            raise FileNotFoundError(f"Cannot upload missing file: {local_path}")
        object_key = f"{self.key_prefix}{key or local_path.name}"
        try:
            await run_in_thread(self._s3.upload_file, str(local_path), self.bucket, object_key)
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 upload failed for %s → %s: %s", local_path, object_key, exc)
            raise
        uri = f"{S3_URI_SCHEME}{self.bucket}/{object_key}"
        logger.info("Uploaded %s → %s", local_path.name, uri)
        return uri

    async def resolve(self, stored: str) -> Path:
        """Return a local Path for reading, downloading from S3 if needed.

        Files cached under `local_cache_dir` survive only as long as the
        task does — fine for short-lived merge/serve operations.

        Async because boto3's download_file is blocking; offloading to a
        worker thread keeps the event loop responsive during multi-MB
        downloads.
        """
        if not stored.startswith(S3_URI_SCHEME):
            return Path(stored)
        if self._s3 is None:
            raise RuntimeError(
                f"Encountered S3 URI {stored} but storage is not configured; set STORAGE_BUCKET."
            )
        parsed = urlparse(stored)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        # Cache under the same key path so repeated reads in a single task share the download.
        cache_path = self.local_cache_dir / key
        if cache_path.exists():
            return cache_path
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            await run_in_thread(self._s3.download_file, bucket, key, str(cache_path))
        except (BotoCoreError, ClientError) as exc:
            logger.error("S3 download failed for %s: %s", stored, exc)
            cache_path.unlink(missing_ok=True)
            raise
        logger.info("Downloaded %s → %s", stored, cache_path)
        return cache_path

    async def delete(self, stored: str) -> None:
        """Best-effort delete — for cleanup paths. Never raises."""
        if not stored.startswith(S3_URI_SCHEME):
            try:
                Path(stored).unlink(missing_ok=True)
            except OSError:
                logger.warning("Failed to delete local file %s", stored, exc_info=True)
            return
        if self._s3 is None:
            return
        parsed = urlparse(stored)
        try:
            await run_in_thread(
                self._s3.delete_object, Bucket=parsed.netloc, Key=parsed.path.lstrip("/")
            )
        except (BotoCoreError, ClientError):
            logger.warning("Failed to delete S3 object %s", stored, exc_info=True)


_default_storage: Optional[FileStorage] = None


def get_storage() -> FileStorage:
    """Module-level singleton — initialised lazily from env."""
    global _default_storage
    if _default_storage is None:
        # Local import to avoid circular dependency at module load.
        from .config import get_config

        bucket = os.getenv("STORAGE_BUCKET") or None
        local_cache = Path(get_config().temp_dir) / "storage_cache"
        _default_storage = FileStorage(bucket=bucket, local_cache_dir=local_cache)
        logger.info("FileStorage initialised: bucket=%s cache=%s", bucket, local_cache)
    return _default_storage
