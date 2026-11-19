"""Video workers record which jobs run on their machine, for the render autoscaler."""

import pytest

from src.workers import host_jobs
from src.workers.tasks import WorkerSettings, on_video_job_begin


class _Redis:
    def __init__(self, fail=False):
        self.sets: dict[str, dict[str, float]] = {}
        self.expiry: dict[str, int] = {}
        self.fail = fail

    async def zadd(self, key, mapping):
        if self.fail:
            raise ConnectionError("redis down")
        self.sets.setdefault(key, {}).update(mapping)

    async def expire(self, key, seconds):
        self.expiry[key] = seconds

    async def zrem(self, key, member):
        if self.fail:
            raise ConnectionError("redis down")
        self.sets.get(key, {}).pop(member, None)

    async def zcount(self, key, low, high):
        assert high == "+inf"
        return sum(1 for score in self.sets.get(key, {}).values() if score >= low)


@pytest.fixture(autouse=True)
def _host(monkeypatch):
    monkeypatch.setenv("RENDER_HOST_NAME", "cloud-worker-a")


@pytest.mark.asyncio
async def test_a_job_is_listed_under_its_host_until_it_ends():
    redis = _Redis()
    ctx = {"job_id": "job-1", "redis": redis}

    await host_jobs.record_job_start(ctx)
    assert await host_jobs.running_jobs(redis, "cloud-worker-a") == 1
    assert await host_jobs.running_jobs(redis, "cloud-worker-b") == 0
    assert redis.expiry[host_jobs.host_jobs_key("cloud-worker-a")] == host_jobs.STALE_AFTER_SECONDS

    await host_jobs.record_job_end(ctx)
    assert await host_jobs.running_jobs(redis, "cloud-worker-a") == 0


@pytest.mark.asyncio
async def test_entries_from_a_dead_worker_stop_counting_after_the_job_timeout():
    redis = _Redis()
    key = host_jobs.host_jobs_key("cloud-worker-a")
    redis.sets[key] = {"old": 1_000.0, "fresh": 20_000.0}

    now = 1_000.0 + host_jobs.STALE_AFTER_SECONDS + 1
    assert await host_jobs.running_jobs(redis, "cloud-worker-a", now=now) == 1


@pytest.mark.asyncio
async def test_redis_errors_never_fail_the_job():
    redis = _Redis(fail=True)
    ctx = {"job_id": "job-1", "redis": redis}
    await host_jobs.record_job_start(ctx)
    await host_jobs.record_job_end(ctx)


@pytest.mark.asyncio
async def test_the_video_worker_records_jobs_and_still_measures_pickup_delay(monkeypatch):
    calls = []

    async def fake_delay(ctx):
        calls.append(("delay", ctx["job_id"]))

    monkeypatch.setattr("src.workers.tasks.on_video_job_start", fake_delay)
    redis = _Redis()
    await on_video_job_begin({"job_id": "job-9", "redis": redis})

    assert calls == [("delay", "job-9")]
    assert "job-9" in redis.sets[host_jobs.host_jobs_key("cloud-worker-a")]
    assert WorkerSettings.on_job_start is on_video_job_begin
    assert WorkerSettings.on_job_end is host_jobs.record_job_end
