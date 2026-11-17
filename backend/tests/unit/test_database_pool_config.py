import pytest

from src.database import _build_engine


def test_default_pool_has_a_finite_connection_budget(monkeypatch):
    for name in ("DATABASE_POOL_SIZE", "DATABASE_MAX_OVERFLOW", "DATABASE_POOL_TIMEOUT_SECONDS"):
        monkeypatch.delenv(name, raising=False)
    engine = _build_engine("postgresql+asyncpg://localhost/test")
    pool = engine.sync_engine.pool
    assert pool.size() == 5
    assert pool._max_overflow == 0
    assert pool.timeout() == 10


@pytest.mark.parametrize("name,value", [("DATABASE_POOL_SIZE", "0"), ("DATABASE_MAX_OVERFLOW", "-1"), ("DATABASE_POOL_TIMEOUT_SECONDS", "0")])
def test_unbounded_pool_settings_are_rejected(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError):
        _build_engine("postgresql+asyncpg://localhost/test")
