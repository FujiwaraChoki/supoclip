"""Media deletion outbox, written in the same transaction as record changes."""

from pathlib import Path
from contextlib import asynccontextmanager
from functools import wraps
import inspect
import logging

from sqlalchemy import text

from ..editor_document import editor_dir
from ..media_storage import MediaStorage
from .session_lock_connection import session_lock_connection


@asynccontextmanager
async def source_guard(db, source_url: str):
    if not source_url.startswith("upload://"):
        yield
        return
    async with session_lock_connection(db) as connection:
        await connection.execute(text("SET LOCAL lock_timeout = '5s'"))
        await connection.execute(text("SELECT pg_advisory_lock(hashtextextended(:url, 113462))"), {"url": source_url})
        await connection.commit()
        yield


def guard_source_creation(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        arguments = inspect.signature(method).bind(self, *args, **kwargs).arguments
        url = arguments["url"]
        # Legacy local uploads keep their existing admission behavior.
        if not url.startswith("upload://s3/"):
            return await method(self, *args, **kwargs)
        async with source_guard(self.db, url):
            return await method(self, *args, **kwargs)
    return wrapped


async def queue_delete(db, file_path: str, *, prefix=False):
    if not file_path.startswith("s3://"):
        path = Path(file_path).resolve()
        root = Path(MediaStorage().config.temp_dir).resolve()
        if path == root or not path.is_relative_to(root):
            logging.getLogger(__name__).warning("Skipped media cleanup outside TEMP_DIR")
            return
    await db.execute(text("""
        INSERT INTO media_deletions (file_path, is_prefix)
        VALUES (:path, :prefix) ON CONFLICT (file_path) DO NOTHING
    """), {"path": file_path, "prefix": prefix})


async def queue_clip_delete(db, clip):
    await queue_delete(db, clip["file_path"])
    storage = MediaStorage()
    directory = editor_dir(storage.config.temp_dir, clip["task_id"], clip["id"], clip["filename"])
    await queue_delete(db, str(directory), prefix=True)
    if storage.enabled:
        await queue_delete(db, storage.editor_reference(directory), prefix=True)


async def persist_clip(db, file_path: str, task_id: str):
    from ..utils.async_helpers import run_in_thread

    storage = MediaStorage()
    reference = await run_in_thread(storage.persist_clip, file_path, task_id)
    if reference != file_path:
        # The local output is removed only after the new database reference
        # commits. A rollback leaves the previous media intact.
        await queue_delete(db, file_path)
    return reference
