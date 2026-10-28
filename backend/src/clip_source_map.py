"""
Helpers for persisting a clip's mapping back to source-video time ranges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple
import json
import logging

logger = logging.getLogger(__name__)

SourceRange = Tuple[float, float]
SOURCE_MAP_VERSION = 1
MIN_RANGE_SECONDS = 0.05


def clip_source_map_path(clip_path: Path) -> Path:
    return clip_path.with_suffix(".source_map.json")


def normalize_source_ranges(
    ranges: Iterable[tuple[float, float]] | None,
) -> List[SourceRange]:
    normalized: List[SourceRange] = []
    for start, end in ranges or []:
        try:
            start_value = float(start)
            end_value = float(end)
        except (TypeError, ValueError):
            continue
        if end_value - start_value <= MIN_RANGE_SECONDS:
            continue
        normalized.append((start_value, end_value))
    return normalized


def total_source_duration(ranges: Iterable[tuple[float, float]] | None) -> float:
    return sum(end - start for start, end in normalize_source_ranges(ranges))


def source_range_bounds(
    ranges: Iterable[tuple[float, float]] | None,
) -> SourceRange | None:
    normalized = normalize_source_ranges(ranges)
    if not normalized:
        return None
    # Ranges play in list order and may be non-monotonic (a hook teaser is
    # played before the clip body), so the bounds are the extremes.
    return (
        min(start for start, _ in normalized),
        max(end for _, end in normalized),
    )


def save_clip_source_ranges(
    clip_path: Path,
    ranges: Iterable[tuple[float, float]] | None,
    output_offsets: Iterable[float] | None = None,
) -> None:
    normalized = normalize_source_ranges(ranges)
    path = clip_source_map_path(clip_path)
    if not normalized:
        path.unlink(missing_ok=True)
        return

    # Rewriting the ranges of an existing clip (a partial download's timeline
    # restore) keeps the settings stored next to them.
    payload = _read_payload(path)
    payload.update({
        "version": SOURCE_MAP_VERSION,
        "source_ranges": [
            {"start": round(start, 6), "end": round(end, 6)}
            for start, end in normalized
        ],
    })
    if output_offsets is None:
        cursor = 0.0
        output_offsets = []
        for a, b in normalized:
            output_offsets.append(cursor)
            cursor += b - a
    payload["output_offsets"] = [round(float(value), 6) for value in output_offsets]
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_payload(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def load_clip_source_ranges(clip_path: Path) -> List[SourceRange] | None:
    path = clip_source_map_path(clip_path)
    if not path.exists():
        return None

    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read clip source map %s: %s", path, exc)
        return None

    raw_ranges = payload.get("source_ranges")
    if not isinstance(raw_ranges, list):
        return None

    parsed_ranges: List[SourceRange] = []
    for item in raw_ranges:
        if not isinstance(item, dict):
            continue
        if "start" not in item or "end" not in item:
            continue
        parsed_ranges.append((item["start"], item["end"]))

    normalized = normalize_source_ranges(parsed_ranges)
    return normalized or None


def copy_clip_source_ranges(source_path: Path, target_path: Path) -> None:
    ranges = load_clip_source_ranges(source_path)
    if ranges is None:
        clip_source_map_path(target_path).unlink(missing_ok=True)
        return
    save_clip_source_ranges(target_path, ranges, load_clip_output_offsets(source_path, ranges))


def save_clip_caption_settings(clip_path: Path, settings: dict) -> None:
    """Store editor settings alongside the existing source mapping."""
    path = clip_source_map_path(clip_path)
    payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"version": SOURCE_MAP_VERSION}
    payload["caption_settings"] = settings
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_clip_caption_settings(clip_path: Path) -> dict | None:
    path = clip_source_map_path(clip_path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        settings = payload.get("caption_settings")
        return settings if isinstance(settings, dict) else None
    except (OSError, ValueError, AttributeError):
        return None


def save_clip_broll(clip_path: Path, placements: list[dict] | None) -> None:
    """Store the clip's B-roll placements (clip seconds) next to its source map."""
    path = clip_source_map_path(clip_path)
    if not placements and not path.exists():
        return
    payload = _read_payload(path) or {"version": SOURCE_MAP_VERSION}
    if placements:
        payload["broll"] = placements
    else:
        payload.pop("broll", None)
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_clip_broll(clip_path: Path) -> list[dict]:
    placements = _read_payload(clip_source_map_path(clip_path)).get("broll")
    if not isinstance(placements, list):
        return []
    return [p for p in placements if isinstance(p, dict) and {"start", "duration"} <= p.keys()]


