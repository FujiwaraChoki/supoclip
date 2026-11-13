"""A video job arq will not run again ends its task and refunds it exactly once.

The incident: a render was cut short three times (worker restarts), so the
fourth pickup was rejected by arq with "max 3 retries exceeded" before the
function ran. The task stayed ``processing`` at 65% with no clips and its
minutes charged.
"""

import asyncio
import os
import uuid

import pytest
from arq import create_pool
from arq.connections import RedisSettings
from arq.constants import in_progress_key_prefix, job_key_prefix, retry_key_prefix
from arq.worker import Worker
from sqlalchemy import text

from src.repositories.minute_usage_repository import record_minute_usage, refund_failed_minute_usage
from src.repositories.task_repository import TaskRepository
from src.repositories.task_run_guard import task_run_guard
from src.workers import video_admission
from src.workers.tasks import WorkerSettings
from src.workers.video_job_recovery import (
    EXHAUSTED_MESSAGE,
    EXHAUSTED_WITH_CLIPS_MESSAGE,
    ORPHANED_MESSAGE,
    VIDEO_JOB_MAX_TRIES,
    fail_abandoned_attempt,
    fail_exhausted_video_job,
    reconcile_orphaned_video_tasks,
)
from tests.fixtures.factories import create_clip, create_source, create_task, create_user


def redis_settings():
    return RedisSettings(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
    )


@pytest.fixture
async def arq_redis():
    pool = await create_pool(redis_settings())
    yield pool
    await pool.aclose()


@pytest.fixture
async def charged_task(db_session):
    """A processing task at 65%, charged 10 minutes, whose current job is known."""

    async def make(status: str = "processing"):
        user = await create_user(db_session)
        source = await create_source(db_session)
        task = await create_task(
            db_session,
            user_id=user["id"],
            source_id=source["id"],
            status=status,
            source_duration_seconds=600,
        )
        job_id = f"{task['id']}:{uuid.uuid4().hex}"
        await TaskRepository.save_source_metadata(db_session, task["id"], {"processing_job_id": job_id})
        await db_session.execute(
            text("UPDATE tasks SET progress = 65, progress_message = 'Downloading selected segments...' WHERE id = :id"),
            {"id": task["id"]},
        )
        await record_minute_usage(db_session, user["id"], task["id"], 600)
        await db_session.commit()
        return {"task_id": task["id"], "user_id": user["id"], "job_id": job_id}

    return make


async def task_state(db, task_id: str):
    row = (
        await db.execute(
            text(
                """
                SELECT t.status, t.progress_message, m.refunded_at
                FROM tasks t LEFT JOIN minute_usage_events m ON m.task_id = t.id
                WHERE t.id = :id
                """
            ),
            {"id": task_id},
        )
    ).first()
    await db.rollback()
    return row


def video_function():
    return next(
        f for f in WorkerSettings.functions if getattr(f, "name", None) == "process_video_task"
    )


async def run_one_pickup(arq_redis, task, *, previous_pickups: int, functions=None):
    """Enqueue the task's current job as arq sees it after earlier interrupted runs."""
    queue = f"test_video_exhaustion:{uuid.uuid4().hex}"
    job_id = task["job_id"]
    args = (task["task_id"], "https://www.youtube.com/watch?v=seeded", "youtube", task["user_id"])
    await arq_redis.enqueue_job("process_video_task", *args, _job_id=job_id, _queue_name=queue)
    # Each earlier pickup incremented arq's retry counter (a worker restart
    # cancels the run and leaves the job on the queue).
    await arq_redis.set(retry_key_prefix + job_id, previous_pickups)
    worker = Worker(
        functions=functions or [video_function()],
        # Its own connection: closing a worker closes its pool.
        redis_settings=redis_settings(),
        queue_name=queue,
        burst=True,
        poll_delay=0.05,
        handle_signals=False,
        max_tries=WorkerSettings.max_tries,
        keep_result=60,
    )
    try:
        await worker.main()
    finally:
        await worker.close()
        await arq_redis.delete(queue, job_key_prefix + job_id, in_progress_key_prefix + job_id)
    return worker


