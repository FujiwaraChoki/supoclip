"""
Worker tasks - background jobs processed by arq workers.
"""

import logging
import random
from typing import Dict, Any, Optional
import json
from arq import Retry
from arq.worker import func
from sqlalchemy.exc import TimeoutError as PoolTimeout
from ..repositories.task_run_guard import TaskRunBusy, TaskRunUnavailable
from ..youtube_proxy_trial import run_trial_job

from ..observability import configure_logging, set_trace_id, set_user_id

configure_logging()

logger = logging.getLogger(__name__)

UNSTARTED_TASK_MESSAGE = "The server was too busy to start this video. Please try again."


def _job_try(ctx: Dict[str, Any]) -> int:
    return int(ctx.get("job_try", 1))


def _is_last_try(ctx: Dict[str, Any]) -> bool:
    return _job_try(ctx) >= int(getattr(WorkerSettings, "max_tries", 3))


def _pool_retry_delay(ctx: Dict[str, Any]) -> float:
    # Jobs that started together (a worker restart) come back spread out.
    return min(60, 5 * 2 ** (_job_try(ctx) - 1)) + random.uniform(0, 5)


async def _record_dead_letter(ctx: Dict[str, Any], task_id: str, progress, error: Exception) -> None:
    try:
        if _is_last_try(ctx):
            payload = {
                "task_id": task_id,
                "error": str(error),
                "tries": _job_try(ctx),
            }
            await ctx["redis"].set(
                f"dead_letter:{task_id}", json.dumps(payload)
            )
            await ctx["redis"].sadd("tasks:dead_letter", task_id)
            await progress.error("Task failed permanently after retries")
    except Exception:
        logger.exception("Failed to persist dead-letter payload")


async def _fail_unstarted_task(ctx: Dict[str, Any], task_id: str) -> None:
    """Fail and refund a task whose last try could not start.

    Holds the run guard so a live render or a resume is left alone, and leaves a
    task that has since moved to another job (resume, deferral) to that job.
    """
    from ..database import AsyncSessionLocal
    from ..repositories.task_repository import TaskRepository
    from ..repositories.task_run_guard import task_run_guard

    try:
        async with AsyncSessionLocal() as db:
            async with task_run_guard(db, task_id):
                metadata = await TaskRepository.load_source_metadata(db, task_id) or {}
                current_job = metadata.get("processing_job_id")
                if current_job and ctx.get("job_id") and current_job != ctx["job_id"]:
                    return
                await TaskRepository.update_task_status(
                    db,
                    task_id,
                    "error",
                    progress=0,
                    progress_message=UNSTARTED_TASK_MESSAGE,
                    expected_statuses=["queued", "processing"],
                )
    except TaskRunBusy:
        logger.info("Task %s is owned by another run; leaving it alone", task_id)
    except Exception:
        # The stale-task sweep is the backstop.
        logger.exception("Could not fail unstarted task %s", task_id)


