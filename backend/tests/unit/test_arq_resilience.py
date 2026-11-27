"""A Redis blip while a worker claims or polls for jobs must not end the worker.

A render worker that lost Redis for a few seconds raised ``WatchError`` out of
arq's ``start_jobs``; the process exited and took its running renders with it.
"""

import asyncio
import logging
import os
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest
from arq import worker as arq_worker
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import WatchError
from src.workers import arq_resilience


class FakePipeline:
    connection = None
    watching = False

    def __init__(self, redis, job_id_holder):
        self.redis = redis
        self.job = job_id_holder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def reset(self):
        pass

    async def watch(self, key):
        self.job["id"] = key.rsplit(":", 1)[-1]
        if self.job["id"] in self.redis.fail_watch:
            # What redis-py raises when the connection drops during a WATCH.
            raise WatchError(
                "A ConnectionError occurred on while watching one or more keys"
            )

    async def exists(self, key):
        if self.job["id"] in self.redis.timeout_exists:
            raise RedisTimeoutError("Timeout reading from redis")
        return self.job["id"] in self.redis.running

    async def zscore(self, queue, job_id):
        return self.redis.scores.get(job_id)

    def multi(self):
        pass

    def psetex(self, key, ms, value):
        pass

    async def execute(self):
        if self.job["id"] in self.redis.lose_race:
            raise WatchError("Watched variable changed.")
        self.redis.running.add(self.job["id"])


class FakeRedis:
    def __init__(self, scores):
        self.scores = scores
        self.running = set()
        self.fail_watch = set()
        self.timeout_exists = set()
        self.lose_race = set()

    def pipeline(self, transaction=True):
        return FakePipeline(self, {})


def make_worker(redis, max_jobs=4):
    started = []
    loop = asyncio.get_running_loop()

    async def run_job(job_id, score):
        started.append((job_id, score))
        await asyncio.sleep(3600)

    worker = SimpleNamespace(
        pool=redis,
        queue_name="supoclip_tasks",
        in_progress_timeout_s=300,
        max_jobs=max_jobs,
        sem=asyncio.BoundedSemaphore(max_jobs + 1),
        job_counter=0,
        tasks={},
        loop=loop,
        run_job=run_job,
    )

    def release():
        worker.job_counter -= 1
        worker.sem.release()

    worker._release_sem_dec_counter_on_complete = release
    return worker, started


def past_score():
    return time.time() * 1000 - 1000


async def test_a_redis_error_during_a_claim_gives_the_slot_back_and_keeps_the_worker_running(
    caplog,
):
    redis = FakeRedis({"a": past_score(), "b": past_score()})
    redis.fail_watch.add("a")
    worker, started = make_worker(redis)

    with caplog.at_level(logging.WARNING):
        await arq_resilience.start_jobs(worker, [b"a", b"b"])

    assert worker.job_counter == 0
    assert worker.sem._value == worker.max_jobs + 1
    assert worker.tasks == {}
    assert started == []
    assert "it stays queued for the next poll" in caplog.text


async def test_a_read_timeout_inside_the_watch_is_handled_the_same_way():
    redis = FakeRedis({"a": past_score()})
    redis.timeout_exists.add("a")
    worker, _ = make_worker(redis)

    await arq_resilience.start_jobs(worker, [b"a"])

    assert worker.job_counter == 0
    assert worker.sem._value == worker.max_jobs + 1


async def test_a_job_claimed_by_another_worker_is_skipped_and_the_next_one_starts():
    redis = FakeRedis({"a": past_score(), "b": past_score(), "c": past_score()})
    redis.lose_race.add("a")
    redis.running.add("b")
    worker, started = make_worker(redis)

    await arq_resilience.start_jobs(worker, [b"a", b"b", b"c"])
    await asyncio.sleep(0)

    assert set(worker.tasks) == {"c"}
    assert worker.job_counter == 1
    assert started == [("c", int(redis.scores["c"]))]
    for task in worker.tasks.values():
        task.cancel()


async def test_a_job_deferred_into_the_future_is_left_alone():
    redis = FakeRedis({"a": time.time() * 1000 + 60_000})
    worker, _ = make_worker(redis)

    await arq_resilience.start_jobs(worker, [b"a"])

    assert worker.tasks == {}
    assert worker.job_counter == 0


