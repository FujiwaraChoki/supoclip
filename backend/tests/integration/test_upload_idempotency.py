"""Retries of one upload reuse the stored file and the in-progress task."""

import asyncio
import hashlib
import hmac
import json
import time
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.media_storage import MediaStorage
from src.repositories.task_repository import TaskRepository
from src.workers.media_cleanup import cleanup_upload_sessions
from tests.fixtures.factories import create_source, create_task, create_user


def _headers_for(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    signature = hmac.new(b"test-backend-auth-secret", payload, hashlib.sha256).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


async def _upload(client, headers, payload: bytes, *, key: str | None = None, name: str = "demo.mp4"):
    extra = {"Idempotency-Key": key} if key else {}
    return await client.post(
        "/upload",
        headers={**headers, **extra},
        files={"video": (name, payload, "video/mp4")},
    )


@pytest.mark.asyncio
async def test_upload_stores_bytes_without_normalizing_and_retries_reuse_the_file(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    app.state.config.temp_dir = str(tmp_path)

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("slow upload work must not run before the response")

    monkeypatch.setattr("src.media.source_limits.normalize_source_media", _forbidden)
    monkeypatch.setattr(MediaStorage, "persist_source", _forbidden)

    first_bytes = b"same-video-bytes"
    first = await _upload(client, auth_headers, first_bytes)
    assert first.status_code == 200, first.text
    video_path = first.json()["video_path"]
    assert video_path.startswith("upload://")
    assert not video_path.startswith("upload://s3/")
    saved = tmp_path / "uploads" / video_path.removeprefix("upload://")
    assert saved.read_bytes() == first_bytes

    again = await _upload(client, auth_headers, first_bytes)
    assert again.status_code == 200, again.text
    assert again.json() == {
        "message": "Video uploaded successfully",
        "video_path": video_path,
    }
    assert list((tmp_path / "uploads").glob("*.mp4")) == [saved]

    other = await _upload(client, auth_headers, b"different-video-bytes")
    assert other.status_code == 200, other.text
    assert other.json()["video_path"] != video_path
    assert len(list((tmp_path / "uploads").glob("*.mp4"))) == 2

    sessions = (await db_session.execute(text(
        "SELECT COUNT(*) FROM media_upload_sessions WHERE user_id = 'user-1' AND state = 'completed'"
    ))).scalar()
    assert sessions == 2


@pytest.mark.asyncio
async def test_reupload_restores_missing_bytes_without_a_durable_object(
    client, app, auth_headers, db_session, tmp_path,
):
    await create_user(db_session, user_id="user-1")
    app.state.config.temp_dir = str(tmp_path)
    payload = b"restore-me"
    first = await _upload(client, auth_headers, payload)
    assert first.status_code == 200, first.text
    video_path = first.json()["video_path"]
    saved = tmp_path / "uploads" / video_path.removeprefix("upload://")
    saved.unlink()

    again = await _upload(client, auth_headers, payload)
    assert again.status_code == 200, again.text
    assert again.json()["video_path"] == video_path
    assert saved.read_bytes() == payload
    assert list((tmp_path / "uploads").glob("*.mp4")) == [saved]

    keyed = b"restore-with-key"
    key = "client-retry-9"
    keyed_first = await _upload(client, auth_headers, keyed, key=key)
    assert keyed_first.status_code == 200, keyed_first.text
    keyed_path = keyed_first.json()["video_path"]
    keyed_file = tmp_path / "uploads" / keyed_path.removeprefix("upload://")
    keyed_file.unlink()
    keyed_again = await _upload(client, auth_headers, keyed, key=key)
    assert keyed_again.status_code == 200, keyed_again.text
    assert keyed_again.json()["video_path"] == keyed_path
    assert keyed_file.read_bytes() == keyed

    durable = b"kept-in-object-storage"
    durable_first = await _upload(client, auth_headers, durable)
    assert durable_first.status_code == 200, durable_first.text
    durable_path = durable_first.json()["video_path"]
    await db_session.execute(text("""
        UPDATE media_upload_sessions
        SET storage_reference = 'sources/user/original.mp4'
        WHERE video_path = :path
    """), {"path": durable_path})
    await db_session.commit()
    durable_file = tmp_path / "uploads" / durable_path.removeprefix("upload://")
    durable_file.unlink()
    durable_again = await _upload(client, auth_headers, durable)
    assert durable_again.status_code == 200, durable_again.text
    assert durable_again.json()["video_path"] == durable_path
    assert not durable_file.exists()
    assert sorted(path.name for path in (tmp_path / "uploads").glob("*.mp4")) == sorted([
        saved.name,
        keyed_file.name,
    ])


@pytest.mark.asyncio
async def test_idempotency_key_reuses_one_file_and_rejects_a_different_file(
    client, app, auth_headers, db_session, tmp_path,
):
    await create_user(db_session, user_id="user-1")
    app.state.config.temp_dir = str(tmp_path)
    key = "ios-retry-key"

    invalid = await _upload(client, auth_headers, b"video", key="short")
    assert invalid.status_code == 400
    assert not (tmp_path / "uploads").exists() or not list((tmp_path / "uploads").glob("*.mp4"))

    first = await _upload(client, auth_headers, b"keyed-video", key=key)
    assert first.status_code == 200, first.text
    video_path = first.json()["video_path"]
    assert (await db_session.execute(text("""
        SELECT k.idempotency_key
        FROM media_upload_idempotency_keys k
        JOIN media_upload_sessions s ON s.id = k.session_id
        WHERE s.video_path = :path
    """), {"path": video_path})).scalar_one() == key

    replay = await _upload(client, auth_headers, b"keyed-video", key=key)
    assert replay.status_code == 200
    assert replay.json()["video_path"] == video_path

    conflict = await _upload(client, auth_headers, b"other-video", key=key)
    assert conflict.status_code == 409
    assert list((tmp_path / "uploads").glob("*.mp4")) == [
        tmp_path / "uploads" / video_path.removeprefix("upload://"),
    ]


@pytest.mark.asyncio
async def test_task_retry_reuses_the_upload_until_options_change(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    app.state.config.temp_dir = str(tmp_path)

    async def _duration(*_args):
        return 12.0

    monkeypatch.setattr("src.api.routes.tasks._preflight_upload_source", _duration)
    uploaded = await _upload(client, auth_headers, b"task-source")
    assert uploaded.status_code == 200, uploaded.text
    video_path = uploaded.json()["video_path"]
    body = {"source": {"url": video_path}}
    queued = app.state.queue_adapter.enqueued_jobs
    before = len(queued)

    first = await client.post("/tasks/", headers=auth_headers, json=body)
    assert first.status_code == 200, first.text
    second = await client.post("/tasks/", headers=auth_headers, json=body)
    assert second.status_code == 200, second.text
    assert isinstance(first.json(), dict), first.text
    assert isinstance(second.json(), dict), second.text
    assert second.json()["task_id"] == first.json()["task_id"]
    assert second.json()["message"] == "Task already queued"

    added = [
        job for job in queued[before:]
        if job["function_name"] == "process_video_task"
    ]
    assert len(added) == 1

    changed = await client.post(
        "/tasks/",
        headers=auth_headers,
        json={"source": {"url": video_path}, "clip_length": "short"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["task_id"] != first.json()["task_id"]
    added = [
        job for job in queued[before:]
        if job["function_name"] == "process_video_task"
    ]
    assert len(added) == 2


@pytest.mark.asyncio
async def test_another_account_cannot_use_a_stored_upload(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    await create_user(db_session, user_id="user-2", email="user-2@example.com")
    app.state.config.temp_dir = str(tmp_path)
    uploaded = await _upload(client, auth_headers, b"private-video")
    assert uploaded.status_code == 200, uploaded.text

    async def _duration(*_args):
        raise AssertionError("a foreign upload must be rejected before probing")

    monkeypatch.setattr("src.api.routes.tasks._preflight_upload_source", _duration)
    response = await client.post(
        "/tasks/",
        headers=_headers_for("user-2"),
        json={"source": {"url": uploaded.json()["video_path"]}},
    )
    assert response.status_code == 404, response.text


@pytest.mark.asyncio
async def test_upload_ownership_outlives_session_cleanup(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    await create_user(db_session, user_id="user-2", email="user-2@example.com")
    app.state.config.temp_dir = str(tmp_path)

    async def _duration(*_args):
        return 12.0

    monkeypatch.setattr("src.api.routes.tasks._preflight_upload_source", _duration)
    uploaded = await _upload(client, auth_headers, b"owned-video")
    assert uploaded.status_code == 200, uploaded.text
    video_path = uploaded.json()["video_path"]
    source = await create_source(db_session, source_type="video_url", url=video_path)
    await create_task(db_session, user_id="user-1", source_id=source["id"])
    await db_session.execute(text("""
        UPDATE media_upload_sessions
        SET created_at = NOW() - INTERVAL '3 days'
        WHERE video_path = :path
    """), {"path": video_path})
    await db_session.commit()

    storage = MediaStorage(config=app.state.config)
    storage.bucket = None
    await cleanup_upload_sessions(db_session, storage)

    saved = tmp_path / "uploads" / video_path.removeprefix("upload://")
    assert saved.read_bytes() == b"owned-video"
    owner = (await db_session.execute(text(
        "SELECT user_id FROM media_upload_sessions WHERE video_path = :path AND state = 'completed'"
    ), {"path": video_path})).scalar_one()
    assert owner == "user-1"

    reused = await client.post("/tasks/", headers=auth_headers, json={"source": {"url": video_path}})
    assert reused.status_code == 200, reused.text
    foreign = await client.post(
        "/tasks/",
        headers=_headers_for("user-2"),
        json={"source": {"url": video_path}},
    )
    assert foreign.status_code == 404, foreign.text

    await db_session.execute(text(
        "DELETE FROM media_upload_sessions WHERE video_path = :path"
    ), {"path": video_path})
    await db_session.commit()
    # A new option does not match the task already queued above, so ownership
    # is checked again. The file is still on disk and has no owner row.
    fresh_options = {"source": {"url": video_path}, "clip_length": "short"}
    for headers in (auth_headers, _headers_for("user-2")):
        rejected = await client.post("/tasks/", headers=headers, json=fresh_options)
        assert rejected.status_code == 404, rejected.text


@pytest.mark.asyncio
async def test_local_cleanup_expires_orphaned_uploads_and_keeps_referenced_files(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    app.state.config.temp_dir = str(tmp_path)
    orphan = await _upload(client, auth_headers, b"orphan-video")
    kept = await _upload(client, auth_headers, b"kept-video")
    fresh = await _upload(client, auth_headers, b"fresh-video")
    assert orphan.status_code == kept.status_code == fresh.status_code == 200
    orphan_path = orphan.json()["video_path"]
    kept_path = kept.json()["video_path"]
    fresh_path = fresh.json()["video_path"]
    orphan_file = tmp_path / "uploads" / orphan_path.removeprefix("upload://")
    kept_file = tmp_path / "uploads" / kept_path.removeprefix("upload://")
    orphan_capped = orphan_file.with_name(f"{orphan_file.stem}.capped.mp4")
    kept_capped = kept_file.with_name(f"{kept_file.stem}.capped.mp4")
    orphan_capped.write_bytes(b"orphan-capped")
    kept_capped.write_bytes(b"kept-capped")
    source = await create_source(db_session, source_type="video_url", url=kept_path)
    await create_task(db_session, user_id="user-1", source_id=source["id"])
    await db_session.execute(text("""
        UPDATE media_upload_sessions
        SET created_at = NOW() - INTERVAL '3 days'
        WHERE video_path = :orphan OR video_path = :kept
    """), {"orphan": orphan_path, "kept": kept_path})
    await db_session.commit()

    storage = MediaStorage(config=app.state.config)
    storage.bucket = None
    await cleanup_upload_sessions(db_session, storage)

    assert not orphan_file.exists()
    assert not orphan_capped.exists()
    assert kept_file.read_bytes() == b"kept-video"
    assert kept_capped.read_bytes() == b"kept-capped"
    assert (tmp_path / "uploads" / fresh_path.removeprefix("upload://")).read_bytes() == b"fresh-video"
    remaining = {
        row.video_path
        for row in await db_session.execute(text(
            "SELECT video_path FROM media_upload_sessions WHERE user_id = 'user-1'"
        ))
    }
    assert remaining == {kept_path, fresh_path}

    def _refuse_delete(self, reference, *, prefix=False):
        raise OSError("disk busy")

    monkeypatch.setattr(MediaStorage, "delete", _refuse_delete)
    await db_session.execute(text("""
        UPDATE media_upload_sessions
        SET created_at = NOW() - INTERVAL '3 days'
        WHERE video_path = :path
    """), {"path": fresh_path})
    await db_session.commit()
    await cleanup_upload_sessions(db_session, storage)
    assert (tmp_path / "uploads" / fresh_path.removeprefix("upload://")).is_file()
    assert (await db_session.execute(text(
        "SELECT COUNT(*) FROM media_upload_sessions WHERE video_path = :path"
    ), {"path": fresh_path})).scalar_one() == 1


@pytest.mark.asyncio
async def test_content_hash_reuse_binds_a_new_idempotency_key(
    client, app, auth_headers, db_session, tmp_path,
):
    await create_user(db_session, user_id="user-1")
    app.state.config.temp_dir = str(tmp_path)
    payload = b"hashed-video"
    first = await _upload(client, auth_headers, payload)
    assert first.status_code == 200, first.text
    video_path = first.json()["video_path"]

    bound = await _upload(client, auth_headers, payload, key="client-retry-1")
    assert bound.status_code == 200, bound.text
    assert bound.json()["video_path"] == video_path
    assert (await db_session.execute(text(
        "SELECT idempotency_key FROM media_upload_sessions WHERE video_path = :path"
    ), {"path": video_path})).scalar_one() == "client-retry-1"

    replay = await _upload(client, auth_headers, payload)
    assert replay.status_code == 200
    assert replay.json()["video_path"] == video_path
    assert (await db_session.execute(text(
        "SELECT idempotency_key FROM media_upload_sessions WHERE video_path = :path"
    ), {"path": video_path})).scalar_one() == "client-retry-1"

    conflict = await _upload(client, auth_headers, b"other-bytes", key="client-retry-1")
    assert conflict.status_code == 409

    switched = await _upload(client, auth_headers, payload, key="client-retry-2")
    assert switched.status_code == 200, switched.text
    assert switched.json()["video_path"] == video_path
    assert (await db_session.execute(text(
        "SELECT idempotency_key FROM media_upload_sessions WHERE video_path = :path"
    ), {"path": video_path})).scalar_one() == "client-retry-1"
    bound_keys = (await db_session.execute(text("""
        SELECT k.idempotency_key
        FROM media_upload_idempotency_keys k
        JOIN media_upload_sessions s ON s.id = k.session_id
        WHERE s.video_path = :path
        ORDER BY k.idempotency_key
    """), {"path": video_path})).scalars().all()
    assert bound_keys == ["client-retry-1", "client-retry-2"]
    assert (await _upload(client, auth_headers, b"other-bytes", key="client-retry-1")).status_code == 409
    assert (await _upload(client, auth_headers, b"other-bytes", key="client-retry-2")).status_code == 409
    assert list((tmp_path / "uploads").glob("*.mp4")) == [
        tmp_path / "uploads" / video_path.removeprefix("upload://"),
    ]


@pytest.mark.asyncio
async def test_upload_task_dedupe_uses_completed_at(db_session):
    await create_user(db_session, user_id="user-1")
    source = await create_source(db_session, source_type="video_url", url="upload://clip.mp4")
    task = await create_task(db_session, user_id="user-1", source_id=source["id"], status="completed")
    key = "same-upload-request"
    await db_session.execute(text("""
        UPDATE tasks
        SET created_at = NOW() - INTERVAL '30 minutes',
            source_metadata = CAST(:meta AS jsonb)
        WHERE id = :id
    """), {"id": task["id"], "meta": json.dumps({"upload_request_key": key})})
    await db_session.commit()
    assert await TaskRepository.find_recent_upload_task(db_session, "user-1", key) is None

    await db_session.execute(text(
        "UPDATE tasks SET completed_at = NOW() WHERE id = :id"
    ), {"id": task["id"]})
    await db_session.commit()
    found = await TaskRepository.find_recent_upload_task(db_session, "user-1", key)
    assert found is not None and found.id == task["id"]

    await db_session.execute(text(
        "UPDATE tasks SET completed_at = NOW() - INTERVAL '30 minutes' WHERE id = :id"
    ), {"id": task["id"]})
    await db_session.commit()
    assert await TaskRepository.find_recent_upload_task(db_session, "user-1", key) is None

    await db_session.execute(text("""
        UPDATE tasks
        SET status = 'processing', completed_at = NULL
        WHERE id = :id
    """), {"id": task["id"]})
    await db_session.commit()
    found = await TaskRepository.find_recent_upload_task(db_session, "user-1", key)
    assert found is not None and found.id == task["id"]


@pytest.mark.asyncio
async def test_expiry_keeps_an_upload_claimed_while_cleanup_waits(
    app, db_session, tmp_path, monkeypatch, initialized_database,
):
    from src.repositories.media_repository import source_guard
    from src.workers import media_cleanup

    await create_user(db_session, user_id="user-1")
    app.state.config.temp_dir = str(tmp_path)
    video_path = "upload://claimed.mp4"
    saved = tmp_path / "uploads" / "claimed.mp4"
    saved.parent.mkdir()
    saved.write_bytes(b"claimed-video")
    await db_session.execute(text("""
        INSERT INTO media_upload_sessions (id, user_id, video_path, size, state, created_at)
        VALUES ('claimed-session', 'user-1', :path, 14, 'completed', NOW() - INTERVAL '3 days')
    """), {"path": video_path})
    await db_session.commit()

    waiting = asyncio.Event()

    @asynccontextmanager
    async def _wait_for_the_task_lock(db, url):
        waiting.set()
        async with source_guard(db, url):
            yield

    monkeypatch.setattr(media_cleanup, "source_guard", _wait_for_the_task_lock)
    storage = MediaStorage(config=app.state.config)
    storage.bucket = None
    sessions = async_sessionmaker(initialized_database, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as holder, sessions() as cleaner:
        async with source_guard(holder, video_path):
            job = asyncio.create_task(media_cleanup.cleanup_upload_sessions(cleaner, storage))
            await asyncio.wait_for(waiting.wait(), timeout=5)
            source = await create_source(db_session, source_type="video_url", url=video_path)
            await create_task(db_session, user_id="user-1", source_id=source["id"])
        await asyncio.wait_for(job, timeout=5)

    assert saved.read_bytes() == b"claimed-video"
    assert (await db_session.execute(text(
        "SELECT COUNT(*) FROM media_upload_sessions WHERE video_path = :path"
    ), {"path": video_path})).scalar_one() == 1
