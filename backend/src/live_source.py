"""Live streams: find a stream that is still going and the recording behind it.

SupoClip clips live streams by processing a *part* of their recording
(``source_range.py``), never by recording on our side:

* **Twitch**: a live channel keeps an in-progress VOD (``twitch.tv/videos/<id>``)
  whose HLS playlist already covers the stream from its first second. It just
  has no ``#EXT-X-ENDLIST`` yet. The existing VOD downloader slices it.
* **Kick**: same shape. The ongoing stream is the newest entry of the channel's
  videos, with a playlist from the start of the stream.
* **YouTube**: the watch URL of the live video. While it is live, the worker
  reads the live DVR by segment number (``youtube_proxy.live_dvr_fragments``);
  once it ends, the same timeline is the finished video.

So a "last 30 minutes" request becomes ``source.start_seconds/end_seconds`` on
the recording, with the range marked ``live``. Only audio of that part and the
clip ranges cross the network, exactly like a part of a long VOD.

Checking whether a channel is live is metadata only: Twitch GraphQL and the Kick
API through yt-dlp, and the YouTube Data API when a key is configured (no proxy
bytes), else yt-dlp through the proxy.
"""
from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from .config import get_config

logger = logging.getLogger(__name__)

LIVE_PLATFORMS = ("youtube", "twitch", "kick")
# Newest seconds of a live recording that may not be published yet.
_EDGE_MARGIN_SECONDS = {"youtube": 30.0, "twitch": 10.0, "kick": 10.0}
# A part needs room for a few clips.
MIN_LIVE_PART_SECONDS = 60.0
# The ongoing Kick VOD starts within seconds of the stream.
_KICK_VOD_START_SLACK_SECONDS = 600.0
_PLAYLIST_MAX_BYTES = 8_000_000
RESOLVE_DEADLINE_SECONDS = 30.0

_TWITCH_HOSTS = {"twitch.tv", "m.twitch.tv", "go.twitch.tv"}
_TWITCH_RESERVED = {"videos", "directory", "search", "settings", "downloads", "jobs", "p", "turbo", "subscriptions", "inventory", "wallet", "drops"}
_KICK_RESERVED = {"video", "videos", "categories", "browse", "search", "following", "dashboard", "settings", "category"}
_YOUTUBE_HOSTS = {"youtube.com", "m.youtube.com", "music.youtube.com"}
_YOUTUBE_CHANNEL_LIVE = re.compile(r"^/(?:@[^/]+|channel/[A-Za-z0-9_-]+|c/[^/]+|user/[^/]+)/live$")
_TWITCH_CHANNEL = re.compile(r"^/([A-Za-z0-9_]{2,25})$")
_KICK_CHANNEL = re.compile(r"^/([A-Za-z0-9_-]{2,40})$")
_EXTINF = re.compile(r"^#EXTINF:([0-9.]+)", re.MULTILINE)


