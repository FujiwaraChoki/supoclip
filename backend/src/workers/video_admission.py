"""Keep one account from filling every video-worker slot.

Extra video jobs stay on the queue with a future score, so a worker never
holds a slot for them. They are not dropped and they are not charged again:
minutes were recorded when the task was created, and cancellation still
refunds that charge when the task leaves the queue.

Within one account, renders start in submission order: a waiting job takes a
free slot only when no older task of the account is still waiting for one
(see ``waiting_video_tasks``). When a render ends, the next waiting job's
existing queue entry is moved to now, so the slot does not sit idle for a
whole deferral.
"""

from __future__ import annotations

import logging
import secrets
import time

from arq.constants import in_progress_key_prefix, job_key_prefix
from sqlalchemy import text

from ..config import get_config
from ..repositories.task_repository import TaskRepository
from .job_queue import DEFAULT_QUEUE_NAME, JobQueue

logger = logging.getLogger(__name__)

ACTIVE_KEY = "user_video_active:{user_id}"
# Longer than the processing-task fail-safe, so a slot outlives a live render
# and is pruned once that task is no longer processing.
SLOT_TTL_SECONDS = 6 * 60 * 60

UPLOAD_WAIT_KEY = "upload_wait:{task_id}"
UPLOAD_WAIT_MESSAGE = "Saving your upload..."
UPLOAD_LOST_MESSAGE = "The uploaded video could not be saved. Upload it again to retry."

_ACQUIRE_LUA = """
local key = KEYS[1]
local task_id = ARGV[1]
local limit = tonumber(ARGV[2])
local ttl = tonumber(ARGV[3])
if redis.call('SISMEMBER', key, task_id) == 1 then
  redis.call('EXPIRE', key, ttl)
  return 1
end
if redis.call('SCARD', key) >= limit then
  return 0
end
redis.call('SADD', key, task_id)
redis.call('EXPIRE', key, ttl)
return 1
"""


def _decode(value) -> str:
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


async def acquire_video_slot(redis, user_id: str, task_id: str, limit: int) -> bool:
    key = ACTIVE_KEY.format(user_id=user_id)
    result = await redis.eval(
        _ACQUIRE_LUA, 1, key, task_id, str(limit), str(SLOT_TTL_SECONDS)
    )
    return int(result) == 1


async def release_video_slot(redis, user_id: str, task_id: str) -> None:
    try:
        await redis.srem(ACTIVE_KEY.format(user_id=user_id), task_id)
    except Exception:
        logger.exception("Could not release video slot for task %s", task_id)


async def prune_video_slots(redis, user_id: str, live_task_ids: set[str]) -> None:
    key = ACTIVE_KEY.format(user_id=user_id)
    members = await redis.smembers(key)
    stale = [_decode(member) for member in members if _decode(member) not in live_task_ids]
    if stale:
        await redis.srem(key, *stale)


async def active_task_ids(db, user_id: str) -> set[str]:
    """Queued and processing tasks still own a slot if they acquired one.

    A render is queued for a moment after it takes a slot, before its status
    flips to processing. Pruning only processing ids would free that slot and
    let a second video from the same account start.
    """
    rows = await db.execute(
        text(
            """
            SELECT id FROM tasks
            WHERE user_id = :user AND status IN ('queued', 'processing')
            """
        ),
        {"user": user_id},
    )
    return {row[0] for row in rows}


async def queued_video_tasks(db, user_id: str) -> list[tuple[str, str | None]]:
    """The account's queued tasks and their current job ids, oldest first."""
    rows = await db.execute(
        text(
            """
            SELECT id, source_metadata->>'processing_job_id' AS job_id
            FROM tasks
            WHERE user_id = :user AND status = 'queued'
            ORDER BY created_at ASC, id ASC
            """
        ),
        {"user": user_id},
    )
    return [(row[0], row[1]) for row in rows]


