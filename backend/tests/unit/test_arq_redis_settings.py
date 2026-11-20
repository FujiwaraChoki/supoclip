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
from src.workers.redis_settings import arq_redis_settings, arq_socket_options, use_socket_options
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


class _BlackholeProxy:
    """TCP proxy to Redis whose open connections can go silent, the way a
    proxy's can when Redis restarts behind it: no data, no close."""

    def __init__(self, port):
        self.port = port
        self.silent = set()
        self._open = set()
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    def go_silent(self):
        self.silent.update(self._open)

    async def _serve(self, client_reader, client_writer):
        upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", self.port)
        key = object()
        self._open.add(key)

        async def pipe(reader, writer):
            while data := await reader.read(65536):
                if key in self.silent:
                    await asyncio.Event().wait()
                writer.write(data)
                await writer.drain()

        try:
            await asyncio.gather(pipe(client_reader, upstream_writer), pipe(upstream_reader, client_writer))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            client_writer.close()
            upstream_writer.close()

    async def close(self):
        self.server.close()


def _config(**overrides):
    from src.config import Config

    config = Config()
    for name, value in overrides.items():
        setattr(config, name, value)
    return config


async def _run_startup(name, pool, monkeypatch):
    if name == "video worker":
        import src.workers.tasks as tasks

        async def nothing(*_args, **_kwargs):
            return None

        monkeypatch.setattr(tasks, "check_media_volume", nothing)
        monkeypatch.setattr(tasks, "_maintain_scratch_forever", nothing)
        ctx = {"redis": pool}
        await WorkerSettings.on_startup(ctx)
        await ctx["scratch_maintenance"]
    elif name == "cron worker":
        await CronWorkerSettings.on_startup({"redis": pool})
    else:
        await AIWorkerSettings.on_startup({"redis": pool})


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["video worker", "cron worker", "ai worker", "enqueue"])
async def test_every_arq_process_bounds_reads_and_keeps_connections_alive(name, monkeypatch):
    """A connection that dies silently must not hang the worker's poll."""
    from src.workers.job_queue import JobQueue

    if name == "enqueue":
        monkeypatch.setattr(JobQueue, "_pool", None)
        pool = await JobQueue.get_pool()
    else:
        pool = await create_pool(_all_settings()[name])
        await _run_startup(name, pool, monkeypatch)
    try:
        kwargs = pool.connection_pool.connection_kwargs
        assert 0 < kwargs.get("socket_timeout", 0) <= 60
        assert kwargs.get("socket_keepalive") is True
        await pool.ping()
        connection = next(iter(pool.connection_pool._available_connections))
        assert connection.socket_timeout == kwargs["socket_timeout"]
    finally:
        await pool.aclose()
        monkeypatch.setattr(JobQueue, "_pool", None)


def test_socket_timeout_comes_from_the_environment(monkeypatch):
    from src.config import Config

    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT_SECONDS", "45")
    assert arq_socket_options(Config())["socket_timeout"] == 45
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT_SECONDS", "nonsense")
    assert arq_socket_options(Config())["socket_timeout"] == 30
    monkeypatch.setenv("REDIS_SOCKET_TIMEOUT_SECONDS", "0")
    assert arq_socket_options(Config())["socket_timeout"] == 1


@pytest.mark.asyncio
async def test_a_silently_dead_connection_times_out_and_reconnects():
    """Redis restarted behind a proxy: the pooled connection stays open but
    nothing comes back. The worker's next poll must give up and reconnect
    instead of waiting until the OS drops the socket."""
    config = _config()
    proxy = _BlackholeProxy(config.redis_port)
    proxied = _config(redis_host="127.0.0.1", redis_port=await proxy.start(), redis_socket_timeout_seconds=1)
    pool = await create_pool(arq_redis_settings(proxied))
    try:
        await use_socket_options(pool, proxied)
        await pool.zadd("arq-dead-conn-probe", {"job": 1})
        proxy.go_silent()
        found = await asyncio.wait_for(pool.zrangebyscore("arq-dead-conn-probe", 0, 2), 15)
        assert found == [b"job"]
    finally:
        await pool.delete("arq-dead-conn-probe")
        await pool.aclose()
        await proxy.close()


@pytest.mark.asyncio
async def test_each_reconnect_is_logged(monkeypatch, caplog):
    """An outage the worker rides out must still show up in its logs."""
    pool = await create_pool(WorkerSettings.redis_settings)
    try:
        real_read = Connection.read_response
        close_next = True

        async def closed_once(self, *args, **kwargs):
            nonlocal close_next
            if close_next:
                close_next = False
                raise RedisConnectionError("Connection closed by server.")
            return await real_read(self, *args, **kwargs)

        monkeypatch.setattr(Connection, "read_response", closed_once)
        with caplog.at_level("WARNING", logger="src.workers.redis_settings"):
            assert await pool.ping() is True
        messages = [r.getMessage() for r in caplog.records if r.name == "src.workers.redis_settings"]
        assert len(messages) == 1
        assert "Connection closed by server." in messages[0]
        assert "retry 1 of" in messages[0]
    finally:
        await pool.aclose()


def _interrupt_transactions(monkeypatch, *, after_exec: bool):
    """The connection drops on the next MULTI/EXEC, before it reaches Redis or
    after Redis ran it but before the reply came back."""
    from redis.asyncio.client import Pipeline

    calls = {"interrupted": 0}
    if after_exec:
        real = Pipeline.parse_response

        async def reply_lost(self, connection, command_name, **options):
            # "_" is the EXEC reply: Redis has run the transaction.
            if command_name == "_" and calls["interrupted"] == 0:
                calls["interrupted"] += 1
                await real(self, connection, command_name, **options)
                raise RedisConnectionError("Connection closed by server.")
            return await real(self, connection, command_name, **options)

        monkeypatch.setattr(Pipeline, "parse_response", reply_lost)
    else:
        real = Pipeline._execute_transaction

        async def never_sent(self, *args, **kwargs):
            if calls["interrupted"] == 0:
                calls["interrupted"] += 1
                raise RedisConnectionError("Connection closed by server.")
            return await real(self, *args, **kwargs)

        monkeypatch.setattr(Pipeline, "_execute_transaction", never_sent)
    return calls


