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


from arq.constants import job_key_prefix, in_progress_key_prefix, expires_extra_ms
from ..broll import BRollSession
from ..workers.job_queue import JobQueue
from ..repositories.task_repository import TaskRepository
from ..repositories.minute_usage_repository import charged_duration
from ..repositories.source_repository import SourceRepository
from ..repositories.clip_repository import ClipRepository
from ..repositories.cache_repository import CacheRepository
from .video_service import VideoService
from .clip_service import ClipEditingMixin
from ..repositories.edit_transaction import serialized_task_edit, task_edit_transaction, TaskCancelled
from ..repositories.task_run_guard import exclusive_task_run, task_run_guard
from ..repositories.generation_allowance_guard import generation_allowance_guard
from .billing_service import BillingService, reduced_media_limits
from .task_completion_email_service import (
    TaskCompletionEmailService,
    TaskCompletionRecipient,
)
from ..config import Config, get_config
from ..youtube_utils import cleanup_downloaded_files, extract_video_id
from ..youtube_source import load_time_map, localize_segment, restore_clip_timeline, transcript_source_for
from ..clip_cleanup import normalize_clip_cleanup_settings
from ..clip_duration import clip_duration, validate_clip_length
from ..ai import TRANSCRIPT_ANALYSIS_CACHE_VERSION
from ..clip_source_map import load_clip_caption_settings
from ..media_storage import MediaStorage, media_work
from ..utils.async_helpers import run_in_thread
from ..repositories.media_repository import queue_delete, guard_source_creation
from ..stream_source import cleanup_stream_path, cleanup_stream_task, seed_stream_resolution

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
    def _build_cache_key(
        url: str, source_type: str, processing_mode: str, quality: Optional[str] = None
    ) -> str:
        payload = (
            f"{source_type}|{processing_mode}|"
            f"{TRANSCRIPT_ANALYSIS_CACHE_VERSION}|{url.strip()}"
        )
        # Reduced-quality YouTube downloads get their own entry so a 1080p
        # request never reuses a 720p source. Full quality keeps the old key.
        if quality and source_type == "youtube":
            payload += f"|q{quality}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _build_analysis_cache_key(cache_key: str, clip_length: str = "auto") -> str:
        """Row holding the analysis for a preset. Auto keeps the source row.

        The source and transcript do not depend on the preset, so other presets
        store only their analysis under a derived key and share the rest.
        """
        if validate_clip_length(clip_length) == "auto":
            return cache_key
        payload = f"{cache_key}|length:{clip_length}"
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    async def _media_limits(self, user_id: Optional[str]) -> tuple[Optional[str], Optional[int]]:
        """(download quality, clip cap) for the user's plan; see reduced_media_limits."""
        if not user_id:
            return None, None
        summary = await BillingService(self.db, self.config).get_usage_summary(user_id)
        return reduced_media_limits(summary)

    def _is_stale_queued_task(self, task: Dict[str, Any]) -> bool:
        """Detect queued tasks that have likely stalled due to worker issues."""
        if task.get("status") != "queued":
            return False

        return self._task_age_seconds(task) >= self.config.queued_task_timeout_seconds

    async def _queued_job_is_missing(self, task: Dict[str, Any]) -> bool:
        """Confirm this reservation has no queued/running ARQ job, not worker health.

        A heartbeat gap cannot invalidate a healthy backlog. Redis outages leave
        reservations intact until job existence can be checked again.
        """
        metadata = await self.task_repo.load_source_metadata(self.db, task["id"])
        job_id = metadata.get("processing_job_id")
        if not job_id:
            # Old tasks predate tracked job IDs. Give their jobs the full ARQ
            # retention window instead of dropping them during a worker restart.
            return self._task_age_seconds(task) >= max(
                self.config.queued_task_timeout_seconds, expires_extra_ms / 1000
            )
        try:
            pool = await JobQueue.get_pool()
            return not bool(await pool.exists(
                job_key_prefix + job_id, in_progress_key_prefix + job_id
            ))
        except Exception:
            logger.exception("Could not verify queued job for task %s", task["id"])
            return False

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
                    try:
                        MediaStorage().save_youtube_metadata(
                            video_id, transcript_source_for(video_id, video_path)
                        )
                    except Exception:
                        logger.exception("Could not back up YouTube word timings")
                    cleanup_downloaded_files(video_id, source_path=video_path)
                    logger.info("Cleaned up YouTube source artifacts for %s", video_id)
                    return
            except Exception as e:
                logger.warning("Failed YouTube source cleanup: %s", e)

        if source_type in {"twitch", "kick"}:
            try:
                # Removes only the operation (processing job or one editor
                # render) that produced this file, never a concurrent edit's.
                if cleanup_stream_path(video_path):
                    logger.info("Cleaned up stream source artifacts")
                    return
            except Exception as e:
                logger.warning("Failed stream source cleanup: %s", e)

        # Fallback / upload path: at least drop the transcription audio sidecar
        # (e.g. "<stem>.transcription.mp3") next to the source file.
        try:
            MediaStorage().save_source_metadata(video_path)
            sidecar = video_path.with_name(f"{video_path.stem}.transcription.mp3")
            if sidecar.exists():
                sidecar.unlink()
                logger.info("Removed transcription audio sidecar: %s", sidecar.name)
        except Exception as e:
            logger.warning("Failed to remove transcription sidecar: %s", e)

    @guard_source_creation
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
        source_duration_seconds: Optional[int] = None,
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
        if url.startswith("upload://s3/"):
            await run_in_thread(MediaStorage().validate_source, url, user_id)

        # Routes preflight new sources. Other callers must also persist a verified
        # duration, rather than creating an unmetered task with a NULL default.
        try:
            source_duration_seconds = charged_duration(source_duration_seconds)
        except ValueError:
            source_duration_seconds = await self.video_service.measure_source_duration(url, user_id)

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
            source_duration_seconds=source_duration_seconds,
            charge_minutes=self.config.monetization_enabled,
        )

        logger.info(f"Created task {task_id} for user {user_id}")
        return task_id

    async def _load_performance_context(self, user_id: Optional[str]) -> Optional[str]:
        """Build the creator's performance summary for the AI prompt (best effort)."""
        if not user_id:
            return None
        try:
            from ..database import AsyncSessionLocal
            from .social_service import SocialService

            # A short-lived session of its own: a failed lookup (e.g. social
            # tables not migrated yet when the worker deploys first) must not
            # leave the task's transaction aborted.
            async with AsyncSessionLocal() as session:
                return await SocialService(session, self.config).build_performance_context(
                    user_id
                )
        except Exception as exc:  # never block processing
            logger.warning("Skipping performance context for user %s: %s", user_id, exc)
            return None

    def _processing_transaction(self, task_id: str):
        return task_edit_transaction(self.db, task_id, processing=True)

    def _task_run_guard(self, task_id: str):
        return task_run_guard(self.db, task_id)

    async def _seed_stream_source(self, task_id: str) -> None:
        """Reuse the preflight's length and any cheap actor result in the worker."""
        try:
            task = await self.task_repo.get_task_by_id(self.db, task_id) or {}
            metadata = await self.task_repo.load_source_metadata(self.db, task_id) or {}
            await run_in_thread(
                seed_stream_resolution,
                task_id,
                metadata.get("stream_resolution"),
                task.get("source_duration_seconds"),
            )
        except Exception as exc:  # the worker can still resolve everything itself
            logger.warning("Could not seed stream source details: %s", exc)

    @exclusive_task_run
    @media_work
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
        clip_length: str = "auto",
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
            # New tasks already have an atomic acceptance charge. Recover queued
            # legacy jobs with missing durations before doing paid processing.
            if self.config.monetization_enabled and user_id:
                async with generation_allowance_guard(self.db, user_id, enabled=True):
                    await BillingService(self.db, self.config).assert_can_resume_task(
                        user_id, task_id, require_active_plan=False
                    )
            started_at = datetime.utcnow()
            stage_timings: Dict[str, float] = {}
            billing = (
                await BillingService(self.db, self.config).get_usage_summary(user_id)
                if user_id
                else {}
            )
            # Reduced-resolution plans download at their output quality and get
            # a final downscale; 1080p plans keep the renderer's output.
            download_quality, output_short_side = reduced_media_limits(billing)
            cache_key = self._build_cache_key(
                url, source_type, processing_mode, download_quality
            )
            analysis_cache_key = self._build_analysis_cache_key(cache_key, clip_length)

            cache_entry = await self.cache_repo.get_cache(self.db, cache_key)
            cached_transcript = (
                cache_entry.get("transcript_text") if cache_entry else None
            )
            analysis_entry = (
                cache_entry
                if analysis_cache_key == cache_key
                else await self.cache_repo.get_cache(self.db, analysis_cache_key)
            )
            cached_analysis_json = (
                analysis_entry.get("analysis_json") if analysis_entry else None
            )
            # Performance loop: feed this creator's real audience data back into
            # clip selection. Personalized analyses bypass the shared analysis
            # cache so one creator's history never shapes another's clips.
            performance_context = await self._load_performance_context(user_id)
            if performance_context:
                cached_analysis_json = None
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
                expected_statuses=["queued", "processing"],
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
            if source_type in {"youtube", "twitch", "kick"} and user_id:
                max_video_duration = self.config.max_youtube_video_duration_for_plan(
                    billing.get("plan"),
                    billing.get("subscription_status"),
                    billing.get("subscription_provider"),
                )

            if source_type in {"twitch", "kick"}:
                await self._seed_stream_source(task_id)

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
                download_quality=download_quality,
                cached_transcript=cached_transcript,
                cached_analysis_json=cached_analysis_json,
                progress_callback=update_progress,
                should_cancel=should_cancel,
                performance_context=performance_context,
                clip_length=clip_length,
            )
            stage_timings["pipeline_seconds"] = round(
                perf_counter() - pipeline_start, 3
            )
            video_path = Path(result["video_path"])

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
                    video_path=MediaStorage().source_reference(url) if url.startswith("upload://s3/") else result.get("video_path"),
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
                video_path=MediaStorage().source_reference(url) if url.startswith("upload://s3/") else result.get("video_path"),
                transcript_text=result.get("transcript"),
                analysis_json=(
                    result.get("analysis_json")
                    if not performance_context and analysis_cache_key == cache_key
                    else None
                ),
            )
            if not performance_context and analysis_cache_key != cache_key:
                await self.cache_repo.upsert_cache(
                    self.db,
                    cache_key=analysis_cache_key,
                    source_url=url,
                    source_type=source_type,
                    analysis_json=result.get("analysis_json"),
                )

            video_path = Path(result["video_path"])
            time_map = await run_in_thread(load_time_map, video_path)
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
            # Read from the task so queued jobs and resumes honour the setting.
            task_row = await self.task_repo.get_task_by_id(self.db, task_id) or {}
            include_broll = bool(task_row.get("include_broll"))
            broll_session = BRollSession()

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

                # A partial download is a concatenated timeline. Render against
                # local timestamps, then store the clip against the source.
                render_segment = localize_segment(segment, time_map) if time_map else segment
                clip_info = await self.video_service.create_single_clip(
                    video_path,
                    render_segment,
                    i,
                    clips_output_dir,
                    font_family,
                    font_size,
                    font_color,
                    caption_template,
                    output_format,
                    add_subtitles,
                    normalized_cleanup_settings,
                    max_short_side=output_short_side,
                    max_clip_seconds=clip_duration(clip_length).maximum,
                    include_broll=include_broll,
                    broll_session=broll_session,
                )
                pending_clip_path = Path(clip_info["path"]) if clip_info else None
                await check_cancelled()
                if clip_info is None:
                    continue  # Skip failed clip
                if time_map:
                    clip_info = await run_in_thread(
                        restore_clip_timeline, clip_info, time_map, segment
                    )

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

            if not clip_ids:
                raise ValueError("No clips could be rendered. Please try again.")

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
            if progress_callback:
                try:
                    await progress_callback(0, str(e), "error")
                except Exception:
                    logger.exception("Could not publish task failure for %s", task_id)
            raise
        finally:
            await run_in_thread(self._cleanup_source_video, video_path, source_type, url)
            if source_type in {"twitch", "kick"}:
                # Also handles failures before process_video_complete returns a
                # path (and therefore before the generic path cleanup can run).
                await run_in_thread(cleanup_stream_task, task_id)

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

        if self._is_stale_queued_task(task) and await self._queued_job_is_missing(task):
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
                    "The queued job is no longer available. Please resume this generation."
                ),
                expected_statuses=["queued"],
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
                expected_statuses=["processing"],
            )
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            if not task:
                return None

        # Get clips
        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        task["clips"] = [
            {
                **{key: value for key, value in clip.items() if key != "file_path"},
                "caption_settings": await run_in_thread(
                    MediaStorage().caption_settings,
                    clip["file_path"],
                ) if clip.get("file_path") else None,
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
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        source_url = task.get("source_url") if task else None
        if source_url and source_url.startswith("upload://"):
            if source_url.startswith("upload://s3/"):
                path = MediaStorage().source_reference(source_url)
            else:
                path = str(self.video_service.resolve_local_video_path(source_url))
            await queue_delete(self.db, path)
        elif source_url and MediaStorage().enabled:
            video_id = extract_video_id(source_url)
            if video_id:
                await queue_delete(self.db, MediaStorage().youtube_metadata_reference(video_id))
        # Delete all clips for this task
        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        # Delete the task
        await self.task_repo.delete_task(self.db, task_id)

        logger.info(f"Deleted task {task_id} and all associated clips")


    async def get_performance_metrics(self) -> Dict[str, Any]:
        """Return aggregate processing performance metrics."""
        return await self.task_repo.get_performance_metrics(self.db)
