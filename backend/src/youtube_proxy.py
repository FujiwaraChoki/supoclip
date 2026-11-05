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
import secrets
import shutil
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from itertools import islice
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urljoin, urlsplit

from .config import get_config
from .download_progress import DownloadRecovery, report_download_recovery

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


class YouTubeGeoRestrictedError(YouTubeDownloadError):
    """The source has region restrictions and download recovery did not succeed."""

    def __init__(self):
        super().__init__(
            "This YouTube video has regional restrictions, and we could not download it. "
            "Upload the video file or choose a different source."
        )


def _exception_chain(exc: BaseException):
    """yt-dlp also retains its extractor error in DownloadError.exc_info."""
    pending = [exc]
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if not isinstance(current, BaseException) or id(current) in seen:
            continue
        seen.add(id(current))
        yield current
        # Implicit exception context may describe a prior provider's failure,
        # rather than the reason this operation failed.
        pending.append(current.__cause__)
        exc_info = getattr(current, "exc_info", None)
        if isinstance(exc_info, tuple) and len(exc_info) > 1:
            pending.append(exc_info[1])


def is_geo_restricted_error(exc: BaseException) -> bool:
    from yt_dlp.utils import GeoRestrictedError

    for error in _exception_chain(exc):
        if isinstance(error, (GeoRestrictedError, YouTubeGeoRestrictedError)):
            return True
        message = str(error).lower()
        if any(marker in message for marker in (
            "not made this video available in your country",
            "not available in your country",
            "not available from your location",
            "this youtube video has regional restrictions",
        )):
            return True
    return False


def _allowed_geo_countries(exc: BaseException) -> list[str]:
    """Use structured extractor evidence, never guess from a generic 403."""
    from yt_dlp.utils import GeoRestrictedError, ISO3166Utils

    countries = set()
    for error in _exception_chain(exc):
        if not isinstance(error, GeoRestrictedError):
            continue
        values = error.countries
        if not isinstance(values, (list, tuple, set)):
            continue
        for value in values:
            if isinstance(value, str) and re.fullmatch(r"[A-Za-z]{2}", value):
                if ISO3166Utils.short2full(value.upper()):
                    countries.add(value.lower())
    # Prefer widely available proxy exits when the source explicitly permits them.
    preferred = [country for country in ("us", "ca", "gb") if country in countries]
    return preferred + sorted(countries.difference(preferred))


class DownloadBudgetExceeded(YouTubeDownloadError):
    """The per-job byte limit was crossed."""


class PartialDownloadUnavailable(YouTubeDownloadError):
    """The formats cannot be sliced without an ffmpeg network fetch."""


class DownloadDeadlineExceeded(YouTubeDownloadError):
    """One proxy attempt expired; independent providers may still be tried."""


class AudioDownloadForbidden(YouTubeDownloadError):
    """Direct audio was forbidden and the HLS audio retry did not produce a file."""


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
        # Recovery belongs to this job, never to the process-wide configuration.
        self._proxy_routes: dict[tuple[Optional[str], str], tuple[str, str]] = {}

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

    def proxy_route(self, task_id: Optional[str], video_id: str) -> Optional[tuple[str, str]]:
        with self._lock:
            return self._proxy_routes.get((task_id, video_id))

    def remember_proxy_route(self, task_id: Optional[str], video_id: str,
                             country: str, session: str) -> None:
        with self._lock:
            self._proxy_routes[(task_id, video_id)] = (country, session)


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


_URL_IN_LOG = re.compile(r"https?://\S+")


def public_download_error(exc: BaseException | str) -> str:
    """Error text safe for logs: no proxy userinfo and no signed media URLs."""
    text = exc if isinstance(exc, str) else str(exc)
    text = _URL_IN_LOG.sub("[url]", redact_secrets(text))
    return text[:500]


