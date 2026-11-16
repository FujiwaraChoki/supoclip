"""Retry committed deletions and reclaim media left by interrupted jobs.

Object storage and the database are shared, so one scheduler handles them.
Local files are not: every machine keeps its own ``TEMP_DIR``. Each process
only deletes local paths queued for the disk it mounts, and every video
worker runs ``maintain_local_scratch`` for its own machine.
"""

from contextlib import nullcontext
import logging
from pathlib import Path, PurePosixPath
import time
import re

from sqlalchemy import text

from ..database import AsyncSessionLocal
from ..media_storage import MediaStorage, VIDEO_SUFFIXES, STORAGE_ERRORS, is_s3
from botocore.exceptions import ClientError
from ..editor_document import editor_dir
from ..local_volume import volume_id
from ..repositories.media_repository import source_guard, queue_delete
from ..youtube_utils import get_youtube_video_id
from ..utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)


def source_url(reference: str, storage: MediaStorage) -> str | None:
    if is_s3(reference):
        key = storage.parse(reference)
        prefix = storage.key("sources") + "/"
        return "upload://s3/" + key.removeprefix(prefix) if key.startswith(prefix) else None
    path = Path(reference)
    if path.parent.resolve() == (Path(storage.config.temp_dir) / "uploads").resolve():
        return f"upload://{path.name}"
    return None


async def reference_index(db, storage):
    rows = await db.execute(text("SELECT id, task_id, filename, file_path FROM generated_clips"))
    rows = list(rows)
    editors = {editor_dir(storage.config.temp_dir, row.task_id, row.id, row.filename).name for row in rows}
    clips = {row.file_path for row in rows}
    # A post being published may still upload a render the clip already replaced.
    rows = await db.execute(text(
        "SELECT source_file_path FROM social_posts WHERE status = 'publishing' AND source_file_path IS NOT NULL"
    ))
    clips |= {row.source_file_path for row in rows}
    rows = await db.execute(text("SELECT DISTINCT s.url, s.type FROM sources s JOIN tasks t ON t.source_id = s.id"))
    rows = list(rows)
    sources = {row.url for row in rows}
    videos = {get_youtube_video_id(row.url) for row in rows if row.type == "youtube" and row.url}
    # A stored upload can be turned into a task during the session lifetime.
    # Its local file and object copy stay until that window closes or a task
    # records the same reference.
    rows = await db.execute(text("""
        SELECT video_path FROM media_upload_sessions
        WHERE state = 'completed' AND video_path IS NOT NULL
    """))
    sources |= {row.video_path for row in rows}
    rows = await db.execute(text("""
        SELECT storage_reference FROM media_upload_sessions
        WHERE state = 'completed' AND storage_reference IS NOT NULL
        UNION
        SELECT source_metadata->>'storage_reference' FROM tasks
        WHERE source_metadata->>'storage_reference' IS NOT NULL
    """))
    storage_refs = _with_capped_companions(storage, {row.storage_reference for row in rows})
    return {
        "editors": editors,
        "videos": videos,
        "clips": clips,
        "sources": sources,
        "storage": storage_refs,
    }


def _with_capped_companions(storage: MediaStorage, references: set[str]) -> set[str]:
    """Keep a 1080p master that sits beside a still-referenced original."""
    expanded = set(references)
    for reference in references:
        if not is_s3(reference):
            continue
        try:
            capped = storage.capped_source_key(storage.parse(reference))
        except ValueError:
            continue
        if capped:
            expanded.add(storage.reference(capped))
    return expanded


