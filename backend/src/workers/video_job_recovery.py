"""End video tasks whose job arq will not run again, and refund them.

arq counts every pickup of a job: a run cut short by a worker restart or a
lost container, and every ``Retry``. Past ``max_tries`` it drops the job
without calling the function, so nothing moved the task out of
``processing`` and its minutes stayed charged. ``process_video_task`` is
registered with one extra try, and that pickup ends the task here instead of
rendering. ``reconcile_orphaned_video_tasks`` (cron) catches tasks whose job
left Redis any other way: an arq rejection past that extra try, a job that
failed outside ``process_task``, a flushed Redis.

Both paths end only the attempt they were asked about. The task must still
be queued or processing, still name that job as its current
``processing_job_id`` (a resume or a deferral records a new one), and no
other process may hold its run guard (a live render or a resume holds it).
The refund is the one ``update_task_status`` applies to a terminal failure
without saved clips; it fires once per charge, so a second reconciliation,
the stale-task sweep or a later failure cannot refund twice.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from arq.constants import in_progress_key_prefix, job_key_prefix
from sqlalchemy import text

from ..repositories.task_repository import TaskRepository
from ..repositories.task_run_guard import TaskRunBusy, task_run_guard
from .progress import ProgressTracker
from .video_admission import release_video_slot, wake_next_video_jobs

logger = logging.getLogger(__name__)

# Pickups a video job gets. arq is given one more so it calls the function on
# the exhausted pickup instead of dropping the job silently.
VIDEO_JOB_MAX_TRIES = 3

EXHAUSTED_MESSAGE = (
    "Processing was interrupted too many times. Please retry this generation. "
    "No minutes were used."
)
ORPHANED_MESSAGE = (
    "The processing job stopped unexpectedly. Please retry this generation. "
    "No minutes were used."
)
# Saved clips keep the charge, so those tasks get a message without the refund.
EXHAUSTED_WITH_CLIPS_MESSAGE = (
    "Processing was interrupted too many times. The clips that finished are kept; "
    "retry to generate the rest."
)
ORPHANED_WITH_CLIPS_MESSAGE = (
    "The processing job stopped unexpectedly. The clips that finished are kept; "
    "retry to generate the rest."
)

# A task updated this recently may be between saving its job id and enqueueing it.
ORPHAN_GRACE_SECONDS = 10 * 60
ORPHAN_SWEEP_LIMIT = 200


async def _has_clips(db, task_id: str) -> bool:
    result = await db.execute(
        text("SELECT 1 FROM generated_clips WHERE task_id = :task_id LIMIT 1"),
        {"task_id": task_id},
    )
    return result.first() is not None


async def fail_abandoned_attempt(
    db, *, task_id: str, job_id: str, message: str, message_with_clips: str
) -> bool:
    """Mark this job's attempt failed (and refunded) if nothing else owns the task.

    Returns True only when this call moved the task to ``error``.
    """
    try:
        async with task_run_guard(db, task_id):
            # Read under the guard: no render can save a clip while it is held.
            has_clips = await _has_clips(db, task_id)
            return await TaskRepository.update_task_status(
                db,
                task_id,
                "error",
                progress=0,
                progress_message=message_with_clips if has_clips else message,
                expected_statuses=["queued", "processing"],
                expected_job_id=job_id,
            )
    except TaskRunBusy:
        logger.info("Task %s is owned by another run; job %s leaves it alone", task_id, job_id)
        return False


async def _after_failure(redis, *, task_id: str, user_id: str | None, message: str) -> None:
    """Tell the task page and hand the account's render slot to its next task."""
    from ..database import AsyncSessionLocal

    try:
        await ProgressTracker(redis, task_id).error(message)
    except Exception:
        logger.exception("Could not publish the failure of task %s", task_id)
    if not user_id:
        return
    await release_video_slot(redis, user_id, task_id)
    try:
        async with AsyncSessionLocal() as db:
            await wake_next_video_jobs(db, redis, user_id)
    except Exception:
        logger.exception("Could not wake the next video task after task %s", task_id)