async def _retry_unstarted_job(ctx: Dict[str, Any], task_id: str, progress, error: Exception) -> None:
    """No database connection came free before the job did anything.

    Retry while tries remain. On the last one, fail and refund the task so it
    does not stay queued and charged, then let the error fail the job.
    """
    if not _is_last_try(ctx):
        delay = _pool_retry_delay(ctx)
        logger.warning(
            "Task %s could not get a database connection (try %s); retrying in %.0fs: %s",
            task_id, _job_try(ctx), delay, error,
        )
        raise Retry(defer=delay)
    logger.error(f"Task {task_id} failed: {error}", exc_info=error)
    await _fail_unstarted_task(ctx, task_id)
    await _record_dead_letter(ctx, task_id, progress, error)
    raise error


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
    clip_length: str = "auto",
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
    set_user_id(user_id)
    logger.info(f"Worker processing task {task_id}")

    from ..font_registry import fetch_user_font
    from ..utils.async_helpers import run_in_thread
    from .video_admission import (
        UPLOAD_LOST_MESSAGE,
        UPLOAD_WAIT_MESSAGE,
        admit_video_job,
        hold_for_upload,
        release_video_slot,
        wake_next_video_jobs,
    )

    job_args = (
        task_id,
        url,
        source_type,
        user_id,
        font_family,
        font_size,
        font_color,
        caption_template,
        processing_mode,
        output_format,
        add_subtitles,
        cleanup_settings,
        clip_length,
    )

    # Create progress tracker
    progress = ProgressTracker(ctx["redis"], task_id)

    async with AsyncSessionLocal() as db:
        try:
            await load_runtime_settings_cache(db)
            decision = await admit_video_job(
                ctx, db, task_id=task_id, user_id=user_id, job_args=job_args, url=url
            )
        except PoolTimeout as e:
            await _retry_unstarted_job(ctx, task_id, progress, e)
        if decision != "run":
            logger.info("Video task %s admission=%s", task_id, decision)
            return {"task_id": task_id, "admission": decision}

        # An upload this machine cannot read yet goes back on the queue.
        decision = await hold_for_upload(
            ctx, db, task_id=task_id, user_id=user_id, url=url, job_args=job_args
        )
        if decision != "run":
            if decision == "failed":
                await progress.error(UPLOAD_LOST_MESSAGE)
            else:
                await progress.update(0, UPLOAD_WAIT_MESSAGE, "queued")
            return {"task_id": task_id, "admission": decision}

        # Admission only read. Close that transaction before the render writes.
        await db.commit()
        task_service = TaskService(db)
        if font_family:
            try:
                # A font the user uploaded lives on the API's disk and in object storage.
                await run_in_thread(fetch_user_font, font_family, user_id)
            except Exception:
                logger.warning("Could not fetch font for task %s; captions use the default", task_id, exc_info=True)

        try:
            # Progress callback
            async def update_progress(
                percent: int, message: str, status: str = "processing"
            ):
                await progress.update(percent, message, status)
                logger.info(f"Task {task_id}: {percent}% - {message}")

            async def should_cancel() -> bool:
                cancelled = await ctx["redis"].get(f"task_cancel:{task_id}")
                return bool(cancelled)

            async def clip_ready_callback(
                clip_index: int, total_clips: int, clip_data: dict
            ):
                await progress.clip_ready(clip_index, total_clips, clip_data)

            # Process the video
            result = await run_trial_job(
                task_service.process_task, ctx["redis"], task_id, source_type,
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
                cleanup_settings=cleanup_settings,
                clip_length=clip_length,
            )

            logger.info(f"Task {task_id} completed successfully")
            return result

        except TaskRunBusy:
            # A resume request may still own the guard while publishing this job.
            # Retry without rewriting task state or creating a dead-letter entry.
            raise Retry(defer=2)
        except TaskRunUnavailable as e:
            # The run guard's first read found the pool exhausted, before any work.
            await _retry_unstarted_job(ctx, task_id, progress, e)
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}", exc_info=True)
            await _record_dead_letter(ctx, task_id, progress, e)
            # Error will be caught by arq and task status will be updated
            raise
        finally:
            # The slot is held only while this process is actually rendering.
            await release_video_slot(ctx["redis"], user_id, task_id)
            # Hand it to the account's next task now instead of after its deferral.
            try:
                async with AsyncSessionLocal() as wake_db:
                    await wake_next_video_jobs(wake_db, ctx["redis"], user_id)
            except Exception:
                logger.exception("Could not wake the next video task after task %s", task_id)


async def _social_service(db):
    from ..runtime_settings import load_runtime_settings_cache
    from ..services.social_service import SocialService

    await load_runtime_settings_cache(db)
    return SocialService(db)


async def publish_social_post(ctx: Dict[str, Any], post_id: str) -> Dict[str, Any]:
    """Upload one queued social post to its platform."""
    from ..database import AsyncSessionLocal

    set_trace_id(f"social-{post_id}")
    logger.info("Worker publishing social post %s", post_id)
    async with AsyncSessionLocal() as db:
        service = await _social_service(db)
        try:
            post = await service.publish_post(post_id)
        except Exception as exc:
            # Failures are recorded on the post row; never let arq retry blindly.
            # Cancellation (job timeout, shutdown) propagates; the stale
            # publishing sweep fails that post. Logged without a traceback
            # because provider errors can carry request URLs.
            from ..social.base import redact

            logger.error(
                "Publishing social post %s crashed (%s): %s",
                post_id,
                type(exc).__name__,
                redact(exc),
            )
            return {"post_id": post_id, "status": "error"}
    return {"post_id": post_id, "status": post.get("status")}


