"""Twitch and Kick sources: URL validation plus ordered proxy/direct/Apify fallbacks.

Default provider chains (configurable; Apify actors always run last):

* Twitch: ``proxy`` -> ``direct`` -> ``apify_twitch`` (abotapi/twitch-scraper, VODs only)
* Kick: ``proxy`` -> ``direct_retry`` -> ``apify_kick_hls`` (hipersoft/kick-downloader)
  -> ``apify_kick_mp4`` (parsebird/kick-video-downloader)

Work files live under ``<temp>/stream-work/<task>/<operation>/``. The main
processing job uses the ``job`` operation for every phase (audio, selected
sections, full-video fallback) so paid results are reused across phases. Editor
and caption re-renders get a fresh operation each, so concurrent edits of one
task never delete each other's files or inherit each other's failures.
"""
from __future__ import annotations

import contextvars
import copy
import logging
import math
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Optional
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urljoin, urlparse

import requests

from .stream_transfer import (
    TransferBudget, TransferStopped, account_chunk, bounded_client,
    transfer_scope, check_transfer,
)
from .config import get_config
from .youtube_proxy import normalize_country, render_proxy_url, sticky_session_id
from .youtube_source import CachedPiece, assemble_timeline, merge_ranges, ranges_from_segments

logger = logging.getLogger(__name__)

PRIVATE_VOD_MESSAGE = "This VOD is subscriber-only or private and can't be downloaded"
FETCH_FAILED_MESSAGE = "We couldn't fetch this video, try again later"
LIVE_MESSAGE = "Live streams aren't supported. Use a VOD or clip URL instead."
TWITCH_UNSUPPORTED_MESSAGE = "Unsupported Twitch URL. Use a Twitch VOD (twitch.tv/videos/<id>) or clip URL."
KICK_UNSUPPORTED_MESSAGE = "Unsupported Kick URL. Use a Kick VOD (kick.com/<channel>/videos/<id>) or clip URL."
UNSUPPORTED_SOURCE_MESSAGE = "Only YouTube, Twitch VOD/clip, Kick VOD/clip, or uploaded video sources are supported"

WORK_DIRNAME = "stream-work"
PROCESSING_OPERATION = "job"
YTDLP_PROVIDERS = frozenset({"proxy", "direct", "direct_retry"})
APIFY_PROVIDERS = frozenset({"apify_twitch", "apify_kick_hls", "apify_kick_mp4"})
# Cheap metadata actors (~$0.10 cap per run) that the API preflight may use when
# yt-dlp cannot reach the platform. parsebird downloads the whole video and
# must never run during preflight.
PREFLIGHT_ACTORS = frozenset({"apify_twitch", "apify_kick_hls"})
PREFLIGHT_DEADLINE_SECONDS = 30.0
_MIN_PREFLIGHT_ACTOR_SECONDS = 5.0
_DIRECT_RETRY_BACKOFF_SECONDS = 1.5
_FAILED_TTL_SECONDS = 600.0
_STATE_TTL_SECONDS = 6 * 3600.0
_SECTION_TOLERANCE_SECONDS = 1.0
# Selected sections of an HLS VOD are fetched fragment by fragment, a few at a
# time: one proxied connection runs at ~1 MiB/s, slower than 1080p60 plays.
_HLS_SECTION_WORKERS = 4
_HLS_FRAGMENT_ATTEMPTS = 3
_HLS_CHUNK_BYTES = 1024 * 1024
# Actor media URLs are signed and expire; an old preflight result (e.g. a manual
# retry hours later) is ignored so the actor may run once more instead.
_SEEDED_MEDIA_TTL_SECONDS = 3600.0

_TWITCH_HOSTS = {"twitch.tv", "m.twitch.tv", "go.twitch.tv"}
_TWITCH_VOD = re.compile(r"^/videos/(?P<id>\d+)$")
_TWITCH_CHANNEL_CLIP = re.compile(r"^/[A-Za-z0-9_]+/clip/(?P<id>[A-Za-z0-9_-]+)$")
_TWITCH_CLIP = re.compile(r"^/(?P<id>[A-Za-z0-9_-]+)$")
_TWITCH_CHANNEL = re.compile(r"^/[A-Za-z0-9_]+$")
_UUID = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
_KICK_VOD = re.compile(rf"^(?:/[A-Za-z0-9_-]+/videos|/video)/(?P<id>{_UUID})$")
_KICK_CLIP = re.compile(r"^/[A-Za-z0-9_-]+/clips/(?P<id>clip_[A-Za-z0-9_-]+)$")
_KICK_CHANNEL = re.compile(r"^/[A-Za-z0-9_-]+$")
_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")

# Messages that mean the video itself is gone or gated. Only matched against
# yt-dlp errors for the platform URL: Apify errors such as "Actor ... does not
# exist" must not stop the remaining fallbacks.
_PRIVATE_ERROR = re.compile(
    r"subscriber-only|sub-only|subscribers only|private video|members-only"
    r"|not available due to privacy|deleted vod|video has been deleted"
    r"|\b(?:video|vod)\s+v?\d+\s+does not exist",
    re.IGNORECASE,
)


class StreamSourceError(RuntimeError):
    """User-facing failure that ends the provider chain."""


class StreamTooLongError(StreamSourceError):
    """The source is longer than the plan allows; nothing more is downloaded."""


class StreamDurationUnavailableError(StreamSourceError):
    """A provider answered but reported no length (0 or missing).

    Kept apart from network/provider failures: an in-progress livestream VOD
    looks like this, so the caller can say so without claiming it is live.
    """


