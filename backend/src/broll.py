"""
B-roll for generated clips: plan stock-footage cutaways and source them from Pexels.

Each clip gets its own plan. The model reads the clip's word-timed transcript
and picks the moments where the speaker names something filmable; the cutaways
are then placed on those words, sourced from Pexels at the clip's resolution,
and composited under the captions by the renderer (``media/broll_overlay.py``).

Placements are stored on the clip's source-map sidecar in clip seconds, so a
caption edit or regeneration of the same clip re-applies the same footage.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

from .config import get_config

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"

# Cutaway placement. The opening seconds stay on the speaker (and the hook
# title), and the clip ends on the speaker too.
EARLIEST_START_SECONDS = 3.0
END_GUARD_SECONDS = 1.0
MIN_CUE_SECONDS = 1.5
MAX_CUE_SECONDS = 4.0
MIN_GAP_SECONDS = 2.0
MAX_COVERAGE = 0.4
SECONDS_PER_CUE = 12.0
MAX_CUES_PER_CLIP = 4
WORD_SNAP_SECONDS = 1.0

SEARCH_RESULTS_PER_QUERY = 10
SEARCH_CACHE_TTL_SECONDS = 6 * 3600
SEARCH_CACHE_MAX_ENTRIES = 512
MAX_DOWNLOAD_BYTES = 150 * 1024 * 1024
# Footage is re-downloaded on demand, so the cache can stay small. Files used
# recently are kept even over the cap: a task prepares every clip's footage
# before its renders run.
CACHE_MAX_AGE_SECONDS = 24 * 3600
CACHE_MAX_BYTES = 2 * 1024 * 1024 * 1024
CACHE_IN_USE_SECONDS = 2 * 3600
HTTP_TIMEOUT = httpx.Timeout(30.0, connect=10.0)

_search_cache: Dict[tuple[str, str], tuple[float, List[Dict[str, Any]]]] = {}
_rate_limited_until = 0.0
_download_locks: Dict[str, asyncio.Lock] = {}


def broll_configured() -> bool:
    return bool(get_config().pexels_api_key)


def broll_cache_dir() -> Path:
    return Path(get_config().temp_dir) / "broll_cache"


# --------------------------------------------------------------------------
# Pexels search and file choice
# --------------------------------------------------------------------------


async def search_pexels_videos(
    query: str, orientation: str = "portrait", per_page: int = SEARCH_RESULTS_PER_QUERY
) -> List[Dict[str, Any]]:
    """Pexels results for ``query`` in relevance order; empty on any failure.

    Results are cached in-process, and a 429 pauses every search until the
    quota resets instead of spending the rest of the job on failing requests.
    """
    global _rate_limited_until
    api_key = get_config().pexels_api_key
    query = " ".join((query or "").split())[:80]
    if not api_key or not query:
        return []

    key = (query.lower(), orientation)
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < SEARCH_CACHE_TTL_SECONDS:
        return cached[1]
    if time.time() < _rate_limited_until:
        return []

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                PEXELS_SEARCH_URL,
                params={"query": query, "orientation": orientation, "per_page": per_page},
                headers={"Authorization": api_key},
            )
    except httpx.HTTPError as exc:
        logger.warning("Pexels search failed for %r: %s", query, type(exc).__name__)
        return []

    if response.status_code == 429:
        reset = response.headers.get("X-Ratelimit-Reset")
        try:
            _rate_limited_until = float(reset) if reset else time.time() + 3600
        except ValueError:
            _rate_limited_until = time.time() + 3600
        logger.warning("Pexels rate limit reached; B-roll paused until %s", int(_rate_limited_until))
        return []
    if response.status_code != 200:
        logger.warning("Pexels search returned HTTP %s for %r", response.status_code, query)
        return []

    try:
        videos = [video for video in response.json().get("videos", []) if isinstance(video, dict)]
    except ValueError:
        return []
    if len(_search_cache) >= SEARCH_CACHE_MAX_ENTRIES:
        _search_cache.pop(next(iter(_search_cache)))
    _search_cache[key] = (time.time(), videos)
    return videos


def _matches_orientation(width: int, height: int, orientation: str) -> bool:
    if orientation == "portrait":
        return height > width
    if orientation == "landscape":
        return width > height
    return True


def pick_video_file(
    video: Dict[str, Any], target_short_side: int, orientation: str
) -> Optional[Dict[str, Any]]:
    """The smallest MP4 rendition that still covers the output resolution.

    Pexels' ``quality`` label is unreliable (newer uploads leave it null and
    "hd" covers both 720p and 1080p), so this goes by the real dimensions.
    Falls back to the largest rendition when none is big enough.
    """
    files = []
    for item in video.get("video_files") or []:
        try:
            width, height = int(item.get("width") or 0), int(item.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if not item.get("link") or width <= 0 or height <= 0:
            continue
        if item.get("file_type") not in (None, "video/mp4"):
            continue
        files.append((width, height, item))
    oriented = [f for f in files if _matches_orientation(f[0], f[1], orientation)]
    candidates = oriented or files
    if not candidates:
        return None
    large_enough = [f for f in candidates if min(f[0], f[1]) >= target_short_side]
    if large_enough:
        width, height, item = min(large_enough, key=lambda f: f[0] * f[1])
    else:
        width, height, item = max(candidates, key=lambda f: f[0] * f[1])
    return {"link": item["link"], "width": width, "height": height}


def choose_video(
    videos: Sequence[Dict[str, Any]],
    min_duration: float,
    orientation: str,
    used_ids: Iterable[int] = (),
) -> Optional[Dict[str, Any]]:
    """First relevant result that is long enough, oriented right and unused."""
    used = set(used_ids)
    for video in videos:
        if video.get("id") in used:
            continue
        try:
            duration = float(video.get("duration") or 0)
            width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        except (TypeError, ValueError):
            continue
        if duration < min_duration:
            continue
        if width and height and not _matches_orientation(width, height, orientation):
            continue
        return video
    return None


# --------------------------------------------------------------------------
# Download cache
# --------------------------------------------------------------------------


def broll_file_path(entry: Dict[str, Any]) -> Path:
    return broll_cache_dir() / (
        f"pexels_{int(entry['pexels_id'])}_{int(entry['width'])}x{int(entry['height'])}.mp4"
    )


def prune_broll_cache(
    max_age_seconds: float = CACHE_MAX_AGE_SECONDS,
    max_bytes: int = CACHE_MAX_BYTES,
    in_use_seconds: float = CACHE_IN_USE_SECONDS,
) -> None:
    """Drop footage unused for a day, then the oldest until under the size cap.

    Footage used in the last ``in_use_seconds`` is never removed.
    """
    directory = broll_cache_dir()
    if not directory.is_dir():
        return
    cutoff = time.time() - max_age_seconds
    kept = []
    for path in directory.glob("pexels_*.mp4"):
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


async def _download(url: str, destination: Path) -> bool:
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.part")
    received = 0
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    logger.warning("B-roll download returned HTTP %s", response.status_code)
                    return False
                with open(partial, "wb") as handle:
                    async for chunk in response.aiter_bytes(1 << 16):
                        received += len(chunk)
                        if received > MAX_DOWNLOAD_BYTES:
                            logger.warning("B-roll download exceeded %s bytes", MAX_DOWNLOAD_BYTES)
                            return False
                        handle.write(chunk)
        if received == 0:
            return False
        os.replace(partial, destination)
        return True
    except (httpx.HTTPError, OSError) as exc:
        logger.warning("B-roll download failed: %s", type(exc).__name__)
        return False
    finally:
        partial.unlink(missing_ok=True)


async def ensure_broll_file(entry: Dict[str, Any]) -> Optional[Path]:
    """Local copy of a placement's footage, downloading it when not cached."""
    try:
        path = broll_file_path(entry)
    except (KeyError, TypeError, ValueError):
        return None
    lock = _download_locks.setdefault(str(path), asyncio.Lock())
    async with lock:
        if path.is_file() and path.stat().st_size > 0:
            path.touch()
            return path
        if not entry.get("file_url") or not await _download(entry["file_url"], path):
            return None
        # A 200 can still be an error page or a truncated file, and one bad
        # input would fail the whole clip render.
        if not await asyncio.to_thread(_is_playable_video, path):
            logger.warning("B-roll download for %s is not a playable video", entry.get("pexels_id"))
            path.unlink(missing_ok=True)
            return None
        return path


