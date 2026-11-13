"""Report when a video-queue job waits too long before a worker starts it.

The wait runs from when a job became due to its first pickup, which is what
grows when renders occupy every slot. A job enqueued with a deferral (a
per-user deferral creates a new one) is due at its score, not its enqueue
time, so the deferral itself is not counted. Retries are not measured: arq
keeps the original enqueue time on every try, and a job picked up again after
a worker restart keeps its original score too, so their "wait" would include
the earlier runs. One PostHog exception per interval keeps the critical-alert
webhook from firing on every late job.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from ..config import get_config
from ..observability import get_posthog_client
from .job_queue import DEFAULT_QUEUE_NAME

logger = logging.getLogger(__name__)

QUEUE_DELAY_FINGERPRINT = "queue_delay_exceeded"
# Not an account. The alert is about the queue, not a customer.
QUEUE_DELAY_DISTINCT_ID = "supoclip-worker"
QUEUE_DELAY_ALERT_KEY = "queue_delay_alert:supoclip_tasks"


class QueueDelayExceeded(Exception):
    """A job sat in the video queue longer than the configured threshold."""


def wait_seconds(enqueue_time: datetime | None, now: datetime) -> float:
    if not isinstance(enqueue_time, datetime):
        return 0.0
    if enqueue_time.tzinfo is None:
        enqueue_time = enqueue_time.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return (now - enqueue_time).total_seconds()


def job_due_time(ctx) -> datetime | None:
    """When this pickup's job became runnable: its enqueue time or later score."""
    enqueue_time = ctx.get("enqueue_time")
    if not isinstance(enqueue_time, datetime):
        return None
    if enqueue_time.tzinfo is None:
        enqueue_time = enqueue_time.replace(tzinfo=timezone.utc)
    score = ctx.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool) and score > 0:
        scored = datetime.fromtimestamp(score / 1000, tz=timezone.utc)
        return max(enqueue_time, scored)
    return enqueue_time


async def maybe_report_queue_delay(
    redis,
    *,
    enqueue_time: datetime | None,
    queue_name: str = DEFAULT_QUEUE_NAME,
    now: datetime | None = None,
    client: Any = None,
    threshold_seconds: int | None = None,
    interval_seconds: int | None = None,
    rate_limit_key: str = QUEUE_DELAY_ALERT_KEY,
) -> bool:
    """Send at most one ``queue_delay_exceeded`` exception per interval.

    Returns True when this call won the rate-limit slot, whether or not
    PostHog is configured. No account id is attached.
    """
    config = get_config()
    threshold = (
        config.queue_delay_alert_seconds
        if threshold_seconds is None
        else threshold_seconds
    )
    interval = (
        config.queue_delay_alert_interval_seconds
        if interval_seconds is None
        else interval_seconds
    )
    waited = wait_seconds(enqueue_time, now or datetime.now(timezone.utc))
    if waited < threshold:
        return False
    try:
        acquired = await redis.set(rate_limit_key, "1", nx=True, ex=int(interval))
    except Exception:
        logger.exception("Queue delay alert rate limit failed")
        return False
    if not acquired:
        return False

    depth = -1
    try:
        depth = int(await redis.zcard(queue_name))
    except Exception:
        logger.exception("Queue depth lookup failed")
    waited_s = max(0, int(waited))
    properties = {
        "$exception_fingerprint": QUEUE_DELAY_FINGERPRINT,
        "fingerprint": QUEUE_DELAY_FINGERPRINT,
        "queue_depth": depth,
        "wait_seconds": waited_s,
        "queue": queue_name,
        "threshold_seconds": int(threshold),
    }
    exc = QueueDelayExceeded(
        f"Queue {queue_name} wait {waited_s}s exceeds {int(threshold)}s (depth {depth})"
    )
    posthog = get_posthog_client() if client is None else client
    if posthog is None:
        logger.error(
            "queue_delay_exceeded wait_seconds=%s queue_depth=%s queue=%s",
            waited_s,
            depth,
            queue_name,
        )
        return True
    try:
        posthog.capture_exception(
            exc, distinct_id=QUEUE_DELAY_DISTINCT_ID, properties=properties
        )
    except Exception:
        logger.exception("Failed to report queue delay")
    return True


async def on_video_job_start(ctx) -> None:
    """arq hook: measure the first pickup delay of every job on the video queue."""
    try:
        if int(ctx.get("job_try") or 1) > 1:
            return
        await maybe_report_queue_delay(
            ctx["redis"],
            enqueue_time=job_due_time(ctx),
            queue_name=DEFAULT_QUEUE_NAME,
        )
    except Exception:
        logger.exception("Queue delay check failed")
