"""Download YouTube videos through SupoClip's own download service (``ytdl/``)."""
from __future__ import annotations

import logging
import shutil
import tempfile
import time
from pathlib import Path

import requests

from .config import get_config

logger = logging.getLogger(__name__)
POLL_INTERVAL = 2
# The service restarts itself for yt-dlp upgrades; ride those out.
RETRYABLE_STATUS = {502, 503, 504}

# Codes where another provider would hit the same wall: do not pay for a fallback.
PERMANENT_CODES = {"private", "age_restricted", "members_only", "not_started", "live",
                   "removed", "invalid_url", "unavailable", "too_long", "too_large"}


class YtdlDownloadError(RuntimeError):
    """Safe-to-display failure. ``permanent`` means no other provider can help."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.code = code
        self.permanent = code in PERMANENT_CODES


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise YtdlDownloadError("YouTube download timed out.", "timeout")
    return remaining


def _call(session: requests.Session, method: str, path: str, deadline: float, **kwargs) -> dict:
    base = get_config().ytdl_api_url.rstrip("/")
    while True:
        try:
            response = session.request(method, base + path, timeout=(10, min(30, _remaining(deadline))),
                                       allow_redirects=False, **kwargs)
        except requests.RequestException:
            logger.warning("YouTube download service unreachable; retrying")
            time.sleep(min(5, _remaining(deadline)))
            continue
        if response.status_code in RETRYABLE_STATUS:
            time.sleep(min(5, _remaining(deadline)))
            continue
        if response.status_code == 404 and method == "GET":
            raise YtdlDownloadError("YouTube download was lost; retrying.", "lost")
        if response.status_code not in {200, 202}:
            raise YtdlDownloadError(
                f"YouTube download service returned HTTP {response.status_code}.", "service")
        try:
            payload = response.json()
        except ValueError:
            raise YtdlDownloadError("YouTube download service returned invalid data.", "service") from None
        if not isinstance(payload, dict):
            raise YtdlDownloadError("YouTube download service returned invalid data.", "service")
        return payload


def _wait(session: requests.Session, video_id: str, deadline: float) -> dict:
    config = get_config()
    body = {"video_id": video_id, "max_height": config.ytdl_video_quality,
            "max_bytes": config.max_video_upload_bytes}
    # A restart forgets in-flight jobs; resubmitting is cheap because the
    # service resumes from its cache or de-duplicates.
    for _ in range(3):
        job = _call(session, "POST", "/v1/downloads", deadline, json=body)
        try:
            while job.get("status") in {"queued", "running"}:
                time.sleep(min(POLL_INTERVAL, _remaining(deadline)))
                job = _call(session, "GET", f"/v1/downloads/{job['id']}", deadline)
        except YtdlDownloadError as exc:
            if exc.code == "lost":
                continue
            raise
        return job
    raise YtdlDownloadError("YouTube download service kept losing the download.", "lost")


def _transfer(session: requests.Session, job: dict, destination: Path, deadline: float) -> int:
    base = get_config().ytdl_api_url.rstrip("/")
    expected = int((job.get("result") or {}).get("size") or 0)
    if expected > get_config().max_video_upload_bytes:
        raise YtdlDownloadError("YouTube download exceeds the file size limit.", "too_large")
    if expected <= 0:
        raise YtdlDownloadError("YouTube download service returned no file size.", "service")
    part = destination.with_suffix(destination.suffix + ".part")
    try:
        with session.get(f"{base}/v1/downloads/{job['id']}/file", stream=True, allow_redirects=False,
                         timeout=(10, min(120, _remaining(deadline)))) as response:
            if response.status_code == 410:
                raise YtdlDownloadError("YouTube download expired; retrying.", "lost")
            if response.status_code != 200:
                raise YtdlDownloadError(
                    f"YouTube download file returned HTTP {response.status_code}.", "service")
            total = 0
            with part.open("wb") as output:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    _remaining(deadline)
                    total += len(chunk)
                    if total > expected:
                        raise YtdlDownloadError("YouTube download file is larger than reported.", "service")
                    output.write(chunk)
        if total != expected:
            raise YtdlDownloadError("YouTube download was incomplete.", "service")
        part.replace(destination)
        return total
    except requests.RequestException:
        raise YtdlDownloadError("YouTube download file could not be transferred.", "service") from None
    finally:
        part.unlink(missing_ok=True)


def download_video_via_ytdl(video_id: str) -> Path:
    config = get_config()
    if not config.ytdl_api_url or not config.ytdl_api_token:
        raise YtdlDownloadError("YouTube download service is not configured.", "config")
    deadline = time.monotonic() + config.ytdl_download_timeout_seconds
    Path(config.temp_dir).mkdir(parents=True, exist_ok=True)
    work_dir = Path(tempfile.mkdtemp(prefix=f"ytdl-{video_id}-", dir=config.temp_dir))
    try:
        with requests.Session() as session:
            session.trust_env = False
            session.headers["Authorization"] = f"Bearer {config.ytdl_api_token}"
            for _ in range(2):
                job = _wait(session, video_id, deadline)
                if job.get("status") != "completed":
                    raise YtdlDownloadError(job.get("error") or "YouTube download failed.",
                                            job.get("error_code") or "error")
                destination = work_dir / f"{video_id}.mp4"
                try:
                    size = _transfer(session, job, destination, deadline)
                except YtdlDownloadError as exc:
                    if exc.code == "lost":
                        continue
                    raise
                result = job.get("result") or {}
                logger.info("ytdl download complete: video=%s bytes=%s %sp via %s (%s attempt(s))",
                            video_id, size, result.get("height"), result.get("route"),
                            result.get("attempts"))
                return destination
            raise YtdlDownloadError("YouTube download expired before transfer.", "lost")
    except BaseException:
        shutil.rmtree(work_dir, ignore_errors=True)
        raise