def _is_playable_video(path: Path) -> bool:
    from .media.ffmpeg import ffprobe_duration, ffprobe_video_size

    try:
        width, height = ffprobe_video_size(path)
        return width > 0 and height > 0 and ffprobe_duration(path) > 0
    except Exception:
        return False


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


def max_cues_for_duration(clip_duration: float) -> int:
    return max(1, min(MAX_CUES_PER_CLIP, round(clip_duration / SECONDS_PER_CUE)))


def timed_lines_from_words(words: Sequence[Dict[str, Any]], max_words: int = 8) -> str:
    """Clip transcript as short phrases, each prefixed with its start second."""
    lines: List[str] = []
    current: List[str] = []
    line_start = 0.0
    previous_end: Optional[float] = None
    for word in words:
        text = str(word.get("text") or "").strip()
        if not text:
            continue
        start = float(word.get("start") or 0.0)
        if current and (
            len(current) >= max_words
            or (previous_end is not None and start - previous_end > 0.6)
            or current[-1].endswith((".", "?", "!"))
        ):
            lines.append(f"[{line_start:.1f}] {' '.join(current)}")
            current = []
        if not current:
            line_start = max(0.0, start)
        current.append(text)
        previous_end = float(word.get("end") or start)
    if current:
        lines.append(f"[{line_start:.1f}] {' '.join(current)}")
    return "\n".join(lines)