def is_forbidden_media_error(exc: BaseException) -> bool:
    """True for HTTP 403 and yt-dlp's ``unable to download video data`` failures.

    Budget and deadline errors are not retried as forbidden media. A capped
    job must not start another, larger download.
    """
    if isinstance(exc, (DownloadBudgetExceeded, DownloadDeadlineExceeded, AudioDownloadForbidden)):
        return False
    text = str(exc).lower()
    if (
        "http error 403" in text
        or "403: forbidden" in text
        or "unable to download video data" in text
    ):
        return True
    cause = exc.__cause__
    if isinstance(cause, BaseException) and cause is not exc:
        return is_forbidden_media_error(cause)
    return False


def log_youtube_attempt(
    *,
    provider: str,
    video_id: str,
    kind: str,
    outcome: str,
    nbytes: int,
    task_id: Optional[str] = None,
    error: Optional[str] = None,
    fallback: Optional[str] = None,
) -> None:
    """One structured line per attempt so Axiom can sum bytes and cost.

    ``fallback`` is set only when that retry produced the file (``hls_audio``
    or ``section``). Failure lines stay free of proxy credentials and media URLs.
    """
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
    args: list[Any] = [provider, video_id, kind, outcome, int(nbytes), task_id or "-"]
    if fallback:
        extra["youtube_fallback"] = fallback
        message += " fallback=%s"
        args.append(fallback)
    if error:
        message += " error=%s"
        args.append(public_download_error(error))
        logger.warning(message, *args, extra=extra)
        return
    logger.info(message, *args, extra=extra)


def base_proxy_params(task_id: Optional[str] = None, video_id: Optional[str] = None, config=None,
                      *, session_id: Optional[str] = None,
                      country: Optional[str] = None) -> dict[str, Any]:
    """yt-dlp params that send metadata and media through the proxy.

    Player clients are left at yt-dlp defaults. PO tokens are requested only
    when a bgutil HTTP provider URL is configured.
    """
    runtime = config or get_config()
    session = session_id or sticky_session_id(task_id, video_id)
    country = normalize_country(
        getattr(runtime, "youtube_proxy_country", "") if country is None else country
    )
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
            seen[filename] = downloaded
            budget.add(downloaded - previous)

    return hook


def download_options(
    task_id: Optional[str],
    video_id: str,
    budget: ByteBudget,
    *,
    seen: Optional[dict[str, int]] = None,
    session_id: Optional[str] = None,
    country: Optional[str] = None,
) -> dict[str, Any]:
    """Options for a native yt-dlp download. Never sets ``download_ranges``."""
    options = base_proxy_params(task_id, video_id, session_id=session_id, country=country)
    options["_youtube_deadline"] = time.monotonic() + max(
        30, getattr(get_config(), "youtube_proxy_timeout_seconds", 300)
    )
    observed = seen if seen is not None else {}
    count_bytes = _progress_hook(budget, observed)
    options["_youtube_count_bytes"] = count_bytes
    options["_youtube_seen_bytes"] = observed

    def bounded_progress(status):
        count_bytes(status)
        _check_deadline(options)

    options["progress_hooks"] = [bounded_progress]
    # Guard: section cuts must not be delegated to ffmpeg's protocol handler.
    options.pop("download_ranges", None)
    return options


def is_transient_proxy_error(exc: BaseException) -> bool:
    """Only transport failures merit a new residential session, never bad media."""
    transient = False
    for error in _exception_chain(exc):
        if isinstance(error, (DownloadBudgetExceeded, PartialDownloadUnavailable)):
            return False
        if isinstance(error, (DownloadDeadlineExceeded, TimeoutError, ConnectionError)):
            transient = True
        message = str(error).lower()
        if any(marker in message for marker in (
            "unable to connect to proxy", "tunnel connection failed", "no_host_connection",
            "no_ray", "timed out", "connection reset", "connection aborted",
            "remote end closed", "http error 502", "http error 503", "http error 504",
        )):
            transient = True
        # yt-dlp's IncompleteRead is often wrapped in a DownloadError. A peer
        # closing a response early is recoverable just like a connection reset.
        if re.search(r"\b\d+ bytes read, \d+ more expected\b", message):
            transient = True
    return transient


