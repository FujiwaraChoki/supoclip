"""Downloads from explicitly supported third-party video hosts."""

from __future__ import annotations

import asyncio
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import yt_dlp

from .config import get_config


SUPPORTED_EXTERNAL_HOSTS = {
    "vimeo.com", "player.vimeo.com", "twitch.tv", "www.twitch.tv", "clips.twitch.tv",
    "drive.google.com", "docs.google.com", "dropbox.com", "www.dropbox.com",
    "loom.com", "www.loom.com", "zoom.us", "streamyard.com", "www.streamyard.com",
}


def is_supported_external_url(url: str) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and any(host == allowed or host.endswith(f".{allowed}") for allowed in SUPPORTED_EXTERNAL_HOSTS)


def download_external_video(url: str, task_id: str | None = None) -> Path | None:
    if not is_supported_external_url(url):
        return None
    output_dir = Path(get_config().temp_dir) / "external"
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = task_id or uuid4().hex
    options = {
        "outtmpl": str(output_dir / f"{stem}.%(ext)s"),
        "format": "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
        "merge_output_format": "mp4", "noplaylist": True, "quiet": True,
        "socket_timeout": 60, "retries": 3,
    }
    with yt_dlp.YoutubeDL(options) as downloader:
        downloader.download([url])
    candidates = sorted(output_dir.glob(f"{stem}.*"), key=lambda path: path.stat().st_size, reverse=True)
    return candidates[0] if candidates else None


async def async_download_external_video(url: str, task_id: str | None = None) -> Path | None:
    return await asyncio.to_thread(download_external_video, url, task_id)
