"""Queue platform-preset clip exports and serve the finished file.

The synchronous ``GET .../export`` route still encodes inside the request.
These jobs use the existing ARQ worker so clients can poll instead of holding
a connection open for the whole ffmpeg run.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import shutil
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession

from ..clip_editor import ExportCancelled, export_with_preset
from ..config import get_config
from ..media_storage import STORAGE_ERRORS, MediaStorage, is_s3, media_work
from ..observability import set_trace_id, set_user_id
from ..repositories.clip_export_repository import ClipExportRepository
from ..repositories.clip_repository import ClipRepository
from ..repositories.media_repository import queue_delete
from ..utils.async_helpers import run_in_thread
from ..workers.job_queue import DuplicateJobError

logger = logging.getLogger(__name__)

CLIP_EXPORT_JOB_NAME = "export_clip_preset"
CLIP_EXPORT_TTL = timedelta(days=7)
CLIP_EXPORT_TTL_SECONDS = int(CLIP_EXPORT_TTL.total_seconds())
# A live worker touches the row every couple of seconds. A few minutes without
# a touch means the worker that owned the job is gone.
CLIP_EXPORT_STALE_SECONDS = 180
CLIP_EXPORT_JOB_TIMEOUT_SECONDS = 30 * 60
STALE_MESSAGE = "Export timed out before it finished."
FAILED_MESSAGE = "Export failed. Try again."
WORKER_UNAVAILABLE_MESSAGE = "The export worker is unavailable."
CLIP_CHANGED_MESSAGE = "The clip changed before the export finished."
FILE_MISSING_MESSAGE = "Export file is no longer available."
LOCAL_DIR_NAME = "clip-exports"


class ExportWorkerUnavailable(RuntimeError):
    pass


def export_options_hash(preset: str, source_file_path: str) -> str:
    """Identity of one export. A regenerated clip has a new source path."""
    payload = json.dumps(
        {"preset": preset, "source_file_path": source_file_path},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def export_download_filename(clip_filename: str, preset: str) -> str:
    suffix = f"_{preset}.mp4"
    stem = Path(clip_filename).name
    if stem.lower().endswith(".mp4"):
        stem = stem[:-4]
    for character in ('"', "\r", "\n", "/", "\\"):
        stem = stem.replace(character, "")
    stem = stem.strip() or "clip"
    return f"{stem[: 255 - len(suffix)]}{suffix}"


def job_id_for(export_id: str) -> str:
    return f"clip-export:{export_id}"


def api_download_path(row: dict) -> str:
    return f"/tasks/{row['task_id']}/clips/{row['clip_id']}/exports/{row['id']}/file"


def serialize_export(row: dict, download_url: str | None) -> dict:
    status = row["status"]
    return {
        "id": row["id"],
        "status": status,
        "progress": int(row.get("progress") or 0),
        "preset": row["preset"],
        "error": row.get("error_message") if status == "failed" else None,
        "download_url": download_url if status == "succeeded" else None,
    }


def artifact_state(storage: MediaStorage, file_path: str | None) -> str:
    """Return present, missing, or unknown (storage could not be checked)."""
    if not file_path:
        return "missing"
    try:
        if is_s3(file_path):
            return "present" if storage.exists(file_path) else "missing"
        path = Path(file_path).resolve()
        root = Path(storage.config.temp_dir).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            return "missing"
        return "present"
    except STORAGE_ERRORS:
        return "unknown"


def local_export_dir(temp_dir: str, export_id: str) -> Path:
    return Path(temp_dir) / LOCAL_DIR_NAME / export_id


def remove_local_export_dir(path: Path, temp_dir: str) -> None:
    root = (Path(temp_dir) / LOCAL_DIR_NAME).resolve()
    try:
        directory = path.resolve()
    except OSError:
        return
    if directory.is_relative_to(root) and directory != root and directory.is_dir():
        shutil.rmtree(directory, ignore_errors=True)


def sweep_local_export_dirs(storage: MediaStorage, live_ids: set[str]) -> None:
    """Drop encode directories left behind by a crashed worker."""
    import time

    root = Path(storage.config.temp_dir) / LOCAL_DIR_NAME
    if not root.is_dir():
        return
    cutoff = time.time() - 3600
    for directory in root.iterdir():
        if not directory.is_dir() or directory.name in live_ids:
            continue
        try:
            latest = max(
                (item.stat().st_mtime for item in directory.rglob("*") if item.is_file()),
                default=directory.stat().st_mtime,
            )
        except OSError:
            continue
        if latest < cutoff:
            shutil.rmtree(directory, ignore_errors=True)


async def resolve_download_url(storage: MediaStorage, row: dict) -> str | None:
    if row["status"] != "succeeded":
        return None
    file_path = row.get("file_path")
    if file_path and is_s3(file_path):
        try:
            return await run_in_thread(
                storage.signed_url,
                file_path,
                row["download_filename"],
                attachment=True,
            )
        except STORAGE_ERRORS:
            logger.warning("Could not sign clip export %s", row["id"])
    return api_download_path(row)


async def present_export(db: AsyncSession, row: dict) -> dict:
    """Reclaim a dead job or a missing file, then attach a download URL."""
    failed = await ClipExportRepository.fail_matching(
        db,
        message=STALE_MESSAGE,
        stale_seconds=CLIP_EXPORT_STALE_SECONDS,
        export_id=row["id"],
    )
    if failed:
        await db.commit()
        refreshed = await ClipExportRepository.get(db, row["id"])
        row = refreshed or row
    if row["status"] == "succeeded":
        state = await run_in_thread(artifact_state, MediaStorage(), row.get("file_path"))
        if state == "missing":
            if row.get("file_path"):
                await queue_delete(db, row["file_path"])
            await ClipExportRepository.mark_failed(
                db,
                row["id"],
                FILE_MISSING_MESSAGE,
                statuses=("succeeded",),
            )
            await db.commit()
            refreshed = await ClipExportRepository.get(db, row["id"])
            row = refreshed or {**row, "status": "failed", "error_message": FILE_MISSING_MESSAGE}
    download_url = await resolve_download_url(MediaStorage(), row)
    return serialize_export(row, download_url)


async def start_export(
    db: AsyncSession,
    *,
    task: dict,
    clip: dict,
    preset: str,
    queue,
) -> dict:
    options_hash = export_options_hash(preset, clip["file_path"])
    await ClipExportRepository.lock_options(db, clip["id"], options_hash)
    await ClipExportRepository.fail_matching(
        db,
        message=STALE_MESSAGE,
        stale_seconds=CLIP_EXPORT_STALE_SECONDS,
        clip_id=clip["id"],
        options_hash=options_hash,
    )
    existing = await ClipExportRepository.get_live(
        db, clip["id"], options_hash, for_update=True
    )
    if existing and existing["status"] == "succeeded":
        state = await run_in_thread(
            artifact_state, MediaStorage(), existing.get("file_path")
        )
        if state == "missing" or _expired(existing):
            if existing.get("file_path"):
                await queue_delete(db, existing["file_path"])
            message = FILE_MISSING_MESSAGE if state == "missing" else "Export expired."
            await ClipExportRepository.mark_failed(
                db, existing["id"], message, statuses=("succeeded",)
            )
            existing = None
    if existing:
        await db.commit()
        refreshed = await ClipExportRepository.get(db, existing["id"])
        return await present_export(db, refreshed or existing)

    export_id = str(uuid4())
    created = await ClipExportRepository.insert(
        db,
        export_id=export_id,
        task_id=task["id"],
        clip_id=clip["id"],
        user_id=task["user_id"],
        preset=preset,
        options_hash=options_hash,
        source_file_path=clip["file_path"],
        download_filename=export_download_filename(clip["filename"], preset),
        ttl_seconds=CLIP_EXPORT_TTL_SECONDS,
    )
    await db.commit()
    try:
        await queue.enqueue_job(
            CLIP_EXPORT_JOB_NAME,
            export_id,
            _job_id=job_id_for(export_id),
        )
    except DuplicateJobError:
        logger.info("Clip export %s is already queued", export_id)
    except Exception:
        logger.exception("Failed to enqueue clip export %s", export_id)
        await ClipExportRepository.mark_failed(db, export_id, WORKER_UNAVAILABLE_MESSAGE)
        await db.commit()
        raise ExportWorkerUnavailable(WORKER_UNAVAILABLE_MESSAGE) from None
    return serialize_export(created, None)


def _expired(row: dict) -> bool:
    expires_at = row.get("expires_at")
    if expires_at is None:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= datetime.now(timezone.utc)


async def expire_clip_exports(db: AsyncSession, storage: MediaStorage, *, limit: int = 50) -> dict:
    stale = await ClipExportRepository.fail_matching(
        db,
        message=STALE_MESSAGE,
        stale_seconds=CLIP_EXPORT_STALE_SECONDS,
    )
    await db.commit()
    removed = 0
    for row in await ClipExportRepository.lock_expired(db, limit=limit):
        if row.get("file_path"):
            await queue_delete(db, row["file_path"])
        await ClipExportRepository.delete(db, row["id"])
        await db.commit()
        removed += 1
    purged = await ClipExportRepository.purge_failures(db, days=CLIP_EXPORT_TTL.days)
    await db.commit()
    live_ids = await ClipExportRepository.live_ids(db)
    await run_in_thread(sweep_local_export_dirs, storage, live_ids)
    return {"stale_failed": stale, "expired": removed, "purged": purged}


async def _record_failure(export_id: str, message: str) -> None:
    from ..database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        await ClipExportRepository.mark_failed(db, export_id, message)
        await db.commit()


@media_work
async def export_clip_preset(ctx, export_id: str) -> dict:
    """ARQ job: re-encode one clip with a platform preset and store the file."""
    from ..database import AsyncSessionLocal

    set_trace_id(f"clip-export-{export_id}")
    async with AsyncSessionLocal() as db:
        row = await ClipExportRepository.get(db, export_id)
    if not row or row["status"] in {"succeeded", "failed"}:
        return {"export_id": export_id, "status": None if not row else row["status"]}
    set_user_id(row["user_id"])
    logger.info("Worker exporting clip %s as %s", row["clip_id"], row["preset"])

    async with AsyncSessionLocal() as db:
        started = await ClipExportRepository.mark_running(db, export_id)
        await db.commit()
    if not started:
        return {"export_id": export_id, "status": "skipped"}

    storage = MediaStorage()
    config = get_config()
    directory = local_export_dir(config.temp_dir, export_id)
    output_path = directory / f"{export_id}.mp4"
    stop = threading.Event()
    progress = {"value": 1}
    stored_path: str | None = None
    try:
        async with AsyncSessionLocal() as db:
            clip = await ClipRepository.get_clip_by_id(db, row["clip_id"])
        if (
            not clip
            or clip["task_id"] != row["task_id"]
            or clip["file_path"] != row["source_file_path"]
        ):
            await _record_failure(export_id, CLIP_CHANGED_MESSAGE)
            return {"export_id": export_id, "status": "failed"}

        source = await run_in_thread(storage.local_path, clip["file_path"])
        pump = asyncio.create_task(_pump_progress(export_id, progress, stop))
        try:
            await run_in_thread(
                export_with_preset,
                Path(source),
                directory,
                row["preset"],
                output_path=output_path,
                on_progress=lambda percent: progress.__setitem__("value", percent),
                should_stop=stop.is_set,
            )
        finally:
            stop.set()
            pump.cancel()
            try:
                await pump
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.warning("Clip export %s stopped reporting progress", export_id, exc_info=True)

        reference = str(output_path)
        if storage.enabled:
            key = storage.key("exports", f"{export_id}.mp4")
            reference = await run_in_thread(
                storage.upload_file, output_path, key, ContentType="video/mp4"
            )
        stored_path = reference
        async with AsyncSessionLocal() as db:
            saved = await ClipExportRepository.mark_succeeded(db, export_id, reference)
            await db.commit()
        if not saved:
            await _discard_unreferenced(storage, reference, config.temp_dir)
            return {"export_id": export_id, "status": "abandoned"}
        stored_path = None
        if storage.enabled:
            await run_in_thread(remove_local_export_dir, directory, config.temp_dir)
        logger.info("Clip export %s stored", export_id)
        return {"export_id": export_id, "status": "succeeded"}
    except asyncio.CancelledError:
        await _record_failure(export_id, STALE_MESSAGE)
        await _cleanup_partial(storage, stored_path, directory, config.temp_dir)
        raise
    except ExportCancelled:
        await _record_failure(export_id, STALE_MESSAGE)
        await _cleanup_partial(storage, stored_path, directory, config.temp_dir)
        return {"export_id": export_id, "status": "failed"}
    except Exception:
        logger.exception("Clip export %s failed", export_id)
        await _record_failure(export_id, FAILED_MESSAGE)
        await _cleanup_partial(storage, stored_path, directory, config.temp_dir)
        return {"export_id": export_id, "status": "failed"}


async def _pump_progress(export_id: str, progress: dict, stop: threading.Event) -> None:
    from ..database import AsyncSessionLocal

    last = -1
    while not stop.is_set():
        await asyncio.sleep(2)
        if stop.is_set():
            return
        try:
            async with AsyncSessionLocal() as db:
                current = await ClipExportRepository.get(db, export_id)
                if not current or current["status"] != "running":
                    stop.set()
                    return
                value = int(progress["value"])
                if value != last:
                    last = value
                    await ClipExportRepository.update_progress(db, export_id, value)
                else:
                    await ClipExportRepository.touch(db, export_id)
                await db.commit()
        except Exception:
            logger.warning("Clip export %s progress update failed", export_id, exc_info=True)


async def _discard_unreferenced(storage: MediaStorage, reference: str, temp_dir: str) -> None:
    try:
        if is_s3(reference):
            await run_in_thread(storage.delete, reference)
        else:
            await run_in_thread(remove_local_export_dir, Path(reference).parent, temp_dir)
    except STORAGE_ERRORS:
        logger.warning("Could not remove abandoned clip export %s", reference)


async def _cleanup_partial(
    storage: MediaStorage, stored_path: str | None, directory: Path, temp_dir: str
) -> None:
    if stored_path and is_s3(stored_path):
        try:
            await run_in_thread(storage.delete, stored_path)
        except STORAGE_ERRORS:
            logger.warning("Could not remove failed clip export upload %s", stored_path)
    await run_in_thread(remove_local_export_dir, directory, temp_dir)