class LiveSourceError(Exception):
    """A user-facing refusal: ``code`` and ``message`` go into the 422 detail."""

    def __init__(self, code: str, message: str, *, status: int = 422, **detail: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail

    def to_detail(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, **self.detail}


def not_live_error(platform: str, channel: str) -> LiveSourceError:
    name = channel or "This channel"
    return LiveSourceError(
        "STREAM_NOT_LIVE",
        f"{name} isn't live right now. Paste a link to a finished video, VOD or clip instead. "
        "No minutes were used.",
        platform=platform,
    )


@dataclass
class LiveStream:
    """A stream that is live now and the recording a part can be cut from."""

    platform: str
    channel_url: str
    channel: str
    recording_url: str
    recording_id: str
    recorded_seconds: float
    title: Optional[str] = None
    started_at: Optional[float] = None
    checked_at: float = field(default_factory=time.time)
    # The stream has ended and ``recorded_seconds`` is the finished recording.
    final: bool = False

    @property
    def edge_seconds(self) -> float:
        """The newest second a part may end on."""
        if self.final:
            return max(0.0, float(self.recorded_seconds))
        margin = _EDGE_MARGIN_SECONDS.get(self.platform, 15.0)
        return max(0.0, float(self.recorded_seconds) - margin)

    def public(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "channel": self.channel,
            "channel_url": self.channel_url,
            "title": self.title,
            "elapsed_seconds": int(self.recorded_seconds),
            "started_at": int(self.started_at) if self.started_at else None,
        }


# ---------------------------------------------------------------------- URLs

def _host_and_path(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(str(url).strip())
    except (TypeError, ValueError):
        return "", ""
    if parsed.scheme not in {"http", "https"} or parsed.username or parsed.password:
        return "", ""
    host = (parsed.hostname or "").lower().removeprefix("www.")
    return host, parsed.path.rstrip("/") or "/"


def live_channel_url(url: str) -> Optional[tuple[str, str]]:
    """``(platform, canonical_url)`` for a URL that can only mean a live stream.

    A Twitch or Kick channel page and a YouTube channel's ``/live`` page. A
    YouTube watch URL can also be live, but only metadata can tell.
    """
    host, path = _host_and_path(url)
    if host in _TWITCH_HOSTS:
        match = _TWITCH_CHANNEL.fullmatch(path)
        if match and match.group(1).lower() not in _TWITCH_RESERVED:
            return "twitch", f"https://www.twitch.tv/{match.group(1).lower()}"
        return None
    if host == "kick.com":
        match = _KICK_CHANNEL.fullmatch(path)
        if match and match.group(1).lower() not in _KICK_RESERVED and "clip=" not in str(url):
            return "kick", f"https://kick.com/{match.group(1).lower()}"
        return None
    if host in _YOUTUBE_HOSTS and _YOUTUBE_CHANNEL_LIVE.fullmatch(path):
        return "youtube", f"https://www.youtube.com{path}"
    return None


def is_youtube_url(url: str) -> bool:
    from .youtube_utils import get_youtube_video_id

    host, _path = _host_and_path(url)
    return bool(host in _YOUTUBE_HOSTS | {"youtu.be"} and (get_youtube_video_id(url) or live_channel_url(url)))


def platform_enabled(platform: str) -> bool:
    return platform in get_config().live_clipping_platforms


# ------------------------------------------------------------------ windows

@dataclass(frozen=True)
class LiveWindow:
    start: int
    end: float
    billed_seconds: float
    fresh_after: Optional[float] = None


def window_for(stream: LiveStream, *, window_seconds: Optional[float] = None,
               from_seconds: Optional[float] = None, max_seconds: Optional[float] = None) -> LiveWindow:
    """The part of the recording to clip: the newest ``window_seconds``, or from a second.

    ``from_seconds`` is used by continuous clipping: the next part starts where
    the previous one ended, minus a short overlap (``fresh_after`` marks where new
    material begins, and only that is billed).
    """
    end = stream.edge_seconds
    if from_seconds is not None:
        fresh_after = max(0.0, float(from_seconds))
        overlap = float(get_config().live_follow_overlap_seconds)
        start = max(0.0, fresh_after - overlap)
        if max_seconds and end - start > max_seconds:
            end = start + float(max_seconds)
        billed = max(0.0, end - fresh_after)
    else:
        fresh_after = None
        wanted = float(window_seconds or 1800)
        if max_seconds:
            wanted = min(wanted, float(max_seconds))
        start = max(0.0, end - wanted)
        billed = end - start
    start_whole = int(math.floor(start))
    if end - start_whole < MIN_LIVE_PART_SECONDS:
        wait = int(math.ceil(MIN_LIVE_PART_SECONDS - (end - start_whole)))
        raise LiveSourceError(
            "LIVE_TOO_SHORT",
            f"There isn't a full minute of this stream to clip yet. Try again in about {max(wait, 30)} seconds. "
            "No minutes were used.",
            retry_after_seconds=max(wait, 30),
        )
    return LiveWindow(start_whole, round(end, 3), round(billed, 3), fresh_after)


# --------------------------------------------------------------- resolution

def resolve_live_stream(url: str, *, deadline_seconds: float = RESOLVE_DEADLINE_SECONDS) -> LiveStream:
    """The live stream behind ``url`` and its recording, or ``LiveSourceError``.

    Raises ``STREAM_NOT_LIVE`` when the channel or video is not live (a YouTube
    watch URL of a finished video included), ``LIVE_NO_RECORDING`` when the
    platform keeps no recording to cut from, ``LIVE_UNAVAILABLE`` otherwise.
    """
    deadline = time.monotonic() + deadline_seconds
    channel = live_channel_url(url)
    platform = channel[0] if channel else ("youtube" if is_youtube_url(url) else None)
    if platform is None:
        raise LiveSourceError("LIVE_UNSUPPORTED", "Paste a YouTube, Twitch or Kick live stream link.")
    if not platform_enabled(platform):
        raise LiveSourceError(
            "LIVE_PLATFORM_UNAVAILABLE",
            f"Clipping live {platform.title()} streams isn't available yet. Use the finished video instead.",
            platform=platform,
        )
    try:
        if platform == "twitch":
            return _resolve_twitch(channel[1], deadline)
        if platform == "kick":
            return _resolve_kick(channel[1], deadline)
        return _resolve_youtube(channel[1] if channel else url, deadline)
    except LiveSourceError:
        raise
    except Exception as exc:  # noqa: BLE001 - provider failures become one public message
        logger.warning("live_resolve_failed platform=%s error=%s", platform, _redact(exc))
        raise LiveSourceError(
            "LIVE_UNAVAILABLE",
            "We couldn't reach this live stream right now. Try again in a minute. No minutes were used.",
            status=503, platform=platform,
        ) from None


def _redact(exc: BaseException) -> str:
    from .stream_source import _redact as redact

    return redact(exc)[:300]


def _not_live_message(exc: BaseException) -> bool:
    text = str(exc).lower()
    return any(marker in text for marker in (
        "not currently live", "is offline", "not live", "user not live", "this live event will begin",
        "premieres in", "live event has ended", "does not exist",
    ))


def _stream_extract(url: str, platform: str, deadline: float, **extra: Any) -> dict:
    """yt-dlp metadata through the platform's configured order (proxy, direct)."""
    from .stream_source import (
        ProviderSkipped, YTDLP_PROVIDERS, _provider_order, _socket_timeout, _ydl_options,
    )
    from .stream_transfer import bounded_client

    last: Optional[BaseException] = None
    for provider in _provider_order(platform):
        if provider not in YTDLP_PROVIDERS:
            continue
        if time.monotonic() >= deadline - 1:
            break
        try:
            options = _ydl_options(
                "direct" if provider == "direct_retry" else provider, Path("metadata"),
                session=f"live{platform}", socket_timeout=_socket_timeout(deadline),
            )
            options.update(extra)
            with bounded_client(options) as ydl:
                info = ydl.extract_info(url, download=False, process=False)
            if not info:
                raise RuntimeError("provider returned no metadata")
            return info
        except ProviderSkipped:
            continue
        except Exception as exc:  # noqa: BLE001 - try the next provider
            if _not_live_message(exc):
                raise LiveSourceError("STREAM_NOT_LIVE", "not live") from None
            last = exc
    raise RuntimeError(f"no metadata provider answered: {_redact(last) if last else 'none configured'}")


def _resolve_twitch(channel_url: str, deadline: float) -> LiveStream:
    login = channel_url.rsplit("/", 1)[-1]
    try:
        live = _stream_extract(channel_url, "twitch", deadline)
    except LiveSourceError:
        raise not_live_error("twitch", login) from None
    if not live.get("is_live"):
        raise not_live_error("twitch", login)
    started_at = _number(live.get("timestamp"))
    # yt-dlp's live_from_start resolves a live channel to its in-progress VOD
    # (only when that VOD started with this stream).
    try:
        entry = _stream_extract(channel_url, "twitch", deadline, live_from_start=True)
    except LiveSourceError:
        raise not_live_error("twitch", login) from None
    vod_url = entry.get("url") if entry.get("_type") == "url" else None
    match = re.search(r"/videos/(\d+)", str(vod_url or ""))
    if not match:
        raise LiveSourceError(
            "LIVE_NO_RECORDING",
            f"{live.get('uploader') or login} doesn't save past broadcasts on Twitch, so there's no recording "
            "of this stream to clip yet. Clip the VOD after the stream, or upload your own recording. "
            "No minutes were used.",
            platform="twitch",
        )
    vod_id = match.group(1)
    vod_url = f"https://www.twitch.tv/videos/{vod_id}"
    vod = _stream_extract(vod_url, "twitch", deadline)
    recorded = _number(vod.get("duration"))
    if not recorded:
        raise RuntimeError("in-progress Twitch VOD reported no length")
    return LiveStream(
        platform="twitch", channel_url=channel_url, channel=live.get("uploader") or login,
        recording_url=vod_url, recording_id=vod_id, recorded_seconds=recorded,
        title=live.get("description") or vod.get("title") or live.get("title"),
        started_at=started_at,
    )


def _resolve_kick(channel_url: str, deadline: float) -> LiveStream:
    slug = channel_url.rsplit("/", 1)[-1]
    try:
        live = _stream_extract(channel_url, "kick", deadline)
    except LiveSourceError:
        raise not_live_error("kick", slug) from None
    if live.get("live_status") not in (None, "is_live") or live.get("is_live") is False:
        raise not_live_error("kick", slug)
    started_at = _number(live.get("timestamp"))
    listing = _stream_extract(f"{channel_url}/videos", "kick", deadline, extract_flat=True, playlist_items="1-3")
    entries = list(listing.get("entries") or [])[:3]
    vod_url = None
    for entry in entries:
        stamp = _number(entry.get("timestamp"))
        if stamp and started_at and abs(stamp - started_at) <= _KICK_VOD_START_SLACK_SECONDS:
            vod_url = str(entry.get("url") or "").split("#", 1)[0]
            break
    match = re.search(r"/videos/([0-9a-fA-F-]{36})", vod_url or "")
    if not match:
        raise LiveSourceError(
            "LIVE_NO_RECORDING",
            f"Kick isn't keeping a recording of {live.get('uploader') or slug}'s stream that we can reach yet. "
            "Try again in a few minutes, or clip the VOD after the stream. No minutes were used.",
            platform="kick",
        )
    vod_id = match.group(1)
    vod_url = f"https://kick.com/{slug}/videos/{vod_id}"
    vod = _stream_extract(vod_url, "kick", deadline)
    recorded = _number(vod.get("duration")) or _playlist_seconds(vod, deadline)
    if not recorded:
        raise RuntimeError("in-progress Kick VOD reported no length")
    return LiveStream(
        platform="kick", channel_url=channel_url, channel=live.get("uploader") or live.get("channel") or slug,
        recording_url=vod_url, recording_id=vod_id, recorded_seconds=recorded,
        title=live.get("title") or vod.get("title"), started_at=started_at,
    )


def resolve_recording_length(platform: str, recording_url: str, recording_id: str,
                             *, deadline_seconds: float = RESOLVE_DEADLINE_SECONDS) -> Optional[float]:
    """Length of a stream's recording once the stream has ended, or None while it isn't ready.

    Twitch and Kick finish the same VOD. YouTube reports the broadcast's start and
    end through the Data API, or a duration once the video has processed.
    """
    deadline = time.monotonic() + deadline_seconds
    if platform in ("twitch", "kick"):
        info = _stream_extract(recording_url, platform, deadline)
        if info.get("live_status") == "is_live" or info.get("is_live") is True:
            return None
        return _number(info.get("duration")) or (_playlist_seconds(info, deadline) if platform == "kick" else None)
    details = _youtube_data_api_times(recording_id, deadline)
    if details is not None:
        start, end = details
        return end - start if start and end and end > start else None
    info = _youtube_ytdlp(f"https://www.youtube.com/watch?v={recording_id}", deadline)
    if info.get("live_status") in ("is_live", "post_live"):
        return None
    return _number(info.get("duration"))


def _youtube_data_api_times(video_id: str, deadline: float) -> Optional[tuple[Optional[float], Optional[float]]]:
    from .youtube_utils import YOUTUBE_DATA_API_URL

    api_key = get_config().resolve_youtube_data_api_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            YOUTUBE_DATA_API_URL,
            params={"part": "liveStreamingDetails", "id": video_id, "key": api_key,
                    "fields": "items(liveStreamingDetails(actualStartTime,actualEndTime))"},
            timeout=max(1.0, min(10.0, deadline - time.monotonic())),
        )
        response.raise_for_status()
        items = response.json().get("items") or []
    except Exception as exc:  # noqa: BLE001
        logger.info("YouTube Data API recording length unavailable: %s", _redact(exc))
        return None
    details = (items[0].get("liveStreamingDetails") or {}) if items else {}
    return _iso_seconds(details.get("actualStartTime")), _iso_seconds(details.get("actualEndTime"))


def _playlist_seconds(info: dict, deadline: float) -> Optional[float]:
    """Length of an in-progress HLS recording: the sum of its segments so far."""
    formats = [f for f in (info.get("formats") or []) if "m3u8" in str(f.get("protocol") or "") and f.get("url")]
    if not formats:
        return None
    smallest = min(formats, key=lambda f: (f.get("tbr") or f.get("height") or 1e9))
    timeout = max(1.0, min(10.0, deadline - time.monotonic()))
    with requests.get(smallest["url"], headers=smallest.get("http_headers") or {}, timeout=timeout, stream=True) as response:
        response.raise_for_status()
        payload = response.raw.read(_PLAYLIST_MAX_BYTES + 1, decode_content=True)
    if len(payload) > _PLAYLIST_MAX_BYTES:
        return None
    total = sum(float(value) for value in _EXTINF.findall(payload.decode("utf-8", "replace")))
    return total if total > 0 else None


def _resolve_youtube(url: str, deadline: float) -> LiveStream:
    from .youtube_utils import get_youtube_video_id

    video_id = get_youtube_video_id(url)
    info: Optional[dict] = None
    if not video_id:
        # A channel's /live page: resolve which video is live (one page through the proxy).
        info = _youtube_ytdlp(url, deadline)
        video_id = info.get("id") if isinstance(info, dict) else None
        if not video_id:
            raise not_live_error("youtube", "")
    details = _youtube_data_api_live(video_id, deadline)
    if details is None:
        info = info if info and info.get("live_status") else _youtube_ytdlp(
            f"https://www.youtube.com/watch?v={video_id}", deadline,
        )
        status = info.get("live_status")
        if status != "is_live" and info.get("is_live") is not True:
            raise not_live_error("youtube", info.get("channel") or info.get("uploader") or "")
        details = {
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader") or "",
            "started_at": _number(info.get("release_timestamp")) or _number(info.get("timestamp")),
        }
    started_at = details.get("started_at")
    if not started_at:
        raise RuntimeError("live YouTube video reported no start time")
    recorded = max(0.0, time.time() - started_at)
    return LiveStream(
        platform="youtube", channel_url=f"https://www.youtube.com/watch?v={video_id}",
        channel=details.get("channel") or "", recording_url=f"https://www.youtube.com/watch?v={video_id}",
        recording_id=video_id, recorded_seconds=recorded, title=details.get("title"), started_at=started_at,
    )


def _youtube_data_api_live(video_id: str, deadline: float) -> Optional[dict]:
    """Live status from the YouTube Data API (one quota unit, no proxy). None when unavailable."""
    from .youtube_utils import YOUTUBE_DATA_API_URL

    api_key = get_config().resolve_youtube_data_api_key()
    if not api_key:
        return None
    try:
        response = requests.get(
            YOUTUBE_DATA_API_URL,
            params={
                "part": "snippet,liveStreamingDetails", "id": video_id, "key": api_key,
                "fields": "items(id,snippet(title,channelTitle,liveBroadcastContent),"
                          "liveStreamingDetails(actualStartTime,actualEndTime))",
            },
            timeout=max(1.0, min(10.0, deadline - time.monotonic())),
        )
        response.raise_for_status()
        items = response.json().get("items") or []
    except Exception as exc:  # noqa: BLE001 - a Gemini-only key, quota, network: fall back to yt-dlp
        logger.info("YouTube Data API live check unavailable: %s", _redact(exc))
        return None
    if not items:
        raise not_live_error("youtube", "")
    snippet = items[0].get("snippet") or {}
    details = items[0].get("liveStreamingDetails") or {}
    if snippet.get("liveBroadcastContent") != "live" or details.get("actualEndTime"):
        raise not_live_error("youtube", snippet.get("channelTitle") or "")
    return {
        "title": snippet.get("title"),
        "channel": snippet.get("channelTitle") or "",
        "started_at": _iso_seconds(details.get("actualStartTime")),
    }


def _youtube_ytdlp(url: str, deadline: float) -> dict:
    from .youtube_proxy import base_proxy_params, extract_video_info, proxy_configured

    if proxy_configured():
        options = base_proxy_params(None, "live")
    else:
        options = {"quiet": True, "no_warnings": True, "noplaylist": True, "socket_timeout": 20}
    options["_youtube_deadline"] = deadline
    try:
        info = extract_video_info(url, options)
    except Exception as exc:
        if _not_live_message(exc):
            raise not_live_error("youtube", "") from None
        raise
    if info.get("_type") in ("url", "url_transparent") and info.get("url") and info.get("url") != url:
        return _youtube_ytdlp(info["url"], deadline)
    return info


def _iso_seconds(value: Any) -> Optional[float]:
    if not value:
        return None
    from datetime import datetime

    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None
