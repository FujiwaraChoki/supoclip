"""A Redis blip while a worker claims or polls for jobs must not end the worker.

A render worker that lost Redis for a few seconds raised ``WatchError`` out of
arq's ``start_jobs``; the process exited and took its running renders with it.
"""

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest
from arq import worker as arq_worker
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from redis.exceptions import WatchError
from src.workers import arq_resilience


class FakePipeline:
    def __init__(self, redis, job_id_holder):
        self.redis = redis
        self.job = job_id_holder

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

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


async def test_a_real_worker_survives_a_dropped_watch_and_runs_the_job_on_the_next_poll():
    """Against real Redis and arq's own Worker (needs Redis on 127.0.0.1:6379)."""
    from uuid import uuid4

    import src.workers.tasks  # noqa: F401  (installs the patch)
    from arq import create_pool
    from arq.connections import RedisSettings

    ran = asyncio.Event()

    async def noop(ctx):
        ran.set()

    queue = f"test-resilience-{uuid4().hex}"
    settings = RedisSettings(host="127.0.0.1", port=6379)
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
        await pool.delete(queue)
        await pool.aclose()
