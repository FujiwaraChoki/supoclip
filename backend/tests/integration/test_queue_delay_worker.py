"""A job enqueued with a backdated due time does not look delayed to a real worker."""

from datetime import datetime, timedelta, timezone

import pytest
from arq import create_pool
from arq.worker import Worker, func

from src.workers import queue_delay
from src.workers.job_queue import DEFAULT_QUEUE_NAME
from src.workers.queue_delay import QUEUE_DELAY_ALERT_KEY, on_video_job_start
from src.workers.tasks import WorkerSettings


class _Client:
    def __init__(self):
        self.calls = []

    def capture_exception(self, exc, distinct_id=None, properties=None):
        self.calls.append((exc, distinct_id, properties))


async def _noop(ctx):
    return "ok"


@pytest.mark.asyncio
async def test_backdated_defer_until_does_not_alert_or_take_the_slot(monkeypatch):
    client = _Client()
    monkeypatch.setattr(queue_delay, "get_posthog_client", lambda: client)
    redis = await create_pool(WorkerSettings.redis_settings)
    await redis.delete(QUEUE_DELAY_ALERT_KEY)
    try:
        await redis.enqueue_job(
            "_noop",
            _queue_name=DEFAULT_QUEUE_NAME,
            _defer_until=datetime.now(timezone.utc) - timedelta(seconds=1200),
        )
        worker = Worker(
            functions=[func(_noop, name="_noop")],
            queue_name=DEFAULT_QUEUE_NAME,
            redis_pool=redis,
            on_job_start=on_video_job_start,
            burst=True,
            poll_delay=0.05,
        )
        await worker.main()
        assert worker.jobs_complete == 1
        assert client.calls == []
        assert await redis.get(QUEUE_DELAY_ALERT_KEY) is None
    finally:
        await redis.delete(QUEUE_DELAY_ALERT_KEY)
        await redis.aclose()