def select_cues(
    cues: Sequence[Any],
    words: Sequence[Dict[str, Any]],
    clip_duration: float,
    max_cues: int,
) -> List[Dict[str, Any]]:
    """Validate the model's cues against the clip: snap, clamp, space, cap."""
    word_starts = sorted(
        float(word["start"]) for word in words if isinstance(word.get("start"), (int, float))
    )
    latest_end = clip_duration - END_GUARD_SECONDS
    candidates = []
    for cue in cues:
        query = " ".join(str(getattr(cue, "query", "") or "").split())[:80]
        try:
            start = float(getattr(cue, "start", 0))
            duration = float(getattr(cue, "duration", 0))
        except (TypeError, ValueError):
            continue
        if not query or not math.isfinite(start) or not math.isfinite(duration):
            continue
        # Land on the spoken word the model pointed at, a beat before it.
        nearest = min(word_starts, key=lambda s: abs(s - start), default=None)
        if nearest is not None and abs(nearest - start) <= WORD_SNAP_SECONDS:
            start = nearest - 0.1
        start = max(start, EARLIEST_START_SECONDS)
        duration = min(max(duration, MIN_CUE_SECONDS), MAX_CUE_SECONDS)
        end = min(start + duration, latest_end)
        if end - start < MIN_CUE_SECONDS:
            continue
        candidates.append(
            {
                "start": round(start, 3),
                "duration": round(end - start, 3),
                "query": query,
                "fallback_query": " ".join(str(getattr(cue, "fallback_query", "") or "").split())[:80],
            }
        )

    selected: List[Dict[str, Any]] = []
    covered = 0.0
    for cue in sorted(candidates, key=lambda c: c["start"]):
        if len(selected) >= max_cues:
            break
        if selected:
            previous = selected[-1]
            if cue["start"] < previous["start"] + previous["duration"] + MIN_GAP_SECONDS:
                continue
        if covered + cue["duration"] > clip_duration * MAX_COVERAGE:
            continue
        selected.append(cue)
        covered += cue["duration"]
    return selected


class BRollSession:
    """Per-task state, so one task never repeats the same stock clip."""

    def __init__(self) -> None:
        self.used_ids: set[int] = set()


