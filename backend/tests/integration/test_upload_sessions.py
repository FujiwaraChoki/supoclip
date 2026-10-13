"""Multipart capabilities stay within durable per-user admission boundaries."""

import asyncio
import uuid
from unittest.mock import Mock

import pytest
from botocore.exceptions import ClientError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.media_storage import owner_key
from src.repositories.upload_repository import reserve_upload, bind_upload, UploadLimitReached
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
    monkeypatch.setattr(media, '_get_authenticated_user_id', AsyncMock(return_value=owner['id']))
    request = SimpleNamespace(json=AsyncMock(return_value={'size': 1, 'filename': 'video.mp4'}))
    with pytest.raises(OSError, match='Abort unavailable'):
        await media.authorize_upload(request, db_session)
    session = (await db_session.execute(text('SELECT upload_id, state FROM media_upload_sessions WHERE user_id = :owner'), {'owner': owner['id']})).one()
    assert session.upload_id and session.state == 'uploading'
    with pytest.raises(UploadLimitReached):
        await reserve_upload(db_session, owner['id'], 1, storage.config)
