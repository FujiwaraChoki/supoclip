"""
Video service - handles video processing business logic.
"""

import asyncio
import copy
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
    local_ranges_for,
    persist_cache_sidecar,
)
from ..video_utils import (
    get_video_transcript,
    create_clips_with_transitions,
    create_optimized_clip,
    load_cached_transcript_data,
    parse_timestamp_to_seconds,
    build_keep_ranges_from_source_ranges,
    build_clip_signal_summary,
    keep_ranges_transcript_text,
    seconds_to_mmss,
    get_transcript_text_in_range,
)
from ..broll import BRollSession, broll_configured, broll_overlays_for, plan_clip_broll
from ..clip_source_map import (
    normalize_source_ranges,
    save_clip_broll,
    save_clip_source_ranges,
)
from ..ai import get_most_relevant_parts_by_transcript, max_final_clips_for_duration
from ..clip_openings import OPENING_HOOK_PENALTY, opening_text_issue, review_opening
from ..clip_duration import clip_duration
from ..config import get_config
from ..workflows.contracts import PlanCache, PlanContext, WorkflowResult, WorkflowSpec
from ..workflows.durations import workflow_budget
from ..media.source_limits import normalize_source_media
from ..media.upload_ingest import (
    materialize_stored_upload,
    persist_local_upload,
    read_upload_reference,
    upload_needs_remote_copy,
)
from ..media_storage import MediaStorage
from ..media.ffmpeg import (
    cap_video_short_side,
    ffprobe_duration,
    ffprobe_video_size,
    rendered_clip_duration,
    rendered_ranges_duration,
)
from ..media.render_ranges import (
    expand_ranges_with_groups,
    output_offsets_for_junctions,
    rendered_duration,
    resolve_render_ranges,
    segment_bounds,
)
from ..media.timeline import get_words_for_keep_ranges
from ..media.visual_suitability import (
    is_visually_static,
    segment_static_fraction,
    segment_shows_interruption_notice,
    still_picture_shows_face,
)
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
OPENING_CONTEXT_SECONDS = 10.0


async def resolve_upload_for_edit(video_service, url: str) -> Path:
    """Open an uploaded source for an existing clip.

    A real video service falls back to the session storage reference. Fakes
    that only implement the local resolver stay on that path.
    """
    opener = getattr(video_service, "resolve_stored_upload", None)
    if opener is None:
        return await run_in_thread(video_service.resolve_local_video_path, url)
    return await opener(url)