async def referenced(db, reference: str, storage: MediaStorage, *, prefix=False, index=None) -> bool:
    if prefix:
        if is_s3(reference):
            key = storage.parse(reference)
            if key.startswith(storage.key("clips") + "/"):
                return bool((await db.execute(text(
                    "SELECT EXISTS(SELECT 1 FROM generated_clips WHERE starts_with(file_path, :prefix))"
                ), {"prefix": reference.rstrip("/") + "/"})).scalar())
            if key.startswith(storage.key("sources") + "/"):
                url_prefix = "upload://s3/" + key.removeprefix(storage.key("sources") + "/").rstrip("/") + "/"
                return bool((await db.execute(text(
                    "SELECT EXISTS(SELECT 1 FROM sources s JOIN tasks t ON t.source_id = s.id WHERE starts_with(s.url, :prefix))"
                ), {"prefix": url_prefix})).scalar())
        name = PurePosixPath(reference).name
        live = index if index is not None else await reference_index(db, storage)
        return name in live["editors"]
    if is_s3(reference) and storage.parse(reference).startswith(storage.key("transcripts") + "/"):
        video_id = PurePosixPath(reference).name.removesuffix(".transcript_cache.json")
        live = index if index is not None else await reference_index(db, storage)
        return video_id in live["videos"]
    url = source_url(reference, storage)
    if index is not None:
        return (
            reference in index["clips"]
            or url in index["sources"]
            or reference in index.get("storage", ())
        )
    candidates = _companion_originals(storage, reference)
    result = await db.execute(text("""
        SELECT EXISTS(SELECT 1 FROM generated_clips WHERE file_path = :path)
            OR EXISTS(SELECT 1 FROM sources s JOIN tasks t ON t.source_id = s.id WHERE s.url = :url)
            OR EXISTS(SELECT 1 FROM social_posts WHERE status = 'publishing' AND source_file_path = :path)
            OR EXISTS(
                SELECT 1 FROM media_upload_sessions
                WHERE state = 'completed' AND (video_path = :url OR storage_reference = ANY(CAST(:candidates AS text[])))
            )
            OR EXISTS(
                SELECT 1 FROM tasks
                WHERE source_metadata->>'storage_reference' = ANY(CAST(:candidates AS text[]))
            )
    """), {"path": reference, "url": url, "candidates": candidates})
    return bool(result.scalar())


def _companion_originals(storage: MediaStorage, reference: str) -> list[str]:
    """The object itself, plus the original when this is its capped master."""
    found = [reference]
    if not is_s3(reference):
        return found
    try:
        key = storage.parse(reference)
    except ValueError:
        return found
    name = PurePosixPath(key).name
    marker = ".capped.mp4"
    if not name.endswith(marker):
        return found
    stem = name[: -len(marker)]
    parent = PurePosixPath(key).parent
    for suffix in VIDEO_SUFFIXES:
        found.append(storage.reference(str(parent / f"{stem}{suffix}")))
    return found


async def drain_deletions(db, storage: MediaStorage, *, limit=100, shared=True):
    """Carry out due deletions this process is able to perform.

    A local path can only be removed from the disk it was queued on. ``shared``
    adds object storage references and rows from before disks were recorded;
    one scheduler handles those. A video worker passes False and only cleans
    its own disk.
    """
    volume = volume_id(storage.config.temp_dir)
    for _ in range(limit):
        result = await db.execute(text("""
            SELECT file_path, is_prefix FROM media_deletions
            WHERE hold_token IS NULL AND next_attempt_at <= clock_timestamp()
              AND (local_volume = :volume OR (:shared AND local_volume IS NULL))
            ORDER BY next_attempt_at, created_at FOR UPDATE SKIP LOCKED LIMIT 1
        """), {"volume": volume, "shared": shared})
        item = result.first()
        if item is None:
            await db.rollback()
            break
        reference, prefix = item.file_path, item.is_prefix
        try:
            url = source_url(reference, storage)
            guard = source_guard(db, url) if url else nullcontext()
            async with guard:
                if not await referenced(db, reference, storage, prefix=prefix):
                    await run_in_thread(storage.delete, reference, prefix=prefix)
                await db.execute(text("DELETE FROM media_deletions WHERE file_path = :path"), {"path": reference})
                await db.commit()
        except Exception:
            await db.rollback()
            logger.exception("Media deletion failed; retained for retry")
            await db.execute(text("""
                UPDATE media_deletions SET attempts = attempts + 1,
                    next_attempt_at = clock_timestamp() + make_interval(secs => LEAST(21600, 60 * power(2, LEAST(attempts, 9)))::integer)
                WHERE file_path = :path
            """), {"path": reference})
            await db.commit()