def _with_proxy_retry(operation: Callable, *, task_id: Optional[str], video_id: str,
                      budget: ByteBudget, cleanup: Callable[[], None],
                      deadline: Optional[float] = None):
    """One transport and one region retry stay inside the initial budget plus 60s.

    Each attempt re-extracts metadata, keeping signed URLs and all fragments on
    that attempt's session. Completed sections outside this operation are kept.
    """
    runtime = get_config()
    initial_seconds = max(30, getattr(runtime, "youtube_proxy_timeout_seconds", 300))
    overall_deadline = time.monotonic() + initial_seconds + 60
    if deadline is not None:
        overall_deadline = min(overall_deadline, deadline)
    route = budget.proxy_route(task_id, video_id)
    country = route[0] if route else normalize_country(getattr(runtime, "youtube_proxy_country", ""))
    session = route[1] if route else sticky_session_id(task_id, video_id)
    geo_retry = False
    transport_retry = False
    retry_deadline = None
    for attempt in range(3):
        options = download_options(
            task_id, video_id, budget,
            session_id=session, country=country,
        )
        options["_youtube_deadline"] = min(
            overall_deadline, retry_deadline if attempt else time.monotonic() + initial_seconds
        )
        options["_youtube_metadata_ready"] = lambda country=country, session=session: (
            budget.remember_proxy_route(task_id, video_id, country, session)
        )
        try:
            _check_deadline(options)
            result = operation(options)
            budget.remember_proxy_route(task_id, video_id, country, session)
            return result
        except Exception as exc:
            cleanup()
            if isinstance(exc, DownloadBudgetExceeded):
                raise
            is_geo = is_geo_restricted_error(exc)
            now = time.monotonic()
            allowed = [value for value in _allowed_geo_countries(exc) if value != country] if is_geo else []
            can_switch_region = (
                is_geo and not geo_retry and allowed
                and "{country}" in (runtime.youtube_proxy_url or "")
            )
            if now < overall_deadline and can_switch_region:
                country = allowed[0]
                geo_retry = True
                # Region errors normally happen before transfer. Preserve the
                # unused initial allowance for downloading from the working exit.
                retry_deadline = overall_deadline
                logger.info(
                    "youtube_proxy_region_retry country=%s video_id=%s task_id=%s",
                    country, video_id, task_id or "-",
                )
            elif now < overall_deadline and not is_geo and not transport_retry and is_transient_proxy_error(exc):
                transport_retry = True
                retry_deadline = min(overall_deadline, now + 60)
            else:
                if is_geo:
                    raise YouTubeGeoRestrictedError() from exc
                raise
            session = secrets.token_hex(12)
            report_download_recovery(DownloadRecovery.CONNECTION)
            logger.info(
                "youtube_proxy_retry attempt=%s video_id=%s task_id=%s error=%s",
                attempt + 2, video_id, task_id or "-", public_download_error(exc),
            )


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


_DUB_MARK = re.compile(r"dubbed|auto-dub", re.IGNORECASE)
_ORIGINAL_MARK = re.compile(r"\boriginal\b", re.IGNORECASE)
_DESC_LANGUAGE = re.compile(r"(?:^|[-_])desc(?:$|[-_])", re.IGNORECASE)
_DESC_NOTE = re.compile(r"\b(?:descriptive|audio description)\b", re.IGNORECASE)


def _is_dubbed_audio(fmt: dict) -> bool:
    """True for auto-dub renditions, even when language_preference prefers them."""
    text = " ".join(str(fmt.get(key) or "") for key in ("format_note", "format_id", "format"))
    return _DUB_MARK.search(text) is not None


def _is_descriptive_audio(fmt: dict) -> bool:
    """Audio description, not the spoken original.

    yt-dlp marks these as a language like ``en-desc`` and ``language_preference=-10``.
    ``-1`` is its ordinary non-preferred value and is not a description track.
    """
    language = fmt.get("language")
    if isinstance(language, str) and _DESC_LANGUAGE.search(language):
        return True
    text = " ".join(str(fmt.get(key) or "") for key in ("format_note", "format", "format_id"))
    if _DESC_NOTE.search(text):
        return True
    preference = fmt.get("language_preference")
    if isinstance(preference, bool) or not isinstance(preference, (int, float)):
        return False
    return preference <= -10


