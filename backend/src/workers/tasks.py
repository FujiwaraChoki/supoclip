"""
Worker tasks - background jobs processed by arq workers.
"""
import logging
from typing import Dict, Any
from pathlib import Path

logger = logging.getLogger(__name__)


async def process_video_task(
    ctx: Dict[str, Any],
    task_id: str,
    url: str,
    source_type: str,
    user_id: str,
    font_family: str = "TikTokSans-Regular",
    font_size: int = 24,
    font_color: str = "#FFFFFF"
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
    from ..services.task_service import TaskService
    from ..workers.progress import ProgressTracker

    logger.info(f"Worker processing task {task_id}")

    # Create progress tracker
    progress = ProgressTracker(ctx['redis'], task_id)

    async with AsyncSessionLocal() as db:
        task_service = TaskService(db)

        try:
            # Progress callback
            async def update_progress(percent: int, message: str):
                await progress.update(percent, message)
                logger.info(f"Task {task_id}: {percent}% - {message}")

            # Process the video
            result = await task_service.process_task(
                task_id=task_id,
                url=url,
                source_type=source_type,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                progress_callback=update_progress
            )

            logger.info(f"Task {task_id} completed successfully")
            return result

        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            # Error will be caught by arq and task status will be updated
            raise


# Import mass clip generation task
from .mass_clip_tasks import generate_mass_clips_task


# Worker configuration for arq
class WorkerSettings:
    """Configuration for arq worker."""

    from ..config import Config
    from arq.connections import RedisSettings

    config = Config()

    # Functions to run - include both regular and mass generation tasks
    functions = [process_video_task, generate_mass_clips_task]
    queue_name = "supoclip_tasks"

    # Redis settings from environment
    redis_settings = RedisSettings(
        host=config.redis_host,
        port=config.redis_port,
        database=0
    )

    # Retry settings
    max_tries = 3  # Retry failed jobs up to 3 times
    job_timeout = 7200  # 2 hour timeout for mass generation (was 1 hour)

    # Worker pool settings
    max_jobs = 4  # Process up to 4 jobs simultaneously
