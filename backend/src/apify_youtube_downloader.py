"""
Apify-backed helpers for downloading YouTube videos.
"""

from __future__ import annotations

import logging
import mimetypes
import re
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse

import requests

try:
    from apify_client import ApifyClient
except ImportError:  # pragma: no cover - exercised through fallback behavior
    ApifyClient = None

from .config import get_config

logger = logging.getLogger(__name__)

APIFY_YOUTUBE_DOWNLOADER_ACTOR = "epctex/youtube-video-downloader"
ALLOWED_APIFY_QUALITIES = {"360", "480", "720", "1080"}


class ApifyDownloadError(RuntimeError):
    """Raised when the Apify YouTube download flow cannot produce a local file."""


def normalize_apify_quality(value: Optional[str]) -> str:
    normalized = (value or "").strip()
    if normalized in ALLOWED_APIFY_QUALITIES:
        return normalized
    return "1080"


def _extract_download_url(payload: Any) -> Optional[str]:
    if isinstance(payload, dict):
        direct_value = payload.get("downloadUrl")
        if isinstance(direct_value, str) and direct_value.startswith(("http://", "https://")):
            return direct_value

        for key, value in payload.items():
            if (
                "download" in key.lower()
                and isinstance(value, str)
                and value.startswith(("http://", "https://"))
            ):
                return value

        for value in payload.values():
            resolved = _extract_download_url(value)
            if resolved:
                return resolved

    if isinstance(payload, list):
        for item in payload:
            resolved = _extract_download_url(item)
            if resolved:
                return resolved

    return None


def _infer_file_extension(response: requests.Response, download_url: str) -> str:
    disposition = response.headers.get("Content-Disposition", "")
    filename_match = re.search(
        r"""filename\*?=(?:UTF-8''|")?(?P<name>[^";]+)""",
        disposition,
        flags=re.IGNORECASE,
    )
    if filename_match:
        filename = unquote(filename_match.group("name")).strip().strip('"')
        suffix = Path(filename).suffix.lower()
        if suffix:
            return suffix

    content_type = response.headers.get("Content-Type", "").split(";", 1)[0].strip()
    if content_type:
        guessed_extension = mimetypes.guess_extension(content_type)
        if guessed_extension:
            return guessed_extension

    path_suffix = Path(urlparse(download_url).path).suffix.lower()
    if path_suffix:
        return path_suffix

    return ".mp4"


def _download_file(download_url: str, destination_stem: Path) -> Path:
    response = requests.get(download_url, stream=True, timeout=(15, 120))
    response.raise_for_status()

    extension = _infer_file_extension(response, download_url)
    destination = destination_stem.with_suffix(extension)
    partial_destination = destination.with_suffix(f"{extension}.part")

    try:
        if partial_destination.exists():
            partial_destination.unlink()
        if destination.exists():
            destination.unlink()

        with partial_destination.open("wb") as file_handle:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    file_handle.write(chunk)

        partial_destination.replace(destination)
        return destination
    except Exception:
        if partial_destination.exists():
            partial_destination.unlink()
        raise


def download_video_via_apify(
    url: str,
    video_id: str,
    temp_dir: Path,
    api_token: Optional[str] = None,
    quality: Optional[str] = None,
) -> Path:
    config = get_config()
    resolved_token = api_token or config.apify_api_token
    if not resolved_token:
        raise ApifyDownloadError("Missing APIFY_API_TOKEN")

    if ApifyClient is None:
        raise ApifyDownloadError("apify-client is not installed")

    resolved_quality = normalize_apify_quality(
        quality or config.apify_youtube_default_quality
    )
    temp_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Starting Apify YouTube download for %s with target quality %s",
        video_id,
        resolved_quality,
    )

    try:
        client = ApifyClient(resolved_token)
        run = client.actor(APIFY_YOUTUBE_DOWNLOADER_ACTOR).call(
            run_input={
                "startUrls": [url],
                "quality": resolved_quality,
                "proxy": {"useApifyProxy": True},
            }
        )
        dataset_id = run.get("defaultDatasetId")
        if not dataset_id:
            raise ApifyDownloadError("Apify run did not return a dataset ID")

        item = next(client.dataset(dataset_id).iterate_items(), None)
        if not item:
            raise ApifyDownloadError("Apify run returned no dataset items")

        download_url = _extract_download_url(item)
        if not download_url:
            raise ApifyDownloadError("Apify result did not contain a download URL")

        return _download_file(download_url, temp_dir / video_id)
    except ApifyDownloadError:
        raise
    except Exception as exc:
        raise ApifyDownloadError(f"Apify download failed: {exc}") from exc
