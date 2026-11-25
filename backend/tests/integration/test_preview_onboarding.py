"""The free preview clip and the onboarding routes (docs/onboarding.md)."""

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.onboarding import variant as variant_module
from tests.fixtures.factories import create_user
from tests.integration.test_clip_edit_atomicity import make_task
from tests.integration.test_processing_cancellation import setup_worker

YOUTUBE = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


# Some tests here commit through real sessions (AsyncSessionLocal), so the per-test rollback
# does not undo them. Remove what they leave for the shared test user, or later billing tests
# see minutes this file used.
_COMMITTED_BY_PREVIEWS = ("minute_usage_events", "rejected_source_attempts", "tasks")


async def _forget_test_user(engine):
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for table in _COMMITTED_BY_PREVIEWS:
            await session.execute(text(f"DELETE FROM {table} WHERE user_id = 'user-1'"))
        await session.commit()


@pytest.fixture
async def hosted(app, feature_flag, monkeypatch, initialized_database):
    """Hosted billing on, previews switched on, and a YouTube preflight that never calls out."""
    await feature_flag("pebble_onboarding", web_enabled=True, ios_enabled=True)
    app.state.config.monetization_enabled = True
    app.state.config.self_host = False
    lengths = {"seconds": 30 * 60}

    async def preflight(url, limit):
        if limit and lengths["seconds"] > limit:
            raise HTTPException(422, {"code": "VIDEO_TOO_LONG", "message": "too long"})
        return {"title": "How we grew a podcast", "duration": lengths["seconds"]}

    monkeypatch.setattr("src.api.routes.tasks._preflight_youtube_source", preflight)
    monkeypatch.setattr("src.api.routes.onboarding._preflight_youtube_source", preflight)
    try:
        yield lengths
    finally:
        app.state.config.monetization_enabled = False
        app.state.config.self_host = True
        await _forget_test_user(initialized_database)


async def _preview(client, headers, url=YOUTUBE):
    return await client.post("/tasks/", headers=headers, json={"source": {"url": url}, "preview": True})


