"""Task-specific clip length presets shared by selection and rendering."""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ClipDuration:
    minimum: int
    maximum: int
    ideal_minimum: int
    ideal_maximum: int


CLIP_DURATIONS = {
    "auto": ClipDuration(15, 60, 25, 50),
    # Ideal maxima leave room for the sentence rewind/extension before the cap.
    "short": ClipDuration(15, 30, 18, 25),
    "medium": ClipDuration(30, 60, 35, 55),
    "long": ClipDuration(60, 120, 75, 110),
}


def validate_clip_length(value: Any) -> str:
    if not isinstance(value, str) or value not in CLIP_DURATIONS:
        raise ValueError("clip_length must be auto, short, medium, or long")
    return value


def normalize_clip_length(value: Any) -> str:
    """Stored task settings fall back to Auto instead of failing the job."""
    return value if isinstance(value, str) and value in CLIP_DURATIONS else "auto"


def clip_duration(value: str = "auto") -> ClipDuration:
    return CLIP_DURATIONS[validate_clip_length(value)]


MIN_CLIP_COUNT = 1
MAX_CLIP_COUNT = 20


def validate_clip_count(value: Any) -> "str | int":
    """'auto' or a whole number from 1 to 20."""
    if value == "auto":
        return "auto"
    if isinstance(value, int) and not isinstance(value, bool) and MIN_CLIP_COUNT <= value <= MAX_CLIP_COUNT:
        return value
    raise ValueError(f"clip_count must be auto or a number from {MIN_CLIP_COUNT} to {MAX_CLIP_COUNT}")


def normalize_clip_count(value: Any) -> "str | int":
    """Stored task settings fall back to Auto instead of failing the job."""
    try:
        return validate_clip_count(value)
    except ValueError:
        return "auto"


def normalize_clip_instructions(value: Any) -> "str | None":
    if not isinstance(value, str):
        return None
    # No length cap: creators can write as much guidance as they like. Line
    # breaks survive so long, structured instructions stay readable to the LLM.
    lines = (" ".join(line.split()) for line in value.splitlines())
    return "\n".join(line for line in lines if line) or None


def validate_clip_instructions(value: Any) -> "str | None":
    if value is not None and not isinstance(value, str):
        raise ValueError("clip_instructions must be text")
    return normalize_clip_instructions(value)
