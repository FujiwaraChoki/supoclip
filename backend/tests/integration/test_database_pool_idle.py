import asyncio
import logging

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src import database
from src.repositories.session_lock_connection import session_lock_connection


async def live(engine, pids):
    async with engine.connect() as connection:
        return set(await connection.scalars(
            text("SELECT pid FROM pg_stat_activity WHERE pid = ANY(:pids)"),
            {"pids": list(pids)},
        ))


async def fill_pool(sessions, count):
    pids = set()
    ready = asyncio.Event()

    async def hold():
        async with sessions() as session:
            pids.add(await session.scalar(text("SELECT pg_backend_pid()")))
            if len(pids) == count:
                ready.set()
            await ready.wait()

    await asyncio.wait_for(asyncio.gather(*(hold() for _ in range(count))), 5)
    return pids


@pytest.mark.asyncio
async def test_idle_pool_connections_are_released_and_replaced(
    initialized_database, monkeypatch, caplog
):
    monkeypatch.setenv("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("DATABASE_POOL_SIZE", "6")
    caplog.set_level(logging.WARNING)
    engine = database._build_engine(initialized_database.url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        pids = await fill_pool(sessions, 6)
        assert len(pids) == 6

        # Queries this frequent would keep all six alive if the pool handed
        # out each connection in turn.
        for _ in range(25):
            async with sessions() as session:
                assert await session.scalar(text("SELECT 1")) == 1
            await asyncio.sleep(.1)
        assert len(await live(initialized_database, pids)) <= 1

        # A burst afterwards gets working connections, with nothing logged.
        async def query():
            async with sessions() as session:
                return await session.scalar(text("SELECT 1"))
        assert await asyncio.gather(*(query() for _ in range(8))) == [1] * 8
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_advisory_lock_holder_outlives_idle_session_timeout(
    initialized_database, monkeypatch
):
    monkeypatch.setenv("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", "1")
    engine = database._build_engine(initialized_database.url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            async with session_lock_connection(session) as connection:
                await connection.execute(text("SELECT pg_advisory_lock(913999)"))
                await connection.commit()
                await asyncio.sleep(2.5)
                assert await connection.scalar(text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                    "AND objid = 913999 AND pid = pg_backend_pid()"
                )) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_long_lived_session_survives_idle_gaps(initialized_database, monkeypatch, caplog):
    # A worker keeps one session for a whole job and leaves it unused while it
    # renders. Committing hands the connection back, so the next statement checks
    # out a pinged one even after the server closed the old connection.
    monkeypatch.setenv("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", "1")
    caplog.set_level(logging.WARNING)
    engine = database._build_engine(initialized_database.url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sessions() as session:
            first = await session.scalar(text("SELECT pg_backend_pid()"))
            await session.commit()
            await asyncio.sleep(2.5)
            assert first not in await live(initialized_database, {first})
            assert await session.scalar(text("SELECT 1")) == 1
            await session.commit()
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    finally:
        await engine.dispose()
