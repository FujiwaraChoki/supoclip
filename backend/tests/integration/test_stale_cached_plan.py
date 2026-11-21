"""A column added while a process runs must not fail its next ``SELECT t.*``.

asyncpg keeps each connection's prepared statements, so after ``ALTER TABLE tasks ADD COLUMN`` from
another process the next ``SELECT t.*`` on that connection is rejected ("cached plan must not change
result type"). The repositories retry that once.
"""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.repositories.source_repository import SourceRepository
from src.repositories.task_repository import TaskRepository
from src.workers import video_admission
from tests.fixtures.factories import create_source, create_task, create_user


@pytest.mark.asyncio
async def test_reads_survive_a_column_added_by_another_process(initialized_database):
    url = initialized_database.url
    # One pooled connection, so the second read reuses the first read's prepared statement.
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    other_process = create_async_engine(url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            assert await TaskRepository.get_task_by_id(db, "stale-plan-missing") is None
            assert await SourceRepository.get_source_by_id(db, "stale-plan-missing") is None
            await db.commit()

            async with other_process.begin() as ddl:
                await ddl.execute(text("ALTER TABLE tasks ADD COLUMN stale_plan_probe INTEGER"))
                await ddl.execute(text("ALTER TABLE sources ADD COLUMN stale_plan_probe INTEGER"))

            # Inside the caller's transaction: the retry must leave it usable.
            await db.execute(text("SELECT 1"))
            assert await TaskRepository.get_task_by_id(db, "stale-plan-missing") is None
            assert await SourceRepository.get_source_by_id(db, "stale-plan-missing") is None
            assert await db.scalar(text("SELECT 1")) == 1
            await db.commit()
    finally:
        await engine.dispose()
        async with other_process.begin() as ddl:
            await ddl.execute(text("ALTER TABLE tasks DROP COLUMN IF EXISTS stale_plan_probe"))
            await ddl.execute(text("ALTER TABLE sources DROP COLUMN IF EXISTS stale_plan_probe"))
        await other_process.dispose()


class _Redis:
    async def get(self, _key):
        return None


class _Settings:
    video_job_user_concurrency = 1
    video_job_defer_seconds = 20


@pytest.mark.asyncio
async def test_video_admission_survives_a_column_added_by_another_process(
    initialized_database, db_session, monkeypatch
):
    owner = await create_user(db_session)
    source = await create_source(db_session, source_type="video_url", url="upload://input.mp4")
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"], status="processing")
    await db_session.commit()
    monkeypatch.setattr(video_admission, "get_config", lambda: _Settings())
    monkeypatch.setattr(video_admission, "prune_video_slots", AsyncMock())
    monkeypatch.setattr(video_admission, "_clear_finished_upload_wait", AsyncMock())
    monkeypatch.setattr(video_admission, "acquire_video_slot", AsyncMock(return_value=True))

    async def admit(db):
        return await video_admission.admit_video_job(
            {"redis": _Redis()}, db, task_id=task["id"], user_id=owner["id"], job_args=(task["id"],),
        )

    url = initialized_database.url
    engine = create_async_engine(url, pool_size=1, max_overflow=0)
    other_process = create_async_engine(url, poolclass=NullPool)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as db:
            assert await admit(db) == "run"
            await db.commit()

        async with other_process.begin() as ddl:
            await ddl.execute(text("ALTER TABLE tasks ADD COLUMN stale_plan_probe INTEGER"))

        # The next job on the same pooled connection, already in a transaction as in process_video_task.
        async with sessions() as db:
            await db.execute(text("SELECT 1"))
            assert await admit(db) == "run"
            await db.commit()
    finally:
        await engine.dispose()
        async with other_process.begin() as ddl:
            await ddl.execute(text("ALTER TABLE tasks DROP COLUMN IF EXISTS stale_plan_probe"))
        await other_process.dispose()