async def test_a_full_worker_takes_nothing():
    redis = FakeRedis({"a": past_score()})
    worker, _ = make_worker(redis, max_jobs=1)
    worker.job_counter = 1

    await arq_resilience.start_jobs(worker, [b"a"])

    assert worker.tasks == {}
    assert worker.sem._value == worker.max_jobs + 1


async def test_a_redis_error_anywhere_in_a_poll_is_logged_and_polling_continues(
    monkeypatch, caplog
):
    calls = []

    async def failing_poll(self):
        calls.append(self)
        raise RedisConnectionError("Connection closed by server.")

    monkeypatch.setattr(arq_resilience, "_original_poll_iteration", failing_poll)
    worker = SimpleNamespace(loop=asyncio.get_running_loop(), job_counter=0)
    worker._supoclip_watchdog = "running"  # keep the watchdog out of this test

    with caplog.at_level(logging.WARNING):
        await arq_resilience._poll_iteration(worker)
        await arq_resilience._poll_iteration(worker)

    assert len(calls) == 2
    assert "polling again" in caplog.text
    assert worker._supoclip_last_poll <= time.monotonic()


async def test_other_errors_in_a_poll_still_surface(monkeypatch):
    async def broken_poll(self):
        raise KeyError("bug")

    monkeypatch.setattr(arq_resilience, "_original_poll_iteration", broken_poll)
    worker = SimpleNamespace(loop=asyncio.get_running_loop(), job_counter=0)
    worker._supoclip_watchdog = "running"

    with pytest.raises(KeyError):
        await arq_resilience._poll_iteration(worker)


async def test_a_stalled_poll_loop_is_reported_once_with_where_it_waits(
    monkeypatch, caplog
):
    monkeypatch.setattr(arq_resilience, "STALL_WARNING_SECONDS", 0.05)
    monkeypatch.setattr(arq_resilience, "_WATCHDOG_INTERVAL_SECONDS", 0.02)

    async def stuck():
        await asyncio.sleep(3600)

    main_task = asyncio.get_running_loop().create_task(stuck())
    worker = SimpleNamespace(
        job_counter=2, main_task=main_task, _supoclip_last_poll=time.monotonic()
    )

    with caplog.at_level(logging.ERROR):
        watchdog = asyncio.get_running_loop().create_task(
            arq_resilience._watch_polling(worker)
        )
        await asyncio.sleep(0.3)
        watchdog.cancel()
        main_task.cancel()

    reports = [r for r in caplog.records if "has not finished a poll" in r.getMessage()]
    assert len(reports) == 1
    assert "stuck" in reports[0].getMessage()


def test_every_worker_process_installs_the_patch():
    import src.workers.ai_worker
    import src.workers.cron_worker
    import src.workers.tasks  # noqa: F401

    assert arq_worker.Worker.start_jobs is arq_resilience.start_jobs
    assert arq_worker.Worker._poll_iteration is arq_resilience._poll_iteration
    arq_resilience.install()
    assert arq_worker.Worker.start_jobs is arq_resilience.start_jobs


def test_the_patched_start_jobs_matches_the_arq_version_it_was_copied_from():
    """Re-check ``start_jobs`` against arq's own before upgrading arq."""
    import arq

    assert arq.VERSION == "0.28.0"


def _redis_settings(port=None):
    from arq.connections import RedisSettings

    return RedisSettings(
        host=os.environ.get("REDIS_HOST", "127.0.0.1"),
        port=port or int(os.environ.get("REDIS_PORT", "6379")),
    )


