"""Trusted multipart sessions and admission limits shared across API workers."""

import json
import re
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..media.upload_limits import upload_too_large_detail


class UploadLimitReached(ValueError):
    pass


class UploadIdempotencyConflict(ValueError):
    """The same idempotency key was already used for different bytes."""


async def reserve_upload(db, user_id: str, size: int, config):
    if type(size) is not int or size <= 0:
        raise ValueError("Uploaded file is too large or empty")
    if size > config.max_video_upload_bytes:
        raise ValueError(upload_too_large_detail(config.max_video_upload_bytes))
    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 113463))"), {"owner": user_id})
    counts = (await db.execute(text("""
        SELECT COUNT(*) FILTER (WHERE state IN ('reserved', 'uploading')) AS active,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 day') AS daily
        FROM media_upload_sessions WHERE user_id = :owner
    """), {"owner": user_id})).one()
    if counts.active >= config.media_upload_concurrency or counts.daily >= config.media_upload_daily_limit:
        await db.rollback()
        raise UploadLimitReached("Upload limit reached. Finish existing uploads or try again later.")
    session_id = str(uuid4())
    await db.execute(text("INSERT INTO media_upload_sessions (id, user_id, size) VALUES (:id, :owner, :size)"),
        {"id": session_id, "owner": user_id, "size": size})
    await db.commit()
    return session_id


async def bind_upload(db, session_id: str, authorization):
    await db.execute(text("""
        UPDATE media_upload_sessions SET upload_id = :upload, video_path = :path, state = 'uploading'
        WHERE id = :id AND state = 'reserved'
    """), {"id": session_id, "upload": authorization["uploadId"], "path": authorization["video_path"]})
    await db.commit()


async def get_upload(db, user_id: str, payload, *, for_abort=False):
    if not isinstance(payload.get("uploadId"), str) or not isinstance(payload.get("video_path"), str):
        raise ValueError("An upload ID and video path are required")
    validity = "" if for_abort else " AND state IN ('uploading', 'completed') AND expires_at > clock_timestamp()"
    row = (await db.execute(text("""
        SELECT id, size FROM media_upload_sessions
        WHERE user_id = :owner AND upload_id = :upload AND video_path = :path
    """ + validity), {"owner": user_id, "upload": payload["uploadId"], "path": payload["video_path"]})).first()
    if row is None:
        raise ValueError("Upload expired or is unavailable; restart the upload")
    return row


async def finish_upload(db, session_id: str, state: str):
    await db.execute(text("UPDATE media_upload_sessions SET state = :state WHERE id = :id AND (:state != 'aborted' OR state != 'completed')"), {"id": session_id, "state": state})
    await db.commit()


def _admission_counts():
    return text("""
        SELECT COUNT(*) FILTER (WHERE state IN ('reserved', 'uploading')) AS active,
            COUNT(*) FILTER (WHERE created_at > NOW() - INTERVAL '1 day') AS daily
        FROM media_upload_sessions WHERE user_id = :owner
    """)


async def claim_stored_upload(db, user_id: str, size: int, digest: str, video_path: str, idempotency_key: str | None, config):
    """Record a finished local upload, or return the path already stored for these bytes.

    The advisory lock serializes one user's uploads. A retry that hashes to a
    completed session reuses that path and does not consume another daily slot.
    """
    if type(size) is not int or isinstance(size, bool) or size <= 0:
        raise ValueError("Uploaded file is too large or empty")
    if size > config.max_video_upload_bytes:
        raise ValueError(upload_too_large_detail(config.max_video_upload_bytes))
    if not re.fullmatch(r"[0-9a-f]{64}", digest or ""):
        raise ValueError("Invalid upload digest")
    if idempotency_key is not None and not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", idempotency_key):
        raise ValueError("Invalid idempotency key")
    if not video_path.startswith("upload://") or video_path.startswith("upload://s3/"):
        raise ValueError("Invalid upload path")

    await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 113463))"), {"owner": user_id})
    if idempotency_key:
        existing = (await db.execute(text("""
            SELECT video_path, content_sha256 FROM media_upload_sessions
            WHERE user_id = :owner AND idempotency_key = :key AND state = 'completed'
            ORDER BY created_at DESC LIMIT 1
        """), {"owner": user_id, "key": idempotency_key})).first()
        if existing is not None:
            await db.rollback()
            if existing.content_sha256 != digest:
                raise UploadIdempotencyConflict("This idempotency key was already used for a different file.")
            return existing.video_path, False
    existing = (await db.execute(text("""
        SELECT video_path FROM media_upload_sessions
        WHERE user_id = :owner AND content_sha256 = :digest AND state = 'completed'
        ORDER BY created_at DESC LIMIT 1
    """), {"owner": user_id, "digest": digest})).first()
    if existing is not None:
        await db.rollback()
        return existing.video_path, False

    counts = (await db.execute(_admission_counts(), {"owner": user_id})).one()
    if counts.active >= config.media_upload_concurrency or counts.daily >= config.media_upload_daily_limit:
        await db.rollback()
        raise UploadLimitReached("Upload limit reached. Finish existing uploads or try again later.")
    session_id = str(uuid4())
    try:
        await db.execute(text("""
            INSERT INTO media_upload_sessions
                (id, user_id, size, state, video_path, content_sha256, idempotency_key)
            VALUES
                (:id, :owner, :size, 'completed', :path, :digest, :key)
        """), {
            "id": session_id,
            "owner": user_id,
            "size": size,
            "path": video_path,
            "digest": digest,
            "key": idempotency_key,
        })
        await db.commit()
    except IntegrityError:
        await db.rollback()
        existing = (await db.execute(text("""
            SELECT video_path FROM media_upload_sessions
            WHERE user_id = :owner AND content_sha256 = :digest AND state = 'completed'
            ORDER BY created_at DESC LIMIT 1
        """), {"owner": user_id, "digest": digest})).first()
        if existing is None:
            raise
        return existing.video_path, False
    return video_path, True


async def get_stored_upload(db, video_path: str):
    if not isinstance(video_path, str) or not video_path.startswith("upload://"):
        return None
    return (await db.execute(text("""
        SELECT id, user_id, video_path, storage_reference FROM media_upload_sessions
        WHERE video_path = :path AND state = 'completed'
        ORDER BY created_at DESC LIMIT 1
    """), {"path": video_path})).first()


async def save_storage_reference(db, session_id: str, video_path: str, reference: str) -> None:
    """Remember the durable object and stamp tasks that already point at this upload."""
    await db.execute(text("""
        UPDATE media_upload_sessions SET storage_reference = :reference
        WHERE id = :id AND state = 'completed' AND storage_reference IS NULL
    """), {"id": session_id, "reference": reference})
    await db.execute(text("""
        UPDATE tasks SET source_metadata = source_metadata || CAST(:meta AS JSONB)
        WHERE source_id IN (SELECT id FROM sources WHERE url = :url)
    """), {"meta": json.dumps({"storage_reference": reference}), "url": video_path})
    await db.commit()
