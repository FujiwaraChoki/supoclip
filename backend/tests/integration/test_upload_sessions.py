"""Multipart capabilities stay within durable per-user admission boundaries."""

import asyncio
import uuid
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.media_storage import PART_BYTES, owner_key
from src.repositories.upload_repository import reserve_upload, bind_upload, finish_upload, UploadLimitReached
from src.workers.media_cleanup import cleanup_upload_sessions
from tests.fixtures.factories import create_user
from tests.integration.test_media_deletion_transactions import media_backend


async def test_multipart_api_binds_refresh_to_declared_size_and_session(client, db_session, media_backend, auth_headers):
    storage = media_backend
    await create_user(db_session, user_id="user-1")
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()
    storage.config.media_upload_concurrency = 1
    storage.client.create_multipart_upload = Mock(return_value={"UploadId": "one-byte-upload"})
    storage.client.generate_presigned_url = Mock(return_value="https://storage.test/part")
    storage.client.abort_multipart_upload = Mock()
    request = {"filename": "recording", "size": 1, "contentType": "video/mp4"}
    response = await client.post("/upload/authorize", json=request, headers=auth_headers)
    assert response.status_code == 200
    authorization = response.json()
    assert len(authorization["parts"]) == 1
    assert storage.client.generate_presigned_url.call_args.kwargs["Params"]["ContentLength"] == 1
    payload = {"video_path": authorization["video_path"], "uploadId": authorization["uploadId"]}
    assert (await client.post("/upload/authorize", json=request, headers=auth_headers)).status_code == 429
    assert storage.client.create_multipart_upload.call_count == 1
    for malicious in ({**payload, "partNumbers": [2], "size": 12000000000},
                      {**payload, "partNumbers": [True]},
                      {**payload, "uploadId": "unrecorded-upload", "partNumbers": [1]}):
        assert (await client.post("/upload/parts", json=malicious, headers=auth_headers)).status_code == 400
    assert (await client.post("/upload/parts", json={**payload, "partNumbers": [1]}, headers=auth_headers)).status_code == 200
    assert storage.client.generate_presigned_url.call_args.kwargs["Params"]["ContentLength"] == 1
    assert (await client.post("/upload/abort", json=payload, headers=auth_headers)).status_code == 200
    assert (await db_session.execute(text("SELECT state FROM media_upload_sessions WHERE upload_id = 'one-byte-upload'"))).scalar() == "aborted"
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()


async def test_session_capacity_is_atomic_across_connections(db_session, initialized_database, media_backend):
    owner = await create_user(db_session)
    await db_session.commit()
    storage = media_backend
    storage.config.media_upload_concurrency = 1
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    async def attempt():
        async with sessions() as db:
            return await reserve_upload(db, owner["id"], 1, storage.config)
    results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
    assert sum(isinstance(result, str) for result in results) == 1
    assert sum(isinstance(result, UploadLimitReached) for result in results) == 1


async def test_failed_abort_retains_expired_capacity_until_cleanup_succeeds(db_session, media_backend):
    storage = media_backend
    storage.config.media_upload_concurrency = 1
    owner = await create_user(db_session)
    await db_session.commit()
    session = await reserve_upload(db_session, owner["id"], 1, storage.config)
    authorization = {"uploadId": uuid.uuid4().hex, "video_path": f"upload://s3/{owner_key(owner['id'])}/upload/video.mp4"}
    await bind_upload(db_session, session, authorization)
    await db_session.execute(text("UPDATE media_upload_sessions SET expires_at = NOW() - INTERVAL '1 second' WHERE id = :id"), {"id": session})
    await db_session.commit()
    storage.client.abort_multipart_upload = Mock(side_effect=ClientError({"Error": {"Code": "ServiceUnavailable"}}, "AbortMultipartUpload"))
    await cleanup_upload_sessions(db_session, storage)
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, owner["id"], 1, storage.config)
    storage.client.abort_multipart_upload = Mock()
    await cleanup_upload_sessions(db_session, storage)
    assert await reserve_upload(db_session, owner["id"], 1, storage.config)


