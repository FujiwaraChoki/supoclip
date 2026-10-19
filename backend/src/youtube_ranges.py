"""Validate URL time ranges independently of download and transcription providers."""

import re
from urllib.parse import parse_qs, urlparse


def _timestamp(value: str) -> int:
    if len(value) > 32:
        raise ValueError("YouTube timestamp is too large")
    if re.fullmatch(r"[0-9]+", value):
        seconds = int(value)
    else:
        match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", value)
        if not match or not any(match.groups()):
            raise ValueError("Use seconds or a YouTube timestamp such as 1h2m3s")
        hours, minutes, secs = (int(part or 0) for part in match.groups())
        seconds = hours * 3600 + minutes * 60 + secs
    if seconds > 2**31 - 1:
        raise ValueError("YouTube timestamp is too large")
    return seconds


def parse_youtube_range(url: str) -> tuple[int, int | None] | None:
    parsed = urlparse(url)
    params = parse_qs(parsed.query, keep_blank_values=True)
    fragment = parse_qs(parsed.fragment, keep_blank_values=True)
    for key in ("t", "start", "end"):
        if key not in params and key in fragment:
            params[key] = fragment[key]
        if key in params and len(params[key]) != 1:
            raise ValueError(f"Specify {key} only once")
    values = {key: _timestamp(params[key][0]) for key in ("t", "start", "end") if key in params}
    start = values.get("start", values.get("t", 0))
    end = values.get("end")
    if end is not None and end <= start:
        raise ValueError("The end of the range must be after its start")
    return (start, end) if start or end is not None else None


def youtube_range_duration(url: str, duration: float) -> float:
    bounds = parse_youtube_range(url)
    if bounds is None:
        return duration
    start, end = bounds
    if start >= duration:
        raise ValueError("The range must start before the end of the video")
    return min(end, duration) - start if end is not None else duration - start


def youtube_cache_key(url: str, video_id: str) -> str:
    bounds = parse_youtube_range(url)
    if bounds is None:
        return video_id
    start, end = bounds
    return f"{video_id}__range_{start}_{end if end is not None else 'end'}"