def _primary_language(value) -> Optional[str]:
    if not isinstance(value, str):
        return None
    text = value.strip().lower().replace("_", "-")
    if not text or text in {"und", "none", "null"} or _DESC_LANGUAGE.search(text):
        return None
    primary = text.split("-", 1)[0]
    if not primary.isalpha() or not 2 <= len(primary) <= 3:
        return None
    return primary


def _format_language(fmt: dict) -> Optional[str]:
    return _primary_language(fmt.get("language"))


def _note_says_original(fmt: dict) -> bool:
    return _ORIGINAL_MARK.search(fmt.get("format_note") or "") is not None


def _audio_variant_index(fmt: dict) -> Optional[str]:
    """Numeric YouTube track suffix, such as ``0`` in ``233-0``. Protocol tags are not tracks."""
    _, sep, rest = str(fmt.get("format_id") or "").partition("-")
    if sep and rest.isdigit():
        return rest
    return None


def _resolved_original_language(
    candidates: list[dict], source_language: Optional[str]
) -> tuple[Optional[str], bool]:
    """Language to keep, and whether a track is explicitly labeled original.

    A labeled original track wins over the info-dict language. Client locale
    can mark an auto-dub as the video language; that must not redefine original.
    """
    labeled = [fmt for fmt in candidates if _note_says_original(fmt)]
    if labeled:
        for fmt in labeled:
            lang = _format_language(fmt)
            if lang:
                return lang, True
        return None, True
    return _primary_language(source_language), False


def _is_identifiable_original(fmt: dict, original_language: Optional[str], labeled: bool) -> bool:
    if _note_says_original(fmt):
        return True
    if labeled and not original_language:
        return False
    lang = _format_language(fmt)
    return bool(original_language and lang == original_language)


def _without_language_mismatches(
    candidates: list[dict], original_language: Optional[str], labeled: bool
) -> list[dict]:
    if not original_language:
        return candidates
    if not any(_is_identifiable_original(fmt, original_language, labeled) for fmt in candidates):
        return candidates
    kept = [
        fmt for fmt in candidates
        if _note_says_original(fmt) or _format_language(fmt) in (None, original_language)
    ]
    return kept or candidates


def _video_language(info: dict) -> Optional[str]:
    language = info.get("language")
    if isinstance(language, str) and language.strip():
        return language.strip()
    return None


def _original_audio_resolution(
    formats: list[dict], source_language: Optional[str] = None
) -> tuple[Optional[str], bool]:
    """Original language from every non-dubbed, non-description audio format.

    A progressive itag such as 140 often carries the ``original`` label when
    the HLS renditions do not. Resolving after a protocol filter would ignore
    that label and trust the client locale instead.
    """
    pool = [
        fmt for fmt in formats
        if _is_audio_only(fmt) and not _is_dubbed_audio(fmt) and not _is_descriptive_audio(fmt)
    ]
    return _resolved_original_language(pool, source_language)


def _prefer_original_audio(
    candidates: list[dict],
    source_language: Optional[str] = None,
    *,
    original_audio: Optional[tuple[Optional[str], bool]] = None,
) -> list[dict]:
    """Drop dubs and description tracks, then keep the original-language tracks.

    ``language_preference`` is only a fallback. YouTube raises it for the
    client locale, so an English auto-dub can outrank the source track.
    ``original_audio`` is the resolution from the full format list. A narrowed
    HLS pool must not replace it with the client locale.
    """
    remaining = [fmt for fmt in candidates if not _is_dubbed_audio(fmt)]
    if not remaining:
        return []
    spoken = [fmt for fmt in remaining if not _is_descriptive_audio(fmt)]
    if spoken:
        remaining = spoken
    if original_audio is None:
        original_language, labeled = _resolved_original_language(remaining, source_language)
    else:
        original_language, labeled = original_audio
    remaining = _without_language_mismatches(remaining, original_language, labeled)
    identifiable = [
        fmt for fmt in remaining if _is_identifiable_original(fmt, original_language, labeled)
    ]
    if identifiable:
        # Non-dubbed tracks that only carry language_preference stay available so
        # modest itag selection still sees them. HLS ranking prefers a track that
        # is actually identifiable as original over those extras.
        chosen = {id(fmt) for fmt in identifiable}
        extras = [
            fmt for fmt in remaining
            if (fmt.get("language_preference") or 0) > 0 and id(fmt) not in chosen
        ]
        return identifiable + extras
    if any(_audio_variant_index(fmt) is not None for fmt in remaining):
        plain = [
            fmt for fmt in remaining
            if fmt.get("format_id") not in (None, "") and _audio_variant_index(fmt) is None
        ]
        if plain:
            return plain
    preferred = [fmt for fmt in remaining if (fmt.get("language_preference") or 0) > 0]
    return preferred or remaining


