"""A multipart upload abandoned by a closed tab must not hold an upload slot for a day."""

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.api.routes import media
from src.media_storage import owner_key
from src.repositories.upload_repository import (
    UPLOAD_IDLE_SECONDS,
    UploadLimitReached,
    bind_upload,
    reclaim_idle_uploads,
    reserve_upload,
)
from tests.fixtures.factories import create_user
from tests.integration.test_media_deletion_transactions import media_backend  # noqa: F401

REQUEST = {"filename": "recording.mp4", "size": 1, "contentType": "video/mp4"}


async def _open_upload(db, storage, owner: str, *, age_seconds: int) -> dict:
    """A bound multipart session created ``age_seconds`` ago, as a browser leaves it."""
    session = await reserve_upload(db, owner, 1, storage.config)
    payload = {
        "uploadId": uuid.uuid4().hex,
        "video_path": f"upload://s3/{owner_key(owner)}/upload/{uuid.uuid4().hex}.mp4",
    }
    await bind_upload(db, session, {**payload, "parts": []})
    await db.execute(text(
        "UPDATE media_upload_sessions SET created_at = NOW() - make_interval(secs => CAST(:age AS INTEGER)) WHERE id = :id"
    ), {"id": session, "age": age_seconds})
    await db.commit()
    return {"session": session, **payload}


async def _state(db, session: str) -> str:
    return (await db.execute(
        text("SELECT state FROM media_upload_sessions WHERE id = :id"), {"id": session}
    )).scalar_one()


def _parts(*ages_seconds):
    now = datetime.now(timezone.utc)
    return {"Parts": [
        {"PartNumber": number, "Size": 1, "LastModified": now - timedelta(seconds=age)}
        for number, age in enumerate(ages_seconds, start=1)
    ], "IsTruncated": False}


@pytest.fixture
async def at_capacity(client, db_session, media_backend):  # noqa: F811
    """user-1 at a one-upload limit with S3 calls stubbed."""
    storage = media_backend
    await create_user(db_session, user_id="user-1")
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()
    storage.config.media_upload_concurrency = 1
    storage.client.create_multipart_upload = Mock(return_value={"UploadId": "fresh-upload"})
    storage.client.generate_presigned_url = Mock(return_value="https://storage.test/part")
    storage.client.abort_multipart_upload = Mock()
    storage.client.list_parts = Mock(return_value=_parts())
    yield storage
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()


async def test_abandoned_upload_frees_its_slot_for_a_new_authorization(client, db_session, at_capacity, auth_headers):
    storage = at_capacity
    stale = await _open_upload(db_session, storage, "user-1", age_seconds=UPLOAD_IDLE_SECONDS + 600)
    storage.client.list_parts.return_value = _parts(UPLOAD_IDLE_SECONDS + 300)

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["uploadId"] == "fresh-upload"
    assert await _state(db_session, stale["session"]) == "aborted"
    aborted = storage.client.abort_multipart_upload.call_args.kwargs
    assert aborted["UploadId"] == stale["uploadId"]
    assert aborted["Key"].endswith(stale["video_path"].rsplit("/", 1)[1])


async def test_abandoned_upload_without_any_part_is_reclaimed(client, db_session, at_capacity, auth_headers):
    stale = await _open_upload(db_session, at_capacity, "user-1", age_seconds=UPLOAD_IDLE_SECONDS + 60)

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 200, response.text
    assert await _state(db_session, stale["session"]) == "aborted"


async def test_upload_still_sending_parts_keeps_its_slot(client, db_session, at_capacity, auth_headers):
    storage = at_capacity
    active = await _open_upload(db_session, storage, "user-1", age_seconds=3 * UPLOAD_IDLE_SECONDS)
    storage.client.list_parts.return_value = _parts(3 * UPLOAD_IDLE_SECONDS, 120)

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 429
    assert await _state(db_session, active["session"]) == "uploading"
    storage.client.abort_multipart_upload.assert_not_called()
    storage.client.create_multipart_upload.assert_not_called()


async def test_recent_upload_is_not_even_inspected(client, db_session, at_capacity, auth_headers):
    storage = at_capacity
    recent = await _open_upload(db_session, storage, "user-1", age_seconds=UPLOAD_IDLE_SECONDS - 300)

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 429
    assert await _state(db_session, recent["session"]) == "uploading"
    storage.client.list_parts.assert_not_called()
    storage.client.abort_multipart_upload.assert_not_called()