async def _waits_for_slot(redis, task_id: str, job_id: str | None) -> bool:
    """A queued task that would take a free slot when its job next runs.

    Not: a task being cancelled, one whose upload is still being copied
    (``hold_for_upload`` gives the slot straight back), or one with no job
    left on the queue (the stale-queued sweep fails it). Those must not hold
    up newer tasks.
    """
    if await redis.get(f"task_cancel:{task_id}"):
        return False
    if await redis.exists(UPLOAD_WAIT_KEY.format(task_id=task_id)):
        return False
    if not job_id:
        return False
    return bool(
        await redis.exists(job_key_prefix + job_id, in_progress_key_prefix + job_id)
    )


async def waiting_video_tasks(
    db, redis, user_id: str, holders: set[str], *, before: str | None = None
) -> list[tuple[str, str]]:
    """Queued tasks waiting for a render slot, oldest first.

    With ``before``, only tasks submitted ahead of that task are returned.
    """
    waiting = []
    for task_id, job_id in await queued_video_tasks(db, user_id):
        if task_id == before:
            break
        if task_id in holders:
            continue
        if await _waits_for_slot(redis, task_id, job_id):
            waiting.append((task_id, job_id))
    return waiting


async def _slot_holders(redis, user_id: str) -> set[str]:
    return {_decode(m) for m in await redis.smembers(ACTIVE_KEY.format(user_id=user_id))}


async def _is_turn(db, redis, *, task_id: str, user_id: str, limit: int) -> bool:
    """True when this task is among the oldest waiting tasks that fit the free slots."""
    holders = await _slot_holders(redis, user_id)
    if task_id in holders:
        return True
    free = limit - len(holders)
    if free <= 0:
        return False
    ahead = await waiting_video_tasks(db, redis, user_id, holders, before=task_id)
    return len(ahead) < free


async def _clear_finished_upload_wait(redis, task_id: str, url: str | None) -> None:
    """Forget an upload wait once the copy has arrived, so the task is in line again."""
    key = UPLOAD_WAIT_KEY.format(task_id=task_id)
    if not url or not await redis.exists(key):
        return
    from ..media.upload_ingest import upload_copy_pending

    if not await upload_copy_pending(url):
        await redis.delete(key)


# Move a deferred job's existing queue entry to now. Only an entry that is
# still on the queue and due later is touched, so no job is created, run twice
# or revived after it finished.
_WAKE_LUA = """
local score = redis.call('ZSCORE', KEYS[1], ARGV[1])
if score and tonumber(score) > tonumber(ARGV[2]) then
  redis.call('ZADD', KEYS[1], ARGV[2], ARGV[1])
  return 1
end
return 0
"""


async def wake_next_video_jobs(db, redis, user_id: str) -> list[str]:
    """Start the account's next waiting tasks now that a slot is free.

    Returns the task ids whose jobs were moved forward. Each job still goes
    through ``admit_video_job``, so a wake never bypasses the cap or the order.
    """
    limit = get_config().video_job_user_concurrency
    await prune_video_slots(redis, user_id, await active_task_ids(db, user_id))
    holders = await _slot_holders(redis, user_id)
    free = limit - len(holders)
    if free <= 0:
        return []
    woken = []
    now_ms = int(time.time() * 1000)
    for task_id, job_id in (await waiting_video_tasks(db, redis, user_id, holders))[:free]:
        if int(await redis.eval(_WAKE_LUA, 1, DEFAULT_QUEUE_NAME, job_id, str(now_ms))):
            woken.append(task_id)
    if woken:
        logger.info("Woke queued video tasks %s after a slot freed", woken)
    return woken


async def _drop_job(job_id: str) -> None:
    try:
        pool = await JobQueue.get_pool()
        await pool.delete(job_key_prefix + job_id)
        await pool.zrem(DEFAULT_QUEUE_NAME, job_id)
    except Exception:
        logger.exception("Could not drop deferred video job %s", job_id)


