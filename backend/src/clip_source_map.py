"""
Helpers for persisting a clip's mapping back to source-video time ranges.
"""

from __future__ import annotations

from pathlib import Path
from typing import Iterable, List, Tuple, TypedDict
import json
import logging
import math

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


# Generated filters are a few KB; anything far larger is not a plan we wrote.
MAX_VERTICAL_FILTER_CHARS = 200_000
# A stored filter is only replayed when it reads no other file.
_FILTER_FILE_INPUTS = ("movie=", "amovie=", "sendcmd", "filename=", "file=")


def save_clip_vertical_layout(clip_path: Path, layout: dict | None) -> None:
    """Store the 9:16 plan the clip was rendered with, so the editor can show the same framing.

    ``layout`` holds ``kind``, ``video_filter`` and ``mode`` from
    ``describe_vertical_layout``, plus the ``width``, ``height`` and
    ``duration`` of the trimmed source the filter was planned on. An
    interview layout may add its per-shot ``segments`` (start, end, kind)
    and the burned-in source ``caption_band`` (top, bottom), from which the
    studio derives the caption rows (``edit_fx.caption_rows``).
    """
    path = clip_source_map_path(clip_path)
    if not layout and not path.exists():
        return
    payload = _read_payload(path) or {"version": SOURCE_MAP_VERSION}
    if layout:
        stored = {
            "kind": str(layout["kind"]),
            "video_filter": str(layout["video_filter"]),
            "mode": str(layout["mode"]),
            "width": int(layout["width"]),
            "height": int(layout["height"]),
            "duration": round(float(layout["duration"]), 3),
        }
        segments = _layout_segments(layout.get("segments"))
        if segments:
            stored["segments"] = segments
        band = _layout_band(layout.get("caption_band"))
        if band is not None:
            stored["caption_band"] = band
        payload["vertical_layout"] = stored
    else:
        payload.pop("vertical_layout", None)
    path.write_text(json.dumps(payload), encoding="utf-8")


def load_clip_vertical_layout(clip_path: Path) -> dict | None:
    """The stored vertical plan, or None when absent or not safe to replay."""
    layout = _read_payload(clip_source_map_path(clip_path)).get("vertical_layout")
    if not isinstance(layout, dict):
        return None
    try:
        plan = {
            "kind": str(layout["kind"]),
            "video_filter": str(layout["video_filter"]),
            "mode": str(layout["mode"]),
            "width": int(layout["width"]),
            "height": int(layout["height"]),
            "duration": float(layout["duration"]),
        }
    except (KeyError, TypeError, ValueError):
        return None
    lowered = plan["video_filter"].lower()
    if (
        plan["mode"] not in {"vf", "complex"}
        or not plan["video_filter"]
        or len(plan["video_filter"]) > MAX_VERTICAL_FILTER_CHARS
        or any(marker in lowered for marker in _FILTER_FILE_INPUTS)
        or plan["width"] <= 0
        or plan["height"] <= 0
    ):
        return None
    # Optional caption-row facts: dropped (never the whole plan) when unreadable.
    segments = _layout_segments(layout.get("segments"))
    if segments:
        plan["segments"] = segments
    band = _layout_band(layout.get("caption_band"))
    if band is not None:
        plan["caption_band"] = band
    return plan


MAX_LAYOUT_SEGMENTS = 500
LAYOUT_SEGMENT_KINDS = frozenset({"pair", "single", "fit"})


def _layout_segments(raw) -> list[dict] | None:
    """An interview layout's shots as ``[{start, end, kind}]`` (finite, ordered, known kinds), or None when absent
    or unreadable."""
    if not isinstance(raw, list) or not raw or len(raw) > MAX_LAYOUT_SEGMENTS:
        return None
    out = []
    for item in raw:
        if not isinstance(item, dict) or item.get("kind") not in LAYOUT_SEGMENT_KINDS:
            return None
        try:
            start, end = float(item["start"]), float(item["end"])
        except (KeyError, TypeError, ValueError):
            return None
        if not (math.isfinite(start) and math.isfinite(end)) or start < 0 or end <= start:
            return None
        out.append({"start": round(start, 3), "end": round(end, 3), "kind": str(item["kind"])})
    return out


