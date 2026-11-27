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
- a poll iteration that takes longer than ``POLL_DEADLINE_SECONDS`` is
  abandoned, idle connections are dropped and the next poll starts on a fresh
  one (see "Deadline" below);
- a poll loop that has not finished an iteration for ``STALL_WARNING_SECONDS``
  logs where it is waiting, once per stall, so a silent stall can be traced.

Running jobs are untouched: they use their own Redis calls and already retry.

Deadline
--------
A poll is a handful of small commands and takes milliseconds. One command on a
dead connection does not fail quickly, though: redis-py waits out the socket
timeout on the write and on the read, then reconnects and tries again, up to
``REDIS_CONNECT_RETRIES`` times, so a single command could hold the loop for
several minutes while the worker took no jobs and stopped writing its
heartbeat (arq writes it from the poll). The deadline cuts that short.
Cancelling a command makes redis-py close its connection, and we also drop the
idle ones, so the next poll opens a new connection.

An abandoned claim must not lose or double-run its job. arq claims a job by
setting ``arq:in-progress:<job>`` inside ``WATCH``/``MULTI``/``EXEC``; every
worker skips a job while that key exists, and it only expires after the job
timeout (hours). The job stays in the queue until it finishes, so:

- cut off before ``EXEC`` was sent, nothing was written and the job is simply
  picked up by a later poll;
- cut off after ``EXEC`` was sent (or ``EXEC`` failed without a reply), the
  key may be set now, or later if the command is still on its way. Each claim
  writes its own token as the key's value, and the next poll runs
  ``_RELEASE_CLAIM``: it deletes the key if it still holds our token (we never
  started the job, so nobody runs it), and otherwise writes and deletes the
  key, which fails the ``WATCH`` of an ``EXEC`` still on its way. A key with
  another value is another worker's claim and is left alone. Until that runs
  the job waits (another worker skips it as running), exactly as for a worker
  that died mid-claim. Nothing reads the key's value; arq only checks that it
  exists.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
from uuid import uuid4

from arq import worker as arq_worker
from arq.constants import in_progress_key_prefix
from arq.utils import timestamp_ms
from redis.exceptions import RedisError, ResponseError, WatchError

logger = logging.getLogger(__name__)

STALL_WARNING_SECONDS = 180
_WATCHDOG_INTERVAL_SECONDS = 30
# Below the heartbeat's lifetime (health_check_interval + 1s), so one hung
# command no longer lets the heartbeat expire for minutes.
POLL_DEADLINE_SECONDS = 20

# Errors that mean "Redis didn't answer this time", not a bug in our code.
_TRANSIENT = (RedisError, OSError, asyncio.TimeoutError)

_original_poll_iteration = arq_worker.Worker._poll_iteration


_RELEASE_CLAIM = """
local value = redis.call('GET', KEYS[1])
if value == ARGV[1] then
    redis.call('DEL', KEYS[1])
    return 1
end
if not value then
    -- Not set (yet): a write fails the WATCH of an EXEC still on its way.
    redis.call('SET', KEYS[1], ARGV[1], 'PX', 1000)
    redis.call('DEL', KEYS[1])
end
return 0
"""


async def _claim(worker: arq_worker.Worker, job_id: str) -> int | None:
    """Mark ``job_id`` in progress; its queue score if this worker got it."""
    in_progress_key = in_progress_key_prefix + job_id
    token = uuid4().hex.encode()
    pipe = worker.pool.pipeline(transaction=True)
    finished = False
    try:
        await pipe.watch(in_progress_key)
        ongoing_exists = await pipe.exists(in_progress_key)
        score = await pipe.zscore(worker.queue_name, job_id)
        if ongoing_exists or not score or score > timestamp_ms():
            # Running elsewhere, already finished, or re-queued with a delay.
            finished = True
            return None
        pipe.multi()
        pipe.psetex(in_progress_key, int(worker.in_progress_timeout_s * 1000), token)
        try:
            await pipe.execute()
        except BaseException as error:
            # The EXEC may have run, or may still run: release it next poll.
            _unsettled_claims(worker)[job_id] = token
            if isinstance(error, (ResponseError, WatchError)):
                # Another worker claimed it, or the connection died under the WATCH.
                return None
            raise
        finished = True
    finally:
        await _close_pipeline(pipe, finished)
    return int(score)


