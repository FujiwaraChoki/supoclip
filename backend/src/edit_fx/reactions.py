"""Reaction GIF inserts (§3.2, §8.5 rule 11): the GIPHY provider behind ``ReactionCue``.

The resolver places a ``ReactionCue`` with a search ``query`` and no asset. ``attach_reactions`` searches GIPHY
(``rating=pg-13``), takes the top 8 results that have an mp4 rendition of at most 8 MB, picks one with the plan's
seed (``cue_seed(rng_seed, cue.id, "reaction")``) and records its id and rendition URL on the cue. Any failure for a
cue drops that cue: a reaction is decorative and must never cost the clip (§0.4.7).

``ensure_reaction_file`` turns a cue into a local mp4 in a render's scratch directory. Like B-roll footage, files are
cached on this machine (``TEMP_DIR/fx_reaction_cache``) and, when ``MEDIA_STORAGE=s3``, in the bucket under
``fx-reactions/giphy/``, so a render worker on another machine reuses the copy instead of calling GIPHY again.

Everything is opt-in: with ``REACTIONS_PROVIDER=off`` (the default) or no ``GIPHY_API_KEY`` reactions are unavailable,
``attach_reactions`` drops reaction cues it would have to resolve, and ``ensure_reaction_file`` returns None.
Downloads only ever go to GIPHY's media hosts, also across redirects, because a studio document's cue URL is
user-editable.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import time
import uuid
import weakref
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from .contracts import FxPlan, ReactionCue
from .env import giphy_api_key, reactions_provider
from .rng import cue_seed, hash01

logger = logging.getLogger(__name__)

GIPHY_SEARCH_URL = "https://api.giphy.com/v1/gifs/search"
SEARCH_LIMIT = 8                      # the seeded pick is among the top 8 results
SEARCH_RATING = "pg-13"
MAX_RENDITION_BYTES = 8 * 1024 * 1024
# Largest first: the PiP covers up to 0.70 W, so a bigger rendition looks sharper when it fits the size cap.
RENDITION_ORDER = ("original_mp4", "original", "downsized_small", "fixed_height", "fixed_width")
SEARCH_TIMEOUT = httpx.Timeout(8.0, connect=4.0)
DOWNLOAD_TIMEOUT = httpx.Timeout(20.0, connect=5.0)
# httpx timeouts bound each read, not the whole transfer: a server that trickles bytes is cut off by these.
SEARCH_DEADLINE_SECONDS = 15.0
DOWNLOAD_DEADLINE_SECONDS = 60.0
SEARCH_CACHE_TTL_SECONDS = 6 * 3600
SEARCH_CACHE_MAX_ENTRIES = 256
RATE_LIMIT_PAUSE_SECONDS = 15 * 60
BUCKET_PREFIX = ("fx-reactions", "giphy")
CACHE_DIRNAME = "fx_reaction_cache"
CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
CACHE_MAX_BYTES = 512 * 1024 * 1024
CACHE_IN_USE_SECONDS = 2 * 3600
MAX_QUERY_CHARS = 60

_MEDIA_HOST = re.compile(r"^(?:media\d*|i)\.giphy\.com$")
_ASSET_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_URL_MAX = 600

# Tests swap this for an ``httpx.MockTransport``; None uses the default network transport.
_transport: httpx.AsyncBaseTransport | None = None
_search_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_rate_limited_until = 0.0
# One lock per file and event loop: a render thread may run its own loop, and an asyncio.Lock is bound to one loop.
# Across loops and processes the atomic ``os.replace`` of each download keeps the cache consistent.
_file_locks: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = weakref.WeakKeyDictionary()


class _ReactionUnavailable(Exception):
    """A search or download that cannot produce a usable reaction (logged by type, never raised to callers)."""


def reactions_available() -> bool:
    """True when reactions can be resolved here: ``REACTIONS_PROVIDER=giphy`` and a GIPHY key."""
    return reactions_provider() == "giphy" and bool(giphy_api_key())


# ----------------------------------------------------------------------------- URLs


def _media_url(raw: object) -> str | None:
    """A GIPHY media URL without its tracking query, or None when it is not one (https, GIPHY media host only)."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        parts = urlsplit(raw.strip())
        port = parts.port              # raises ValueError for a non-numeric or out-of-range port
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or not _MEDIA_HOST.match(host) or port not in (None, 443):
        return None
    if parts.username or parts.password or not parts.path.startswith("/") or ".." in parts.path.split("/"):
        return None
    url = urlunsplit(("https", host, parts.path, "", ""))
    return url if len(url) <= _URL_MAX else None