async def test_a_real_worker_survives_a_dropped_watch_and_runs_the_job_on_the_next_poll():
    """Against real Redis (REDIS_HOST/REDIS_PORT) and arq's own Worker."""
    import src.workers.tasks  # noqa: F401  (installs the patch)
    from arq import create_pool

    ran = asyncio.Event()

    async def noop(ctx):
        ran.set()

    queue = f"test-resilience-{uuid4().hex}"
    settings = _redis_settings()
    pool = await create_pool(settings, default_queue_name=queue)
    worker = arq_worker.Worker(
        functions=[arq_worker.func(noop, name="noop")],
        redis_pool=pool,
        queue_name=queue,
        max_jobs=2,
        handle_signals=False,
    )
    try:
        await pool.enqueue_job("noop", _queue_name=queue)

        real_pipeline = pool.pipeline
        dropped = []

        def flaky_pipeline(*args, **kwargs):
            pipe = real_pipeline(*args, **kwargs)
            if not dropped:
                dropped.append(True)

                async def watch(*keys):
                    raise WatchError(
                        "A ConnectionError occurred on while watching one or more keys"
                    )

                pipe.watch = watch
            return pipe

        pool.pipeline = flaky_pipeline
        await worker._poll_iteration()  # arq 0.28 alone raises here
        assert worker.job_counter == 0
        assert await pool.zcard(queue) == 1

        await worker._poll_iteration()
        await asyncio.wait_for(ran.wait(), 5)
    finally:
        for task in worker.tasks.values():
            task.cancel()
        watchdog = getattr(worker, "_supoclip_watchdog", None)
        if watchdog is not None:
            watchdog.cancel()
        await pool.delete(queue)
        await pool.aclose()


# --- A Redis command that never answers ------------------------------------
#
# The worker reaches Redis through a TCP proxy that can stop answering on the
# connections it already has (a half-open connection), or on the one that
# sends a job claim's EXEC: drop Redis's reply ("drop_reply", the claim was
# applied but the worker never hears it) or hold the EXEC itself ("hold", it
# reaches Redis later). New connections always pass.

DEADLINE = 0.5


class _Link:
    def __init__(self, upstream_writer):
        self.mode = "pass"
        self.upstream_writer = upstream_writer
        self.held = []
        self.replies = bytearray()


