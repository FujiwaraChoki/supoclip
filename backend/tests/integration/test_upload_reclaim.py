"""A multipart upload abandoned by a crashed tab must not hold an upload slot for a day,
and an upload that may still be sending a part must never lose its slot."""

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
    UPLOAD_RECLAIM_MIN_AGE_SECONDS as MIN_AGE,
    UploadConcurrencyLimitReached,
    UploadDailyLimitReached,
    UploadLimitReached,
    bind_upload,
    finish_upload,
    reclaim_dead_uploads,
    reserve_upload,
)
from tests.fixtures.factories import create_user
from tests.integration.test_media_deletion_transactions import media_backend  # noqa: F401

REQUEST = {"filename": "recording.mp4", "size": 1, "contentType": "video/mp4"}
GONE = ClientError({"Error": {"Code": "NoSuchUpload"}}, "ListParts")


async def _open_upload(db, storage, owner: str, *, age_seconds: int) -> dict:
    """A bound multipart session created ``age_seconds`` ago, as a browser leaves it."""
    session = await reserve_upload(db, owner, 1, storage.config)
    payload = {
        "uploadId": uuid.uuid4().hex,
        "video_path": f"upload://s3/{owner_key(owner)}/upload/{uuid.uuid4().hex}.mp4",
    }
    await bind_upload(db, session, {**payload, "parts": []})
    await _age(db, session, age_seconds)
    return {"session": session, **payload}


async def _age(db, session: str, age_seconds: int) -> None:
    await db.execute(text(
        "UPDATE media_upload_sessions SET created_at = NOW() - make_interval(secs => CAST(:age AS INTEGER)) WHERE id = :id"
    ), {"id": session, "age": age_seconds})
    await db.commit()


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


async def test_crashed_tab_whose_upload_is_gone_frees_its_slot(client, db_session, at_capacity, auth_headers):
    storage = at_capacity
    dead = await _open_upload(db_session, storage, "user-1", age_seconds=MIN_AGE + 600)
    storage.client.list_parts.side_effect = GONE

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 200, response.text
    assert response.json()["uploadId"] == "fresh-upload"
    assert await _state(db_session, dead["session"]) == "aborted"
    listed = storage.client.list_parts.call_args.kwargs
    assert listed["UploadId"] == dead["uploadId"] and listed["MaxParts"] == 1
    assert listed["Key"].endswith(dead["video_path"].rsplit("/", 1)[1])
    storage.client.abort_multipart_upload.assert_not_called()


@pytest.mark.parametrize("listing", [
    _parts(),                                   # slow link: first part still in flight
    _parts(5 * MIN_AGE),                        # one part, hours ago; the next one still in flight
    _parts(5 * MIN_AGE, 4 * MIN_AGE, 3 * MIN_AGE),  # long sleep or frozen tab mid-upload
    {"Parts": [{"PartNumber": 1, "Size": 1}], "IsTruncated": False},  # no timestamps at all
], ids=["no-parts", "old-part", "all-parts-stale", "no-timestamp"])
async def test_open_upload_without_recent_parts_keeps_its_slot(client, db_session, at_capacity, auth_headers, listing):
    """A missing or old finished part never proves the client stopped: the part in flight is unlisted."""
    storage = at_capacity
    slow = await _open_upload(db_session, storage, "user-1", age_seconds=6 * MIN_AGE)
    storage.client.list_parts.return_value = listing

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 429
    assert await _state(db_session, slow["session"]) == "uploading"
    storage.client.abort_multipart_upload.assert_not_called()
    storage.client.create_multipart_upload.assert_not_called()