async def test_abandoned_sessions_stop_holding_upload_slots(db_session, media_backend):
    """A closed tab never sends its abort; its session must not block new uploads for a day."""
    storage = media_backend
    storage.config.media_upload_concurrency = 3
    storage.config.media_upload_stale_seconds = 3 * 3600
    owner = await create_user(db_session)
    await db_session.commit()
    abandoned = []
    for number in range(3):
        session = await reserve_upload(db_session, owner["id"], 1, storage.config)
        await bind_upload(db_session, session, {
            "uploadId": uuid.uuid4().hex,
            "video_path": f"upload://s3/{owner_key(owner['id'])}/abandoned-{number}/video.mp4",
        })
        abandoned.append(session)
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, owner["id"], 1, storage.config)
    # Past the stale window but well inside the one-day expiry.
    await db_session.execute(text("""
        UPDATE media_upload_sessions SET created_at = NOW() - INTERVAL '4 hours'
        WHERE id = ANY(:ids)
    """), {"ids": abandoned})
    await db_session.commit()
    fresh = await reserve_upload(db_session, owner["id"], 1, storage.config)

    storage.client.list_parts = Mock(return_value={"Parts": []})
    storage.client.abort_multipart_upload = Mock()
    await cleanup_upload_sessions(db_session, storage)
    assert len(_aborted(storage, owner)) == 3
    states = dict((await db_session.execute(text(
        "SELECT id, state FROM media_upload_sessions WHERE user_id = :owner"
    ), {"owner": owner["id"]})).all())
    assert {states[session] for session in abandoned} == {"aborted"}
    assert states[fresh] == "reserved"


async def _open_upload(db, config, owner, name, *, age="4 hours", activity=None):
    session = await reserve_upload(db, owner["id"], 1, config)
    authorization = {"uploadId": f"{name}-{uuid.uuid4().hex}",
                     "video_path": f"upload://s3/{owner_key(owner['id'])}/{name}/video.mp4"}
    await bind_upload(db, session, authorization)
    await db.execute(text(f"""
        UPDATE media_upload_sessions SET created_at = NOW() - INTERVAL '{age}',
            last_activity_at = {"NULL" if activity is None else f"NOW() - INTERVAL '{activity}'"}
        WHERE id = :id
    """), {"id": session})
    await db.commit()
    return session, authorization


def _aborted(storage, owner):
    """Upload ids aborted for this owner; other tests' leftover sessions are cleaned up too."""
    prefix = owner_key(owner["id"])
    return [call.kwargs["UploadId"] for call in storage.client.abort_multipart_upload.call_args_list
            if f"/{prefix}/" in call.kwargs["Key"]]


async def _states(db, owner):
    return dict((await db.execute(text(
        "SELECT id, state FROM media_upload_sessions WHERE user_id = :owner"
    ), {"owner": owner["id"]})).all())


async def test_slow_upload_that_keeps_reporting_parts_keeps_its_slot(client, db_session, media_backend, auth_headers):
    """A 4 GB file on a slow uplink can take longer than the stale window; parts keep it alive."""
    storage = media_backend
    storage.config.media_upload_concurrency = 1
    storage.config.media_upload_stale_seconds = 3 * 3600
    owner = await create_user(db_session, user_id="user-1")
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()
    session, authorization = await _open_upload(db_session, storage.config, owner, "slow", activity="2 hours 50 minutes")

    # The browser finishes a part: the session is fresh again.
    response = await client.post("/upload/progress", json=authorization, headers=auth_headers)
    assert response.status_code == 200
    # A part URL refresh is activity too.
    storage.client.generate_presigned_url = Mock(return_value="https://storage.test/part")
    response = await client.post("/upload/parts", json={**authorization, "partNumbers": [1]}, headers=auth_headers)
    assert response.status_code == 200
    # Hours past creation, it still holds the only slot and cleanup leaves it running.
    await db_session.execute(text("UPDATE media_upload_sessions SET created_at = NOW() - INTERVAL '20 hours' WHERE id = :id"),
                             {"id": session})
    await db_session.commit()
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, owner["id"], 1, storage.config)
    storage.client.list_parts = Mock(return_value={"Parts": []})
    storage.client.abort_multipart_upload = Mock()
    await cleanup_upload_sessions(db_session, storage)
    assert _aborted(storage, owner) == []
    assert (await _states(db_session, owner))[session] == "uploading"

    # Someone else's progress report cannot keep it alive.
    other = await client.post("/upload/progress", json={**authorization, "uploadId": "someone-else"}, headers=auth_headers)
    assert other.status_code == 400
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()