def pick_audio_format(
    formats: list[dict],
    *,
    modest: bool = True,
    source_language: Optional[str] = None,
    original_audio: Optional[tuple[Optional[str], bool]] = None,
) -> Optional[dict]:
    candidates = [fmt for fmt in formats if _is_audio_only(fmt)]
    if not candidates:
        return None
    if original_audio is None:
        original_audio = _original_audio_resolution(formats, source_language)
    candidates = _prefer_original_audio(
        candidates, source_language, original_audio=original_audio
    )
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


def _log_hls_audio_choice(fmt: dict, candidates: int) -> None:
    preference = fmt.get("language_preference")
    logger.info(
        "hls_audio_selected format_id=%s format_note=%s language=%s language_preference=%s candidates=%s",
        fmt.get("format_id") or "-",
        (fmt.get("format_note") or "-").replace("\n", " ")[:200],
        fmt.get("language") or "-",
        "-" if preference is None else preference,
        candidates,
    )


def _hls_audio_rank(fmt: dict, original_language: Optional[str], labeled: bool, prefer_plain: bool) -> tuple:
    note = fmt.get("format_note") or ""
    return (
        0 if _is_identifiable_original(fmt, original_language, labeled) else 1,
        0 if prefer_plain and _audio_variant_index(fmt) is None else 1,
        fmt.get("abr") or fmt.get("tbr") or 10**6,
        0 if "low" in note.lower() else 1,
        fmt.get("filesize") or 0,
        str(fmt.get("format_id") or ""),
    )


def pick_hls_audio_format(
    formats: list[dict],
    *,
    exclude_ids: Optional[set[str]] = None,
    source_language: Optional[str] = None,
    original_audio: Optional[tuple[Optional[str], bool]] = None,
) -> Optional[dict]:
    """Modest original-language HLS audio, or the lowest muxed HLS rendition under the height cap.

    The choice stays in-process. A yt-dlp format string such as
    ``bestaudio[protocol^=m3u8]`` can hand the playlist to ffmpeg, and ffmpeg
    does not use this job's proxy. ``m3u8`` matches yt-dlp's ``m3u8_native``
    protocol as well (the same rule as ``protocol^=m3u8``). Dubbed tracks are
    removed before bitrate ranking so a low-abr auto-dub cannot win.

    The original language is resolved from every audio format, including
    progressive renditions, before this function narrows to HLS.
    """
    if original_audio is None:
        original_audio = _original_audio_resolution(formats, source_language)
    original_language, labeled = original_audio
    excluded = {str(item) for item in (exclude_ids or set()) if item not in (None, "")}

    def allowed(fmt: dict) -> bool:
        return "m3u8" in (fmt.get("protocol") or "") and str(fmt.get("format_id") or "") not in excluded

    hls_audio = [fmt for fmt in formats if allowed(fmt) and _is_audio_only(fmt)]
    if hls_audio:
        pool = _prefer_original_audio(
            hls_audio, source_language, original_audio=original_audio
        )
        if pool:
            prefer_plain = not any(
                _is_identifiable_original(fmt, original_language, labeled) for fmt in pool
            )
            chosen = min(
                pool,
                key=lambda fmt: _hls_audio_rank(fmt, original_language, labeled, prefer_plain),
            )
            _log_hls_audio_choice(chosen, len(hls_audio))
            return chosen
    limit = capped_height()
    muxed = [
        fmt for fmt in formats
        if allowed(fmt)
        and _is_video_format(fmt)
        and fmt.get("acodec") not in (None, "none")
        and 0 < (fmt.get("height") or 0) <= limit
    ]
    muxed_pool = _prefer_original_audio(muxed, source_language, original_audio=original_audio)
    if not muxed_pool:
        return None
    chosen = min(
        muxed_pool,
        key=lambda fmt: (
            0 if _is_identifiable_original(fmt, original_language, labeled) else 1,
            fmt.get("height") or limit,
            fmt.get("tbr") or fmt.get("abr") or 10**9,
            str(fmt.get("format_id") or ""),
        ),
    )
    _log_hls_audio_choice(chosen, len(muxed))
    return chosen


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


