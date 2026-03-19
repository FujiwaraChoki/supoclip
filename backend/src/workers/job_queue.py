"""
Job queue setup using arq (async Redis queue).
"""

import logging
from typing import Optional
from arq import create_pool
from arq.connections import RedisSettings, ArqRedis
from ..config import get_config

logger = logging.getLogger(__name__)

# Queue names
DEFAULT_QUEUE_NAME = "supoclip_tasks"
FAST_QUEUE_NAME = "supoclip_fast"


def _get_redis_settings() -> RedisSettings:
    config = get_config()
    return RedisSettings(host=config.redis_host, port=config.redis_port, password=config.redis_password, database=0)


class JobQueue:
    """Wrapper for arq job queue operations."""

    _pool: Optional[ArqRedis] = None

    @classmethod
    async def get_pool(cls) -> ArqRedis:
        """Get or create the Redis connection pool."""
        if cls._pool is None:
            config = get_config()
            cls._pool = await create_pool(_get_redis_settings())
            logger.info(
                f"Created arq Redis pool: {config.redis_host}:{config.redis_port}"
            )
        return cls._pool

    @classmethod
    async def close_pool(cls):
        """Close the Redis connection pool."""
        if cls._pool is not None:
            await cls._pool.close()
            cls._pool = None
            logger.info("Closed arq Redis pool")

    @classmethod
    async def enqueue_job(cls, function_name: str, *args, **kwargs) -> str:
        """
        Enqueue a job to be processed by workers.

        Args:
            function_name: Name of the worker function to call
            *args: Positional arguments for the function
            **kwargs: Keyword arguments for the function

        Returns:
            job_id: Unique ID for the enqueued job
        """
        pool = await cls.get_pool()
        queue_name = kwargs.pop("_queue_name", DEFAULT_QUEUE_NAME)
        job = await pool.enqueue_job(
            function_name, *args, _queue_name=queue_name, **kwargs
        )
        if not job:
            raise RuntimeError("Failed to enqueue job")
        job_id = getattr(job, "job_id", None)
        if not job_id:
            raise RuntimeError("Failed to enqueue job: missing job ID")

        logger.info(f"Enqueued job {job_id}: {function_name} on queue {queue_name}")
        return str(job_id)

    @classmethod
    async def enqueue_processing_job(
        cls, function_name: str, processing_mode: str, *args, **kwargs
    ) -> str:
        # Keep a single queue for now; processing_mode remains available for future
        # dedicated queue routing once multiple worker pools are configured.
        queue_name = DEFAULT_QUEUE_NAME
        return await cls.enqueue_job(
            function_name, *args, _queue_name=queue_name, **kwargs
        )

    @classmethod
    async def get_job_result(cls, job_id: str):
        """Get the result of a completed job."""
        pool = await cls.get_pool()
        job = await pool.job(job_id)
        if job:
            return await job.result()
        return None

    @classmethod
    async def get_job_status(cls, job_id: str) -> Optional[str]:
        """Get the status of a job."""
        pool = await cls.get_pool()
        job = await pool.job(job_id)
        if job:
            return await job.status()
        return None
