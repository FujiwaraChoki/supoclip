"""Idempotency-Key on task creation: one key, one task, one charge."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.routes import tasks
from src.repositories.task_repository import TaskRepository
from src.services.billing_service import BillingService
from tests.fixtures.factories import create_user
from tests.integration.test_generation_allowance_race import wait_for_admission_waiter
from tests.integration.test_upload_idempotency import _headers_for, _upload


async def _uploaded_source(client, app, headers, tmp_path, monkeypatch, payload=b"keyed-source"):
    app.state.config.temp_dir = str(tmp_path)

    async def _duration(*_args):
        return 12.0

    monkeypatch.setattr("src.api.routes.tasks._preflight_upload_source", _duration)
    uploaded = await _upload(client, headers, payload)
    assert uploaded.status_code == 200, uploaded.text
    return uploaded.json()["video_path"]


async def _create(client, headers, body, key):
    return await client.post("/tasks/", headers={**headers, "Idempotency-Key": key}, json=body)


async def _task_count(db, user_id):
    return (await db.execute(
        text("SELECT count(*) FROM tasks WHERE user_id = :user"), {"user": user_id}
    )).scalar_one()


def _processing_jobs(app, before):
    return [job for job in app.state.queue_adapter.enqueued_jobs[before:] if job["function_name"] == "process_video_task"]


@pytest.mark.asyncio
async def test_late_retry_returns_the_task_long_after_the_short_dedupe_window(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    url = await _uploaded_source(client, app, auth_headers, tmp_path, monkeypatch)
    body = {"source": {"url": url}}
    before = len(app.state.queue_adapter.enqueued_jobs)

    first = await _create(client, auth_headers, body, "ios-key-0001")
    assert first.status_code == 200, first.text
    task_id = first.json()["task_id"]

    # The task finished long ago: outside the old 2-minute window, inside retention.
    await db_session.execute(text("""
        UPDATE tasks SET status = 'completed', completed_at = NOW() - INTERVAL '20 minutes' WHERE id = :id
    """), {"id": task_id})
    await db_session.execute(text("""
        UPDATE task_idempotency_keys SET created_at = NOW() - INTERVAL '23 hours'
        WHERE user_id = 'user-1' AND idempotency_key = 'ios-key-0001'
    """))
    await db_session.commit()

    retry = await _create(client, auth_headers, body, "ios-key-0001")
    assert retry.status_code == 200, retry.text
    assert retry.json()["task_id"] == task_id
    assert retry.json()["message"] == "Task already queued"
    assert retry.json()["job_id"] == first.json()["job_id"]
    assert await _task_count(db_session, "user-1") == 1
    assert len(_processing_jobs(app, before)) == 1

    # Without the key, the same request after the window is a new task, as before.
    unkeyed = await client.post("/tasks/", headers=auth_headers, json=body)
    assert unkeyed.status_code == 200, unkeyed.text
    assert unkeyed.json()["task_id"] != task_id
    # A new key is a new request.
    fresh = await _create(client, auth_headers, body, "ios-key-0002")
    assert fresh.status_code == 200, fresh.text
    assert fresh.json()["task_id"] not in {task_id, unkeyed.json()["task_id"]}
    assert await _task_count(db_session, "user-1") == 3


@pytest.mark.asyncio
async def test_expired_key_starts_a_new_task_and_rebinds(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    url = await _uploaded_source(client, app, auth_headers, tmp_path, monkeypatch)
    body = {"source": {"url": url}}
    first = await _create(client, auth_headers, body, "expiring-key")
    await db_session.execute(text(
        "UPDATE tasks SET status = 'completed', completed_at = NOW() - INTERVAL '2 days' WHERE user_id = 'user-1'"
    ))
    await db_session.execute(text(
        "UPDATE task_idempotency_keys SET created_at = NOW() - INTERVAL '25 hours' WHERE user_id = 'user-1'"
    ))
    await db_session.commit()

    again = await _create(client, auth_headers, body, "expiring-key")
    assert again.status_code == 200, again.text
    assert again.json()["task_id"] != first.json()["task_id"]
    bound = (await db_session.execute(text(
        "SELECT task_id FROM task_idempotency_keys WHERE user_id = 'user-1' AND idempotency_key = 'expiring-key'"
    ))).scalar_one()
    assert bound == again.json()["task_id"]


@pytest.mark.asyncio
async def test_key_reused_for_a_different_request_is_rejected(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    url = await _uploaded_source(client, app, auth_headers, tmp_path, monkeypatch)
    first = await _create(client, auth_headers, {"source": {"url": url}}, "reused-key-1")
    assert first.status_code == 200, first.text

    for changed in (
        {"source": {"url": url}, "clip_length": "short"},
        {"source": {"url": url, "title": "Another title"}},
    ):
        conflict = await _create(client, auth_headers, changed, "reused-key-1")
        assert conflict.status_code == 409, conflict.text
        assert conflict.json()["detail"]["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert await _task_count(db_session, "user-1") == 1

    # Spelling that normalizes to the same options is the same request.
    same = await _create(client, auth_headers, {"source": {"url": url}, "clip_length": "auto"}, "reused-key-1")
    assert same.status_code == 200, same.text
    assert same.json()["task_id"] == first.json()["task_id"]


@pytest.mark.asyncio
async def test_keys_belong_to_one_user(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    await create_user(db_session, user_id="user-2", email="user-2@example.com")
    own = await _uploaded_source(client, app, auth_headers, tmp_path, monkeypatch, b"one")
    other_headers = _headers_for("user-2")
    other = (await _upload(client, other_headers, b"two")).json()["video_path"]

    first = await _create(client, auth_headers, {"source": {"url": own}}, "shared-key-1")
    second = await _create(client, other_headers, {"source": {"url": other}}, "shared-key-1")

    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert first.json()["task_id"] != second.json()["task_id"]
    assert await _task_count(db_session, "user-2") == 1


@pytest.mark.asyncio
async def test_deleting_the_task_does_not_free_its_key(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    url = await _uploaded_source(client, app, auth_headers, tmp_path, monkeypatch)
    body = {"source": {"url": url}}
    first = await _create(client, auth_headers, body, "deleted-key-1")
    await db_session.execute(text("DELETE FROM tasks WHERE id = :id"), {"id": first.json()["task_id"]})
    await db_session.commit()

    retry = await _create(client, auth_headers, body, "deleted-key-1")

    # The original answer, not a second task (charges survive deletion).
    assert retry.status_code == 200, retry.text
    assert retry.json()["task_id"] == first.json()["task_id"]
    assert await _task_count(db_session, "user-1") == 0


@pytest.mark.asyncio
async def test_keyed_link_requests_replay_too(client, app, auth_headers, db_session, monkeypatch):
    await create_user(db_session, user_id="user-1")

    async def _preflight(_url, _max):
        return {"title": "Talk", "duration": 300}

    monkeypatch.setattr(tasks, "_preflight_youtube_source", _preflight)
    body = {"source": {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}}
    first = await _create(client, auth_headers, body, "link-key-0001")
    await db_session.execute(text(
        "UPDATE tasks SET status = 'completed', completed_at = NOW() - INTERVAL '1 hour' WHERE user_id = 'user-1'"
    ))
    await db_session.commit()
    retry = await _create(client, auth_headers, body, "link-key-0001")

    assert first.status_code == 200 and retry.status_code == 200, (first.text, retry.text)
    assert retry.json()["task_id"] == first.json()["task_id"]
    assert await _task_count(db_session, "user-1") == 1


@pytest.mark.asyncio
async def test_concurrent_requests_with_one_key_make_one_task(client, app, auth_headers, db_session, monkeypatch):
    """Self-hosted link creates aren't serialized: the key's primary key decides."""
    await create_user(db_session, user_id="user-1")

    async def _preflight(_url, _max):
        return {"title": "Talk", "duration": 300}

    monkeypatch.setattr(tasks, "_preflight_youtube_source", _preflight)
    before = len(app.state.queue_adapter.enqueued_jobs)
    original = TaskRepository.create_task
    barrier = asyncio.Barrier(2)

    async def both_past_every_lookup(*args, **kwargs):
        # Both requests have checked for the key; only the insert can decide.
        await asyncio.wait_for(barrier.wait(), 5)
        return await original(*args, **kwargs)

    monkeypatch.setattr(TaskRepository, "create_task", staticmethod(both_past_every_lookup))
    url = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    orphan_sql = text("""
        SELECT count(*) FROM sources s WHERE s.url = :url
          AND NOT EXISTS (SELECT 1 FROM tasks t WHERE t.source_id = s.id)
    """)
    orphans_before = (await db_session.execute(orphan_sql, {"url": url})).scalar_one()
    body = {"source": {"url": url}}
    first, second = await asyncio.gather(
        _create(client, auth_headers, body, "racing-key-1"),
        _create(client, auth_headers, body, "racing-key-1"),
    )

    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert first.json()["task_id"] == second.json()["task_id"]
    assert sorted([first.json()["message"], second.json()["message"]]) == [
        "Task already queued", "Task created and queued for processing",
    ]
    assert await _task_count(db_session, "user-1") == 1
    assert len(_processing_jobs(app, before)) == 1
    keys = (await db_session.execute(text(
        "SELECT count(*) FROM task_idempotency_keys WHERE user_id = 'user-1'"
    ))).scalar_one()
    assert keys == 1
    # The loser's source row went with its rolled-back task.
    orphans = (await db_session.execute(orphan_sql, {"url": url})).scalar_one()
    assert orphans == orphans_before


@pytest.mark.asyncio
async def test_concurrent_keyed_upload_creates_make_one_task(
    client, app, auth_headers, db_session, tmp_path, monkeypatch,
):
    await create_user(db_session, user_id="user-1")
    url = await _uploaded_source(client, app, auth_headers, tmp_path, monkeypatch)
    before = len(app.state.queue_adapter.enqueued_jobs)
    barrier = asyncio.Barrier(2)

    async def _duration(*_args):
        # Both requests are past the fast-path key check.
        await asyncio.wait_for(barrier.wait(), 5)
        return 12.0

    monkeypatch.setattr("src.api.routes.tasks._preflight_upload_source", _duration)
    body = {"source": {"url": url}}
    first, second = await asyncio.gather(
        _create(client, auth_headers, body, "upload-race-1"),
        _create(client, auth_headers, body, "upload-race-1"),
    )

    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert first.json()["task_id"] == second.json()["task_id"]
    assert await _task_count(db_session, "user-1") == 1
    assert len(_processing_jobs(app, before)) == 1


@pytest.mark.asyncio
async def test_retry_waiting_on_a_paid_admission_gets_the_task_not_a_limit_error(
    initialized_database, app, db_session, monkeypatch,
):
    """The original uses the last minute; its retry must not be told to upgrade."""
    user_id = str(uuid4())
    app.state.config.monetization_enabled = True
    app.state.config.pro_plan_minute_limit = 1
    await create_user(db_session, user_id=user_id, plan="pro", subscription_status="active")
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value=user_id))
    monkeypatch.setattr(tasks.VideoService, "probe_upload_duration", lambda _url, _user: 60.0)
    before = len(app.state.queue_adapter.enqueued_jobs)

    def keyed_request():
        return SimpleNamespace(
            json=AsyncMock(return_value={"source": {"url": "upload://paid.mp4"}}),
            headers={"idempotency-key": "paid-key-0001"},
            app=app,
        )

    checked = asyncio.Event()
    release = asyncio.Event()
    original = BillingService.assert_can_create_task
    calls = 0

    async def pause_inside_first_admission(self, user_id, duration=None):
        nonlocal calls
        summary = await original(self, user_id, duration)
        calls += 1
        if calls == 2:  # the first request's check under the admission lock
            checked.set()
            await release.wait()
        return summary

    monkeypatch.setattr(BillingService, "assert_can_create_task", pause_inside_first_admission)
    sessions = async_sessionmaker(initialized_database, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as first_db, sessions() as second_db:
        first = asyncio.create_task(tasks.create_task(keyed_request(), first_db))
        await asyncio.wait_for(checked.wait(), 5)
        second = asyncio.create_task(tasks.create_task(keyed_request(), second_db))
        try:
            await wait_for_admission_waiter(initialized_database)
            assert not second.done()
        finally:
            release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)

    assert all(isinstance(result, dict) for result in results), results
    assert results[0]["task_id"] == results[1]["task_id"]
    assert results[1]["message"] == "Task already queued"
    assert len(_processing_jobs(app, before)) == 1
    summary = await BillingService(db_session, app.state.config).get_usage_summary(user_id)
    assert summary["usage_count"] == 1