class _NoDuration(RuntimeError):
    pass


class ProviderSkipped(RuntimeError):
    """A provider is not configured or does not support this URL."""


@dataclass
class _Operation:
    expected_duration: Optional[float] = None
    preferred: Optional[str] = None
    actor_results: dict[str, dict] = field(default_factory=dict)
    actors_run: set[str] = field(default_factory=set)
    failed_until: float = 0.0
    local_media: Optional[Path] = None
    touched: float = field(default_factory=time.monotonic)
    budget: Optional[TransferBudget] = None


_operations: dict[tuple[str, str], _Operation] = {}
_operations_lock = threading.RLock()


# --------------------------------------------------------------------------- URLs

def detect_stream_source(url: str) -> tuple[str, str, str]:
    """Return ``(source, kind, platform_id)`` or raise ``ValueError``."""
    try:
        parsed = urlparse(str(url).strip())
    except (TypeError, ValueError):
        raise ValueError("Invalid video URL") from None
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        raise ValueError("Invalid video URL")
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    if host in _TWITCH_HOSTS:
        if m := _TWITCH_VOD.fullmatch(path):
            return "twitch", "vod", m.group("id")
        if m := _TWITCH_CHANNEL_CLIP.fullmatch(path):
            return "twitch", "clip", m.group("id")
        if path != "/" and _TWITCH_CHANNEL.fullmatch(path) and path.lower() not in {"/videos", "/directory", "/search", "/settings"}:
            raise ValueError(LIVE_MESSAGE)
        raise ValueError(TWITCH_UNSUPPORTED_MESSAGE)
    if host == "clips.twitch.tv":
        if m := _TWITCH_CLIP.fullmatch(path):
            return "twitch", "clip", m.group("id")
        raise ValueError("Invalid Twitch clip URL. Use clips.twitch.tv/<clip> or twitch.tv/<channel>/clip/<clip>.")
    if host == "kick.com":
        if m := _KICK_VOD.fullmatch(path):
            return "kick", "vod", m.group("id")
        if m := _KICK_CLIP.fullmatch(path):
            return "kick", "clip", m.group("id")
        clip = parse_qs(parsed.query).get("clip", [""])[0]
        if path != "/" and _KICK_CHANNEL.fullmatch(path):
            if re.fullmatch(r"clip_[A-Za-z0-9_-]+", clip):
                return "kick", "clip", clip
            if path.lower() not in {"/video", "/videos", "/categories", "/browse", "/search"}:
                raise ValueError(LIVE_MESSAGE)
        raise ValueError(KICK_UNSUPPORTED_MESSAGE)
    raise ValueError(UNSUPPORTED_SOURCE_MESSAGE)


def _provider_order(source: str, config=None) -> list[str]:
    config = config or get_config()
    providers = [
        item.strip().lower()
        for item in getattr(config, f"{source}_download_providers").split(",")
        if item.strip()
    ]
    allowed = YTDLP_PROVIDERS | APIFY_PROVIDERS
    if invalid := set(providers) - allowed:
        raise StreamSourceError(f"Unsupported {source} download provider: {sorted(invalid)[0]}")
    seen: list[str] = []
    for provider in providers:
        if provider not in seen:
            seen.append(provider)
    # Apify is always a fallback, even if an operator accidentally lists it first.
    return [p for p in seen if p not in APIFY_PROVIDERS] + [p for p in seen if p in APIFY_PROVIDERS]


# ------------------------------------------------------------------- utilities

def _is_private_error(exc: BaseException | str | None) -> bool:
    return bool(exc) and bool(_PRIVATE_ERROR.search(str(exc)))


def _redact(value: Any) -> str:
    """Keep proxy credentials and signed media query strings out of logs."""
    text = str(value)
    text = re.sub(r"([a-z][a-z0-9+.-]*://)[^/\s@]+@", r"\1***@", text, flags=re.IGNORECASE)
    return re.sub(r"(https?://[^\s?'\"]+)\?[^\s'\"]+", r"\1?…", text)


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _close(actual: Optional[float], expected: Optional[float]) -> bool:
    if not actual or not expected:
        return True
    return abs(actual - expected) <= max(5.0, expected * 0.01)


def _check_cap(duration: Optional[float], max_duration: Optional[float]) -> None:
    if duration and max_duration and duration > max_duration:
        raise StreamTooLongError(
            f"Video is too long ({int(duration) // 60} min). "
            f"Maximum allowed duration is {int(max_duration) // 60} minutes."
        )


def _clamp_ranges(ranges: Optional[list[tuple[float, float]]], duration: Optional[float]):
    if ranges is None or not duration:
        return ranges
    clamped = [(max(0.0, a), min(b, duration)) for a, b in ranges if min(b, duration) - max(0.0, a) > 0.05]
    if not clamped:
        raise StreamSourceError("No valid video sections were selected")
    return clamped


def _probe(path_or_url: str | Path, timeout: int = 60) -> Optional[float]:
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path_or_url)],
            capture_output=True, text=True, check=True, timeout=timeout,
        )
        return _number(result.stdout.strip())
    except Exception:
        return None


def _safe_component(value: str) -> str:
    value = str(value)
    if not _SAFE_COMPONENT.fullmatch(value) or value in {".", ".."}:
        raise ValueError("Invalid stream work directory component")
    return value


def _work_root() -> Path:
    return Path(get_config().temp_dir) / WORK_DIRNAME