async def test_slow_upload_keeps_working_after_a_competing_authorization(client, db_session, at_capacity, auth_headers):
    """The slow client's part refresh and completion still succeed after another tab hit the limit."""
    storage = at_capacity
    slow = await _open_upload(db_session, storage, "user-1", age_seconds=10 * MIN_AGE)
    payload = {"uploadId": slow["uploadId"], "video_path": slow["video_path"]}

    assert (await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)).status_code == 429

    refreshed = await client.post("/upload/parts", json={**payload, "partNumbers": [1]}, headers=auth_headers)
    assert refreshed.status_code == 200, refreshed.text
    storage.client.list_parts.return_value = _parts(1)
    storage.client.complete_multipart_upload = Mock()
    storage.client.head_object = Mock(return_value={"ContentLength": 1, "ContentType": "video/mp4"})
    completed = await client.post("/upload/complete", headers=auth_headers,
        json={**payload, "parts": [{"partNumber": 1, "etag": "etag-1"}]})
    assert completed.status_code == 200, completed.text
    assert await _state(db_session, slow["session"]) == "completed"
    storage.client.abort_multipart_upload.assert_not_called()


async def test_recent_upload_is_not_even_inspected(client, db_session, at_capacity, auth_headers):
    storage = at_capacity
    recent = await _open_upload(db_session, storage, "user-1", age_seconds=MIN_AGE - 300)
    storage.client.list_parts.side_effect = GONE

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 429
    assert await _state(db_session, recent["session"]) == "uploading"
    storage.client.list_parts.assert_not_called()


async def test_storage_error_keeps_the_slot(client, db_session, at_capacity, auth_headers):
    storage = at_capacity
    unknown = await _open_upload(db_session, storage, "user-1", age_seconds=2 * MIN_AGE)
    storage.client.list_parts.side_effect = ClientError({"Error": {"Code": "ServiceUnavailable"}}, "ListParts")

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 429
    assert await _state(db_session, unknown["session"]) == "uploading"


async def _fill_daily_quota(db, storage, owner: str) -> str:
    """Spend the day's uploads; the last one holds the only slot and is provably dead."""
    for _ in range(storage.config.media_upload_daily_limit - 1):
        session = await reserve_upload(db, owner, 1, storage.config)
        await finish_upload(db, session, "aborted")
    dead = await _open_upload(db, storage, owner, age_seconds=2 * MIN_AGE)
    return dead["session"]


async def test_daily_limit_never_reclaims_on_authorize(client, db_session, at_capacity, auth_headers):
    storage = at_capacity
    storage.config.media_upload_daily_limit = 4
    dead = await _fill_daily_quota(db_session, storage, "user-1")
    storage.client.list_parts.side_effect = GONE

    response = await client.post("/upload/authorize", json=REQUEST, headers=auth_headers)

    assert response.status_code == 429
    assert await _state(db_session, dead) == "uploading"
    storage.client.list_parts.assert_not_called()
    storage.client.create_multipart_upload.assert_not_called()


async def test_daily_limit_never_reclaims_on_direct_upload(client, db_session, at_capacity, auth_headers, monkeypatch):
    storage = at_capacity
    storage.config.media_upload_daily_limit = 4
    monkeypatch.setattr(media, "persist_upload_after_response", lambda *_: None)
    dead = await _fill_daily_quota(db_session, storage, "user-1")
    storage.client.list_parts.side_effect = GONE

    response = await client.post("/upload", headers=auth_headers,
        files={"video": ("phone.mp4", b"phone-video-bytes", "video/mp4")})

    assert response.status_code == 429
    assert await _state(db_session, dead) == "uploading"
    storage.client.list_parts.assert_not_called()


async def test_daily_limit_is_reported_before_concurrency(db_session, at_capacity):
    storage = at_capacity
    storage.config.media_upload_daily_limit = 1
    await _open_upload(db_session, storage, "user-1", age_seconds=2 * MIN_AGE)

    with pytest.raises(UploadDailyLimitReached):
        await reserve_upload(db_session, "user-1", 1, storage.config)
    storage.config.media_upload_daily_limit = 20
    with pytest.raises(UploadConcurrencyLimitReached):
        await reserve_upload(db_session, "user-1", 1, storage.config)