def orphan_candidates(storage: MediaStorage):
    yield from local_orphan_candidates(storage)
    yield from stored_orphan_candidates(storage)


def local_orphan_candidates(storage: MediaStorage):
    """Old files on this machine's disk. Sidecars follow their video."""
    cutoff = time.time() - storage.config.media_orphan_grace_seconds
    root = Path(storage.config.temp_dir)
    for folder in ("uploads", "clips", "exports"):
        directory = root / folder
        if directory.exists():
            for path in entries(directory):
                if path.is_file() and path.suffix.lower() in VIDEO_SUFFIXES and modified(path) < cutoff:
                    yield str(path), False
    if root.exists():
        for path in entries(root):
            if modified(path) >= cutoff:
                continue
            if path.is_file() and re.fullmatch(r"[A-Za-z0-9_-]{11}\.(?:mp4|webm|mkv|m4a|transcription\.mp3|assemblyai\.mp3|part)", path.name):
                yield str(path), False
            # Reclaim interrupted staging directories, including legacy providers.
            elif path.is_dir() and path.name.startswith(("proxy-", "apify-", "rapidapi-", "oxy-", "videoscale-", "ytdl-")):
                if max((modified(p) for p in entries(path)), default=0) < cutoff:
                    yield str(path), True
    editor = root / "editor"
    if editor.exists():
        for directory in entries(editor):
            if directory.is_dir() and max((modified(p) for p in entries(directory)), default=0) < cutoff:
                yield str(directory), True