async def admit_video_job(
    ctx, db, *, task_id: str, user_id: str, job_args: tuple, url: str | None = None
) -> str:
    """Return ``run``, ``deferred``, or ``skipped``.

    ``deferred`` has already queued one later attempt and does not hold a
    worker slot. ``skipped`` is a task that was cancelled or finished. A
    queued task runs only when it is the account's oldest task waiting for a
    slot (the oldest N with a limit of N), so renders start in submission order.
    """
    redis = ctx["redis"]
    task = await TaskRepository.get_task_by_id(db, task_id)
    status = task.get("status") if task else None
    if status not in {"queued", "processing"}:
        return "skipped"
    if await redis.get(f"task_cancel:{task_id}"):
        return "skipped"

    config = get_config()
    limit = config.video_job_user_concurrency
    try:
        await prune_video_slots(redis, user_id, await active_task_ids(db, user_id))
        await _clear_finished_upload_wait(redis, task_id, url)
        # A task already processing (a retry of a started render) is not
        # queued behind anyone; only queued tasks wait for their turn.
        in_turn = status != "queued" or await _is_turn(
            db, redis, task_id=task_id, user_id=user_id, limit=limit
        )
        acquired = in_turn and await acquire_video_slot(redis, user_id, task_id, limit)
    except Exception:
        # A Redis blip must not drop a paid job. Running it may briefly exceed
        # the cap; losing it would refund or strand the task.
        logger.exception("Video concurrency check failed for task %s", task_id)
        try:
            await db.rollback()
        except Exception:
            logger.exception("Could not roll back after a concurrency check failure")
        return "run"
    if acquired:
        return "run"

    await defer_video_job(db, task_id, job_args, config.video_job_defer_seconds)
    logger.info(
        "Deferred video task %s for %ss; account is at the concurrency cap or an older task is next",
        task_id,
        config.video_job_defer_seconds,
    )
    return "deferred"


async def defer_video_job(db, task_id: str, job_args: tuple, seconds: int) -> None:
    """Queue one later attempt of this job. The current attempt must then return."""
    new_job_id = f"{task_id}:{secrets.token_hex(16)}"
    await JobQueue.enqueue_job(
        "process_video_task",
        *job_args,
        _job_id=new_job_id,
        _queue_name=DEFAULT_QUEUE_NAME,
        _defer_by=seconds,
    )
    try:
        await TaskRepository.save_source_metadata(
            db, task_id, {"processing_job_id": new_job_id}
        )
    except Exception:
        # The stale-queued sweep trusts this id. Drop the extra job so a
        # failed save retries the current one instead of running twice.
        await _drop_job(new_job_id)
        raise


async def hold_for_upload(ctx, db, *, task_id: str, user_id: str, url: str, job_args: tuple) -> str:
    """Return ``run``, ``deferred``, or ``failed`` for a job whose source is an upload.

    An upload is written to the API's disk first and copied to object storage
    afterwards. A worker on another machine can only read that copy. Until it
    exists the job goes back on the queue, without holding a worker slot or
    the account's render slot. A copy that never arrives fails the task, which
    releases its minutes.
    """
    from ..media.upload_ingest import upload_copy_pending

    if not url.startswith("upload://") or not await upload_copy_pending(url):
        return "run"
    redis = ctx["redis"]
    config = get_config()
    key = UPLOAD_WAIT_KEY.format(task_id=task_id)
    await redis.set(key, str(time.time()), nx=True, ex=24 * 60 * 60)
    started = float(_decode(await redis.get(key) or time.time()))
    await release_video_slot(redis, user_id, task_id)
    if time.time() - started > config.upload_copy_wait_seconds:
        logger.error("Upload for task %s never reached object storage", task_id)
        await TaskRepository.update_task_status(
            db, task_id, "error", progress=0, progress_message=UPLOAD_LOST_MESSAGE,
            expected_statuses=["queued", "processing"],
        )
        await redis.delete(key)
        return "failed"
    await defer_video_job(db, task_id, job_args, config.video_job_defer_seconds)
    logger.info("Deferred video task %s; its upload is still being copied to object storage", task_id)
    # The slot this task gave back goes to the next task in line now.
    try:
        await wake_next_video_jobs(db, redis, user_id)
    except Exception:
        logger.exception("Could not wake the next video task after task %s", task_id)
    return "deferred"