def _sync_plan_ranges(plan: Dict[str, Any], segments: List[Dict[str, Any]]) -> None:
    """Drop the plan's recap points / trailer beats whose ranges no longer play."""
    playing = [
        (float(start), float(end))
        for segment in segments
        for start, end in segment.get("source_ranges") or []
    ]

    def plays(item_range) -> bool:
        start, end = float(item_range[0]), float(item_range[1])
        return any(abs(start - a) < 1e-3 and abs(end - b) < 1e-3 for a, b in playing)

    for key in ("points", "beats"):
        items = plan.get(key)
        if isinstance(items, list):
            plan[key] = [
                item for item in items
                if not isinstance(item, dict) or all(plays(r) for r in item.get("ranges") or [])
            ]


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
    def _segment_words(
        transcript_data: Optional[Dict[str, Any]], start: float, end: float
    ) -> List[Dict[str, Any]]:
        """Transcript words in source seconds from ``start`` to ``end``.

        Zero-length words are kept: the opening review needs every token.
        """
        words = []
        for word in (transcript_data or {}).get("words") or []:
            word_start = float(word.get("start", 0)) / 1000.0
            if start <= word_start < end:
                words.append(
                    {
                        "text": str(word.get("text", "")),
                        "start": word_start,
                        "end": max(word_start, float(word.get("end", 0)) / 1000.0),
                    }
                )
        return sorted(words, key=lambda word: word["start"])

    @staticmethod
    def _refine_segment_openings(
        segments: List[Dict[str, Any]],
        transcript_data: Optional[Dict[str, Any]],
        clip_length: str = "auto",
    ) -> List[Dict[str, Any]]:
        """Trim lead-in banter or fragments off each opening, or demote the clip.

        A start that only moves within limits keeps the clip's point; an
        opening that still leans on missing context loses hook points, and the
        list is re-sorted so the demoted clip no longer leads.
        """
        min_duration = clip_duration(clip_length).minimum
        penalised = False
        for segment in segments:
            # Cached analyses were reviewed when first stored; a second pass
            # would apply the penalty again.
            if segment.get("opening_reviewed"):
                continue
            segment["opening_reviewed"] = True
            try:
                start = parse_timestamp_to_seconds(str(segment.get("start_time") or ""))
                end = parse_timestamp_to_seconds(str(segment.get("end_time") or ""))
            except Exception:
                continue
            if end <= start:
                continue
            # Earlier words show where the opening sentence really starts.
            words = VideoService._segment_words(transcript_data, start - OPENING_CONTEXT_SECONDS, end)
            if words:
                review = review_opening(words, start, end, min_duration)
                issue, should_penalise = review.issue, review.penalise
                if review.start is not None and review.start <= start:
                    # The trim target is not after the stored start, so the
                    # opening stays as it is; rank it like an untrimmable one.
                    should_penalise = True
            else:
                issue = opening_text_issue(str(segment.get("text") or ""))
                review = None
                should_penalise = issue is not None
            if review is not None and review.start is not None and review.start > start:
                new_start = seconds_to_mmss(review.start)
                logger.info(
                    "Opening of %s-%s is %s (%r); starting at %s instead",
                    segment.get("start_time"),
                    segment.get("end_time"),
                    issue,
                    review.skipped_text,
                    new_start,
                )
                segment["start_time"] = new_start
                kept_words = VideoService._segment_words(
                    transcript_data, review.first_word_start or review.start, end
                )
                segment["text"] = " ".join(word["text"] for word in kept_words) or segment.get("text", "")
                hook_start = segment.get("hook_start_time")
                try:
                    if hook_start and parse_timestamp_to_seconds(str(hook_start)) < review.start:
                        segment["hook_start_time"] = None
                        segment["hook_end_time"] = None
                except Exception:
                    segment["hook_start_time"] = None
                    segment["hook_end_time"] = None
            if should_penalise:
                hook_score = int(segment.get("hook_score") or 0)
                reduced = max(0, hook_score - OPENING_HOOK_PENALTY)
                segment["hook_score"] = reduced
                segment["virality_score"] = max(
                    0, int(segment.get("virality_score") or 0) - (hook_score - reduced)
                )
                penalised = True
                logger.info(
                    "Opening of %s-%s still depends on missing context (%s); hook score %d -> %d",
                    segment.get("start_time"),
                    segment.get("end_time"),
                    issue,
                    hook_score,
                    reduced,
                )
        if penalised:
            # Analysis output is already best-first; a stable sort only moves
            # the demoted clips.
            segments = sorted(
                segments, key=lambda segment: int(segment.get("virality_score") or 0), reverse=True
            )
        return segments

    @staticmethod
    def _drop_static_segments(
        video_path: Path, segments: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Leave out segments whose picture is one still frame (slate, card, black).

        Selection only reads the transcript, so a strong passage spoken over a
        "footage unavailable" card would otherwise lead the results. When every
        segment is static (an audio podcast with cover art), all are kept.
        """
        notices = [False] * len(segments)
        fractions: List[Optional[float]] = []
        static: List[bool] = []
        for segment, notice in zip(segments, notices):
            fraction: Optional[float] = None
            is_static = False
            try:
                start = parse_timestamp_to_seconds(str(segment.get("start_time") or ""))
                end = parse_timestamp_to_seconds(str(segment.get("end_time") or ""))
                # Planned (topic) segments are judged on what they play, not their labels.
                planned = normalize_source_ranges(segment.get("source_ranges"))
                ranges = local_ranges_for(video_path, planned or [(start, end)])
                fraction = segment_static_fraction(video_path, ranges)
                notice = segment_shows_interruption_notice(video_path, ranges)
                notices[len(static)] = notice
                is_static = is_visually_static(fraction) or notice
                # A presenter in front of a fixed backdrop barely moves either;
                # only a held picture without a face is a card or placeholder.
                if is_static and still_picture_shows_face(video_path, ranges):
                    logger.info(
                        "Segment %s-%s holds one picture for %.0f%% but shows a face; keeping it",
                        segment.get("start_time"),
                        segment.get("end_time"),
                        (fraction or 0.0) * 100,
                    )
                    is_static = False
            except Exception as exc:
                logger.warning(
                    "Visual check unavailable for segment %s-%s: %s",
                    segment.get("start_time"),
                    segment.get("end_time"),
                    exc,
                )
                is_static = False
            fractions.append(fraction)
            static.append(is_static)

        if not any(static):
            return segments
        if all(static) and not any(notices):
            logger.warning(
                "Every selected segment shows a still picture; keeping them all "
                "because the whole source looks static"
            )
            return segments
        kept = []
        for segment, fraction, is_static in zip(segments, fractions, static):
            if is_static:
                logger.warning(
                    "Dropping segment %s-%s: one still picture covers %.0f%% of it "
                    "(interruption card, placeholder or black)",
                    segment.get("start_time"),
                    segment.get("end_time"),
                    (fraction or 0.0) * 100,
                )
                continue
            kept.append(segment)
        return kept

    @staticmethod
    def _range_picture(video_path: Path, ranges: List[tuple]) -> tuple[bool, bool]:
        """(holds one still picture without a face, shows an interruption notice) over these source ranges."""
        local = local_ranges_for(video_path, [(float(start), float(end)) for start, end in ranges])
        fraction = segment_static_fraction(video_path, local)
        notice = segment_shows_interruption_notice(video_path, local)
        static = is_visually_static(fraction) or notice
        return static and not still_picture_shows_face(video_path, local), notice

    @staticmethod
    def _picture_is_static(video_path: Path, ranges: List[tuple]) -> bool:
        """One still picture (card, placeholder, black) over these source ranges, without a face."""
        return VideoService._range_picture(video_path, ranges)[0]

    @staticmethod
    def _log_static_parts(video_path: Path, segments: List[Dict[str, Any]]) -> None:
        """Warn about series parts that hold a still picture; parts are never dropped."""
        for segment in segments:
            try:
                if VideoService._picture_is_static(video_path, segment.get("source_ranges") or []):
                    logger.warning(
                        "Part %s holds one still picture for most of its length; keeping it",
                        segment.get("part_number"),
                    )
            except Exception as exc:
                logger.warning(
                    "Visual check unavailable for part %s: %s", segment.get("part_number"), exc
                )

    @staticmethod
    def _planned_play_seconds(
        video_path: Optional[Path],
        ranges: List[tuple],
        fades: List[float],
        cleanup_settings: Optional[Dict[str, Any]],
    ) -> float:
        """How long these planned ranges play once rendered: cleanup cuts and junction fades included."""
        if video_path is not None:
            try:
                local = local_ranges_for(video_path, [(float(start), float(end)) for start, end in ranges])
                if len(local) == len(ranges):
                    keep, groups = expand_ranges_with_groups(video_path, local, cleanup_settings)
                    return rendered_duration(keep, output_offsets_for_junctions(keep, groups, fades))
            except Exception as exc:
                logger.warning("Could not measure the planned edit; estimating it: %s", exc)
        return sum(float(end) - float(start) for start, end in ranges) - sum(fades)

    @staticmethod
    def _drop_static_ranges(
        video_path: Path,
        segment: Dict[str, Any],
        min_seconds: float,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Remove still-picture ranges from a recap or trailer when the edit can spare them.

        Only a droppable point (recap) or an escalation/context beat (trailer)
        goes, and only while at least two ranges and ``min_seconds`` of
        rendered edit remain. Essential ranges stay even when static: the one
        output is never left without its opening, its payoff or its button.
        When every range shows a still picture (an audio podcast with cover
        art) the picture says nothing about the passage, so all of them stay.
        """
        meta = dict(segment.get("workflow_meta") or {})
        items_key = "points" if "points" in meta else "beats"
        ranges = [tuple(item) for item in segment.get("source_ranges") or []]
        fades = [float(value) for value in segment.get("junction_fades") or [0.0] * max(0, len(ranges) - 1)]
        items = [dict(item) for item in meta.get(items_key) or []]
        if len(ranges) <= 2:
            return segment

        checks: List[tuple[bool, bool]] = []
        for index, item_range in enumerate(ranges):
            try:
                checks.append(VideoService._range_picture(video_path, [item_range]))
            except Exception as exc:
                logger.warning("Visual check unavailable for range %d: %s", index + 1, exc)
                checks.append((False, False))
        if not any(static for static, _ in checks):
            return segment
        if all(static for static, _ in checks) and not any(notice for _, notice in checks):
            logger.warning(
                "Every range of the edit shows a still picture; keeping them all "
                "because the whole source looks static"
            )
            return segment

        def droppable(index: int) -> bool:
            owners = [item for item in items if item.get("range_index") == index]
            if not owners:
                return False
            if items_key == "points":
                return all(bool(item.get("droppable")) for item in owners)
            return all(item.get("role") not in ("cold_open", "button") for item in owners)

        dropped = False
        for index in reversed(range(len(ranges))):
            if len(ranges) <= 2 or not checks[index][0] or not droppable(index):
                continue
            if index == 0:
                next_fades = fades[1:]
            elif index == len(ranges) - 1:
                next_fades = fades[:-1]
            else:
                # The neighbours now meet; keep the softer of the two junctions.
                next_fades = [*fades[: index - 1], max(fades[index - 1], fades[index]), *fades[index + 1:]]
            next_ranges = ranges[:index] + ranges[index + 1:]
            remaining = VideoService._planned_play_seconds(
                video_path, next_ranges, next_fades, cleanup_settings
            )
            if remaining < min_seconds:
                logger.info(
                    "Range %d holds a still picture but the edit needs it to stay in budget; keeping it",
                    index + 1,
                )
                continue
            logger.warning("Dropping range %d of the edit: it holds one still picture", index + 1)
            ranges, fades = next_ranges, next_fades
            checks = checks[:index] + checks[index + 1:]
            items = [
                {**item, "range_index": item["range_index"] - (1 if item["range_index"] > index else 0)}
                for item in items
                if item.get("range_index") != index
            ]
            dropped = True

        if not dropped:
            return segment
        meta[items_key] = items
        meta["ranges"] = [list(item) for item in ranges]
        meta["junction_fades"] = fades
        return {
            **segment,
            "source_ranges": [list(item) for item in ranges],
            "junction_fades": fades,
            "start_time": seconds_to_mmss(ranges[0][0]),
            "end_time": seconds_to_mmss(ranges[0][1]),
            "workflow_meta": meta,
        }

    @staticmethod
    async def _plan_workflow_segments(
        *,
        workflow_spec: WorkflowSpec,
        task_id: Optional[str],
        transcript: str,
        transcript_data: Optional[Dict[str, Any]],
        source_seconds: float,
        cached_analysis_json: Optional[str],
        performance_context: Optional[str],
        cleanup_settings: Optional[Dict[str, Any]],
        plan_cache: Optional[PlanCache],
        outline_cache_key: Optional[str],
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]],
        should_cancel: Optional[Callable[[], Awaitable[bool]]],
    ) -> WorkflowResult:
        """The workflow's locked segments: from the plan cache, else from the planner."""
        from ..workflows import pipeline, units

        if plan_cache is None or not outline_cache_key:
            raise ValueError("Workflow planning needs a plan cache and an outline cache key")

        # Planners cut on word timings; a plan cached for other timings is stale.
        units_fingerprint = await run_in_thread(units.units_fingerprint, transcript_data, transcript)
        cached = WorkflowResult.from_cache_json(
            cached_analysis_json, workflow_spec.workflow, units_fingerprint
        )
        if cached is not None:
            logger.info("Reusing the cached %s plan", workflow_spec.workflow)
            return cached

        # Planners may report from concurrent passes; one task session serves them all.
        progress_lock = asyncio.Lock()

        async def report(percent: int, message: str) -> None:
            async with progress_lock:
                if should_cancel and await should_cancel():
                    raise Exception("Task cancelled")
                if progress_callback:
                    await progress_callback(percent, message, "processing")

        context = PlanContext(
            spec=workflow_spec,
            task_id=task_id,
            transcript_text=transcript,
            transcript_data=transcript_data,
            source_seconds=source_seconds,
            cleanup_settings=cleanup_settings or {},
            performance_context=performance_context,
            progress=report,
            plan_cache=plan_cache,
            outline_cache_key=outline_cache_key,
        )
        result = await pipeline.plan_workflow(context)
        logger.info(
            "Planned %s: %d segment(s)", workflow_spec.workflow, len(result.segments)
        )
        return result

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
            path = Path(get_config().temp_dir) / "uploads" / filename
            if path.is_file():
                return path
            reference = read_upload_reference(path)
            if reference:
                try:
                    return MediaStorage().local_path(reference)
                except (ValueError, OSError):
                    return path
            return path
        raise ValueError("Only upload:// references are allowed for local video sources")

    async def resolve_stored_upload(self, url: str) -> Path:
        """Resolve an upload for editing, including its durable object when the local file is gone."""
        local = await run_in_thread(self.resolve_local_video_path, url)
        if local.is_file() or str(url).startswith("upload://s3/"):
            return local
        materialized = await materialize_stored_upload(url)
        if materialized is not None and materialized.is_file():
            return materialized
        return local

    @staticmethod
    def probe_upload_duration(url: str, user_id: str, storage_reference: str | None = None) -> float:
        """Read an uploaded video's length without downloading it."""
        if url.startswith("upload://s3/"):
            storage = MediaStorage()
            reference = storage.validate_source(url, user_id)
            # ffprobe reads only the container headers through ranged requests.
            return ffprobe_duration(storage.signed_url(reference, "source.mp4", expires_in=300))
        path = VideoService.resolve_local_video_path(url)
        if path.is_file():
            return ffprobe_duration(path)
        # Local file and sidecar are both gone. The upload session still names
        # the durable object; ffprobe reads its headers through a signed URL.
        if storage_reference:
            storage = MediaStorage()
            if storage.enabled:
                return ffprobe_duration(storage.signed_url(storage_reference, "source.mp4", expires_in=300))
        return ffprobe_duration(path)

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
        are rendered. This only enforces the absolute ceiling. An uploaded
        original is copied to object storage first, so the capped file can
        be stored beside that original.
        """
        from ..media.upload_ingest import ensure_local_upload_durable

        await ensure_local_upload_durable(Path(path))
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
        instructions: Optional[str] = None,
    ) -> Any:
        """
        Analyze transcript with AI to find relevant segments.
        This is already async, no need to wrap.
        ``instructions`` (the user's steering text) reaches the prompt only
        when set, so the call without them is unchanged.
        """
        logger.info("Starting AI analysis of transcript")
        relevant_parts = await get_most_relevant_parts_by_transcript(
            transcript,
            clip_signals=clip_signals,
            performance_context=performance_context,
            clip_length=clip_length,
            include_reserve=True,
            **({"instructions": instructions} if instructions else {}),
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
        output_offsets: Optional[List[float]] = None,
    ) -> List[Dict[str, Any]]:
        """Plan and fetch stock cutaways for a clip rendered from ``keep_ranges``.

        ``output_offsets`` places planned ranges on the clip timeline; without
        them the renderer's default crossfades apply.
        """
        if not broll_configured() or not keep_ranges:
            return []
        try:
            transcript = await run_in_thread(load_cached_transcript_data, video_path)
            words = get_words_for_keep_ranges(transcript, keep_ranges, output_offsets) if transcript else []
            # The renderer crossfades between kept ranges, which shortens the clip.
            clip_seconds = (
                rendered_duration(keep_ranges, output_offsets)
                if output_offsets is not None
                else rendered_ranges_duration(keep_ranges)
            )
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
                if not placements and segment.get("planned"):
                    # The same ranges and junction fades the render will use.
                    resolved = await run_in_thread(
                        resolve_render_ranges,
                        video_path,
                        segment,
                        cleanup_settings,
                        float(segment.get("max_seconds") or max_clip_seconds),
                    )
                    placements = await VideoService.plan_broll(
                        video_path, resolved.keep_ranges, output_format, max_short_side, session,
                        output_offsets=resolved.output_offsets,
                    )
                elif not placements:
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
                    # The re-encode can shift the length by a frame.
                    clip_info["duration"] = await run_in_thread(
                        rendered_clip_duration,
                        Path(clip_info["path"]),
                        clip_info.get("keep_ranges") or [],
                    )
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
            start_seconds, end_seconds = segment_bounds(segment)
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
            resolved = resolve_render_ranges(
                video_path, segment, cleanup_settings, max_clip_seconds
            )
            keep_ranges = resolved.keep_ranges
            output_offsets = resolved.output_offsets
            clip_text = resolved.text
            start_seconds, end_seconds = resolved.bounds

            broll_placements: List[Dict[str, Any]] = []
            if include_broll:
                broll_placements = await VideoService.plan_broll(
                    video_path, keep_ranges, output_format, max_short_side, broll_session,
                    **({"output_offsets": output_offsets} if output_offsets is not None else {}),
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
                **({"output_offsets": output_offsets} if output_offsets is not None else {}),
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

            from ..media.ffmpeg import output_offsets_for_ranges
            save_clip_source_ranges(
                clip_path, keep_ranges, output_offsets or output_offsets_for_ranges(keep_ranges)
            )
            save_clip_broll(clip_path, broll_placements)
            if render_report.get("caption_y") is not None:
                from ..clip_source_map import save_clip_caption_settings
                save_clip_caption_settings(clip_path, {"position_y": render_report["caption_y"], "position_is_center": True})
            # Store what plays, after crossfades and the resolution cap.
            cleaned_duration = await run_in_thread(
                rendered_clip_duration, clip_path, keep_ranges, output_offsets
            )
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
                "output_offsets": output_offsets,
                "group_output_starts": resolved.group_output_starts,
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
    async def _select_highlight_segments(
        *,
        video_path: Path,
        transcript: str,
        cached_analysis_json: Optional[str],
        performance_context: Optional[str],
        clip_length: str,
        processing_mode: str,
        file_duration: Optional[float],
        progress_callback: Optional[Callable[[int, str, str], Awaitable[None]]],
        should_cancel: Optional[Callable[[], Awaitable[bool]]],
        runtime_config: Any,
        instructions: Optional[str] = None,
    ) -> tuple[Any, List[Dict[str, Any]], int]:
        """Pick standalone highlights: (analysis, ranked segments with reserve, clip limit).

        ``instructions`` steers the analysis prompt; without them the
        analysis is exactly the original highlights pipeline.
        """
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
                **({"instructions": instructions} if instructions else {}),
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
                if segment.get("opening_reviewed"):
                    segment_payload["opening_reviewed"] = True
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

        try:
            segments_json = VideoService._refine_segment_openings(
                segments_json, transcript_data, clip_length
            )
        except Exception:
            # A heuristic must never fail the task; keep the AI's openings.
            logger.exception("Opening review failed; keeping the selected openings")

        clip_limit = max_final_clips_for_duration(file_duration, runtime_config.max_clips)
        if processing_mode == "fast":
            clip_limit = min(clip_limit, runtime_config.fast_mode_max_clips)
        # Retain a bounded reserve until picture checks finish, so a rejected
        # slate can be replaced by the next already-ranked candidate.
        segments_json = segments_json[: min(24, max(1, clip_limit) * 2)]

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

        return relevant_parts, segments_json, clip_limit

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
        *,
        workflow_spec: Optional[WorkflowSpec] = None,
        plan_cache: Optional[PlanCache] = None,
        outline_cache_key: Optional[str] = None,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Complete video processing pipeline.
        Returns dict with segments and clips info.

        Without ``workflow_spec`` (or for highlights) this picks standalone
        highlights. Any other workflow is planned by ``plan_workflow`` from the
        word-timed transcript, using ``plan_cache`` and ``outline_cache_key``
        for its cached plan and outline, and returns locked segments plus the
        ``workflow_plan`` to persist.

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
                if url.startswith("upload://s3/"):
                    video_path = await run_in_thread(VideoService.resolve_local_video_path, url)
                else:
                    video_path = await materialize_stored_upload(url)
                    if video_path is None or not video_path.exists():
                        video_path = VideoService.resolve_local_video_path(url)
                if video_path is None or not video_path.exists():
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
            # Uploads only. YouTube and streams already downloaded above.
            if source_type not in {"youtube", "twitch", "kick"} and await upload_needs_remote_copy(url):
                if progress_callback:
                    await progress_callback(12, "Storing upload...", "processing")
                await persist_local_upload(url)
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

            workflow = workflow_spec.workflow if workflow_spec is not None else "highlights"
            workflow_result: Optional[WorkflowResult] = None
            if workflow == "highlights":
                instructions = workflow_spec.options.instructions if workflow_spec is not None else ""
                relevant_parts, segments_json, clip_limit = await VideoService._select_highlight_segments(
                    video_path=video_path,
                    transcript=transcript,
                    cached_analysis_json=cached_analysis_json,
                    performance_context=performance_context,
                    clip_length=clip_length,
                    processing_mode=processing_mode,
                    file_duration=file_duration,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                    runtime_config=runtime_config,
                    **({"instructions": instructions} if instructions else {}),
                )
            else:
                if should_cancel and await should_cancel():
                    raise Exception("Task cancelled")
                if progress_callback:
                    await progress_callback(50, "Reading the whole video...", "processing")
                # Planners cut on these word timings, so they are read before planning.
                transcript_data = await run_in_thread(load_cached_transcript_data, video_path)
                source_seconds = float(file_duration or 0.0)
                if source_seconds <= 0:
                    words = (transcript_data or {}).get("words") or []
                    source_seconds = max((float(word.get("end") or 0) for word in words), default=0.0) / 1000.0
                workflow_result = await VideoService._plan_workflow_segments(
                    workflow_spec=workflow_spec,
                    task_id=task_id,
                    transcript=transcript,
                    transcript_data=transcript_data,
                    source_seconds=source_seconds,
                    cached_analysis_json=cached_analysis_json,
                    performance_context=performance_context,
                    cleanup_settings=cleanup_settings,
                    plan_cache=plan_cache,
                    outline_cache_key=outline_cache_key,
                    progress_callback=progress_callback,
                    should_cancel=should_cancel,
                )
                # Static checks below edit segments and plan; the cached result stays as planned.
                segments_json = copy.deepcopy(workflow_result.segments)
                workflow_plan = copy.deepcopy(workflow_result.plan)
                # Topic plans list a few reserve moments after the selected ones.
                clip_limit = sum(
                    1 for segment in segments_json
                    if not (segment.get("workflow_meta") or {}).get("reserve")
                )
                relevant_parts = None

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

            # The picture is only available now when analysis ran on audio.
            if workflow in ("highlights", "topic"):
                if segments_json:
                    try:
                        segments_json = await run_in_thread(
                            VideoService._drop_static_segments, video_path, segments_json
                        )
                    except Exception:
                        logger.exception("Visual suitability check failed; keeping every segment")

                segments_json = segments_json[:max(1, clip_limit)]
                if not segments_json:
                    raise ValueError("No usable footage remains after excluding interruption notices.")
            elif workflow == "series":
                # Parts tell one story in order; a still picture is never a reason to skip one.
                await run_in_thread(VideoService._log_static_parts, video_path, segments_json)
            else:
                min_seconds = workflow_budget(workflow, workflow_spec.clip_length, source_seconds).min
                segments_json = [
                    await run_in_thread(
                        VideoService._drop_static_ranges,
                        video_path, segment, min_seconds, cleanup_settings,
                    )
                    for segment in segments_json
                ]
                _sync_plan_ranges(workflow_plan, segments_json)

            if workflow_result is not None:
                for segment in segments_json:
                    (segment.get("workflow_meta") or {}).pop("reserve", None)
                return {
                    "segments": segments_json,
                    "segments_to_render": segments_json,
                    "video_path": str(video_path),
                    "clips": [],
                    "summary": workflow_result.summary,
                    "key_topics": workflow_result.key_topics,
                    "transcript": transcript,
                    "analysis_json": workflow_result.to_cache_json(),
                    "workflow_plan": workflow_plan,
                }

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
