"""
Helpers for clip cleanup settings and normalization.
"""

from __future__ import annotations

from typing import Any

DEFAULT_PAUSE_THRESHOLD_MS = 900
DEFAULT_FILTERED_WORDS = [
    "um",
    "uh",
    "erm",
    "hmm",
    "mm",
    "you know",
    "i mean",
    "sort of",
    "kind of",
]


def normalize_pause_threshold_ms(
    value: Any, default: int = DEFAULT_PAUSE_THRESHOLD_MS
) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(250, min(3000, parsed))


def normalize_filtered_words(value: Any) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        if not isinstance(item, str):
            continue
        cleaned = " ".join(item.strip().lower().split())
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        normalized.append(cleaned)
    return normalized


def normalize_clip_cleanup_settings(
    cut_long_pauses: Any = False,
    pause_threshold_ms: Any = DEFAULT_PAUSE_THRESHOLD_MS,
    remove_filler_words: Any = False,
    filtered_words: Any = None,
) -> dict[str, Any]:
    return {
        "cut_long_pauses": bool(cut_long_pauses),
        "pause_threshold_ms": normalize_pause_threshold_ms(pause_threshold_ms),
        "remove_filler_words": bool(remove_filler_words),
        "filtered_words": normalize_filtered_words(filtered_words),
    }


def clip_cleanup_enabled(settings: dict[str, Any] | None) -> bool:
    if not settings:
        return False
    return bool(
        settings.get("cut_long_pauses")
        or settings.get("remove_filler_words")
        or settings.get("filtered_words")
    )
