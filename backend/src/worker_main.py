"""
Worker process entry point.
Run this to start background job workers.

Usage:
    arq src.workers.tasks.WorkerSettings
"""

import logging
from arq import run_worker
from .workers.tasks import WorkerSettings
from .config import Config
from .observability import configure_logging

configure_logging()

logger = logging.getLogger(__name__)

if __name__ == "__main__":
    logger.info("Starting SupoClip worker...")
    logger.info(f"Redis: {Config().redis_host}:{Config().redis_port}")
    run_worker(WorkerSettings)
