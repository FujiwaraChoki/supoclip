"""A video job that finds the database pool exhausted before it starts is retried.

2026-11-18: twelve jobs started at once after a worker restart against a pool of
five. One job's run-guard read timed out, the job failed for good, and the task
stayed queued with its minutes charged.
"""

import asyncio
import os
import uuid
from unittest.mock import AsyncMock

import pytest
from arq import Retry
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from src.database import configure_database
from src.repositories.task_repository import TaskRepository
from src.workers import tasks as worker_tasks
from src.workers import video_admission
from tests.fixtures.factories import create_source, create_task, create_user


@pytest.fixture()
async def one_connection_pool(initialized_database, test_database_url):
    """The worker's engine with a single pooled connection and a short wait."""
    engine = create_async_engine(test_database_url, pool_size=1, max_overflow=0, pool_timeout=1)
    configure_database(
        engine=engine,
        session_maker=async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
    )
    try:
        yield engine
    finally:
        configure_database(engine=initialized_database)
        await engine.dispose()


@pytest.fixture()
async def redis():
    client = Redis(host=os.getenv("REDIS_HOST", "127.0.0.1"), port=int(os.getenv("REDIS_PORT", "6379")))
    try:
        yield client
    finally:
        await client.aclose()


@pytest.fixture()
def admitted(monkeypatch):
    """Admission passes without a query, so the first pooled read is the run guard's."""
    monkeypatch.setattr("src.runtime_settings.load_runtime_settings_cache", AsyncMock())
    monkeypatch.setattr(video_admission, "admit_video_job", AsyncMock(return_value="run"))
    monkeypatch.setattr(video_admission, "hold_for_upload", AsyncMock(return_value="run"))
    monkeypatch.setattr(video_admission, "release_video_slot", AsyncMock())
    monkeypatch.setattr(video_admission, "wake_next_video_jobs", AsyncMock(return_value=[]))


async def charged_queued_task(db_session, job_id: str) -> tuple[str, str]:
    user = await create_user(db_session)
    source = await create_source(db_session, source_type="video_url", url="upload://input.mp4")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="queued", source_duration_seconds=120
    )
    await TaskRepository.save_source_metadata(db_session, task["id"], {"processing_job_id": job_id})
    await db_session.execute(
        text(
            "INSERT INTO minute_usage_events (id, user_id, task_id, minutes, source_duration_seconds) "
            "VALUES (:id, :user_id, :task_id, 2, 120)"
        ),
        {"id": str(uuid.uuid4()), "user_id": user["id"], "task_id": task["id"]},
    )
    await db_session.commit()
    return task["id"], user["id"]


async def task_state(db_session, task_id: str) -> tuple[str, object]:
    await db_session.rollback()
    row = (
        await db_session.execute(
            text(
                "SELECT t.status, m.refunded_at FROM tasks t "
                "JOIN minute_usage_events m ON m.task_id = t.id WHERE t.id = :id"
            ),
            {"id": task_id},
        )
    ).one()
    return row.status, row.refunded_at


async def run_job(redis, task_id: str, user_id: str, *, job_try: int, job_id: str):
    ctx = {"redis": redis, "job_try": job_try, "job_id": job_id}
    return await worker_tasks.process_video_task(ctx, task_id, "upload://input.mp4", "upload", user_id)


async def test_guard_read_on_an_exhausted_pool_retries_the_job(
    db_session, one_connection_pool, redis, admitted
):
    job_id = f"job-{uuid.uuid4()}"
    task_id, user_id = await charged_queued_task(db_session, job_id)

    # Every other job on the worker holds the pool's connections.
    async with one_connection_pool.connect() as busy:
        await busy.execute(text("SELECT 1"))
        with pytest.raises(Retry) as retry:
            await run_job(redis, task_id, user_id, job_try=1, job_id=job_id)

    # Retried later, with jitter so jobs that started together come back apart.
    assert 5_000 <= retry.value.defer_score <= 10_000
    assert await task_state(db_session, task_id) == ("queued", None)
    assert not await redis.sismember("tasks:dead_letter", task_id)


async def test_last_try_on_an_exhausted_pool_fails_and_refunds_the_task(
    db_session, one_connection_pool, redis, admitted
):
    job_id = f"job-{uuid.uuid4()}"
    task_id, user_id = await charged_queued_task(db_session, job_id)
    try:
        # The pool stays exhausted for the whole job, cleanup included.
        async with one_connection_pool.connect() as busy:
            await busy.execute(text("SELECT 1"))
            with pytest.raises(Exception) as failed:
                await run_job(redis, task_id, user_id, job_try=3, job_id=job_id)
        status, refunded_at = await task_state(db_session, task_id)
        assert status == "error"
        assert refunded_at is not None
        assert not isinstance(failed.value, Retry)
        assert isinstance(failed.value.__cause__, PoolTimeout)
        assert await redis.sismember("tasks:dead_letter", task_id)
    finally:
        await redis.delete(f"dead_letter:{task_id}", f"progress:{task_id}")
        await redis.srem("tasks:dead_letter", task_id)


async def test_last_try_leaves_a_task_that_moved_to_another_job(
    db_session, one_connection_pool, redis, admitted
):
    task_id, user_id = await charged_queued_task(db_session, "job-from-a-resume")
    try:
        async with one_connection_pool.connect() as busy:
            await busy.execute(text("SELECT 1"))
            with pytest.raises(Exception):
                await run_job(redis, task_id, user_id, job_try=3, job_id="stale-job")
        assert await task_state(db_session, task_id) == ("queued", None)
    finally:
        await redis.delete(f"dead_letter:{task_id}", f"progress:{task_id}")
        await redis.srem("tasks:dead_letter", task_id)


async def test_admission_read_on_an_exhausted_pool_retries_the_job(
    db_session, one_connection_pool, redis
):
    job_id = f"job-{uuid.uuid4()}"
    task_id, user_id = await charged_queued_task(db_session, job_id)

    async with one_connection_pool.connect() as busy:
        await busy.execute(text("SELECT 1"))
        with pytest.raises(Retry):
            await asyncio.wait_for(run_job(redis, task_id, user_id, job_try=2, job_id=job_id), 10)

    assert await task_state(db_session, task_id) == ("queued", None)