async def _close_pipeline(pipe, finished: bool) -> None:
    """Hand the claim's connection back to the pool, never leaking it.

    redis-py's ``reset()`` sends UNWATCH first and only returns the connection
    if that works or raises ``ConnectionError``; a read timeout or a
    cancellation there leaves it checked out for good ("Unclosed Pipeline
    client" when the pipeline is collected). A closed connection drops its
    WATCH on the server too, so when the claim did not finish normally we
    close it instead of sending UNWATCH.
    """
    if finished:
        try:
            await pipe.reset()
            return
        except BaseException:
            await _force_close(pipe)
            raise
    await _force_close(pipe)


async def _force_close(pipe) -> None:
    if pipe.connection is not None:
        try:
            await pipe.connection.disconnect(nowait=True)
        except Exception:
            pass
    pipe.watching = False
    await pipe.reset()


def _unsettled_claims(worker: arq_worker.Worker) -> dict[str, bytes]:
    claims = getattr(worker, "_supoclip_unsettled_claims", None)
    if claims is None:
        claims = worker._supoclip_unsettled_claims = {}
    return claims


async def _settle_abandoned_claims(worker: arq_worker.Worker) -> None:
    """Release every claim whose EXEC had no clear answer (module docstring)."""
    claims = _unsettled_claims(worker)
    for job_id, token in list(claims.items()):
        try:
            released = await worker.pool.eval(
                _RELEASE_CLAIM, 1, in_progress_key_prefix + job_id, token
            )
        except ResponseError as error:
            # Redis refuses the script (e.g. no EVAL permission): the claim
            # expires with the job timeout, as it did before this release.
            del claims[job_id]
            logger.error("Could not release the claim on job %s: %s", job_id, error)
            continue
        except _TRANSIENT as error:
            logger.warning(
                "Could not release the claim on job %s yet (%s: %s)",
                job_id,
                type(error).__name__,
                error,
            )
            return
        del claims[job_id]
        if released:
            logger.warning(
                "Released job %s: its claim was cut off but had reached Redis; "
                "it is queued again",
                job_id,
            )


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
        except BaseException as error:
            # Also when the poll deadline cancels the claim: the slot goes back.
            self.job_counter = self.job_counter - 1
            self.sem.release()
            if not isinstance(error, _TRANSIENT):
                raise
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
    deadline = asyncio.timeout(POLL_DEADLINE_SECONDS)
    try:
        async with deadline:
            await _settle_abandoned_claims(self)
            await _original_poll_iteration(self)
    except _TRANSIENT as error:
        if deadline.expired():
            logger.warning(
                "A poll took longer than %gs; abandoned it and reconnecting to Redis",
                POLL_DEADLINE_SECONDS,
            )
            await _drop_idle_connections(self)
            # The heartbeat may have been cut off after arq noted it as written.
            self._last_health_check = 0
        else:
            logger.warning(
                "Redis failed during a poll (%s: %s); polling again",
                type(error).__name__,
                error,
            )
    finally:
        self._supoclip_last_poll = time.monotonic()


async def _drop_idle_connections(worker: arq_worker.Worker) -> None:
    """Close the pool's idle connections; they reconnect on their next use.

    ``disconnect(nowait=True)`` never suspends, so no running job can take one
    of these connections from the pool while we close it.
    """
    for connection in list(worker.pool.connection_pool._available_connections):
        try:
            await connection.disconnect(nowait=True)
        except Exception as error:
            logger.debug("Could not close an idle Redis connection: %s", error)


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
