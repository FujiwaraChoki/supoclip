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
    "short": ClipDuration(15, 30, 20, 30),
    "medium": ClipDuration(30, 60, 35, 55),
    "long": ClipDuration(60, 120, 75, 110),
}


def validate_clip_length(value: Any) -> str:
    if not isinstance(value, str) or value not in CLIP_DURATIONS:
        raise ValueError("clip_length must be auto, short, medium, or long")
    return value


def clip_duration(value: str = "auto") -> ClipDuration:
    return CLIP_DURATIONS[validate_clip_length(value)]
