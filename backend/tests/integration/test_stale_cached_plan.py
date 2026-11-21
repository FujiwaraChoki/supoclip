"""A column added while a process runs must not fail its next ``SELECT t.*``.

asyncpg keeps each connection's prepared statements, so after ``ALTER TABLE tasks ADD COLUMN`` from
another process the next ``SELECT t.*`` on that connection is rejected ("cached plan must not change
result type"). The repositories retry that once.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.repositories.source_repository import SourceRepository
from src.repositories.task_repository import TaskRepository


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
