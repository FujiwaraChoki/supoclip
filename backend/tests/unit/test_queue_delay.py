"""Pickup delay is reported once, without an account id."""

from datetime import datetime, timedelta, timezone

import pytest

from src.workers.queue_delay import (
    QUEUE_DELAY_DISTINCT_ID,
    QUEUE_DELAY_FINGERPRINT,
    maybe_report_queue_delay,
)


class _Redis:
    def __init__(self, depth=12):
        self.depth = depth
        self.keys = set()
        self.sets = []

    async def set(self, key, value, nx=False, ex=None):
        self.sets.append((key, nx, ex))
        if nx and key in self.keys:
            return None
        self.keys.add(key)
        return True

    async def zcard(self, _name):
        return self.depth


class _Client:
    def __init__(self):
        self.calls = []

    def capture_exception(self, exc, distinct_id=None, properties=None):
        self.calls.append((exc, distinct_id, properties))


@pytest.mark.asyncio
async def test_a_short_wait_does_not_report():
    redis = _Redis()
    reported = await maybe_report_queue_delay(
        redis,
        enqueue_time=datetime.now(timezone.utc) - timedelta(seconds=30),
        threshold_seconds=600,
        client=_Client(),
        rate_limit_key="queue_delay_alert:test-short",
    )
    assert reported is False
    assert redis.sets == []


@pytest.mark.asyncio
async def test_a_long_wait_reports_once_with_depth_and_no_account():
    redis = _Redis(depth=284)
    client = _Client()
    now = datetime.now(timezone.utc)
    # Naive timestamps are the shape arq puts on the job context.
    enqueue_time = (now - timedelta(seconds=4850)).replace(tzinfo=None)
    first = await maybe_report_queue_delay(
        redis,
        enqueue_time=enqueue_time,
        now=now,
        threshold_seconds=600,
        interval_seconds=900,
        client=client,
        rate_limit_key="queue_delay_alert:test-long",
        queue_name="supoclip_tasks",
    )
    second = await maybe_report_queue_delay(
        redis,
        enqueue_time=enqueue_time,
        now=now,
        threshold_seconds=600,
        interval_seconds=900,
        client=client,
        rate_limit_key="queue_delay_alert:test-long",
    )
    assert first is True
    assert second is False
    assert len(client.calls) == 1
    exc, distinct_id, properties = client.calls[0]
    assert distinct_id == QUEUE_DELAY_DISTINCT_ID
    assert properties["$exception_fingerprint"] == QUEUE_DELAY_FINGERPRINT
    assert properties["fingerprint"] == QUEUE_DELAY_FINGERPRINT
    assert properties["queue_depth"] == 284
    assert properties["wait_seconds"] == 4850
    assert properties["queue"] == "supoclip_tasks"
    assert "user" not in properties
    assert exc.__class__.__name__ == "QueueDelayExceeded"
    assert redis.sets[0][1] is True
    assert redis.sets[0][2] == 900


@pytest.mark.asyncio
async def test_missing_posthog_still_consumes_the_rate_limit(monkeypatch):
    monkeypatch.setattr(
        "src.workers.queue_delay.get_posthog_client", lambda: None
    )
    redis = _Redis()
    reported = await maybe_report_queue_delay(
        redis,
        enqueue_time=datetime.now(timezone.utc) - timedelta(seconds=700),
        threshold_seconds=600,
        client=None,
        rate_limit_key="queue_delay_alert:test-nophone",
    )
    assert reported is True
    assert "queue_delay_alert:test-nophone" in redis.keys


def test_video_worker_measures_delay_on_start():
    from src.workers.tasks import WorkerSettings

    assert WorkerSettings.cron_jobs == []
    assert WorkerSettings.on_job_start.__name__ == "on_video_job_start"
