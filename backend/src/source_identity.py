"""Identity of a submitted video, shared by duplicate detection.

A YouTube link, a Twitch or Kick VOD, and an upload of the same bytes are one
source even when the URL spelling differs. The key is not a secret and never
includes an account email.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from .repositories.session_lock_connection import session_lock_connection
from .stream_source import detect_stream_source
from .youtube_utils import get_youtube_video_id

DUPLICATE_SOURCE_CODE = "DUPLICATE_SOURCE"
DUPLICATE_SOURCE_MESSAGE = "This video is already being processed."
ACTIVE_TASK_STATUSES = ("queued", "processing")


def source_key_from_url(url: str) -> str | None:
    """Stable key for a link, or None for an upload or unsupported URL."""
    if not isinstance(url, str):
        return None
    text_url = url.strip()
    if not text_url or text_url.lower().startswith("upload://"):
        return None
    video_id = get_youtube_video_id(text_url)
    if video_id:
        return f"youtube:{video_id}"
    try:
        source, kind, platform_id = detect_stream_source(text_url)
    except ValueError:
        return None
    return f"{source}:{kind}:{platform_id}"


async def upload_source_key(db, user_id: str, url: str) -> str:
    """Hash when the upload session has one, otherwise the upload id or path."""
    if db is None or not isinstance(url, str) or not url.startswith("upload://"):
        return f"upload:url:{url}"
    try:
        row = (
            await db.execute(
                text(
                    """
                    SELECT id, content_sha256
                    FROM media_upload_sessions
                    WHERE user_id = :user AND video_path = :path AND state = 'completed'
                    ORDER BY created_at DESC
                    LIMIT 1
                    """
                ),
                {"user": user_id, "path": url},
            )
        ).first()
    except ProgrammingError:
        await db.rollback()
        return f"upload:url:{url}"
    if row is not None and row.content_sha256:
        return f"upload:sha256:{row.content_sha256}"
    if row is not None and row.id:
        return f"upload:id:{row.id}"
    return f"upload:url:{url}"


async def source_key_for_url(db, user_id: str, url: str) -> str | None:
    if isinstance(url, str) and url.startswith("upload://"):
        return await upload_source_key(db, user_id, url)
    return source_key_from_url(url)


def duplicate_source_response(task_id: str, job_id: str | None) -> dict:
    """Success body whose task id is the generation already in progress.

    Web and iOS clients navigate to ``task_id`` on a 200. ``duplicate`` and
    ``code`` tell them this is that generation, not a new one.
    """
    return {
        "task_id": task_id,
        "job_id": job_id or task_id,
        "duplicate": True,
        "code": DUPLICATE_SOURCE_CODE,
        "message": DUPLICATE_SOURCE_MESSAGE,
    }


@asynccontextmanager
async def source_admission_lock(db, user_id: str, source_key: str | None):
    """Hold one account's submission of one source across the insert commit."""
    if not source_key:
        yield
        return
    async with session_lock_connection(db) as connection:
        await connection.execute(
            text("SELECT pg_advisory_lock(hashtextextended(:key, 113464))"),
            {"key": f"{user_id}\n{source_key}"},
        )
        await connection.commit()
        yield
