"""yt-dlp downloads through a configurable residential proxy.

Section downloads stay on yt-dlp's native HTTP client. ``--download-sections``
hands the media URL to ffmpeg, and ffmpeg does not route SOCKS proxies and
mishandles authenticated HTTP proxies, so the media would leave from the
server IP instead of the proxy.
"""

from __future__ import annotations

import logging
import math
import re
import shutil
import subprocess
import threading
import time
from contextlib import contextmanager
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

from .config import get_config

logger = logging.getLogger(__name__)

YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY = "yt_dlp_proxy"
_ATTEMPT_LOG_KEYS = (
    "youtube_provider",
    "youtube_video_id",
    "youtube_download_kind",
    "youtube_outcome",
    "youtube_bytes",
    "task_id",
)


class YouTubeDownloadError(RuntimeError):
    """A YouTube download attempt failed."""


class DownloadBudgetExceeded(YouTubeDownloadError):
    """The per-job byte limit was crossed."""


class PartialDownloadUnavailable(YouTubeDownloadError):
    """The formats cannot be sliced without an ffmpeg network fetch."""


class DownloadDeadlineExceeded(YouTubeDownloadError):
    """One proxy attempt expired; independent providers may still be tried."""


def _check_deadline(options: dict) -> float:
    deadline = options.get("_youtube_deadline")
    remaining = deadline - time.monotonic() if deadline is not None else 20.0
    if remaining <= 0:
        raise DownloadDeadlineExceeded("Residential proxy download attempt timed out")
    return remaining


@contextmanager
def _proxy_client(options: dict):
    import yt_dlp
    from yt_dlp.networking import Request

    _check_deadline(options)
    with yt_dlp.YoutubeDL(options) as client:
        if "_youtube_deadline" in options:
            original_open = client.urlopen

            def bounded_open(request):
                remaining = _check_deadline(options)
                if isinstance(request, str):
                    request = Request(request)
                # Bound each socket operation by the remaining attempt time.
                request.extensions["timeout"] = min(20.0, remaining)
                return original_open(request)

            client.urlopen = bounded_open
        yield client
        _check_deadline(options)


class ByteBudget:
    """Shared byte counter for one job (audio, sections, and fallbacks)."""

    def __init__(self, limit: int):
        self.limit = max(0, int(limit))
        self.total = 0
        self._lock = threading.Lock()

    def add(self, nbytes: int) -> None:
        if nbytes <= 0:
            return
        with self._lock:
            self.total += int(nbytes)
            if self.limit and self.total > self.limit:
                raise DownloadBudgetExceeded(
                    f"YouTube download exceeded the {self.limit} byte job limit "
                    f"({self.total} bytes)"
                )

    def snapshot(self) -> int:
        with self._lock:
            return self.total


def proxy_configured(config=None) -> bool:
    runtime = config or get_config()
    return bool((getattr(runtime, "youtube_proxy_url", "") or "").strip())


def capped_height(value: int | None = None, config=None) -> int:
    """Never request above 1080p. Lower env values are honored."""
    runtime = config or get_config()
    raw = runtime.youtube_max_height if value is None else value
    try:
        height = int(raw)
    except (TypeError, ValueError):
        height = 1080
    return max(144, min(height, 1080))


def video_format_selector(height: int | None = None) -> str:
    limit = capped_height(height)
    return f"bv*[height<={limit}]+ba/b[height<={limit}]"


def audio_format_selector() -> str:
    # Format 140 is the common 128kbps m4a track. Modest bitrate keeps the
    # full-duration audio pass near ~0.06 GB per source hour.
    return "ba[ext=m4a][abr<=160]/140/ba[ext=m4a]/ba"


def sticky_session_id(task_id: Optional[str], video_id: Optional[str] = None) -> str:
    """URL-safe sticky id so one job keeps a single proxy exit IP."""
    seed = (task_id or "").strip() or (video_id or "").strip()
    cleaned = re.sub(r"[^A-Za-z0-9]", "", seed)
    if len(cleaned) >= 8:
        return cleaned[:24]
    # Stable for a given seed, and not empty when both inputs are missing.
    digest_source = seed or "job"
    import hashlib

    return hashlib.sha256(digest_source.encode()).hexdigest()[:16]


