"""An extra (cloud) video worker only takes jobs an always-on host has left waiting."""

import asyncio
import os
import secrets

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.utils import timestamp_ms
from arq.worker import Worker, func

from src.workers import overflow


def _settings():
    return RedisSettings(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", 0.0), ("0", 0.0), ("15", 15.0), (" 2.5 ", 2.5), ("-3", 0.0), ("soon", 0.0), ("nan", 0.0), ("inf", 0.0)],
)
def test_overflow_setting(raw, expected):
    assert overflow.overflow_after_seconds(raw) == expected


def test_overflow_is_off_by_default(monkeypatch):
    monkeypatch.delenv(overflow.OVERFLOW_ENV, raising=False)
    assert overflow.overflow_after_seconds() == 0.0


class _Pool:
    def __init__(self):
        self.calls = []

    async def zrangebyscore(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        return []


@pytest.mark.asyncio
async def test_only_reads_of_the_video_queue_are_held_back():
    pool = _Pool()
    overflow.take_only_overflow(pool, "videos", 10)
    now = timestamp_ms()

    await pool.zrangebyscore("videos", min=float("-inf"), start=0, num=5, max=now)
    await pool.zrangebyscore(b"videos", float("-inf"), "+inf")
    await pool.zrangebyscore("other", min=0, max=now)

    held, positional, other = pool.calls
    assert now - 10_000 - 1_000 <= held[2]["max"] <= now - 10_000
    assert held[2]["start"] == 0 and held[2]["num"] == 5
    assert positional[1][1] <= timestamp_ms() - 10_000
    assert other[2]["max"] == now


@pytest.mark.asyncio
async def test_a_new_job_waits_for_the_primary_then_the_overflow_worker_takes_it():
    queue = f"test-overflow-{secrets.token_hex(4)}"
    pool = await create_pool(_settings(), default_queue_name=queue)
    started: list[str] = []

    async def render(ctx, name):
        started.append(name)

    async def startup(ctx):
        overflow.take_only_overflow(ctx["redis"], queue, 1.5)

    try:
        await pool.enqueue_job("render", "old", _job_id=f"{queue}-old")
        await pool.enqueue_job("render", "new", _job_id=f"{queue}-new")
        # "old" became due 5 s ago: nobody took it, so the overflow worker should.
        await pool.zadd(queue, {f"{queue}-old": timestamp_ms() - 5_000})

        worker = Worker(
            functions=[func(render, name="render")], queue_name=queue, redis_pool=pool, poll_delay=0.05,
            on_startup=startup, handle_signals=False, max_jobs=4,
        )
        task = asyncio.create_task(worker.main())
        await asyncio.sleep(0.6)
        assert started == ["old"]

        # No starvation: once "new" has waited long enough, the overflow worker takes it too.
        for _ in range(60):
            if "new" in started:
                break
            await asyncio.sleep(0.1)
        assert started == ["old", "new"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.close()
    finally:
        keys = await pool.keys(f"*{queue}*")
        if keys:
            await pool.delete(*keys)
        await pool.aclose()


@pytest.mark.asyncio
async def test_a_primary_worker_takes_a_new_job_at_once(monkeypatch):
    monkeypatch.delenv(overflow.OVERFLOW_ENV, raising=False)
    queue = f"test-overflow-{secrets.token_hex(4)}"
    pool = await create_pool(_settings(), default_queue_name=queue)
    started: list[str] = []

    async def render(ctx, name):
        started.append(name)

    try:
        await pool.enqueue_job("render", "new", _job_id=f"{queue}-new")
        worker = Worker(
            functions=[func(render, name="render")], queue_name=queue, redis_pool=pool, poll_delay=0.05,
            handle_signals=False, max_jobs=4,
        )
        task = asyncio.create_task(worker.main())
        for _ in range(20):
            if started:
                break
            await asyncio.sleep(0.05)
        assert started == ["new"]
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await worker.close()
    finally:
        keys = await pool.keys(f"*{queue}*")
        if keys:
            await pool.delete(*keys)
        await pool.aclose()


@pytest.mark.asyncio
async def test_the_video_worker_holds_back_new_jobs_when_configured(monkeypatch):
    from src.workers import tasks

    monkeypatch.setattr(tasks, "check_media_volume", lambda redis: asyncio.sleep(0))
    monkeypatch.setattr(tasks, "_maintain_scratch_forever", lambda: asyncio.sleep(0))
    # The fake pool has no sockets to configure.
    monkeypatch.setattr("src.workers.redis_settings.use_socket_options", lambda redis: asyncio.sleep(0))
    for value, held in (("15", True), ("", False)):
        monkeypatch.setenv(overflow.OVERFLOW_ENV, value)
        pool = _Pool()
        ctx = {"redis": pool}
        await tasks.on_worker_startup(ctx)
        await ctx["scratch_maintenance"]
        now = timestamp_ms()
        await pool.zrangebyscore(tasks.WorkerSettings.queue_name, min=float("-inf"), max=now)
        assert (pool.calls[0][2]["max"] <= now - 15_000) is held
