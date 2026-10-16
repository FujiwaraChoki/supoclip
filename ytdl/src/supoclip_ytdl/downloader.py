"""One download, attempted across exits until it succeeds or cannot succeed."""
from __future__ import annotations

import json
import logging
import math
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import yt_dlp

from .config import Route, Settings
from .errors import Cancelled, DownloadFailed, Failure, Stalled, classify
from .routing import Router, redact

logger = logging.getLogger(__name__)

MEDIA_SUFFIXES = {".mp4", ".mkv", ".webm", ".m4a"}


@dataclass
class Progress:
    """Mutable state shared with the API thread; written only by the download thread."""

    stage: str = "queued"
    attempt: int = 0
    route: str | None = None
    downloaded_bytes: int = 0
    total_bytes: int | None = None
    transferred_bytes: int = 0
    cancel: threading.Event = field(default_factory=threading.Event)


@dataclass
class Result:
    path: Path
    size: int
    title: str | None
    duration: float
    width: int
    height: int
    format_id: str
    route: str
    attempts: int
    transferred_bytes: int


class _Logger:
    """yt-dlp logger that keeps proxy credentials out of our logs."""

    def __init__(self, video_id: str):
        self.prefix = f"[{video_id}] "

    def debug(self, msg):
        if not msg.startswith("[debug] "):
            logger.debug(self.prefix + redact(msg))

    def info(self, msg):
        logger.debug(self.prefix + redact(msg))

    def warning(self, msg):
        logger.info(self.prefix + redact(msg))

    def error(self, msg):
        logger.info(self.prefix + redact(msg))