def normalize_country(value: Optional[str]) -> str:
    text = (value or "").strip().lower()
    if not text:
        return ""
    if not re.fullmatch(r"[a-z]{2}", text):
        logger.warning("Ignoring invalid YOUTUBE_PROXY_COUNTRY value")
        return ""
    return text


def render_proxy_url(template: str, session_id: str, country: str = "") -> str:
    """Substitute ``{session}`` and ``{country}`` in a proxy URL template.

    Example: ``http://user-session-{session}:pass@host:port``
    """
    raw = (template or "").strip()
    if not raw:
        raise YouTubeDownloadError("YOUTUBE_PROXY_URL is empty")
    if not session_id or not re.fullmatch(r"[A-Za-z0-9]{8,24}", session_id):
        raise YouTubeDownloadError("Proxy session id is not URL-safe")
    rendered = raw.replace("{session}", session_id).replace("{country}", country or "")
    if "{session}" in rendered or "{country}" in rendered:
        raise YouTubeDownloadError("Proxy URL template still contains a placeholder")
    return rendered


def redact_secrets(text: str) -> str:
    """Strip userinfo so proxy passwords never land in logs."""
    return re.sub(r"//[^/\s:@]+:[^/\s@]+@", "//***:***@", text or "")


def log_youtube_attempt(
    *,
    provider: str,
    video_id: str,
    kind: str,
    outcome: str,
    nbytes: int,
    task_id: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """One structured line per attempt so Axiom can sum bytes and cost."""
    message = (
        "youtube_download provider=%s video_id=%s kind=%s outcome=%s bytes=%s task_id=%s"
    )
    extra = {
        "youtube_provider": provider,
        "youtube_video_id": video_id,
        "youtube_download_kind": kind,
        "youtube_outcome": outcome,
        "youtube_bytes": int(nbytes),
        "task_id": task_id or "",
    }
    if error:
        logger.warning(message + " error=%s", provider, video_id, kind, outcome, int(nbytes), task_id or "-", redact_secrets(error), extra=extra)
        return
    logger.info(message, provider, video_id, kind, outcome, int(nbytes), task_id or "-", extra=extra)


def base_proxy_params(task_id: Optional[str] = None, video_id: Optional[str] = None, config=None) -> dict[str, Any]:
    """yt-dlp params that send metadata and media through the proxy.

    Player clients are left at yt-dlp defaults. PO tokens are requested only
    when a bgutil HTTP provider URL is configured.
    """
    runtime = config or get_config()
    session = sticky_session_id(task_id, video_id)
    country = normalize_country(getattr(runtime, "youtube_proxy_country", ""))
    params: dict[str, Any] = {
        "proxy": render_proxy_url(runtime.youtube_proxy_url, session, country),
        # yt-dlp treats 0.0.0.0 as "force IPv4" (the -4 flag).
        "source_address": "0.0.0.0",
        "socket_timeout": 30,
        "retries": 1,
        "fragment_retries": 1,
        "concurrent_fragment_downloads": 4,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "overwrites": True,
        "http_headers": {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    pot_url = (getattr(runtime, "youtube_pot_provider_url", "") or "").strip()
    if pot_url:
        params["extractor_args"] = {
            "youtubepot-bgutilhttp": {"base_url": [pot_url]},
        }
    if shutil.which("deno"):
        # yt-dlp needs a JS runtime for YouTube's player challenge. Prefer the
        # deno binary installed in the image; do not pin a player client.
        params["js_runtimes"] = {"deno": {}}
    return params


def _progress_hook(budget: ByteBudget, seen: dict[str, int]) -> Callable[[dict], None]:
    def hook(status: dict) -> None:
        if status.get("status") not in {"downloading", "finished"}:
            return
        filename = str(status.get("filename") or status.get("tmpfilename") or "")
        downloaded = int(status.get("downloaded_bytes") or status.get("total_bytes") or 0)
        previous = seen.get(filename, 0)
        if downloaded > previous:
            budget.add(downloaded - previous)
            seen[filename] = downloaded

    return hook


def download_options(
    task_id: Optional[str],
    video_id: str,
    budget: ByteBudget,
    *,
    seen: Optional[dict[str, int]] = None,
) -> dict[str, Any]:
    """Options for a native yt-dlp download. Never sets ``download_ranges``."""
    options = base_proxy_params(task_id, video_id)
    options["_youtube_deadline"] = time.monotonic() + max(
        30, getattr(get_config(), "youtube_proxy_timeout_seconds", 300)
    )
    count_bytes = _progress_hook(budget, seen if seen is not None else {})

    def bounded_progress(status):
        _check_deadline(options)
        count_bytes(status)

    options["progress_hooks"] = [bounded_progress]
    # Guard: section cuts must not be delegated to ffmpeg's protocol handler.
    options.pop("download_ranges", None)
    return options


def _is_video_format(fmt: dict) -> bool:
    return fmt.get("vcodec") not in (None, "none") and (fmt.get("height") or 0) > 0


def _is_audio_only(fmt: dict) -> bool:
    # YouTube HLS audio renditions can omit CODECS in their master playlist.
    # yt-dlp still explicitly marks them video-free; do not discard them.
    audio = fmt.get("acodec") not in (None, "none") or (
        fmt.get("vcodec") == "none" and fmt.get("acodec") is None
        and "m3u8" in (fmt.get("protocol") or "")
    )
    return audio and fmt.get("vcodec") in (None, "none")


def _fragmented(fmt: dict) -> bool:
    protocol = fmt.get("protocol") or ""
    return bool(fmt.get("fragments") or "dash" in protocol or "m3u8" in protocol)


def _has_media_timing(fmt: dict) -> bool:
    """True when fragments carry a duration or a start time.

    Byte-range chunk lists have neither, so they cannot be sliced by time
    without handing the URL to ffmpeg.
    """
    fragments = fmt.get("fragments")
    if not fragments or callable(fragments):
        return False
    try:
        sample = fragments[:8] if isinstance(fragments, list) else list(islice(fragments, 8))
    except TypeError:
        return False
    return any(
        isinstance(frag, dict)
        and (frag.get("duration") is not None or frag.get("start_time") is not None)
        for frag in sample
    )


def pick_video_format(formats: list[dict], height: int | None = None) -> Optional[dict]:
    limit = capped_height(height)
    candidates = [fmt for fmt in formats if _is_video_format(fmt) and (fmt.get("height") or 0) <= limit]
    if not candidates:
        return None

    def score(fmt: dict) -> tuple:
        mp4 = 1 if fmt.get("ext") in {"mp4", "m4v"} else 0
        return (
            int(_has_media_timing(fmt)),
            int(_fragmented(fmt)),
            mp4,
            fmt.get("height") or 0,
            fmt.get("tbr") or 0,
        )

    return max(candidates, key=score)


def pick_audio_format(formats: list[dict], *, modest: bool = True) -> Optional[dict]:
    candidates = [fmt for fmt in formats if _is_audio_only(fmt)]
    if not candidates:
        return None
    originals = [fmt for fmt in candidates
                 if (fmt.get("language_preference") or 0) > 0
                 or re.search(r"\boriginal\b", fmt.get("format_note") or "", re.IGNORECASE)]
    if originals:
        candidates = originals
    else:
        candidates = [fmt for fmt in candidates
                      if "dubbed" not in (fmt.get("format_note") or "").lower()]
    if not candidates:
        return None
    if modest:
        preferred = [
            fmt for fmt in candidates
            if str(fmt.get("format_id")).split("-", 1)[0] == "140"
            or ((fmt.get("abr") or 999) <= 160 and fmt.get("ext") in {"m4a", "mp4"})
        ]
        pool = preferred or candidates
        return min(
            pool,
            key=lambda fmt: (
                0 if str(fmt.get("format_id")).split("-", 1)[0] == "140" else 1,
                -(fmt.get("abr") or 0),
                fmt.get("filesize") or 0,
            ),
        )

    def score(fmt: dict) -> tuple:
        m4a = 1 if fmt.get("ext") in {"m4a", "mp4"} or str(fmt.get("format_id")) == "140" else 0
        return (int(_has_media_timing(fmt)), int(_fragmented(fmt)), m4a,
                fmt.get("abr") or fmt.get("tbr") or 0,
                int("high" in (fmt.get("format_note") or "").lower()))

    return max(candidates, key=score)


def resolve_fragments(fmt: dict) -> Optional[list]:
    fragments = fmt.get("fragments")
    if fragments is None:
        return None
    if callable(fragments):
        try:
            fragments = list(fragments({
                "filename": "section",
                "live": False,
                "total_frags": None,
                "fragment_index": 0,
            }))
        except Exception as exc:
            logger.info("Could not expand media fragments: %s", redact_secrets(str(exc)))
            return None
    if not isinstance(fragments, list) or not fragments:
        return None
    return fragments


def _hls_url(base: str, value: str) -> str:
    try:
        address = urljoin(base, value)
        parsed = urlsplit(address)
        valid = (parsed.scheme == "https" and parsed.hostname
                 and parsed.hostname.endswith(".googlevideo.com")
                 and not parsed.username and not parsed.password and parsed.port in (None, 443)
                 and not any(char in address for char in '\r\n"'))
    except ValueError:
        valid = False
    if not valid:
        raise PartialDownloadUnavailable("HLS playlist contains an unsupported media address")
    return address


def parse_hls_fragments(playlist: str, base_url: str, duration: float) -> list[dict]:
    """Expand a complete, unencrypted YouTube media playlist onto its source timeline.

    Unsupported HLS features use the full-file fallback rather than guessing at
    offsets, skipping content, or letting ffmpeg fetch a remote playlist.
    """
    from yt_dlp.utils import parse_m3u8_attributes

    lines = [line.strip() for line in playlist.lstrip("\ufeff").splitlines() if line.strip()]
    if not lines or lines[0] != "#EXTM3U" or lines[-1] != "#EXT-X-ENDLIST":
        raise PartialDownloadUnavailable("Section downloads require a complete HLS media playlist")
    fragments: list[dict] = []
    cursor = 0.0
    pending = None
    sequence = 0
    media_seen = False
    for line in lines[1:]:
        if line.startswith(("#EXT-X-STREAM-INF:", "#EXT-X-I-FRAME-STREAM-INF:",
                            "#EXT-X-BYTERANGE:", "#EXT-X-DISCONTINUITY", "#EXT-X-GAP",
                            "#EXT-X-SKIP:", "#EXT-X-START:", "#EXT-X-I-FRAMES-ONLY")):
            # A zero discontinuity-sequence is normal for complete VOD playlists.
            if line == "#EXT-X-DISCONTINUITY-SEQUENCE:0":
                continue
            raise PartialDownloadUnavailable("HLS playlist needs the full-file fallback")
        if line.startswith("#EXT-X-KEY:"):
            if parse_m3u8_attributes(line.partition(":")[2]).get("METHOD") != "NONE":
                raise PartialDownloadUnavailable("Encrypted HLS is not supported by section downloads")
        elif line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            if media_seen:
                raise PartialDownloadUnavailable("HLS sequence changed during the playlist")
            try:
                sequence = int(line.partition(":")[2])
            except ValueError:
                raise PartialDownloadUnavailable("Invalid HLS media sequence") from None
            if sequence < 0:
                raise PartialDownloadUnavailable("Invalid HLS media sequence")
        elif line.startswith("#EXT-X-MAP:"):
            attrs = parse_m3u8_attributes(line.partition(":")[2])
            if media_seen or fragments or attrs.get("BYTERANGE") or not attrs.get("URI"):
                raise PartialDownloadUnavailable("HLS initialization requires the full-file fallback")
            fragments.append({"url": _hls_url(base_url, attrs["URI"]), "is_init": True})
        elif line.startswith("#EXTINF:"):
            if pending is not None:
                raise PartialDownloadUnavailable("HLS segment has no media address")
            try:
                pending = float(line.partition(":")[2].split(",", 1)[0])
            except ValueError:
                raise PartialDownloadUnavailable("Invalid HLS segment duration") from None
            if not math.isfinite(pending) or pending <= 0:
                raise PartialDownloadUnavailable("Invalid HLS segment duration")
        elif not line.startswith("#"):
            if pending is None:
                raise PartialDownloadUnavailable("HLS segment has no timing information")
            fragments.append({"url": _hls_url(base_url, line), "start_time": cursor,
                              "end_time": cursor + pending, "duration": pending,
                              "media_sequence": sequence})
            cursor += pending
            sequence += 1
            pending = None
            media_seen = True
    expected = float(duration)
    if (pending is not None or not media_seen or not math.isfinite(expected) or expected <= 0
            or abs(cursor - expected) > max(2.0, expected * .001)):
        raise PartialDownloadUnavailable("HLS playlist does not cover the full source timeline")
    return fragments


def expand_hls_format(fmt: dict, options: dict, budget: ByteBudget, duration: float) -> dict:
    if "m3u8" not in (fmt.get("protocol") or "") or _has_media_timing(fmt):
        return fmt
    import yt_dlp
    from yt_dlp.networking import Request

    address = _hls_url(fmt["url"], fmt["url"])
    try:
        with _proxy_client(options) as ydl:
            response = ydl.urlopen(Request(address, headers=fmt.get("http_headers") or {}))
            try:
                base_url = _hls_url(address, response.url)
                payload = response.read(4_000_001)
            finally:
                response.close()
        budget.add(len(payload))
        if len(payload) > 4_000_000:
            raise PartialDownloadUnavailable("HLS playlist exceeds its size limit")
        fragments = parse_hls_fragments(payload.decode("utf-8"), base_url, duration)
    except (DownloadBudgetExceeded, DownloadDeadlineExceeded, PartialDownloadUnavailable):
        raise
    except Exception:
        raise YouTubeDownloadError("Could not fetch the HLS media playlist through the proxy") from None
    return dict(fmt, fragments=fragments)


def _section_hls_playlist(fragments: list[dict]) -> str:
    media = [fragment for fragment in fragments if fragment.get("duration")]
    if not media:
        raise PartialDownloadUnavailable("No timed HLS segments cover this section")
    lines = ["#EXTM3U", "#EXT-X-VERSION:7",
             f"#EXT-X-TARGETDURATION:{math.ceil(max(f['duration'] for f in media))}",
             f"#EXT-X-MEDIA-SEQUENCE:{media[0].get('media_sequence', 0)}",
             "#EXT-X-PLAYLIST-TYPE:VOD"]
    for fragment in fragments:
        if fragment.get("is_init"):
            lines.append(f'#EXT-X-MAP:URI="{fragment["url"]}"')
        else:
            lines.extend([f'#EXTINF:{fragment["duration"]:.9f},', fragment["url"]])
    lines.append("#EXT-X-ENDLIST")
    return "\n".join(lines) + "\n"


def select_fragments_for_window(
    fragments: list,
    start: float,
    end: float,
) -> Optional[tuple[list, float, float]]:
    """Keep init fragments plus media fragments overlapping ``[start, end)``.

    Returns ``(fragments, media_start, media_end)`` in original timeline seconds.
    ``None`` means the fragments have no timing and must not be partially fetched
    via ffmpeg.
    """
    if end <= start or not fragments:
        return None

    cursor = 0.0
    timed = False
    selected: list = []
    media_start: Optional[float] = None
    media_end: Optional[float] = None
    for fragment in fragments:
        frag_start = fragment.get("start_time")
        frag_end = fragment.get("end_time")
        duration = fragment.get("duration")
        if frag_start is not None and frag_end is not None:
            fs, fe = float(frag_start), float(frag_end)
            timed = True
        elif duration is not None:
            fs, fe = cursor, cursor + float(duration)
            cursor = fe
            timed = True
            if fe - fs <= 0.001:
                selected.append(fragment)
                continue
        else:
            # Initialization or index fragments have no media duration.
            selected.append(fragment)
            continue
        if fe > start and fs < end:
            selected.append(fragment)
            media_start = fs if media_start is None else min(media_start, fs)
            media_end = fe if media_end is None else max(media_end, fe)
    if not timed or media_start is None or media_end is None or media_end <= media_start:
        return None
    return selected, media_start, media_end


def select_window(fmt: dict, start: float, end: float) -> Optional[tuple[list, float, float]]:
    fragments = resolve_fragments(fmt)
    if not fragments:
        return None
    return select_fragments_for_window(fragments, start, end)


def _find_download(dest_stem: Path) -> Path:
    matches = [
        path for path in dest_stem.parent.glob(dest_stem.name + ".*")
        if path.is_file() and path.suffix.lower() not in {".part", ".ytdl", ".json", ".txt"}
    ]
    if not matches:
        raise YouTubeDownloadError(f"yt-dlp did not produce a file for {dest_stem.name}")
    return max(matches, key=lambda path: path.stat().st_size)


def native_download_format(
    options: dict[str, Any],
    url: str,
    video_id: str,
    fmt: dict,
    dest_stem: Path,
    fragments: Optional[list] = None,
) -> Path:
    """Download one format with yt-dlp's own HTTP stack (proxy-aware)."""
    import yt_dlp

    dest_stem.parent.mkdir(parents=True, exist_ok=True)
    params = dict(options)
    params.pop("download_ranges", None)
    params["outtmpl"] = str(dest_stem) + ".%(ext)s"
    params["overwrites"] = True
    params["external_downloader"] = {"default": "native"}
    params["skip_unavailable_fragments"] = False
    info: dict[str, Any] = {
        "id": video_id,
        "title": video_id,
        "extractor": "youtube",
        "extractor_key": "Youtube",
        "webpage_url": url,
        "ext": fmt.get("ext") or "mp4",
        "url": fmt.get("url"),
        "protocol": fmt.get("protocol") or "https",
        "http_headers": fmt.get("http_headers") or {},
        "format_id": fmt.get("format_id"),
        "vcodec": fmt.get("vcodec"),
        "acodec": fmt.get("acodec"),
        "width": fmt.get("width"),
        "height": fmt.get("height"),
        "downloader_options": fmt.get("downloader_options"),
        "fragment_base_url": fmt.get("fragment_base_url"),
        "extra_param_to_segment_url": fmt.get("extra_param_to_segment_url"),
    }
    if fragments is not None:
        info["fragments"] = fragments
        protocol = fmt.get("protocol") or ""
        info["protocol"] = "m3u8_native" if "m3u8" in protocol else "http_dash_segments"
        if "m3u8" in protocol:
            # HlsFD ignores info['fragments']; give it only the selected playlist.
            # This also preserves its native TS/fMP4 handling and local fixups.
            info["hls_media_playlist_data"] = _section_hls_playlist(fragments)
    # Section metadata would make yt-dlp pick FFmpegFD and bypass the proxy.
    info.pop("section_start", None)
    info.pop("section_end", None)
    with _proxy_client(params) as ydl:
        ydl.process_info(info)
    return _find_download(dest_stem)


def extract_video_info(url: str, options: dict[str, Any]) -> dict:
    import yt_dlp

    params = dict(options)
    params.pop("download_ranges", None)
    params.update({"skip_download": True, "quiet": True, "noplaylist": True})
    with _proxy_client(params) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise YouTubeDownloadError("yt-dlp returned no video info")
    return info


def _account_file(path: Path, budget: ByteBudget, seen_before: int) -> int:
    """Count on-disk bytes when the progress hook did not observe the transfer."""
    if budget.snapshot() == seen_before and path.exists():
        budget.add(path.stat().st_size)
    return max(0, budget.snapshot() - seen_before)


def download_audio(
    url: str,
    video_id: str,
    dest_stem: Path,
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
) -> Path:
    """Download modest full-duration audio. ``dest_stem`` has no suffix."""
    runtime = get_config()
    counter = budget or ByteBudget(runtime.youtube_download_max_bytes)
    seen: dict[str, int] = {}
    options = download_options(task_id, video_id, counter, seen=seen)
    before = counter.snapshot()
    try:
        info = extract_video_info(url, options)
        fmt = pick_audio_format(info.get("formats") or [], modest=True)
        if not fmt:
            raise YouTubeDownloadError("No audio format is available")
        path = native_download_format(options, url, video_id, fmt, dest_stem)
        nbytes = _account_file(path, counter, before)
        log_youtube_attempt(
            provider=YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY,
            video_id=video_id,
            kind="audio",
            outcome="success",
            nbytes=nbytes,
            task_id=task_id,
        )
        return path
    except Exception as exc:
        log_youtube_attempt(
            provider=YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY,
            video_id=video_id,
            kind="audio",
            outcome="failure",
            nbytes=max(0, counter.snapshot() - before),
            task_id=task_id,
            error=str(exc),
        )
        raise


def download_section_file(
    url: str,
    video_id: str,
    start: float,
    end: float,
    dest: Path,
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
) -> tuple[Path, float, float]:
    """Download one time window. Returns the file and its original-timeline bounds.

    Media bytes go through yt-dlp. ffmpeg only trims and muxes local files.
    """
    runtime = get_config()
    counter = budget or ByteBudget(runtime.youtube_download_max_bytes)
    seen: dict[str, int] = {}
    options = download_options(task_id, video_id, counter, seen=seen)
    before = counter.snapshot()
    work = dest.parent / f".parts-{dest.stem}"
    try:
        info = extract_video_info(url, options)
        formats = info.get("formats") or []
        video_fmt = pick_video_format(formats)
        audio_fmt = pick_audio_format(formats, modest=False)
        if not video_fmt or not audio_fmt:
            raise PartialDownloadUnavailable("No capped video or audio format is available")
        duration = info.get("duration") or 0
        video_fmt = expand_hls_format(video_fmt, options, counter, duration)
        audio_fmt = expand_hls_format(audio_fmt, options, counter, duration)
        window_end = min(end, float(duration)) if duration else end
        video_window = select_window(video_fmt, start, window_end)
        audio_window = select_window(audio_fmt, start, window_end)
        if not video_window or not audio_window:
            raise PartialDownloadUnavailable(
                "Selected formats have no timed fragments; refusing an ffmpeg section download"
            )
        video_fragments, video_start, video_end = video_window
        audio_fragments, audio_start, audio_end = audio_window
        media_start = max(video_start, audio_start)
        media_end = min(video_end, audio_end)
        if media_end - media_start < 0.5:
            raise PartialDownloadUnavailable("Audio and video fragment windows do not overlap")
        work.mkdir(parents=True, exist_ok=True)
        before_media = counter.snapshot()
        video_path = native_download_format(
            options, url, video_id, video_fmt, work / "video", video_fragments
        )
        audio_path = native_download_format(
            options, url, video_id, audio_fmt, work / "audio", audio_fragments
        )
        dest.parent.mkdir(parents=True, exist_ok=True)
        _mux_local_window(
            video_path,
            audio_path,
            dest,
            video_offset=max(0.0, media_start - video_start),
            audio_offset=max(0.0, media_start - audio_start),
            duration=media_end - media_start,
        )
        _account_file(dest, counter, before_media)
        nbytes = counter.snapshot() - before
        log_youtube_attempt(
            provider=YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY,
            video_id=video_id,
            kind="section",
            outcome="success",
            nbytes=nbytes,
            task_id=task_id,
        )
        return dest, media_start, media_end
    except Exception as exc:
        log_youtube_attempt(
            provider=YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY,
            video_id=video_id,
            kind="section",
            outcome="failure",
            nbytes=max(0, counter.snapshot() - before),
            task_id=task_id,
            error=str(exc),
        )
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise
    finally:
        shutil.rmtree(work, ignore_errors=True)


def download_full_with_proxy(
    url: str,
    video_id: str,
    dest_dir: Path,
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
    quality: Optional[str] = None,
) -> Path:
    """Full-file fallback through the proxy, capped at 1080p, native downloader."""
    import yt_dlp

    runtime = get_config()
    counter = budget or ByteBudget(runtime.youtube_download_max_bytes)
    seen: dict[str, int] = {}
    before = counter.snapshot()
    dest_dir.mkdir(parents=True, exist_ok=True)
    options = download_options(task_id, video_id, counter, seen=seen)
    options.update({
        "format": video_format_selector(int(quality) if quality else None),
        "merge_output_format": "mp4",
        "outtmpl": str(dest_dir / f"{video_id}.%(ext)s"),
    })
    options.pop("download_ranges", None)
    try:
        with _proxy_client(options) as ydl:
            ydl.download([url])
        path = _find_download(dest_dir / video_id)
        _account_file(path, counter, before)
        return path
    except Exception:
        partials = list(dest_dir.glob(f"{video_id}.*"))
        for partial in partials:
            if partial.suffix.lower() in {".part", ".ytdl"}:
                partial.unlink(missing_ok=True)
        raise


def _mux_local_window(
    video_path: Path,
    audio_path: Path,
    dest: Path,
    *,
    video_offset: float,
    audio_offset: float,
    duration: float,
) -> None:
    """Trim and mux already-downloaded files. Inputs are local paths only."""
    command = [
        "ffmpeg", "-y", "-nostdin",
        "-ss", f"{video_offset:.3f}", "-i", str(video_path),
        "-ss", f"{audio_offset:.3f}", "-i", str(audio_path),
        "-t", f"{duration:.3f}",
        "-map", "0:v:0", "-map", "1:a:0",
        "-c", "copy",
        str(dest),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not dest.exists() or dest.stat().st_size <= 0:
        raise YouTubeDownloadError(
            "Could not mux the downloaded section locally: "
            + redact_secrets((result.stderr or "")[-500:])
        )