async def test_quiet_session_is_not_revived_by_a_late_report(db_session, media_backend):
    """Once its slot was released, a session cannot take it back: cleanup aborts it."""
    from src.repositories.upload_repository import touch_upload

    storage = media_backend
    storage.config.media_upload_stale_seconds = 3 * 3600
    owner = await create_user(db_session)
    await db_session.commit()
    stale, _ = await _open_upload(db_session, storage.config, owner, "stale", activity="3 hours 1 minute")
    fresh, _ = await _open_upload(db_session, storage.config, owner, "fresh", activity="2 hours")
    assert await touch_upload(db_session, stale, storage.config) is False
    assert await touch_upload(db_session, fresh, storage.config) is True
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = :owner"), {"owner": owner["id"]})
    await db_session.commit()


async def test_cleanup_keeps_a_stale_session_whose_parts_are_still_arriving(db_session, media_backend):
    """A page loaded before progress reports existed never sends them; storage shows it is alive."""
    from datetime import datetime, timedelta, timezone

    storage = media_backend
    storage.config.media_upload_concurrency = 2
    storage.config.media_upload_stale_seconds = 3 * 3600
    owner = await create_user(db_session)
    await db_session.commit()
    alive, alive_auth = await _open_upload(db_session, storage.config, owner, "alive")
    dead, dead_auth = await _open_upload(db_session, storage.config, owner, "dead")
    recent = datetime.now(timezone.utc) - timedelta(minutes=5)
    old = datetime.now(timezone.utc) - timedelta(hours=4)

    def list_parts(**kwargs):
        when = recent if kwargs["UploadId"] == alive_auth["uploadId"] else old
        return {"Parts": [{"PartNumber": 1, "Size": 1, "LastModified": when}]}

    storage.client.list_parts = Mock(side_effect=list_parts)
    storage.client.abort_multipart_upload = Mock()
    await cleanup_upload_sessions(db_session, storage)
    states = await _states(db_session, owner)
    assert states == {alive: "uploading", dead: "aborted"}
    assert _aborted(storage, owner) == [dead_auth["uploadId"]]
    # The live upload holds its slot again, so the cap still means two at once.
    await reserve_upload(db_session, owner["id"], 1, storage.config)
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, owner["id"], 1, storage.config)
    activity = (await db_session.execute(text(
        "SELECT last_activity_at FROM media_upload_sessions WHERE id = :id"), {"id": alive})).scalar_one()
    assert abs((activity - recent).total_seconds()) < 1


async def test_one_failing_abort_does_not_hold_up_the_others(db_session, media_backend):
    storage = media_backend
    storage.config.media_upload_stale_seconds = 3 * 3600
    owner = await create_user(db_session)
    await db_session.commit()
    sessions = [await _open_upload(db_session, storage.config, owner, f"quiet-{number}", age=f"{5 + number} hours")
                for number in range(3)]
    # Expired a while ago too, so this one sorts first.
    await db_session.execute(text("UPDATE media_upload_sessions SET expires_at = NOW() - INTERVAL '1 hour' WHERE id = :id"),
                             {"id": sessions[0][0]})
    await db_session.commit()
    failing = sessions[0][1]["uploadId"]

    def abort(**kwargs):
        if kwargs["UploadId"] == failing:
            raise ClientError({"Error": {"Code": "ServiceUnavailable"}}, "AbortMultipartUpload")

    storage.client.list_parts = Mock(side_effect=ClientError({"Error": {"Code": "NoSuchUpload"}}, "ListParts"))
    storage.client.abort_multipart_upload = Mock(side_effect=abort)
    await cleanup_upload_sessions(db_session, storage)
    states = await _states(db_session, owner)
    assert states[sessions[0][0]] == "uploading"
    assert states[sessions[1][0]] == states[sessions[2][0]] == "aborted"
    # The next run retries it.
    storage.client.abort_multipart_upload = Mock()
    await cleanup_upload_sessions(db_session, storage)
    assert (await _states(db_session, owner))[sessions[0][0]] == "aborted"


