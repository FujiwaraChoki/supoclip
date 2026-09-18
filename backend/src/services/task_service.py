"""
Task service - orchestrates task creation and processing workflow.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, Callable
import logging
from datetime import datetime
from pathlib import Path
import json
import hashlib
from time import perf_counter


from ..repositories.task_repository import TaskRepository
from ..repositories.source_repository import SourceRepository
from ..repositories.clip_repository import ClipRepository
from ..repositories.cache_repository import CacheRepository
from .video_service import VideoService
from .clip_service import ClipEditingMixin
from ..repositories.edit_transaction import serialized_task_edit, task_edit_transaction, TaskCancelled
from ..repositories.task_run_guard import exclusive_task_run, task_run_guard
from .billing_service import BillingService
from .task_completion_email_service import (
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)
from ..config import Config, get_config
from ..youtube_utils import cleanup_downloaded_files, extract_video_id
from ..clip_cleanup import normalize_clip_cleanup_settings
from ..ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION
from ..clip_source_map import load_clip_caption_settings

logger = logging.getLogger(__name__)
PROCESSING_CACHE_VERSION = "20260319_grounded_segments_v1"


class TaskService(ClipEditingMixin):
    """Service for task workflow orchestration."""

    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.task_repo = TaskRepository()
        self.source_repo = SourceRepository()
        self.clip_repo = ClipRepository()
        self.cache_repo = CacheRepository()
        self.video_service = VideoService()
        self.config = config or get_config()

    @staticmethod
    def _build_cache_key(url: str, source_type: str, processing_mode: str) -> str:
        payload = (
            f"{source_type}|{processing_mode}|"
            f"{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url.strip()}"
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def _is_stale_queued_task(self, task: Dict[str, Any]) -> bool:
        """Detect queued tasks that have likely stalled due to worker issues."""
        if task.get("status") != "queued":
            return False

        return self._task_age_seconds(task) >= self.config.queued_task_timeout_seconds

    def _is_stale_processing_task(self, task: Dict[str, Any]) -> bool:
        """Detect processing tasks whose worker likely died mid-run.

        Recovers tasks stuck in "processing" (e.g. arq hard-killed the job past
        its timeout) so they don't stay "processing" forever. The configured
        timeout defaults well above arq's job_timeout, so a legitimately
        long-running job is never falsely swept into "error".
        """
        if task.get("status") != "processing":
            return False

        return (
            self._task_age_seconds(task)
            >= self.config.processing_task_timeout_seconds
        )

    @staticmethod
    def _task_age_seconds(task: Dict[str, Any]) -> float:
        """Seconds since the task's last update (or creation)."""
        created_at = task.get("created_at")
        updated_at = task.get("updated_at") or created_at

        if not created_at or not updated_at:
            return 0.0

        now = (
            datetime.now(updated_at.tzinfo)
            if getattr(updated_at, "tzinfo", None)
            else datetime.utcnow()
        )
        return (now - updated_at).total_seconds()

    @staticmethod
    def _cleanup_source_video(
        video_path: Optional[Path], source_type: str, url: str
    ) -> None:
        """Delete the downloaded source video + audio sidecar after a task.

        Generated clips and the tiny transcript-cache JSON are left in place.
        For YouTube sources, ``cleanup_downloaded_files`` removes downloaded
        media and partials but retains word timings. For uploads we remove the audio sidecar
        generated for transcription — the original upload is owned elsewhere.
        """
        if not video_path:
            return

        if source_type == "youtube":
            try:
                video_id = extract_video_id(url)
                if video_id:
                    cleanup_downloaded_files(video_id)
                    logger.info("Cleaned up YouTube source artifacts for %s", video_id)
                    return
            except Exception as e:
                logger.warning("Failed YouTube source cleanup: %s", e)

        # Fallback / upload path: at least drop the transcription audio sidecar
        # (e.g. "<stem>.transcription.mp3") next to the source file.
        try:
            sidecar = video_path.with_name(f"{video_path.stem}.transcription.mp3")
            if sidecar.exists():
                sidecar.unlink()
                logger.info("Removed transcription audio sidecar: %s", sidecar.name)
        except Exception as e:
            logger.warning("Failed to remove transcription sidecar: %s", e)

    async def create_task_with_source(
        self,
        user_id: str,
        url: str,
        title: Optional[str] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        include_broll: bool = False,
        processing_mode: str = "fast",
    ) -> str:
        """
        Create a new task with associated source.
        Returns the task ID.
        """
        # Validate user exists
        if not await self.task_repo.user_exists(self.db, user_id):
            raise ValueError(f"User {user_id} not found")

        # Determine source type
        source_type = self.video_service.determine_source_type(url)

        # Get or generate title
        if not title:
            if source_type == "youtube":
                title = await self.video_service.get_video_title(url)
            else:
                title = "Uploaded Video"

        # Create source
        source_id = await self.source_repo.create_source(
            self.db, source_type=source_type, title=title, url=url
        )

        # Create task
        task_id = await self.task_repo.create_task(
            self.db,
            user_id=user_id,
            source_id=source_id,
            status="queued",  # Changed from "processing" to "queued"
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            caption_template=caption_template,
            include_broll=include_broll,
            processing_mode=processing_mode,
        )

        logger.info(f"Created task {task_id} for user {user_id}")
        return task_id

    def _processing_transaction(self, task_id: str):
        return task_edit_transaction(self.db, task_id, processing=True)

    def _task_run_guard(self, task_id: str):
        return task_run_guard(self.db, task_id)

    @exclusive_task_run
    async def process_task(
        self,
        task_id: str,
        url: str,
        source_type: str,
        user_id: Optional[str] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        progress_callback: Optional[Callable] = None,
        should_cancel: Optional[Callable] = None,
        clip_ready_callback: Optional[Callable] = None,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Process a task: download video, analyze, create clips.
        Returns processing results.
        """
        # Tracked so the downloaded source video can be cleaned up on both the
        # success and error paths; multi-GB YouTube downloads otherwise pile up
        # in temp/ until the disk fills and later tasks fail.
        video_path: Optional[Path] = None
        pending_clip_path: Optional[Path] = None
        clips_output_dir = Path(self.config.temp_dir) / "clips"

        async def check_cancelled():
            if should_cancel and await should_cancel():
                raise TaskCancelled()

        try:
            await check_cancelled()
            logger.info(f"Starting processing for task {task_id}")
            started_at = datetime.utcnow()
            stage_timings: Dict[str, float] = {}
            cache_key = self._build_cache_key(url, source_type, processing_mode)

            cache_entry = await self.cache_repo.get_cache(self.db, cache_key)
            cached_transcript = (
                cache_entry.get("transcript_text") if cache_entry else None
            )
            cached_analysis_json = (
                cache_entry.get("analysis_json") if cache_entry else None
            )
            cache_hit = bool(cached_transcript and cached_analysis_json)

            await self.task_repo.update_task_runtime_metadata(
                self.db,
                task_id,
                started_at=started_at,
                cache_hit=cache_hit,
            )

            # Update status to processing
            started = await self.task_repo.update_task_status(
                self.db, task_id, "processing", progress=0,
                progress_message="Starting...",
                expected_statuses=["queued", "processing", "error", "completed"],
            )
            if started is False:
                raise TaskCancelled()

            # Progress callback wrapper
            async def update_progress(
                progress: int, message: str, status: str = "processing"
            ):
                await check_cancelled()
                changed = await self.task_repo.update_task_status(
                    self.db, task_id, status, progress=progress,
                    progress_message=message, expected_statuses=["processing"],
                )
                if changed is False:
                    raise TaskCancelled()
                if progress_callback:
                    await progress_callback(progress, message, status)

            # Process video with progress updates
            max_video_duration = self.config.max_video_duration
            if source_type == "youtube" and user_id:
                billing = await BillingService(self.db, self.config).get_usage_summary(
                    user_id
                )
                max_video_duration = self.config.max_youtube_video_duration_for_plan(
                    billing.get("plan"), billing.get("subscription_status")
                )

            pipeline_start = perf_counter()
            result = await self.video_service.process_video_complete(
                url=url,
                source_type=source_type,
                task_id=task_id,
                font_family=font_family,
                font_size=font_size,
                font_color=font_color,
                caption_template=caption_template,
                processing_mode=processing_mode,
                output_format=output_format,
                add_subtitles=add_subtitles,
                max_video_duration=max_video_duration,
                cached_transcript=cached_transcript,
                cached_analysis_json=cached_analysis_json,
                progress_callback=update_progress,
                should_cancel=should_cancel,
            )
            stage_timings["pipeline_seconds"] = round(
                perf_counter() - pipeline_start, 3
            )

            normalized_cleanup_settings = normalize_clip_cleanup_settings(
                **(cleanup_settings or {})
            )

            # Render clips incrementally: render, save, notify one at a time
            segments_to_render = result.get("segments_to_render", [])
            if not segments_to_render:
                await self.cache_repo.upsert_cache(
                    self.db,
                    cache_key=cache_key,
                    source_url=url,
                    source_type=source_type,
                    video_path=result.get("video_path"),
                    transcript_text=result.get("transcript"),
                    analysis_json=None,
                )
                raise ValueError(
                    "No usable clip segments were selected for this video."
                )

            await self.cache_repo.upsert_cache(
                self.db,
                cache_key=cache_key,
                source_url=url,
                source_type=source_type,
                video_path=result.get("video_path"),
                transcript_text=result.get("transcript"),
                analysis_json=result.get("analysis_json"),
            )

            video_path = Path(result["video_path"])
            total_clips = len(segments_to_render)
            clips_output_dir = Path(self.config.temp_dir) / "clips"
            clips_output_dir.mkdir(parents=True, exist_ok=True)

            # Retries and regenerations should replace earlier clip rows instead of
            # accumulating duplicates for the same task.
            async with self._processing_transaction(task_id):
                await check_cancelled()
                await self.clip_repo.delete_clips_by_task(self.db, task_id)
                await self.task_repo.update_task_clips(self.db, task_id, [])

            clip_ids = []
            render_start = perf_counter()

            for i, segment in enumerate(segments_to_render):
                # Check cancellation
                await check_cancelled()

                # Update progress: 70-95% spread across clips
                clip_progress = 70 + int(
                    ((i + 1) / total_clips) * 25
                ) if total_clips > 0 else 95
                await update_progress(
                    clip_progress,
                    f"Creating clip {i + 1}/{total_clips}...",
                )

                # Render single clip in thread pool
                clip_info = await self.video_service.create_single_clip(
                    video_path,
                    segment,
                    i,
                    clips_output_dir,
                    font_family,
                    font_size,
                    font_color,
                    caption_template,
                    output_format,
                    add_subtitles,
                    normalized_cleanup_settings,
                )
                pending_clip_path = Path(clip_info["path"]) if clip_info else None
                await check_cancelled()
                if clip_info is None:
                    continue  # Skip failed clip

                async with self._processing_transaction(task_id):
                    await check_cancelled()
                    # Save to DB immediately
                    clip_id = await self.clip_repo.create_clip(
                        self.db,
                        task_id=task_id,
                        filename=clip_info["filename"],
                        file_path=clip_info["path"],
                        start_time=clip_info["start_time"],
                        end_time=clip_info["end_time"],
                        duration=clip_info["duration"],
                        text=clip_info.get("text", ""),
                        relevance_score=clip_info.get("relevance_score", 0.0),
                        reasoning=clip_info.get("reasoning", ""),
                        clip_order=i + 1,
                        virality_score=clip_info.get("virality_score", 0),
                        hook_score=clip_info.get("hook_score", 0),
                        engagement_score=clip_info.get("engagement_score", 0),
                        value_score=clip_info.get("value_score", 0),
                        shareability_score=clip_info.get("shareability_score", 0),
                        hook_type=clip_info.get("hook_type"),
                        hook_title=clip_info.get("hook_title"),
                    )

                    # Update task's clip IDs array
                    await self.task_repo.update_task_clips(self.db, task_id, [*clip_ids, clip_id])

                    await check_cancelled()
                pending_clip_path = None
                clip_ids.append(clip_id)

                # Notify frontend via SSE
                if clip_ready_callback:
                    clip_record = await self.clip_repo.get_clip_by_id(
                        self.db, clip_id
                    )
                    if clip_record:
                        await clip_ready_callback(i, total_clips, clip_record)

            stage_timings["render_seconds"] = round(
                perf_counter() - render_start, 3
            )

            # A cancellation committed after the final render must win over completion.
            await check_cancelled()
            completed = await self.task_repo.update_task_status(
                self.db,
                task_id,
                "completed",
                progress=100,
                progress_message="Complete!",
                expected_statuses=["processing"],
            )
            if completed is False:
                raise TaskCancelled()

            if progress_callback:
                await progress_callback(100, "Complete!", "completed")

            await self.task_repo.update_task_runtime_metadata(
                self.db,
                task_id,
                completed_at=datetime.utcnow(),
                stage_timings_json=json.dumps(stage_timings),
                error_code="",
            )
            await self._send_completion_notification_if_needed(
                task_id=task_id,
                clips_count=len(clip_ids),
            )

            logger.info(
                f"Task {task_id} completed successfully with {len(clip_ids)} clips"
            )

            self._cleanup_source_video(video_path, source_type, url)

            return {
                "task_id": task_id,
                "clips_count": len(clip_ids),
                "segments": result["segments"],
                "summary": result.get("summary"),
                "key_topics": result.get("key_topics"),
            }

        except Exception as e:
            logger.error(f"Error processing task {task_id}: {e}")
            # Clear failed writes before recording the terminal error state.
            await self.db.rollback()
            if pending_clip_path and pending_clip_path != video_path:
                try:
                    if pending_clip_path.resolve().is_relative_to(clips_output_dir.resolve()):
                        pending_clip_path.unlink(missing_ok=True)
                        pending_clip_path.with_suffix(".source_map.json").unlink(missing_ok=True)
                except OSError:
                    logger.warning("Could not remove unfinished clip %s", pending_clip_path)
            self._cleanup_source_video(video_path, source_type, url)
            if str(e) == "Task cancelled":
                await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    "cancelled",
                    progress=0,
                    progress_message="Cancelled by user",
                    expected_statuses=["queued", "processing"],
                )
                raise
            failed = await self.task_repo.update_task_status(
                self.db, task_id, "error", progress=0, progress_message=str(e),
                expected_statuses=["queued", "processing"],
            )
            if failed is False:
                raise
            error_code = "task_error"
            message = str(e).lower()
            if "download" in message or "youtube" in message:
                error_code = "download_error"
            elif "analysis" in message:
                error_code = "analysis_error"
            elif "transcript" in message:
                error_code = "transcription_error"
            elif "cancelled" in message:
                error_code = "cancelled"

            await self.task_repo.update_task_runtime_metadata(
                self.db,
                task_id,
                completed_at=datetime.utcnow(),
                error_code=error_code,
            )
            raise

    async def _send_completion_notification_if_needed(
        self, *, task_id: str, clips_count: int
    ) -> None:
        context = await self.task_repo.get_task_notification_context(self.db, task_id)
        if not context:
            logger.warning("Task %s missing notification context; skipping email", task_id)
            return

        if not context.get("notify_on_completion"):
            return

        if context.get("completion_notification_sent_at"):
            logger.info(
                "Completion notification already sent for task %s; skipping", task_id
            )
            return

        user_email = context.get("user_email")
        if not user_email:
            logger.warning(
                "Task %s has notify_on_completion enabled but user email is missing",
                task_id,
            )
            return

        email_service = TaskCompletionEmailService(self.config)
        if not email_service.is_configured:
            logger.warning(
                "Skipping completion notification for task %s because Amazon SES is not configured",
                task_id,
            )
            return

        try:
            await email_service.send_task_completed_email(
                recipient=TaskCompletionRecipient(
                    email=user_email,
                    name=context.get("user_name"),
                    first_name=context.get("user_first_name"),
                ),
                task_id=task_id,
                source_title=context.get("source_title"),
                clips_count=clips_count,
            )
            stamped = await self.task_repo.mark_completion_notification_sent(
                self.db, task_id
            )
            if not stamped:
                logger.info(
                    "Completion notification stamp already existed for task %s",
                    task_id,
                )
        except Exception:
            logger.exception(
                "Failed to send completion notification for task %s",
                task_id,
            )

    async def get_task_with_clips(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get task details with all clips."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)

        if not task:
            return None

        if self._is_stale_queued_task(task):
            timeout_seconds = self.config.queued_task_timeout_seconds
            logger.warning(
                f"Task {task_id} stuck in queued status for over {timeout_seconds}s; marking as error"
            )
            await self.task_repo.update_task_status(
                self.db,
                task_id,
                "error",
                progress=0,
                progress_message=(
                    "Task timed out while waiting in queue. "
                    "Ensure the worker service is running and healthy (docker-compose logs -f worker)."
                ),
            )
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            if not task:
                return None

        if self._is_stale_processing_task(task):
            timeout_seconds = self.config.processing_task_timeout_seconds
            logger.warning(
                f"Task {task_id} stuck in processing status for over {timeout_seconds}s; marking as error"
            )
            await self.task_repo.update_task_status(
                self.db,
                task_id,
                "error",
                progress=0,
                progress_message=(
                    "Task stalled during processing (worker likely stopped or timed out). "
                    "Check the worker logs (docker-compose logs -f worker) and try again."
                ),
            )
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            if not task:
                return None

        # Get clips
        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        task["clips"] = [
            {
                **{key: value for key, value in clip.items() if key != "file_path"},
                "caption_settings": load_clip_caption_settings(Path(clip["file_path"])) if clip.get("file_path") else None,
            }
            for clip in clips
        ]
        task["clips_count"] = len(clips)
        task.update(await self._load_task_source_settings(task_id))

        return task

    async def get_user_tasks(
        self, user_id: str, limit: int = 50
    ) -> list[Dict[str, Any]]:
        """Get all tasks for a user."""
        return await self.task_repo.get_user_tasks(self.db, user_id, limit)

    @serialized_task_edit
    async def delete_task(self, task_id: str) -> None:
        """Delete a task and all its associated clips."""
        # Delete all clips for this task
        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        # Delete the task
        await self.task_repo.delete_task(self.db, task_id)

        logger.info(f"Deleted task {task_id} and all associated clips")


    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Return aggregate processing performance metrics."""
        return await self.task_repo.get_performance_metrics(self.db)