def _operation(task_key: str, operation: str) -> _Operation:
    now = time.monotonic()
    with _operations_lock:
        for key in [k for k, v in _operations.items() if now - v.touched > _STATE_TTL_SECONDS]:
            _operations.pop(key, None)
        state = _operations.setdefault((task_key, operation), _Operation())
        state.touched = now
        return state


# --------------------------------------------------------------------- yt-dlp

def _ydl_options(provider, output, *, audio=False, ranges=None, session=None, socket_timeout=10):
    c = get_config()
    opts = {
        "outtmpl": str(output), "noplaylist": True, "quiet": True, "no_warnings": True,
        "retries": 1, "fragment_retries": 3, "socket_timeout": socket_timeout,
        "max_filesize": c.youtube_download_max_bytes,
        "external_downloader": {"default": "native"},
        "hls_prefer_native": True,
        "concurrent_fragment_downloads": 1,
        "skip_unavailable_fragments": False,
        # yt-dlp's HLS downloader may otherwise delegate unsupported manifests
        # to ffmpeg, bypassing response accounting and cooperative cancellation.
        # Local cutting/remuxing uses our own ffmpeg after the network closes.
        "ffmpeg_location": str(_work_root() / ".network-ffmpeg-disabled"),
        "fixup": "never",
    }
    if provider == "proxy":
        if not c.youtube_proxy_url:
            raise ProviderSkipped("proxy is not configured")
        opts["proxy"] = render_proxy_url(
            c.youtube_proxy_url,
            sticky_session_id(None, session or Path(output).parent.name),
            normalize_country(c.youtube_proxy_country),
        )
    # Kick HLS has no audio-only rendition; the smallest video keeps proxy traffic low.
    opts["format"] = (
        "bestaudio/worst[height<=360]/worst" if audio
        else f"bestvideo[height<={c.youtube_max_height}]+bestaudio/best[height<={c.youtube_max_height}]/best"
    )
    if not audio:
        opts["merge_output_format"] = "mkv"
    if ranges:
        raise StreamSourceError("Network section downloads are disabled; cut local media instead")
    return opts


def _socket_timeout(deadline: Optional[float], default: float = 10.0) -> float:
    if deadline is None:
        return default
    return max(1.0, min(default, deadline - time.monotonic()))


def _extract(url: str, provider: str, *, session: str, deadline: Optional[float] = None) -> dict:
    """Metadata only (no media), with a real second attempt for ``direct_retry``."""
    attempts = 2 if provider == "direct_retry" else 1
    for attempt in range(attempts):
        try:
            options = _ydl_options(provider, Path("metadata"), session=session, socket_timeout=_socket_timeout(deadline))
            with bounded_client(options) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
            if not info:
                raise RuntimeError("provider returned no metadata")
            return info
        except ProviderSkipped:
            raise
        except Exception as exc:
            check_transfer()
            out_of_time = deadline is not None and time.monotonic() + _DIRECT_RETRY_BACKOFF_SECONDS + 1 >= deadline
            if attempt + 1 == attempts or _is_private_error(exc) or out_of_time:
                raise
            logger.info("Stream provider %s retrying after: %s", provider, _redact(exc))
            time.sleep(_DIRECT_RETRY_BACKOFF_SECONDS)
    raise RuntimeError("provider returned no metadata")  # pragma: no cover


def _download_info(info: dict, provider: str, template: Path, *, audio: bool, ranges, session: str) -> None:
    """Download from already-extracted metadata so the platform is not queried twice."""
    with bounded_client(_ydl_options(provider, template, audio=audio, ranges=ranges, session=session)) as ydl:
        ydl.process_ie_result(info, download=True)


def _download_url(url: str, provider: str, template: Path, *, audio: bool, ranges, session: str) -> None:
    with bounded_client(_ydl_options(provider, template, audio=audio, ranges=ranges, session=session)) as ydl:
        ydl.extract_info(url, download=True)


def _media_files(directory: Path) -> list[Path]:
    if not directory.exists():
        return []
    return sorted(
        p for p in directory.iterdir()
        if p.is_file() and p.suffix not in {".part", ".ytdl", ".ts", ".json"} and not p.name.endswith(".part")
    )


def _finish(directory: Path, call_dir: Path, ranges) -> Path:
    files = _media_files(directory)
    if not files:
        raise RuntimeError("provider returned no media file")
    if not ranges:
        return files[0]
    if len(files) != len(ranges):
        raise RuntimeError("provider did not return every requested section")
    pieces = []
    for path, (start, end) in zip(files, ranges):
        actual = _probe(path)
        wanted = end - start
        if actual is None or abs(actual - wanted) > max(_SECTION_TOLERANCE_SECONDS, wanted * 0.02):
            raise RuntimeError(f"section {start:.1f}-{end:.1f}s came back {actual or 0:.1f}s long; refusing an inaccurate cut")
        pieces.append((CachedPiece(start, end, path), start, end))
    dest = call_dir / "selected-segments.mkv"
    assemble_timeline(pieces, dest)
    return dest


# ------------------------------------------------------------- HLS sections

class _NoHlsSections(Exception):
    """This rendition can't be fetched fragment by fragment; download it whole."""


@dataclass(frozen=True)
class _HlsSegment:
    start: float
    duration: float
    uri: str
    init: Optional[str]
    discontinuity: bool

    @property
    def end(self) -> float:
        return self.start + self.duration


