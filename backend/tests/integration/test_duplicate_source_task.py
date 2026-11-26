"""A second submission of the same video reuses the in-progress task."""

import hashlib
import hmac
import time
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.api.routes import tasks
from src.repositories.task_repository import TaskRepository
from src.source_identity import source_key_for_url
from tests.fixtures.factories import create_source, create_task, create_user


def headers_for(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


async def _minutes(db, user_id: str) -> int:
    return (
        await db.execute(
            text("SELECT COUNT(*) FROM minute_usage_events WHERE user_id = :user"),
            {"user": user_id},
        )
    ).scalar_one()


async def _tasks(db, user_id: str) -> int:
    return (
        await db.execute(
            text("SELECT COUNT(*) FROM tasks WHERE user_id = :user"),
            {"user": user_id},
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_youtube_and_twitch_duplicates_do_not_enqueue_or_charge(
    client, app, db_session, monkeypatch
):
    async def info(_url, **_kwargs):
        return {"duration": 600, "title": "Source"}

    monkeypatch.setattr("src.api.routes.tasks.async_get_youtube_video_info", info)
    monkeypatch.setattr(
        "src.api.routes.tasks._preflight_stream_source",
        AsyncMock(return_value={"duration": 90, "title": "VOD", "provider": "direct"}),
    )
    app.state.config.monetization_enabled = True
    owner = str(uuid4())
    other = str(uuid4())
    await create_user(db_session, user_id=owner, plan="pro", subscription_status="active")
    await create_user(db_session, user_id=other, plan="pro", subscription_status="active")
    video = uuid4().hex[:11]
    watch = f"https://www.youtube.com/watch?v={video}"
    short = f"https://www.youtube.com/shorts/{video}"
    twitch = f"https://www.twitch.tv/videos/{str(uuid4().int)[:10]}"
    queue = app.state.queue_adapter
    before = len(queue.enqueued_jobs)

    first = await client.post(
        "/tasks/", headers=headers_for(owner), json={"source": {"url": watch}}
    )
    assert first.status_code == 200, first.text
    created = first.json()
    assert created.get("duplicate") is not True
    assert await _minutes(db_session, owner) == 1
    assert await _tasks(db_session, owner) == 1

    again = await client.post(
        "/tasks/",
        headers=headers_for(owner),
        json={"source": {"url": short}, "font_options": {"font_size": 30}},
    )
    assert again.status_code == 200, again.text
    body = again.json()
    assert body["duplicate"] is True
    assert body["code"] == "DUPLICATE_SOURCE"
    assert body["task_id"] == created["task_id"]
    assert body["message"] == "This video is already being processed."
    assert await _minutes(db_session, owner) == 1
    assert await _tasks(db_session, owner) == 1

    await db_session.execute(
        text("UPDATE tasks SET status = 'processing' WHERE id = :id"),
        {"id": created["task_id"]},
    )
    await db_session.commit()
    still = await client.post(
        "/tasks/", headers=headers_for(owner), json={"source": {"url": f"https://youtu.be/{video}"}}
    )
    assert still.status_code == 200
    assert still.json()["task_id"] == created["task_id"]
    assert still.json()["duplicate"] is True

    # Another account can process the same video.
    theirs = await client.post(
        "/tasks/", headers=headers_for(other), json={"source": {"url": watch}}
    )
    assert theirs.status_code == 200, theirs.text
    assert theirs.json()["task_id"] != created["task_id"]
    assert theirs.json().get("duplicate") is not True
    assert await _minutes(db_session, other) == 1

    await db_session.execute(
        text("UPDATE tasks SET status = 'completed' WHERE id = :id"),
        {"id": created["task_id"]},
    )
    await db_session.commit()
    fresh = await client.post(
        "/tasks/", headers=headers_for(owner), json={"source": {"url": watch}}
    )
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["task_id"] != created["task_id"]
    assert await _minutes(db_session, owner) == 2
    assert await _tasks(db_session, owner) == 2

    vod = await client.post(
        "/tasks/", headers=headers_for(owner), json={"source": {"url": twitch}}
    )
    assert vod.status_code == 200, vod.text
    vod_id = vod.json()["task_id"]
    repeat = await client.post(
        "/tasks/",
        headers=headers_for(owner),
        json={"source": {"url": twitch.replace("https://www.", "https://") + "?t=30s"}},
    )
    assert repeat.status_code == 200, repeat.text
    assert repeat.json()["task_id"] == vod_id
    assert repeat.json()["duplicate"] is True
    # The duplicate added no job. Creates: first, other, fresh, vod.
    assert len(queue.enqueued_jobs) == before + 4

    stored = (
        await db_session.execute(
            text("SELECT source_metadata->>'source_key' FROM tasks WHERE id = :id"),
            {"id": created["task_id"]},
        )
    ).scalar_one()
    assert stored == f"youtube:{video}"


@pytest.mark.asyncio
async def test_same_upload_bytes_reuse_the_in_progress_task(client, app, db_session):
    app.state.config.monetization_enabled = True
    owner = str(uuid4())
    await create_user(db_session, user_id=owner, plan="pro", subscription_status="active")
    digest = uuid4().hex
    source = await create_source(db_session, source_type="video_url", url="upload://videos/first.mp4")
    task = await create_task(db_session, user_id=owner, source_id=source["id"], status="queued")
    await db_session.execute(
        text(
            """
            UPDATE tasks
            SET source_metadata = CAST(:metadata AS jsonb)
            WHERE id = :id
            """
        ),
        {"id": task["id"], "metadata": f'{{"source_key": "upload:sha256:{digest}"}}'},
    )
    path = f"upload://videos/{uuid4().hex}.mp4"
    await db_session.execute(
        text(
            """
            INSERT INTO media_upload_sessions (
                id, user_id, upload_id, video_path, size, state, content_sha256
            ) VALUES (
                :id, :user, :upload, :path, 100, 'completed', :digest
            )
            """
        ),
        {
            "id": str(uuid4()),
            "user": owner,
            "upload": str(uuid4()),
            "path": path,
            "digest": digest,
        },
    )
    await db_session.commit()
    queue = app.state.queue_adapter
    before = len(queue.enqueued_jobs)
    minutes = await _minutes(db_session, owner)

    response = await client.post(
        "/tasks/", headers=headers_for(owner), json={"source": {"url": path}}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["task_id"] == task["id"]
    assert body["duplicate"] is True
    assert await _minutes(db_session, owner) == minutes
    assert await _tasks(db_session, owner) == 1
    assert len(queue.enqueued_jobs) == before


@pytest.mark.parametrize("change", [
    {"output_format": "vertical_split"},
    {"output_format": "vertical_fit"},
    {"output_format": "original"},
])
async def test_same_source_with_different_layout_creates_a_task(
    client, db_session, monkeypatch, change,
):
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 600}))
    # Dedupe must already work before the post-insert metadata and enqueue steps run.
    monkeypatch.setattr(tasks, "_save_task_source_metadata", AsyncMock())
    owner = str(uuid4())
    await create_user(db_session, user_id=owner)
    headers = headers_for(owner)
    base = {"source": {"url": "https://youtu.be/abcdefghijk"}}
    original = await client.post("/tasks/", headers=headers, json=base)
    assert original.status_code == 200, original.text

    changed = await client.post("/tasks/", headers=headers, json={**base, **change})
    assert changed.status_code == 200, changed.text
    assert changed.json()["task_id"] != original.json()["task_id"]
    assert changed.json().get("duplicate") is not True

    identical = await client.post("/tasks/", headers=headers, json={**base, **change})
    assert identical.status_code == 200, identical.text
    assert identical.json()["task_id"] == changed.json()["task_id"]
    assert identical.json()["duplicate"] is True
    assert await _tasks(db_session, owner) == 2


@pytest.mark.parametrize("status", ["queued", "processing"])
async def test_create_immediately_after_cancellation_does_not_reuse_task(
    client, db_session, monkeypatch, status,
):
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 600}))
    owner = str(uuid4())
    await create_user(db_session, user_id=owner)
    headers = headers_for(owner)
    body = {"source": {"url": "https://youtu.be/abcdefghijk"}, "output_format": "vertical_split"}
    first = await client.post("/tasks/", headers=headers, json=body)
    assert first.status_code == 200, first.text
    task_id = first.json()["task_id"]
    await db_session.execute(text("UPDATE tasks SET status = :status WHERE id = :id"), {"status": status, "id": task_id})
    await db_session.commit()

    cancelled = await client.post(f"/tasks/{task_id}/cancel", headers=headers)
    assert cancelled.status_code == 200, cancelled.text
    saved_status = await db_session.scalar(text("SELECT status FROM tasks WHERE id = :id"), {"id": task_id})
    assert saved_status == "cancelled"

    second = await client.post("/tasks/", headers=headers, json=body)
    assert second.status_code == 200, second.text
    assert second.json()["task_id"] != task_id
    assert second.json().get("duplicate") is not True


@pytest.mark.parametrize("styled", [False, True])
async def test_output_format_matches_legacy_source_url_and_default_settings(db_session, styled):
    owner = str(uuid4())
    await create_user(db_session, user_id=owner)
    url = "https://youtu.be/abcdefghijk"
    source = await create_source(db_session, url=url)
    style = {"intensity": "bold"} if styled else None
    task_id = await TaskRepository.create_task(
        db_session, owner, source["id"], status="processing", source_duration_seconds=600, edit_style=style,
    )
    key = await source_key_for_url(db_session, owner, url)
    edit_identity = (style, "default") if style else None

    for fmt in [None, "vertical"]:
        task = await TaskRepository.find_active_source_task(
            db_session, owner, key, output_format=fmt, edit_identity=edit_identity,
        )
        assert task is not None and task.id == task_id
    assert await TaskRepository.find_active_source_task(
        db_session, owner, key, output_format="vertical_split",
        edit_identity=edit_identity,
    ) is None