def _size(value: object) -> int | None:
    try:
        size = int(str(value))
    except (TypeError, ValueError):
        return None
    return size if size > 0 else None


def _rendition(item: dict[str, Any]) -> str | None:
    """The best mp4 rendition URL of one search result that fits ``MAX_RENDITION_BYTES``."""
    images = item.get("images")
    if not isinstance(images, dict):
        return None
    for name in RENDITION_ORDER:
        rendition = images.get(name)
        if not isinstance(rendition, dict):
            continue
        url = _media_url(rendition.get("mp4"))
        size = _size(rendition.get("mp4_size"))
        if url and size is not None and size <= MAX_RENDITION_BYTES:
            return url
    return None


def _candidates(items: list[dict[str, Any]]) -> list[tuple[str, str]]:
    """(asset id, rendition URL) for the top results that have a usable rendition, in GIPHY's relevance order."""
    found: list[tuple[str, str]] = []
    for item in items[:SEARCH_LIMIT]:
        asset_id = str(item.get("id") or "")
        url = _rendition(item)
        if _ASSET_ID.match(asset_id) and url:
            found.append((asset_id, url))
    return found


# ----------------------------------------------------------------------------- HTTP


async def _only_giphy_media(request: httpx.Request) -> None:
    host = (request.url.host or "").lower()
    if request.url.scheme != "https" or not _MEDIA_HOST.match(host):
        raise _ReactionUnavailable("redirect outside GIPHY media")


def _client(timeout: httpx.Timeout, **kwargs: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=timeout, transport=_transport, **kwargs)


def _normalise_query(query: str) -> str:
    return " ".join((query or "").split())[:MAX_QUERY_CHARS]


async def _search(query: str) -> list[dict[str, Any]]:
    """GIPHY results for ``query`` (cached in-process). Raises ``_ReactionUnavailable`` on any failure."""
    global _rate_limited_until
    key = query.lower()
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < SEARCH_CACHE_TTL_SECONDS:
        return cached[1]
    if time.time() < _rate_limited_until:
        raise _ReactionUnavailable("rate limited")
    api_key = giphy_api_key()
    if not api_key:
        raise _ReactionUnavailable("no key")

    async with asyncio.timeout(SEARCH_DEADLINE_SECONDS), _client(SEARCH_TIMEOUT) as client:
        response = await client.get(GIPHY_SEARCH_URL, params={
            "api_key": api_key, "q": query, "limit": SEARCH_LIMIT, "offset": 0, "rating": SEARCH_RATING,
            "lang": "en",
        })
    if response.status_code == 429:
        _rate_limited_until = time.time() + RATE_LIMIT_PAUSE_SECONDS
        logger.warning("fx_reactions_rate_limited pause_seconds=%s", RATE_LIMIT_PAUSE_SECONDS)
        raise _ReactionUnavailable("rate limited")
    if response.status_code != 200:
        raise _ReactionUnavailable(f"search HTTP {response.status_code}")
    data = response.json().get("data")
    if not isinstance(data, list):
        raise _ReactionUnavailable("malformed search response")
    items = [item for item in data if isinstance(item, dict)]
    if len(_search_cache) >= SEARCH_CACHE_MAX_ENTRIES:
        _search_cache.pop(next(iter(_search_cache)))
    _search_cache[key] = (time.time(), items)
    return items


def _pick(candidates: list[tuple[str, str]], seed: int, taken: set[str]) -> tuple[str, str] | None:
    """Seeded choice among the candidates; a GIF another cue of this plan already uses is skipped when possible."""
    if not candidates:
        return None
    start = min(int(hash01(seed, 0) * len(candidates)), len(candidates) - 1)
    for step in range(len(candidates)):
        choice = candidates[(start + step) % len(candidates)]
        if choice[0] not in taken:
            return choice
    return candidates[start]