def _parse_media_playlist(text: str, base_url: str) -> list[_HlsSegment]:
    """Segments of a plain (unencrypted, whole-file) HLS media playlist."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U":
        raise _NoHlsSections("not an HLS playlist")
    segments: list[_HlsSegment] = []
    position, duration, init, discontinuity = 0.0, None, None, False
    for line in lines[1:]:
        if line.startswith("#EXT-X-STREAM-INF") or line.startswith("#EXT-X-BYTERANGE"):
            raise _NoHlsSections("master playlist or byte-range segments")
        if line.startswith("#EXT-X-KEY"):
            method = re.search(r"METHOD=([^,\s]+)", line)
            if not method or method.group(1).upper() != "NONE":
                raise _NoHlsSections("encrypted segments")
        elif line.startswith("#EXT-X-MAP"):
            uri = re.search(r'URI="([^"]+)"', line)
            if not uri or "BYTERANGE" in line:
                raise _NoHlsSections("unsupported init segment")
            init = urljoin(base_url, uri.group(1))
        elif line == "#EXT-X-DISCONTINUITY":
            discontinuity = True
        elif line.startswith("#EXTINF:"):
            duration = _number(line[len("#EXTINF:"):].split(",", 1)[0])
            if duration is None:
                raise _NoHlsSections("segment without a duration")
        elif not line.startswith("#"):
            if duration is None:
                raise _NoHlsSections("segment without a duration")
            segments.append(_HlsSegment(position, duration, urljoin(base_url, line), init, discontinuity))
            position += duration
            duration, discontinuity = None, False
    if not segments:
        raise _NoHlsSections("playlist has no segments")
    return segments


def _segment_spans(segments: list[_HlsSegment], ranges) -> list[tuple[int, int]]:
    """Inclusive segment index spans covering every range, merged when they touch."""
    spans: list[tuple[int, int]] = []
    for start, end in sorted(ranges):
        first = next((i for i, seg in enumerate(segments) if seg.end > start), None)
        last = next((i for i in range(len(segments) - 1, -1, -1) if segments[i].start < end), None)
        if first is None or last is None or last < first:
            raise _NoHlsSections("a range is outside the playlist")
        if spans and first <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], last))
        else:
            spans.append((first, last))
    for first, last in spans:
        if any(segments[i].discontinuity or segments[i].init != segments[first].init for i in range(first + 1, last + 1)):
            raise _NoHlsSections("a section crosses a discontinuity")
    return spans


def _read_url(ydl, url: str, headers: dict, destination: Optional[Path] = None) -> bytes:
    """Fetch one URL through the bounded client, retrying transport errors."""
    from yt_dlp.networking import Request

    for attempt in range(_HLS_FRAGMENT_ATTEMPTS):
        try:
            with ydl.urlopen(Request(url, headers=headers)) as response:
                if destination is None:
                    return response.read()
                with destination.open("wb") as handle:
                    while chunk := response.read(_HLS_CHUNK_BYTES):
                        handle.write(chunk)
                return b""
        except TransferStopped:
            raise
        except Exception:
            check_transfer()
            if attempt + 1 == _HLS_FRAGMENT_ATTEMPTS:
                raise
            time.sleep(min(2.0, 0.5 * (attempt + 1)))
    raise RuntimeError("unreachable")  # pragma: no cover


def _hls_section_pieces(info: dict, provider: str, work_dir: Path, ranges, *, session: str) -> list[tuple[CachedPiece, float, float]]:
    """Download only the HLS fragments under ``ranges``; return slices to assemble.

    yt-dlp's own section download hands the network to ffmpeg, which bypasses the
    byte budget, so fragments are fetched here through the same bounded client.
    Raises ``_NoHlsSections`` when the rendition is not plain HLS; the caller
    then downloads the whole file as before.
    """
    if not info.get("formats") and not info.get("url"):
        raise _NoHlsSections("no formats to choose from")
    work_dir.mkdir(parents=True, exist_ok=True)
    with bounded_client(_ydl_options(provider, work_dir / "unused.%(ext)s", session=session)) as ydl:
        try:
            selected = ydl.process_ie_result(copy.deepcopy(info), download=False)
        except Exception as exc:
            check_transfer()
            raise _NoHlsSections(f"format selection failed: {_redact(exc)}") from exc
        url = selected.get("url")
        if selected.get("requested_formats") or not url or selected.get("protocol") not in {"m3u8", "m3u8_native"}:
            raise _NoHlsSections("selected rendition is not a single HLS stream")
        headers = dict(selected.get("http_headers") or {})
        playlist = _read_url(ydl, url, headers).decode("utf-8", "replace")
        segments = _parse_media_playlist(playlist, url)
        spans = _segment_spans(segments, ranges)
        jobs = []
        for number, (first, last) in enumerate(spans):
            for index in range(first, last + 1):
                jobs.append((segments[index].uri, work_dir / f"span-{number:03d}-{index:06d}.part"))
        init_files = {
            init: work_dir / f"init-{number:03d}.part"
            for number, init in enumerate(sorted({segments[first].init for first, _last in spans if segments[first].init}))
        }
        jobs.extend(init_files.items())
        logger.info(
            "Stream HLS sections provider=%s spans=%d fragments=%d of %d",
            provider, len(spans), len(jobs) - len(init_files), len(segments),
        )
        failed = threading.Event()

        def fetch(uri: str, dest: Path) -> None:
            if failed.is_set():
                return
            try:
                _read_url(ydl, uri, headers, dest)
            except BaseException:
                failed.set()
                raise

        with ThreadPoolExecutor(max_workers=_HLS_SECTION_WORKERS) as pool:
            # Each worker thread needs this call's transfer scope (a ContextVar).
            futures = [pool.submit(contextvars.copy_context().run, fetch, uri, dest) for uri, dest in jobs]
            for future in futures:
                future.result()

    pieces: list[CachedPiece] = []
    for number, (first, last) in enumerate(spans):
        init = segments[first].init
        span_path = work_dir / f"span-{number:03d}{'.mp4' if init else '.ts'}"
        with span_path.open("wb") as handle:
            parts = ([init_files[init]] if init else []) + [
                work_dir / f"span-{number:03d}-{index:06d}.part" for index in range(first, last + 1)
            ]
            for part in parts:
                with part.open("rb") as source:
                    shutil.copyfileobj(source, handle, _HLS_CHUNK_BYTES)
                if part not in init_files.values():
                    part.unlink()
        start, end = segments[first].start, segments[last].end
        actual, wanted = _probe(span_path), end - start
        if actual is None or abs(actual - wanted) > max(_SECTION_TOLERANCE_SECONDS, wanted * 0.02):
            raise RuntimeError(f"section {start:.1f}-{end:.1f}s came back {actual or 0:.1f}s long; refusing an inaccurate cut")
        pieces.append(CachedPiece(start, end, span_path))
    slices = []
    for a, b in ranges:
        piece = next(p for p in pieces if p.start <= a + 1e-6 and b <= p.end + 1e-6)
        slices.append((piece, a, b))
    return slices


def _ytdlp_media(url, provider, state: _Operation, attempt_dir, call_dir, template, *, audio, ranges, max_duration, session):
    attempts = 2 if provider == "direct_retry" else 1
    for attempt in range(attempts):
        try:
            info = _extract(url, "direct" if provider == "direct_retry" else provider, session=session)
            duration = _number(info.get("duration"))
            if duration and not state.expected_duration:
                state.expected_duration = duration
            # Enforce the plan cap before any media is downloaded.
            _check_cap(duration or state.expected_duration, max_duration)
            wanted = _clamp_ranges(ranges, duration or state.expected_duration)
            if ranges and state.local_media is not None and state.local_media.exists():
                return _from_local(state.local_media, call_dir, audio=False, ranges=wanted)
            if ranges:
                # Fetch only the fragments under the clips. Downloading a long
                # VOD whole takes longer than the transfer allows and can exceed
                # the byte budget (a 1-hour 1080p60 Twitch VOD is ~3.3 GB).
                try:
                    slices = _hls_section_pieces(info, provider, attempt_dir / "hls", wanted, session=session)
                except _NoHlsSections as exc:
                    logger.info("Stream provider %s downloading the whole file: %s", provider, exc)
                    shutil.rmtree(attempt_dir / "hls", ignore_errors=True)
                else:
                    dest = call_dir / "selected-segments.mkv"
                    assemble_timeline(slices, dest)
                    return dest
            full_template = attempt_dir / "source.%(ext)s"
            _download_info(info, provider, full_template, audio=audio, ranges=None, session=session)
            result = _finish(attempt_dir, call_dir, None)
            if not audio:
                state.local_media = result
            return _from_local(result, call_dir, audio=False, ranges=wanted) if ranges else result
        except (ProviderSkipped, StreamSourceError):
            raise
        except Exception as exc:
            if state.budget is not None:
                state.budget.check()
            if attempt + 1 == attempts or _is_private_error(exc):
                raise
            logger.info("Stream provider %s retrying download after: %s", provider, _redact(exc))
            shutil.rmtree(attempt_dir, ignore_errors=True)
            attempt_dir.mkdir(parents=True, exist_ok=True)
            time.sleep(_DIRECT_RETRY_BACKOFF_SECONDS)
    raise RuntimeError("provider returned no media file")  # pragma: no cover


# ---------------------------------------------------------------------- Apify

def _actor_items(actor, run_input, charge, timeout_secs=None):
    c = get_config()
    if not c.apify_stream_enabled or not c.apify_api_token:
        raise ProviderSkipped("Apify token/provider is not configured")
    try:
        from apify_client import ApifyClient

        client = ApifyClient(c.apify_api_token)
        run = client.actor(actor).call(
            run_input=run_input,
            timeout_secs=int(timeout_secs or c.apify_stream_timeout_seconds),
            memory_mbytes=256,
            max_total_charge_usd=Decimal(str(charge)),
        )
        if not run or str(run.get("status", "")).upper() != "SUCCEEDED":
            raise RuntimeError(f"actor run failed: {(run or {}).get('status', 'no result')}")
        return list(client.dataset(run["defaultDatasetId"]).iterate_items())
    except ProviderSkipped:
        raise
    except Exception as exc:
        raise RuntimeError(f"Apify actor failed: {_redact(exc)}") from exc


def _duration(item: dict, provider: str) -> Optional[float]:
    for key in ("lengthSeconds", "durationSeconds", "durationSec", "duration"):
        value = _number(item.get(key))
        if value is not None:
            return value / 1000 if provider == "apify_kick_mp4" else value
    return None


def _matches(requested: str, item: dict, keys: Iterable[str] = ("id",)) -> bool:
    """Match the actor's own platform id. Echoed input URLs prove nothing."""
    wanted = requested.strip().lower().removeprefix("v")
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip().lower().removeprefix("v") == wanted:
            return True
    return False