def stored_orphan_candidates(storage: MediaStorage):
    """Old objects in the bucket, grouped by upload or render directory."""
    cutoff = time.time() - storage.config.media_orphan_grace_seconds
    if storage.enabled:
        for page in storage.client.get_paginator("list_objects_v2").paginate(Bucket=storage.bucket, Prefix=storage.key("transcripts") + "/"):
            for obj in page.get("Contents", []):
                if obj["LastModified"].timestamp() < cutoff:
                    yield storage.reference(obj["Key"]), False
        for kind in ("sources", "clips"):
            groups = {}
            for page in storage.client.get_paginator("list_objects_v2").paginate(Bucket=storage.bucket, Prefix=storage.key(kind) + "/"):
                for obj in page.get("Contents", []):
                    parent = str(PurePosixPath(obj["Key"]).parent)
                    group = groups.setdefault(parent, {"latest": 0, "has_video": False})
                    group["latest"] = max(group["latest"], obj["LastModified"].timestamp())
                    group["has_video"] |= PurePosixPath(obj["Key"]).suffix.lower() in VIDEO_SUFFIXES
                    if PurePosixPath(obj["Key"]).suffix.lower() in VIDEO_SUFFIXES and obj["LastModified"].timestamp() < cutoff:
                        yield storage.reference(obj["Key"]), False
            # A process may stop after uploading metadata but before its video.
            # Reclaim those otherwise invisible partial object groups too.
            for parent, group in groups.items():
                # Only an individual upload/render directory is eligible.
                # Unexpected flat keys must never cause a namespace deletion.
                relative_parts = PurePosixPath(parent).relative_to(PurePosixPath(storage.key(kind))).parts
                if len(relative_parts) == 2 and not group["has_video"] and group["latest"] < cutoff:
                    yield storage.reference(parent), True
        groups = {}
        prefix = storage.key("editor") + "/"
        for page in storage.client.get_paginator("list_objects_v2").paginate(Bucket=storage.bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                directory = obj["Key"].removeprefix(prefix).split("/")[0]
                groups[directory] = max(groups.get(directory, 0), obj["LastModified"].timestamp())
        for directory, last_write in groups.items():
            if last_write < cutoff:
                yield storage.reference(prefix + directory), True


def entries(directory):
    try:
        return list(directory.iterdir())
    except FileNotFoundError:
        return []


def modified(path):
    try:
        return path.stat().st_mtime
    except FileNotFoundError:
        return time.time()


async def cleanup_media(ctx):
    storage = MediaStorage()
    async with AsyncSessionLocal() as db:
        await drain_deletions(db, storage)
        await cleanup_upload_sessions(db, storage)
        from ..services.clip_export_service import expire_clip_exports

        await expire_clip_exports(db, storage)
        await drain_deletions(db, storage)
        await forget_lost_volumes(db, storage)
        await delete_orphaned_editor_sessions(db, storage)
    from ..font_registry import publish_local_user_fonts

    # Fonts uploaded before they were kept in object storage are only on the
    # API's disk. Store them so workers on other machines can render with them.
    await run_in_thread(publish_local_user_fonts)
    await sweep_local_caches(storage)


async def copy_pending_uploads(ctx):
    """Finish copying stored uploads to object storage.

    The API starts each copy after its upload response. This catches the ones
    an API restart or a storage error left behind, so a task waiting on
    another machine's worker is not stuck on a file only this disk has.
    """
    from ..media.upload_ingest import persist_pending_uploads

    return {"copied": await persist_pending_uploads()}


async def sweep_local_caches(storage: MediaStorage) -> None:
    await run_in_thread(storage.sweep_cache)
    await run_in_thread(storage.sync_source_metadata)
    await run_in_thread(storage.sweep_editor_cache)


async def delete_orphaned_editor_sessions(db, storage: MediaStorage) -> int:
    """Remove editing sessions whose clip file no longer exists.

    A session is kept by the same rule as its media: some clip still has this
    task, id and filename. The grace period covers a combined clip, whose
    session is written just before its clip row.
    """
    result = await db.execute(text("""
        DELETE FROM editor_sessions s
        WHERE s.updated_at < NOW() - make_interval(secs => :grace)
          AND NOT EXISTS (
              SELECT 1 FROM generated_clips c
              WHERE c.id = s.clip_id AND c.task_id = s.task_id AND c.filename = s.filename
          )
    """), {"grace": storage.config.media_orphan_grace_seconds})
    await db.commit()
    return result.rowcount or 0


async def forget_lost_volumes(db, storage: MediaStorage) -> None:
    """Drop deletions queued for a disk that has not collected them in a month.

    That machine is gone or was rebuilt. If it returns, its own orphan sweep
    finds whatever is still on its disk.
    """
    await db.execute(text("""
        DELETE FROM media_deletions
        WHERE local_volume IS NOT NULL AND local_volume <> :volume
          AND created_at < NOW() - INTERVAL '30 days'
    """), {"volume": volume_id(storage.config.temp_dir)})
    await db.commit()


async def maintain_local_scratch(*, orphans: bool = False) -> None:
    """Housekeeping for the disk of the machine this worker runs on.

    The scheduler's jobs run on one machine and only see that machine's
    files. A worker elsewhere has to collect its own queued deletions, expired
    caches and abandoned renders. On the scheduler's own machine this repeats
    work the scheduler already does, which is harmless.
    """
    storage = MediaStorage()
    async with AsyncSessionLocal() as db:
        await drain_deletions(db, storage, shared=False)
        if orphans:
            candidates = await run_in_thread(lambda: list(local_orphan_candidates(storage)))
            await queue_orphans(db, storage, candidates)
            await drain_deletions(db, storage, shared=False)
        from ..repositories.clip_export_repository import ClipExportRepository
        from ..services.clip_export_service import sweep_local_export_dirs

        live_ids = await ClipExportRepository.live_ids(db)
    await run_in_thread(sweep_local_export_dirs, storage, live_ids)
    await sweep_local_caches(storage)


def _finished_local_upload(storage, video_path: str) -> Path | None:
    """Uploads-directory file for a local ``upload://`` path, using this storage's temp dir."""
    if not isinstance(video_path, str) or not video_path.startswith("upload://") or video_path.startswith("upload://s3/"):
        return None
    name = video_path.removeprefix("upload://")
    if not name or name != Path(name).name or name in {".", ".."}:
        return None
    return Path(storage.config.temp_dir) / "uploads" / name


def _delete_finished_local_upload(storage, video_path: str) -> None:
    path = _finished_local_upload(storage, video_path)
    if path is None:
        return
    storage.delete(str(path))
    storage.delete(str(path.with_name(f"{path.stem}.capped.mp4")))


async def _upload_still_unreferenced(db, video_path: str | None) -> bool:
    if not video_path:
        return True
    referenced = await db.execute(text("""
        SELECT EXISTS (
            SELECT 1 FROM sources s
            JOIN tasks t ON t.source_id = s.id
            WHERE s.url = :path
        )
    """), {"path": video_path})
    return not bool(referenced.scalar())


async def _expire_one_upload_session(db, storage, row) -> None:
    """Delete one finished session unless a task source appears while we wait.

    Task creation holds ``source_guard`` across the source insert and its
    commit. Taking that same lock here, then checking again, means expiry
    cannot remove a file whose task source has already committed, and cannot
    pass a task create that is still inside the guard.
    """
    video_path = row.video_path or ""
    guard = source_guard(db, video_path) if video_path.startswith("upload://") else nullcontext()
    async with guard:
        if not await _upload_still_unreferenced(db, video_path):
            return
        try:
            await run_in_thread(_delete_finished_local_upload, storage, video_path)
        except (OSError, ValueError):
            return
        await db.execute(text("DELETE FROM media_upload_sessions WHERE id = :id"), {"id": row.id})
        await db.commit()


async def _expire_finished_upload_sessions(db, storage) -> None:
    """Drop completed and aborted sessions after two days, and their local files.

    A session whose ``video_path`` is still a task source stays. That row is the
    ownership record, and the file is still in use. Local storage has no bucket,
    so this expiry runs even when the multipart-abort loop does not.
    """
    rows = (await db.execute(text("""
        SELECT id, video_path FROM media_upload_sessions
        WHERE state IN ('completed', 'aborted')
          AND created_at < NOW() - INTERVAL '2 days'
          AND NOT EXISTS (
              SELECT 1 FROM sources s
              JOIN tasks t ON t.source_id = s.id
              WHERE s.url = media_upload_sessions.video_path
          )
    """))).all()
    # Release this read before waiting on a task create. The locked re-check
    # below is what decides whether the file is still free.
    await db.rollback()
    for row in rows:
        await _expire_one_upload_session(db, storage, row)
    await db.rollback()


async def cleanup_upload_sessions(db, storage):
    if storage.bucket:
        await _abort_expired_multipart_uploads(db, storage)
    await _expire_finished_upload_sessions(db, storage)


async def _abort_expired_multipart_uploads(db, storage):
    for _ in range(100):
        row = (await db.execute(text("""
            SELECT id, user_id, upload_id, video_path FROM media_upload_sessions
            WHERE expires_at < clock_timestamp() AND state IN ('reserved', 'uploading')
            ORDER BY expires_at FOR UPDATE SKIP LOCKED LIMIT 1
        """))).first()
        if row is None:
            await db.rollback()
            break
        try:
            if row.upload_id:
                try:
                    await run_in_thread(storage.abort_upload, row.user_id,
                        {"uploadId": row.upload_id, "video_path": row.video_path})
                except ClientError as exc:
                    if exc.response["Error"]["Code"] != "NoSuchUpload":
                        raise
            await db.execute(text("UPDATE media_upload_sessions SET state = 'aborted' WHERE id = :id"), {"id": row.id})
            await db.commit()
        except STORAGE_ERRORS:
            await db.rollback()
            logger.warning("Expired multipart upload retained for abort retry")
            break


async def queue_orphans(db, storage: MediaStorage, candidates) -> None:
    index = await reference_index(db, storage)
    for number, (reference, prefix) in enumerate(candidates, start=1):
        if not await referenced(db, reference, storage, prefix=prefix, index=index):
            await queue_delete(db, reference, prefix=prefix)
        if number % 100 == 0:
            await db.commit()
    await db.commit()


async def sweep_orphaned_media(ctx):
    storage = MediaStorage()
    candidates = await run_in_thread(lambda: list(orphan_candidates(storage)))
    async with AsyncSessionLocal() as db:
        await queue_orphans(db, storage, candidates)
        await drain_deletions(db, storage)