def _layout_band(raw) -> list[float] | None:
    if not isinstance(raw, (list, tuple)) or len(raw) != 2:
        return None
    try:
        top, bottom = float(raw[0]), float(raw[1])
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(top) and math.isfinite(bottom)) or not 0 <= top < bottom <= 1:
        return None
    return [round(top, 4), round(bottom, 4)]


# --- dynamic edits (FX path only; §3.3) ------------------------------------------------------------------------------
# A legacy clip's sidecar never gets these keys: they are written only by the FX render path and removed only by an
# explicit switch back to off + a legacy template.
FX_SIDECAR_KEYS = ("edit_style", "edit_beats", "edit_fx", "face_track", "fx_level", "edit_fx_warp")
FX_LEVELS = ("full", "safe", "none")
FACE_TRACK_MAX_FPS = 6.0
FACE_TRACK_DECIMALS = 3


class EditFxSidecar(TypedDict):
    style: dict; beats: dict | None; plan: dict | None; face_track: dict | None; fx_level: str | None
    applied_warp: list[dict]          # [] when the key is missing (identity)


def _compact_face_track(track: dict | None) -> dict | None:
    """Decimate to at most 6 fps and round to 3 decimals: keeps the sidecar inside its 150 KB budget.

    A malformed track is dropped (logged), never raised: the face track is decorative and must not cost the clip.
    """
    if not isinstance(track, dict):
        return None
    try:
        fps = float(track.get("fps", FACE_TRACK_MAX_FPS) or FACE_TRACK_MAX_FPS)
        if not math.isfinite(fps) or fps <= 0:
            raise ValueError(f"bad fps {fps!r}")
        samples = list(track.get("samples") or ())
        step = 1
        if fps > FACE_TRACK_MAX_FPS + 1e-9:
            # ceil, not round: 15 fps must become 5 fps (step 3), never 7.5 fps (step 2).
            step = math.ceil(fps / FACE_TRACK_MAX_FPS - 1e-9)
            fps = fps / step
        compact = []
        for t, faces in samples[::step]:
            compact.append([
                round(float(t), FACE_TRACK_DECIMALS),
                [[round(float(v), FACE_TRACK_DECIMALS) for v in face] for face in faces],
            ])
    except (TypeError, ValueError) as exc:
        logger.warning("fx_face_track_unsavable error=%s", exc)
        return None
    return {**track, "fps": fps, "samples": compact}