def _apify_info(url: str, provider: str, expected_duration: Optional[float] = None, timeout_secs: Optional[float] = None) -> dict:
    source, kind, source_id = detect_stream_source(url)
    c = get_config()
    if provider == "apify_twitch":
        if source != "twitch":
            raise ProviderSkipped("not a Twitch URL")
        if kind == "clip":
            raise ProviderSkipped("Twitch clip media returned by this actor is unauthorized")
        items = _actor_items(c.apify_twitch_actor, {"mode": "url", "urls": [url], "maxResults": 1, "maxPages": 1, "includeMediaUrls": True}, .10, timeout_secs)
        valid = [x for x in items if x.get("recordType") == "vod_metadata" and x.get("vodHlsUrl")]
        media_key, require_id = "vodHlsUrl", True
    elif provider == "apify_kick_hls":
        if source != "kick":
            raise ProviderSkipped("not a Kick URL")
        items = _actor_items(c.apify_kick_hls_actor, {"urls": [url], "maxItems": 1}, .10, timeout_secs)
        valid = [x for x in items if x.get("status") == "ready" and x.get("hlsUrl")]
        media_key, require_id = "hlsUrl", True
    elif provider == "apify_kick_mp4":
        if source != "kick":
            raise ProviderSkipped("not a Kick URL")
        if not expected_duration:
            # Its output carries no platform id, so a known length is the only
            # way to prove the file is the requested video.
            raise RuntimeError("full-video actor needs a known duration to verify its result")
        cap = max(100, min(c.apify_kick_max_video_size_mb, math.ceil(expected_duration / 60 * 30)))
        items = _actor_items(c.apify_kick_mp4_actor, {"startUrls": [{"url": url}], "quality": "720p60", "maxVideoSizeMb": cap}, 2.0, timeout_secs)
        valid = [x for x in items if x.get("status") == "success" and x.get("download_url")]
        media_key, require_id = "download_url", False
    else:
        raise ProviderSkipped(f"{provider} is not an Apify provider")
    if not valid:
        raise RuntimeError("actor returned no ready media")
    item = valid[0]
    if (require_id or item.get("id") is not None) and not _matches(source_id, item):
        raise RuntimeError("actor result id does not match requested video")
    duration = _duration(item, provider)
    if expected_duration and duration and not _close(duration, expected_duration):
        raise RuntimeError("actor result duration does not match requested video")
    if provider == "apify_kick_mp4" and not duration:
        raise RuntimeError("full-video actor returned no duration to verify")
    return {"id": source_id, "title": item.get("title"), "duration": duration, "media_url": item[media_key], "provider": provider}


