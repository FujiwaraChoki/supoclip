"""Filesystem + subprocess helpers extracted from ``youtube_utils.py``."""

from pathlib import Path
from typing import Any, Dict, Optional
import logging
import subprocess

logger = logging.getLogger(__name__)


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
