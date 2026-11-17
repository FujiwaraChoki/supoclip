import pytest
from sqlalchemy import text
from sqlalchemy.exc import TimeoutError

from src.database import _build_engine


@pytest.mark.asyncio
async def test_sixth_checkout_waits_then_times_out_and_pool_recovers(initialized_database, monkeypatch):
    monkeypatch.setenv("DATABASE_POOL_SIZE", "5")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT_SECONDS", "1")
    engine = _build_engine(initialized_database.url)
    connections = []
    try:
        for _ in range(5):
            connections.append(await engine.connect())
        with pytest.raises(TimeoutError):
            await engine.connect()
        await connections.pop().close()
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
        assert engine.pool.checkedout() == 4
    finally:
        for connection in connections:
            await connection.close()
        await engine.dispose()
