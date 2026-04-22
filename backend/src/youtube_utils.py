"""
Utility functions for YouTube-related operations.

Thin entry point: parsers live in ``_youtube_parsers``, filesystem/subprocess
helpers in ``_youtube_io``, and the yt-dlp option presets in
``_youtube_downloader``. This module keeps the public fetch/download entry
points and re-exports the helpers so existing ``patch("src.youtube_utils.*")``
tests keep working.
"""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
import yt_dlp

from .apify_youtube_downloader import ApifyDownloadError, download_video_via_apify
from .config import get_config
from ._youtube_downloader import YouTubeDownloader
from ._youtube_io import (
    _build_info_options,
    _empty_video_info,
    _get_local_video_dimensions,
    _remove_cached_downloads,
)
from ._youtube_parsers import (
    YOUTUBE_DATA_API_URL,
    YOUTUBE_METADATA_PROVIDER_DATA_API,
    YOUTUBE_METADATA_PROVIDER_YTDLP,
    _normalize_upload_date,
    _parse_iso8601_duration_to_seconds,
    _parse_optional_int,
    _pick_best_thumbnail,
    get_youtube_video_id,
    validate_youtube_url,
)

logger = logging.getLogger(__name__)


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
    """Backward-compatible metadata lookup entrypoint."""
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


def get_youtube_video_title(url: str) -> Optional[str]:
    """Get the title of a YouTube video from a URL."""
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
    """Download YouTube video with optimized settings and retry logic."""
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
    """Download YouTube video with Apify primary + yt-dlp fallback."""
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
    """Check if video is suitable for processing based on duration."""
    video_info = get_youtube_video_info(url)
    if not video_info:
        return False

    duration = video_info.get("duration", 0)

    if duration < min_duration or duration > max_duration:
        logger.warning(
            f"Video duration {duration}s outside allowed range ({min_duration}-{max_duration}s)"
        )
        return False

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


def extract_video_id(url: str) -> Optional[str]:
    """Backward compatibility wrapper."""
    return get_youtube_video_id(url)
