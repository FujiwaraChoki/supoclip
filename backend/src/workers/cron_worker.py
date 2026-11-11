"""Maintenance jobs on their own queue.

Video renders share a few CPU slots and can run for hours. Cron jobs used to
be appended to that same queue, so a busy worker accumulated one new copy of
each schedule every time it came due. This worker drains ``supoclip_cron``
only. Each schedule uses one stable job id: while that job is queued or
running, arq refuses another copy.

The worker container starts this process next to the video worker. The
optional ``cron-worker`` Compose service (profile ``cron``) is the same
process on its own; set ``EMBED_CRON_WORKER=0`` on the video worker when
using it.

Run it on the machine that hosts the API. Uploads and user fonts are written
to that machine's disk first, and ``copy_pending_uploads`` and
``cleanup_media`` are what move the leftovers into object storage. A video
worker on another machine sets ``EMBED_CRON_WORKER=0`` and keeps its own disk
tidy with ``maintain_local_scratch``.
"""

import os

os.environ["SUPOCLIP_SERVICE"] = "cron-worker"

from arq import cron  # noqa: E402
from arq.connections import RedisSettings  # noqa: E402

from ..config import Config  # noqa: E402
from ..observability import configure_logging  # noqa: E402
from .media_cleanup import cleanup_media, copy_pending_uploads, sweep_orphaned_media  # noqa: E402
from .media_replication import check_media_storage, replicate_pending_media  # noqa: E402
from .tasks import (  # noqa: E402
    dispatch_scheduled_social_posts,
    refresh_social_metrics,
)

configure_logging()

CRON_QUEUE_NAME = "supoclip_cron"


def scheduled(coroutine, **kwargs):
    """One pending job per cron function, across every schedule tick."""
    name = f"cron:{coroutine.__name__}"
    return cron(coroutine, name=name, job_id=name, unique=True, **kwargs)


class CronWorkerSettings:
    config = Config()
    functions = []
    queue_name = CRON_QUEUE_NAME
    redis_settings = RedisSettings(
        host=config.redis_host,
        port=config.redis_port,
        password=config.redis_password,
        database=0,
    )
    max_jobs = 4
    max_tries = 1
    job_timeout = 3600
    health_check_interval = 30
    cron_jobs = [
        scheduled(check_media_storage, second=20, run_at_startup=True, timeout=120),
        scheduled(replicate_pending_media, second=40, run_at_startup=True, timeout=1800),
        scheduled(
            cleanup_media,
            minute={0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55},
            run_at_startup=True,
        ),
        scheduled(sweep_orphaned_media, hour={0, 6, 12, 18}, minute=15),
        scheduled(copy_pending_uploads, second=10, run_at_startup=True, timeout=1800),
        scheduled(dispatch_scheduled_social_posts, second=0, timeout=300),
        scheduled(refresh_social_metrics, minute=7, second=0, timeout=1800),
    ]