def format_selector(max_height: int) -> tuple[str, list[str]]:
    # Prefer H.264 + AAC over HTTPS: mp4-native (no re-encode), widely decodable
    # downstream, and HTTPS formats resume across IP rotations, HLS does not.
    # Original-language audio beats dubbed tracks. Fall back to anything at or
    # below the cap, then to anything at all, rather than failing.
    fmt = (f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b")
    sort = ["lang", f"res:{max_height}", "vcodec:h264", "acodec:aac", "proto:https"]
    return fmt, sort


def _int(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height:format=duration", "-of", "json", str(path)],
        capture_output=True, check=True, timeout=60,
    )
    return json.loads(result.stdout)


def _validate(path: Path, expected_duration: float | None) -> tuple[float, int, int]:
    info = _probe(path)
    streams = info.get("streams", [])
    kinds = {s.get("codec_type") for s in streams}
    if not {"video", "audio"} <= kinds:
        raise DownloadFailed(Failure("incomplete", "transient", "YouTube download is missing a media track."))
    video = next(s for s in streams if s.get("codec_type") == "video")
    try:
        duration = float(info.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = 0.0
    if not math.isfinite(duration) or duration <= 0:
        raise DownloadFailed(Failure("incomplete", "transient", "YouTube download has no duration."))
    if expected_duration and abs(duration - expected_duration) > max(5.0, expected_duration * 0.005):
        raise DownloadFailed(Failure("incomplete", "transient", "YouTube download was truncated."))
    return duration, _int(video.get("width")), _int(video.get("height"))


class Attempt:
    """Progress hook state for one attempt: byte accounting, stall detection, cancel."""

    def __init__(self, progress: Progress, settings: Settings, work: Path, deadline: float):
        self.progress = progress
        self.settings = settings
        self.deadline = deadline
        # Resumed .part files already count as transferred by earlier attempts.
        self.seen = {str(p.with_suffix("")): p.stat().st_size for p in work.glob("*.part")}
        self.transferred = 0
        # Starts at the first media bytes: extraction time is not download speed.
        self.window_start: float | None = None
        self.window_bytes = 0
        self.finished: dict[str, int] = {}

    def hook(self, d: dict) -> None:
        if self.progress.cancel.is_set():
            raise Cancelled()
        if time.monotonic() > self.deadline:
            raise DownloadFailed(Failure("timeout", "fatal", "YouTube download timed out."))
        # "finished" events carry only the final filename; key everything by it.
        name = d.get("filename") or str(d.get("tmpfilename") or "").removesuffix(".part")
        current = _int(d.get("downloaded_bytes"))
        delta = max(0, current - self.seen.get(name, 0))
        self.seen[name] = max(current, self.seen.get(name, 0))
        self.transferred += delta
        self.progress.transferred_bytes += delta
        if d.get("status") == "finished":
            self.finished[name] = current
        self.progress.downloaded_bytes = sum(self.finished.values()) + (
            current if d.get("status") == "downloading" else 0)

        now = time.monotonic()
        if self.window_start is None:
            self.window_start = now
        self.window_bytes += delta
        if now - self.window_start >= self.settings.stall_seconds:
            if self.window_bytes < self.settings.min_speed_bytes * self.settings.stall_seconds:
                raise Stalled()
            self.window_start, self.window_bytes = now, 0


def _unwrap(exc: BaseException) -> BaseException:
    """yt-dlp wraps exceptions raised from hooks; find ours if present."""
    seen = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, (Cancelled, Stalled, DownloadFailed)):
            return current
        seen.add(id(current))
        exc_info = getattr(current, "exc_info", None)
        nested = exc_info[1] if exc_info and len(exc_info) > 1 else None
        current = nested or current.__cause__ or current.__context__
    return exc


def _clear(work: Path, keep_parts: bool) -> None:
    for path in work.iterdir():
        if keep_parts and path.name.endswith((".part", ".ytdl")):
            continue
        if path.is_file():
            path.unlink(missing_ok=True)


def download(video_id: str, max_height: int, max_bytes: int, settings: Settings,
             router: Router, progress: Progress, work: Path) -> Result:
    url = f"https://www.youtube.com/watch?v={video_id}"
    work.mkdir(parents=True, exist_ok=True)
    settings.ytdlp_cache_dir.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + settings.job_timeout_seconds
    base_format, format_sort = format_selector(max_height)
    pinned: str | None = None
    route_blocks: dict[str, int] = {}
    last: Failure | None = None

    plan = router.plan()[: settings.max_attempts]
    for index, route in enumerate(plan, start=1):
        if progress.cancel.is_set():
            raise Cancelled()
        if time.monotonic() > deadline:
            raise DownloadFailed(Failure("timeout", "fatal", "YouTube download timed out."))
        exit_ = router.exit_for(route)
        clients = settings.player_clients
        client = clients[route_blocks.get(route.name, 0) % len(clients)]
        progress.attempt, progress.route, progress.stage = index, route.name, "extracting"
        attempt = Attempt(progress, settings, work, deadline)
        logger.info("[%s] attempt %s/%s via %s (session %s, clients %s)", video_id, index,
                    len(plan), route.name, exit_.session, client or "default")
        try:
            result = _attempt(url, video_id, max_height, max_bytes, settings, exit_.proxy,
                              client, base_format, format_sort, pinned, attempt, progress, work)
        except BaseException as exc:  # noqa: BLE001 - classified below
            pinned = pinned or getattr(exc, "_ytdl_pinned", None)
            exc = _unwrap(exc)
            if isinstance(exc, Cancelled):
                router.record(route, "cancelled", attempt.transferred)
                raise exc from None
            if isinstance(exc, Stalled):
                failure = Failure("stalled", "transient", "The YouTube download stalled.")
            elif isinstance(exc, DownloadFailed):
                failure = exc.failure
            elif isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            else:
                failure = classify(exc)
            outcome = {"blocked": "blocked", "transient": "failed", "fatal": "fatal"}[failure.kind]
            router.record(route, outcome, attempt.transferred, f"{failure.code}: {exc}")
            logger.warning("[%s] attempt %s via %s failed: %s (%s) %s", video_id, index,
                           route.name, failure.code, failure.kind, redact(str(exc))[:300])
            last = failure
            if failure.kind == "fatal":
                raise DownloadFailed(failure) from None
            if failure.kind == "blocked":
                route_blocks[route.name] = route_blocks.get(route.name, 0) + 1
            # Keep partial media for resume unless the output itself was bad.
            _clear(work, keep_parts=failure.code != "incomplete")
            if failure.code == "incomplete":
                pinned = None
            continue
        router.record(route, "success", attempt.transferred)
        result.attempts = index
        result.transferred_bytes = progress.transferred_bytes
        return result

    raise DownloadFailed(last or Failure("no_route", "fatal", "No YouTube download route is available."))


def _attempt(url, video_id, max_height, max_bytes, settings, proxy, client, base_format,
             format_sort, pinned, attempt: Attempt, progress: Progress, work: Path) -> Result:
    extractor_args: dict[str, dict[str, list[str]]] = {}
    if client:
        extractor_args["youtube"] = {"player_client": client.split(",")}
    if settings.pot_base_url:
        extractor_args["youtubepot-bgutilhttp"] = {"base_url": [settings.pot_base_url]}
    opts = {
        "outtmpl": str(work / f"{video_id}.%(ext)s"),
        "format": f"{pinned}/{base_format}" if pinned else base_format,
        "format_sort": format_sort,
        "merge_output_format": "mp4",
        "noplaylist": True,
        # "" means a direct connection and ignores *_PROXY environment variables.
        "proxy": proxy or "",
        "cachedir": str(settings.ytdlp_cache_dir),
        "socket_timeout": 20,
        "retries": 2,
        "fragment_retries": 5,
        "extractor_retries": 0,
        "http_chunk_size": 10 * 1024 * 1024,
        "concurrent_fragment_downloads": 4,
        "continuedl": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "logger": _Logger(video_id),
        "progress_hooks": [attempt.hook],
        "extractor_args": extractor_args,
        "writethumbnail": False,
        "writesubtitles": False,
        "writeautomaticsub": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise DownloadFailed(Failure("unavailable", "fatal", "This YouTube video is unavailable."))
        live_status = info.get("live_status")
        if info.get("is_live") or live_status in {"is_live", "is_upcoming", "post_live"}:
            raise DownloadFailed(Failure(
                "live", "fatal",
                "This YouTube video is live or still processing; try again once the stream ends."))
        duration = info.get("duration")
        if duration and duration > settings.max_duration_seconds:
            raise DownloadFailed(Failure("too_long", "fatal", "This YouTube video is too long."))
        selected = info.get("requested_formats") or [info]
        format_id = "+".join(str(f.get("format_id")) for f in selected)
        expected = sum(_int(f.get("filesize") or f.get("filesize_approx")) for f in selected)
        if expected > max_bytes:
            raise DownloadFailed(Failure("too_large", "fatal", "This YouTube video is too large."))
        progress.total_bytes = expected or None
        progress.stage = "downloading"
        try:
            ydl.process_ie_result(info, download=True)
        except BaseException as exc:
            # Let the next exit resume the same formats from the .part files.
            exc._ytdl_pinned = format_id  # type: ignore[attr-defined]
            raise
    progress.stage = "verifying"
    output = work / f"{video_id}.mp4"
    if not output.exists():
        candidates = [p for p in work.iterdir() if p.suffix in MEDIA_SUFFIXES and p.stem == video_id]
        if not candidates:
            raise DownloadFailed(Failure("incomplete", "transient", "YouTube download produced no file."))
        output = candidates[0]
    duration_s, width, height = _validate(output, float(duration) if duration else None)
    size = output.stat().st_size
    if size > max_bytes:
        raise DownloadFailed(Failure("too_large", "fatal", "This YouTube video is too large."))
    return Result(path=output, size=size, title=info.get("title"), duration=duration_s,
                  width=width, height=height, format_id=format_id, route=attempt.progress.route or "",
                  attempts=0, transferred_bytes=0)


def remove_tree(path: Path) -> None:
    shutil.rmtree(path, ignore_errors=True)
