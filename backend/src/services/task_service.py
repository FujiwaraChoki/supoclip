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
import uuid
from time import perf_counter

import redis.asyncio as redis

from ..repositories.task_repository import TaskRepository
from ..repositories.source_repository import SourceRepository
from ..repositories.clip_repository import ClipRepository
from ..repositories.cache_repository import CacheRepository
from .video_service import VideoService
from .billing_service import BillingService
from .content_policy_service import ContentPolicyService
from .metadata_service import MetadataService
from .task_completion_email_service import (
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)
from ..config import Config, get_config
from ..clip_editor import (
    trim_clip_file,
    split_clip_file,
    merge_clip_files,
    overlay_custom_captions,
)
from ..video_utils import VALID_OUTPUT_FORMATS, parse_timestamp_to_seconds
from ..youtube_utils import cleanup_downloaded_files, extract_video_id
from ..clip_cleanup import normalize_clip_cleanup_settings
from ..ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION
from ..clip_source_map import (
    copy_clip_source_ranges,
    load_clip_source_ranges,
    save_clip_source_ranges,
    source_range_bounds,
    split_source_ranges,
    total_source_duration,
    trim_source_ranges,
)

logger = logging.getLogger(__name__)
PROCESSING_CACHE_VERSION = "20260319_grounded_segments_v1"


