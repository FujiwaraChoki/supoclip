import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from src import database
from src.database import _build_engine


def test_default_pool_has_a_finite_connection_budget(monkeypatch):
    for name in ("DATABASE_POOL_SIZE", "DATABASE_MAX_OVERFLOW", "DATABASE_POOL_TIMEOUT_SECONDS",
                 "DATABASE_IDLE_SESSION_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    engine = _build_engine("postgresql+asyncpg://localhost/test")
    pool = engine.sync_engine.pool
    assert pool.size() == 5
    assert pool._max_overflow == 0
    assert pool.timeout() == 10


def build(monkeypatch):
    seen = {}

    def capture(url, **kwargs):
        seen.update(kwargs)
        return create_async_engine(url, **kwargs)

    monkeypatch.setattr(database, "create_async_engine", capture)
    engine = _build_engine("postgresql+asyncpg://localhost/test")
    return engine, seen


def test_idle_pooled_connections_are_reclaimed_by_default(monkeypatch):
    monkeypatch.delenv("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", raising=False)
    monkeypatch.setenv("DATABASE_POOL_SIZE", "10")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "5")
    engine, kwargs = build(monkeypatch)
    pool = engine.sync_engine.pool
    assert (pool.size(), pool._max_overflow) == (10, 5)
    assert kwargs["pool_pre_ping"] and kwargs["pool_use_lifo"]
    assert kwargs["connect_args"]["server_settings"] == {
        "idle_in_transaction_session_timeout": "300000",
        "idle_session_timeout": "600000",
    }


def test_idle_session_timeout_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", "0")
    _, kwargs = build(monkeypatch)
    assert kwargs["connect_args"]["server_settings"] == {"idle_in_transaction_session_timeout": "300000"}


@pytest.mark.parametrize("name,value", [("DATABASE_POOL_SIZE", "0"), ("DATABASE_MAX_OVERFLOW", "-1"), ("DATABASE_POOL_TIMEOUT_SECONDS", "0"),
                                        ("DATABASE_IDLE_SESSION_TIMEOUT_SECONDS", "-1")])
def test_unbounded_pool_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        _build_engine("postgresql+asyncpg://localhost/test")
