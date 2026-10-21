"""Retry committed deletions and reclaim media left by interrupted jobs."""

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
    return {"editors": editors, "videos": videos, "clips": clips, "sources": sources}


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
        return reference in index["clips"] or url in index["sources"]
    result = await db.execute(text("""
        SELECT EXISTS(SELECT 1 FROM generated_clips WHERE file_path = :path)
            OR EXISTS(SELECT 1 FROM sources s JOIN tasks t ON t.source_id = s.id WHERE s.url = :url)
            OR EXISTS(SELECT 1 FROM social_posts WHERE status = 'publishing' AND source_file_path = :path)
    """), {"path": reference, "url": url})
    return bool(result.scalar())


async def drain_deletions(db, storage: MediaStorage, *, limit=100):
    for _ in range(limit):
        result = await db.execute(text("""
            SELECT file_path, is_prefix FROM media_deletions
            WHERE hold_token IS NULL AND next_attempt_at <= clock_timestamp()
            ORDER BY next_attempt_at, created_at FOR UPDATE SKIP LOCKED LIMIT 1
        """))
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
    """Group files by video/directory, avoiding separate deletion of sidecars."""
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
            # Reclaim legacy temporary files after upgrades; these are not provider options.
            elif path.is_dir() and path.name.startswith(("apify-", "rapidapi-", "oxy-", "videoscale-", "ytdl-")):
                if max((modified(p) for p in entries(path)), default=0) < cutoff:
                    yield str(path), True
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
    editor = root / "editor"
    if editor.exists():
        for directory in entries(editor):
            if directory.is_dir() and max((modified(p) for p in entries(directory)), default=0) < cutoff:
                yield str(directory), True


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
    await run_in_thread(storage.sweep_cache)
    await run_in_thread(storage.sync_source_metadata)
    await run_in_thread(storage.sync_editor_drafts)
    await run_in_thread(storage.sweep_editor_cache)


async def cleanup_upload_sessions(db, storage):
    if not storage.bucket:
        return
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
    await db.execute(text("DELETE FROM media_upload_sessions WHERE state IN ('completed', 'aborted') AND created_at < NOW() - INTERVAL '2 days'"))
    await db.commit()


async def sweep_orphaned_media(ctx):
    storage = MediaStorage()
    candidates = await run_in_thread(lambda: list(orphan_candidates(storage)))
    async with AsyncSessionLocal() as db:
        index = await reference_index(db, storage)
        for number, (reference, prefix) in enumerate(candidates, start=1):
            if not await referenced(db, reference, storage, prefix=prefix, index=index):
                await queue_delete(db, reference, prefix=prefix)
            if number % 100 == 0:
                await db.commit()
        await db.commit()
        await drain_deletions(db, storage)