def _resolved(cue: ReactionCue) -> bool:
    return bool(_ASSET_ID.match(cue.asset_id or "")) and _media_url(cue.url) == cue.url


async def attach_reactions(plan: FxPlan, *, rng_seed: int) -> FxPlan:
    """Fill ``asset_id``/``url`` on every unresolved ``ReactionCue``; drop a cue on any error. Never raises.

    Cues that already carry a valid GIPHY asset (a re-render, a user pick) are kept as they are. When reactions are
    unavailable, unresolved reaction cues are dropped. A plan without reaction cues is returned unchanged.
    """
    if not any(c.kind == "reaction" for c in plan.cues):
        return plan
    available = reactions_available()
    taken = {c.asset_id for c in plan.cues if c.kind == "reaction" and _resolved(c)}
    kept: list = []
    dropped = 0
    for cue in plan.cues:
        if cue.kind != "reaction" or _resolved(cue):
            kept.append(cue)
            continue
        query = _normalise_query(cue.query)
        if not available or not query:
            dropped += 1
            continue
        try:
            choice = _pick(_candidates(await _search(query)), cue_seed(rng_seed, cue.id, "reaction"), taken)
        except Exception as exc:  # noqa: BLE001 - a reaction must never cost the clip
            logger.warning("fx_reaction_search_failed cue=%s error=%s", cue.id, type(exc).__name__)
            choice = None
        if choice is None:
            dropped += 1
            continue
        asset_id, url = choice
        taken.add(asset_id)
        kept.append(cue.model_copy(update={"asset_id": asset_id, "url": url}))
    if dropped:
        logger.info("fx_reactions_dropped count=%s available=%s", dropped, available)
    return plan.model_copy(update={"cues": tuple(kept)})


# ----------------------------------------------------------------------------- files


def reaction_cache_dir() -> Path:
    from ..config import get_config

    return Path(get_config().temp_dir) / CACHE_DIRNAME


def _file_name(cue: ReactionCue) -> str:
    rendition = hashlib.sha256(cue.url.encode("utf-8")).hexdigest()[:12]
    return f"giphy_{cue.asset_id}_{rendition}.mp4"


