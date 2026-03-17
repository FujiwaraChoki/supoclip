"""
Utility functions for YouTube-related operations.
Optimized for Apify-first downloads with direct yt-dlp fallback.
"""

import asyncio
from datetime import datetime
import logging
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import parse_qs, urlparse

import requests
import yt_dlp

from .apify_youtube_downloader import ApifyDownloadError, download_video_via_apify
from .config import get_config

logger = logging.getLogger(__name__)

YOUTUBE_METADATA_PROVIDER_YTDLP = "yt_dlp"
YOUTUBE_METADATA_PROVIDER_DATA_API = "youtube_data_api"
YOUTUBE_DATA_API_URL = "https://www.googleapis.com/youtube/v3/videos"


class YouTubeDownloader:
    """Enhanced YouTube downloader with optimized settings."""

    def __init__(self):
        self.temp_dir = Path(get_config().temp_dir)
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def get_optimal_download_options(
        self,
        video_id: str,
    ) -> Dict[str, Any]:
        """Get optimal yt-dlp options for high-quality downloads."""
        output_path = self.temp_dir / f"{video_id}.%(ext)s"

        opts = {
            "outtmpl": str(output_path),
            # Use best available video/audio to avoid quality caps from container constraints.
            "format": "bestvideo*+bestaudio/best",
            "format_sort": ["res", "fps"],
            "merge_output_format": "mp4",
            "writesubtitles": False,
            "writeautomaticsub": False,
            "noplaylist": True,
            "overwrites": True,
            # Optimized for speed and reliability
            "socket_timeout": 30,
            "retries": 5,  # Increased retries
            "fragment_retries": 5,
            "http_chunk_size": 10485760,  # 10MB chunks
            # Quiet operation - only errors/warnings
            "quiet": True,
            "no_warnings": False,  # Show warnings but not info
            "ignoreerrors": False,
            # Enhanced headers to avoid 403 errors
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            },
            # Metadata extraction
            "extract_flat": False,
            "writeinfojson": False,
            # Additional bypass options
            "nocheckcertificate": True,
            "prefer_insecure": False,
            "age_limit": None,
        }

        return opts


def _build_info_options() -> Dict[str, Any]:
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extractaudio": False,
        "skip_download": True,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Connection": "keep-alive",
        },
        "nocheckcertificate": True,
    }
    return ydl_opts


def _empty_video_info(video_id: Optional[str] = None) -> Dict[str, Any]:
    return {
        "id": video_id,
        "title": None,
        "description": "",
        "duration": None,
        "uploader": None,
        "upload_date": None,
        "view_count": None,
        "like_count": None,
        "thumbnail": None,
        "format_id": None,
        "resolution": None,
        "fps": None,
        "filesize": None,
    }


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


