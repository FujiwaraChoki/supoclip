"""Trusted multipart sessions and admission limits shared across API workers."""

import json
import os
import re
import shutil
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..media.upload_limits import upload_too_large_detail
from ..utils.async_helpers import run_in_thread


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


_COMPLETED_KEY_LOOKUP = text("""
    SELECT s.video_path, s.content_sha256, s.storage_reference
    FROM media_upload_sessions s
    WHERE s.user_id = :owner AND s.state = 'completed'
      AND (
        s.idempotency_key = :key
        OR EXISTS (
            SELECT 1 FROM media_upload_idempotency_keys k
            WHERE k.user_id = s.user_id
              AND k.idempotency_key = :key
              AND k.session_id = s.id
        )
      )
    ORDER BY s.created_at DESC
    LIMIT 1
""")


async def _remember_idempotency_key(db, user_id: str, session_id: str, idempotency_key: str | None) -> None:
    """Bind a key without replacing keys already recorded for this upload."""
    if not idempotency_key:
        return
    await db.execute(text("""
        INSERT INTO media_upload_idempotency_keys (user_id, idempotency_key, session_id)
        VALUES (:owner, :key, :session)
        ON CONFLICT (user_id, idempotency_key) DO NOTHING
    """), {"owner": user_id, "key": idempotency_key, "session": session_id})
    bound = (await db.execute(text("""
        SELECT session_id FROM media_upload_idempotency_keys
        WHERE user_id = :owner AND idempotency_key = :key
    """), {"owner": user_id, "key": idempotency_key})).scalar_one()
    if bound != session_id:
        raise UploadIdempotencyConflict(
            "This idempotency key was already used for a different file."
        )
    # The session column holds the first key. Later keys stay in the mapping table.
    await db.execute(text("""
        UPDATE media_upload_sessions
        SET idempotency_key = :key
        WHERE id = :id AND state = 'completed' AND idempotency_key IS NULL
    """), {"id": session_id, "key": idempotency_key})


def _place_missing_upload(existing_path: str, new_path: str) -> None:
    """Hardlink the just-published bytes onto a stored path whose file is gone."""
    from ..media.upload_ingest import local_upload_path

    try:
        target = local_upload_path(existing_path)
        source = local_upload_path(new_path)
    except ValueError:
        return
    if target.is_file() or not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except FileExistsError:
        return
    except OSError:
        temporary = target.with_name(f".{target.name}.restore")
        try:
            with source.open("rb") as src, temporary.open("wb") as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
            with temporary.open("rb+") as handle:
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    directory = os.open(target.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


async def _restore_missing_upload(existing_path: str, storage_reference: str | None, new_path: str) -> None:
    """Write a retry's bytes back when the stored file is gone and nothing durable can replace it."""
    if storage_reference or not existing_path or existing_path == new_path:
        return
    await run_in_thread(_place_missing_upload, existing_path, new_path)


async def _reuse_completed_upload(db, user_id: str, row, idempotency_key: str | None, new_path: str):
    """Return a completed upload and keep every key bound to that session."""
    try:
        await _restore_missing_upload(row.video_path, row.storage_reference, new_path)
    except Exception:
        await db.rollback()
        raise
    if not idempotency_key:
        await db.rollback()
        return row.video_path, False
    try:
        await _remember_idempotency_key(db, user_id, row.id, idempotency_key)
        await db.commit()
    except UploadIdempotencyConflict:
        await db.rollback()
        raise
    except IntegrityError:
        await db.rollback()
        raise UploadIdempotencyConflict(
            "This idempotency key was already used for a different file."
        )
    return row.video_path, False


async def claim_stored_upload(db, user_id: str, size: int, digest: str, video_path: str, idempotency_key: str | None, config):
    """Record a finished local upload, or return the path already stored for these bytes.

    The advisory lock serializes one user's uploads. A retry that hashes to a
    completed session reuses that path and does not consume another daily slot.
    When that file is gone and the session has no durable object, the retry's
    bytes are written back to the stored path before it is returned.
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
        existing = (await db.execute(
            _COMPLETED_KEY_LOOKUP, {"owner": user_id, "key": idempotency_key}
        )).first()
        if existing is not None:
            if existing.content_sha256 != digest:
                await db.rollback()
                raise UploadIdempotencyConflict("This idempotency key was already used for a different file.")
            try:
                await _restore_missing_upload(existing.video_path, existing.storage_reference, video_path)
            except Exception:
                await db.rollback()
                raise
            await db.rollback()
            return existing.video_path, False
    existing = (await db.execute(text("""
        SELECT id, video_path, idempotency_key, storage_reference FROM media_upload_sessions
        WHERE user_id = :owner AND content_sha256 = :digest AND state = 'completed'
        ORDER BY created_at DESC LIMIT 1
    """), {"owner": user_id, "digest": digest})).first()
    if existing is not None:
        return await _reuse_completed_upload(db, user_id, existing, idempotency_key, video_path)

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
        await _remember_idempotency_key(db, user_id, session_id, idempotency_key)
        await db.commit()
    except UploadIdempotencyConflict:
        await db.rollback()
        raise
    except IntegrityError:
        await db.rollback()
        await db.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:owner, 113463))"), {"owner": user_id})
        if idempotency_key:
            existing_key = (await db.execute(
                _COMPLETED_KEY_LOOKUP, {"owner": user_id, "key": idempotency_key}
            )).first()
            if existing_key is not None:
                if existing_key.content_sha256 != digest:
                    await db.rollback()
                    raise UploadIdempotencyConflict(
                        "This idempotency key was already used for a different file."
                    )
                try:
                    await _restore_missing_upload(
                        existing_key.video_path, existing_key.storage_reference, video_path,
                    )
                except Exception:
                    await db.rollback()
                    raise
                await db.rollback()
                return existing_key.video_path, False
        existing = (await db.execute(text("""
            SELECT id, video_path, idempotency_key, storage_reference FROM media_upload_sessions
            WHERE user_id = :owner AND content_sha256 = :digest AND state = 'completed'
            ORDER BY created_at DESC LIMIT 1
        """), {"owner": user_id, "digest": digest})).first()
        if existing is None:
            await db.rollback()
            raise
        return await _reuse_completed_upload(db, user_id, existing, idempotency_key, video_path)
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
