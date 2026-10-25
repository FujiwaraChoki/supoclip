"""
Video service - handles video processing business logic.
"""

import asyncio
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Awaitable
import logging
import json
import subprocess
import uuid

from ..utils.async_helpers import run_in_thread
from ..repositories.minute_usage_repository import charged_duration
from ..youtube_utils import (
    async_get_youtube_video_info,
    async_get_youtube_video_title,
    cleanup_downloaded_files,
    download_youtube_video,
    get_youtube_video_id,
)
from ..youtube_proxy import ByteBudget, DownloadBudgetExceeded, PartialDownloadUnavailable, proxy_configured
from ..youtube_source import (
    cached_full_media,
    download_analysis_source,
    materialize_clip_source,
    resolve_full_youtube_download,
    copy_transcript_sidecar,
    load_time_map,
    persist_cache_sidecar,
)
from ..video_utils import (
    get_video_transcript,
    create_clips_with_transitions,
    create_optimized_clip,
    load_cached_transcript_data,
    parse_timestamp_to_seconds,
    build_clip_keep_ranges,
    build_keep_ranges_from_source_ranges,
    build_clip_signal_summary,
    apply_segment_hook_teaser,
    clamp_keep_ranges_to_max_duration,
    extend_keep_ranges_to_sentence_boundary,
    keep_ranges_transcript_text,
    snap_clip_start_to_sentence,
    seconds_to_mmss,
    get_transcript_text_in_range,
)
from ..broll import BRollSession, broll_configured, broll_overlays_for, plan_clip_broll
from ..clip_source_map import (
    normalize_source_ranges,
    save_clip_broll,
    save_clip_source_ranges,
    source_range_bounds,
)
from ..ai import get_most_relevant_parts_by_transcript
from ..clip_duration import clip_duration
from ..config import get_config
from ..media.source_limits import normalize_source_media
from ..media_storage import MediaStorage
from ..media.ffmpeg import (
    cap_video_short_side,
    crossfade_fade_for_ranges,
    ffprobe_duration,
    ffprobe_video_size,
)
from ..media.timeline import get_words_for_keep_ranges
from ..stream_source import (
    PROCESSING_OPERATION,
    StreamSourceError,
    detect_stream_source,
    download_stream_audio,
    download_stream_sections,
    download_stream_video,
)

logger = logging.getLogger(__name__)
UPLOAD_URL_PREFIX = "upload://"