def _stream_download(response, destination: Path, max_bytes: int, budget=None) -> None:
    response.raise_for_status()
    total = 0
    with destination.open("wb") as handle:
        for chunk in response.iter_content(1024 * 1024):
            total += len(chunk)
            if budget is not None:
                account_chunk(chunk)
            if total > max_bytes:
                raise RuntimeError("Video download exceeds the configured byte budget")
            handle.write(chunk)


def _parsebird_file(info: dict, state: _Operation, op_dir: Path) -> Path:
    """Download parsebird's MPEG-TS once per operation and remux it to MP4."""
    if state.local_media is not None and state.local_media.exists():
        return state.local_media
    c = get_config()
    shared = op_dir / "_shared"
    shared.mkdir(parents=True, exist_ok=True)
    raw, remuxed = shared / "parsebird.ts", shared / "parsebird.mp4"
    try:
        with requests.get(info["media_url"], stream=True, timeout=(10, min(10, c.apify_stream_timeout_seconds))) as response:
            _stream_download(response, raw, c.youtube_download_max_bytes, state.budget)
        result = subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-i", str(raw), "-map", "0", "-c", "copy", "-movflags", "+faststart", str(remuxed)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not remuxed.exists():
            raise RuntimeError("could not remux the downloaded MPEG-TS file")
        actual = _probe(remuxed)
        if not actual:
            raise RuntimeError("downloaded media has no readable duration")
        if not _close(actual, info.get("duration")) or not _close(actual, state.expected_duration):
            raise RuntimeError("downloaded media duration does not match the requested video")
    except Exception:
        shutil.rmtree(shared, ignore_errors=True)
        raise
    finally:
        raw.unlink(missing_ok=True)
    state.local_media = remuxed
    return remuxed


