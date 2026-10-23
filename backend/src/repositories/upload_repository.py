"""Trusted multipart sessions and admission limits shared across API workers."""

from uuid import uuid4

from sqlalchemy import text

from ..media.upload_limits import upload_too_large_detail


class UploadLimitReached(ValueError):
    pass


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