@pytest.mark.parametrize("parts_error", [
    ClientError({"Error": {"Code": "ServiceUnavailable"}}, "ListParts"),
    None,
])
async def test_unknown_activity_or_failed_abort_keeps_the_slot(client, db_session, at_capacity, auth_headers, parts_error):
    storage = at_capacity
    stale = await _open_upload(db_session, storage, "user-1", age_seconds=2 * UPLOAD_IDLE_SECONDS)
    if parts_error is not None:
        storage.client.list_parts.side_effect = parts_error
    else:
        storage.client.abort_multipart_upload.side_effect = ClientError(
            {"Error": {"Code": "ServiceUnavailable"}}, "AbortMultipartUpload")

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 429
    assert await _state(db_session, stale["session"]) == "uploading"


async def test_part_without_timestamp_counts_as_activity(db_session, at_capacity):
    storage = at_capacity
    stale = await _open_upload(db_session, storage, "user-1", age_seconds=2 * UPLOAD_IDLE_SECONDS)
    storage.client.list_parts.return_value = {"Parts": [{"PartNumber": 1, "Size": 1}], "IsTruncated": False}

    assert await reclaim_idle_uploads(db_session, "user-1", storage) == 0
    assert await _state(db_session, stale["session"]) == "uploading"
    storage.client.abort_multipart_upload.assert_not_called()


async def test_multipart_session_already_gone_is_released(db_session, at_capacity):
    storage = at_capacity
    stale = await _open_upload(db_session, storage, "user-1", age_seconds=2 * UPLOAD_IDLE_SECONDS)
    storage.client.list_parts.side_effect = ClientError({"Error": {"Code": "NoSuchUpload"}}, "ListParts")

    assert await reclaim_idle_uploads(db_session, "user-1", storage) == 1
    assert await _state(db_session, stale["session"]) == "aborted"


async def test_completion_during_reclaim_stays_completed(db_session, initialized_database, at_capacity):
    """A completion that lands while the bucket abort runs keeps its row."""
    storage = at_capacity
    stale = await _open_upload(db_session, storage, "user-1", age_seconds=2 * UPLOAD_IDLE_SECONDS)
    loop = asyncio.get_running_loop()
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def complete():
        async with sessions() as other:
            await other.execute(text("UPDATE media_upload_sessions SET state = 'completed' WHERE id = :id"),
                {"id": stale["session"]})
            await other.commit()

    storage.client.abort_multipart_upload.side_effect = (
        lambda **_: asyncio.run_coroutine_threadsafe(complete(), loop).result(5))

    assert await reclaim_idle_uploads(db_session, "user-1", storage) == 0
    assert await _state(db_session, stale["session"]) == "completed"


async def test_other_users_uploads_are_never_reclaimed(db_session, at_capacity):
    storage = at_capacity
    other = await create_user(db_session)
    await db_session.commit()
    theirs = await _open_upload(db_session, storage, other["id"], age_seconds=2 * UPLOAD_IDLE_SECONDS)

    assert await reclaim_idle_uploads(db_session, "user-1", storage) == 0
    assert await _state(db_session, theirs["session"]) == "uploading"
    storage.client.list_parts.assert_not_called()


async def test_direct_upload_route_reclaims_abandoned_multipart_slot(client, db_session, at_capacity, auth_headers, monkeypatch):
    """The iOS /upload route shares the slots, and must not 429 after receiving the whole file."""
    storage = at_capacity
    monkeypatch.setattr(media, "persist_upload_after_response", lambda *_: None)
    stale = await _open_upload(db_session, storage, "user-1", age_seconds=2 * UPLOAD_IDLE_SECONDS)

    response = await client.post("/upload", headers=auth_headers,
        files={"video": ("phone.mp4", b"phone-video-bytes", "video/mp4")})

    assert response.status_code == 200, response.text
    assert await _state(db_session, stale["session"]) == "aborted"


async def test_reservation_without_a_bucket_upload_is_released(db_session, at_capacity):
    """A reservation whose bucket upload never started holds nothing to abort."""
    storage = at_capacity
    session = await reserve_upload(db_session, "user-1", 1, storage.config)
    await db_session.execute(text(
        "UPDATE media_upload_sessions SET created_at = NOW() - INTERVAL '2 hours' WHERE id = :id"
    ), {"id": session})
    await db_session.commit()

    assert await reclaim_idle_uploads(db_session, "user-1", storage) == 1
    assert await _state(db_session, session) == "aborted"
    storage.client.abort_multipart_upload.assert_not_called()
    assert await reserve_upload(db_session, "user-1", 1, storage.config)
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, "user-1", 1, storage.config)