def save_clip_edit_fx(clip_path: Path, *, style: dict, beats: dict | None, plan: dict,
                      face_track: dict | None, fx_level: str, applied_warp: list[dict]) -> None:
    """Record how an FX clip was made (style, beats, the plan as rendered, face track, level, applied warp).

    Other keys of the sidecar (source ranges, caption settings, B-roll, vertical layout) are kept. A missing
    ``beats`` or ``face_track`` removes a stale one, so the sidecar always describes this render.
    """
    if fx_level not in FX_LEVELS:
        raise ValueError(f"fx_level must be one of {FX_LEVELS}, not {fx_level!r}")
    path = clip_source_map_path(clip_path)
    payload = _read_payload(path) or {"version": SOURCE_MAP_VERSION}
    payload["edit_style"] = dict(style or {})
    payload["edit_fx"] = plan
    payload["fx_level"] = fx_level
    payload["edit_fx_warp"] = list(applied_warp or [])
    if beats is not None:
        payload["edit_beats"] = beats
    else:
        payload.pop("edit_beats", None)
    compact_track = _compact_face_track(face_track)
    if compact_track is not None:
        payload["face_track"] = compact_track
    else:
        payload.pop("face_track", None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def clear_clip_edit_fx(clip_path: Path) -> None:
    """Remove the FX keys (explicit off + legacy template only, §13.1). Never creates a sidecar."""
    path = clip_source_map_path(clip_path)
    if not path.exists():
        return
    payload = _read_payload(path)
    if not any(key in payload for key in FX_SIDECAR_KEYS):
        return
    for key in FX_SIDECAR_KEYS:
        payload.pop(key, None)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _corrupt(clip_path: Path, field: str, exc: Exception | str) -> None:
    logger.warning("fx_sidecar_corrupt clip=%s field=%s error=%s", clip_path.name, field, exc)


def _lenient_model(clip_path: Path, field: str, model_cls, raw) -> dict | None:
    from pydantic import ValidationError

    from .edit_fx.compat import known_fields, log_dropped

    if raw is None:
        return None
    if not isinstance(raw, dict):
        _corrupt(clip_path, field, "not an object")
        return None
    clean, dropped = known_fields(model_cls, raw)
    log_dropped(f"sidecar.{field}", dropped)
    try:
        return model_cls.model_validate(clean).model_dump(mode="json")
    except ValidationError as exc:
        if isinstance(clean.get("cues"), list):
            # One cue a newer deploy wrote with a value this reader does not know (a new accent variant, a new
            # sound) costs that cue, not the whole plan: drop the cues that fail on their own and retry once.
            salvaged = _valid_cues(clean["cues"])
            if len(salvaged) < len(clean["cues"]):
                try:
                    plan = model_cls.model_validate({**clean, "cues": salvaged}).model_dump(mode="json")
                except ValidationError:
                    pass
                else:
                    _corrupt(clip_path, f"{field}.cues", f"{len(clean['cues']) - len(salvaged)} unreadable cues dropped")
                    return plan
        _corrupt(clip_path, field, f"{exc.error_count()} validation errors")
        return None


def _valid_cues(cues: list) -> list:
    from pydantic import TypeAdapter, ValidationError

    from .edit_fx.contracts import FxCue

    adapter = TypeAdapter(FxCue)
    valid = []
    for cue in cues:
        try:
            adapter.validate_python(cue)
        except ValidationError:
            continue
        valid.append(cue)
    return valid


def load_clip_edit_fx(clip_path: Path) -> EditFxSidecar | None:
    """The FX keys of a clip's sidecar, or None when it has no ``edit_fx`` key (a legacy clip).

    Lenient (AI-M9): unknown keys are dropped via ``compat.known_fields``; a corrupt field reads as None (``style``
    as ``{}``, ``applied_warp`` as ``[]``) with a logged ``fx_sidecar_corrupt`` event. Nothing is ever deleted here.
    """
    from .edit_fx.compat import known_fields
    from .edit_fx.contracts import EditBeats, FaceTrack, FxPlan, WarpPieceJson
    from .edit_fx.settings import read_edit_style
    from .edit_fx.warp import warp_from_json

    payload = _read_payload(clip_source_map_path(clip_path))
    if "edit_fx" not in payload:
        return None

    raw_style = payload.get("edit_style")
    if raw_style is not None and not isinstance(raw_style, dict):
        _corrupt(clip_path, "edit_style", "not an object")
    style = read_edit_style(raw_style).to_json() if isinstance(raw_style, dict) else {}

    fx_level = payload.get("fx_level")
    if fx_level is not None and fx_level not in FX_LEVELS:
        _corrupt(clip_path, "fx_level", repr(fx_level)[:40])
        fx_level = None

    applied_warp: list[dict] = []
    raw_warp = payload.get("edit_fx_warp")
    if raw_warp:
        try:
            if not isinstance(raw_warp, list):
                raise ValueError("not a list")
            pieces = [
                WarpPieceJson.model_validate(known_fields(WarpPieceJson, piece)[0]).model_dump(mode="json")
                for piece in raw_warp
            ]
            if not warp_from_json(pieces).nontrivial:
                raise ValueError("unreadable pieces")
            applied_warp = pieces
        except Exception as exc:   # pydantic ValidationError, ValueError, TypeError
            _corrupt(clip_path, "edit_fx_warp", exc)

    return EditFxSidecar(
        style=style,
        beats=_lenient_model(clip_path, "edit_beats", EditBeats, payload.get("edit_beats")),
        plan=_lenient_model(clip_path, "edit_fx", FxPlan, payload.get("edit_fx")),
        face_track=_lenient_model(clip_path, "face_track", FaceTrack, payload.get("face_track")),
        fx_level=fx_level,
        applied_warp=applied_warp,
    )