def _discard_output(dest_stem: Path) -> None:
    """Remove a failed attempt so the next retry cannot reuse a partial file."""
    parent = dest_stem.parent
    if not parent.exists():
        return
    for path in parent.glob(dest_stem.name + ".*"):
        if path.is_file():
            path.unlink(missing_ok=True)


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
        "downloader_options": fmt.get("downloader_options") or {},
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
    try:
        with _proxy_client(params) as ydl:
            ydl.process_info(info)
    except Exception as exc:
        _account_failed_media(
            options, dest_stem, exc, extensions={"." + info["ext"]},
            include_incomplete=fragments is None,
            incomplete_filename=str(dest_stem) + "." + info["ext"],
        )
        raise
    return _find_download(dest_stem)


def _metadata_ready(options: dict[str, Any]) -> None:
    """Keep a proven route even if format selection or media transfer later fails."""
    callback = options.get("_youtube_metadata_ready")
    if callback:
        callback()


def extract_video_info(url: str, options: dict[str, Any]) -> dict:
    params = dict(options)
    params.pop("download_ranges", None)
    params.update({"skip_download": True, "quiet": True, "noplaylist": True})
    with _proxy_client(params) as ydl:
        info = ydl.extract_info(url, download=False)
    if not isinstance(info, dict):
        raise YouTubeDownloadError("yt-dlp returned no video info")
    _metadata_ready(options)
    return info


def extract_video_info_with_proxy(
    url: str,
    video_id: str,
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
    deadline: Optional[float] = None,
) -> dict:
    """Metadata preflight gets the same bounded region recovery as media."""
    return _with_proxy_retry(
        lambda options: extract_video_info(url, options),
        task_id=task_id, video_id=video_id,
        budget=budget or ByteBudget(get_config().youtube_download_max_bytes),
        cleanup=lambda: None, deadline=deadline,
    )


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
    counter = budget or ByteBudget(get_config().youtube_download_max_bytes)
    return _with_proxy_retry(
        lambda options: _download_audio_attempt(
            url, video_id, dest_stem, task_id=task_id, budget=counter, options=options,
        ),
        task_id=task_id, video_id=video_id, budget=counter,
        cleanup=lambda: _discard_output(dest_stem),
    )


