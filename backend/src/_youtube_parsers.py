"""YouTube URL + metadata parsers extracted from ``youtube_utils.py``.

Pure functions; no IO. ``youtube_utils`` re-exports ``get_youtube_video_id`` and
``validate_youtube_url`` so external callers keep their existing import paths.
"""

from datetime import datetime
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse
import logging
import re

logger = logging.getLogger(__name__)

YOUTUBE_METADATA_PROVIDER_YTDLP = "yt_dlp"
YOUTUBE_METADATA_PROVIDER_DATA_API = "youtube_data_api"
YOUTUBE_DATA_API_URL = "https://www.googleapis.com/youtube/v3/videos"


def _parse_iso8601_duration_to_seconds(value: str) -> int:
    match = re.fullmatch(
        r"P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+)S)?)?",
        value or "",
    )
    if not match:
        raise ValueError(f"Unsupported ISO 8601 duration: {value}")

    days = int(match.group("days") or 0)
    hours = int(match.group("hours") or 0)
    minutes = int(match.group("minutes") or 0)
    seconds = int(match.group("seconds") or 0)
    return (((days * 24) + hours) * 60 + minutes) * 60 + seconds


def _pick_best_thumbnail(thumbnails: Optional[Dict[str, Any]]) -> Optional[str]:
    if not thumbnails:
        return None

    for key in ("maxres", "standard", "high", "medium", "default"):
        candidate = thumbnails.get(key)
        if isinstance(candidate, dict) and candidate.get("url"):
            return candidate["url"]

    for candidate in thumbnails.values():
        if isinstance(candidate, dict) and candidate.get("url"):
            return candidate["url"]

    return None


def _normalize_upload_date(published_at: Optional[str]) -> Optional[str]:
    if not published_at:
        return None

    try:
        return (
            datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            .strftime("%Y%m%d")
        )
    except ValueError:
        logger.warning("Could not parse YouTube publishedAt value: %s", published_at)
        return None


def _parse_optional_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def get_youtube_video_id(url: str) -> Optional[str]:
    """
    Extract YouTube video ID from various URL formats.
    Supports standard, short, embed, and mobile URLs.
    """
    if not isinstance(url, str) or not url.strip():
        return None

    url = url.strip()

    patterns = [
        r"(?:youtube\.com/(?:.*v=|v/|embed/|shorts/)|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
        r"youtube\.com/embed/([A-Za-z0-9_-]{11})",
        r"youtube\.com/v/([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"youtube\.com/shorts/([A-Za-z0-9_-]{11})",
        r"m\.youtube\.com/watch\?v=([A-Za-z0-9_-]{11})",
    ]

    for pattern in patterns:
        match = re.search(pattern, url, re.IGNORECASE)
        if match:
            video_id = match.group(1)
            if len(video_id) == 11:
                return video_id

    try:
        parsed_url = urlparse(url)
        if "youtube.com" in parsed_url.netloc.lower():
            query = parse_qs(parsed_url.query)
            video_ids = query.get("v")
            if video_ids and len(video_ids[0]) == 11:
                return video_ids[0]
    except Exception as e:
        logger.warning(f"Error parsing URL query parameters: {e}")

    return None


def validate_youtube_url(url: str) -> bool:
    """Validate if URL is a proper YouTube URL."""
    video_id = get_youtube_video_id(url)
    return video_id is not None
