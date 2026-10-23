"""API Router fallback: bounded extraction, original audio and verified ranged media."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit, urlunsplit

import requests

from .budget_apify_downloader import MAX_BYTES, BudgetApifyError, _public_https, _verify_media

logger = logging.getLogger(__name__)


def _api_host() -> str:
    """The extraction API host; kept in private configuration."""
    return os.getenv("RAPIDAPI_YOUTUBE_HOST", "").strip()


def _relay_host() -> str:
    """The media relay host the extraction API returns; kept in private configuration."""
    return os.getenv("RAPIDAPI_YOUTUBE_RELAY_HOST", "").strip()


class RapidAPIDownloadError(RuntimeError):
    """A safe-to-log fallback failure (never includes signed URLs or credentials)."""


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RapidAPIDownloadError("API Router download timed out.")
    return remaining


def _extract(video_id: str, api_key: str, deadline: float) -> dict:
    # Exactly one billable extraction attempt. Never follow redirects with the key.
    with requests.Session() as session:
        session.trust_env = False
        with session.post(
            f"https://{_api_host()}/video.php",
            headers={"x-rapidapi-key": api_key, "x-rapidapi-host": _api_host()},
            files={"video_url": (None, f"https://www.youtube.com/watch?v={video_id}")},
            timeout=min(30, _remaining(deadline)), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code != 200:
                raise RapidAPIDownloadError(f"API Router extraction returned HTTP {response.status_code}.")
            body = bytearray()
            for chunk in response.iter_content(65536):
                _remaining(deadline)
                body.extend(chunk)
                if len(body) > 2_000_000:
                    raise RapidAPIDownloadError("API Router returned an oversized response.")
            payload = json.loads(body)
    if not isinstance(payload, dict):
        raise RapidAPIDownloadError("API Router returned an invalid response.")
    thumb = urlsplit(str(payload.get("thumbnail", "")))
    if thumb.hostname not in {"i.ytimg.com", "img.youtube.com"} or not re.match(
        rf"/vi(?:_webp)?/{re.escape(video_id)}/", thumb.path
    ):
        raise RapidAPIDownloadError("API Router returned the wrong video identity.")
    return payload


def _track(row: dict) -> dict:
    relay = _public_https(str(row.get("download_url", "")))
    query = parse_qs(relay.query, keep_blank_values=True)
    if relay.hostname != _relay_host() or relay.path != "/" or set(query) != {"url"} or len(query["url"]) != 1:
        raise RapidAPIDownloadError("API Router returned an unsupported media relay.")
    source_url = query["url"][0]
    source = _public_https(source_url)
    if not source.hostname.endswith(".googlevideo.com") or source.path != "/videoplayback":
        raise RapidAPIDownloadError("API Router returned an unsupported media source.")
    params = parse_qs(source.query, keep_blank_values=True)
    if any(len(values) != 1 for values in params.values()):
        raise RapidAPIDownloadError("API Router returned ambiguous media parameters.")
    size = int(params["clen"][0])
    duration = float(params["dur"][0])
    if not 0 < size <= MAX_BYTES or not math.isfinite(duration) or duration <= 0:
        raise RapidAPIDownloadError("API Router media is empty or exceeds the 10 GB limit.")
    return {"source": source_url, "size": size, "duration": duration,
            "tags": params.get("xtags", [""])[0], "row": row}


def _select(payload: dict, quality: str) -> tuple[dict, dict]:
    rows = payload.get("medias")
    if not isinstance(rows, list):
        raise RapidAPIDownloadError("API Router returned no media formats.")
    videos = [r for r in rows if isinstance(r, dict) and r.get("type") == "video"
              and str(r.get("mimeType", "")).startswith("video/mp4")
              and "avc1" in str(r.get("mimeType", ""))
              and 0 < int(r.get("height", 0)) <= int(quality)]
    if not videos:
        raise RapidAPIDownloadError("API Router returned no supported video quality.")
    video = _track(max(videos, key=lambda r: (int(r["height"]), float(r.get("fps") or 0))))
    audios = [_track(r) for r in rows if isinstance(r, dict) and r.get("type") == "audio"
              and str(r.get("mimeType", "")).startswith("audio/")]
    originals = [a for a in audios if "acont=original" in a["tags"]]
    # Untagged tracks are safe only when there are no alternate-language tracks.
    candidates = originals or ([a for a in audios if not a["tags"]]
                              if not any("acont=" in a["tags"] for a in audios) else [])
    if not candidates:
        raise RapidAPIDownloadError("API Router returned no original audio track.")
    audio = max(candidates, key=lambda a: ("drc=1" not in a["tags"], float(a["row"].get("bitrate") or 0)))
    if video["size"] + audio["size"] > MAX_BYTES:
        raise RapidAPIDownloadError("API Router combined media exceeds the 10 GB limit.")
    if abs(video["duration"] - audio["duration"]) > max(5, video["duration"] * .001):
        raise RapidAPIDownloadError("API Router audio and video durations do not match.")
    return video, audio


def _range_url(track: dict, start: int, end: int) -> str:
    source = urlsplit(track["source"])
    params = parse_qs(source.query)
    params["range"] = [f"{start}-{end}"]
    inner = urlunsplit(source._replace(query=urlencode(params, doseq=True)))
    return f"https://{_relay_host()}/?" + urlencode({"url": inner})



def _open_range(session, track: dict, start: int, end: int, deadline: float):
    """Keep Google CDN redirects inside the approved relay, without credentials."""
    current = track
    seen = set()
    for hop in range(4):
        url = _range_url(current, start, end)
        if url in seen:
            raise RapidAPIDownloadError("API Router media relay redirected back to the same source.")
        seen.add(url)
        response = session.get(url, stream=True, allow_redirects=False,
                               timeout=min(30, _remaining(deadline)))
        if response.status_code not in {301, 302, 303, 307, 308}:
            return response
        try:
            location = response.headers.get("Location")
            if not location:
                raise RapidAPIDownloadError("API Router media relay returned a redirect without a destination.")
            if hop == 3:
                raise RapidAPIDownloadError("API Router media redirect limit exceeded.")
            address = urljoin(url, location)
            parsed = _public_https(address)
            if parsed.hostname == _relay_host():
                redirected = _track({"download_url": address})
                if redirected["size"] != track["size"] or abs(redirected["duration"] - track["duration"]) > 1:
                    raise RapidAPIDownloadError("API Router redirected to different media.")
                current = redirected
            elif parsed.hostname.endswith(".googlevideo.com") and parsed.path == "/videoplayback":
                params = parse_qs(parsed.query)
                if any(len(values) != 1 for values in params.values()):
                    raise RapidAPIDownloadError("API Router returned ambiguous redirect parameters.")
                if "clen" in params and int(params["clen"][0]) != track["size"]:
                    raise RapidAPIDownloadError("API Router redirected to different media.")
                current = dict(track, source=address)
            else:
                raise RapidAPIDownloadError("API Router returned an unsupported media redirect.")
        finally:
            response.close()
    raise RapidAPIDownloadError("API Router media redirect limit exceeded.")


def _download_track(track: dict, target: Path, deadline: float, *, chunk_size: int, workers: int):
    stop = threading.Event()
    size = track["size"]
    with target.open("wb") as output:
        output.truncate(size)

    def part(start: int):
        end = min(start + chunk_size, size) - 1
        expected = end - start + 1
        # Each thread owns its session/file handle. API credentials never reach media hosts.
        with requests.Session() as session:
            session.trust_env = False
            with _open_range(session, track, start, end, deadline) as response:
                if response.status_code not in {200, 206}:
                    raise RapidAPIDownloadError(f"API Router media returned HTTP {response.status_code}.")
                length = response.headers.get("Content-Length")
                if length is not None and int(length) != expected:
                    raise RapidAPIDownloadError("API Router returned an incorrect byte range.")
                if response.status_code == 206 and response.headers.get("Content-Range") != f"bytes {start}-{end}/{size}":
                    raise RapidAPIDownloadError("API Router returned an incorrect byte range.")
                received = 0
                with target.open("r+b") as output:
                    output.seek(start)
                    for chunk in response.iter_content(256 * 1024):
                        if stop.is_set():
                            raise RapidAPIDownloadError("API Router download cancelled.")
                        _remaining(deadline)
                        received += len(chunk)
                        if received > expected:
                            raise RapidAPIDownloadError("API Router returned an oversized byte range.")
                        output.write(chunk)
                if received != expected:
                    raise RapidAPIDownloadError("API Router returned an incomplete byte range.")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = [pool.submit(part, start) for start in range(0, size, chunk_size)]
        try:
            for future in as_completed(pending, timeout=_remaining(deadline)):
                future.result()
        except BaseException:
            stop.set()
            for future in pending:
                future.cancel()
            raise


def download_video_via_rapidapi(video_id: str, temp_dir: Path, api_key: str,
                               quality: str = "1080", timeout_seconds: int = 1200) -> Path:
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", video_id) or not api_key or not _api_host() or not _relay_host():
        raise RapidAPIDownloadError("API Router download is not configured correctly.")
    deadline = time.monotonic() + max(60, min(1800, timeout_seconds))
    temp_dir.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=f"rapidapi-{video_id}-", dir=temp_dir))
    complete = False
    try:
        video, audio = _select(_extract(video_id, api_key, deadline), quality)
        total = video["size"] + audio["size"]
        if shutil.disk_usage(folder).free < 2 * total + 128 * 1024 * 1024:
            raise RapidAPIDownloadError("Not enough temporary disk space for API Router media.")
        logger.info("API Router downloading %s (%s bytes, %sp, original audio)",
                    video_id, total, video["row"]["height"])
        video_path, audio_path = folder / "video.mp4", folder / "audio.media"
        _download_track(video, video_path, deadline, chunk_size=16 * 1024 * 1024, workers=8)
        _download_track(audio, audio_path, deadline, chunk_size=1024 * 1024, workers=8)
        result = folder / f"{video_id}.mp4"
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(video_path),
                        "-i", str(audio_path), "-map", "0:v:0", "-map", "1:a:0",
                        "-c", "copy", "-strict", "-2", "-y", str(result)],
                       capture_output=True, check=True, timeout=_remaining(deadline))
        if not 0 < result.stat().st_size <= MAX_BYTES:
            raise RapidAPIDownloadError("API Router output exceeds the 10 GB limit.")
        _verify_media(result, {"durationSeconds": video["duration"],
                              "width": int(video["row"]["width"]),
                              "height": int(video["row"]["height"])}, deadline)
        video_path.unlink()
        audio_path.unlink()
        complete = True
        return result
    except RapidAPIDownloadError:
        raise
    except BudgetApifyError:
        raise RapidAPIDownloadError("API Router delivered invalid media or a disallowed address.") from None
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError,
            OSError, subprocess.SubprocessError, TimeoutError):
        raise RapidAPIDownloadError("API Router could not deliver a complete verified video.") from None
    finally:
        if not complete:
            shutil.rmtree(folder, ignore_errors=True)
