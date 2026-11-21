"""Clip part of a long video: the source range a task processes.

A creator whose video is longer than the plan's per-video limit can pick one
part of it (``source.start_seconds`` / ``source.end_seconds`` on
``POST /tasks/``). The API validates the range against the real duration and
the plan limit, charges the range length only, and stores it in
``tasks.source_metadata["source_range"]``.

Timestamps stay on the original video's timeline everywhere outside the
analysis step. The worker transcribes and analyses an audio file of just the
range (with a ``.timeline.json`` that maps it back), then shifts the selected
segments by the range start. Clips, the editor, exports and source links all
see original-video seconds, so a clip that starts 1:02:10 into a stream says so.

A task without a range is untouched: no metadata key, and every cache key,
checkpoint key and duplicate-source key is byte-identical to before.
"""

from __future__ import annotations

import logging
import math
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

logger = logging.getLogger(__name__)

METADATA_KEY = "source_range"
# Shortest part worth clipping. Highlights need room for several 15-60 s clips.
MIN_SOURCE_RANGE_SECONDS = 60
# A client that sends the probed length as `end` may be a fraction past it.
END_TOLERANCE_SECONDS = 2.0
INVALID_SOURCE_RANGE = "INVALID_SOURCE_RANGE"
RANGE_WORKFLOWS = frozenset({"highlights"})


class SourceRangeError(ValueError):
    """A requested range the API rejects with 422 ``INVALID_SOURCE_RANGE``."""

    def __init__(self, message: str, **detail: Any):
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_detail(self) -> Dict[str, Any]:
        return {
            "code": INVALID_SOURCE_RANGE,
            "message": self.message,
            "min_range_seconds": MIN_SOURCE_RANGE_SECONDS,
            **self.detail,
        }


@dataclass(frozen=True)
class RequestedRange:
    """What the client asked for, before the duration is known."""

    start: Optional[float]
    end: Optional[float]

    def key(self) -> str:
        """Stable text for dedupe keys: two different parts are two jobs."""
        start = _whole(self.start or 0.0)
        end = "end" if self.end is None else str(_whole(self.end))
        return f"{start}-{end}"


@dataclass(frozen=True)
class SourceRange:
    """A validated part of the source, in whole seconds from its start."""

    start: int
    end: float
    source_duration: Optional[float] = None

    @property
    def length(self) -> float:
        return float(self.end) - float(self.start)

    def to_metadata(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"start_seconds": self.start, "end_seconds": self.end}
        if self.source_duration:
            payload["source_duration_seconds"] = self.source_duration
        return payload

    @classmethod
    def from_metadata(cls, metadata: Optional[Mapping[str, Any]]) -> Optional["SourceRange"]:
        raw = (metadata or {}).get(METADATA_KEY) if isinstance(metadata, Mapping) else None
        if not isinstance(raw, Mapping):
            return None
        try:
            start = int(raw["start_seconds"])
            end = float(raw["end_seconds"])
        except (KeyError, TypeError, ValueError):
            return None
        if start < 0 or end <= start:
            return None
        duration = raw.get("source_duration_seconds")
        try:
            duration = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration = None
        return cls(start, end, duration)

    def cache_suffix(self) -> str:
        """Appended to cache and checkpoint inputs; whole-video keys never carry it."""
        return f"|range:{self.start}-{self.end:.3f}"

    def time_map(self):
        from .youtube_source import build_time_map

        return build_time_map([(float(self.start), float(self.end))])


def _whole(value: float) -> int:
    return int(math.floor(float(value) + 0.5))


def _number(value: Any, name: str) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise SourceRangeError(f"{name} must be a number of seconds.")
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise SourceRangeError(f"{name} must be a number of seconds.") from None
    if not math.isfinite(number) or number < 0:
        raise SourceRangeError(f"{name} must be zero or more seconds.")
    return number


def parse_requested_range(source: Mapping[str, Any]) -> Optional[RequestedRange]:
    """Read ``start_seconds``/``end_seconds`` from the request's ``source``.

    Either may be omitted (start defaults to 0, end to the video's end).
    Checks that need the duration happen in ``resolve_source_range``.
    """
    start = _number(source.get("start_seconds"), "start_seconds")
    end = _number(source.get("end_seconds"), "end_seconds")
    if start is None and end is None:
        return None
    if start is not None and end is not None and end <= start:
        raise SourceRangeError("The end of the part must come after its start.")
    return RequestedRange(start, end)


