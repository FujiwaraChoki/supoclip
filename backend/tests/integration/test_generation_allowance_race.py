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
    app.state.config.pro_plan_minute_limit = limit
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
    # Every source is one minute long, so each admission claims one minute.
    retry_tasks = [await create_task(
        db_session, user_id="user-1", source_id=source["id"], status="error",
        source_duration_seconds=60,
    ) for _ in range(2)]
    monkeypatch.setattr(tasks.JobQueue, "enqueue_processing_job", AsyncMock(return_value="job-1"))
    redis = SimpleNamespace(delete=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(tasks.redis, "Redis", lambda **_kwargs: redis)
    original_create = BillingService.assert_can_create_task
    original_resume = BillingService.assert_can_resume_task
    calls = 0

    async def pause_after_first_check(self, user_id, task_id=None):
        nonlocal calls
        # create passes the source length; resume passes a task id.
        resuming = isinstance(task_id, str)
        summary = (
            await original_resume(self, user_id, task_id) if resuming
            else await original_create(self, user_id, task_id)
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
async def test_lock_waiters_do_not_exhaust_request_connection_pool(initialized_database):
    from sqlalchemy.ext.asyncio import create_async_engine
    engine = create_async_engine(
        initialized_database.url, pool_size=1, max_overflow=0, pool_timeout=.25
    )
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as owner, sessions() as waiter:
            async with generation_allowance_guard(owner, "pool-owner", enabled=True):
                async def acquire():
                    async with generation_allowance_guard(waiter, "pool-owner", enabled=True):
                        return await waiter.scalar(text("SELECT 1"))
                job = asyncio.create_task(acquire())
                try:
                    await wait_for_admission_waiter(engine)
                    assert await owner.scalar(text("SELECT 1")) == 1
                    await owner.commit()
                except BaseException:
                    job.cancel()
                    await asyncio.gather(job, return_exceptions=True)
                    raise
            assert await job == 1
    finally:
        await engine.dispose()
