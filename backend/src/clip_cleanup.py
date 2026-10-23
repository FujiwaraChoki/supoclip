"""
Helpers for clip cleanup settings and normalization.
"""

from __future__ import annotations

from typing import Any

# Pauses shorter than this stay in the cut. 700ms was eating ordinary breaths
# in a conversation (about a fifth of a talking-head clip) and the junctions
# sounded chopped. 1.2s still removes real dead air.
DEFAULT_PAUSE_THRESHOLD_MS = 1200
# New tasks clean up pacing unless the request explicitly opts out. Settings
# loaded for existing tasks keep treating a missing value as off.
NEW_TASK_CUT_LONG_PAUSES = True
NEW_TASK_REMOVE_FILLER_WORDS = True
# Pure disfluencies only: phrases like "kind of" or "you know" often carry
# meaning, so they are left to the user's own filtered_words list. "um" and
# "er" are English hesitations but ordinary words elsewhere (German "um" is a
# preposition, "er" means "he"), so English fillers only apply to English.
DEFAULT_FILTERED_WORDS = [
    "um",
    "umm",
    "uh",
    "uhh",
    "uhm",
    "erm",
    "hmm",
    "mm",
]
# Hesitation sounds that aren't ordinary words in any supported language.
LANGUAGE_NEUTRAL_FILTERED_WORDS = ["uh", "uhh", "uhm", "hmm", "mm"]
LANGUAGE_FILTERED_WORDS = {
    "en": DEFAULT_FILTERED_WORDS,
    "de": [*LANGUAGE_NEUTRAL_FILTERED_WORDS, "äh", "ähm", "öhm"],
    "fr": [*LANGUAGE_NEUTRAL_FILTERED_WORDS, "euh", "heu"],
}


def default_filtered_words(language_code: Any) -> list[str]:
    """Built-in filler words for a transcript language (e.g. "en_us", "de").

    Unknown languages, including transcripts cached before the language was
    recorded, get only the language-neutral hesitation sounds.
    """
    if isinstance(language_code, str) and language_code.strip():
        base = language_code.strip().lower().replace("-", "_").split("_", 1)[0]
        if base in LANGUAGE_FILTERED_WORDS:
            return list(LANGUAGE_FILTERED_WORDS[base])
    return list(LANGUAGE_NEUTRAL_FILTERED_WORDS)


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


def new_task_cleanup_settings(data: dict[str, Any]) -> dict[str, Any]:
    """Cleanup settings for a new task request.

    Omitted toggles (API key / MCP clients) get the new-task defaults; an
    explicit false still turns the cleanup off.
    """
    return normalize_clip_cleanup_settings(
        data.get("cut_long_pauses", NEW_TASK_CUT_LONG_PAUSES),
        data.get("pause_threshold_ms"),
        data.get("remove_filler_words", NEW_TASK_REMOVE_FILLER_WORDS),
        data.get("filtered_words"),
    )


def updated_cleanup_settings(
    payload: dict[str, Any], stored: dict[str, Any] | None
) -> dict[str, Any]:
    """Cleanup settings for a task update; omitted fields keep stored values."""
    stored = stored or {}

    def value(key: str) -> Any:
        return payload[key] if key in payload else stored.get(key)

    return normalize_clip_cleanup_settings(
        value("cut_long_pauses"),
        value("pause_threshold_ms"),
        value("remove_filler_words"),
        value("filtered_words"),
    )


def clip_cleanup_enabled(settings: dict[str, Any] | None) -> bool:
    if not settings:
        return False
    return bool(
        settings.get("cut_long_pauses")
        or settings.get("remove_filler_words")
        or settings.get("filtered_words")
    )