def _get_local_video_dimensions(path: Path) -> tuple[int, int]:
    """Return local video width/height using ffprobe."""
    try:
        command = [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(path),
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        output = result.stdout.strip()
        if not output or "x" not in output:
            return (0, 0)
        width_str, height_str = output.split("x", 1)
        return (int(width_str), int(height_str))
    except Exception:
        return (0, 0)


def _remove_cached_downloads(temp_dir: Path, video_id: str) -> None:
    cached_files = [
        file_path
        for file_path in temp_dir.glob(f"{video_id}.*")
        if file_path.is_file()
        and file_path.suffix.lower() in [".mp4", ".mkv", ".webm", ".mov", ".m4v"]
    ]
    if not cached_files:
        return

    logger.info(
        "Refreshing download for %s (found %s cached file(s))",
        video_id,
        len(cached_files),
    )
    for cached_file in cached_files:
        try:
            cached_file.unlink()
        except Exception as exc:
            logger.warning("Failed to remove stale cache file %s: %s", cached_file, exc)


def get_youtube_video_id(url: str) -> Optional[str]:
    """
    Extract YouTube video ID from various URL formats.
    Supports standard, short, embed, and mobile URLs.
    """
    if not isinstance(url, str) or not url.strip():
        return None

    url = url.strip()

    # Comprehensive regex patterns for different YouTube URL formats
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
            # Validate video ID length (YouTube IDs are always 11 characters)
            if len(video_id) == 11:
                return video_id

    # Fallback: parse query parameters
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


def _fetch_video_info_with_ytdlp(url: str) -> Dict[str, Any]:
    video_id = get_youtube_video_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")

    ydl_opts = _build_info_options()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    return {
        "id": info.get("id"),
        "title": info.get("title"),
        "description": info.get("description", ""),
        "duration": info.get("duration"),
        "uploader": info.get("uploader"),
        "upload_date": info.get("upload_date"),
        "view_count": info.get("view_count"),
        "like_count": info.get("like_count"),
        "thumbnail": info.get("thumbnail"),
        "format_id": info.get("format_id"),
        "resolution": info.get("resolution"),
        "fps": info.get("fps"),
        "filesize": info.get("filesize"),
    }


def _fetch_video_info_with_youtube_data_api(url: str) -> Dict[str, Any]:
    video_id = get_youtube_video_id(url)
    if not video_id:
        raise ValueError(f"Invalid YouTube URL: {url}")

    config = get_config()
    api_key = config.resolve_youtube_data_api_key()
    if not api_key:
        raise ValueError("Missing YOUTUBE_DATA_API_KEY and GOOGLE_API_KEY")

    response = requests.get(
        YOUTUBE_DATA_API_URL,
        params={
            "part": "snippet,contentDetails,statistics",
            "id": video_id,
            "key": api_key,
            "fields": (
                "items(id,"
                "snippet(title,description,channelTitle,publishedAt,"
                "thumbnails(default(url),medium(url),high(url),standard(url),maxres(url))),"
                "contentDetails(duration),"
                "statistics(viewCount,likeCount))"
            ),
        },
        timeout=(10, 30),
    )
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items") or []
    if not items:
        raise ValueError(f"No YouTube Data API results for video {video_id}")

    item = items[0]
    snippet = item.get("snippet") or {}
    content_details = item.get("contentDetails") or {}
    statistics = item.get("statistics") or {}
    normalized = _empty_video_info(item.get("id") or video_id)
    normalized.update(
        {
            "title": snippet.get("title"),
            "description": snippet.get("description", ""),
            "duration": _parse_iso8601_duration_to_seconds(
                content_details.get("duration", "")
            ),
            "uploader": snippet.get("channelTitle"),
            "upload_date": _normalize_upload_date(snippet.get("publishedAt")),
            "view_count": _parse_optional_int(statistics.get("viewCount")),
            "like_count": _parse_optional_int(statistics.get("likeCount")),
            "thumbnail": _pick_best_thumbnail(snippet.get("thumbnails")),
        }
    )
    return normalized


def fetch_video_info(url: str) -> Optional[Dict[str, Any]]:
    """
    Backward-compatible metadata lookup entrypoint.
    """
    return get_youtube_video_info(url)


async def async_get_youtube_video_info(
    url: str,
    task_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return await asyncio.to_thread(get_youtube_video_info, url, task_id)


def get_youtube_video_info(
    url: str,
    task_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    del task_id  # Reserved for future provider-specific tracing.

    video_id = get_youtube_video_id(url)
    if not video_id:
        logger.error("Invalid YouTube URL: %s", url)
        return None

    config = get_config()
    primary_provider = config.youtube_metadata_provider
    secondary_provider = (
        YOUTUBE_METADATA_PROVIDER_DATA_API
        if primary_provider == YOUTUBE_METADATA_PROVIDER_YTDLP
        else YOUTUBE_METADATA_PROVIDER_YTDLP
    )
    providers = [primary_provider, secondary_provider]
    last_error: Optional[Exception] = None

    for index, provider in enumerate(providers):
        try:
            if provider == YOUTUBE_METADATA_PROVIDER_DATA_API:
                video_info = _fetch_video_info_with_youtube_data_api(url)
            else:
                video_info = _fetch_video_info_with_ytdlp(url)

            if index == 0:
                logger.info(
                    "Fetched YouTube metadata for %s using primary provider %s",
                    video_id,
                    provider,
                )
            else:
                logger.info(
                    "Fetched YouTube metadata for %s using fallback provider %s",
                    video_id,
                    provider,
                )
            return video_info
        except Exception as exc:
            last_error = exc
            if index == 0:
                logger.warning(
                    "Primary YouTube metadata provider %s failed for %s: %s. Trying %s.",
                    provider,
                    video_id,
                    exc,
                    secondary_provider,
                )
            else:
                logger.warning(
                    "Fallback YouTube metadata provider %s failed for %s: %s",
                    provider,
                    video_id,
                    exc,
                )

    if last_error:
        logger.warning("YouTube video info fetch failed for %s: %s", video_id, last_error)
    return None


def get_youtube_video_title(
    url: str,
) -> Optional[str]:
    """
    Get the title of a YouTube video from a URL.
    Enhanced with better error handling and validation.
    """
    video_info = get_youtube_video_info(url)
    return video_info.get("title") if video_info else None


async def async_get_youtube_video_title(url: str) -> Optional[str]:
    video_info = await async_get_youtube_video_info(url)
    return video_info.get("title") if video_info else None


def download_youtube_video_with_apify(
    url: str,
    video_id: str,
) -> Path:
    config = get_config()
    downloader = YouTubeDownloader()
    logger.info(
        "Attempting Apify YouTube download for %s with quality %s",
        video_id,
        config.apify_youtube_default_quality,
    )
    return download_video_via_apify(
        url=url,
        video_id=video_id,
        temp_dir=downloader.temp_dir,
        api_token=config.apify_api_token,
        quality=config.apify_youtube_default_quality,
    )


def _download_youtube_video_with_ytdlp(
    url: str,
    max_retries: int = 3,
    task_id: Optional[str] = None,
) -> Optional[Path]:
    """
    Download YouTube video with optimized settings and retry logic.
    Returns the path to the downloaded file, or None if download fails.
    """
    logger.info(f"Starting YouTube download: {url}")

    video_id = get_youtube_video_id(url)
    if not video_id:
        logger.error(f"Could not extract video ID from URL: {url}")
        return None

    downloader = YouTubeDownloader()
    video_info = get_youtube_video_info(
        url,
        task_id=task_id,
    )
    if not video_info:
        logger.error(f"Could not retrieve video information for: {url}")
        return None

    logger.info(f"Video: '{video_info.get('title')}' ({video_info.get('duration')}s)")

    duration = video_info.get("duration", 0)
    if duration > 3600:
        logger.warning(f"Video duration ({duration}s) exceeds recommended limit")

    last_error: Optional[str] = None

    for attempt in range(max_retries):
        try:
            logger.info("Download attempt %s/%s", attempt + 1, max_retries)

            ydl_opts = downloader.get_optimal_download_options(video_id)

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            logger.info(f"Searching for downloaded file: {video_id}.*")
            downloaded_files = [
                file_path
                for file_path in downloader.temp_dir.glob(f"{video_id}.*")
                if file_path.is_file()
                and file_path.suffix.lower() in [".mp4", ".mkv", ".webm"]
            ]
            if downloaded_files:
                ranked_files = []
                for candidate in downloaded_files:
                    width, height = _get_local_video_dimensions(candidate)
                    ranked_files.append(
                        (
                            height,
                            width,
                            candidate.stat().st_size,
                            candidate,
                        )
                    )
                ranked_files.sort(reverse=True)
                best_downloaded_file = ranked_files[0][3]
                file_size = best_downloaded_file.stat().st_size
                width, height = _get_local_video_dimensions(best_downloaded_file)
                logger.info(
                    f"Download successful: {best_downloaded_file.name} ({file_size // 1024 // 1024}MB, {width}x{height})"
                )
                return best_downloaded_file

            logger.warning("No video file found after download attempt %s", attempt + 1)

        except yt_dlp.utils.DownloadError as e:
            last_error = str(e)
            logger.warning("Download attempt %s failed: %s", attempt + 1, e)
            if attempt < max_retries - 1:
                wait_time = 2**attempt
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error("All download attempts failed")

        except Exception as e:
            last_error = str(e)
            logger.error(
                "Unexpected error during download attempt %s: %s",
                attempt + 1,
                e,
            )
            if attempt < max_retries - 1:
                wait_time = 2**attempt
                logger.info(f"Retrying in {wait_time} seconds...")
                time.sleep(wait_time)
            else:
                logger.error("All download attempts failed")

    if last_error:
        logger.error("All download attempts failed for %s: %s", url, last_error)

    return None


def download_youtube_video(
    url: str,
    max_retries: int = 3,
    task_id: Optional[str] = None,
) -> Optional[Path]:
    """
    Download YouTube video with Apify as the primary provider and yt-dlp fallback.
    Returns the path to the downloaded file, or None if both providers fail.
    """
    logger.info("Starting YouTube download: %s", url)

    video_id = get_youtube_video_id(url)
    if not video_id:
        logger.error("Could not extract video ID from URL: %s", url)
        return None

    downloader = YouTubeDownloader()
    _remove_cached_downloads(downloader.temp_dir, video_id)

    config = get_config()
    if config.apify_api_token:
        try:
            downloaded_path = download_youtube_video_with_apify(url, video_id)
            file_size = downloaded_path.stat().st_size
            width, height = _get_local_video_dimensions(downloaded_path)
            logger.info(
                "Apify download successful: %s (%sMB, %sx%s)",
                downloaded_path.name,
                file_size // 1024 // 1024,
                width,
                height,
            )
            return downloaded_path
        except ApifyDownloadError as exc:
            logger.warning("Apify download failed for %s, falling back to yt-dlp: %s", url, exc)
        except Exception as exc:
            logger.error(
                "Unexpected Apify download error for %s, falling back to yt-dlp: %s",
                url,
                exc,
            )
    else:
        logger.info("APIFY_API_TOKEN not set; using yt-dlp fallback for %s", url)

    return _download_youtube_video_with_ytdlp(url, max_retries, task_id)


async def async_download_youtube_video(
    url: str,
    max_retries: int = 3,
    task_id: Optional[str] = None,
) -> Optional[Path]:
    logger.info(f"Starting async YouTube download: {url}")
    return await asyncio.to_thread(download_youtube_video, url, max_retries, task_id)


def get_video_duration(url: str) -> Optional[int]:
    """Get video duration in seconds without downloading."""
    video_info = get_youtube_video_info(url)
    return video_info.get("duration") if video_info else None


def is_video_suitable_for_processing(
    url: str, min_duration: int = 60, max_duration: int = 7200
) -> bool:
    """
    Check if video is suitable for processing based on duration and other factors.
    Default limits: 1 minute to 2 hours.
    """
    video_info = get_youtube_video_info(url)
    if not video_info:
        return False

    duration = video_info.get("duration", 0)

    # Check duration constraints
    if duration < min_duration or duration > max_duration:
        logger.warning(
            f"Video duration {duration}s outside allowed range ({min_duration}-{max_duration}s)"
        )
        return False

    # Additional checks could go here (e.g., content type, quality, etc.)

    return True


def cleanup_downloaded_files(video_id: str):
    """Clean up downloaded files for a specific video ID."""
    temp_dir = Path(get_config().temp_dir)

    for file_path in temp_dir.glob(f"{video_id}.*"):
        try:
            if file_path.is_file():
                file_path.unlink()
                logger.info(f"Cleaned up: {file_path.name}")
        except Exception as e:
            logger.warning(f"Failed to cleanup {file_path.name}: {e}")


# Backward compatibility functions
def extract_video_id(url: str) -> Optional[str]:
    """Backward compatibility wrapper."""
    return get_youtube_video_id(url)