def _from_local(source: Path, call_dir: Path, *, audio: bool, ranges) -> Path:
    if ranges:
        total = _probe(source)
        wanted = _clamp_ranges(ranges, total)
        dest = call_dir / "selected-segments.mkv"
        # Cut from the full remux with re-encoding (frame-accurate), not -c copy.
        assemble_timeline([(CachedPiece(0.0, total or wanted[-1][1], source), a, b) for a, b in wanted], dest)
        return dest
    if audio:
        output = call_dir / "source.mp3"
        result = subprocess.run(
            ["ffmpeg", "-y", "-nostdin", "-i", str(source), "-vn", "-codec:a", "libmp3lame", str(output)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not output.exists():
            raise RuntimeError("could not extract audio from the downloaded video")
        return output
    return source


def _actor_media(url, provider, state: _Operation, op_dir, call_dir, attempt_dir, template, *, audio, ranges, max_duration, session):
    info = state.actor_results.get(provider)
    if info is None:
        if provider in state.actors_run:
            raise RuntimeError("actor already failed for this task; not running it again")
        state.actors_run.add(provider)
        info = _apify_info(url, provider, state.expected_duration)
        state.actor_results[provider] = info
    reported = info.get("duration")
    if reported and not state.expected_duration:
        state.expected_duration = reported
    _check_cap(reported or state.expected_duration, max_duration)
    if provider == "apify_kick_mp4":
        return _from_local(_parsebird_file(info, state, op_dir), call_dir, audio=audio, ranges=ranges)

    media = info["media_url"]
    actual = reported or state.expected_duration
    if not _close(actual, reported) or not _close(actual, state.expected_duration):
        raise RuntimeError("stream duration does not match the requested video")
    _check_cap(actual, max_duration)
    wanted = _clamp_ranges(ranges, actual or reported or state.expected_duration)
    # Actor media URLs are fetched directly first; the proxy only if that fails.
    try:
        _download_url(media, "direct", attempt_dir / "source.%(ext)s", audio=audio, ranges=None, session=session)
    except Exception as direct_exc:
        if state.budget is not None:
            state.budget.check()
        shutil.rmtree(attempt_dir, ignore_errors=True)
        attempt_dir.mkdir(parents=True, exist_ok=True)
        try:
            _download_url(media, "proxy", attempt_dir / "source.%(ext)s", audio=audio, ranges=None, session=session)
        except ProviderSkipped:
            # No proxy configured: report the real direct-download error.
            raise direct_exc
        except Exception as proxy_exc:
            raise RuntimeError(f"direct: {_redact(direct_exc)}; proxy: {_redact(proxy_exc)}") from proxy_exc
    result = _finish(attempt_dir, call_dir, None)
    if not _close(_probe(result), actual):
        raise RuntimeError("stream duration does not match the requested video")
    if not audio:
        state.local_media = result
    return _from_local(result, call_dir, audio=False, ranges=wanted) if ranges else result


# ------------------------------------------------------------------ public API

def fetch_stream_info(url: str, *, deadline_seconds: float = PREFLIGHT_DEADLINE_SECONDS, allow_actors: bool = True) -> dict:
    """Cheap metadata preflight bounded by an overall deadline.

    Tries yt-dlp (proxy, direct) and, if those fail, the cheap metadata actors
    so Kick and Twitch VOD fallbacks stay reachable during a proxy outage. The
    returned actor result is persisted with the task so the worker reuses it
    instead of running the actor again. parsebird never runs here.
    """
    source, _kind, source_id = detect_stream_source(url)
    deadline = time.monotonic() + deadline_seconds
    failures: list[str] = []
    no_duration = False
    for provider in _provider_order(source):
        if provider in APIFY_PROVIDERS and (not allow_actors or provider not in PREFLIGHT_ACTORS):
            continue
        remaining = deadline - time.monotonic()
        needed = _MIN_PREFLIGHT_ACTOR_SECONDS if provider in APIFY_PROVIDERS else 1.0
        if remaining < needed:
            failures.append(f"{provider}: preflight deadline reached")
            break
        try:
            if provider in APIFY_PROVIDERS:
                info = _apify_info(url, provider, timeout_secs=max(_MIN_PREFLIGHT_ACTOR_SECONDS, remaining))
            else:
                info = _extract(url, provider, session=f"preflight{source_id}", deadline=deadline)
            duration = _number(info.get("duration"))
            if not duration or duration <= 0:
                raise _NoDuration("provider returned no duration")
            result = {"id": source_id, "title": info.get("title"), "duration": duration, "provider": provider}
            if provider in APIFY_PROVIDERS:
                result["media_url"] = info["media_url"]
            logger.info("Stream metadata fetched provider=%s source=%s", provider, source)
            return result
        except ProviderSkipped as exc:
            failures.append(f"{provider}: skipped ({exc})")
            logger.info("Stream metadata provider unavailable provider=%s source=%s: %s", provider, source, exc)
        except Exception as exc:
            if provider in YTDLP_PROVIDERS and _is_private_error(exc):
                raise StreamSourceError(PRIVATE_VOD_MESSAGE) from exc
            no_duration = no_duration or isinstance(exc, _NoDuration)
            failures.append(f"{provider}: {_redact(exc)}")
            logger.warning("Stream metadata provider failed provider=%s source=%s: %s", provider, source, _redact(exc))
    logger.error("All stream metadata providers failed source=%s failures=%s", source, "; ".join(failures))
    if no_duration:
        raise StreamDurationUnavailableError(FETCH_FAILED_MESSAGE)
    raise StreamSourceError(FETCH_FAILED_MESSAGE)


def seed_stream_resolution(task_id: Optional[str], resolution: Optional[dict] = None, duration: Any = None, operation: str = PROCESSING_OPERATION) -> None:
    """Prime the worker with what the API preflight already learned."""
    if not task_id:
        return
    state = _operation(_safe_component(task_id), _safe_component(operation))
    known = _number(duration) or _number((resolution or {}).get("duration"))
    if known:
        state.expected_duration = known
    resolved_at = _number((resolution or {}).get("resolved_at"))
    fresh = resolved_at is None or time.time() - resolved_at < _SEEDED_MEDIA_TTL_SECONDS
    if fresh and isinstance(resolution, dict) and resolution.get("provider") in APIFY_PROVIDERS and resolution.get("media_url"):
        provider = resolution["provider"]
        state.actor_results[provider] = dict(resolution)
        state.actors_run.add(provider)


def _download_unbounded(url: str, *, audio=False, ranges=None, expected_duration=None, max_duration=None, task_id=None, operation=None) -> Path:
    source, kind, _source_id = detect_stream_source(url)
    task_key = _safe_component(task_id) if task_id else f"unscoped-{uuid.uuid4().hex}"
    op_key = _safe_component(operation) if operation else f"edit-{uuid.uuid4().hex[:12]}"
    state = _operation(task_key, op_key)
    if _number(expected_duration):
        state.expected_duration = _number(expected_duration)
    if state.failed_until > time.monotonic():
        # Every provider already failed in this operation; don't pay for it again.
        raise StreamSourceError(FETCH_FAILED_MESSAGE)
    op_dir = _work_root() / task_key / op_key
    call_dir = op_dir / uuid.uuid4().hex[:12]
    call_dir.mkdir(parents=True, exist_ok=True)
    providers = _provider_order(source)
    if state.preferred in providers:
        providers = providers[providers.index(state.preferred):]
    failures: list[str] = []
    for provider in providers:
        attempt_dir = call_dir / provider
        attempt_dir.mkdir(exist_ok=True)
        template = attempt_dir / ("section-%(section_number)03d.%(ext)s" if ranges else "source.%(ext)s")
        try:
            if provider in APIFY_PROVIDERS:
                result = _actor_media(url, provider, state, op_dir, call_dir, attempt_dir, template, audio=audio, ranges=ranges, max_duration=max_duration, session=task_key)
            else:
                result = _ytdlp_media(url, provider, state, attempt_dir, call_dir, template, audio=audio, ranges=ranges, max_duration=max_duration, session=task_key)
            state.preferred = provider
            logger.info("Stream download completed provider=%s source=%s kind=%s", provider, source, kind)
            return result
        except ProviderSkipped as exc:
            shutil.rmtree(attempt_dir, ignore_errors=True)
            failures.append(f"{provider}: skipped ({exc})")
            logger.info("Stream provider unavailable provider=%s source=%s: %s", provider, source, exc)
        except StreamSourceError:
            shutil.rmtree(call_dir, ignore_errors=True)
            raise
        except Exception as exc:
            if state.budget is not None:
                state.budget.check()
            shutil.rmtree(attempt_dir, ignore_errors=True)
            if provider in YTDLP_PROVIDERS and _is_private_error(exc):
                shutil.rmtree(call_dir, ignore_errors=True)
                raise StreamSourceError(PRIVATE_VOD_MESSAGE) from exc
            failures.append(f"{provider}: {_redact(exc)}")
            logger.warning("Stream download provider failed provider=%s source=%s: %s", provider, source, _redact(exc))
    state.failed_until = time.monotonic() + _FAILED_TTL_SECONDS
    shutil.rmtree(call_dir, ignore_errors=True)
    logger.error("All stream download providers failed source=%s kind=%s failures=%s", source, kind, "; ".join(failures))
    raise StreamSourceError(FETCH_FAILED_MESSAGE)


def _download(url: str, *, audio=False, ranges=None, expected_duration=None, max_duration=None, task_id=None, operation=None) -> Path:
    # Persist the allowance across audio, sections, full fallback and repeated calls.
    task_key = task_id or f"unscoped-{uuid.uuid4().hex}"
    op_key = operation or f"edit-{uuid.uuid4().hex[:12]}"
    state = _operation(_safe_component(task_key), _safe_component(op_key))
    if state.budget is None:
        state.budget = TransferBudget(get_config().youtube_download_max_bytes)
    try:
        config = get_config()
        with transfer_scope(
            state.budget,
            getattr(config, "youtube_proxy_timeout_seconds", 300),
            getattr(config, "stream_download_max_seconds", 2400),
        ):
            return _download_unbounded(url, audio=audio, ranges=ranges, expected_duration=expected_duration,
                max_duration=max_duration, task_id=task_key, operation=op_key)
    except TransferStopped as exc:
        raise StreamSourceError(str(exc)) from exc


def download_stream_audio(url, duration=None, task_id=None, *, max_duration=None, operation=None):
    return _download(url, audio=True, expected_duration=duration, max_duration=max_duration, task_id=task_id, operation=operation)


def download_stream_video(url, duration=None, task_id=None, *, max_duration=None, operation=None):
    return _download(url, expected_duration=duration, max_duration=max_duration, task_id=task_id, operation=operation)


def download_stream_sections(url, segments, duration=None, task_id=None, *, max_duration=None, operation=None):
    ranges = merge_ranges(
        ranges_from_segments(segments),
        padding=get_config().youtube_section_padding_seconds,
        duration=duration,
    )
    if not ranges:
        raise StreamSourceError("No valid video sections were selected")
    return _download(url, ranges=ranges, expected_duration=duration, max_duration=max_duration, task_id=task_id, operation=operation)


def clear_stream_task_cache(task_id: Optional[str], operation: Optional[str] = None) -> None:
    if not task_id:
        return
    with _operations_lock:
        for key in [k for k in _operations if k[0] == task_id and (operation is None or k[1] == operation)]:
            _operations.pop(key, None)


def cleanup_stream_task(task_id: Optional[str], operation: str = PROCESSING_OPERATION) -> None:
    """Remove one operation's media (default: the processing job) and its state."""
    if not task_id:
        return
    try:
        task_dir = _work_root() / _safe_component(task_id)
        shutil.rmtree(task_dir / _safe_component(operation), ignore_errors=True)
        try:
            task_dir.rmdir()  # only when no other operation is still using it
        except OSError:
            pass
    except ValueError:
        logger.warning("Refusing to clean an invalid stream work path")
    clear_stream_task_cache(task_id, operation)


def cleanup_stream_path(path: Optional[Path]) -> bool:
    """Clean the operation that produced ``path``. Returns False if not stream media."""
    if path is None:
        return False
    root = _work_root().resolve()
    try:
        relative = Path(path).resolve().relative_to(root)
    except (ValueError, OSError):
        return False
    if len(relative.parts) < 2:
        return False
    cleanup_stream_task(relative.parts[0], relative.parts[1])
    return True
