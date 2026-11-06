import os
import sys
import time
import hmac
import hashlib
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Config, set_config_override
from src.database import configure_database, init_db, reset_database_state
from src.main import create_app
from src import feature_flags as feature_flags_module


class _FakeRedisPool:
    async def ping(self):
        return True


class FakeQueueAdapter:
    enqueued_jobs = []

    @classmethod
    async def get_pool(cls):
        return _FakeRedisPool()

    @classmethod
    async def close_pool(cls):
        return None

    @classmethod
    async def enqueue_job(cls, function_name: str, *args, **kwargs):
        cls.enqueued_jobs.append(
            {"function_name": function_name, "args": args, "kwargs": kwargs}
        )
        return "job-test-2"

    @classmethod
    async def enqueue_processing_job(cls, function_name: str, processing_mode: str, *args, **kwargs):
        cls.enqueued_jobs.append(
          {
            "function_name": function_name,
            "processing_mode": processing_mode,
            "args": args,
          }
        )
        return kwargs.get("_job_id", "job-test-1")


@pytest.fixture(scope="session")
def test_database_url():
    return os.getenv("TEST_DATABASE_URL") or os.getenv("DATABASE_URL")


@pytest.fixture(scope="session")
async def initialized_database(test_database_url):
    if not test_database_url:
        pytest.skip("DATABASE_URL or TEST_DATABASE_URL must be set for backend tests")

    engine = create_async_engine(test_database_url, poolclass=NullPool)
    configure_database(engine=engine)
    await init_db()
    yield engine
    await reset_database_state()


@pytest.fixture()
async def db_session(initialized_database):
    session_maker = async_sessionmaker(
        initialized_database,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_maker() as session:
        try:
            yield session
        finally:
            await session.rollback()
            await session.close()


@pytest.fixture()
async def app(db_session):
    config = Config()
    config.self_host = True
    config.monetization_enabled = False
    config.backend_auth_secret = "test-backend-auth-secret"
    config.allow_unsigned_backend_auth = False
    config.redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
    config.redis_port = int(os.getenv("REDIS_PORT", "6379"))

    test_app = create_app(config=config, queue_adapter=FakeQueueAdapter)
    feature_flags_module.clear_cache()
    try:
        yield test_app
    finally:
        set_config_override(None)
        feature_flags_module.clear_cache()


@pytest.fixture()
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture()
def auth_headers():
    user_id = "user-1"
    timestamp = str(int(time.time()))
    payload = f"{user_id}:{timestamp}".encode("utf-8")
    signature = hmac.new(
        b"test-backend-auth-secret", payload, hashlib.sha256
    ).hexdigest()

    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": timestamp,
        "x-supoclip-signature": signature,
    }


_FLAG_COLUMNS = ("web_enabled", "ios_enabled", "admins_bypass")


@pytest.fixture()
async def feature_flag(db_session):
    """``await feature_flag("workflows", web_enabled=True)`` sets a flag row for one
    test (creating it if needed) and puts every touched row back afterwards."""
    from sqlalchemy import text

    saved: dict[str, dict | None] = {}

    async def set_flag(key: str, **values: bool) -> None:
        row = (
            await db_session.execute(
                text("SELECT web_enabled, ios_enabled, admins_bypass FROM feature_flags WHERE key = :key"),
                {"key": key},
            )
        ).mappings().first()
        if key not in saved:
            saved[key] = dict(row) if row else None
        current = {"web_enabled": False, "ios_enabled": False, "admins_bypass": True, **(dict(row) if row else {})}
        current.update(values)
        await db_session.execute(
            text(
                """
                INSERT INTO feature_flags (key, web_enabled, ios_enabled, admins_bypass)
                VALUES (:key, :web_enabled, :ios_enabled, :admins_bypass)
                ON CONFLICT (key) DO UPDATE SET web_enabled = EXCLUDED.web_enabled,
                    ios_enabled = EXCLUDED.ios_enabled, admins_bypass = EXCLUDED.admins_bypass
                """
            ),
            {"key": key, **{column: current[column] for column in _FLAG_COLUMNS}},
        )
        await db_session.commit()
        feature_flags_module.clear_cache()

    yield set_flag

    await db_session.rollback()
    for key, row in saved.items():
        if row is None:
            await db_session.execute(text("DELETE FROM feature_flags WHERE key = :key"), {"key": key})
        else:
            await db_session.execute(
                text(
                    "UPDATE feature_flags SET web_enabled = :web_enabled, ios_enabled = :ios_enabled, "
                    "admins_bypass = :admins_bypass WHERE key = :key"
                ),
                {"key": key, **row},
            )
    await db_session.commit()
    feature_flags_module.clear_cache()