def _minutes(seconds: float) -> str:
    return f"{seconds / 60:g}"


def resolve_source_range(
    requested: Optional[RequestedRange],
    duration: float,
    max_duration: Optional[float],
) -> Optional[SourceRange]:
    """Validate a requested part against the real duration and the plan limit.

    Returns None when nothing was requested or the part is the whole video
    (which then processes exactly like a request without a range).
    """
    if requested is None:
        return None
    detail: Dict[str, Any] = {"duration_seconds": duration}
    if max_duration is not None:
        detail["max_duration_seconds"] = max_duration
    start = _whole(requested.start or 0.0)
    end = float(duration) if requested.end is None else float(requested.end)
    if start >= duration:
        raise SourceRangeError(
            f"The part starts after the video ends ({_clock(duration)}). No minutes were used.", **detail,
        )
    if end > duration + END_TOLERANCE_SECONDS:
        raise SourceRangeError(
            f"The part ends after the video ends ({_clock(duration)}). No minutes were used.", **detail,
        )
    end = min(end, float(duration))
    if end > start + 1 and abs(end - round(end)) < 1e-6:
        end = float(round(end))
    length = end - start
    if length < MIN_SOURCE_RANGE_SECONDS and length < duration:
        raise SourceRangeError(
            f"Pick a part at least {MIN_SOURCE_RANGE_SECONDS // 60} minute long. No minutes were used.", **detail,
        )
    if max_duration is not None and max_duration > 0 and length > max_duration:
        raise SourceRangeError(
            f"This part is {length / 60:.1f} minutes long. Your plan allows up to "
            f"{_minutes(max_duration)} minutes per video. Pick a shorter part. No minutes were used.",
            **detail,
        )
    if start <= 0 and end >= duration - 0.5:
        return None
    return SourceRange(start, end, float(duration))


def _clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def video_too_long_detail(
    duration: float, max_duration: float, *, platform: str = "", upload: bool = False,
) -> Dict[str, Any]:
    """422 ``VIDEO_TOO_LONG`` for a whole video over the limit: the client offers a range."""
    noun = "This video" if not platform else f"This {platform} video"
    return {
        "code": "VIDEO_TOO_LONG",
        "message": (
            f"{noun} is {duration / 60:.1f} minutes long. Your plan allows "
            f"{'uploads' if upload else 'videos'} up to {_minutes(max_duration)} minutes. "
            f"Pick a part up to {_minutes(max_duration)} minutes to clip, or choose a shorter video. "
            "No minutes were used."
        ),
        "duration_seconds": duration,
        "max_duration_seconds": max_duration,
        "range_allowed": True,
        "min_range_seconds": MIN_SOURCE_RANGE_SECONDS,
    }


def workflow_supports_range(workflow: str) -> bool:
    return workflow in RANGE_WORKFLOWS


def shift_segment(segment: Dict[str, Any], offset: float) -> Dict[str, Any]:
    """Move one selected segment from the range's timeline onto the source's."""
    from .media.timeline import seconds_to_mmss
    from .youtube_source import parse_clock

    if not offset:
        return dict(segment)
    shifted = dict(segment)
    for key in ("start_time", "end_time", "hook_start_time", "hook_end_time"):
        parsed = parse_clock(segment.get(key))
        if parsed is None:
            continue
        if isinstance(segment.get(key), (int, float)) and not isinstance(segment.get(key), bool):
            shifted[key] = parsed + offset
        else:
            shifted[key] = seconds_to_mmss(parsed + offset)
    for key in ("keep_ranges", "source_ranges"):
        ranges = segment.get(key)
        if ranges:
            shifted[key] = [[float(start) + offset, float(end) + offset] for start, end in ranges]
    return shifted


