#!/usr/bin/env python3
"""Migrate referenced local media, retaining existing IDs and clip URLs.

Defaults to a report only. Drain workers and back up PostgreSQL before --apply.
Local deletion is held until the printed cleanup token is explicitly released.
"""

import argparse
import asyncio
import json
from pathlib import Path
import sys
import uuid
import logging

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from src.database import AsyncSessionLocal
from src.editor_document import editor_dir
from src.media_storage import MediaStorage
from src.repositories.clip_repository import ClipRepository
from src.repositories.edit_transaction import task_edit_transaction
from src.repositories.media_repository import queue_delete
from src.services.video_service import VideoService
from src.services.task_service import TaskService
from src.utils.async_helpers import run_in_thread


async def migrate(apply=False, task_id=None, release_cleanup=None):
    storage = MediaStorage()
    if not storage.enabled:
        raise ValueError("Set MEDIA_STORAGE=s3 and the media bucket configuration first")
    counts = {"clips": 0, "sources": 0, "skipped": 0, "apply": apply,
        "cleanup_token": str(uuid.uuid4()) if apply else None}
    async with AsyncSessionLocal() as db:
        if release_cleanup:
            token = str(uuid.UUID(release_cleanup))
            result = await db.execute(text("UPDATE media_deletions SET hold_token = NULL, next_attempt_at = NOW() WHERE hold_token = :token"), {"token": token})
            await db.commit()
            counts["released"] = result.rowcount
            print(json.dumps(counts))
            return counts
        if apply:
            db.info["media_deletion_hold"] = counts["cleanup_token"]
            # A later failure must not hide the token for already committed work.
            print(json.dumps({"cleanup_token": counts["cleanup_token"]}), flush=True)
        clause = " AND task_id = :target" if task_id else ""
        result = await db.execute(text("SELECT DISTINCT task_id FROM generated_clips WHERE file_path NOT LIKE 's3://%'" + clause),
            {"target": task_id} if task_id else {})
        task_ids = list(result.scalars())
        for current_task_id in task_ids:
            status = (await db.execute(text("SELECT status FROM tasks WHERE id = :id"), {"id": current_task_id})).scalar()
            if status in {"queued", "processing"}:
                counts["skipped"] += 1
                continue
            async with task_edit_transaction(db, current_task_id):
                clips = await ClipRepository.get_clips_by_task(db, current_task_id)
                for clip in clips:
                    if clip["file_path"].startswith("s3://"):
                        continue
                    path = Path(clip["file_path"])
                    if not path.exists():
                        counts["skipped"] += 1
                        continue
                    if apply:
                        directory = editor_dir(storage.config.temp_dir, current_task_id, clip["id"], clip["filename"])
                        if directory.exists():
                            await run_in_thread(storage.save_editor, directory)
                        await ClipRepository.update_clip(db, clip["id"], clip["filename"], str(path),
                            clip["start_time"], clip["end_time"], clip["duration"], clip["text"])
                    counts["clips"] += 1
        clause = " AND s.id = (SELECT source_id FROM tasks WHERE id = :target)" if task_id else ""
        result = await db.execute(text("""
            SELECT s.id, s.url, array_agg(DISTINCT t.user_id) AS owners
            FROM sources s JOIN tasks t ON t.source_id = s.id
            WHERE s.url LIKE 'upload://%' AND s.url NOT LIKE 'upload://s3/%'
        """ + clause + """
            GROUP BY s.id, s.url
        """), {"target": task_id} if task_id else {})
        sources = list(result)
        await db.rollback()
        for source in sources:
            # Historical uploads shared across owners need an explicit ownership
            # decision. Keep them local rather than silently assigning ownership.
            if len(source.owners) != 1:
                counts["skipped"] += 1
                continue
            reference = None
            try:
                path = VideoService.resolve_local_video_path(source.url)
                if not path.exists() or not 0 < path.stat().st_size <= storage.config.max_video_upload_bytes:
                    counts["skipped"] += 1
                    continue
                await db.execute(text("SELECT id FROM sources WHERE id = :id FOR UPDATE"), {"id": source.id})
                rows = await db.execute(text("SELECT id, status FROM tasks WHERE source_id = :id FOR UPDATE"), {"id": source.id})
                source_tasks = list(rows.mappings())
                if any(task["status"] in {"queued", "processing"} for task in source_tasks):
                    counts["skipped"] += 1
                    await db.rollback()
                    continue
                if apply:
                    # Promote legacy Redis-only framing/cleanup options before
                    # writing a nonempty PostgreSQL source-metadata document.
                    settings = {task["id"]: await TaskService(db)._load_task_source_settings(task["id"])
                        for task in source_tasks}
                    url, reference = await run_in_thread(storage.persist_source, path, source.owners[0])
                    await run_in_thread(storage.validate_source, url, source.owners[0])
                    await db.execute(text("UPDATE sources SET url = :url, updated_at = NOW() WHERE id = :id"), {"url": url, "id": source.id})
                    for task in source_tasks:
                        await db.execute(text("""
                            UPDATE tasks SET source_metadata = CAST(:settings AS JSONB) || source_metadata
                                || jsonb_build_object('url', CAST(:url AS TEXT))
                            WHERE id = :id
                        """), {"url": url, "id": task["id"], "settings": json.dumps(settings[task["id"]])})
                    await queue_delete(db, str(path))
                    await db.commit()
                else:
                    await db.rollback()
                counts["sources"] += 1
            except BaseException as exc:
                await db.rollback()
                if reference:
                    try:
                        await run_in_thread(storage.delete, reference)
                    except Exception:
                        logging.getLogger(__name__).warning("Uncommitted migration upload will be reclaimed by orphan cleanup")
                if isinstance(exc, ValueError):
                    counts["skipped"] += 1
                    continue
                raise
    print(json.dumps(counts))
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--task-id", help="Migrate one task and its uploaded source")
    parser.add_argument("--release-cleanup", help="Release a printed migration cleanup token after verification")
    args = parser.parse_args()
    asyncio.run(migrate(args.apply, args.task_id, args.release_cleanup))