@pytest.mark.asyncio
async def test_an_enqueue_cut_off_by_the_connection_is_not_reported_as_a_duplicate(monkeypatch):
    """arq WATCHes the job key while enqueueing; a dropped connection then
    surfaces as WatchError and arq returns None, which used to read as
    "already queued", so exports and social posts were never run."""
    from arq.constants import job_key_prefix

    from src.workers.job_queue import JobQueue

    monkeypatch.setattr(JobQueue, "_pool", None)
    pool = await JobQueue.get_pool()
    job_id = "arq-enqueue-interrupted-probe"
    try:
        await pool.delete(job_key_prefix + job_id)
        calls = _interrupt_transactions(monkeypatch, after_exec=False)
        assert await JobQueue.enqueue_job("probe", _job_id=job_id, _queue_name="arq-probe-queue") == job_id
        assert calls["interrupted"] == 1
        assert await pool.exists(job_key_prefix + job_id)
    finally:
        await pool.delete(job_key_prefix + job_id, "arq-probe-queue")
        await pool.aclose()
        monkeypatch.setattr(JobQueue, "_pool", None)


@pytest.mark.asyncio
async def test_an_enqueue_whose_reply_was_lost_is_a_duplicate_not_a_second_job(monkeypatch):
    from arq.constants import job_key_prefix

    from src.workers.job_queue import DuplicateJobError, JobQueue

    monkeypatch.setattr(JobQueue, "_pool", None)
    pool = await JobQueue.get_pool()
    job_id = "arq-enqueue-reply-lost-probe"
    try:
        await pool.delete(job_key_prefix + job_id, "arq-probe-queue")
        calls = _interrupt_transactions(monkeypatch, after_exec=True)
        with pytest.raises(DuplicateJobError):
            await JobQueue.enqueue_job("probe", _job_id=job_id, _queue_name="arq-probe-queue")
        assert calls["interrupted"] == 1
        assert await pool.zcard("arq-probe-queue") == 1
    finally:
        await pool.delete(job_key_prefix + job_id, "arq-probe-queue")
        await pool.aclose()
        monkeypatch.setattr(JobQueue, "_pool", None)


def test_the_api_enqueue_pool_has_a_request_sized_budget():
    """A request must not wait out the workers' ~17-77s of reconnects: the
    gateway would time it out first."""
    config = _config()
    serving = arq_redis_settings(config, serving_requests=True)
    assert serving.conn_timeout <= 3
    assert serving.retry._retries <= 2
    assert serving.retry_on_timeout is True and RedisConnectionError in serving.retry_on_error
    assert arq_socket_options(config, serving_requests=True)["socket_timeout"] <= 5
    # Workers keep their patient budget.
    worker = arq_redis_settings(config)
    assert worker.conn_timeout >= 10 and worker.retry._retries >= 3
    assert arq_socket_options(config)["socket_timeout"] == 30


@pytest.mark.asyncio
async def test_an_api_command_fails_within_seconds_once_redis_is_gone(monkeypatch):
    """Redis goes away behind the API's pooled connection: the next enqueue
    errors out quickly instead of hanging the request."""
    from src.workers.job_queue import JobQueue

    config = _config()
    proxy = _BlackholeProxy(config.redis_port)
    proxied = _config(redis_host="127.0.0.1", redis_port=await proxy.start(), redis_socket_timeout_seconds=1)
    monkeypatch.setattr("src.workers.job_queue.get_config", lambda: proxied)
    monkeypatch.setattr(JobQueue, "_pool", None)
    monkeypatch.setattr(JobQueue, "serving_requests", True)
    pool = await JobQueue.get_pool()
    try:
        await pool.ping()
        proxy.go_silent()
        await proxy.close()  # reconnects are now refused
        started = asyncio.get_running_loop().time()
        with pytest.raises((RedisConnectionError, asyncio.TimeoutError, OSError)):
            await asyncio.wait_for(pool.zrangebyscore("arq-api-budget-probe", 0, 1), 20)
        assert asyncio.get_running_loop().time() - started < 6
    finally:
        await pool.aclose()
        monkeypatch.setattr(JobQueue, "_pool", None)


@pytest.mark.asyncio
async def test_the_api_builds_its_queue_pool_with_the_request_budget(monkeypatch):
    import contextlib
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    import src.main as main
    from src.config import Config, set_config_override

    async def nothing(*_args, **_kwargs):
        return None

    @contextlib.asynccontextmanager
    async def no_session():
        yield None

    monkeypatch.setattr(main, "init_db", nothing)
    monkeypatch.setattr(main, "close_db", nothing)
    monkeypatch.setattr(main, "load_runtime_settings_cache", nothing)
    monkeypatch.setattr(main, "AsyncSessionLocal", no_session)

    class Queue:
        serving_requests = False
        built_for_requests = None

        @classmethod
        async def get_pool(cls):
            cls.built_for_requests = cls.serving_requests
            return SimpleNamespace(set=AsyncMock())

        @classmethod
        async def close_pool(cls):
            return None

    app = main.create_app(config=Config(), queue_adapter=Queue)
    try:
        async with app.router.lifespan_context(app):
            pass
    finally:
        set_config_override(None)
    assert Queue.built_for_requests is True
