"""
Worker tasks - background jobs processed by arq workers.
"""

import logging
from typing import Dict, Any, Optional
import json

from ..observability import configure_logging, set_trace_id
from ..repositories.task_repository import TaskRepository

configure_logging()

logger = logging.getLogger(__name__)


async def process_video_task(
    ctx: Dict[str, Any],
    task_id: str,
    url: str,
    source_type: str,
    user_id: str,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    processing_mode: str = "fast",
    output_format: str = "vertical",
    add_subtitles: bool = True,
    cleanup_settings: Dict[str, Any] | None = None,
    hook_style: Dict[str, Any] | None = None,
    social_overlay: Dict[str, Any] | None = None,
    target_duration_seconds: float | None = None,
    max_clips: int | None = None,
) -> Dict[str, Any]:
    """
    Background worker task to process a video.

    Args:
        ctx: arq context (provides Redis connection and other utilities)
        task_id: Task ID to update
        url: Video URL or file path
        source_type: "youtube" or "upload"
        user_id: User ID who created the task
        font_family: Font family for subtitles
        font_size: Font size for subtitles
        font_color: Font color for subtitles

    Returns:
        Dict with processing results
    """
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.task_service import TaskService
    from ..workers.progress import ProgressTracker

    set_trace_id(f"task-{task_id}")
    logger.info(f"Worker processing task {task_id}")

    # Create progress tracker
    progress = ProgressTracker(ctx["redis"], task_id)

    async with AsyncSessionLocal() as db:
        await load_runtime_settings_cache(db)
        task_service = TaskService(db)

        try:
            # Progress callback
            async def update_progress(
                percent: int,
                message: str,
                status: str = "processing",
                stage: str | None = None,
            ):
                await progress.update(percent, message, status, stage=stage)
                logger.info(f"Task {task_id}: {percent}% - {message}")

            async def should_cancel() -> bool:
                cancelled = await ctx["redis"].get(f"task_cancel:{task_id}")
                return bool(cancelled)

            async def clip_ready_callback(
                clip_index: int, total_clips: int, clip_data: dict
            ):
                await progress.clip_ready(clip_index, total_clips, clip_data)

            async def clip_started_callback(clip_index: int, total_clips: int):
                await progress.clip_started(clip_index, total_clips)

            # Process the video
            result = await task_service.process_task(
                task_id=task_id,
                url=url,
                source_type=source_type,
                user_id=user_id,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                processing_mode=processing_mode,
                output_format=output_format,
                add_subtitles=add_subtitles,
                progress_callback=update_progress,
                should_cancel=should_cancel,
                clip_ready_callback=clip_ready_callback,
                clip_started_callback=clip_started_callback,
                cleanup_settings=cleanup_settings,
                hook_style=hook_style,
                social_overlay=social_overlay,
                target_duration_seconds=target_duration_seconds,
                max_clips=max_clips,
            )

            logger.info(f"Task {task_id} completed successfully")
            return result

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            try:
                job_try = int(ctx.get("job_try", 1))
                max_tries = int(getattr(WorkerSettings, "max_tries", 3))
                if job_try >= max_tries:
                    payload = {
                        "task_id": task_id,
                        "error": str(e),
                        "tries": job_try,
                    }
                    await ctx["redis"].set(
                        f"dead_letter:{task_id}", json.dumps(payload)
                    )
                    await ctx["redis"].sadd("tasks:dead_letter", task_id)
                    await progress.error("Task failed permanently after retries")
            except Exception:
                logger.exception("Failed to persist dead-letter payload")
            # Error will be caught by arq and task status will be updated
            raise


async def process_ranking_task(ctx: Dict[str, Any], task_id: str) -> Dict[str, Any]:
    """Background worker job for the Ranking/Compilation tool: renders one
    compilation video from a ranking task's ordered input videos (see
    RankingService.process_ranking_complete). Mirrors process_video_task's
    session/progress/error-handling shape."""
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.ranking_service import RankingService
    from ..workers.progress import ProgressTracker

    set_trace_id(f"ranking-{task_id}")
    logger.info(f"Worker processing ranking task {task_id}")

    progress = ProgressTracker(ctx["redis"], task_id)

    async with AsyncSessionLocal() as db:
        await load_runtime_settings_cache(db)
        ranking_service = RankingService(db)

        async def update_progress(
            percent: int, message: str, status: str = "processing", stage: str | None = None
        ):
            await progress.update(percent, message, status, stage=stage)
            logger.info(f"Ranking task {task_id}: {percent}% - {message}")

        try:
            result = await ranking_service.process_ranking_complete(
                task_id=task_id, progress_callback=update_progress
            )
            logger.info(f"Ranking task {task_id} completed successfully")
            return result
        except Exception as e:
            logger.error(f"Ranking task {task_id} failed: {e}", exc_info=True)
            try:
                job_try = int(ctx.get("job_try", 1))
                max_tries = int(getattr(WorkerSettings, "max_tries", 3))
                if job_try >= max_tries:
                    await db.rollback()
                    await TaskRepository.update_task_status(
                        db, task_id, "error", progress_message=str(e)
                    )
                    payload = {
                        "task_id": task_id,
                        "error": str(e),
                        "tries": job_try,
                    }
                    await ctx["redis"].set(
                        f"dead_letter:{task_id}", json.dumps(payload)
                    )
                    await ctx["redis"].sadd("tasks:dead_letter", task_id)
                    await progress.error("Ranking task failed permanently after retries")
            except Exception:
                logger.exception("Failed to persist ranking task failure state")
            # Error will be caught by arq and task status will be updated
            raise


async def process_batch_queue_task(ctx: Dict[str, Any], batch_queue_id: str) -> None:
    """Background worker job for a batch queue: walks its items sequentially
    (see BatchQueueService.run_batch). Pause/cancel is signaled the same way
    single-task cancellation is — a Redis key checked between items."""
    from ..database import AsyncSessionLocal
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.batch_queue_service import BatchQueueService

    set_trace_id(f"batch-{batch_queue_id}")
    logger.info(f"Worker processing batch queue {batch_queue_id}")

    async def should_pause_or_cancel() -> Optional[str]:
        cancelled = await ctx["redis"].get(f"batch_cancel:{batch_queue_id}")
        if cancelled:
            return "cancel"
        paused = await ctx["redis"].get(f"batch_pause:{batch_queue_id}")
        if paused:
            return "pause"
        return None

    async with AsyncSessionLocal() as db:
        await load_runtime_settings_cache(db)
        try:
            await BatchQueueService(db).run_batch(
                batch_queue_id, should_pause_or_cancel=should_pause_or_cancel
            )
        except Exception:
            logger.exception("Batch queue %s failed", batch_queue_id)
            raise


# Worker configuration for arq
class WorkerSettings:
    """Configuration for arq worker."""

    from ..config import Config
    from arq.connections import RedisSettings

    config = Config()

    # Functions to run
    functions = [process_video_task, process_batch_queue_task, process_ranking_task]
    queue_name = "supoclip_tasks"

    # Redis settings from environment
    redis_settings = RedisSettings(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, database=0
    )

    # Retry settings
    max_tries = 3  # Retry failed jobs up to 3 times
    job_timeout = 10800  # 3 hour timeout for video processing

    # Worker pool settings
    max_jobs = 4  # Process up to 4 jobs simultaneously
    cron_jobs = []