class VideoService:
    """Service for video processing operations."""

    async def measure_source_duration(self, url: str, user_id: str) -> int:
        """Resolve the full source length for older/unmetered task entry paths."""
        source_type = self.determine_source_type(url)
        if source_type == "youtube":
            metadata = await asyncio.wait_for(
                async_get_youtube_video_info(url),
                timeout=get_config().youtube_preflight_timeout_seconds,
            )
            duration = (metadata or {}).get("duration")
        elif source_type in {"twitch", "kick"}:
            from ..stream_source import fetch_stream_info, PREFLIGHT_DEADLINE_SECONDS

            metadata = await asyncio.wait_for(
                run_in_thread(fetch_stream_info, url, deadline_seconds=PREFLIGHT_DEADLINE_SECONDS),
                timeout=PREFLIGHT_DEADLINE_SECONDS + 5,
            )
            duration = (metadata or {}).get("duration")
        else:
            duration = await run_in_thread(self.probe_upload_duration, url, user_id)
        return charged_duration(duration)

    @staticmethod
    def _ground_segment_text(
        segment_payload: Dict[str, Any],
        transcript_data: Optional[Dict[str, Any]],
    ) -> str:
        if not transcript_data:
            return str(segment_payload.get("text") or "").strip()

        start_time = str(segment_payload.get("start_time") or "").strip()
        end_time = str(segment_payload.get("end_time") or "").strip()
        if not start_time or not end_time:
            return str(segment_payload.get("text") or "").strip()

        start_seconds = parse_timestamp_to_seconds(start_time)
        end_seconds = parse_timestamp_to_seconds(end_time)
        if end_seconds <= start_seconds:
            return str(segment_payload.get("text") or "").strip()

        grounded_text = get_transcript_text_in_range(
            transcript_data,
            start_seconds,
            end_seconds,
        ).strip()
        return grounded_text or str(segment_payload.get("text") or "").strip()

    @staticmethod
    def _get_file_duration(path: Path) -> Optional[float]:
        """Return video duration in seconds via ffprobe, or None on failure."""
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries", "format=duration",
                    "-of", "csv=p=0",
                    str(path),
                ],
                capture_output=True, text=True, check=True,
            )
            return float(result.stdout.strip())
        except Exception:
            return None

    @staticmethod
    def _build_fallback_segment(
        video_duration: Optional[float],
        transcript: str,
        target_duration: int,
    ) -> Dict[str, Any]:
        """Create a bounded starter clip when AI analysis selects no segments."""
        fallback_duration = max(1.0, float(target_duration or 30))
        if video_duration and video_duration > 0:
            fallback_duration = min(fallback_duration, max(1.0, video_duration))

        transcript_preview = " ".join((transcript or "").split())
        if len(transcript_preview) > 240:
            transcript_preview = f"{transcript_preview[:237]}..."

        return {
            "start_time": "00:00",
            "end_time": seconds_to_mmss(fallback_duration),
            "text": transcript_preview,
            "relevance_score": 0.25,
            "reasoning": (
                "AI analysis did not identify a strong standalone segment, "
                "so SupoClip generated the first available portion of the video."
            ),
            "virality_score": 0,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": "fallback",
            "hook_title": None,
        }

    @staticmethod
    def resolve_local_video_path(url: str) -> Path:
        """Resolve uploaded-video references without exposing server filesystem paths."""
        if url.startswith(UPLOAD_URL_PREFIX):
            if url.startswith("upload://s3/"):
                storage = MediaStorage()
                return storage.local_path(storage.source_reference(url))
            filename = Path(url.removeprefix(UPLOAD_URL_PREFIX)).name
            return Path(get_config().temp_dir) / "uploads" / filename
        raise ValueError("Only upload:// references are allowed for local video sources")

    @staticmethod
    def probe_upload_duration(url: str, user_id: str) -> float:
        """Read an uploaded video's length without downloading it."""
        if url.startswith("upload://s3/"):
            storage = MediaStorage()
            reference = storage.validate_source(url, user_id)
            # ffprobe reads only the container headers through ranged requests.
            return ffprobe_duration(storage.signed_url(reference, "source.mp4", expires_in=300))
        return ffprobe_duration(VideoService.resolve_local_video_path(url))

    @staticmethod
    async def download_video(
        url: str,
        task_id: Optional[str] = None,
        quality: Optional[str] = None,
        ranges: Optional[list] = None,
        budget: Optional[ByteBudget] = None,
        skip_proxy: bool = False,
    ) -> Optional[Path]:
        """
        Download YouTube media for rendering.

        With a proxy configured, ``ranges`` downloads only those padded
        sections (reusing the cache). Otherwise this returns a full file,
        stored so later edits do not fetch it again.
        """
        logger.info(f"Starting video download: {url}")
        video_id = get_youtube_video_id(url)

        def _resolve() -> Optional[Path]:
            if not video_id:
                return None
            job_budget = budget or ByteBudget(get_config().youtube_download_max_bytes)
            bypass_proxy = skip_proxy
            if ranges:
                try:
                    segments = [
                        {"start_time": start, "end_time": end}
                        for start, end in ranges
                    ]
                    return materialize_clip_source(
                        url,
                        video_id,
                        segments,
                        task_id=task_id,
                        budget=job_budget,
                    )
                except DownloadBudgetExceeded:
                    raise
                except Exception as exc:
                    # Only unsupported slicing warrants a full proxy attempt.
                    bypass_proxy = skip_proxy or not isinstance(exc, PartialDownloadUnavailable)
                    logger.warning(
                        "Section download unavailable for %s, using a full file: %s",
                        video_id,
                        exc,
                    )
            return resolve_full_youtube_download(
                url,
                video_id,
                lambda **kwargs: download_youtube_video(url, 3, task_id, quality, **kwargs),
                task_id=task_id,
                budget=job_budget,
                skip_proxy=bypass_proxy,
            )

        try:
            video_path = await run_in_thread(_resolve)
        except DownloadBudgetExceeded:
            logger.error("YouTube download exceeded the per-job byte limit: %s", url)
            raise

        if not video_path:
            logger.error(f"Failed to download video: {url}")
            return None

        logger.info(f"Video downloaded successfully: {video_path}")
        if video_id:
            try:
                await run_in_thread(MediaStorage().restore_youtube_metadata, video_id, Path(video_path))
            except BaseException:
                await run_in_thread(cleanup_downloaded_files, video_id, source_path=Path(video_path))
                raise
        return video_path

    @staticmethod
    async def prepare_source(path: Path) -> Path:
        """Scale a local source down to at most 1080p and 60 fps.

        Plan-specific output (for example 720p) is applied later, when clips
        are rendered. This only enforces the absolute ceiling.
        """
        prepared = await run_in_thread(normalize_source_media, path)
        return prepared if isinstance(prepared, Path) else path

    @staticmethod
    async def get_video_title(url: str) -> str:
        """
        Get video title asynchronously.
        Returns a default title if retrieval fails.
        """
        try:
            title = await async_get_youtube_video_title(url)
            return title or "YouTube Video"
        except Exception as e:
            logger.warning(f"Failed to get video title: {e}")
            return "YouTube Video"

    @staticmethod
    async def generate_transcript(
        video_path: Path,
        processing_mode: str = "balanced",
        source_url: Optional[str] = None,
    ) -> str:
        """
        Generate transcript from video using the configured provider
        (assemblyai, whisper, or youtube_captions).
        Runs in thread pool to avoid blocking.
        """
        logger.info(f"Generating transcript for: {video_path}")
        speech_model = "best"
        runtime_config = get_config()
        # The fast-mode model selector is only meaningful for AssemblyAI; the
        # other providers ignore it and would otherwise silently change behaviour.
        if processing_mode == "fast" and runtime_config.transcription_provider == "assemblyai":
            speech_model = runtime_config.fast_mode_transcript_model

        transcript = await run_in_thread(
            get_video_transcript, video_path, speech_model, source_url
        )
        logger.info(f"Transcript generated: {len(transcript)} characters")
        return transcript

    @staticmethod
    async def analyze_transcript(
        transcript: str,
        clip_signals: Optional[str] = None,
        performance_context: Optional[str] = None,
        clip_length: str = "auto",
    ) -> Any:
        """
        Analyze transcript with AI to find relevant segments.
        This is already async, no need to wrap.
        """
        logger.info("Starting AI analysis of transcript")
        relevant_parts = await get_most_relevant_parts_by_transcript(
            transcript,
            clip_signals=clip_signals,
            performance_context=performance_context,
            clip_length=clip_length,
        )
        logger.info(
            f"AI analysis complete: {len(relevant_parts.most_relevant_segments)} segments found"
        )
        return relevant_parts

    @staticmethod
    async def plan_broll(
        video_path: Path,
        keep_ranges: List[tuple],
        output_format: str = "vertical",
        max_short_side: Optional[int] = None,
        session: Optional[BRollSession] = None,
    ) -> List[Dict[str, Any]]:
        """Plan and fetch stock cutaways for a clip rendered from ``keep_ranges``."""
        if not broll_configured() or not keep_ranges:
            return []
        try:
            transcript = await run_in_thread(load_cached_transcript_data, video_path)
            words = get_words_for_keep_ranges(transcript, keep_ranges) if transcript else []
            # The renderer crossfades between kept ranges, which shortens the clip.
            fade = crossfade_fade_for_ranges(keep_ranges)
            clip_seconds = sum(end - start for start, end in keep_ranges) - fade * (len(keep_ranges) - 1)
            orientation = "portrait"
            target_short_side = 1080
            if output_format == "original":
                width, height = await run_in_thread(ffprobe_video_size, video_path)
                if width > height:
                    orientation = "landscape"
                target_short_side = min(target_short_side, min(width, height) or target_short_side)
            if max_short_side:
                target_short_side = min(target_short_side, max_short_side)
            return await plan_clip_broll(
                words, clip_seconds, orientation, target_short_side, session
            )
        except Exception as exc:
            logger.warning("B-roll unavailable for this clip: %s", exc)
            return []

    @staticmethod
    async def create_video_clips(
        video_path: Path,
        segments: List[Dict[str, Any]],
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        max_short_side: Optional[int] = None,
        max_clip_seconds: float = 60.0,
        include_broll: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Create standalone video clips from segments with optional subtitles.
        ``max_short_side`` downscales the finished clips for plans below 1080p.
        With ``include_broll``, a segment's stored ``broll`` placements are
        reused; segments without them get a fresh plan.
        Runs in thread pool as video processing is CPU-intensive.
        output_format: 'vertical' (9:16) or 'original' (keep source size, faster).
        add_subtitles: False skips subtitles; with original format uses ffmpeg stream copy (no re-encode).
        """
        logger.info(f"Creating {len(segments)} video clips subtitles={add_subtitles}")
        clips_output_dir = Path(get_config().temp_dir) / "clips"
        clips_output_dir.mkdir(parents=True, exist_ok=True)

        if include_broll:
            session = BRollSession()
            segments = [dict(segment) for segment in segments]
            for segment in segments:
                placements = segment.get("broll") or []
                if not placements:
                    keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
                    if not keep_ranges and segment.get("source_ranges"):
                        keep_ranges = await run_in_thread(
                            build_keep_ranges_from_source_ranges,
                            video_path,
                            normalize_source_ranges(segment.get("source_ranges")),
                            cleanup_settings,
                        )
                    placements = await VideoService.plan_broll(
                        video_path, keep_ranges, output_format, max_short_side, session
                    )
                segment["broll"] = placements
                segment["broll_overlays"] = await broll_overlays_for(placements)
        else:
            segments = [{**segment, "broll": [], "broll_overlays": []} for segment in segments]

        clips_info = await run_in_thread(
            create_clips_with_transitions,
            video_path,
            segments,
            clips_output_dir,
            font_family,
            font_size,
            font_color,
            caption_template,
            output_format,
            add_subtitles,
            cleanup_settings,
            max_clip_seconds=max_clip_seconds,
        )

        if max_short_side:
            for clip_info in clips_info:
                try:
                    await run_in_thread(cap_video_short_side, Path(clip_info["path"]), max_short_side)
                except Exception as e:
                    logger.warning(f"Keeping full-size clip {clip_info.get('filename')}: {e}")

        logger.info(f"Successfully created {len(clips_info)} clips")
        return clips_info

    @staticmethod
    async def create_single_clip(
        video_path: Path,
        segment: Dict[str, Any],
        clip_index: int,
        output_dir: Path,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        max_short_side: Optional[int] = None,
        max_clip_seconds: float = 60.0,
        include_broll: bool = False,
        broll_session: Optional[BRollSession] = None,
    ) -> Optional[Dict[str, Any]]:
        """Render a single clip in the thread pool and return clip_info dict, or None on failure.

        ``max_short_side`` downscales the finished clip for plans below 1080p.
        ``include_broll`` adds stock cutaways; ``broll_session`` keeps one
        task's clips from reusing the same footage.
        """
        try:
            provided_keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
            source_ranges = normalize_source_ranges(segment.get("source_ranges"))
            provided_bounds = source_range_bounds(provided_keep_ranges or source_ranges)
            if provided_bounds:
                start_seconds, end_seconds = provided_bounds
            else:
                start_seconds = parse_timestamp_to_seconds(segment["start_time"])
                end_seconds = parse_timestamp_to_seconds(segment["end_time"])
            duration = end_seconds - start_seconds

            if duration <= 0:
                logger.warning(
                    f"Skipping clip {clip_index + 1}: invalid duration {duration:.1f}s"
                )
                return None

            unique_suffix = uuid.uuid4().hex[:12]
            clip_filename = (
                f"clip_{clip_index + 1}_"
                f"{segment['start_time'].replace(':', '')}-"
                f"{segment['end_time'].replace(':', '')}_"
                f"{unique_suffix}.mp4"
            )
            clip_path = output_dir / clip_filename
            if provided_keep_ranges:
                keep_ranges = provided_keep_ranges
            elif source_ranges:
                keep_ranges = build_keep_ranges_from_source_ranges(
                    video_path,
                    source_ranges,
                    cleanup_settings,
                )
            else:
                start_seconds = snap_clip_start_to_sentence(
                    video_path, start_seconds, end_seconds
                )
                keep_ranges = build_clip_keep_ranges(
                    video_path,
                    start_seconds,
                    end_seconds,
                    cleanup_settings,
                )
            keep_ranges = extend_keep_ranges_to_sentence_boundary(video_path, keep_ranges)
            clip_text = segment.get("text", "")
            if not provided_keep_ranges and not source_ranges:
                keep_ranges = apply_segment_hook_teaser(video_path, keep_ranges, segment)
            keep_ranges = clamp_keep_ranges_to_max_duration(video_path, keep_ranges, max_clip_seconds)
            if not provided_keep_ranges and not source_ranges:
                # Snapping, cleanup, the teaser and the cap change what is
                # spoken; keep the stored text word-for-word with the timeline.
                clip_text = keep_ranges_transcript_text(video_path, keep_ranges) or clip_text

            broll_placements: List[Dict[str, Any]] = []
            if include_broll:
                broll_placements = await VideoService.plan_broll(
                    video_path, keep_ranges, output_format, max_short_side, broll_session
                )
            broll_overlays = await broll_overlays_for(broll_placements)
            render_report: Dict[str, Any] = {}

            success = await run_in_thread(
                create_optimized_clip,
                video_path,
                start_seconds,
                end_seconds,
                clip_path,
                add_subtitles,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                keep_ranges,
                segment.get("hook_title"),
                extend_to_sentence=False,
                layout_report=render_report,
                broll_overlays=broll_overlays,
            )

            if not success:
                logger.error(f"Failed to create clip {clip_index + 1}")
                return None
            if not render_report.get("broll_applied"):
                broll_placements = []

            if max_short_side:
                try:
                    await run_in_thread(cap_video_short_side, clip_path, max_short_side)
                except Exception as e:
                    # A higher-resolution clip beats losing the clip entirely.
                    logger.warning(f"Keeping full-size clip {clip_index + 1}: {e}")

            save_clip_source_ranges(clip_path, keep_ranges)
            save_clip_broll(clip_path, broll_placements)
            cleaned_duration = sum(end - start for start, end in keep_ranges)
            logger.info(
                f"Created clip {clip_index + 1}: {cleaned_duration:.1f}s"
                + (f" with {len(broll_placements)} B-roll cutaways" if broll_placements else "")
            )
            return {
                "clip_id": clip_index + 1,
                "filename": clip_filename,
                "path": str(clip_path),
                "start_time": segment["start_time"],
                "end_time": segment["end_time"],
                "duration": cleaned_duration,
                "text": clip_text,
                "relevance_score": segment.get("relevance_score", 0.0),
                "reasoning": segment.get("reasoning", ""),
                "virality_score": segment.get("virality_score", 0),
                "hook_score": segment.get("hook_score", 0),
                "engagement_score": segment.get("engagement_score", 0),
                "value_score": segment.get("value_score", 0),
                "shareability_score": segment.get("shareability_score", 0),
                "hook_type": segment.get("hook_type"),
                "hook_title": segment.get("hook_title"),
                "keep_ranges": keep_ranges,
            }
        except Exception as e:
            logger.error(f"Error creating clip {clip_index + 1}: {e}")
            return None

    @staticmethod
    async def apply_single_transition(
        prev_clip_path: Path,
        current_clip_info: Dict[str, Any],
        clip_index: int,
        output_dir: Path,
    ) -> Dict[str, Any]:
        """Return the original clip info.

        Standalone exports intentionally do not depend on adjacent clips.
        """
        logger.info(
            "Skipping inter-clip transition for clip %s to preserve standalone exports",
            clip_index + 1,
        )
        return current_clip_info

    @staticmethod
    def determine_source_type(url: str) -> str:
        """Determine and validate the supported source type."""
        video_id = get_youtube_video_id(url)
        if video_id:
            return "youtube"
        if url.startswith(UPLOAD_URL_PREFIX):
            return "video_url"
        if not url.startswith(("http://", "https://")):
            raise ValueError("Only YouTube URLs or upload:// references are supported")
        source, _kind, _source_id = detect_stream_source(url)
        return source

    @staticmethod
    async def process_video_complete(
        url: str,
        source_type: str,
        task_id: Optional[str] = None,
        font_family: Optional[str] = None,
        font_size: Optional[int] = None,
        font_color: Optional[str] = None,
        caption_template: str = "default",
        processing_mode: str = "fast",
        output_format: str = "vertical",
        add_subtitles: bool = True,
        max_video_duration: Optional[int] = None,
        download_quality: Optional[str] = None,
        cached_transcript: Optional[str] = None,
        cached_analysis_json: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]] = None,
        should_cancel: Optional[Callable[[], Awaitable[bool]]] = None,
        performance_context: Optional[str] = None,
        clip_length: str = "auto",
    ) -> Dict[str, Any]:
        """
        Complete video processing pipeline.
        Returns dict with segments and clips info.

        progress_callback: Optional function to call with progress updates
                          Signature: async def callback(progress: int, message: str, status: str)
        """
        video_path: Optional[Path] = None
        audio_first = False
        try:
            runtime_config = get_config()
            duration_limit = (
                runtime_config.max_video_duration
                if max_video_duration is None
                else max_video_duration
            )
            # Step 1: Get video path (download or use existing)
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(10, "Downloading video...", "processing")

            if source_type == "youtube":
                video_info = await async_get_youtube_video_info(url, task_id=task_id)
                source_duration = None
                if video_info:
                    duration = video_info.get("duration", 0)
                    source_duration = duration or None
                    if duration and duration > duration_limit:
                        mins = duration_limit // 60
                        raise Exception(
                            f"Video is too long ({duration // 60} min). "
                            f"Maximum allowed duration is {mins} minutes."
                        )

                video_id = get_youtube_video_id(url)
                job_budget = ByteBudget(
                    getattr(runtime_config, "youtube_download_max_bytes", 2_000_000_000)
                )
                cached_full = (
                    await run_in_thread(cached_full_media, video_id) if video_id else None
                )
                # Audio first only when a proxy is configured and we do not
                # already have the full source cached. Uploads never reach here.
                if proxy_configured(runtime_config) and video_id and not cached_full:
                    def _resolve_analysis():
                        def _download_full():
                            return resolve_full_youtube_download(
                                url,
                                video_id,
                                lambda **kwargs: download_youtube_video(
                                    url, 3, task_id, download_quality, **kwargs
                                ),
                                task_id=task_id,
                                budget=job_budget,
                                skip_proxy=True,
                            )

                        return download_analysis_source(
                            url,
                            video_id,
                            task_id=task_id,
                            budget=job_budget,
                            duration=float(source_duration) if source_duration else None,
                            download_full=_download_full,
                        )

                    try:
                        video_path, audio_first = await run_in_thread(_resolve_analysis)
                    except DownloadBudgetExceeded as exc:
                        raise Exception(str(exc)) from exc
                else:
                    audio_first = False
                    video_path = cached_full or await VideoService.download_video(
                        url, task_id=task_id, quality=download_quality, budget=job_budget,
                    )
                if not video_path:
                    raise Exception("Failed to download video")
            elif source_type in {"twitch", "kick"}:
                # The API preflight already measured the source (and the task
                # seeds that length), so no second metadata request is made here.
                # Each provider checks the plan cap before downloading media.
                try:
                    video_path = await run_in_thread(
                        download_stream_audio, url, None, task_id,
                        max_duration=duration_limit, operation=PROCESSING_OPERATION,
                    )
                    audio_first = True
                except StreamSourceError:
                    # Too long, private, or every provider already failed: a
                    # full-video retry would only rerun the same chain.
                    raise
                except Exception as exc:
                    logger.warning("Stream audio download unavailable; using full video: %s", exc)
                    video_path = await run_in_thread(
                        download_stream_video, url, None, task_id,
                        max_duration=duration_limit, operation=PROCESSING_OPERATION,
                    )
            else:
                video_path = (
                    await run_in_thread(VideoService.resolve_local_video_path, url)
                    if url.startswith("upload://s3/")
                    else VideoService.resolve_local_video_path(url)
                )
                if not video_path.exists():
                    raise Exception("Video file not found")

            # Post-download duration guard (catches cases where preflight info was unavailable)
            file_duration = VideoService._get_file_duration(video_path)
            if file_duration and file_duration > duration_limit:
                mins = duration_limit // 60
                raise Exception(
                    f"Video is too long ({int(file_duration) // 60} min). "
                    f"Maximum allowed duration is {mins} minutes."
                )

            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")
            if progress_callback:
                await progress_callback(15, "Preparing video...", "processing")
            video_path = await VideoService.prepare_source(video_path)

            # Step 2: Generate transcript
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(30, "Generating transcript...", "processing")

            transcript = cached_transcript
            # Database text alone cannot recreate timed captions or cleanup.
            # Repair older caches whose word-timing sidecar was removed.
            needs_word_timings = (
                bool(transcript)
                and getattr(runtime_config, "transcription_provider", "assemblyai") != "youtube_captions"
                and not (load_cached_transcript_data(video_path) or {}).get("words")
            )
            if not transcript or needs_word_timings:
                transcript = await VideoService.generate_transcript(
                    video_path,
                    processing_mode=processing_mode,
                    source_url=url if source_type == "youtube" else None,
                )

            # Preserve paid word timings before any analysis/cancellation path.
            try:
                await run_in_thread(MediaStorage().save_source_metadata, video_path)
            except Exception:
                logger.warning("Source word timings retained locally for upload retry")
            await run_in_thread(persist_cache_sidecar, video_path)

            # Step 3: AI analysis
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(
                    50, "Analyzing content with AI...", "processing"
                )

            relevant_parts = None
            if cached_analysis_json:
                try:
                    cached_analysis = json.loads(cached_analysis_json)
                    segments = cached_analysis.get("most_relevant_segments", [])
                    if not segments:
                        logger.info(
                            "Ignoring cached transcript analysis with no clip segments"
                        )
                    else:

                        class _SimpleResult:
                            def __init__(self, payload: Dict[str, Any]):
                                self.summary = payload.get("summary")
                                self.key_topics = payload.get("key_topics")
                                self.most_relevant_segments = payload.get(
                                    "most_relevant_segments", []
                                )

                        relevant_parts = _SimpleResult(
                            {
                                "summary": cached_analysis.get("summary"),
                                "key_topics": cached_analysis.get("key_topics", []),
                                "most_relevant_segments": segments,
                            }
                        )
                except Exception:
                    relevant_parts = None

            if relevant_parts is None:
                try:
                    clip_signals = await run_in_thread(
                        build_clip_signal_summary,
                        video_path,
                        transcript,
                    )
                except Exception as exc:
                    logger.warning("Clip signal extraction failed: %s", exc)
                    clip_signals = None
                relevant_parts = await VideoService.analyze_transcript(
                    transcript,
                    clip_signals=clip_signals,
                    performance_context=performance_context,
                    clip_length=clip_length,
                )

            # Step 4: Create clips
            if should_cancel and await should_cancel():
                raise Exception("Task cancelled")

            if progress_callback:
                await progress_callback(70, "Creating video clips...", "processing")

            raw_segments = relevant_parts.most_relevant_segments
            segments_json: List[Dict[str, Any]] = []
            transcript_data = load_cached_transcript_data(video_path)
            for segment in raw_segments:
                if isinstance(segment, dict):
                    virality = segment.get("virality") or {}
                    if hasattr(virality, "model_dump"):
                        virality = virality.model_dump()
                    segment_payload = {
                        "start_time": segment.get("start_time"),
                        "end_time": segment.get("end_time"),
                        "text": segment.get("text", ""),
                        "relevance_score": segment.get("relevance_score", 0.0),
                        "reasoning": segment.get("reasoning", ""),
                        "virality_score": virality.get("total_score", 0),
                        "hook_score": virality.get("hook_score", 0),
                        "engagement_score": virality.get("engagement_score", 0),
                        "value_score": virality.get("value_score", 0),
                        "shareability_score": virality.get("shareability_score", 0),
                        "hook_type": virality.get("hook_type"),
                        "hook_title": segment.get("hook_title"),
                        "hook_start_time": segment.get("hook_start_time"),
                        "hook_end_time": segment.get("hook_end_time"),
                    }
                else:
                    virality = segment.virality.model_dump() if segment.virality else {}
                    segment_payload = {
                        "start_time": segment.start_time,
                        "end_time": segment.end_time,
                        "text": segment.text,
                        "relevance_score": segment.relevance_score,
                        "reasoning": segment.reasoning,
                        "virality_score": virality.get("total_score", 0),
                        "hook_score": virality.get("hook_score", 0),
                        "engagement_score": virality.get("engagement_score", 0),
                        "value_score": virality.get("value_score", 0),
                        "shareability_score": virality.get("shareability_score", 0),
                        "hook_type": virality.get("hook_type"),
                        "hook_title": getattr(segment, "hook_title", None),
                        "hook_start_time": getattr(segment, "hook_start_time", None),
                        "hook_end_time": getattr(segment, "hook_end_time", None),
                    }

                segment_payload["text"] = VideoService._ground_segment_text(
                    segment_payload,
                    transcript_data,
                )
                segments_json.append(segment_payload)

            clip_limit = runtime_config.max_clips
            if processing_mode == "fast":
                clip_limit = min(clip_limit, runtime_config.fast_mode_max_clips)
            segments_json = segments_json[: max(1, clip_limit)]

            if not segments_json:
                logger.warning(
                    "AI analysis selected no segments; using fallback clip window"
                )
                segments_json = [
                    VideoService._build_fallback_segment(
                        file_duration,
                        transcript,
                        (runtime_config.clip_duration if clip_length == "auto"
                         else clip_duration(clip_length).ideal_minimum),
                    )
                ]

            if audio_first:
                audio_path = video_path
                if progress_callback:
                    await progress_callback(
                        65, "Downloading selected segments...", "processing"
                    )
                try:
                    if source_type in {"twitch", "kick"}:
                        video_path = await run_in_thread(
                            download_stream_sections, url, segments_json, file_duration, task_id,
                            max_duration=duration_limit, operation=PROCESSING_OPERATION,
                        )
                        # Shift word timings onto the stitched sections' timeline.
                        await run_in_thread(
                            copy_transcript_sidecar, audio_path, video_path,
                            await run_in_thread(load_time_map, video_path),
                        )
                    else:
                        video_path = await run_in_thread(materialize_clip_source, url, video_id, segments_json, task_id=task_id, budget=job_budget, duration=file_duration, audio_path=audio_path)
                except DownloadBudgetExceeded as exc:
                    raise Exception(str(exc)) from exc
                except Exception as exc:
                    logger.warning(
                        "Section download failed, falling back to a full download: %s",
                        exc,
                    )
                    if source_type in {"twitch", "kick"}:
                        if isinstance(exc, StreamSourceError):
                            raise
                        video_path = await run_in_thread(
                            download_stream_video, url, file_duration, task_id,
                            max_duration=duration_limit, operation=PROCESSING_OPERATION,
                        )
                    else:
                        video_path = await VideoService.download_video(url, task_id=task_id, quality=download_quality, budget=job_budget, skip_proxy=not isinstance(exc, PartialDownloadUnavailable))
                    if video_path and audio_path:
                        await run_in_thread(copy_transcript_sidecar, audio_path, video_path)
                        await run_in_thread(persist_cache_sidecar, video_path)
                if not video_path:
                    raise Exception("Failed to download selected segments")
                # Transcription used the audio download. The picture is only
                # available now, so the 1080p/60 cap has to run again or a
                # section download stays at the source resolution.
                if should_cancel and await should_cancel():
                    raise Exception("Task cancelled")
                video_path = await VideoService.prepare_source(video_path)

            return {
                "segments": segments_json,
                "segments_to_render": segments_json,
                "video_path": str(video_path),
                "clips": [],
                "summary": relevant_parts.summary if relevant_parts else None,
                "key_topics": relevant_parts.key_topics if relevant_parts else None,
                "transcript": transcript,
                "analysis_json": json.dumps(
                    {
                        "summary": relevant_parts.summary if relevant_parts else None,
                        "key_topics": relevant_parts.key_topics
                        if relevant_parts
                        else [],
                        "most_relevant_segments": segments_json,
                    }
                ),
            }

        except (Exception, asyncio.CancelledError) as e:
            if source_type == "youtube" and video_path is not None:
                video_id = get_youtube_video_id(url)
                if video_id:
                    try:
                        cleanup_downloaded_files(video_id, source_path=video_path)
                    except Exception:
                        logger.exception("Could not clean up failed YouTube download")
            logger.error(f"Error in video processing pipeline: {e}")
            raise
