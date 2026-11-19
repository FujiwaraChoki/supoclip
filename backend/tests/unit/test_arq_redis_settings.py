"""Every arq process opens Redis connections patiently and retries them.

A worker under heavy host load timed out opening a fresh connection at job
start with arq's one-second default; arq re-raised it and the worker exited.
"""

import asyncio

import pytest
from arq import create_pool
from redis.asyncio.connection import Connection
from redis.exceptions import ConnectionError as RedisConnectionError

from src.workers.ai_worker import AIWorkerSettings
from src.workers.cron_worker import CronWorkerSettings
from src.workers.job_queue import _get_redis_settings
from src.workers.redis_settings import arq_redis_settings
from src.workers.tasks import WorkerSettings


def _all_settings():
    return {
        "video worker": WorkerSettings.redis_settings,
        "cron worker": CronWorkerSettings.redis_settings,
        "ai worker": AIWorkerSettings.redis_settings,
        "enqueue": _get_redis_settings(),
    }


@pytest.mark.parametrize("name", ["video worker", "cron worker", "ai worker", "enqueue"])
def test_every_arq_process_waits_and_retries_when_opening_a_connection(name):
    settings = _all_settings()[name]
    assert settings.conn_timeout >= 10
    assert settings.conn_retries >= 3
    assert settings.retry_on_timeout is True
    assert settings.retry is not None
    assert settings.retry._retries >= 3


@pytest.mark.parametrize("name", ["video worker", "cron worker", "ai worker", "enqueue"])
def test_every_arq_process_reconnects_when_redis_closes_the_connection(name):
    """redis-py re-raises any error not in retry_on_error, so without
    ConnectionError there a Redis restart ends the worker."""
    settings = _all_settings()[name]
    assert RedisConnectionError in (settings.retry_on_error or [])


def test_connect_timeout_and_retries_come_from_the_environment(monkeypatch):
    from src.config import Config

    monkeypatch.setenv("REDIS_CONNECT_TIMEOUT_SECONDS", "25")
    monkeypatch.setenv("REDIS_CONNECT_RETRIES", "2")
    settings = arq_redis_settings(Config())
    assert settings.conn_timeout == 25
    assert settings.conn_retries == 2
    assert settings.retry._retries == 2

    monkeypatch.setenv("REDIS_CONNECT_TIMEOUT_SECONDS", "nonsense")
    monkeypatch.setenv("REDIS_CONNECT_RETRIES", "-4")
    settings = arq_redis_settings(Config())
    assert settings.conn_timeout == 10
    assert settings.conn_retries == 0


@pytest.mark.asyncio
async def test_a_slow_connect_on_a_running_worker_is_retried_not_raised(monkeypatch):
    """The production crash: the first command of a job opens a new connection
    and that connect times out once. It must be retried, not surface."""
    settings = WorkerSettings.redis_settings
    pool = await create_pool(settings)
    try:
        await pool.connection_pool.disconnect()
        real_connect = Connection._connect
        attempts = 0

        async def flaky_connect(self):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise asyncio.TimeoutError()
            return await real_connect(self)

        monkeypatch.setattr(Connection, "_connect", flaky_connect)
        async with pool.pipeline(transaction=True) as pipe:
            pipe.incr("arq-conn-timeout-probe")
            pipe.delete("arq-conn-timeout-probe")
            await pipe.execute()
        assert attempts == 2
    finally:
        await pool.aclose()


@pytest.mark.asyncio
async def test_a_connection_closed_by_redis_is_retried_not_raised(monkeypatch):
    """When Redis restarts, the next command on a pooled connection fails with
    'Connection closed by server.' It must reconnect and retry, not surface."""
    settings = WorkerSettings.redis_settings
    pool = await create_pool(settings)
    try:
        real_read = Connection.read_response
        closes = 0
        close_next = False

        async def closed_once(self, *args, **kwargs):
            nonlocal closes, close_next
            if close_next:
                close_next = False
                closes += 1
                raise RedisConnectionError("Connection closed by server.")
            return await real_read(self, *args, **kwargs)

        monkeypatch.setattr(Connection, "read_response", closed_once)
        close_next = True
        assert await pool.ping() is True
        close_next = True
        async with pool.pipeline(transaction=True) as pipe:
            pipe.incr("arq-conn-closed-probe")
            pipe.delete("arq-conn-closed-probe")
            assert await pipe.execute() == [1, 1]
        assert closes == 2
    finally:
        await pool.aclose()


def test_arq_defaults_would_have_raised():
    """Guard the premise: arq's own defaults don't retry a timed-out connect."""
    from arq.connections import RedisSettings

    default = RedisSettings()
    assert default.conn_timeout == 1
    assert default.retry_on_timeout is False and default.retry is None