async def fail_exhausted_video_job(
    ctx: dict[str, Any], *, task_id: str, user_id: str
) -> dict[str, Any]:
    """The pickup after the last allowed try: end the task instead of rendering."""
    from ..database import AsyncSessionLocal

    job_id = str(ctx.get("job_id") or "")
    job_try = int(ctx.get("job_try") or 0)
    redis = ctx["redis"]
    async with AsyncSessionLocal() as db:
        failed = await fail_abandoned_attempt(
            db,
            task_id=task_id,
            job_id=job_id,
            message=EXHAUSTED_MESSAGE,
            message_with_clips=EXHAUSTED_WITH_CLIPS_MESSAGE,
        )
        task = await TaskRepository.get_task_by_id(db, task_id) if failed else None
        await db.rollback()
    logger.error(
        "Video job %s for task %s exhausted its %s tries (try %s); task %s",
        job_id,
        task_id,
        VIDEO_JOB_MAX_TRIES,
        job_try,
        "failed" if failed else "left unchanged",
    )
    if not failed:
        return {"task_id": task_id, "status": "exhausted", "reconciled": False}
    try:
        await redis.set(
            f"dead_letter:{task_id}",
            json.dumps({"task_id": task_id, "error": "max retries exceeded", "tries": job_try}),
        )
        await redis.sadd("tasks:dead_letter", task_id)
    except Exception:
        logger.exception("Failed to persist dead-letter payload")
    message = (task or {}).get("progress_message") or EXHAUSTED_MESSAGE
    await _after_failure(redis, task_id=task_id, user_id=user_id, message=message)
    return {"task_id": task_id, "status": "error", "reconciled": True}


async def reconcile_orphaned_video_tasks(
    ctx: dict[str, Any], grace_seconds: int = ORPHAN_GRACE_SECONDS
) -> dict[str, Any]:
    """Cron: fail queued/processing tasks whose current job is gone from Redis.

    arq keeps a job's key until the job finishes and its in-progress key while
    it runs, so neither existing means no worker will ever pick it up again.
    """
    from ..database import AsyncSessionLocal

    redis = ctx["redis"]
    failed: list[str] = []
    async with AsyncSessionLocal() as db:
        rows = (
            await db.execute(
                text(
                    """
                    SELECT id, user_id, source_metadata->>'processing_job_id' AS job_id
                    FROM tasks
                    WHERE status IN ('queued', 'processing')
                      AND COALESCE(source_metadata->>'processing_job_id', '') <> ''
                      AND updated_at < NOW() - CAST(:grace AS INTEGER) * INTERVAL '1 second'
                    ORDER BY updated_at ASC
                    LIMIT :limit
                    """
                ),
                {"grace": grace_seconds, "limit": ORPHAN_SWEEP_LIMIT},
            )
        ).fetchall()
        await db.rollback()
        for row in rows:
            task_id, user_id, job_id = row.id, row.user_id, row.job_id
            if await redis.exists(job_key_prefix + job_id, in_progress_key_prefix + job_id):
                continue
            try:
                ended = await fail_abandoned_attempt(
                    db,
                    task_id=task_id,
                    job_id=job_id,
                    message=ORPHANED_MESSAGE,
                    message_with_clips=ORPHANED_WITH_CLIPS_MESSAGE,
                )
                task = await TaskRepository.get_task_by_id(db, task_id) if ended else None
                await db.rollback()
            except Exception:
                logger.exception("Could not reconcile orphaned task %s", task_id)
                await db.rollback()
                continue
            if not ended:
                continue
            logger.error("Task %s lost its job %s; marked failed", task_id, job_id)
            failed.append(task_id)
            message = (task or {}).get("progress_message") or ORPHANED_MESSAGE
            await _after_failure(redis, task_id=task_id, user_id=user_id, message=message)
    return {"checked": len(rows), "failed": len(failed)}