def _usable(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _is_playable_video(path: Path) -> bool:
    from ..media.ffmpeg import ffprobe_duration, ffprobe_video_size

    try:
        width, height = ffprobe_video_size(path)
        return width > 0 and height > 0 and ffprobe_duration(path) > 0
    except Exception:  # noqa: BLE001 - any probe failure means "not playable"
        return False


def _place(cached: Path, destination: Path) -> Path:
    """Hard-link (else copy) a cached file into the render scratch, so pruning the cache never touches a render."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    try:
        try:
            os.link(cached, partial)
        except OSError:
            shutil.copyfile(cached, partial)
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)
    return destination


def prune_reaction_cache(
    max_age_seconds: float = CACHE_MAX_AGE_SECONDS,
    max_bytes: int = CACHE_MAX_BYTES,
    in_use_seconds: float = CACHE_IN_USE_SECONDS,
) -> None:
    """Drop cached reactions unused for a week, then the oldest until under the size cap (recent ones are kept)."""
    directory = reaction_cache_dir()
    if not directory.is_dir():
        return
    cutoff = time.time() - max_age_seconds
    kept = []
    for path in directory.glob("giphy_*.mp4"):
        try:
            stat = path.stat()
            if stat.st_mtime < cutoff:
                path.unlink(missing_ok=True)
            else:
                kept.append((stat.st_mtime, stat.st_size, path))
        except OSError:
            continue
    total = sum(size for _, size, _ in kept)
    protect_after = time.time() - in_use_seconds
    for mtime, size, path in sorted(kept):
        if total <= max_bytes or mtime >= protect_after:
            break
        path.unlink(missing_ok=True)
        total -= size


def _storage():
    from ..media_storage import MediaStorage

    storage = MediaStorage()
    return storage if storage.enabled else None


def _bucket_fetch(name: str, path: Path) -> bool:
    from ..media_storage import STORAGE_ERRORS

    storage = _storage()
    if storage is None:
        return False
    try:
        return storage.fetch(storage.reference(storage.key(*BUCKET_PREFIX, name)), path)
    except STORAGE_ERRORS + (ValueError,) as exc:
        logger.warning("fx_reaction_bucket_read_failed error=%s", type(exc).__name__)
        return False


def _bucket_store(name: str, path: Path) -> None:
    from ..media_storage import STORAGE_ERRORS

    storage = _storage()
    if storage is None:
        return
    try:
        storage.upload_file(path, storage.key(*BUCKET_PREFIX, name), CacheControl="public, max-age=31536000, immutable")
    except STORAGE_ERRORS + (ValueError,) as exc:
        logger.warning("fx_reaction_bucket_write_failed error=%s", type(exc).__name__)


async def _download(url: str, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    received = 0
    try:
        async with asyncio.timeout(DOWNLOAD_DEADLINE_SECONDS), _client(
            DOWNLOAD_TIMEOUT, follow_redirects=True, max_redirects=3, event_hooks={"request": [_only_giphy_media]},
        ) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    logger.warning("fx_reaction_download_failed status=%s", response.status_code)
                    return False
                with open(partial, "wb") as handle:
                    async for chunk in response.aiter_bytes(1 << 16):
                        received += len(chunk)
                        if received > MAX_RENDITION_BYTES:
                            logger.warning("fx_reaction_download_too_large limit=%s", MAX_RENDITION_BYTES)
                            return False
                        handle.write(chunk)
        if received == 0:
            return False
        os.replace(partial, destination)
        return True
    except (httpx.HTTPError, OSError, TimeoutError, _ReactionUnavailable) as exc:
        logger.warning("fx_reaction_download_failed error=%s", type(exc).__name__)
        return False
    finally:
        partial.unlink(missing_ok=True)


async def _cached_file(cue: ReactionCue) -> Path | None:
    """This machine's cached copy of the cue's GIF: local cache, then the bucket, then GIPHY."""
    name = _file_name(cue)
    cached = reaction_cache_dir() / name
    locks = _file_locks.setdefault(asyncio.get_running_loop(), {})
    lock = locks.setdefault(name, asyncio.Lock())
    async with lock:
        if _usable(cached):
            cached.touch()
            return cached
        cached.parent.mkdir(parents=True, exist_ok=True)
        if await asyncio.to_thread(_bucket_fetch, name, cached):
            if await asyncio.to_thread(_is_playable_video, cached):
                return cached
            cached.unlink(missing_ok=True)
        if not await _download(cue.url, cached):
            return None
        # A 200 can still be an error page or a truncated file; one bad input would fail the whole P1 graph.
        if not await asyncio.to_thread(_is_playable_video, cached):
            logger.warning("fx_reaction_not_playable asset=%s", cue.asset_id)
            cached.unlink(missing_ok=True)
            return None
        await asyncio.to_thread(_bucket_store, name, cached)
        await asyncio.to_thread(prune_reaction_cache)
        return cached


async def ensure_reaction_file(cue: ReactionCue, scratch: Path) -> Path | None:
    """The cue's GIF as a local mp4 inside ``scratch`` (a render's own directory); None when unavailable.

    Reuses this machine's cache and the bucket before downloading from GIPHY. Never raises: a missing reaction is
    skipped by the render.
    """
    try:
        if reactions_provider() == "off" or getattr(cue, "kind", None) != "reaction" or not _resolved(cue):
            return None
        destination = Path(scratch) / _file_name(cue)
        if _usable(destination):
            return destination
        cached = await _cached_file(cue)
        if cached is None:
            return None
        return await asyncio.to_thread(_place, cached, destination)
    except Exception as exc:  # noqa: BLE001 - decorative layers never cost the clip
        logger.warning("fx_reaction_file_failed cue=%s error=%s", getattr(cue, "id", "?"), type(exc).__name__)
        return None
