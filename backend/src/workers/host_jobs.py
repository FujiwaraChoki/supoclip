"""Which video-queue jobs are running on which machine.

ARQ's ``arq:in-progress:<job>`` keys say a job is running, not where. The
render autoscaler needs to know that a cloud worker has nothing running before
it drains and deletes the machine, so every video worker records its jobs in a
sorted set per host: member = job id, score = start time. The entry is removed
when the job ends. A worker that dies cannot remove its entries, so readers
ignore entries older than the job timeout plus a margin, and the key itself
expires.

Recording never fails a job: Redis errors are logged and ignored.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any

logger = logging.getLogger(__name__)

HOST_JOBS_PREFIX = "supoclip:render-host-jobs:"
# A job older than this cannot still be running (job_timeout is 3 h).
STALE_AFTER_SECONDS = 10800 + 600


def host_name() -> str:
    """The machine's name. Workers use host networking, so this is the server's hostname."""
    return (os.getenv("RENDER_HOST_NAME") or socket.gethostname()).strip()


def host_jobs_key(host: str | None = None) -> str:
    return HOST_JOBS_PREFIX + (host or host_name())


async def record_job_start(ctx: dict[str, Any]) -> None:
    job_id = ctx.get("job_id")
    redis = ctx.get("redis")
    if not job_id or redis is None:
        return
    key = host_jobs_key()
    try:
        await redis.zadd(key, {job_id: time.time()})
        await redis.expire(key, STALE_AFTER_SECONDS)
    except Exception:
        logger.exception("Could not record job %s on this host", job_id)


async def record_job_end(ctx: dict[str, Any]) -> None:
    job_id = ctx.get("job_id")
    redis = ctx.get("redis")
    if not job_id or redis is None:
        return
    try:
        await redis.zrem(host_jobs_key(), job_id)
    except Exception:
        logger.exception("Could not clear job %s on this host", job_id)


async def running_jobs(redis, host: str, *, now: float | None = None) -> int:
    """Jobs that started on ``host`` within the stale horizon and have not ended."""
    now = time.time() if now is None else now
    return int(await redis.zcount(host_jobs_key(host), now - STALE_AFTER_SECONDS, "+inf"))