async def test_the_pickup_after_the_last_try_fails_the_task_and_refunds_it(
    db_session, arq_redis, charged_task
):
    task = await charged_task()
    worker = await run_one_pickup(arq_redis, task, previous_pickups=VIDEO_JOB_MAX_TRIES)

    assert worker.jobs_complete == 1  # the function ran instead of arq dropping the job
    state = await task_state(db_session, task["task_id"])
    assert state.status == "error"
    assert state.progress_message == EXHAUSTED_MESSAGE
    assert state.refunded_at is not None
    assert await arq_redis.sismember("tasks:dead_letter", task["task_id"])
    progress = await arq_redis.get(f"progress:{task['task_id']}")
    assert b'"status": "error"' in progress
    await arq_redis.srem("tasks:dead_letter", task["task_id"])
    await arq_redis.delete(f"dead_letter:{task['task_id']}", f"progress:{task['task_id']}")


async def test_without_the_extra_try_arq_drops_the_job_and_the_task_stays_charged(
    db_session, arq_redis, charged_task
):
    """The bug this guards against: plain registration never calls the function."""
    from src.workers.tasks import process_video_task

    task = await charged_task()
    worker = await run_one_pickup(
        arq_redis, task, previous_pickups=VIDEO_JOB_MAX_TRIES, functions=[process_video_task]
    )

    assert worker.jobs_failed == 1 and worker.jobs_complete == 0
    state = await task_state(db_session, task["task_id"])
    assert state.status == "processing"
    assert state.refunded_at is None
    # The cron sweep is what ends this one.
    result = await reconcile_orphaned_video_tasks({"redis": arq_redis}, grace_seconds=0)
    assert result["failed"] >= 1
    state = await task_state(db_session, task["task_id"])
    assert state.status == "error"
    assert state.progress_message == ORPHANED_MESSAGE
    assert state.refunded_at is not None
    await arq_redis.delete(f"progress:{task['task_id']}")


async def test_a_live_run_holding_the_guard_is_left_alone(db_session, arq_redis, charged_task):
    task = await charged_task()
    async with task_run_guard(db_session, task["task_id"]):
        result = await fail_exhausted_video_job(
            {"redis": arq_redis, "job_id": task["job_id"], "job_try": VIDEO_JOB_MAX_TRIES + 1},
            task_id=task["task_id"],
            user_id=task["user_id"],
        )
    assert result["reconciled"] is False
    state = await task_state(db_session, task["task_id"])
    assert state.status == "processing"
    assert state.refunded_at is None


async def test_a_stale_job_does_not_end_a_resumed_attempt(db_session, arq_redis, charged_task):
    task = await charged_task()
    stale_job = task["job_id"]
    # The user resumed: a new job id is current and the task is queued again.
    await TaskRepository.save_source_metadata(
        db_session, task["task_id"], {"processing_job_id": f"{task['task_id']}:resumed"}
    )
    await TaskRepository.update_task_status(db_session, task["task_id"], "queued")

    assert not await fail_abandoned_attempt(
        db_session,
        task_id=task["task_id"],
        job_id=stale_job,
        message=EXHAUSTED_MESSAGE,
        message_with_clips=EXHAUSTED_WITH_CLIPS_MESSAGE,
    )
    state = await task_state(db_session, task["task_id"])
    assert state.status == "queued"
    assert state.refunded_at is None


@pytest.mark.parametrize("status", ["completed", "cancelled"])
async def test_finished_tasks_are_not_touched(db_session, arq_redis, charged_task, status):
    task = await charged_task(status=status)
    if status == "cancelled":
        # Cancellation already refunded it; remember when.
        await refund_failed_minute_usage(db_session, task["task_id"])
        await db_session.commit()
    before = await task_state(db_session, task["task_id"])

    result = await fail_exhausted_video_job(
        {"redis": arq_redis, "job_id": task["job_id"], "job_try": VIDEO_JOB_MAX_TRIES + 1},
        task_id=task["task_id"],
        user_id=task["user_id"],
    )
    assert result["reconciled"] is False
    after = await task_state(db_session, task["task_id"])
    assert after == before
    assert (after.refunded_at is None) == (status == "completed")


async def test_saved_clips_keep_the_charge(db_session, arq_redis, charged_task):
    task = await charged_task()
    await create_clip(db_session, task_id=task["task_id"])

    result = await fail_exhausted_video_job(
        {"redis": arq_redis, "job_id": task["job_id"], "job_try": VIDEO_JOB_MAX_TRIES + 1},
        task_id=task["task_id"],
        user_id=task["user_id"],
    )
    assert result["reconciled"] is True
    state = await task_state(db_session, task["task_id"])
    assert state.status == "error"
    assert state.progress_message == EXHAUSTED_WITH_CLIPS_MESSAGE
    assert state.refunded_at is None
    await arq_redis.srem("tasks:dead_letter", task["task_id"])
    await arq_redis.delete(f"dead_letter:{task['task_id']}", f"progress:{task['task_id']}")


