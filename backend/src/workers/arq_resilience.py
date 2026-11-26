"""Keep an arq worker polling through a Redis blip.

Render workers reach Redis over the network. When a read times out while arq
claims a job, redis-py turns the error into ``WatchError`` ("A ConnectionError
occurred on while watching one or more keys") because the claim runs inside a
WATCH. arq 0.28's ``start_jobs`` only expects ``WatchError`` from the final
``EXEC``, so this one escapes the poll loop, the worker process exits and every
render it was running dies with it. ``start_jobs`` has also already taken a
slot (semaphore permit and ``job_counter``) for the job by then, and never gives
it back.

``install()`` patches ``Worker`` so that:

- a Redis error while claiming a job gives the slot back and ends that batch;
  the job stays queued and the next poll tries again;
- a Redis error anywhere else in a poll iteration is logged and the next poll
  runs as usual, instead of ending the worker;
- a poll loop that has not finished an iteration for ``STALL_WARNING_SECONDS``
  logs where it is waiting, once per stall, so a silent stall can be traced.

Running jobs are untouched: they use their own Redis calls and already retry.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time

from arq import worker as arq_worker
from arq.constants import in_progress_key_prefix
from arq.utils import timestamp_ms
from redis.exceptions import RedisError, ResponseError, WatchError

logger = logging.getLogger(__name__)

STALL_WARNING_SECONDS = 180
_WATCHDOG_INTERVAL_SECONDS = 30

# Errors that mean "Redis didn't answer this time", not a bug in our code.
_TRANSIENT = (RedisError, OSError, asyncio.TimeoutError)

_original_poll_iteration = arq_worker.Worker._poll_iteration


async def _claim(worker: arq_worker.Worker, job_id: str) -> int | None:
    """Mark ``job_id`` in progress; its queue score if this worker got it."""
    in_progress_key = in_progress_key_prefix + job_id
    async with worker.pool.pipeline(transaction=True) as pipe:
        await pipe.watch(in_progress_key)
        ongoing_exists = await pipe.exists(in_progress_key)
        score = await pipe.zscore(worker.queue_name, job_id)
        if ongoing_exists or not score or score > timestamp_ms():
            # Running elsewhere, already finished, or re-queued with a delay.
            return None
        pipe.multi()
        pipe.psetex(in_progress_key, int(worker.in_progress_timeout_s * 1000), b"1")
        try:
            await pipe.execute()
        except (ResponseError, WatchError):
            # Another worker claimed it between WATCH and EXEC.
            return None
    return int(score)


async def start_jobs(self: arq_worker.Worker, job_ids: list[bytes]) -> None:
    """arq 0.28's ``start_jobs``, giving the slot back when a claim fails."""
    for job_id_b in job_ids:
        await self.sem.acquire()

        if self.job_counter >= self.max_jobs:
            self.sem.release()
            return

        self.job_counter = self.job_counter + 1
        job_id = job_id_b.decode()
        try:
            score = await _claim(self, job_id)
        except _TRANSIENT as error:
            self.job_counter = self.job_counter - 1
            self.sem.release()
            logger.warning(
                "Redis failed while claiming job %s (%s: %s); it stays queued for the next poll",
                job_id,
                type(error).__name__,
                error,
            )
            return

        if score is None:
            self.job_counter = self.job_counter - 1
            self.sem.release()
            logger.debug("job %s already running elsewhere", job_id)
            continue

        task = self.loop.create_task(self.run_job(job_id, score))
        task.add_done_callback(lambda _: self._release_sem_dec_counter_on_complete())
        self.tasks[job_id] = task


async def _poll_iteration(self: arq_worker.Worker) -> None:
    _ensure_watchdog(self)
    try:
        await _original_poll_iteration(self)
    except _TRANSIENT as error:
        logger.warning(
            "Redis failed during a poll (%s: %s); polling again",
            type(error).__name__,
            error,
        )
    finally:
        self._supoclip_last_poll = time.monotonic()


def _ensure_watchdog(worker: arq_worker.Worker) -> None:
    if getattr(worker, "_supoclip_watchdog", None) is not None:
        return
    worker._supoclip_last_poll = time.monotonic()
    worker._supoclip_watchdog = worker.loop.create_task(_watch_polling(worker))


def _stack_of(task: asyncio.Task | None) -> str:
    if task is None:
        return "(no main task)"
    buffer = io.StringIO()
    task.print_stack(file=buffer)
    return buffer.getvalue()


async def _watch_polling(worker: arq_worker.Worker) -> None:
    warned_for = None
    while True:
        await asyncio.sleep(_WATCHDOG_INTERVAL_SECONDS)
        last = worker._supoclip_last_poll
        stalled_for = time.monotonic() - last
        if stalled_for < STALL_WARNING_SECONDS or warned_for == last:
            continue
        warned_for = last
        logger.error(
            "Worker has not finished a poll for %ds (%d jobs running); it takes no new jobs "
            "until it does. Poll loop is waiting at:\n%s",
            stalled_for,
            worker.job_counter,
            _stack_of(getattr(worker, "main_task", None)),
        )


def install() -> None:
    """Patch arq's ``Worker``; a no-op when called again."""
    if getattr(arq_worker.Worker, "_supoclip_resilient", False):
        return
    arq_worker.Worker.start_jobs = start_jobs
    arq_worker.Worker._poll_iteration = _poll_iteration
    arq_worker.Worker._supoclip_resilient = True