async def dispatch_scheduled_social_posts(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Cron: move due scheduled posts onto the publish queue."""
    from ..database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        service = await _social_service(db)
        dispatched = await service.dispatch_scheduled_posts()
        resolved = await service.resolve_pending_posts()
        # Covers workers that crashed or were cancelled mid-upload.
        swept = await service.sweep_stale_publishing()
    return {
        "dispatched": len(dispatched),
        "pending_checked": resolved,
        "stale_failed": len(swept),
    }


async def refresh_social_metrics(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Cron: pull fresh audience metrics for recently published posts."""
    from ..database import AsyncSessionLocal
    from ..repositories.social_repository import SocialRepository

    async with AsyncSessionLocal() as db:
        service = await _social_service(db)
        refreshed = await service.refresh_due_metrics()
        purged = await SocialRepository.purge_expired_oauth_states(db)
    return {"refreshed": refreshed, "purged_oauth_states": purged}


SCRATCH_SWEEP_SECONDS = 10 * 60
ORPHAN_SWEEP_EVERY = 36  # passes, about six hours


async def check_media_volume(redis) -> None:
    """Refuse to render into a disk the API cannot read.

    With local storage, finished clips are files the API serves from its own
    disk. That only works when this worker mounts the same volume. A worker
    on another machine needs MEDIA_STORAGE=s3.
    """
    from ..config import get_config
    from ..local_volume import API_VOLUME_KEY, volume_id

    config = get_config()
    if config.media_storage == "s3":
        return
    api_volume = await redis.get(API_VOLUME_KEY)
    if isinstance(api_volume, bytes):
        api_volume = api_volume.decode()
    if api_volume and api_volume != volume_id(config.temp_dir):
        raise RuntimeError(
            "This worker does not share the API's media volume (TEMP_DIR). "
            "Mount the same volume, or set MEDIA_STORAGE=s3 on the API and every worker "
            "so they exchange media through object storage."
        )


async def _maintain_scratch_forever() -> None:
    """Each machine cleans its own disk; the scheduler only sees the one it runs on."""
    import asyncio
    import random

    from .media_cleanup import maintain_local_scratch

    passes = 0
    # Workers started together should not all sweep in the same second.
    await asyncio.sleep(random.uniform(30, 120))
    while True:
        try:
            await maintain_local_scratch(orphans=passes % ORPHAN_SWEEP_EVERY == 0)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Local scratch cleanup failed; it runs again next pass")
        passes += 1
        await asyncio.sleep(SCRATCH_SWEEP_SECONDS)


async def on_worker_startup(ctx: Dict[str, Any]) -> None:
    import asyncio

    await check_media_volume(ctx["redis"])
    ctx["scratch_maintenance"] = asyncio.create_task(_maintain_scratch_forever())


async def on_worker_shutdown(ctx: Dict[str, Any]) -> None:
    import asyncio

    maintenance = ctx.get("scratch_maintenance")
    if maintenance is not None:
        maintenance.cancel()
        try:
            await maintenance
        except asyncio.CancelledError:
            pass


from ..services.editor_service import prepare_editor, export_editor, combine_editor
from ..services.clip_export_service import (
    CLIP_EXPORT_JOB_TIMEOUT_SECONDS,
    export_clip_preset,
)
from ..services.social_service import PUBLISH_JOB_TIMEOUT_SECONDS

# Worker configuration for arq
class WorkerSettings:
    """Configuration for arq worker."""

    from ..config import Config
    from arq.connections import RedisSettings

    config = Config()

    # Functions to run
    functions = [
        process_video_task,
        prepare_editor,
        export_editor,
        combine_editor,
        # One try: a lost running export is failed by the heartbeat sweep, and a
        # retry would start a second encode for the same row.
        func(
            export_clip_preset,
            timeout=CLIP_EXPORT_JOB_TIMEOUT_SECONDS,
            max_tries=1,
        ),
        # Results are not kept so a finished job's id ("social:{post}:{attempt}")
        # frees up; a dead job's post is failed by the stale-publishing sweep.
        func(publish_social_post, timeout=PUBLISH_JOB_TIMEOUT_SECONDS, keep_result=0),
    ]
    queue_name = "supoclip_tasks"
    health_check_interval = 30

    # Redis settings from environment
    redis_settings = RedisSettings(
        host=config.redis_host, port=config.redis_port, password=config.redis_password, database=0
    )

    # Retry settings
    max_tries = 3  # Retry failed jobs up to 3 times
    job_timeout = 10800  # 3 hour timeout for video processing

    # Worker pool settings. Cron jobs live on CronWorkerSettings so they never
    # take one of these slots or pile up behind a long render.
    max_jobs = 4  # Process up to 4 jobs simultaneously
    cron_jobs = []

    from .queue_delay import on_video_job_start as on_job_start

    on_startup = on_worker_startup
    on_shutdown = on_worker_shutdown
