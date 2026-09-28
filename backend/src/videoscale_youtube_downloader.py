"""Download a complete YouTube video through VideoScale's asynchronous API."""
from __future__ import annotations

import json
import logging
import math
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit, urlunsplit

import requests

from .config import get_config

logger = logging.getLogger(__name__)
API_BASE = "https://gate.apiscrape.net:16262"
STORAGE_HOST = "s3.fr-par.scw.cloud"
STORAGE_PREFIX = "/youtube-download/"
TASK_ID = re.compile(r"[0-9a-fA-F-]{36}\Z")
VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}\Z")
POLL_INTERVAL = 3


class VideoScaleDownloadError(RuntimeError):
    """Safe failure text; provider responses and signed URLs are never exposed."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise VideoScaleDownloadError("YouTube download timed out.")
    return remaining


def _request(session: requests.Session, method: str, path: str, deadline: float,
             read_timeout: int = 30, **kwargs):
    try:
        response = session.request(
            method, API_BASE + path, timeout=(10, min(read_timeout, _remaining(deadline))),
            allow_redirects=False, **kwargs,
        )
        if response.status_code != 200 and not (method == "POST" and response.status_code == 202):
            raise VideoScaleDownloadError(
                f"YouTube download provider returned HTTP {response.status_code}."
            )
        payload = response.json()
        if not isinstance(payload, (dict, list)):
            raise ValueError("Invalid payload")
        return payload
    except (requests.RequestException, ValueError):
        raise VideoScaleDownloadError("YouTube download provider could not be reached.") from None


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (ValueError, TypeError, OverflowError):
        return 0


def _size(item: dict) -> int:
    return _int(item.get("filesize") or item.get("filesize_approx"))


def _duration(item: dict) -> float | None:
    for key in ("duration", "duration_seconds"):
        try:
            value = float(item.get(key))
            if math.isfinite(value) and value > 0:
                return value
        except (TypeError, ValueError):
            pass
    raw_url = item.get("url")
    if isinstance(raw_url, str):
        try:
            values = parse_qs(urlsplit(raw_url).query).get("dur", [])
            if values:
                value = float(values[0])
                if math.isfinite(value) and value > 0:
                    return value
        except (TypeError, ValueError):
            pass
    return None


def select_formats(formats: list[dict], quality: int, max_bytes: int):
    """Select H.264 MP4 video and the preferred original AAC/M4A audio."""
    video = [f for f in formats if isinstance(f, dict)
             and str(f.get("ext", "")).lower() == "mp4"
             and str(f.get("vcodec", "")).lower().startswith(("avc1", "h264"))
             and str(f.get("acodec", "none")).lower() == "none"
             and 0 < _int(f.get("height")) <= quality and f.get("format_id")]
    audio = [f for f in formats if isinstance(f, dict)
             and str(f.get("ext", "")).lower() in {"m4a", "mp4"}
             and str(f.get("acodec", "")).lower().startswith(("mp4a", "aac"))
             and str(f.get("vcodec", "none")).lower() == "none"
             and f.get("format_id")]
    if not video or not audio:
        raise VideoScaleDownloadError("YouTube download has no compatible video and audio formats.")
    video.sort(key=lambda f: (_int(f.get("height")), _int(f.get("width")), _size(f)), reverse=True)
    # yt-dlp language_preference ranks original/default tracks above dubbed ones.
    # format_note is an additional hint; bitrate is only a tie-breaker.
    audio.sort(key=lambda f: (
        "original" in str(f.get("format_note", "")).lower(),
        float(f.get("language_preference") or 0),
        f.get("language") in (None, "", "und"),
        _int(f.get("abr") or f.get("tbr")),
    ), reverse=True)
    selected_video, selected_audio = video[0], audio[0]
    sizes = (_size(selected_video), _size(selected_audio))
    if any(size > max_bytes for size in sizes) or sum(sizes) > max_bytes:
        raise VideoScaleDownloadError("YouTube download exceeds the size limit.")
    return selected_video, selected_audio


def _safe_download_url(raw: object) -> str:
    if not isinstance(raw, str):
        raise VideoScaleDownloadError("YouTube download provider returned an invalid file URL.")
    parsed = urlsplit(raw)
    if (parsed.scheme not in {"http", "https"} or parsed.hostname != STORAGE_HOST
            or parsed.port is not None or parsed.username or parsed.password
            or not parsed.path.startswith(STORAGE_PREFIX) or parsed.fragment):
        raise VideoScaleDownloadError("YouTube download provider returned an invalid file URL.")
    # Observed provider payload uses HTTP for Scaleway; fetch only over TLS.
    return urlunsplit(("https", parsed.netloc, parsed.path, parsed.query, ""))


def _submit_and_wait(session: requests.Session, url: str, format_id: str, deadline: float) -> str:
    payload = _request(session, "POST", "/api/download", deadline,
                       json={"url": url, "format_id": format_id})
    task_id = str(payload.get("task_id", "")) if isinstance(payload, dict) else ""
    if not TASK_ID.fullmatch(task_id):
        raise VideoScaleDownloadError("YouTube download provider returned an invalid task ID.")
    logger.info("VideoScale download submitted: job=%s", task_id)
    # Never repeat the POST. An ambiguous timeout could already have created a paid job.
    while True:
        try:
            status = _request(session, "GET", f"/api/status/{task_id}", deadline)
        except VideoScaleDownloadError as exc:
            transient = ("could not be reached" in str(exc)
                         or any(f"HTTP {status}" in str(exc)
                                for status in (429, 500, 502, 503, 504)))
            if not transient:
                raise
            time.sleep(min(POLL_INTERVAL, _remaining(deadline)))
            continue
        state = str(status.get("status", "")).lower() if isinstance(status, dict) else ""
        if state in {"completed", "complete", "done", "success"}:
            break
        if state in {"failed", "error", "faulted", "cancelled", "canceled"}:
            raise VideoScaleDownloadError("YouTube download failed at the provider.")
        if not re.fullmatch(r"[a-z_]{1,60}", state):
            raise VideoScaleDownloadError("YouTube download provider returned an unknown status.")
        # The vendor adds intermediate states without documenting a fixed enum.
        # Only an explicit completion permits retrieval; other valid state names
        # keep polling the same job under the original deadline.
        logger.debug("VideoScale job=%s status=%s", task_id, state)
        time.sleep(min(POLL_INTERVAL, _remaining(deadline)))
    result = _request(session, "GET", f"/api/download/{task_id}", deadline)
    return _safe_download_url(result.get("download_url") if isinstance(result, dict) else None)


def _transfer(session: requests.Session, url: str, destination: Path, deadline: float, max_bytes: int) -> int:
    part = destination.with_suffix(destination.suffix + ".part")
    try:
        response = session.get(url, stream=True, allow_redirects=False,
                               timeout=(10, min(90, _remaining(deadline))), auth=None)
        if response.status_code != 200:
            raise VideoScaleDownloadError(f"YouTube download file returned HTTP {response.status_code}.")
        expected = _int(response.headers.get("Content-Length"))
        if expected <= 0 or expected > max_bytes:
            raise VideoScaleDownloadError("YouTube download file is empty or exceeds the size limit.")
        total = 0
        with part.open("wb") as output:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                _remaining(deadline)
                if chunk:
                    total += len(chunk)
                    if total > max_bytes:
                        raise VideoScaleDownloadError("YouTube download exceeds the size limit.")
                    output.write(chunk)
        if total != expected:
            raise VideoScaleDownloadError("YouTube download was incomplete.")
        part.replace(destination)
        return total
    except requests.RequestException:
        raise VideoScaleDownloadError("YouTube download file could not be transferred.") from None
    finally:
        part.unlink(missing_ok=True)
        try:
            response.close()
        except UnboundLocalError:
            pass


def _probe(path: Path, deadline: float) -> tuple[set[str], float, tuple[int, int] | None, dict[str, float]]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,duration:format=duration",
         "-of", "json", str(path)], capture_output=True, check=True,
        timeout=min(30, _remaining(deadline)),
    )
    info = json.loads(result.stdout)
    streams = {s.get("codec_type") for s in info.get("streams", [])}
    video_stream = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    dimensions = ((_int(video_stream.get("width")), _int(video_stream.get("height")))
                  if video_stream else None)
    stream_durations = {}
    for stream in info.get("streams", []):
        try:
            value = float(stream.get("duration"))
            if math.isfinite(value) and value > 0:
                stream_durations[stream.get("codec_type")] = value
        except (TypeError, ValueError):
            pass
    duration = float(info.get("format", {}).get("duration", 0))
    if not math.isfinite(duration) or duration <= 0:
        raise VideoScaleDownloadError("YouTube download has invalid duration.")
    return streams, duration, dimensions, stream_durations


def _duration_matches(actual: float, expected: float) -> bool:
    return abs(actual - expected) <= max(5.0, expected * 0.001)


def download_video_via_videoscale(url: str, video_id: str,
                                  expected_duration: float | None = None) -> Path:
    config = get_config()
    if not VIDEO_ID.fullmatch(video_id):
        raise VideoScaleDownloadError("Invalid YouTube video ID.")
    canonical_url = f"https://www.youtube.com/watch?v={video_id}"
    if not config.videoscale_username or not config.videoscale_password:
        raise VideoScaleDownloadError("YouTube download credentials are not configured.")
    quality = _int(config.videoscale_video_quality)
    if quality not in {720, 1080}:
        raise VideoScaleDownloadError("YouTube download quality is not configured.")
    max_bytes = config.max_video_upload_bytes
    work_dir = None
    try:
        with requests.Session() as session:
            session.auth = (config.videoscale_username, config.videoscale_password)
            formats_deadline = time.monotonic() + 90
            payload = _request(session, "GET", "/api/formats", formats_deadline,
                               read_timeout=90, params={"video_url": canonical_url})
            if not isinstance(payload, list):
                raise VideoScaleDownloadError("YouTube download formats were unavailable.")
            video_format, audio_format = select_formats(payload, quality, max_bytes)
            source_duration = expected_duration or _duration(video_format) or _duration(audio_format)
            if not source_duration or not math.isfinite(source_duration) or source_duration <= 0:
                raise VideoScaleDownloadError("YouTube video duration could not be verified.")
            Path(config.temp_dir).mkdir(parents=True, exist_ok=True)
            work_dir = Path(tempfile.mkdtemp(prefix=f"videoscale-{video_id}-", dir=config.temp_dir))
            job_deadline = time.monotonic() + config.videoscale_download_timeout_seconds
            transfer_deadline = None
            paths = []
            sizes = []
            with requests.Session() as storage_session:
                # Do not inherit Basic Auth, cookies, netrc, or proxy settings from the API session.
                storage_session.trust_env = False
                for selected, suffix in ((video_format, ".video.mp4"), (audio_format, ".audio.m4a")):
                    signed_url = _submit_and_wait(session, canonical_url,
                                                  str(selected["format_id"]), job_deadline)
                    transfer_deadline = time.monotonic() + config.videoscale_transfer_timeout_seconds
                    path = work_dir / (video_id + suffix)
                    sizes.append(_transfer(storage_session, signed_url, path, transfer_deadline,
                                           max_bytes - sum(sizes)))
                    paths.append(path)
            video_path, audio_path = paths
            video_streams, video_duration, video_dimensions, video_stream_durations = _probe(video_path, transfer_deadline)
            audio_streams, audio_duration, _, audio_stream_durations = _probe(audio_path, transfer_deadline)
            expected_dimensions = (_int(video_format.get("width")), _int(video_format.get("height")))
            if ("video" not in video_streams or "audio" not in audio_streams
                    or video_dimensions != expected_dimensions
                    or not _duration_matches(video_duration, source_duration)
                    or not _duration_matches(audio_duration, source_duration)
                    or any(not _duration_matches(value, source_duration)
                           for value in (*video_stream_durations.values(), *audio_stream_durations.values()))):
                raise VideoScaleDownloadError("YouTube download is incomplete or missing a media track.")
            merged = work_dir / f"{video_id}.mp4"
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-i", str(video_path),
                 "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0",
                 "-c", "copy", "-movflags", "+faststart", str(merged)],
                capture_output=True, check=True, timeout=_remaining(transfer_deadline),
            )
            merged_streams, merged_duration, merged_dimensions, merged_stream_durations = _probe(merged, transfer_deadline)
            if (not {"video", "audio"}.issubset(merged_streams)
                    or merged_dimensions != expected_dimensions
                    or not _duration_matches(merged_duration, source_duration)
                    or any(not _duration_matches(value, source_duration)
                           for value in merged_stream_durations.values())
                    or merged.stat().st_size > max_bytes):
                raise VideoScaleDownloadError("YouTube download is incomplete or missing a media track.")
            video_path.unlink()
            audio_path.unlink()
            logger.info("VideoScale download complete: video=%s bytes=%s", video_id, merged.stat().st_size)
            return merged
    except VideoScaleDownloadError:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        raise
    except Exception:
        if work_dir:
            shutil.rmtree(work_dir, ignore_errors=True)
        raise VideoScaleDownloadError("YouTube download could not produce a complete video.") from None