def load_clip_output_offsets(clip_path: Path, ranges: Iterable[SourceRange]) -> List[float]:
    """Actual source contributions on the rendered timeline, including crossfade overlaps."""
    import math
    normalized = normalize_source_ranges(ranges)
    values = _read_payload(clip_source_map_path(clip_path)).get("output_offsets")
    if isinstance(values, list) and len(values) == len(normalized):
        try:
            offsets = [float(value) for value in values]
            if offsets and abs(offsets[0]) < 1e-6 and all(math.isfinite(v) and v >= 0 for v in offsets) and all(offsets[i] >= offsets[i-1] and offsets[i] <= offsets[i-1] + normalized[i-1][1] - normalized[i-1][0] + 1e-6 for i in range(1, len(offsets))):
                return offsets
        except (ValueError, TypeError):
            pass
    from .media.ffmpeg import output_offsets_for_ranges
    return output_offsets_for_ranges(normalized)


def slice_source_timeline(ranges: Iterable[SourceRange], output_start: float, output_end: float, output_offsets: Iterable[float]) -> tuple[List[SourceRange], List[float]]:
    normalized = normalize_source_ranges(ranges)
    offsets = list(output_offsets)
    total = max((offset + end - start for offset, (start, end) in zip(offsets, normalized)), default=0.0)
    window_start, window_end = max(0.0, min(output_start, total)), max(0.0, min(output_end, total))
    sliced, mapped = [], []
    for (source_start, source_end), offset in zip(normalized, offsets):
        a, b = max(window_start, offset), min(window_end, offset + source_end - source_start)
        if b - a > MIN_RANGE_SECONDS:
            sliced.append((source_start + a - offset, source_start + b - offset))
            mapped.append(a - window_start)
    return sliced, mapped


def slice_source_ranges(
    ranges: Iterable[tuple[float, float]] | None,
    output_start: float,
    output_end: float,
) -> List[SourceRange]:
    normalized = normalize_source_ranges(ranges)
    if not normalized:
        return []

    total_duration = total_source_duration(normalized)
    window_start = max(0.0, min(float(output_start), total_duration))
    window_end = max(window_start, min(float(output_end), total_duration))

    sliced: List[SourceRange] = []
    timeline_cursor = 0.0
    for source_start, source_end in normalized:
        segment_duration = source_end - source_start
        timeline_start = timeline_cursor
        timeline_end = timeline_cursor + segment_duration

        overlap_start = max(window_start, timeline_start)
        overlap_end = min(window_end, timeline_end)
        if overlap_end - overlap_start > MIN_RANGE_SECONDS:
            relative_start = overlap_start - timeline_start
            relative_end = overlap_end - timeline_start
            sliced.append(
                (source_start + relative_start, source_start + relative_end)
            )

        timeline_cursor = timeline_end

    return normalize_source_ranges(sliced)


def trim_source_ranges(
    ranges: Iterable[tuple[float, float]] | None,
    start_offset: float,
    end_offset: float,
) -> List[SourceRange]:
    total_duration = total_source_duration(ranges)
    return slice_source_ranges(
        ranges,
        max(0.0, float(start_offset)),
        max(0.0, total_duration - max(0.0, float(end_offset))),
    )


def split_source_ranges(
    ranges: Iterable[tuple[float, float]] | None,
    split_time: float,
) -> tuple[List[SourceRange], List[SourceRange]]:
    total_duration = total_source_duration(ranges)
    split_point = max(0.0, min(float(split_time), total_duration))
    return (
        slice_source_ranges(ranges, 0.0, split_point),
        slice_source_ranges(ranges, split_point, total_duration),
    )
