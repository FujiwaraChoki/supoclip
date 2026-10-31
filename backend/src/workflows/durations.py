"""Lengths, counts and source minimums for every workflow.

All lengths are seconds of speech after cleanup, as estimated by
``UnitIndex.speech_seconds``. The task's ``clip_length`` chip means part length
for a series, total length for a recap or trailer, and per-clip length for
highlights and topic clips.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from ..clip_duration import CLIP_DURATIONS, validate_clip_length

SERIES_MIN_PARTS = 2
SERIES_MAX_PARTS = 10
# An explicit part count needs this much source per part minimum: the story
# rarely fills the whole video (intros, sponsor reads, outros).
SERIES_SOURCE_HEADROOM = 1.25
# Render safety cap for any planned segment, whatever its budget says.
WORKFLOW_RENDER_HARD_MAX_SECONDS = 185.0
# Planned clips may run this much past their budget maximum before the render
# cap trims them. Planners never rely on it.
RENDER_CAP_PADDING_SECONDS = 5.0

# Shortest source (seconds) each workflow accepts, checked at the API before
# charging and again in the worker when the API had no duration.
MIN_SOURCE_SECONDS: dict[str, int] = {
    "highlights": 0,
    "series": 120,
    "topic": 60,
    "recap": 180,
    "trailer": 180,
}


@dataclass(frozen=True)
class Budget:
    """A length window in seconds of speech.

    ``min``/``max`` are hard limits, ``ideal_min``/``ideal_max`` the range a
    planner aims for, and ``render_cap`` the renderer's safety cap. Recap and
    trailer budgets have a ``target`` total; ``shortened`` marks a recap target
    reduced to fit a short source (plan note TARGET_SHORTENED).
    """

    min: float
    ideal_min: float
    ideal_max: float
    max: float
    render_cap: float
    target: Optional[float] = None
    shortened: bool = False

    @property
    def ideal(self) -> float:
        """The single length to grow toward: the target, else the ideal midpoint."""
        if self.target is not None:
            return self.target
        return round((self.ideal_min + self.ideal_max) / 2, 1)

    @property
    def maximum(self) -> float:
        return self.max

    @property
    def minimum(self) -> float:
        return self.min

    def contains(self, seconds: float) -> bool:
        return self.min <= seconds <= self.max


# clip_length -> (min, ideal_min, ideal_max, max, render_cap)
_SERIES_PARTS: dict[str, tuple[float, float, float, float, float]] = {
    "auto": (45, 60, 100, 150, 155),
    "short": (30, 35, 50, 60, 65),
    "medium": (45, 55, 80, 90, 95),
    "long": (90, 100, 150, 175, 180),
}
_RECAP_TARGETS: dict[str, float] = {"short": 45, "medium": 75, "long": 120}
# Auto recap target by source length: (source seconds up to, target).
_RECAP_AUTO_TARGETS: tuple[tuple[float, float], ...] = ((600, 45), (1800, 60), (5400, 90))
_RECAP_AUTO_LONGEST = 120.0
_TRAILER_TARGETS: dict[str, float] = {"auto": 30, "short": 20, "medium": 40, "long": 60}


def _rounded(value: float) -> float:
    return round(value, 1)


def part_budget(clip_length: str) -> Budget:
    """Length of one series part."""
    low, ideal_low, ideal_high, high, cap = _SERIES_PARTS[validate_clip_length(clip_length)]
    return Budget(min=low, ideal_min=ideal_low, ideal_max=ideal_high, max=high, render_cap=cap)


def recap_target(clip_length: str, source_seconds: float) -> tuple[float, bool]:
    """Recap total target and whether it was shortened to fit a short source."""
    clip_length = validate_clip_length(clip_length)
    if clip_length == "auto":
        target = next(
            (value for limit, value in _RECAP_AUTO_TARGETS if source_seconds <= limit),
            _RECAP_AUTO_LONGEST,
        )
    else:
        target = _RECAP_TARGETS[clip_length]
    if source_seconds > 0 and target > 0.6 * source_seconds:
        return float(max(30, round(0.4 * source_seconds))), True
    return float(target), False


def recap_budget(clip_length: str, source_seconds: float) -> Budget:
    """Total length of the one recap clip: window [0.85·T, 1.15·T]."""
    target, shortened = recap_target(clip_length, source_seconds)
    return Budget(
        min=_rounded(0.85 * target),
        ideal_min=_rounded(0.95 * target),
        ideal_max=_rounded(1.05 * target),
        max=_rounded(1.15 * target),
        render_cap=_rounded(min(180.0, 1.15 * target + RENDER_CAP_PADDING_SECONDS)),
        target=target,
        shortened=shortened,
    )


def trailer_budget(clip_length: str) -> Budget:
    """Total length of the one trailer clip: window [0.8·T, 1.1·T]."""
    target = _TRAILER_TARGETS[validate_clip_length(clip_length)]
    return Budget(
        min=_rounded(0.8 * target),
        ideal_min=_rounded(0.9 * target),
        ideal_max=float(target),
        max=_rounded(1.1 * target),
        render_cap=_rounded(1.1 * target + RENDER_CAP_PADDING_SECONDS),
        target=float(target),
    )


def clip_budget(clip_length: str) -> Budget:
    """Length of one standalone clip (highlights, topic): the existing presets."""
    preset = CLIP_DURATIONS[validate_clip_length(clip_length)]
    return Budget(
        min=float(preset.minimum),
        ideal_min=float(preset.ideal_minimum),
        ideal_max=float(preset.ideal_maximum),
        max=float(preset.maximum),
        render_cap=float(preset.maximum) + RENDER_CAP_PADDING_SECONDS,
    )


def workflow_budget(workflow: str, clip_length: str, source_seconds: float = 0.0) -> Budget:
    """The per-output budget for a workflow. ``source_seconds`` matters for recap only."""
    if workflow == "series":
        return part_budget(clip_length)
    if workflow == "recap":
        return recap_budget(clip_length, source_seconds)
    if workflow == "trailer":
        return trailer_budget(clip_length)
    if workflow in ("highlights", "topic"):
        return clip_budget(clip_length)
    raise ValueError(f"Unknown workflow: {workflow}")


def max_series_parts(source_seconds: float, clip_length: str) -> int:
    """Most parts an explicit count may ask for on a source this long (may be < 2)."""
    per_part = part_budget(clip_length).min * SERIES_SOURCE_HEADROOM
    return min(SERIES_MAX_PARTS, math.floor(source_seconds / per_part))


def series_lengths() -> dict[str, list[float]]:
    """``GET /tasks/workflows``: part [min, max] per clip length."""
    return {length: [values[0], values[3]] for length, values in _SERIES_PARTS.items()}


def recap_lengths() -> dict[str, Optional[float]]:
    """``GET /tasks/workflows``: recap target per clip length (auto scales, so null)."""
    return {"auto": None, **{length: target for length, target in _RECAP_TARGETS.items()}}


def trailer_lengths() -> dict[str, float]:
    """``GET /tasks/workflows``: trailer target per clip length."""
    return dict(_TRAILER_TARGETS)