async def test_cleanup_never_aborts_a_completed_upload(db_session, media_backend):
    storage = media_backend
    storage.config.media_upload_stale_seconds = 3 * 3600
    owner = await create_user(db_session)
    await db_session.commit()
    session, _ = await _open_upload(db_session, storage.config, owner, "done", age="20 hours")
    await finish_upload(db_session, session, "completed")
    storage.client.list_parts = Mock()
    storage.client.abort_multipart_upload = Mock()
    await cleanup_upload_sessions(db_session, storage)
    assert _aborted(storage, owner) == []
    # A late abort (the page closing after completion) leaves it completed.
    await finish_upload(db_session, session, "aborted")
    assert (await _states(db_session, owner))[session] == "completed"


async def test_daily_limit_cannot_be_reset_by_aborting_sessions(db_session, media_backend):
    owner = await create_user(db_session)
    await db_session.commit()
    media_backend.config.media_upload_daily_limit = 1
    await reserve_upload(db_session, owner["id"], 1, media_backend.config)
    await db_session.execute(text("UPDATE media_upload_sessions SET state = 'aborted' WHERE user_id = :owner"), {"owner": owner["id"]})
    await db_session.commit()
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, owner["id"], 1, media_backend.config)


async def test_signing_and_abort_failure_keeps_sdk_identity_and_capacity(db_session, media_backend, monkeypatch):
    from src.api.routes import media
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    storage = media_backend
    owner = await create_user(db_session)
    await db_session.commit()
    storage.config.media_upload_concurrency = 1
    storage.client.create_multipart_upload = Mock(return_value={'UploadId': uuid.uuid4().hex})
    storage.client.generate_presigned_url = Mock(side_effect=OSError('Signing failed'))
    storage.client.abort_multipart_upload = Mock(side_effect=OSError('Abort unavailable'))
    from src.auth_headers import AuthContext
    monkeypatch.setattr(media, '_get_auth_context', AsyncMock(return_value=AuthContext(user_id=owner['id'], team_id=None)))
    request = SimpleNamespace(json=AsyncMock(return_value={'size': 1, 'filename': 'video.mp4'}))
    with pytest.raises(OSError, match='Abort unavailable'):
        await media.authorize_upload(request, db_session)
    session = (await db_session.execute(text('SELECT upload_id, state FROM media_upload_sessions WHERE user_id = :owner'), {'owner': owner['id']})).one()
    assert session.upload_id and session.state == 'uploading'
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, owner['id'], 1, storage.config)


@pytest.mark.parametrize("plan,status,size,accepted", [
    ("pro", "active", 60_000_000_000, True),
    ("pro", "active", 60_000_000_001, False),
    ("scale", "active", 90_000_000_000, True),
    ("scale", "active", 90_000_000_001, False),
    ("free", "inactive", 4_000_000_001, False),
])
async def test_direct_upload_size_follows_the_plan(client, db_session, media_backend, auth_headers, plan, status, size, accepted):
    storage = media_backend
    storage.config.monetization_enabled = True
    storage.config.max_video_upload_bytes = 4_000_000_000
    storage.config.pro_max_video_upload_bytes = 60_000_000_000
    storage.config.scale_max_video_upload_bytes = 90_000_000_000
    storage.config.team_max_video_upload_bytes = 90_000_000_000
    await create_user(db_session, user_id="user-1", plan=plan, subscription_status=status)
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()
    storage.client.create_multipart_upload = Mock(return_value={"UploadId": "plan-sized-upload"})
    storage.client.generate_presigned_url = Mock(return_value="https://storage.test/part")
    request = {"filename": "recording.mp4", "size": size, "contentType": "video/mp4"}
    response = await client.post("/upload/authorize", json=request, headers=auth_headers)
    if accepted:
        assert response.status_code == 200
        assert len(response.json()["parts"]) == (size + PART_BYTES - 1) // PART_BYTES
    else:
        assert response.status_code == 400
        assert "too large" in response.json()["detail"]
        storage.client.create_multipart_upload.assert_not_called()
    await db_session.execute(text("DELETE FROM media_upload_sessions WHERE user_id = 'user-1'"))
    await db_session.commit()