def _download_audio_attempt(
    url: str,
    video_id: str,
    dest_stem: Path,
    *,
    task_id: Optional[str],
    budget: ByteBudget,
    options: dict[str, Any],
) -> Path:
    """Download modest full-duration audio. ``dest_stem`` has no suffix.

    The first attempt is unchanged: the modest https/m4a format (typically
    itag 140) through the native downloader. A 403 or yt-dlp's "unable to
    download video data" error retries once with an HLS audio format from
    that same info response. The format dict is passed to ``process_info``
    so ffmpeg is not asked to fetch a remote playlist.
    """
    counter = budget
    started = counter.snapshot()
    logged_failure = False
    hls_retry = False

    def _fail(kind: str, exc: BaseException, attempt_started: int) -> None:
        nonlocal logged_failure
        logged_failure = True
        log_youtube_attempt(
            provider=YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY,
            video_id=video_id,
            kind=kind,
            outcome="failure",
            nbytes=max(0, counter.snapshot() - attempt_started),
            task_id=task_id,
            error=str(exc),
        )

    try:
        info = extract_video_info(url, options)
        formats = info.get("formats") or []
        source_language = _video_language(info)
        original_audio = _original_audio_resolution(formats, source_language)
        fmt = pick_audio_format(
            formats,
            modest=True,
            source_language=source_language,
            original_audio=original_audio,
        )
        if not fmt:
            raise YouTubeDownloadError("No audio format is available")
        attempt_started = counter.snapshot()
        try:
            path = native_download_format(options, url, video_id, fmt, dest_stem)
        except (DownloadBudgetExceeded, DownloadDeadlineExceeded):
            raise
        except Exception as exc:
            if not is_forbidden_media_error(exc):
                raise
            _fail("audio", exc, attempt_started)
            excluded: set[str] = set()
            if "m3u8" in (fmt.get("protocol") or ""):
                excluded.add(str(fmt.get("format_id") or ""))
            hls = pick_hls_audio_format(
                formats,
                exclude_ids=excluded,
                source_language=source_language,
                original_audio=original_audio,
            )
            if hls is None:
                raise AudioDownloadForbidden("No HLS audio format is available") from exc
            hls_retry = True
            report_download_recovery(DownloadRecovery.AUDIO)
            logger.info(
                "youtube_audio_fallback retrying via=hls_audio video_id=%s format_id=%s protocol=%s task_id=%s",
                video_id,
                hls.get("format_id") or "-",
                hls.get("protocol") or "-",
                task_id or "-",
            )
            _discard_output(dest_stem)
            attempt_started = counter.snapshot()
            try:
                path = native_download_format(options, url, video_id, hls, dest_stem)
            except DownloadBudgetExceeded as budget_exc:
                _fail("audio_hls", budget_exc, attempt_started)
                raise
            except Exception as hls_exc:
                _discard_output(dest_stem)
                _fail("audio_hls", hls_exc, attempt_started)
                raise AudioDownloadForbidden("HLS audio download failed") from hls_exc
        nbytes = _account_file(path, counter, started if not hls_retry else attempt_started)
        log_youtube_attempt(
            provider=YOUTUBE_DOWNLOAD_PROVIDER_YTDLP_PROXY,
            video_id=video_id,
            kind="audio_hls" if hls_retry else "audio",
            outcome="success",
            nbytes=nbytes,
            task_id=task_id,
            fallback="hls_audio" if hls_retry else None,
        )
        return path
    except Exception as exc:
        if not logged_failure:
            _fail("audio_hls" if hls_retry else "audio", exc, started)
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
    fallback: Optional[str] = None,
) -> tuple[Path, float, float]:
    counter = budget or ByteBudget(get_config().youtube_download_max_bytes)
    return _with_proxy_retry(
        lambda options: _download_section_attempt(
            url, video_id, start, end, dest, task_id=task_id, budget=counter,
            fallback=fallback, options=options,
        ),
        task_id=task_id, video_id=video_id, budget=counter,
        cleanup=lambda: dest.unlink(missing_ok=True),
    )


def _download_section_attempt(
    url: str,
    video_id: str,
    start: float,
    end: float,
    dest: Path,
    *,
    task_id: Optional[str],
    budget: ByteBudget,
    fallback: Optional[str],
    options: dict[str, Any],
) -> tuple[Path, float, float]:
    """Download one time window. Returns the file and its original-timeline bounds.

    Media bytes go through yt-dlp. ffmpeg only trims and muxes local files.
    """
    counter = budget
    before = counter.snapshot()
    work = dest.parent / f".parts-{dest.stem}"
    try:
        info = extract_video_info(url, options)
        formats = info.get("formats") or []
        source_language = _video_language(info)
        original_audio = _original_audio_resolution(formats, source_language)
        video_fmt = pick_video_format(formats)
        audio_fmt = pick_audio_format(
            formats,
            modest=False,
            source_language=source_language,
            original_audio=original_audio,
        )
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
            fallback=fallback,
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


