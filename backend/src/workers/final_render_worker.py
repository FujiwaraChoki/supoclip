"""Worker for on-demand full-quality renders of preview clips.

Started next to the video worker by ``run_workers`` (``EMBED_FINAL_RENDER_WORKER``),
on every machine that renders video. It drains its own queue,
``supoclip_finals``, so a user waiting on a download is never stuck behind
generation jobs that hold every ``supoclip_tasks`` slot for hours. A full
render is one encode of a clip-length file, a minute or two at most.
"""

from __future__ import annotations

from arq.connections import RedisSettings
from arq.worker import func

from ..config import Config
from ..observability import configure_logging
from ..services.full_render_service import (
    FULL_RENDER_JOB_TIMEOUT_SECONDS,
    FULL_RENDER_QUEUE,
    render_full_clip,
)
from .tasks import on_worker_shutdown, on_worker_startup

configure_logging()


class FinalRenderWorkerSettings:
    config = Config()
    # One try: a lost or failed render is marked on the clip and the next
    # request queues it again.
    functions = [
        func(render_full_clip, timeout=FULL_RENDER_JOB_TIMEOUT_SECONDS, max_tries=1, keep_result=0)
    ]
    queue_name = FULL_RENDER_QUEUE
    redis_settings = RedisSettings(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, database=0
    )
    max_jobs = config.full_render_max_jobs
    job_timeout = FULL_RENDER_JOB_TIMEOUT_SECONDS
    health_check_interval = 30
    # Same media-volume check and scratch maintenance as the video worker.
    on_startup = on_worker_startup
    on_shutdown = on_worker_shutdown
