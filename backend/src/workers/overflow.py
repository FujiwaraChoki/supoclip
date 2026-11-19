"""Let an always-on render host take new video jobs before an extra worker.

Every video worker reads the same queue, and the first to ask gets a job. An
extra worker (a cloud machine started by the render autoscaler) would win a
share of new jobs even while the always-on host has free slots, so it would
rarely be idle long enough to be removed.

An extra worker sets ``VIDEO_WORKER_OVERFLOW_AFTER_SECONDS``: it then only
reads jobs that have been due for at least that long. An always-on host polls
every 0.5 s, so it takes a new job first whenever it has a free slot; the extra
worker gets what that host cannot take. Nothing starves: a job nobody else has
started is taken once it is that old. Unset or 0 (the default) changes nothing.

The setting lives in the worker's env file, so it reaches a machine whatever
its compose file says.
"""

from __future__ import annotations

import logging
import math
import os

from arq.utils import timestamp_ms

logger = logging.getLogger(__name__)

OVERFLOW_ENV = "VIDEO_WORKER_OVERFLOW_AFTER_SECONDS"


def overflow_after_seconds(value: str | None = None) -> float:
    raw = os.getenv(OVERFLOW_ENV, "") if value is None else value
    try:
        seconds = float(raw.strip() or 0)
    except ValueError:
        logger.warning("Ignoring %s=%r: not a number", OVERFLOW_ENV, raw)
        return 0.0
    return seconds if math.isfinite(seconds) and seconds > 0 else 0.0


def take_only_overflow(pool, queue_name: str, seconds: float) -> None:
    """Make this worker's reads of ``queue_name`` skip jobs due less than ``seconds`` ago.

    ARQ picks jobs with ``zrangebyscore(queue, max=now)`` on the worker's own
    pool (``ctx["redis"]``); the score is when a job is due, in milliseconds.
    Only reads of the queue are shifted. Job code does not range over the queue.
    """
    read = pool.zrangebyscore
    hold_ms = int(seconds * 1000)

    async def zrangebyscore(name, *args, **kwargs):
        key = name.decode() if isinstance(name, bytes) else name
        if key == queue_name:
            latest = timestamp_ms() - hold_ms
            if "max" in kwargs:
                kwargs["max"] = _earlier(kwargs["max"], latest)
            elif len(args) >= 2:
                args = (args[0], _earlier(args[1], latest), *args[2:])
        return await read(name, *args, **kwargs)

    pool.zrangebyscore = zrangebyscore


def _earlier(bound, latest: int):
    if isinstance(bound, (int, float)):
        return min(bound, latest)
    # '+inf' and other string bounds.
    return latest