@pytest.mark.asyncio
async def test_a_new_account_gets_one_free_preview_without_a_charge(client, app, auth_headers, db_session, hosted):
    await create_user(db_session, user_id="user-1", email_verified=True)

    created = await _preview(client, auth_headers)
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    row = (await db_session.execute(text("SELECT preview FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    assert row.preview == {"status": "pending"}
    charged = (await db_session.execute(text("SELECT COUNT(*) FROM minute_usage_events WHERE task_id = :id"), {"id": task_id})).scalar()
    assert charged == 0

    # One at a time, and one per account once it has finished.
    again = await _preview(client, auth_headers, "https://www.youtube.com/watch?v=aaaaaaaaaaa")
    assert again.status_code == 403 and again.json()["detail"]["reason"] == "running"
    await db_session.execute(text("UPDATE tasks SET status = 'completed' WHERE id = :id"), {"id": task_id})
    await db_session.execute(text("UPDATE users SET preview_used_at = NOW() WHERE id = 'user-1'"))
    await db_session.commit()
    used = await _preview(client, auth_headers, "https://www.youtube.com/watch?v=aaaaaaaaaaa")
    assert used.status_code == 403 and used.json()["detail"]["reason"] == "used"

    # Without "preview", a free account is still asked to pick a plan.
    normal = await client.post("/tasks/", headers=auth_headers, json={"source": {"url": YOUTUBE}})
    assert normal.status_code == 402


@pytest.mark.asyncio
async def test_previews_have_limits(client, app, auth_headers, db_session, hosted, monkeypatch):
    await create_user(db_session, user_id="user-1", email_verified=False)
    unverified = await _preview(client, auth_headers)
    assert unverified.status_code == 403 and unverified.json()["detail"]["reason"] == "unverified"

    await create_user(db_session, user_id="user-1", email_verified=True)
    hosted["seconds"] = 3 * 3600
    too_long = await _preview(client, auth_headers)
    assert too_long.status_code == 422

    hosted["seconds"] = 20 * 60
    workflow = await client.post(
        "/tasks/", headers=auth_headers, json={"source": {"url": YOUTUBE}, "preview": True, "workflow": "trailer"}
    )
    assert workflow.status_code == 422

    monkeypatch.setenv("PREVIEW_DAILY_LIMIT", "1")
    other = await create_user(db_session, email_verified=True)
    await db_session.execute(
        text("INSERT INTO tasks (id, user_id, status, preview) VALUES (:id, :u, 'completed', '{}'::jsonb)"),
        {"id": str(uuid4()), "u": other["id"]},
    )
    await db_session.commit()
    busy = await _preview(client, auth_headers)
    assert busy.status_code == 403 and busy.json()["detail"]["reason"] == "busy"

    await create_user(db_session, user_id="user-1", plan="pro", subscription_status="active", email_verified=True)
    paid = await _preview(client, auth_headers)
    assert paid.status_code == 403 and paid.json()["detail"]["reason"] == "not_needed"


async def _preview_task(db_session, tmp_path, *, status="completed", clips=1):
    task_id, ids, _ = await make_task(db_session, tmp_path, count=clips, status=status)
    await create_user(db_session, user_id="user-1", email_verified=True)
    await db_session.execute(
        text("UPDATE tasks SET user_id = 'user-1', source_duration_seconds = 600, preview = CAST(:p AS JSONB) WHERE id = :id"),
        {"id": task_id, "p": json.dumps({"status": "pending", "found": 4, "rendered_position": 2, "locked": []})},
    )
    await db_session.commit()
    return task_id, ids


@pytest.mark.asyncio
async def test_a_locked_preview_can_be_kept_but_not_edited(client, auth_headers, db_session, hosted, tmp_path):
    task_id, ids = await _preview_task(db_session, tmp_path)
    trim = await client.patch(f"/tasks/{task_id}/clips/{ids[0]}", headers=auth_headers, json={"start_offset": 1})
    assert trim.status_code == 402 and trim.json()["detail"]["code"] == "PREVIEW_LOCKED"
    readable = await client.get(f"/tasks/{task_id}", headers=auth_headers)
    assert readable.status_code == 200 and readable.json()["preview"]["found"] == 4
    deleted = await client.delete(f"/tasks/{task_id}", headers=auth_headers)
    assert deleted.status_code in (200, 204), deleted.text


@pytest.mark.asyncio
async def test_unlocking_charges_the_video_and_renders_the_rest_in_place(
    client, auth_headers, db_session, hosted, tmp_path, monkeypatch
):
    task_id, ids = await _preview_task(db_session, tmp_path)
    enqueue = AsyncMock(return_value="unlock-job")
    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", enqueue)

    refused = await client.post(f"/tasks/{task_id}/unlock", headers=auth_headers)
    assert refused.status_code == 402

    # (Updated in place: recreating the user would delete its tasks.)
    await db_session.execute(text("UPDATE users SET plan = 'pro', subscription_status = 'active' WHERE id = 'user-1'"))
    await db_session.commit()
    unlocked = await client.post(f"/tasks/{task_id}/unlock", headers=auth_headers)
    assert unlocked.status_code == 200, unlocked.text
    assert unlocked.json()["job_id"] == "unlock-job"
    row = (await db_session.execute(text("SELECT status, preview FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    assert row.status == "queued" and row.preview["unlocked_at"]
    charged = (await db_session.execute(text("SELECT COUNT(*) FROM minute_usage_events WHERE task_id = :id"), {"id": task_id})).scalar()
    assert charged == 1
    # Pro is 720p, like the preview: its clip is kept and the rest are added.
    kept = (await db_session.execute(text("SELECT COUNT(*) FROM generated_clips WHERE task_id = :id"), {"id": task_id})).scalar()
    assert kept == 1
    # Once unlocked it's an ordinary project.
    trim = await client.patch(f"/tasks/{task_id}/clips/{ids[0]}", headers=auth_headers, json={"start_offset": 1})
    assert trim.status_code != 402


@pytest.mark.asyncio
async def test_a_preview_renders_only_its_best_clip(db_session, initialized_database, tmp_path):
    task_id, _, original = await make_task(db_session, tmp_path, count=0, status="queued")
    owner = (await db_session.execute(text("SELECT user_id FROM tasks WHERE id = :id"), {"id": task_id})).scalar()
    await db_session.execute(text("UPDATE tasks SET preview = '{\"status\": \"pending\"}'::jsonb WHERE id = :id"), {"id": task_id})
    await db_session.commit()
    segments = [
        {"start_time": "00:10", "end_time": "00:40", "virality_score": 61, "hook_title": "The slow start"},
        {"start_time": "01:00", "end_time": "01:45", "virality_score": 92, "hook_title": "The big reveal"},
        {"start_time": "03:00", "end_time": "03:30", "virality_score": 77, "hook_title": "A hard lesson"},
    ]
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    async with sessions() as worker:
        service, _, _ = setup_worker(worker, tmp_path, original)
        service.video_service.process_video_complete = AsyncMock(return_value={
            "video_path": str(original), "segments_to_render": segments, "segments": segments,
        })
        await service.process_task(task_id, "upload://input.mp4", "upload", user_id=owner)
        assert service.video_service.create_single_clip.await_count == 1

    row = (await db_session.execute(text("SELECT status, preview FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    assert row.status == "completed"
    assert row.preview["found"] == 3 and row.preview["rendered_position"] == 2
    assert [m["hook_title"] for m in row.preview["locked"]] == ["The slow start", "A hard lesson"]
    assert row.preview["locked"][0]["duration"] == 30.0
    used = (await db_session.execute(text("SELECT preview_used_at FROM users WHERE id = :id"), {"id": owner})).scalar()
    assert used is not None


@pytest.mark.asyncio
async def test_new_accounts_get_the_guided_onboarding(client, auth_headers, db_session, hosted, monkeypatch):
    await create_user(db_session, user_id="user-1", email_verified=True)
    await db_session.execute(
        text('UPDATE users SET "createdAt" = :t WHERE id = \'user-1\''),
        {"t": variant_module.GUIDED_SINCE - timedelta(days=1)},
    )
    await db_session.commit()
    old = (await client.get("/onboarding/status", headers=auth_headers)).json()
    assert old["variant"] is None

    await db_session.execute(
        text('UPDATE users SET "createdAt" = :t WHERE id = \'user-1\''),
        {"t": variant_module.GUIDED_SINCE + timedelta(minutes=1)},
    )
    await db_session.commit()
    events = []
    monkeypatch.setattr(variant_module, "capture_event", lambda *args, **kw: events.append((args, kw)))
    ios = {**auth_headers, "x-supoclip-client": "ios"}
    status = (await client.get("/onboarding/status", headers=ios)).json()
    assert status["variant"] == "pebble"
    # Sent once, saying which app the account was first opened in. Not as `platform`: the PostHog
    # client's super property of that name would overwrite it.
    (args, kw), = events
    assert args == ("onboarding_assigned", "user-1", {"client": "ios"}) and kw == {}
    assert status["preview"]["eligible"] is True and status["preview"]["task"] is None
    assert (await client.get("/onboarding/status", headers=auth_headers)).json()["variant"] == "pebble"
    assert len(events) == 1

    assert (await client.post("/onboarding/answers", headers=auth_headers, json={"makes": "podcasts"})).status_code == 200
    assert (await client.post("/onboarding/answers", headers=auth_headers, json={"makes": "cats"})).status_code == 422
    assert (await client.post("/onboarding/complete", headers=auth_headers)).status_code == 200
    status = (await client.get("/onboarding/status", headers=auth_headers)).json()
    assert status["answers"] == {"makes": "podcasts"} and status["completed"] is True


@pytest.mark.asyncio
async def test_accounts_from_the_ab_test_control_group_get_the_standard_home(client, auth_headers, db_session, hosted, monkeypatch):
    await create_user(db_session, user_id="user-1", email_verified=True)
    await db_session.execute(text("UPDATE users SET onboarding_variant = 'classic' WHERE id = 'user-1'"))
    await db_session.commit()
    events = []
    monkeypatch.setattr(variant_module, "capture_event", lambda *args, **kw: events.append((args, kw)))
    assert (await client.get("/onboarding/status", headers=auth_headers)).json()["variant"] is None
    assert events == []


@pytest.mark.asyncio
async def test_a_link_is_looked_at_before_anything_is_made(client, auth_headers, db_session, hosted, monkeypatch):
    await create_user(db_session, user_id="user-1", email_verified=True)
    monkeypatch.setattr("src.api.routes.onboarding._count_link_check", AsyncMock(return_value=1))
    looked = await client.post("/onboarding/link", headers=auth_headers, json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert looked.status_code == 200, looked.text
    body = looked.json()
    assert body["title"] == "How we grew a podcast" and body["duration_seconds"] == 1800
    assert body["thumbnail_url"].endswith("/dQw4w9WgXcQ/hqdefault.jpg")
    assert body["estimated_clips"] >= 1 and body["preview"]["fits"] is True
    bad = await client.post("/onboarding/link", headers=auth_headers, json={"url": "https://example.com/a.mp4"})
    assert bad.status_code == 422
    tasks = (await db_session.execute(text("SELECT COUNT(*) FROM tasks WHERE user_id = 'user-1'"))).scalar()
    assert tasks == 0


@pytest.mark.asyncio
async def test_a_paid_preview_unlocks_in_the_background_after_checkout(
    client, auth_headers, db_session, hosted, tmp_path, monkeypatch, initialized_database
):
    """Asked before checkout, then the tab closed: the cron unlocks it once the plan is active."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.workers import tasks as worker_tasks

    task_id, _ = await _preview_task(db_session, tmp_path)
    enqueue = AsyncMock(return_value="bg-unlock-job")
    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", enqueue)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)

    asked = await client.post(f"/tasks/{task_id}/unlock", headers=auth_headers, json={"when_paid": True})
    assert asked.status_code == 200 and asked.json()["pending"] is True
    row = (await db_session.execute(text("SELECT preview FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    assert row.preview["unlock_requested_at"] and "unlocked_at" not in row.preview

    # No plan yet: nothing happens.
    assert (await worker_tasks.unlock_paid_previews({}))["unlocked"] == 0
    enqueue.assert_not_awaited()

    await db_session.execute(text("UPDATE users SET plan = 'pro', subscription_status = 'active' WHERE id = 'user-1'"))
    await db_session.commit()
    assert (await worker_tasks.unlock_paid_previews({}))["unlocked"] == 1
    enqueue.assert_awaited_once()
    db_session.expire_all()
    row = (await db_session.execute(text("SELECT status, preview FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    assert row.status == "queued" and row.preview["unlocked_at"]
    # Done once: the next tick has nothing to do.
    assert (await worker_tasks.unlock_paid_previews({}))["checked"] == 0


@pytest.mark.asyncio
async def test_two_previews_at_once_make_only_one(client, auth_headers, db_session, hosted, monkeypatch):
    """The second request passed the first check before the other one's task existed; the check under
    the account's lock stops it."""
    await create_user(db_session, user_id="user-1", email_verified=True)
    await db_session.execute(
        text("INSERT INTO tasks (id, user_id, status, preview) VALUES (:id, 'user-1', 'queued', '{\"status\": \"pending\"}'::jsonb)"),
        {"id": str(uuid4())},
    )
    await db_session.commit()
    from src.onboarding.preview import preview_eligibility as real

    calls = []

    async def passes_before_the_lock(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {"eligible": True, "reason": None, "max_minutes": 90}
        return await real(*args, **kwargs)

    monkeypatch.setattr("src.api.routes.tasks.preview_eligibility", passes_before_the_lock)
    second = await _preview(client, auth_headers, "https://www.youtube.com/watch?v=aaaaaaaaaaa")
    assert second.status_code == 403, second.text
    assert second.json()["detail"]["reason"] == "running" and len(calls) == 2
    made = (await db_session.execute(text("SELECT COUNT(*) FROM tasks WHERE user_id = 'user-1'"))).scalar()
    assert made == 1


@pytest.mark.asyncio
async def test_a_preview_is_used_up_as_soon_as_its_clip_is_saved(db_session, initialized_database, tmp_path):
    """Cancelling after the clip appears keeps the clip, so the free preview must already be spent."""
    task_id, _, original = await make_task(db_session, tmp_path, count=0, status="queued")
    owner = (await db_session.execute(text("SELECT user_id FROM tasks WHERE id = :id"), {"id": task_id})).scalar()
    await db_session.execute(text("UPDATE tasks SET preview = '{\"status\": \"pending\"}'::jsonb WHERE id = :id"), {"id": task_id})
    await db_session.commit()
    segments = [{"start_time": "00:10", "end_time": "00:40", "virality_score": 80, "hook_title": "The one"}]
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    seen = []

    async def clip_ready(*_args):
        async with sessions() as other:
            seen.append((await other.execute(text("SELECT preview_used_at FROM users WHERE id = :id"), {"id": owner})).scalar())

    async with sessions() as worker:
        service, _, _ = setup_worker(worker, tmp_path, original)
        service.video_service.process_video_complete = AsyncMock(return_value={
            "video_path": str(original), "segments_to_render": segments, "segments": segments,
        })
        await service.process_task(task_id, "upload://input.mp4", "upload", user_id=owner, clip_ready_callback=clip_ready)
    assert len(seen) == 1 and seen[0] is not None


@pytest.mark.asyncio
async def test_an_unlock_that_cannot_be_queued_stays_locked_and_can_be_retried(
    client, auth_headers, db_session, hosted, tmp_path, monkeypatch
):
    task_id, _ = await _preview_task(db_session, tmp_path)
    await db_session.execute(text("UPDATE users SET plan = 'pro', subscription_status = 'active' WHERE id = 'user-1'"))
    await db_session.commit()
    monkeypatch.setattr(
        "src.api.routes.tasks.JobQueue.enqueue_processing_job", AsyncMock(side_effect=RuntimeError("redis down"))
    )
    failed = await client.post(f"/tasks/{task_id}/unlock", headers=auth_headers)
    assert failed.status_code == 500
    db_session.expire_all()
    row = (await db_session.execute(text("SELECT status, preview FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    assert row.status == "completed" and "unlocked_at" not in row.preview

    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", AsyncMock(return_value="retry-job"))
    retried = await client.post(f"/tasks/{task_id}/unlock", headers=auth_headers)
    assert retried.status_code == 200, retried.text
    assert retried.json()["job_id"] == "retry-job"
    charged = (await db_session.execute(text("SELECT COUNT(*) FROM minute_usage_events WHERE task_id = :id"), {"id": task_id})).scalar()
    assert charged == 1


@pytest.mark.asyncio
async def test_a_buyer_is_unlocked_even_behind_many_unpaid_requests(
    client, auth_headers, db_session, hosted, tmp_path, monkeypatch, initialized_database
):
    from src.workers import tasks as worker_tasks

    task_id, _ = await _preview_task(db_session, tmp_path)
    await db_session.execute(
        text("UPDATE tasks SET preview = preview || CAST(:p AS JSONB) WHERE id = :id"),
        {"id": task_id, "p": json.dumps({"unlock_requested_at": (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()})},
    )
    await db_session.execute(text("UPDATE users SET plan = 'pro', subscription_status = 'active' WHERE id = 'user-1'"))
    newer = json.dumps({"status": "pending", "unlock_requested_at": datetime.now(timezone.utc).isoformat()})
    for _ in range(worker_tasks.UNLOCKS_PER_RUN + 5):
        other = await create_user(db_session, email_verified=True)
        await db_session.execute(
            text("INSERT INTO tasks (id, user_id, status, preview) VALUES (:id, :u, 'completed', CAST(:p AS JSONB))"),
            {"id": str(uuid4()), "u": other["id"], "p": newer},
        )
    await db_session.commit()
    enqueue = AsyncMock(return_value="bg-unlock-job")
    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", enqueue)
    monkeypatch.setattr("src.database.AsyncSessionLocal", async_sessionmaker(initialized_database, expire_on_commit=False))

    result = await worker_tasks.unlock_paid_previews({})
    assert result["pending"] >= worker_tasks.UNLOCKS_PER_RUN + 6
    assert result["checked"] == 1 and result["unlocked"] == 1
    enqueue.assert_awaited_once()


@pytest.mark.asyncio
async def test_retrying_a_failed_preview_after_subscribing_unlocks_it(
    client, auth_headers, db_session, hosted, tmp_path, monkeypatch
):
    task_id, _ = await _preview_task(db_session, tmp_path, status="error")
    await db_session.execute(text("UPDATE users SET plan = 'pro', subscription_status = 'active' WHERE id = 'user-1'"))
    await db_session.commit()
    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", AsyncMock(return_value="resume-job"))
    resumed = await client.post(f"/tasks/{task_id}/resume", headers=auth_headers)
    assert resumed.status_code == 200, resumed.text
    db_session.expire_all()
    row = (await db_session.execute(text("SELECT status, preview FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    # Charged once, and every clip is made (not the preview's one again).
    assert row.status == "queued" and row.preview["unlocked_at"]
    charged = (await db_session.execute(text("SELECT COUNT(*) FROM minute_usage_events WHERE task_id = :id"), {"id": task_id})).scalar()
    assert charged == 1


@pytest.mark.asyncio
async def test_an_unlock_asked_for_while_the_preview_is_made_is_kept(db_session, initialized_database, tmp_path):
    task_id, _, original = await make_task(db_session, tmp_path, count=0, status="queued")
    owner = (await db_session.execute(text("SELECT user_id FROM tasks WHERE id = :id"), {"id": task_id})).scalar()
    await db_session.execute(text("UPDATE tasks SET preview = '{\"status\": \"pending\"}'::jsonb WHERE id = :id"), {"id": task_id})
    await db_session.commit()
    segments = [{"start_time": "00:10", "end_time": "00:40", "virality_score": 80, "hook_title": "The one"}]
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def asked_meanwhile(*_args, **_kwargs):
        # The person picks a plan while the video is analysed.
        async with sessions() as other:
            await other.execute(
                text("UPDATE tasks SET preview = preview || '{\"unlock_requested_at\": \"2026-11-25T10:00:00+00:00\"}'::jsonb WHERE id = :id"),
                {"id": task_id},
            )
            await other.commit()
        return {"video_path": str(original), "segments_to_render": segments, "segments": segments}

    async with sessions() as worker:
        service, _, _ = setup_worker(worker, tmp_path, original)
        service.video_service.process_video_complete = AsyncMock(side_effect=asked_meanwhile)
        await service.process_task(task_id, "upload://input.mp4", "upload", user_id=owner)
    db_session.expire_all()
    row = (await db_session.execute(text("SELECT preview FROM tasks WHERE id = :id"), {"id": task_id})).fetchone()
    assert row.preview["unlock_requested_at"] and row.preview["found"] == 1
