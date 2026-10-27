"""ARQ worker for AI editing.

Runs on its own queue and container so a creator's edit request answers in
seconds even while the main worker is busy rendering. It imports only the
editor and AI modules, not the video pipeline.

Run with: arq src.workers.ai_worker.AIWorkerSettings
"""

import os

# Before observability reads it: logs and errors from here are the AI worker's.
os.environ["SUPOCLIP_SERVICE"] = "ai-worker"

from arq.connections import RedisSettings  # noqa: E402
from arq.worker import func  # noqa: E402

from ..config import Config  # noqa: E402
from ..observability import configure_logging  # noqa: E402
from ..services.ai_edit_service import (  # noqa: E402
    AI_EDIT_QUEUE,
    AI_EDIT_TIMEOUT_SECONDS,
    ai_edit_job,
)

configure_logging()


class AIWorkerSettings:
    config = Config()
    # One try: the browser shows the failure and the creator can resend.
    functions = [func(ai_edit_job, timeout=AI_EDIT_TIMEOUT_SECONDS * 3, max_tries=1, keep_result=0)]
    queue_name = AI_EDIT_QUEUE
    redis_settings = RedisSettings(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, database=0
    )
    # Jobs are network-bound (Claude, Pexels search), so many can run at once.
    max_jobs = 16
    health_check_interval = 30