async def test_completion_racing_reclaim_stays_completed(db_session, initialized_database, at_capacity):
    """The bucket completes (NoSuchUpload) and the row is marked completed while reclaim looks."""
    storage = at_capacity
    racing = await _open_upload(db_session, storage, "user-1", age_seconds=2 * MIN_AGE)
    loop = asyncio.get_running_loop()
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def complete():
        async with sessions() as other:
            await finish_upload(other, racing["session"], "completed")

    def list_parts(**_):
        asyncio.run_coroutine_threadsafe(complete(), loop).result(5)
        raise GONE

    storage.client.list_parts.side_effect = list_parts

    assert await reclaim_dead_uploads(db_session, "user-1", storage) == 0
    assert await _state(db_session, racing["session"]) == "completed"


async def test_completion_recorded_after_reclaim_wins(db_session, at_capacity):
    """The bucket completed, reclaim released the slot, then the completing request records it."""
    storage = at_capacity
    racing = await _open_upload(db_session, storage, "user-1", age_seconds=2 * MIN_AGE)
    storage.client.list_parts.side_effect = GONE

    assert await reclaim_dead_uploads(db_session, "user-1", storage) == 1
    await finish_upload(db_session, racing["session"], "completed")
    assert await _state(db_session, racing["session"]) == "completed"


async def test_other_users_uploads_are_never_reclaimed(db_session, at_capacity):
    storage = at_capacity
    other = await create_user(db_session)
    await db_session.commit()
    theirs = await _open_upload(db_session, storage, other["id"], age_seconds=2 * MIN_AGE)
    storage.client.list_parts.side_effect = GONE

    assert await reclaim_dead_uploads(db_session, "user-1", storage) == 0
    assert await _state(db_session, theirs["session"]) == "uploading"
    storage.client.list_parts.assert_not_called()


async def test_direct_upload_route_reclaims_dead_multipart_slot(client, db_session, at_capacity, auth_headers, monkeypatch):
    """The iOS /upload route shares the slots, and must not 429 after receiving the whole file."""
    storage = at_capacity
    monkeypatch.setattr(media, "persist_upload_after_response", lambda *_: None)
    dead = await _open_upload(db_session, storage, "user-1", age_seconds=2 * MIN_AGE)
    storage.client.list_parts.side_effect = GONE

    response = await client.post("/upload", headers=auth_headers,
        files={"video": ("phone.mp4", b"phone-video-bytes", "video/mp4")})

    assert response.status_code == 200, response.text
    assert await _state(db_session, dead["session"]) == "aborted"


async def test_direct_upload_route_keeps_open_multipart_slot(client, db_session, at_capacity, auth_headers, monkeypatch):
    storage = at_capacity
    monkeypatch.setattr(media, "persist_upload_after_response", lambda *_: None)
    slow = await _open_upload(db_session, storage, "user-1", age_seconds=6 * MIN_AGE)

    response = await client.post("/upload", headers=auth_headers,
        files={"video": ("phone.mp4", b"phone-video-bytes", "video/mp4")})

    assert response.status_code == 429
    assert await _state(db_session, slow["session"]) == "uploading"
    storage.client.abort_multipart_upload.assert_not_called()


async def test_reservation_without_a_bucket_upload_is_released(db_session, at_capacity):
    """A reservation never bound to a bucket upload: no client received an upload id for it."""
    storage = at_capacity
    session = await reserve_upload(db_session, "user-1", 1, storage.config)
    await _age(db_session, session, 2 * MIN_AGE)

    assert await reclaim_dead_uploads(db_session, "user-1", storage) == 1
    assert await _state(db_session, session) == "aborted"
    storage.client.list_parts.assert_not_called()
    storage.client.abort_multipart_upload.assert_not_called()
    assert await reserve_upload(db_session, "user-1", 1, storage.config)
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, "user-1", 1, storage.config)