def _account_failed_media(
    options: dict[str, Any],
    stem: Path,
    exc: BaseException,
    *,
    extensions: Optional[set[str]] = None,
    include_incomplete: bool = True,
    incomplete_filename: Optional[str] = None,
) -> None:
    """Charge an attempt's bytes missed between socket reads and progress hooks.

    ``stem`` must belong to this attempt. Local merged output contains already
    charged component bytes and is excluded even if yt-dlp removed those inputs.
    """
    count_bytes = options.get("_youtube_count_bytes")
    if count_bytes is None:
        return
    extensions = extensions or {".mp4", ".m4a", ".webm", ".mkv", ".opus", ".ogg", ".mp3"}
    seen = options.get("_youtube_seen_bytes", {})
    component_prefix = str(stem) + ".f"
    media = [
        path for path in stem.parent.glob(stem.name + ".*")
        if path.is_file() and Path(str(path).removesuffix(".part")).suffix.lower() in extensions
    ]
    has_components = any(name.startswith(component_prefix) for name in seen) or any(
        str(path).startswith(component_prefix) for path in media
    )
    if has_components:
        media = [path for path in media if str(path).startswith(component_prefix)]
    incomplete = re.search(r"\b(\d+) bytes read, \d+ more expected\b", str(exc)) if include_incomplete else None
    for path in media:
        count_bytes({
            "status": "downloading", "filename": str(path).removesuffix(".part"),
            "downloaded_bytes": path.stat().st_size,
        })
    if incomplete:
        # A complete video can remain while the next audio response fails before
        # its first write. Only an explicit name or a unique partial identifies
        # that response; a sole completed file is not evidence of ownership.
        partials = [path for path in media if path.suffix == ".part"]
        filename = incomplete_filename
        if filename is None and len(partials) == 1:
            filename = str(partials[0]).removesuffix(".part")
        if filename is None:
            filename = str(stem) + ".incomplete"
        count_bytes({"status": "downloading", "filename": filename,
                     "downloaded_bytes": int(incomplete.group(1))})


def download_full_with_proxy(
    url: str,
    video_id: str,
    dest_dir: Path,
    *,
    task_id: Optional[str] = None,
    budget: Optional[ByteBudget] = None,
    quality: Optional[str] = None,
) -> Path:
    """Download into owned staging paths, preserving shared media and sidecars."""
    runtime = get_config()
    counter = budget or ByteBudget(runtime.youtube_download_max_bytes)
    dest_dir.mkdir(parents=True, exist_ok=True)
    job_dir = Path(tempfile.mkdtemp(prefix=f"proxy-{video_id}-", dir=dest_dir))

    def download(options):
        before = counter.snapshot()
        with tempfile.TemporaryDirectory(prefix="attempt-", dir=job_dir) as directory:
            stem = Path(directory) / video_id

            def metadata_available(_info, *, incomplete=False):
                if not incomplete:
                    _metadata_ready(options)
                return None

            options.update({
                "format": video_format_selector(int(quality) if quality else None),
                "merge_output_format": "mp4",
                "outtmpl": str(stem) + ".%(ext)s",
                "match_filter": metadata_available,
            })
            try:
                with _proxy_client(options) as ydl:
                    ydl.download([url])
            except Exception as exc:
                _account_failed_media(options, stem, exc)
                raise
            path = _find_download(stem)
            _account_file(path, counter, before)
            result = job_dir / (video_id + path.suffix)
            path.replace(result)
            return result

    try:
        result = _with_proxy_retry(
            download, task_id=task_id, video_id=video_id, budget=counter,
            cleanup=lambda: None,  # Each operation owns a TemporaryDirectory.
        )
        # Uncached-source deployments keep their transcript beside the old
        # shared download. A unique download still reuses those word timings.
        from .youtube_source import copy_transcript_sidecar

        copy_transcript_sidecar(dest_dir / f"{video_id}.mp4", result)
        return result
    except BaseException:
        shutil.rmtree(job_dir, ignore_errors=True)
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