async def test_racing_reconcilers_refund_once(db_session, arq_redis, charged_task):
    task = await charged_task()
    ctx = {"redis": arq_redis, "job_id": task["job_id"], "job_try": VIDEO_JOB_MAX_TRIES + 1}

    # Two exhausted pickups of the same job (two workers) and the cron sweep's
    # primitive, all at once.
    results = await asyncio.gather(
        fail_exhausted_video_job(ctx, task_id=task["task_id"], user_id=task["user_id"]),
        fail_exhausted_video_job(dict(ctx), task_id=task["task_id"], user_id=task["user_id"]),
        fail_abandoned_attempt(
            db_session,
            task_id=task["task_id"],
            job_id=task["job_id"],
            message=ORPHANED_MESSAGE,
            message_with_clips=ORPHANED_MESSAGE,
        ),
    )
    wins = [r["reconciled"] for r in results[:2]] + [results[2]]
    assert wins.count(True) == 1
    state = await task_state(db_session, task["task_id"])
    assert state.status == "error"
    refunded_at = state.refunded_at
    assert refunded_at is not None

    # Every later path is a no-op: another exhausted pickup, the sweep, the
    # stale-task sweep's status write and a direct refund call.
    again = await fail_exhausted_video_job(ctx, task_id=task["task_id"], user_id=task["user_id"])
    assert again["reconciled"] is False
    await reconcile_orphaned_video_tasks({"redis": arq_redis}, grace_seconds=0)
    assert not await TaskRepository.update_task_status(
        db_session, task["task_id"], "error", expected_statuses=["processing"]
    )
    await refund_failed_minute_usage(db_session, task["task_id"])
    await db_session.commit()
    final = await task_state(db_session, task["task_id"])
    assert final.refunded_at == refunded_at
    count = (
        await db_session.execute(
            text("SELECT COUNT(*) FROM minute_usage_events WHERE task_id = :id"),
            {"id": task["task_id"]},
        )
    ).scalar_one()
    assert count == 1
    await arq_redis.srem("tasks:dead_letter", task["task_id"])
    await arq_redis.delete(f"dead_letter:{task['task_id']}", f"progress:{task['task_id']}")


async def test_the_sweep_skips_live_jobs_and_recent_tasks(db_session, arq_redis, charged_task):
    queued_job = await charged_task(status="queued")
    running_job = await charged_task()
    await arq_redis.set(job_key_prefix + queued_job["job_id"], b"x", ex=60)
    await arq_redis.set(in_progress_key_prefix + running_job["job_id"], b"1", ex=60)
    recent = None
    try:
        await reconcile_orphaned_video_tasks({"redis": arq_redis}, grace_seconds=0)
        assert (await task_state(db_session, queued_job["task_id"])).status == "queued"
        assert (await task_state(db_session, running_job["task_id"])).status == "processing"

        # Recently updated: it may be between saving its job id and enqueueing.
        recent = await charged_task()
        await reconcile_orphaned_video_tasks({"redis": arq_redis}, grace_seconds=600)
        state = await task_state(db_session, recent["task_id"])
        assert state.status == "processing"
        assert state.refunded_at is None
    finally:
        await arq_redis.delete(
            job_key_prefix + queued_job["job_id"], in_progress_key_prefix + running_job["job_id"]
        )
        for task in filter(None, (queued_job, running_job, recent)):
            await TaskRepository.update_task_status(db_session, task["task_id"], "completed")


async def test_reconciling_frees_the_render_slot(db_session, arq_redis, charged_task):
    task = await charged_task()
    slot = video_admission.ACTIVE_KEY.format(user_id=task["user_id"])
    await video_admission.acquire_video_slot(arq_redis, task["user_id"], task["task_id"], 1)
    try:
        await fail_exhausted_video_job(
            {"redis": arq_redis, "job_id": task["job_id"], "job_try": VIDEO_JOB_MAX_TRIES + 1},
            task_id=task["task_id"],
            user_id=task["user_id"],
        )
        assert await arq_redis.smembers(slot) == set()
    finally:
        await arq_redis.delete(slot, f"dead_letter:{task['task_id']}", f"progress:{task['task_id']}")
        await arq_redis.srem("tasks:dead_letter", task["task_id"])
