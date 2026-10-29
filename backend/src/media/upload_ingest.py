"""Store an uploaded video and answer before any transcode or remote copy.

The request is finished once the bytes are on the shared uploads volume and
fsynced. Hashing happens while that file is published, so a retry of the same
bytes can reuse it. Copying the original into object storage, and scaling
sources above 1080p, both happen after the response: the copy on the worker
(with the task's normal progress), and the scale inside ``prepare_source``.
"""

from __future__ import annotations

import fcntl
import hashlib
import logging
import os
from pathlib import Path

import aiofiles
from starlette.datastructures import UploadFile

from ..config import get_config
from ..database import AsyncSessionLocal
from ..media.upload_limits import upload_too_large_detail
from ..media_storage import MediaStorage
from ..repositories.upload_repository import (
    get_stored_upload,
    save_storage_reference,
)
from ..utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024
# Per-file pointer at the durable object. Distinct from a cache directory's
# `.reference`, because one uploads directory holds many videos.
S3_REFERENCE_NAME = ".s3ref"


class UploadRejected(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def reference_sidecar(path: Path) -> Path:
    return path.with_name(f".{path.name}{S3_REFERENCE_NAME}")


def local_upload_path(video_path: str) -> Path:
    """Filesystem path for an `upload://` name. Rejects anything that escapes the directory."""
    if not isinstance(video_path, str) or not video_path.startswith("upload://") or video_path.startswith("upload://s3/"):
        raise ValueError("Invalid upload path")
    name = video_path.removeprefix("upload://")
    if not name or name != Path(name).name or name in {".", ".."}:
        raise ValueError("Invalid upload path")
    return Path(get_config().temp_dir) / "uploads" / name


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _write_sidecar(path: Path, reference: str) -> None:
    sidecar = reference_sidecar(path)
    temporary = sidecar.with_name(f"{sidecar.name}.{os.getpid()}.tmp")
    temporary.write_text(reference)
    _fsync_file(temporary)
    os.replace(temporary, sidecar)


async def publish_spooled_upload(upload: UploadFile, dest: Path, max_bytes: int) -> tuple[str, int]:
    """Publish one spooled upload into ``dest`` and return ``(sha256, size)``.

    Linking the spool avoids a second multi-gigabyte copy. A filesystem that
    cannot link ``/proc/self/fd`` falls back to a single streamed write, and
    the hash is computed on that pass.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    spool = upload.file
    await upload.seek(0)
    linked = False
    try:
        fd = spool.fileno()
        os.link(f"/proc/self/fd/{fd}", dest)
        linked = dest.is_file()
    except OSError:
        linked = False
        dest.unlink(missing_ok=True)

    hasher = hashlib.sha256()
    size = 0
    try:
        if linked:
            async with aiofiles.open(dest, "rb") as source:
                while True:
                    chunk = await source.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadRejected(413, upload_too_large_detail(max_bytes))
                    hasher.update(chunk)
        else:
            async with aiofiles.open(dest, "wb") as target:
                while True:
                    chunk = await upload.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise UploadRejected(413, upload_too_large_detail(max_bytes))
                    hasher.update(chunk)
                    await target.write(chunk)
        if size <= 0:
            raise UploadRejected(400, "No video file provided")
        await run_in_thread(_fsync_file, dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    return hasher.hexdigest(), size


async def upload_needs_remote_copy(video_path: str) -> bool:
    """True when object storage is on and this local upload has not been copied yet."""
    storage = MediaStorage()
    if not storage.enabled:
        return False
    try:
        local_upload_path(video_path)
    except ValueError:
        return False
    async with AsyncSessionLocal() as db:
        row = await get_stored_upload(db, video_path)
    return bool(row and not row.storage_reference)


async def persist_local_upload(video_path: str) -> None:
    """Copy a stored upload into object storage once. Safe to run more than once."""
    storage = MediaStorage()
    if not storage.enabled:
        return
    try:
        path = local_upload_path(video_path)
    except ValueError:
        return
    lock_path = path.with_name(f".{path.name}.persist.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    def acquire() -> object:
        handle = lock_path.open("a")
        fcntl.flock(handle, fcntl.LOCK_EX)
        return handle

    handle = await run_in_thread(acquire)
    try:
        async with AsyncSessionLocal() as db:
            row = await get_stored_upload(db, video_path)
            if row is None:
                return
            if row.storage_reference:
                if path.is_file():
                    await run_in_thread(_write_sidecar, path, row.storage_reference)
                    await run_in_thread(storage.save_source_metadata, path)
                return
            if not path.is_file():
                logger.warning("Stored upload is missing; object storage copy skipped")
                return
            owner = row.user_id
            session_id = row.id
        _relative, reference = await run_in_thread(storage.persist_source, path, owner)
        async with AsyncSessionLocal() as db:
            current = await get_stored_upload(db, video_path)
            if current and current.storage_reference:
                # Another worker finished the copy while the object was uploading.
                await run_in_thread(storage.delete, reference)
                reference = current.storage_reference
            else:
                await save_storage_reference(db, session_id, video_path, reference)
        if path.is_file():
            await run_in_thread(_write_sidecar, path, reference)
            await run_in_thread(storage.save_source_metadata, path)
    finally:
        def release(open_handle) -> None:
            fcntl.flock(open_handle, fcntl.LOCK_UN)
            open_handle.close()

        await run_in_thread(release, handle)


async def persist_upload_after_response(video_path: str) -> None:
    """Best-effort copy started after the client has the video path."""
    try:
        await persist_local_upload(video_path)
    except Exception:
        logger.exception("Stored upload will be copied to object storage when processing starts")


async def materialize_stored_upload(video_path: str) -> Path | None:
    """Return the local upload, or the object-storage copy when the file is gone."""
    try:
        path = local_upload_path(video_path)
    except ValueError:
        return None
    if path.is_file():
        return path
    reference = read_upload_reference(path)
    if not reference:
        async with AsyncSessionLocal() as db:
            row = await get_stored_upload(db, video_path)
        reference = row.storage_reference if row is not None else None
        if reference:
            path.parent.mkdir(parents=True, exist_ok=True)
            await run_in_thread(_write_sidecar, path, reference)
    if not reference:
        return path
    return await run_in_thread(MediaStorage().local_path, reference)


async def ensure_local_upload_durable(path: Path) -> None:
    """Copy an uploads-directory file if object storage does not have it yet."""
    path = Path(path)
    root = (Path(get_config().temp_dir) / "uploads").resolve()
    try:
        resolved = path.resolve()
    except OSError:
        return
    if not resolved.is_relative_to(root):
        return
    await persist_local_upload(f"upload://{resolved.name}")


def read_upload_reference(path: Path) -> str | None:
    sidecar = reference_sidecar(path)
    try:
        if not sidecar.is_file():
            return None
        reference = sidecar.read_text().strip()
    except OSError:
        return None
    return reference or None


async def foreign_upload(db, video_path: str, user_id: str) -> bool:
    """True when this account does not own the stored upload.

    A file that is still on disk without a completed session is rejected too.
    Session cleanup used to drop the ownership row while the video remained,
    and any account that knew the ``upload://`` path could then start a task.
    """
    if db is None or not isinstance(video_path, str) or not video_path.startswith("upload://") or video_path.startswith("upload://s3/"):
        return False
    row = await get_stored_upload(db, video_path)
    if row is not None:
        return row.user_id != user_id
    try:
        path = local_upload_path(video_path)
    except ValueError:
        return True
    return path.is_file()