class TaskService:
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
        For YouTube sources, ``cleanup_downloaded_files`` removes every
        ``{video_id}.*`` artifact in temp/ (the merged video, sidecar audio,
        and any stray partials). For uploads we only remove the audio sidecar
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
        clip_started_callback: Optional[Callable] = None,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        hook_style: Optional[Dict[str, Any]] = None,
        social_overlay: Optional[Dict[str, Any]] = None,
        target_duration_seconds: Optional[float] = None,
        max_clips: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Process a task: download video, analyze, create clips.
        Returns processing results.
        """
        # Tracked so the downloaded source video can be cleaned up on both the
        # success and error paths; multi-GB YouTube downloads otherwise pile up
        # in temp/ until the disk fills and later tasks fail.
        video_path: Optional[Path] = None
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

            # Tracks the last real progress percentage reached, so an error
            # or cancellation can freeze the bar there instead of snapping
            # to 0 — 0 reads as "nothing happened" even when the pipeline
            # failed 90% of the way through.
            last_progress = 0

            # Progress callback wrapper
            async def update_progress(
                progress: int,
                message: str,
                status: str = "processing",
                stage: Optional[str] = None,
            ):
                nonlocal last_progress
                last_progress = progress
                await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    status,
                    progress=progress,
                    progress_message=message,
                )
                if progress_callback:
                    await progress_callback(progress, message, status, stage=stage)

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
                max_clips=max_clips,
                target_duration_seconds=target_duration_seconds,
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
            await self.clip_repo.delete_clips_by_task(self.db, task_id)
            await self.task_repo.update_task_clips(self.db, task_id, [])

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
                    stage="render",
                )
                if clip_started_callback:
                    try:
                        await clip_started_callback(i, total_clips)
                    except Exception:
                        logger.exception("clip_started_callback failed for clip %s", i)

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
                    hook_style,
                    social_overlay,
                    target_duration_seconds,
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
                    hook_title=clip_info.get("hook_title"),
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

            # Content policy scan: regex always, optional Ollama borderline
            # check if the project opted in — one call per video, per spec.
            # Runs after all clips exist so it can persist flags per real
            # clip_id rather than re-deriving them from segments.
            await update_progress(97, "Checking content policy...", stage="policy_check")
            try:
                clips_for_scan = await self.clip_repo.get_clips_by_task(self.db, task_id)
                if user_id and clips_for_scan:
                    await ContentPolicyService(self.db).scan_video(
                        user_id, task_id, clips_for_scan
                    )
            except Exception as exc:
                logger.warning(
                    "Content policy scan failed for task %s: %s", task_id, exc
                )

            # Metadata generation: one LLM call for the whole video, per spec.
            # generate_metadata_for_video (via run_with_llm_fallback) already
            # acquires the shared "llm"/"gpu" resource slots, so this never
            # overlaps a render job on the same GPU.
            await update_progress(98, "Generating clip metadata...", stage="metadata")
            try:
                clips_for_metadata = (
                    await self.clip_repo.get_clips_by_task(self.db, task_id)
                    if self.config.auto_generate_metadata_enabled
                    else []
                )
                if clips_for_metadata:
                    task_record = await self.task_repo.get_task_by_id(self.db, task_id)
                    video_title = (
                        (task_record or {}).get("source_title")
                        or (task_record or {}).get("source_url")
                    )
                    await MetadataService(self.db).regenerate_project_metadata(
                        task_id,
                        clips_for_metadata,
                        video_title=video_title,
                        allow_gemini=self.config.llm_provider_mode in ("gemini", "hybrid"),
                    )
            except Exception as exc:
                logger.warning(
                    "Metadata generation failed for task %s: %s", task_id, exc
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
            self._cleanup_source_video(video_path, source_type, url)
            if str(e) == "Task cancelled":
                await self.task_repo.update_task_status(
                    self.db,
                    task_id,
                    "cancelled",
                    progress=last_progress,
                    progress_message="Cancelled by user",
                )
                if progress_callback:
                    await progress_callback(last_progress, "Cancelled by user", "cancelled")
                raise
            await self.task_repo.update_task_status(
                self.db, task_id, "error", progress=last_progress, progress_message=str(e)
            )
            if progress_callback:
                await progress_callback(last_progress, str(e), "error")
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
            {key: value for key, value in clip.items() if key != "file_path"}
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

    async def get_task_status_counts(self, user_id: str) -> Dict[str, int]:
        """Status -> count for a user's non-deleted tasks."""
        return await self.task_repo.get_status_counts(self.db, user_id)

    async def delete_task(self, task_id: str) -> None:
        """Soft-delete a task (moves it to trash). Clips stay associated with
        the task — they are not touched until the task is purged."""
        await self.task_repo.delete_task(self.db, task_id)
        logger.info(f"Moved task {task_id} to trash")

    async def restore_task(self, task_id: str) -> bool:
        """Restore a task out of trash. Returns True if it was restored."""
        return await self.task_repo.restore_task(self.db, task_id)

    async def list_trash(self, user_id: str, limit: int = 50) -> list[Dict[str, Any]]:
        """List a user's soft-deleted tasks."""
        return await self.task_repo.list_deleted_tasks(self.db, user_id, limit)

    async def purge_task(self, task_id: str) -> None:
        """Permanently delete a trashed task: best-effort removes each clip's
        on-disk file, deletes the clip rows, then hard-deletes the task row.

        Source videos under `sources` are never touched by this flow.
        """
        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        for clip in clips:
            file_path = clip.get("file_path")
            if not file_path:
                continue
            try:
                path = Path(file_path)
                if path.exists():
                    path.unlink()
            except Exception as e:
                logger.warning(f"Failed to remove clip file {file_path} while purging task {task_id}: {e}")

        await self.clip_repo.delete_clips_by_task(self.db, task_id)
        await self.task_repo.purge_task(self.db, task_id)
        logger.info(f"Purged task {task_id} and its clip files")

    async def update_task_settings(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
        apply_to_existing: bool,
        cleanup_settings: Optional[Dict[str, Any]] = None,
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
                cleanup_settings=cleanup_settings,
            )

        return await self.get_task_with_clips(task_id) or {}

    async def regenerate_all_clips_for_task(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Regenerate all clips in a task using existing segment boundaries."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        metadata = await self._load_task_source_settings(task_id)
        output_format = metadata.get("output_format", "vertical")
        add_subtitles = metadata.get("add_subtitles", True)
        hook_style = metadata.get("hook_style")
        social_overlay = metadata.get("social_overlay")
        cleanup_payload = cleanup_settings or {
            "cut_long_pauses": metadata.get("cut_long_pauses"),
            "pause_threshold_ms": metadata.get("pause_threshold_ms"),
            "remove_filler_words": metadata.get("remove_filler_words"),
            "filtered_words": metadata.get("filtered_words"),
            "sensitivity": metadata.get("sensitivity"),
        }
        normalized_cleanup_settings = normalize_clip_cleanup_settings(
            cleanup_payload.get("cut_long_pauses"),
            cleanup_payload.get("pause_threshold_ms"),
            cleanup_payload.get("remove_filler_words"),
            cleanup_payload.get("filtered_words"),
            cleanup_payload.get("sensitivity"),
        )
        existing_cleanup_settings = normalize_clip_cleanup_settings(
            metadata.get("cut_long_pauses"),
            metadata.get("pause_threshold_ms"),
            metadata.get("remove_filler_words"),
            metadata.get("filtered_words"),
            metadata.get("sensitivity"),
        )
        should_recompute_cleanup = (
            cleanup_settings is not None
            and normalized_cleanup_settings != existing_cleanup_settings
        )

        if not source_url or not source_type:
            raise ValueError("Task source URL is missing; cannot regenerate clips")

        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        if not clips:
            return

        video_path: Path
        if source_type == "youtube":
            downloaded = await self.video_service.download_video(source_url)
            if not downloaded:
                raise ValueError("Failed to download source video for regeneration")
            video_path = Path(downloaded)
        else:
            video_path = self.video_service.resolve_local_video_path(source_url)
            if not video_path.exists():
                raise ValueError("Source video file no longer exists")

        segments = []
        for clip in clips:
            source_ranges = self._get_clip_source_ranges(clip)
            bounds = source_range_bounds(source_ranges)
            if bounds:
                start_time = self._seconds_to_mmss(bounds[0])
                end_time = self._seconds_to_mmss(bounds[1])
            else:
                start_time = clip["start_time"]
                end_time = clip["end_time"]

            segments.append(
                {
                    "start_time": start_time,
                    "end_time": end_time,
                    **(
                        {"source_ranges": source_ranges}
                        if should_recompute_cleanup
                        else {"keep_ranges": source_ranges}
                    ),
                    "text": clip.get("text") or "",
                    "relevance_score": clip.get("relevance_score", 0.5),
                    "reasoning": clip.get("reasoning")
                    or "Regenerated with updated settings",
                    "virality_score": clip.get("virality_score", 0),
                    "hook_score": clip.get("hook_score", 0),
                    "engagement_score": clip.get("engagement_score", 0),
                    "value_score": clip.get("value_score", 0),
                    "shareability_score": clip.get("shareability_score", 0),
                    "hook_type": clip.get("hook_type"),
                    "hook_title": clip.get("hook_title"),
                }
            )

        clips_info = await self.video_service.create_video_clips(
            video_path,
            segments,
            font_family,
            font_size,
            font_color,
            caption_template,
            output_format,
            add_subtitles,
            normalized_cleanup_settings,
            hook_style,
            social_overlay,
        )

        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        clip_ids = []
        for i, clip_info in enumerate(clips_info):
            clip_id = await self.clip_repo.create_clip(
                self.db,
                task_id=task_id,
                filename=clip_info["filename"],
                file_path=clip_info["path"],
                start_time=clip_info["start_time"],
                end_time=clip_info["end_time"],
                duration=clip_info["duration"],
                text=clip_info.get("text") or "",
                relevance_score=clip_info.get("relevance_score", 0.5),
                reasoning=clip_info.get("reasoning")
                or "Regenerated with updated settings",
                clip_order=i + 1,
                virality_score=clip_info.get("virality_score", 0),
                hook_score=clip_info.get("hook_score", 0),
                engagement_score=clip_info.get("engagement_score", 0),
                value_score=clip_info.get("value_score", 0),
                shareability_score=clip_info.get("shareability_score", 0),
                hook_type=clip_info.get("hook_type"),
                hook_title=clip_info.get("hook_title"),
            )
            clip_ids.append(clip_id)

        await self.task_repo.update_task_clips(self.db, task_id, clip_ids)

    async def generate_hook_variants_for_clip(
        self, task_id: str, clip_id: str, count: int = 3
    ) -> Dict[str, Any]:
        """Generate alternative hook titles for a clip, for A/B comparison. Appends to any stored variants."""
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        from ..ai import generate_hook_title_variants

        variant_texts = await generate_hook_title_variants(
            clip.get("text") or "",
            clip.get("hook_title"),
            clip.get("hook_type"),
            count=count,
        )
        new_variants = [{"id": uuid.uuid4().hex[:12], "text": text} for text in variant_texts]

        existing_variants = clip.get("hook_title_variants") or []
        existing_texts = {v.get("text") for v in existing_variants}
        combined = existing_variants + [v for v in new_variants if v["text"] not in existing_texts]

        await self.clip_repo.update_clip_hook(self.db, clip_id, hook_title_variants=combined)
        return {"clip_id": clip_id, "hook_title": clip.get("hook_title"), "variants": combined}

    async def select_hook_variant(
        self,
        task_id: str,
        clip_id: str,
        variant_id: Optional[str] = None,
        custom_text: Optional[str] = None,
        hook_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply a chosen hook-title variant (or custom text) to a clip and re-render its burned-in hook.

        Re-renders the clip from the original source (not the already-rendered
        file) since the hook text is baked into the same video frame as the
        crop/captions — burning a second hook on top of the first would just
        overlap the two, so this reuses the full clip-creation pipeline scoped
        to a single segment instead of a lightweight overlay pass.
        """
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        if custom_text is not None:
            new_hook_title = custom_text.strip()
            if not new_hook_title:
                raise ValueError("Hook title cannot be empty")
        else:
            variants = clip.get("hook_title_variants") or []
            match = next((v for v in variants if v.get("id") == variant_id), None)
            if not match:
                raise ValueError("Hook variant not found")
            new_hook_title = match["text"]

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        if not source_url or not source_type:
            raise ValueError("Task source URL is missing; cannot re-render hook")

        metadata = await self._load_task_source_settings(task_id)
        output_format = metadata.get("output_format", "vertical")
        add_subtitles = metadata.get("add_subtitles", True)
        hook_style = metadata.get("hook_style")
        social_overlay = metadata.get("social_overlay")
        cleanup_settings = normalize_clip_cleanup_settings(
            metadata.get("cut_long_pauses"),
            metadata.get("pause_threshold_ms"),
            metadata.get("remove_filler_words"),
            metadata.get("filtered_words"),
        )

        if source_type == "youtube":
            downloaded = await self.video_service.download_video(source_url)
            if not downloaded:
                raise ValueError("Failed to download source video to re-render hook")
            video_path = Path(downloaded)
        else:
            video_path = self.video_service.resolve_local_video_path(source_url)
            if not video_path.exists():
                raise ValueError("Source video file no longer exists")

        source_ranges = self._get_clip_source_ranges(clip)
        bounds = source_range_bounds(source_ranges)
        if bounds:
            start_time = self._seconds_to_mmss(bounds[0])
            end_time = self._seconds_to_mmss(bounds[1])
        else:
            start_time = clip["start_time"]
            end_time = clip["end_time"]

        segment = {
            "start_time": start_time,
            "end_time": end_time,
            "keep_ranges": source_ranges,
            "text": clip.get("text") or "",
            "relevance_score": clip.get("relevance_score", 0.5),
            "reasoning": clip.get("reasoning") or "Hook variant applied",
            "virality_score": clip.get("virality_score", 0),
            "hook_score": clip.get("hook_score", 0),
            "engagement_score": clip.get("engagement_score", 0),
            "value_score": clip.get("value_score", 0),
            "shareability_score": clip.get("shareability_score", 0),
            "hook_type": hook_type or clip.get("hook_type"),
            "hook_title": new_hook_title,
        }

        clips_info = await self.video_service.create_video_clips(
            video_path,
            [segment],
            task.get("font_family"),
            task.get("font_size"),
            task.get("font_color"),
            task.get("caption_template") or "default",
            output_format,
            add_subtitles,
            cleanup_settings,
            hook_style,
            social_overlay,
        )
        if not clips_info:
            raise ValueError("Failed to re-render clip with new hook title")
        clip_info = clips_info[0]

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            clip_info["filename"],
            clip_info["path"],
            clip_info.get("start_time", start_time),
            clip_info.get("end_time", end_time),
            clip_info.get("duration", clip["duration"]),
            clip_info.get("text") or clip.get("text") or "",
        )
        await self.clip_repo.update_clip_hook(
            self.db,
            clip_id,
            hook_title=new_hook_title,
            hook_type=hook_type,
            selected_hook_variant_id=variant_id or "custom",
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}

    async def update_clip_reactions(
        self,
        task_id: str,
        clip_id: str,
        reactions: list[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """Replace a clip's emoji reactions and re-render its burned-in frame.

        Reactions are burned into the same frame as the hook title/crop/
        captions (see `emoji_reactions.build_emoji_reactions_ass`), so — like
        `select_hook_variant` — this re-renders the clip from the original
        source rather than trying to patch the already-encoded file.
        """
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        if not source_url or not source_type:
            raise ValueError("Task source URL is missing; cannot re-render reactions")

        metadata = await self._load_task_source_settings(task_id)
        output_format = metadata.get("output_format", "vertical")
        add_subtitles = metadata.get("add_subtitles", True)
        hook_style = metadata.get("hook_style")
        social_overlay = metadata.get("social_overlay")
        cleanup_settings = normalize_clip_cleanup_settings(
            metadata.get("cut_long_pauses"),
            metadata.get("pause_threshold_ms"),
            metadata.get("remove_filler_words"),
            metadata.get("filtered_words"),
        )

        if source_type == "youtube":
            downloaded = await self.video_service.download_video(source_url)
            if not downloaded:
                raise ValueError("Failed to download source video to re-render reactions")
            video_path = Path(downloaded)
        else:
            video_path = self.video_service.resolve_local_video_path(source_url)
            if not video_path.exists():
                raise ValueError("Source video file no longer exists")

        source_ranges = self._get_clip_source_ranges(clip)
        bounds = source_range_bounds(source_ranges)
        if bounds:
            start_time = self._seconds_to_mmss(bounds[0])
            end_time = self._seconds_to_mmss(bounds[1])
        else:
            start_time = clip["start_time"]
            end_time = clip["end_time"]

        segment = {
            "start_time": start_time,
            "end_time": end_time,
            "keep_ranges": source_ranges,
            "text": clip.get("text") or "",
            "relevance_score": clip.get("relevance_score", 0.5),
            "reasoning": clip.get("reasoning") or "Emoji reactions applied",
            "virality_score": clip.get("virality_score", 0),
            "hook_score": clip.get("hook_score", 0),
            "engagement_score": clip.get("engagement_score", 0),
            "value_score": clip.get("value_score", 0),
            "shareability_score": clip.get("shareability_score", 0),
            "hook_type": clip.get("hook_type"),
            "hook_title": clip.get("hook_title"),
            "reactions": reactions,
        }

        clips_info = await self.video_service.create_video_clips(
            video_path,
            [segment],
            task.get("font_family"),
            task.get("font_size"),
            task.get("font_color"),
            task.get("caption_template") or "default",
            output_format,
            add_subtitles,
            cleanup_settings,
            hook_style,
            social_overlay,
        )
        if not clips_info:
            raise ValueError("Failed to re-render clip with new reactions")
        clip_info = clips_info[0]

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            clip_info["filename"],
            clip_info["path"],
            clip_info.get("start_time", start_time),
            clip_info.get("end_time", end_time),
            clip_info.get("duration", clip["duration"]),
            clip_info.get("text") or clip.get("text") or "",
        )
        await self.clip_repo.update_clip_reactions(self.db, clip_id, reactions)
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}

    async def trim_clip(
        self,
        task_id: str,
        clip_id: str,
        start_offset: float,
        end_offset: float,
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = Path(clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        output_path = trim_clip_file(
            input_path, Path(self.config.temp_dir) / "clips", start_offset, end_offset
        )
        source_ranges = self._get_clip_source_ranges(clip)
        trimmed_ranges = trim_source_ranges(source_ranges, start_offset, end_offset)
        clip_duration = max(0.1, total_source_duration(trimmed_ranges))
        bounds = source_range_bounds(trimmed_ranges)
        if not bounds:
            raise ValueError("Trimmed clip has no remaining source mapping")
        start_seconds, end_seconds = bounds
        save_clip_source_ranges(output_path, trimmed_ranges)

        new_start = self._seconds_to_mmss(start_seconds)
        new_end = self._seconds_to_mmss(end_seconds)

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            new_start,
            new_end,
            clip_duration,
            clip.get("text") or "",
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}

    async def split_clip(
        self, task_id: str, clip_id: str, split_time: float
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = Path(clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        first_path, second_path = split_clip_file(
            input_path, Path(self.config.temp_dir) / "clips", split_time
        )

        clamped_split = max(0.2, min(split_time, float(clip["duration"]) - 0.2))
        source_ranges = self._get_clip_source_ranges(clip)
        first_ranges, second_ranges = split_source_ranges(source_ranges, clamped_split)
        first_bounds = source_range_bounds(first_ranges)
        second_bounds = source_range_bounds(second_ranges)
        if not first_bounds or not second_bounds:
            raise ValueError("Split clip has invalid source mapping")
        save_clip_source_ranges(first_path, first_ranges)
        save_clip_source_ranges(second_path, second_ranges)
        first_duration = max(0.1, total_source_duration(first_ranges))
        second_duration = max(0.1, total_source_duration(second_ranges))

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            first_path.name,
            str(first_path),
            self._seconds_to_mmss(first_bounds[0]),
            self._seconds_to_mmss(first_bounds[1]),
            first_duration,
            clip.get("text") or "",
        )

        await self.clip_repo.create_clip(
            self.db,
            task_id=task_id,
            filename=second_path.name,
            file_path=str(second_path),
            start_time=self._seconds_to_mmss(second_bounds[0]),
            end_time=self._seconds_to_mmss(second_bounds[1]),
            duration=second_duration,
            text=clip.get("text") or "",
            relevance_score=clip.get("relevance_score", 0.5),
            reasoning=clip.get("reasoning") or "Split from original clip",
            clip_order=clip.get("clip_order", 1) + 1,
            virality_score=clip.get("virality_score", 0),
            hook_score=clip.get("hook_score", 0),
            engagement_score=clip.get("engagement_score", 0),
            value_score=clip.get("value_score", 0),
            shareability_score=clip.get("shareability_score", 0),
            hook_type=clip.get("hook_type"),
            hook_title=clip.get("hook_title"),
        )

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clip split successfully"}

    async def merge_clips(self, task_id: str, clip_ids: list[str]) -> Dict[str, Any]:
        if len(clip_ids) < 2:
            raise ValueError("At least two clips are required to merge")

        clips = []
        for clip_id in clip_ids:
            clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
            if not clip or clip["task_id"] != task_id:
                raise ValueError("One or more clips not found")
            clips.append(clip)

        ordered = sorted(clips, key=lambda c: c.get("clip_order", 0))
        merged_path = merge_clip_files(
            [Path(c["file_path"]) for c in ordered],
            Path(self.config.temp_dir) / "clips",
        )

        merged_ranges = []
        for clip in ordered:
            merged_ranges.extend(self._get_clip_source_ranges(clip))
        merged_bounds = source_range_bounds(merged_ranges)
        if merged_bounds:
            start_time = self._seconds_to_mmss(merged_bounds[0])
            end_time = self._seconds_to_mmss(merged_bounds[1])
            duration = total_source_duration(merged_ranges)
            save_clip_source_ranges(merged_path, merged_ranges)
        else:
            start_time = ordered[0]["start_time"]
            end_time = ordered[-1]["end_time"]
            duration = sum(float(c.get("duration", 0.0)) for c in ordered)
        text = " ".join((c.get("text") or "").strip() for c in ordered if c.get("text"))

        first = ordered[0]
        await self.clip_repo.update_clip(
            self.db,
            first["id"],
            merged_path.name,
            str(merged_path),
            start_time,
            end_time,
            duration,
            text,
        )

        for clip in ordered[1:]:
            await self.clip_repo.delete_clip(self.db, clip["id"])

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clips merged successfully", "clip_id": first["id"]}

    async def update_clip_captions(
        self,
        task_id: str,
        clip_id: str,
        caption_text: str,
        position: str,
        highlight_words: list[str],
        font_size: Optional[int] = None,
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = Path(clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        transcript_video_path: Optional[Path] = None
        source_url = task.get("source_url")
        source_type = task.get("source_type")
        processing_mode = (
            task.get("processing_mode") or self.config.default_processing_mode
        )
        if source_url and source_type:
            cache_entry = await self.cache_repo.get_cache(
                self.db,
                self._build_cache_key(source_url, source_type, processing_mode),
            )
            cached_video_path = cache_entry.get("video_path") if cache_entry else None
            if cached_video_path:
                transcript_video_path = Path(cached_video_path)
            elif source_type != "youtube":
                try:
                    transcript_video_path = self.video_service.resolve_local_video_path(
                        source_url
                    )
                except ValueError:
                    transcript_video_path = None

        if font_size is not None:
            # Persist per-project so future clip regenerations (and the
            # settings page) reflect the size chosen in the editor.
            await self.task_repo.update_task_font_size(self.db, task_id, font_size)

        output_path = overlay_custom_captions(
            input_path,
            Path(self.config.temp_dir) / "clips",
            caption_text,
            position,
            highlight_words,
            font_family=task.get("font_family") or None,
            font_size=font_size or task.get("font_size") or None,
            font_color=task.get("font_color") or None,
            caption_template=task.get("caption_template") or "default",
            transcript_video_path=transcript_video_path,
            source_ranges=self._get_clip_source_ranges(clip),
        )
        copy_clip_source_ranges(input_path, output_path)

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            clip["start_time"],
            clip["end_time"],
            clip["duration"],
            caption_text,
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}

    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Return aggregate processing performance metrics."""
        return await self.task_repo.get_performance_metrics(self.db)

    @staticmethod
    def _seconds_to_mmss(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        minutes = total // 60
        secs = total % 60
        return f"{minutes:02d}:{secs:02d}"

    @staticmethod
    def _get_clip_source_ranges(clip: Dict[str, Any]) -> list[tuple[float, float]]:
        file_path = clip.get("file_path")
        if isinstance(file_path, str) and file_path:
            persisted = load_clip_source_ranges(Path(file_path))
            if persisted:
                return persisted

        start_seconds = parse_timestamp_to_seconds(clip["start_time"])
        end_seconds = parse_timestamp_to_seconds(clip["end_time"])
        return [(start_seconds, end_seconds)]

    async def _load_task_source_settings(self, task_id: str) -> Dict[str, Any]:
        defaults = {
            "output_format": "vertical",
            "add_subtitles": True,
            "hook_style": None,
            "social_overlay": None,
            "broll_settings": None,
            "target_duration_seconds": None,
            **normalize_clip_cleanup_settings(),
        }
        redis_client = redis.Redis(
            host=self.config.redis_host,
            port=self.config.redis_port,
            password=self.config.redis_password,
            decode_responses=True,
        )
        try:
            payload = await redis_client.get(f"task_source:{task_id}")
        except Exception as exc:
            logger.warning(
                "Falling back to default task source settings for task %s: %s",
                task_id,
                exc,
            )
            return defaults
        finally:
            try:
                await redis_client.close()
            except Exception:
                pass

        if not payload:
            return defaults

        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError:
            return defaults

        output_format = parsed.get("output_format", defaults["output_format"])
        if output_format not in VALID_OUTPUT_FORMATS:
            output_format = defaults["output_format"]

        add_subtitles = parsed.get("add_subtitles", defaults["add_subtitles"])
        if not isinstance(add_subtitles, bool):
            add_subtitles = defaults["add_subtitles"]

        hook_style = parsed.get("hook_style")
        social_overlay = parsed.get("social_overlay")
        broll_settings = parsed.get("broll_settings")
        target_duration_seconds = parsed.get("target_duration_seconds")
        if not isinstance(target_duration_seconds, (int, float)):
            target_duration_seconds = None

        return {
            "output_format": output_format,
            "add_subtitles": add_subtitles,
            "hook_style": hook_style if isinstance(hook_style, dict) else None,
            "social_overlay": social_overlay if isinstance(social_overlay, dict) else None,
            "broll_settings": broll_settings if isinstance(broll_settings, dict) else None,
            "target_duration_seconds": target_duration_seconds,
            **normalize_clip_cleanup_settings(
                parsed.get("cut_long_pauses"),
                parsed.get("pause_threshold_ms"),
                parsed.get("remove_filler_words"),
                parsed.get("filtered_words"),
                parsed.get("sensitivity"),
            ),
        }
