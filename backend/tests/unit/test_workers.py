"""Tests for ``src/workers/job_queue.py`` and ``src/workers/progress.py``.

Redis + arq clients are fully mocked via ``AsyncMock``/``MagicMock`` so tests
do not touch a real Redis instance.
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.workers import job_queue as jq
from src.workers.progress import ProgressTracker


# ---------------------------------------------------------------------------
# job_queue
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _reset_pool():
    """Ensure each test starts with a fresh JobQueue._pool."""
    jq.JobQueue._pool = None
    yield
    jq.JobQueue._pool = None


class TestRedisSettings:
    def test_pulls_from_config(self, monkeypatch):
        class FakeCfg:
            redis_host = "redis-host"
            redis_port = 6380
            redis_password = "s3cret"

        monkeypatch.setattr(jq, "get_config", lambda: FakeCfg())
        settings = jq._get_redis_settings()
        assert settings.host == "redis-host"
        assert settings.port == 6380
        assert settings.password == "s3cret"
        assert settings.database == 0


class TestJobQueuePool:
    @pytest.mark.asyncio
    async def test_get_pool_creates_and_caches(self, monkeypatch):
        fake_pool = AsyncMock(name="fake_pool")
        create_mock = AsyncMock(return_value=fake_pool)
        monkeypatch.setattr(jq, "create_pool", create_mock)

        first = await jq.JobQueue.get_pool()
        second = await jq.JobQueue.get_pool()
        assert first is fake_pool
        assert second is fake_pool
        # Cached after first call — create_pool only invoked once
        create_mock.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_close_pool_is_idempotent(self, monkeypatch):
        fake_pool = AsyncMock(name="fake_pool")
        fake_pool.close = AsyncMock()
        jq.JobQueue._pool = fake_pool

        await jq.JobQueue.close_pool()
        fake_pool.close.assert_awaited_once()
        assert jq.JobQueue._pool is None

        # Second call is a no-op
        await jq.JobQueue.close_pool()
        fake_pool.close.assert_awaited_once()


class TestEnqueueJob:
    @pytest.mark.asyncio
    async def test_enqueue_job_returns_id_and_uses_default_queue(self, monkeypatch):
        fake_job = MagicMock()
        fake_job.job_id = "job-xyz"
        fake_pool = MagicMock()
        fake_pool.enqueue_job = AsyncMock(return_value=fake_job)

        async def fake_create_pool(_settings):
            return fake_pool

        monkeypatch.setattr(jq, "create_pool", fake_create_pool)

        job_id = await jq.JobQueue.enqueue_job("process_task", 1, 2, foo="bar")
        assert job_id == "job-xyz"
        call = fake_pool.enqueue_job.await_args
        assert call.args[0] == "process_task"
        assert call.args[1:] == (1, 2)
        assert call.kwargs["_queue_name"] == jq.DEFAULT_QUEUE_NAME
        assert call.kwargs["foo"] == "bar"

    @pytest.mark.asyncio
    async def test_enqueue_custom_queue_name(self, monkeypatch):
        fake_job = MagicMock()
        fake_job.job_id = "j1"
        fake_pool = MagicMock()
        fake_pool.enqueue_job = AsyncMock(return_value=fake_job)
        monkeypatch.setattr(jq, "create_pool", AsyncMock(return_value=fake_pool))

        await jq.JobQueue.enqueue_job("fn", _queue_name="custom-q")
        call = fake_pool.enqueue_job.await_args
        assert call.kwargs["_queue_name"] == "custom-q"

    @pytest.mark.asyncio
    async def test_enqueue_job_returns_none_raises(self, monkeypatch):
        fake_pool = MagicMock()
        fake_pool.enqueue_job = AsyncMock(return_value=None)
        monkeypatch.setattr(jq, "create_pool", AsyncMock(return_value=fake_pool))

        with pytest.raises(RuntimeError, match="Failed to enqueue"):
            await jq.JobQueue.enqueue_job("fn")

    @pytest.mark.asyncio
    async def test_enqueue_job_missing_id_raises(self, monkeypatch):
        # arq can return a Job-like object without a job_id in edge cases.
        fake_job = MagicMock()
        fake_job.job_id = None
        fake_pool = MagicMock()
        fake_pool.enqueue_job = AsyncMock(return_value=fake_job)
        monkeypatch.setattr(jq, "create_pool", AsyncMock(return_value=fake_pool))

        with pytest.raises(RuntimeError, match="missing job ID"):
            await jq.JobQueue.enqueue_job("fn")


class TestEnqueueProcessingJob:
    @pytest.mark.asyncio
    async def test_uses_default_queue_regardless_of_mode(self, monkeypatch):
        fake_job = MagicMock()
        fake_job.job_id = "jx"
        fake_pool = MagicMock()
        fake_pool.enqueue_job = AsyncMock(return_value=fake_job)
        monkeypatch.setattr(jq, "create_pool", AsyncMock(return_value=fake_pool))

        await jq.JobQueue.enqueue_processing_job("fn", "fast", 1, 2)
        call = fake_pool.enqueue_job.await_args
        assert call.kwargs["_queue_name"] == jq.DEFAULT_QUEUE_NAME


class TestJobLookups:
    @pytest.mark.asyncio
    async def test_get_job_result_returns_result(self, monkeypatch):
        fake_job = MagicMock()
        fake_job.result = AsyncMock(return_value="ok")
        fake_pool = MagicMock()
        fake_pool.job = AsyncMock(return_value=fake_job)
        monkeypatch.setattr(jq, "create_pool", AsyncMock(return_value=fake_pool))

        assert await jq.JobQueue.get_job_result("j1") == "ok"

    @pytest.mark.asyncio
    async def test_get_job_result_none_when_missing(self, monkeypatch):
        fake_pool = MagicMock()
        fake_pool.job = AsyncMock(return_value=None)
        monkeypatch.setattr(jq, "create_pool", AsyncMock(return_value=fake_pool))

        assert await jq.JobQueue.get_job_result("nope") is None

    @pytest.mark.asyncio
    async def test_get_job_status_returns_status(self, monkeypatch):
        fake_job = MagicMock()
        fake_job.status = AsyncMock(return_value="complete")
        fake_pool = MagicMock()
        fake_pool.job = AsyncMock(return_value=fake_job)
        monkeypatch.setattr(jq, "create_pool", AsyncMock(return_value=fake_pool))

        assert await jq.JobQueue.get_job_status("j1") == "complete"

    @pytest.mark.asyncio
    async def test_get_job_status_none_when_missing(self, monkeypatch):
        fake_pool = MagicMock()
        fake_pool.job = AsyncMock(return_value=None)
        monkeypatch.setattr(jq, "create_pool", AsyncMock(return_value=fake_pool))

        assert await jq.JobQueue.get_job_status("nope") is None


# ---------------------------------------------------------------------------
# ProgressTracker
# ---------------------------------------------------------------------------


def _fake_redis():
    redis = MagicMock()
    redis.setex = AsyncMock()
    redis.publish = AsyncMock()
    redis.get = AsyncMock()
    return redis


class TestProgressTrackerUpdate:
    @pytest.mark.asyncio
    async def test_writes_setex_and_publishes(self):
        redis = _fake_redis()
        tracker = ProgressTracker(redis, "task-1")
        await tracker.update(50, "Processing", status="processing")

        assert redis.setex.await_count == 1
        setex_args = redis.setex.await_args.args
        assert setex_args[0] == "progress:task-1"
        assert setex_args[1] == 3600
        payload = json.loads(setex_args[2])
        assert payload["task_id"] == "task-1"
        assert payload["progress"] == 50
        assert payload["message"] == "Processing"
        assert payload["status"] == "processing"

        # Publish should mirror the same payload on the channel
        publish_args = redis.publish.await_args.args
        assert publish_args[0] == "progress:task-1"
        assert json.loads(publish_args[1]) == payload

    @pytest.mark.asyncio
    async def test_complete_sends_100_percent(self):
        redis = _fake_redis()
        tracker = ProgressTracker(redis, "t")
        await tracker.complete()
        payload = json.loads(redis.setex.await_args.args[2])
        assert payload["progress"] == 100
        assert payload["status"] == "completed"

    @pytest.mark.asyncio
    async def test_error_sends_zero_with_status(self):
        redis = _fake_redis()
        tracker = ProgressTracker(redis, "t")
        await tracker.error("boom")
        payload = json.loads(redis.setex.await_args.args[2])
        assert payload["progress"] == 0
        assert payload["status"] == "error"
        assert payload["message"] == "boom"


class TestProgressTrackerGet:
    @pytest.mark.asyncio
    async def test_returns_none_when_empty(self):
        redis = _fake_redis()
        redis.get = AsyncMock(return_value=None)
        tracker = ProgressTracker(redis, "t")
        assert await tracker.get() is None

    @pytest.mark.asyncio
    async def test_decodes_json(self):
        redis = _fake_redis()
        redis.get = AsyncMock(return_value=json.dumps({"progress": 42}))
        tracker = ProgressTracker(redis, "t")
        result = await tracker.get()
        assert result == {"progress": 42}


class TestProgressTrackerClipReady:
    @pytest.mark.asyncio
    async def test_publishes_clip_ready_event(self):
        redis = _fake_redis()
        tracker = ProgressTracker(redis, "t")
        await tracker.clip_ready(
            clip_index=2,
            total_clips=5,
            clip_data={"id": "c1", "path": "/tmp/c1.mp4"},
        )
        publish = redis.publish.await_args
        assert publish.args[0] == "progress:t"
        payload = json.loads(publish.args[1])
        assert payload["event_type"] == "clip_ready"
        assert payload["clip_index"] == 2
        assert payload["total_clips"] == 5
        assert payload["clip"]["id"] == "c1"


class TestProgressTrackerSubscribe:
    @pytest.mark.asyncio
    async def test_yields_messages_and_cleans_up(self):
        # Build a fake pubsub object with an async listen() generator.
        async def fake_listen():
            yield {"type": "subscribe", "data": "ignored"}
            yield {"type": "message", "data": json.dumps({"progress": 10})}
            yield {"type": "message", "data": json.dumps({"progress": 20})}

        fake_pubsub = MagicMock()
        fake_pubsub.subscribe = AsyncMock()
        fake_pubsub.unsubscribe = AsyncMock()
        fake_pubsub.close = AsyncMock()
        fake_pubsub.listen = fake_listen

        redis = _fake_redis()
        redis.pubsub = MagicMock(return_value=fake_pubsub)

        gen = ProgressTracker.subscribe_to_progress(redis, "t1")
        received = []
        try:
            async for item in gen:
                received.append(item)
                if len(received) == 2:
                    break
        finally:
            await gen.aclose()

        assert received == [{"progress": 10}, {"progress": 20}]
        fake_pubsub.subscribe.assert_awaited_once_with("progress:t1")
        fake_pubsub.unsubscribe.assert_awaited_once_with("progress:t1")
        fake_pubsub.close.assert_awaited_once()