async def _source_cue(
    cue: Dict[str, Any],
    orientation: str,
    target_short_side: int,
    session: BRollSession,
) -> Optional[Dict[str, Any]]:
    for query in dict.fromkeys(q for q in (cue["query"], cue.get("fallback_query")) if q):
        videos = await search_pexels_videos(query, orientation)
        video = choose_video(videos, cue["duration"] + 0.5, orientation, session.used_ids)
        if not video:
            continue
        chosen = pick_video_file(video, target_short_side, orientation)
        if not chosen:
            continue
        session.used_ids.add(video["id"])
        user = video.get("user") or {}
        return {
            "start": cue["start"],
            "duration": cue["duration"],
            "query": query,
            "provider": "pexels",
            "pexels_id": int(video["id"]),
            "pexels_url": video.get("url"),
            "file_url": chosen["link"],
            "width": chosen["width"],
            "height": chosen["height"],
            "credit": user.get("name"),
        }
    return None


async def plan_clip_broll(
    words: Sequence[Dict[str, Any]],
    clip_duration: float,
    orientation: str = "portrait",
    target_short_side: int = 1080,
    session: Optional[BRollSession] = None,
) -> List[Dict[str, Any]]:
    """Plan and download the cutaways for one clip.

    ``words`` are on the clip timeline (seconds from the clip start). Returns
    placements whose footage is cached locally; an empty list whenever B-roll
    is unconfigured, nothing in the clip is filmable, or a provider fails.
    The clip always renders, with or without B-roll.
    """
    if not broll_configured() or not words:
        return []
    if clip_duration < EARLIEST_START_SECONDS + MIN_CUE_SECONDS + END_GUARD_SECONDS:
        return []
    session = session or BRollSession()
    max_cues = max_cues_for_duration(clip_duration)

    from .ai import plan_broll_cues

    try:
        raw_cues = await plan_broll_cues(
            timed_lines_from_words(words), clip_duration, max_cues, EARLIEST_START_SECONDS
        )
    except Exception as exc:
        logger.warning("B-roll planning failed (%s); rendering without B-roll", type(exc).__name__)
        return []

    cues = select_cues(raw_cues, words, clip_duration, max_cues)
    if not cues:
        logger.info("No B-roll moments in this clip")
        return []

    await asyncio.to_thread(prune_broll_cache)
    # Searches run in order so a later cue never takes the stock clip an
    # earlier one picked; the downloads then run together.
    sourced = []
    for cue in cues:
        placement = await _source_cue(cue, orientation, target_short_side, session)
        if placement:
            sourced.append(placement)
        else:
            logger.info("No stock footage found for B-roll query %r", cue["query"])
    downloaded = await asyncio.gather(*(ensure_broll_file(p) for p in sourced))
    placements = [p for p, path in zip(sourced, downloaded) if path]
    logger.info(
        "B-roll: %d/%d cutaways sourced (%s)",
        len(placements),
        len(cues),
        ", ".join(p["query"] for p in placements) or "none",
    )
    return placements


async def broll_overlays_for(placements: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Renderer overlays for stored placements, re-downloading evicted footage."""
    overlays = []
    for placement in placements or []:
        path = await ensure_broll_file(placement)
        if path is None:
            logger.warning("B-roll footage %s unavailable; skipping it", placement.get("pexels_id"))
            continue
        overlays.append(
            {
                "path": path,
                "start": placement["start"],
                "duration": placement["duration"],
                "offset": float(placement.get("footage_offset") or 0.0),
            }
        )
    return overlays


def shift_broll_placements(
    placements: Iterable[Dict[str, Any]] | None, offset: float, duration: float
) -> List[Dict[str, Any]]:
    """Placements for a cut of the clip that starts ``offset`` seconds in.

    Cutaways that end up mostly outside the new clip are dropped; the rest are
    clipped to it. A cutaway cut at its head resumes its footage where the
    cut falls (``footage_offset``) instead of replaying it from the start.
    """
    shifted = []
    for placement in placements or []:
        start = float(placement["start"]) - offset
        end = start + float(placement["duration"])
        footage_offset = float(placement.get("footage_offset") or 0.0) + max(0.0, -start)
        start, end = max(0.0, start), min(duration, end)
        if end - start < MIN_CUE_SECONDS:
            continue
        moved = {**placement, "start": round(start, 3), "duration": round(end - start, 3)}
        if footage_offset > 0:
            moved["footage_offset"] = round(footage_offset, 3)
        shifted.append(moved)
    return shifted
