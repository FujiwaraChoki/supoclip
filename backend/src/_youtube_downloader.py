"""``YouTubeDownloader`` extracted from ``youtube_utils.py``.

Holds yt-dlp option presets. Re-exported by ``youtube_utils`` for callers that
still import it from there.
"""

from pathlib import Path
from typing import Any, Dict

from .config import get_config


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
            "socket_timeout": 30,
            "retries": 5,
            "fragment_retries": 5,
            "http_chunk_size": 10485760,  # 10MB chunks
            "quiet": True,
            "no_warnings": False,
            "ignoreerrors": False,
            "http_headers": {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Accept-Encoding": "gzip, deflate",
                "Connection": "keep-alive",
            },
            "extract_flat": False,
            "writeinfojson": False,
            "nocheckcertificate": True,
            "prefer_insecure": False,
            "age_limit": None,
        }

        return opts
