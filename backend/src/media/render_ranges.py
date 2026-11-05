"""Which source ranges a clip plays, and where each lands on the clip timeline.

Both renderers (``VideoService.create_single_clip`` and
``video_utils.create_clips_from_segments``) resolve a segment here, so a
highlight and its regeneration always cut the same way.

Highlights segments carry rough AI bounds: the start is snapped to a sentence,
the end finishes the sentence, an optional cold-open teaser plays first and
the clip is capped at the preset's maximum.

Workflow segments are *planned*: their ``source_ranges`` already sit on
sentence edges chosen by the planner, and adjacent series parts share one
exact cut. Moving either edge would repeat or drop a word, so planned ranges
are only cleaned up (pauses, fillers) inside each range. Every junction gets
the planner's fade, so captions, speaker turns, B-roll and the stored source
map all read one explicit set of output offsets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..clip_source_map import normalize_source_ranges, source_range_bounds
from ..workflows.durations import WORKFLOW_RENDER_HARD_MAX_SECONDS
from ..workflows.language import is_no_space
from .common import logger
from .ffmpeg import crossfade_fade_for_ranges
from .timeline import (
    apply_segment_hook_teaser,
    build_clip_keep_ranges,
    build_keep_ranges_from_source_ranges,
    clamp_keep_ranges_to_max_duration,
    extend_keep_ranges_to_sentence_boundary,
    get_words_for_keep_ranges,
    keep_ranges_transcript_text,
    parse_timestamp_to_seconds,
    snap_clip_start_to_sentence,
)
from .transcription import load_cached_transcript_data

Range = Tuple[float, float]


@dataclass
class RenderRanges:
    keep_ranges: List[Range]
    text: str
    # None = legacy: consumers derive offsets with output_offsets_for_ranges.
    output_offsets: Optional[List[float]]
    # Planned only, one per planned range (its index): where that range starts
    # on the clip timeline, or None when the safety cap cut it off.
    group_output_starts: List[Optional[float]] = field(default_factory=list)
    # Source bounds handed to the renderer; raw AI bounds get the snapped start.
    bounds: Range = (0.0, 0.0)


def segment_bounds(segment: Dict[str, Any]) -> Range:
    """Source span of a segment: its supplied ranges, else its MM:SS labels."""
    provided_bounds = source_range_bounds(
        normalize_source_ranges(segment.get("keep_ranges"))
        or normalize_source_ranges(segment.get("source_ranges"))
    )
    if provided_bounds:
        return provided_bounds
    return (
        parse_timestamp_to_seconds(segment["start_time"]),
        parse_timestamp_to_seconds(segment["end_time"]),
    )


def resolve_render_ranges(
    video_path: Path,
    segment: Dict[str, Any],
    cleanup_settings: Optional[Dict[str, Any]],
    max_clip_seconds: float,
) -> RenderRanges:
    """Keep ranges, spoken text and output offsets for one segment."""
    if segment.get("planned"):
        return _resolve_planned(video_path, segment, cleanup_settings, max_clip_seconds)
    return _resolve_legacy(video_path, segment, cleanup_settings, max_clip_seconds)


def _resolve_legacy(
    video_path: Path,
    segment: Dict[str, Any],
    cleanup_settings: Optional[Dict[str, Any]],
    max_clip_seconds: float,
) -> RenderRanges:
    provided_keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
    source_ranges = normalize_source_ranges(segment.get("source_ranges"))
    start_seconds, end_seconds = segment_bounds(segment)
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
    # Rewind and sentence extension happen before this, so the stored
    # ranges — and the render, which must not extend them again — stay
    # inside the selected duration cap.
    keep_ranges = clamp_keep_ranges_to_max_duration(video_path, keep_ranges, max_clip_seconds)
    if not provided_keep_ranges and not source_ranges:
        # Snapping, cleanup, the teaser and the cap change what is
        # spoken; keep the stored text word-for-word with the timeline.
        clip_text = keep_ranges_transcript_text(video_path, keep_ranges) or clip_text
    return RenderRanges(
        keep_ranges=keep_ranges,
        text=clip_text,
        output_offsets=None,
        bounds=(start_seconds, end_seconds),
    )


def _resolve_planned(
    video_path: Path,
    segment: Dict[str, Any],
    cleanup_settings: Optional[Dict[str, Any]],
    max_clip_seconds: float,
) -> RenderRanges:
    planned = normalize_source_ranges(segment.get("source_ranges"))
    provided_keep = normalize_source_ranges(segment.get("keep_ranges"))
    fades: Optional[List[float]] = None
    if provided_keep:
        # Regeneration with unchanged cleanup: the stored, already cleaned
        # ranges and the offsets they were rendered with.
        keep = provided_keep
        stored = segment.get("output_offsets") or []
        if len(stored) == len(keep):
            offsets = [float(value) for value in stored]
        else:
            logger.warning(
                "Stored offsets do not match %d kept ranges; re-rendering with hard cuts", len(keep)
            )
            offsets = cumulative_offsets(keep)
        groups = [int(value) for value in segment.get("range_groups") or []]
        if len(groups) != len(keep):
            groups = list(range(len(keep)))
    else:
        keep, groups = expand_ranges_with_groups(video_path, planned, cleanup_settings)
        fades = [float(value) for value in segment.get("junction_fades") or []]
        offsets = output_offsets_for_junctions(keep, groups, fades)

    cap = min(float(segment.get("max_seconds") or max_clip_seconds), WORKFLOW_RENDER_HARD_MAX_SECONDS)
    played = rendered_duration(keep, offsets)
    if keep and played > cap + 0.05:
        # Planners size every output inside its budget; reaching this is a planner bug.
        logger.warning(
            "Planned clip %s-%s plays %.1fs, over its %.1fs cap; trimming the end",
            segment.get("start_time"),
            segment.get("end_time"),
            played,
            cap,
        )
        overlap = sum(end - start for start, end in keep) - played
        keep = normalize_source_ranges(
            clamp_keep_ranges_to_max_duration(video_path, keep, cap + max(0.0, overlap))
        )
        groups = groups[: len(keep)]
        if fades is not None:
            offsets = output_offsets_for_junctions(keep, groups, fades)
        else:
            offsets = offsets[: len(keep)]

    text = _planned_text(video_path, keep) or segment.get("text", "")
    group_count = max(len(planned), max(groups) + 1 if groups else 0)
    group_output_starts: List[Optional[float]] = [
        offsets[groups.index(group)] if group in groups else None
        for group in range(group_count)
    ]
    return RenderRanges(
        keep_ranges=keep,
        text=text,
        output_offsets=offsets,
        group_output_starts=group_output_starts,
        bounds=source_range_bounds(keep) or source_range_bounds(planned) or (0.0, 0.0),
    )


def _planned_text(video_path: Path, keep: Sequence[Range]) -> str:
    """Spoken text of a planned clip, written the way its language writes it.

    Chinese, Japanese and Thai words are joined without spaces, as the
    planner joined its units; other languages read like highlights text.
    """
    transcript_data = load_cached_transcript_data(video_path) or {}
    tokens = [
        str(word["text"]).strip()
        for word in get_words_for_keep_ranges(transcript_data, list(keep))
        if str(word["text"]).strip()
    ]
    if tokens and is_no_space(transcript_data.get("language_code"), "".join(tokens[:60])):
        return "".join(tokens)
    return keep_ranges_transcript_text(video_path, list(keep))


def expand_ranges_with_groups(
    video_path: Path,
    planned: Sequence[Range],
    cleanup_settings: Optional[Dict[str, Any]],
) -> Tuple[List[Range], List[int]]:
    """Clean up each planned range on its own; record which range each piece came from.

    Cleanup only trims silences and fillers inside a range, never its edges'
    neighbours, so the planner's cuts stay exactly where they were.
    """
    keep: List[Range] = []
    groups: List[int] = []
    for index, (start, end) in enumerate(normalize_source_ranges(planned)):
        pieces = normalize_source_ranges(
            build_clip_keep_ranges(video_path, start, end, cleanup_settings)
        ) or [(start, end)]
        keep.extend(pieces)
        groups.extend([index] * len(pieces))
    return keep, groups


def output_offsets_for_junctions(
    keep: Sequence[Range],
    groups: Sequence[int],
    planned_fades: Sequence[float],
) -> List[float]:
    """Clip-timeline start of every kept range.

    Between two planned ranges the planner's fade applies (0 = hard cut).
    Cleanup cuts inside one planned range get today's crossfade rule for that
    range's pieces. A fade never exceeds half of either side.
    """
    keep = normalize_source_ranges(keep)
    durations = [end - start for start, end in keep]
    intra: Dict[int, float] = {}
    for group in set(groups):
        intra[group] = crossfade_fade_for_ranges(
            [piece for piece, owner in zip(keep, groups) if owner == group]
        )
    offsets: List[float] = []
    cursor = 0.0
    for index, duration in enumerate(durations):
        if index > 0:
            group = groups[index]
            if group != groups[index - 1]:
                junction = group - 1
                fade = float(planned_fades[junction]) if 0 <= junction < len(planned_fades) else 0.0
            else:
                fade = intra.get(group, 0.0)
            fade = max(0.0, min(fade, 0.5 * min(durations[index - 1], duration)))
            cursor -= fade
        offsets.append(cursor)
        cursor += duration
    return offsets


def cumulative_offsets(keep: Sequence[Range]) -> List[float]:
    """Hard cuts: each range starts where the previous one ended."""
    offsets: List[float] = []
    cursor = 0.0
    for start, end in normalize_source_ranges(keep):
        offsets.append(cursor)
        cursor += end - start
    return offsets


def rendered_duration(keep: Sequence[Range], offsets: Sequence[float]) -> float:
    """Clip length for these ranges at these offsets."""
    ranges = normalize_source_ranges(keep)
    if not ranges:
        return 0.0
    if len(offsets) != len(ranges):
        raise ValueError("Output offsets must match the kept ranges")
    start, end = ranges[-1]
    return max(0.0, float(offsets[-1]) + end - start)
