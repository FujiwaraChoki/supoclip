"""
Task service - orchestrates task creation and processing workflow.
"""

from sqlalchemy.ext.asyncio import AsyncSession
from typing import Dict, Any, Optional, Callable
import logging
from datetime import datetime
from pathlib import Path
import json
from time import perf_counter

import redis.asyncio as redis

from ..repositories.task_repository import TaskRepository
from ..repositories.source_repository import SourceRepository
from ..repositories.clip_repository import ClipRepository
from ..repositories.cache_repository import CacheRepository
from . import usage_service
from ..usage_context import current_usage
from .video_service import VideoService
from .task_completion_email_service import (  # noqa: F401 — re-exported for tests to patch
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)
from ..config import Config, get_config
from ._clip_edit_mixin import _ClipEditMixin
from ._task_helpers import build_cache_key, is_queued_task_stale, seconds_to_mmss
from ._task_notification_mixin import _TaskNotificationMixin

logger = logging.getLogger(__name__)


class TaskService(_ClipEditMixin, _TaskNotificationMixin):
    """Service for task workflow orchestration.

    Clip-editing methods (``trim_clip``, ``split_clip``, ``merge_clips``,
    ``update_clip_captions``, ``regenerate_all_clips_for_task``) live in
    :class:`_ClipEditMixin`. Completion-email logic lives in
    :class:`_TaskNotificationMixin`.
    """

    def __init__(self, db: AsyncSession, config: Config | None = None):
        self.db = db
        self.task_repo = TaskRepository()
        self.source_repo = SourceRepository()
        self.clip_repo = ClipRepository()
        self.cache_repo = CacheRepository()
        self.video_service = VideoService()
        self.config = config or get_config()

    _build_cache_key = staticmethod(build_cache_key)

    def _is_stale_queued_task(self, task: Dict[str, Any]) -> bool:
        return is_queued_task_stale(task, self.config.queued_task_timeout_seconds)

    async def create_task_with_source(
        self,
        user_id: str,
        url: str,
        title: Optional[str] = None,
        font_family: str = "TikTokSans-Regular",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
        caption_template: str = "default",
        include_broll: bool = False,
        processing_mode: str = "fast",
        stroke_color: Optional[str] = None,
        stroke_width: Optional[int] = None,
        subtitle_bold: bool = False,
        subtitle_italic: bool = False,
        subtitle_underline: bool = False,
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
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            subtitle_bold=subtitle_bold,
            subtitle_italic=subtitle_italic,
            subtitle_underline=subtitle_underline,
        )

        logger.info(f"Created task {task_id} for user {user_id}")
        return task_id

    async def process_task(
        self,
        task_id: str,
        url: str,
        source_type: str,
        font_family: str = "TikTokSans-Regular",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
        caption_template: str = "default",
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        progress_callback: Optional[Callable] = None,
        should_cancel: Optional[Callable] = None,
        clip_ready_callback: Optional[Callable] = None,
        stroke_color: Optional[str] = None,
        stroke_width: Optional[int] = None,
        bold: bool = False,
        italic: bool = False,
        underline: bool = False,
        avoid_original_subtitle: str = "none",
    ) -> Dict[str, Any]:
        """
        Process a task: download video, analyze, create clips.
        Returns processing results.
        """
        # Start recording API usage for this task. The ContextVar lives for
        # the duration of this call; low-level code (AssemblyAI, LLM) reads
        # it and appends events without knowing about task_service.
        recorder, recorder_token = usage_service.start_recording()
        try:
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
            await self.task_repo.update_task_status(
                self.db,
                task_id,
                "processing",
                progress=0,
                progress_message="Starting...",
            )

            # Progress callback wrapper
            async def update_progress(
                progress: int, message: str, status: str = "processing"
            ):
                await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    status,
                    progress=progress,
                    progress_message=message,
                )
                if progress_callback:
                    await progress_callback(progress, message, status)

            # Process video with progress updates
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
                cached_transcript=cached_transcript,
                cached_analysis_json=cached_analysis_json,
                progress_callback=update_progress,
                should_cancel=should_cancel,
                stroke_color=stroke_color,
                stroke_width=stroke_width,
                bold=bold,
                italic=italic,
                underline=underline,
            )
            stage_timings["pipeline_seconds"] = round(
                perf_counter() - pipeline_start, 3
            )

            await self.cache_repo.upsert_cache(
                self.db,
                cache_key=cache_key,
                source_url=url,
                source_type=source_type,
                transcript_text=result.get("transcript"),
                analysis_json=result.get("analysis_json"),
            )

            # Render clips incrementally: render, save, notify one at a time
            segments_to_render = result.get("segments_to_render", [])
            video_path = Path(result["video_path"])
            total_clips = len(segments_to_render)
            clips_output_dir = Path(self.config.temp_dir) / "clips"
            clips_output_dir.mkdir(parents=True, exist_ok=True)

            clip_ids = []
            render_start = perf_counter()

            for i, segment in enumerate(segments_to_render):
                # Check cancellation
                if should_cancel and await should_cancel():
                    raise Exception("Task cancelled")

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
                    stroke_color=stroke_color,
                    stroke_width=stroke_width,
                    bold=bold,
                    italic=italic,
                    underline=underline,
                    avoid_original_subtitle=avoid_original_subtitle,
                )
                if clip_info is None:
                    continue  # Skip failed clip

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
                )
                await self.db.commit()
                clip_ids.append(clip_id)

                # Update task's clip IDs array
                await self.task_repo.update_task_clips(self.db, task_id, clip_ids)

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

            # Mark as completed
            await self.task_repo.update_task_status(
                self.db,
                task_id,
                "completed",
                progress=100,
                progress_message="Complete!",
            )

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

            return {
                "task_id": task_id,
                "clips_count": len(clip_ids),
                "segments": result["segments"],
                "summary": result.get("summary"),
                "key_topics": result.get("key_topics"),
            }

        except Exception as e:
            logger.error(f"Error processing task {task_id}: {e}")
            if str(e) == "Task cancelled":
                await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    "cancelled",
                    progress=0,
                    progress_message="Cancelled by user",
                )
                raise
            await self.task_repo.update_task_status(
                self.db, task_id, "error", progress_message=str(e)
            )
            error_code = "task_error"
            message = str(e).lower()
            if "download" in message or "youtube" in message:
                error_code = "download_error"
            elif "transcript" in message:
                error_code = "transcription_error"
            elif "analysis" in message:
                error_code = "analysis_error"
            elif "cancelled" in message:
                error_code = "cancelled"

            await self.task_repo.update_task_runtime_metadata(
                self.db,
                task_id,
                completed_at=datetime.utcnow(),
                error_code=error_code,
            )
            raise
        finally:
            current_usage.reset(recorder_token)
            try:
                task_row = await self.task_repo.get_task_by_id(self.db, task_id)
                task_user_id = task_row.get("user_id") if task_row else None
                await usage_service.persist_recorder(
                    self.db, recorder, task_id, task_user_id
                )
            except Exception as persist_exc:
                logger.warning(
                    "Failed to persist API usage for task %s: %s",
                    task_id,
                    persist_exc,
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

        # Get clips
        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        task["clips"] = clips
        task["clips_count"] = len(clips)

        return task

    async def get_user_tasks(
        self, user_id: str, limit: int = 50
    ) -> list[Dict[str, Any]]:
        """Get all tasks for a user."""
        return await self.task_repo.get_user_tasks(self.db, user_id, limit)

    async def delete_task(self, task_id: str) -> None:
        """Delete a task and all its associated clips."""
        # Delete all clips for this task
        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        # Delete the task
        await self.task_repo.delete_task(self.db, task_id)

        logger.info(f"Deleted task {task_id} and all associated clips")

    async def update_task_settings(
        self,
        task_id: str,
        font_family: str,
        font_size: int,
        font_color: str,
        caption_template: str,
        include_broll: bool,
        apply_to_existing: bool,
    ) -> Dict[str, Any]:
        """Update task-level settings and optionally regenerate all clips."""
        await self.task_repo.update_task_settings(
            self.db,
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
        )

        if apply_to_existing:
            await self.regenerate_all_clips_for_task(
                task_id,
                font_family,
                font_size,
                font_color,
                caption_template,
            )

        return await self.get_task_with_clips(task_id) or {}

    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Return aggregate processing performance metrics."""
        return await self.task_repo.get_performance_metrics(self.db)

    _seconds_to_mmss = staticmethod(seconds_to_mmss)
