"""Validation and prompt formatting for user-directed clip generation."""

from __future__ import annotations

from typing import Any


DEFAULT_CLIP_COUNT = 4
MIN_CLIP_SECONDS = 15
MAX_CLIP_SECONDS = 60
MAX_PROMPT_CHARS = 2000
MAX_KEYWORDS = 20


def _bounded_int(value: Any, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _timestamp_seconds(value: Any) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return max(0, int(value))

    normalized = str(value).strip()
    if not normalized:
        return None
    if normalized.isdigit():
        return max(0, int(normalized))

    parts = normalized.split(":")
    if len(parts) not in {2, 3} or not all(part.isdigit() for part in parts):
        return None
    numbers = [int(part) for part in parts]
    if len(numbers) == 2:
        minutes, seconds = numbers
        if seconds >= 60:
            return None
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    if minutes >= 60 or seconds >= 60:
        return None
    return hours * 3600 + minutes * 60 + seconds


def normalize_generation_preferences(value: Any) -> dict[str, Any]:
    raw = value if isinstance(value, dict) else {}
    prompt = str(raw.get("prompt") or "").strip()[:MAX_PROMPT_CHARS]

    raw_keywords = raw.get("keywords") or []
    if isinstance(raw_keywords, str):
        raw_keywords = raw_keywords.split(",")
    keywords: list[str] = []
    if isinstance(raw_keywords, list):
        seen = set()
        for item in raw_keywords:
            keyword = str(item).strip()[:80]
            normalized_keyword = keyword.casefold()
            if keyword and normalized_keyword not in seen:
                keywords.append(keyword)
                seen.add(normalized_keyword)
            if len(keywords) >= MAX_KEYWORDS:
                break

    clip_count = _bounded_int(
        raw.get("clip_count"), DEFAULT_CLIP_COUNT, 1, 10
    )
    clip_min_seconds = _bounded_int(
        raw.get("clip_min_seconds"), 25, MIN_CLIP_SECONDS, MAX_CLIP_SECONDS
    )
    clip_max_seconds = _bounded_int(
        raw.get("clip_max_seconds"), 50, clip_min_seconds, MAX_CLIP_SECONDS
    )

    timeframe_start_seconds = _timestamp_seconds(raw.get("timeframe_start"))
    if timeframe_start_seconds is None:
        timeframe_start_seconds = _timestamp_seconds(
            raw.get("timeframe_start_seconds")
        )
    timeframe_end_seconds = _timestamp_seconds(raw.get("timeframe_end"))
    if timeframe_end_seconds is None:
        timeframe_end_seconds = _timestamp_seconds(raw.get("timeframe_end_seconds"))
    if (
        timeframe_end_seconds is not None
        and timeframe_end_seconds
        < (timeframe_start_seconds or 0) + MIN_CLIP_SECONDS
    ):
        timeframe_end_seconds = None

    analysis_mode = str(raw.get("analysis_mode") or "transcript").strip().lower()
    if analysis_mode not in {"transcript", "multimodal"}:
        analysis_mode = "transcript"

    return {
        "prompt": prompt,
        "keywords": keywords,
        "clip_count": clip_count,
        "clip_min_seconds": clip_min_seconds,
        "clip_max_seconds": clip_max_seconds,
        "timeframe_start_seconds": timeframe_start_seconds,
        "timeframe_end_seconds": timeframe_end_seconds,
        "analysis_mode": analysis_mode,
    }


def generation_preferences_prompt(preferences: dict[str, Any] | None) -> str:
    normalized = normalize_generation_preferences(preferences)
    lines = [
        "User clip brief (follow it when it does not conflict with transcript accuracy):",
        f"- Return up to {normalized['clip_count']} strong clips.",
        (
            "- Target durations between "
            f"{normalized['clip_min_seconds']} and {normalized['clip_max_seconds']} seconds."
        ),
    ]
    if normalized["prompt"]:
        lines.append(f"- Creative brief: {normalized['prompt']}")
    if normalized["keywords"]:
        lines.append(f"- Prioritize these topics: {', '.join(normalized['keywords'])}")
    if normalized["timeframe_start_seconds"] is not None:
        lines.append(
            "- Do not start a clip before source second "
            f"{normalized['timeframe_start_seconds']}."
        )
    if normalized["timeframe_end_seconds"] is not None:
        lines.append(
            "- Do not end a clip after source second "
            f"{normalized['timeframe_end_seconds']}."
        )
    return "\n".join(lines)