def cut_audio_range(source: Path, dest: Path, source_range: SourceRange, *, offset: float = 0.0) -> Path:
    """Write the range's audio to ``dest`` and a timeline that maps it back.

    ``offset`` is where ``source`` starts on the original timeline (a section
    file that begins before the range). Audio is re-encoded so the cut is
    sample-accurate: the transcript's word times must line up with the range.
    """
    from .youtube_source import save_time_map

    dest.parent.mkdir(parents=True, exist_ok=True)
    seek = max(0.0, float(source_range.start) - offset)
    command = [
        "ffmpeg", "-y", "-nostdin", "-v", "error",
        "-ss", f"{seek:.3f}", "-i", str(source),
        "-t", f"{source_range.length:.3f}",
        "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-c:a", "aac", "-b:a", "96k", "-ac", "1",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size <= 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError("Could not cut the selected part of the video: " + (result.stderr or "")[-300:])
    save_time_map(dest, source_range.time_map())
    return dest


def original_transcript(payload: Dict[str, Any], source_range: SourceRange) -> Dict[str, Any]:
    """Word timings of the range's transcript, moved onto the source's timeline."""
    from .youtube_source import unshift_transcript

    return unshift_transcript(payload, source_range.time_map())


def media_duration(task: Mapping[str, Any], metadata: Optional[Mapping[str, Any]]) -> Optional[float]:
    """Length of the whole source. ``source_duration_seconds`` is the charged part for a range."""
    source_range = SourceRange.from_metadata(metadata)
    if source_range is not None:
        return source_range.source_duration
    return task.get("source_duration_seconds")


def public_range_fields(metadata: Optional[Mapping[str, Any]]) -> Dict[str, Any]:
    source_range = SourceRange.from_metadata(metadata)
    return {
        "source_start_seconds": source_range.start if source_range else None,
        "source_end_seconds": source_range.end if source_range else None,
    }



async def range_transcript(db, task_id: str, metadata: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """A picked part's word timings on the source's timeline, or None.

    A part's transcript is never stored beside a shared download, so a file
    fetched later for editing has none. The task's own checkpoint keeps it.
    """
    from sqlalchemy import text

    source_range = SourceRange.from_metadata(metadata)
    if source_range is None:
        return None
    result = await db.execute(
        text("SELECT payload->'transcript_data' FROM task_processing_checkpoints WHERE task_id = :task_id"),
        {"task_id": task_id},
    )
    payload = result.scalar_one_or_none()
    if not isinstance(payload, dict) or not payload.get("words"):
        return None
    return original_transcript(payload, source_range)


def transcript_on_media(original: Dict[str, Any], media_path: Path) -> Dict[str, Any]:
    """Source-timeline word timings moved onto a (possibly stitched) file's own timeline."""
    from .youtube_source import load_time_map, shift_transcript

    time_map = load_time_map(Path(media_path))
    return shift_transcript(original, time_map) if time_map is not None else original


def private_link(source: Path, directory: Path) -> Path:
    """This job's own name for a shared file, so its sidecars stay this job's.

    A hard link when the file system allows one (the bytes are not copied),
    else a symbolic link. Writing beside a cached download or a shared upload
    would hand one part's word timings to every later job of the whole video.
    """
    import os

    directory.mkdir(parents=True, exist_ok=True)
    link = directory / f"render{Path(source).suffix or '.mp4'}"
    link.unlink(missing_ok=True)
    try:
        os.link(source, link)
    except OSError:
        os.symlink(Path(source).resolve(), link)
    return link


EDIT_LINK_PREFIX = "edit-"


def link_with_part_words(media_path: Path, original: Dict[str, Any], root: Path) -> Path:
    """A private link to ``media_path`` carrying a part's word timings.

    Its timeline sidecar is copied too, so the timings land on the file's own
    (possibly stitched) timeline. ``release_part_link`` removes it.
    """
    import json
    import shutil
    import uuid

    from .youtube_source import timeline_path

    link = private_link(Path(media_path), Path(root) / f"{EDIT_LINK_PREFIX}{uuid.uuid4().hex[:12]}")
    timeline = timeline_path(Path(media_path))
    if timeline.exists():
        shutil.copyfile(timeline, timeline_path(link))
    payload = transcript_on_media(original, Path(media_path))
    link.with_suffix(".transcript_cache.json").write_text(json.dumps(payload), encoding="utf-8")
    return link


def release_part_link(path: Optional[Path], root: Path) -> None:
    """Remove a link made by ``link_with_part_words``; any other path is left alone."""
    import shutil

    if path is None:
        return
    parent = Path(path).parent
    if parent.parent == Path(root) and parent.name.startswith(EDIT_LINK_PREFIX):
        shutil.rmtree(parent, ignore_errors=True)
