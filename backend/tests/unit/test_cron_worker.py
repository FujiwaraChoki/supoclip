"""Scheduled jobs use their own queue and one stable id."""

import os

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.constants import job_key_prefix

from src.workers.cron_worker import CRON_QUEUE_NAME, CronWorkerSettings
from src.workers.run_workers import (
    CRON_WORKER,
    FINAL_RENDER_WORKER,
    VIDEO_WORKER,
    embed_cron_worker,
    embed_final_render_worker,
    worker_settings,
)
from src.workers.tasks import WorkerSettings


def test_cron_jobs_are_not_on_the_video_worker():
    assert WorkerSettings.cron_jobs == []
    assert WorkerSettings.queue_name == "supoclip_tasks"
    assert CronWorkerSettings.queue_name == CRON_QUEUE_NAME
    assert CronWorkerSettings.queue_name != WorkerSettings.queue_name
    jobs = CronWorkerSettings.cron_jobs
    assert len(jobs) == 9
    assert [job.job_id for job in jobs] == [job.name for job in jobs]
    assert {job.job_id for job in jobs} == {
        "cron:check_media_storage",
        "cron:replicate_pending_media",
        "cron:cleanup_media",
        "cron:sweep_orphaned_media",
        "cron:copy_pending_uploads",
        "cron:dispatch_scheduled_social_posts",
        "cron:refresh_social_metrics",
        "cron:process_cancellation_reviews",
        "cron:deliver_feedback_webhooks",
    }
    for job in jobs:
        assert job.job_id.count(":") == 1
        assert job.coroutine.__name__ == job.job_id.split(":", 1)[1]


def test_the_worker_process_embeds_cron_unless_asked_not_to():
    assert embed_cron_worker("0") is False
    assert embed_cron_worker("false") is False
    assert embed_cron_worker("1") is True
    assert worker_settings(False, False) == [VIDEO_WORKER]
    assert worker_settings(True, False) == [VIDEO_WORKER, CRON_WORKER]


def test_the_worker_process_embeds_the_full_render_worker_unless_asked_not_to():
    assert embed_final_render_worker("off") is False
    assert embed_final_render_worker("1") is True
    assert worker_settings(False, True) == [VIDEO_WORKER, FINAL_RENDER_WORKER]
    assert worker_settings(True, True) == [VIDEO_WORKER, CRON_WORKER, FINAL_RENDER_WORKER]


def test_the_full_render_worker_drains_its_own_queue():
    from src.services.full_render_service import FULL_RENDER_QUEUE, render_full_clip
    from src.workers.final_render_worker import FinalRenderWorkerSettings
    from src.workers.job_queue import DEFAULT_QUEUE_NAME

    assert FinalRenderWorkerSettings.queue_name == FULL_RENDER_QUEUE != DEFAULT_QUEUE_NAME
    assert [job.coroutine for job in FinalRenderWorkerSettings.functions] == [render_full_clip]
    assert FinalRenderWorkerSettings.max_jobs >= 1


@pytest.mark.asyncio
async def test_a_pending_cron_id_is_not_enqueued_twice():
    redis = await create_pool(RedisSettings(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
    ))
    job_id = "cron:dedupe_probe"
    try:
        first = await redis.enqueue_job(
            "cleanup_media", _job_id=job_id, _queue_name=CRON_QUEUE_NAME
        )
        second = await redis.enqueue_job(
            "cleanup_media", _job_id=job_id, _queue_name=CRON_QUEUE_NAME
        )
        assert first is not None
        assert second is None
        assert await redis.zscore(CRON_QUEUE_NAME, job_id) is not None
        # The video queue is not where this job sits.
        assert await redis.zscore("supoclip_tasks", job_id) is None
    finally:
        await redis.delete(job_key_prefix + job_id)
        await redis.zrem(CRON_QUEUE_NAME, job_id)
        await redis.aclose()