class RedisProxy:
    def __init__(self, upstream_port):
        self.upstream_port = upstream_port
        self.links = []
        self.tasks = []
        self.trap = None

    async def start(self):
        self.server = await asyncio.start_server(self._accept, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self

    async def _accept(self, reader, writer):
        upstream_reader, upstream_writer = await asyncio.open_connection(
            os.environ.get("REDIS_HOST", "127.0.0.1"), self.upstream_port
        )
        link = _Link(upstream_writer)
        self.links.append(link)
        loop = asyncio.get_running_loop()
        self.tasks += [
            loop.create_task(self._to_redis(link, reader, upstream_writer)),
            loop.create_task(self._to_client(link, upstream_reader, writer)),
        ]

    async def _to_redis(self, link, reader, writer):
        try:
            while data := await reader.read(65536):
                # redis-py sends MULTI, the PSETEX and EXEC in one write.
                if self.trap and b"EXEC" in data and b"arq:in-progress:" in data:
                    link.mode, self.trap = self.trap, None
                if link.mode == "blackhole":
                    continue
                if link.mode == "hold":
                    link.held.append(data)
                    continue
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        if not link.held:  # a held EXEC still has to reach Redis
            writer.close()

    async def _to_client(self, link, reader, writer):
        try:
            while data := await reader.read(65536):
                if link.mode in ("blackhole", "drop_reply"):
                    continue
                if link.mode == "hold":
                    link.replies += data
                    continue
                writer.write(data)
                await writer.drain()
        except (ConnectionError, OSError):
            pass
        writer.close()

    def blackhole_open_connections(self):
        for link in self.links:
            link.mode = "blackhole"

    async def deliver_held(self):
        """Let a held EXEC reach Redis; Redis's replies to it."""
        (link,) = [link for link in self.links if link.held]
        for data in link.held:
            link.upstream_writer.write(data)
        link.held = []
        await link.upstream_writer.drain()
        # +OK (MULTI), +QUEUED (PSETEX), then EXEC's reply.
        while link.replies.count(b"\r\n") < 3:
            await asyncio.sleep(0.01)
        return bytes(link.replies)

    async def close(self):
        self.server.close()
        for task in self.tasks:
            task.cancel()
        for link in self.links:
            link.upstream_writer.close()


class LiveWorker:
    """arq's Worker, patched, polling Redis through a ``RedisProxy``."""

    def __init__(self, proxy, direct, queue):
        self.proxy, self.direct, self.queue = proxy, direct, queue
        self.runs = []

    async def start(self, *, run=True):
        from arq import create_pool

        arq_resilience.install()

        async def job(ctx):
            self.runs.append(ctx["job_id"])

        self.pool = await create_pool(_redis_settings(self.proxy.port))
        self.worker = arq_worker.Worker(
            functions=[arq_worker.func(job, name="job")],
            redis_pool=self.pool,
            queue_name=self.queue,
            max_jobs=2,
            poll_delay=0.05,
            health_check_interval=1,
            handle_signals=False,
        )
        if run:
            self.worker.main_task = asyncio.get_running_loop().create_task(
                self.worker.main()
            )
        return self

    async def wait_until(self, condition, timeout):
        deadline = time.monotonic() + timeout
        while not await condition():
            assert time.monotonic() < deadline, "timed out"
            await asyncio.sleep(0.02)

    def assert_no_slot_held(self):
        assert self.worker.job_counter == 0
        assert self.worker.sem._value == self.worker.max_jobs + 1

    async def close(self):
        worker = self.worker
        for task in (
            worker.main_task,
            getattr(worker, "_supoclip_watchdog", None),
            *worker.tasks.values(),
        ):
            if task is not None:
                task.cancel()
        await asyncio.sleep(0)
        keys = await self.direct.keys(f"{self.queue}*")
        if keys:
            await self.direct.delete(*keys)
        await self.pool.aclose()


@pytest.fixture
async def live(monkeypatch):
    from arq import create_pool

    monkeypatch.setattr(arq_resilience, "POLL_DEADLINE_SECONDS", DEADLINE, raising=False)
    direct = await create_pool(_redis_settings())
    proxy = await RedisProxy(_redis_settings().port).start()
    workers = []

    async def make(*, run=True):
        queue = f"test-poll-deadline-{uuid4().hex}"
        live_worker = await LiveWorker(proxy, direct, queue).start(run=run)
        workers.append(live_worker)
        return live_worker

    yield make
    for live_worker in workers:
        await live_worker.close()
    await proxy.close()
    await direct.aclose()


async def _enqueue(live_worker):
    job = await live_worker.direct.enqueue_job("job", _queue_name=live_worker.queue)
    return job.job_id


async def _ran_once(live_worker, job_id):
    tasks = live_worker.worker.tasks.values()
    return live_worker.runs == [job_id] and all(task.done() for task in tasks)


async def _tries(live_worker, job_id):
    from arq.jobs import Job

    return (await Job(job_id, live_worker.direct).result_info()).job_try


async def test_a_poll_stuck_on_a_silent_connection_is_abandoned_and_the_worker_recovers(
    live, caplog
):
    live_worker = await live()
    health_key = live_worker.queue + ":health-check"
    await live_worker.wait_until(lambda: live_worker.direct.exists(health_key), 5)

    # Every connection the worker has stops answering; Redis itself is fine.
    live_worker.proxy.blackhole_open_connections()
    await live_worker.direct.delete(health_key)
    job_id = await _enqueue(live_worker)

    with caplog.at_level(logging.WARNING):
        await live_worker.wait_until(lambda: _ran_once(live_worker, job_id), DEADLINE + 4)
        await live_worker.wait_until(lambda: live_worker.direct.exists(health_key), 3)

    assert "abandoned it and reconnecting" in caplog.text
    live_worker.assert_no_slot_held()
    assert await _tries(live_worker, job_id) == 1


async def test_a_claim_whose_reply_is_lost_is_released_and_its_job_runs_exactly_once(
    live, caplog
):
    live_worker = await live()
    # Redis applies the claim, but the worker never hears back.
    live_worker.proxy.trap = "drop_reply"
    job_id = await _enqueue(live_worker)

    with caplog.at_level(logging.WARNING):
        await live_worker.wait_until(lambda: _ran_once(live_worker, job_id), DEADLINE + 4)

    assert live_worker.proxy.trap is None, "the claim was never cut off"
    assert f"Released job {job_id}" in caplog.text
    await asyncio.sleep(0.3)  # a second run would show up here
    assert live_worker.runs == [job_id]
    assert await _tries(live_worker, job_id) == 1
    assert not await live_worker.direct.exists(f"arq:in-progress:{job_id}")
    live_worker.assert_no_slot_held()


async def test_a_claim_that_reaches_redis_after_it_was_abandoned_does_not_block_the_job(
    live,
):
    live_worker = await live(run=False)
    worker = live_worker.worker
    live_worker.proxy.trap = "hold"
    job_id = await _enqueue(live_worker)

    # The claim's EXEC is still on its way when the poll gives up on it.
    await asyncio.wait_for(worker._poll_iteration(), DEADLINE + 3)
    assert live_worker.proxy.trap is None, "the claim was never cut off"
    assert worker.tasks == {}
    live_worker.assert_no_slot_held()

    # The next poll releases it first; then the old EXEC arrives.
    await asyncio.wait_for(arq_resilience._settle_abandoned_claims(worker), 3)
    replies = await asyncio.wait_for(live_worker.proxy.deliver_held(), 3)
    assert replies.endswith(b"*-1\r\n"), replies  # EXEC refused: WATCH failed
    assert not await live_worker.direct.exists(f"arq:in-progress:{job_id}")

    await asyncio.wait_for(worker._poll_iteration(), DEADLINE + 3)
    await live_worker.wait_until(lambda: _ran_once(live_worker, job_id), 3)
    assert await _tries(live_worker, job_id) == 1
    live_worker.assert_no_slot_held()


async def test_a_cancelled_claim_gives_its_slot_back():
    redis = FakeRedis({"a": past_score()})
    worker, started = make_worker(redis)
    entered = asyncio.Event()

    async def hang(*args):
        entered.set()
        await asyncio.sleep(3600)

    pipe = FakePipeline(redis, {})
    pipe.zscore = hang
    redis.pipeline = lambda transaction=True: pipe

    claiming = asyncio.get_running_loop().create_task(
        arq_resilience.start_jobs(worker, [b"a"])
    )
    await asyncio.wait_for(entered.wait(), 2)
    claiming.cancel()
    with pytest.raises(asyncio.CancelledError):
        await claiming

    assert worker.job_counter == 0
    assert worker.sem._value == worker.max_jobs + 1
    assert started == []
    # Cut off before EXEC: nothing to release later.
    assert not getattr(worker, "_supoclip_unsettled_claims", {})


async def test_a_failed_claim_returns_its_connection_to_the_pool(monkeypatch):
    """A timeout on UNWATCH used to leave the claim's connection checked out
    ("Unclosed Pipeline client")."""
    from arq import create_pool
    from redis.asyncio.connection import Connection

    queue = f"test-claim-leak-{uuid4().hex}"
    pool = await create_pool(_redis_settings(), default_queue_name=queue)
    worker, _ = make_worker(pool)
    worker.queue_name = queue
    real_pipeline = pool.pipeline

    def timing_out_pipeline(*args, **kwargs):
        pipe = real_pipeline(*args, **kwargs)

        async def zscore(*args):
            raise RedisTimeoutError("Timeout reading from redis")

        pipe.zscore = zscore
        return pipe

    real_send = Connection.send_command

    async def send_command(self, *args, **kwargs):
        if args and args[0] == "UNWATCH":
            raise RedisTimeoutError("Timeout reading from redis")
        return await real_send(self, *args, **kwargs)

    monkeypatch.setattr(Connection, "send_command", send_command)
    pool.pipeline = timing_out_pipeline
    try:
        await pool.zadd(queue, {"a": past_score()})
        await arq_resilience.start_jobs(worker, [b"a"])

        assert pool.connection_pool._in_use_connections == set()
        assert worker.job_counter == 0
        assert worker.sem._value == worker.max_jobs + 1
    finally:
        pool.pipeline = real_pipeline
        monkeypatch.undo()
        await pool.delete(queue)
        await pool.aclose()


async def test_a_claim_that_cannot_be_released_never_stops_the_poll(monkeypatch, caplog):
    from redis.exceptions import NoPermissionError

    polls = []

    async def poll(self):
        polls.append(self)

    class Pool:
        error = RedisConnectionError("Connection closed by server.")

        async def eval(self, *args):
            raise self.error

    monkeypatch.setattr(arq_resilience, "_original_poll_iteration", poll)
    worker = SimpleNamespace(loop=asyncio.get_running_loop(), job_counter=0, pool=Pool())
    worker._supoclip_watchdog = "running"
    worker._supoclip_unsettled_claims = {"a": b"token"}

    with caplog.at_level(logging.WARNING):
        await arq_resilience._poll_iteration(worker)
        assert worker._supoclip_unsettled_claims == {"a": b"token"}  # tried again next poll

        worker.pool.error = NoPermissionError("this user has no permissions to run 'eval'")
        await arq_resilience._poll_iteration(worker)

    assert worker._supoclip_unsettled_claims == {}
    assert len(polls) == 2
    assert "Could not release the claim on job a" in caplog.text
