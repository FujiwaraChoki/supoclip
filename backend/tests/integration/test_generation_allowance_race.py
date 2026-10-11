import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.routes import tasks
from src.services.billing_service import BillingService
from src.repositories.generation_allowance_guard import (
    generation_allowance_guard, GenerationAllowanceBusy,
)
from tests.fixtures.factories import create_user, create_source, create_task


async def wait_for_admission_waiter(engine):
    async def observe():
        while True:
            async with engine.connect() as connection:
                waiting = await connection.scalar(text(
                    "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND NOT granted"
                ))
            if waiting:
                return
            await asyncio.sleep(.01)
    await asyncio.wait_for(observe(), 3)


@pytest.mark.asyncio
@pytest.mark.parametrize("limit", [1, 10])
@pytest.mark.parametrize("first_action,second_action", [
    ("create", "create"), ("create", "resume"), ("resume", "create"), ("resume", "resume"),
])
async def test_concurrent_admissions_cannot_claim_the_same_allowance_slot(
    initialized_database, app, db_session, monkeypatch, first_action, second_action, limit
):
    app.state.config.monetization_enabled = True
    app.state.config.pro_plan_task_limit = limit
    await create_user(
        db_session, user_id="user-1", plan="pro", subscription_status="active"
    )
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    monkeypatch.setattr(tasks, "_save_task_source_metadata", AsyncMock())
    request = SimpleNamespace(
        json=AsyncMock(return_value={"source": {"url": "upload://source.mp4"}}),
        app=app,
    )
    checked = asyncio.Event()
    release = asyncio.Event()
    source = await create_source(db_session, source_type="video_url", url="upload://source.mp4")
    retry_tasks = [await create_task(
        db_session, user_id="user-1", source_id=source["id"], status="error"
    ) for _ in range(2)]
    monkeypatch.setattr(tasks.JobQueue, "enqueue_processing_job", AsyncMock(return_value="job-1"))
    redis = SimpleNamespace(delete=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(tasks.redis, "Redis", lambda **_kwargs: redis)
    original_create = BillingService.assert_can_create_task
    original_resume = BillingService.assert_can_resume_task
    calls = 0

    async def pause_after_first_check(self, user_id, task_id=None):
        nonlocal calls
        summary = (
            await original_create(self, user_id) if task_id is None
            else await original_resume(self, user_id, task_id)
        )
        calls += 1
        if calls == (2 if first_action == "create" else 1):
            checked.set()
            await release.wait()
        return summary

    monkeypatch.setattr(BillingService, "assert_can_create_task", pause_after_first_check)
    monkeypatch.setattr(BillingService, "assert_can_resume_task", pause_after_first_check)
    async def admit(action, db, retry):
        if action == "create":
            return await tasks.create_task(request, db)
        return await tasks.resume_task(retry["id"], request, db)

    sessions = async_sessionmaker(initialized_database, class_=AsyncSession, expire_on_commit=False)
    async with sessions() as first_db, sessions() as second_db:
        first = asyncio.create_task(admit(first_action, first_db, retry_tasks[0]))
        await asyncio.wait_for(checked.wait(), 5)
        second = asyncio.create_task(admit(second_action, second_db, retry_tasks[1]))
        try:
            await wait_for_admission_waiter(initialized_database)
            assert not second.done()
        finally:
            release.set()
        results = await asyncio.gather(first, second, return_exceptions=True)

    expected_successes = 1 if limit == 1 else 2
    assert sum(isinstance(result, dict) for result in results) == expected_successes, results
    rejected = [result for result in results if isinstance(result, HTTPException)]
    if limit == 1:
        assert len(rejected) == 1
        assert rejected[0].status_code == 402
    else:
        assert rejected == []
    summary = await BillingService(db_session, app.state.config).get_usage_summary("user-1")
    assert summary["usage_count"] == expected_successes
    if limit == 1:
        async with sessions() as db:
            with pytest.raises(HTTPException) as sequential:
                await tasks.create_task(request, db)
            assert sequential.value.status_code == 402


@pytest.mark.asyncio
async def test_allowance_guard_survives_commits_and_releases_on_cancellation(
    db_session, initialized_database
):
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    entered = asyncio.Event()
    async with sessions() as other:
        async def hold():
            async with generation_allowance_guard(db_session, "guard-owner", enabled=True):
                await db_session.commit()
                entered.set()
                await asyncio.Event().wait()

        job = asyncio.create_task(hold())
        await asyncio.wait_for(entered.wait(), 5)
        try:
            with pytest.raises(GenerationAllowanceBusy):
                async with generation_allowance_guard(other, "guard-owner", enabled=True):
                    pass
            async with generation_allowance_guard(other, "another-owner", enabled=True):
                pass
        finally:
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
        async with generation_allowance_guard(other, "guard-owner", enabled=True):
            pass


@pytest.mark.asyncio
async def test_self_hosted_admissions_do_not_need_an_allowance_lock():
    async with generation_allowance_guard(None, "self-host", enabled=False):
        pass


@pytest.mark.asyncio
async def test_allowance_guard_releases_after_body_exception(db_session):
    with pytest.raises(ValueError, match="admission failed"):
        async with generation_allowance_guard(db_session, "failed-owner", enabled=True):
            raise ValueError("admission failed")
    async with generation_allowance_guard(db_session, "failed-owner", enabled=True):
        pass


@pytest.mark.asyncio
async def test_refunded_error_job_cannot_start_without_explicit_resume(
    db_session, initialized_database, tmp_path
):
    from tests.integration.test_clip_edit_atomicity import make_task
    from tests.integration.test_processing_cancellation import setup_worker
    from src.repositories.task_repository import TaskRepository
    from src.services.billing_service import BillingService
    from src.config import Config
    
    task_id, _, original = await make_task(db_session, tmp_path, status="error")
    await create_user(db_session, user_id="user-1", plan="pro", subscription_status="active")
    await db_session.execute(
        text("UPDATE tasks SET user_id='user-1' WHERE id=:id"), {"id": task_id}
    )
    await db_session.execute(
        text("DELETE FROM generated_clips WHERE task_id=:id"), {"id": task_id}
    )
    await db_session.commit()
    config = Config()
    config.monetization_enabled = True
    assert (await BillingService(db_session, config).get_usage_summary("user-1"))["usage_count"] == 0
    service, _, _ = setup_worker(db_session, tmp_path, original)
    result = await service.process_task(task_id, "upload://source.mp4", "video_url")
    assert result["skipped"] is True
    assert result["status"] == "error"
    service.video_service.process_video_complete.assert_not_awaited()
    assert (await TaskRepository.get_task_by_id(db_session, task_id))["status"] == "error"


@pytest.mark.asyncio
async def test_cancelling_waiting_admission_does_not_leak_lock(initialized_database):
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    async with sessions() as owner, sessions() as waiter:
        async with generation_allowance_guard(owner, "cancelled-waiter", enabled=True):
            async def acquire():
                async with generation_allowance_guard(waiter, "cancelled-waiter", enabled=True):
                    pytest.fail("Waiter entered while the owner held the lock")
            job = asyncio.create_task(acquire())
            await wait_for_admission_waiter(initialized_database)
            job.cancel()
            with pytest.raises(asyncio.CancelledError):
                await job
        async with generation_allowance_guard(waiter, "cancelled-waiter", enabled=True):
            pass


@pytest.mark.asyncio
@pytest.mark.parametrize("heartbeat", [b"healthy", None])
async def test_polling_old_queue_reservation_only_refunds_when_worker_absent(
    db_session, initialized_database, tmp_path, monkeypatch, heartbeat
):
    from src.services.task_service import TaskService
    from src.workers.job_queue import JobQueue
    from tests.integration.test_clip_edit_atomicity import make_task
    from tests.integration.test_processing_cancellation import setup_worker

    task_id, _, original = await make_task(db_session, tmp_path, status="queued")
    await db_session.execute(text(
        "UPDATE tasks SET created_at=NOW()-INTERVAL '1 hour', "
        "updated_at=NOW()-INTERVAL '1 hour' WHERE id=:id"
    ), {"id": task_id})
    await db_session.commit()
    monkeypatch.setattr(JobQueue, "get_pool", AsyncMock(return_value=SimpleNamespace(
        get=AsyncMock(return_value=heartbeat)
    )))
    service = TaskService(db_session)
    service._load_task_source_settings = AsyncMock(return_value={})
    polled = await service.get_task_with_clips(task_id)
    assert polled["status"] == ("queued" if heartbeat else "error")
    worker, _, _ = setup_worker(db_session, tmp_path, original)
    result = await worker.process_task(task_id, "upload://input.mp4", "upload")
    if heartbeat:
        worker.video_service.process_video_complete.assert_awaited_once()
        assert (await worker.task_repo.get_task_by_id(db_session, task_id))["status"] == "completed"
    else:
        assert result["skipped"] is True
        worker.video_service.process_video_complete.assert_not_awaited()
