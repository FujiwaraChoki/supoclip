from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from src.api.routes import tasks
from src.services.billing_service import BillingLimitExceeded


@pytest.fixture
def resume_context(monkeypatch):
    task = {
        "id": "task-1", "user_id": "user-1", "status": "error",
        "source_url": "https://youtu.be/abcdefghijk", "source_type": "youtube",
    }
    repo = SimpleNamespace(
        get_task_by_id=AsyncMock(return_value=task),
        update_task_status=AsyncMock(return_value=True),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda _db: SimpleNamespace(task_repo=repo))
    monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(return_value=task))
    monkeypatch.setattr(tasks, "_load_task_source_metadata", AsyncMock(return_value={}))

    @asynccontextmanager
    async def guard(_db, _task_id):
        yield

    monkeypatch.setattr(tasks, "task_run_guard", guard)
    billing_guard = AsyncMock(return_value={"can_create_task": True})
    monkeypatch.setattr(tasks.BillingService, "assert_can_resume_task", billing_guard)
    queue = AsyncMock(return_value="job-1")
    monkeypatch.setattr(tasks.JobQueue, "enqueue_processing_job", queue)
    redis = SimpleNamespace(delete=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(tasks.redis, "Redis", lambda **_kwargs: redis)
    return task, repo, billing_guard, queue, redis


@pytest.mark.asyncio
async def test_rejected_resume_leaves_status_cancellation_and_queue_untouched(resume_context):
    _task, repo, billing_guard, queue, redis = resume_context
    billing_guard.side_effect = BillingLimitExceeded({"reason": "Plan usage limit reached"})
    with pytest.raises(HTTPException) as error:
        await tasks.resume_task("task-1", SimpleNamespace(), db=None)
    assert error.value.status_code == 402
    assert error.value.detail["message"] == "Plan usage limit reached"
    repo.update_task_status.assert_not_awaited()
    redis.delete.assert_not_awaited()
    queue.assert_not_awaited()


@pytest.mark.asyncio
async def test_allowed_resume_checks_billing_before_reserving_and_enqueueing(resume_context):
    _task, repo, billing_guard, queue, _redis = resume_context
    events = []
    billing_guard.side_effect = lambda *_args: events.append("billing")
    repo.update_task_status.side_effect = lambda *_args, **_kwargs: events.append("queued")
    queue.side_effect = lambda *_args: events.append("enqueue") or "job-1"
    result = await tasks.resume_task("task-1", SimpleNamespace(), db=None)
    assert result["job_id"] == "job-1"
    assert events == ["billing", "queued", "enqueue"]
    billing_guard.assert_awaited_once_with("user-1", "task-1")


@pytest.mark.asyncio
async def test_failed_enqueue_restores_previous_failure_state(resume_context):
    _task, repo, _billing_guard, queue, _redis = resume_context
    queue.side_effect = RuntimeError("queue unavailable")
    with pytest.raises(HTTPException) as error:
        await tasks.resume_task("task-1", SimpleNamespace(), db=None)
    assert error.value.status_code == 500
    assert [call.args[2] for call in repo.update_task_status.await_args_list] == ["queued", "error"]


@pytest.mark.asyncio
async def test_already_queued_resume_does_not_need_another_slot(resume_context):
    task, repo, billing_guard, queue, _redis = resume_context
    task["status"] = "queued"
    result = await tasks.resume_task("task-1", SimpleNamespace(), db=None)
    assert result["message"] == "Task already queued"
    billing_guard.assert_not_awaited()
    repo.update_task_status.assert_not_awaited()
    queue.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["resume", "create"])
async def test_billing_denial_returns_json_402_with_real_datetime_fields(resume_context, monkeypatch, action):
    _task, repo, billing_guard, queue, redis = resume_context
    period_start = datetime(2026, 9, 28, 19, 37, tzinfo=timezone.utc)
    period_end = datetime(2026, 10, 28, 19, 37, tzinfo=timezone.utc)
    summary = {
        "reason": "Plan usage limit reached", "usage_count": 50, "usage_limit": 50,
        "period_start": period_start, "period_end": period_end,
        "trial_ends_at": None, "cancel_at": period_end,
    }
    billing_guard.side_effect = BillingLimitExceeded(summary)
    monkeypatch.setattr(
        tasks.BillingService, "assert_can_create_task",
        AsyncMock(side_effect=BillingLimitExceeded(summary)),
    )
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    app = FastAPI()
    app.include_router(tasks.router)

    async def no_database():
        yield None

    app.dependency_overrides[tasks.get_db] = no_database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        if action == "resume":
            response = await client.post("/tasks/task-1/resume")
        else:
            response = await client.post("/tasks/", json={"source": {"url": "https://youtu.be/abcdefghijk"}})

    assert response.status_code == 402
    detail = response.json()["detail"]
    assert detail["code"] == "SUBSCRIPTION_REQUIRED"
    assert detail["message"] == "Plan usage limit reached"
    assert detail["billing"]["usage_count"] == 50
    assert detail["billing"]["period_start"] == period_start.isoformat()
    assert detail["billing"]["period_end"] == period_end.isoformat()
    assert detail["billing"]["cancel_at"] == period_end.isoformat()
    assert detail["billing"]["trial_ends_at"] is None
    repo.update_task_status.assert_not_awaited()
    redis.delete.assert_not_awaited()
    queue.assert_not_awaited()
