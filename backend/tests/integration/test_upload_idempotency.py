"""Retries of one upload reuse the stored file and the in-progress task."""

import hashlib
import hmac
import time

import pytest
from sqlalchemy import text

from src.media_storage import MediaStorage
from tests.fixtures.factories import create_user


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

    async def _duration(_url, _user_id, _limit):
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
