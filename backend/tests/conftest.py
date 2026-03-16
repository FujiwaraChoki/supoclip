import os
import sys
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.config import Config
from src.database import configure_database, init_db, reset_database_state
from src.main_refactored import create_app


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
    async def enqueue_processing_job(cls, function_name: str, processing_mode: str, *args):
        cls.enqueued_jobs.append(
          {
            "function_name": function_name,
            "processing_mode": processing_mode,
            "args": args,
          }
        )
        return "job-test-1"


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
    config.redis_host = os.getenv("REDIS_HOST", "127.0.0.1")
    config.redis_port = int(os.getenv("REDIS_PORT", "6379"))

    test_app = create_app(config=config, queue_adapter=FakeQueueAdapter)
    return test_app


@pytest.fixture()
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as async_client:
        yield async_client


@pytest.fixture()
def auth_headers():
    return {"x-supoclip-user-id": "user-1"}
