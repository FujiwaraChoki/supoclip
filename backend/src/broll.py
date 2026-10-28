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
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import httpx

from .config import get_config

logger = logging.getLogger(__name__)

PEXELS_SEARCH_URL = "https://api.pexels.com/videos/search"
PEXELS_VIDEO_URL = "https://api.pexels.com/videos/videos/{id}"

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
# Spoken words around a cutaway that its footage is judged against.
CONTEXT_BEFORE_SECONDS = 5.0
CONTEXT_AFTER_SECONDS = 2.0

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

_search_cache: Dict[tuple, tuple[float, List[Dict[str, Any]]]] = {}
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
    query: str,
    orientation: str = "portrait",
    per_page: int = SEARCH_RESULTS_PER_QUERY,
    page: int = 1,
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

    key = (query.lower(), orientation, per_page, page)
    cached = _search_cache.get(key)
    if cached and time.time() - cached[0] < SEARCH_CACHE_TTL_SECONDS:
        return cached[1]
    if time.time() < _rate_limited_until:
        return []

    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                PEXELS_SEARCH_URL,
                params={"query": query, "orientation": orientation, "per_page": per_page, "page": page},
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
    if orientation == "square":
        return True
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
    query: str = "",
    context: str = "",
) -> Optional[Dict[str, Any]]:
    """The most on-topic result that is long enough, oriented right and unused.

    With a ``query``, results whose description is off-topic or carries a tone
    the spoken ``context`` does not (see ``footage_mismatch``) are skipped, and
    the one sharing the most words with the query wins; ties keep Pexels' order.
    """
    used = set(used_ids)
    best, best_score = None, -1
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
        if not query:
            return video
        try:
            reason = footage_mismatch(video, query, context)
            score = footage_relevance(video, query)
        except Exception:
            # Judging is a refinement: an odd result must not cost the clip its B-roll.
            logger.warning("Could not judge B-roll %s; using it unjudged", video.get("id"), exc_info=True)
            reason, score = None, 0
        if reason:
            logger.info("Skipping B-roll %s for %r: %s", video.get("id"), query, reason)
            continue
        if score > best_score:
            best, best_score = video, score
    return best


# --------------------------------------------------------------------------
# Relevance and tone
# --------------------------------------------------------------------------

# Footage whose description carries one of these themes is used only when the
# spoken context is about the same theme: a neutral line about a dark bedroom
# must not cut to "a depressed woman lying on bed with pills". Terms ending in
# "*" match any word they start.
SENSITIVE_THEMES: Dict[str, tuple[str, ...]] = {
    "distress": (
        "depress*", "anxi*", "lonel*", "grief", "griev*", "cry", "cries", "crying", "tears",
        "sad", "sadness", "sorrow*", "despair*", "stress", "stressed", "upset", "panic*",
        "suicid*", "frustrat*", "heartbr*", "mourn*",
    ),
    "medical": (
        "pill", "pills", "medic*", "drug*", "hospital", "hospitals", "hospitalized",
        "patient", "patients", "syringe*", "inject*",
        "surgery", "surgical", "sick*", "illness", "disease*", "overdos*", "ambulance*",
        "injur*", "wound*", "therapist*", "psychiatr*",
    ),
    "substances": (
        "alcohol*", "drunk*", "cigarett*", "smoking", "vape", "vaping", "cocaine",
        "heroin", "marijuana", "cannabis", "addict*", "hangover*",
    ),
    "violence": (
        "violen*", "blood*", "gun", "guns", "weapon*", "knife", "knives", "assault*",
        "abus*", "murder*", "kill*", "crime*", "criminal*", "arrest*", "prison*",
        "explosion*", "war", "fighting",
    ),
    "death": (
        "death", "dead", "die", "dying", "funeral*", "coffin*", "grave", "graves",
        "cemetery", "corpse*",
    ),
    "accident": ("accident*", "crash*", "disaster*", "wreck*"),
    "adult": ("sexy", "lingerie", "naked", "nude", "erotic*", "seductive*"),
}

# Words that say nothing about what footage shows, so sharing one is no match.
_GENERIC_WORDS = frozenset(
    """a an and are as at be by for from in into is it its of on or over the this to
    under up with while near off out close shot view video footage stock clip scene
    background person people man men woman women guy girl boy someone wearing holding
    using doing looking""".split()
)


def _tokens(text: str) -> List[str]:
    return re.findall(r"[a-z0-9]+", (text or "").lower())


def _content_words(text: str) -> set[str]:
    words = set()
    for token in _tokens(text):
        if token in _GENERIC_WORDS or token.isdigit():
            continue
        for suffix in ("ing", "ed", "s", "y"):
            if token.endswith(suffix) and len(token) - len(suffix) >= 3:
                token = token[: -len(suffix)]
                break
        words.add(token)
    return words


# Second halves of compounds that still show their first word ("bed" -> "bedroom").
_COMPOUND_TAILS = frozenset({"room", "time", "side", "light", "shine", "rise", "set"})


def _same_word(a: str, b: str) -> bool:
    short, long = sorted((a, b), key=len)
    if a == b:
        return True
    if len(short) < 3 or not long.startswith(short):
        return False
    tail = long[len(short):]
    return (len(short) >= 4 and len(tail) <= 2) or tail in _COMPOUND_TAILS


def footage_description(video: Dict[str, Any]) -> str:
    """What a stock result shows: its title/alt/tags, else the slug of its page URL.

    Pexels page URLs read like ``/video/a-depressed-woman-lying-on-bed-with-pills-7279741/``.
    """
    parts = [str(video.get(key) or "") for key in ("title", "alt", "description")]
    tags = video.get("tags")
    if isinstance(tags, (list, tuple)):
        parts += [str(tag.get("name") if isinstance(tag, dict) else tag) for tag in tags if tag]
    match = re.search(r"/video/([^/?#]+)", str(video.get("url") or ""))
    if match:
        parts.append(re.sub(r"-?\d+$", "", match.group(1)).replace("-", " "))
    return " ".join(part for part in parts if part.strip())


def sensitive_themes(text: str) -> set[str]:
    themes = set()
    tokens = _tokens(text)
    for theme, terms in SENSITIVE_THEMES.items():
        for term in terms:
            if term.endswith("*"):
                hit = any(token.startswith(term[:-1]) for token in tokens)
            else:
                hit = term in tokens
            if hit:
                themes.add(theme)
                break
    return themes


def footage_relevance(video: Dict[str, Any], query: str) -> int:
    """How many of the query's descriptive words the footage's description shares."""
    described = _content_words(footage_description(video))
    return sum(1 for word in _content_words(query) if any(_same_word(word, d) for d in described))


def footage_mismatch(video: Dict[str, Any], query: str, context: str = "") -> Optional[str]:
    """Why a stock result does not fit the cue, or None when it does.

    Footage is rejected when its description brings a sensitive theme that
    neither the query nor the spoken ``context`` is about, or when it is
    described and shares no descriptive word with the query. Results without a
    description cannot be judged and are allowed.
    """
    description = footage_description(video)
    if not description:
        return None
    unwanted = sensitive_themes(description) - sensitive_themes(f"{query} {context}")
    if unwanted:
        return f"implies {', '.join(sorted(unwanted))}"
    described = _content_words(description)
    wanted = _content_words(query)
    if described and wanted and not any(_same_word(w, d) for w in wanted for d in described):
        return "off-topic"
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


def cue_context(words: Sequence[Dict[str, Any]], start: float, duration: float) -> str:
    """What the speaker says around a cutaway, for judging its footage."""
    first, last = start - CONTEXT_BEFORE_SECONDS, start + duration + CONTEXT_AFTER_SECONDS
    return " ".join(
        str(word.get("text") or "").strip()
        for word in words
        if isinstance(word.get("start"), (int, float)) and first <= word["start"] <= last
    )


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
        video = choose_video(
            videos,
            cue["duration"] + 0.5,
            orientation,
            session.used_ids,
            query=" ".join(dict.fromkeys((query, cue["query"]))),
            context=cue.get("context", ""),
        )
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
            "footage_duration": float(video.get("duration") or 0),
            "thumbnail": video.get("image"),
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
    for cue in cues:
        cue["context"] = cue_context(words, cue["start"], cue["duration"])
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


# --------------------------------------------------------------------------
# Editor: search, lookup and conversion between placements and editor clips
# --------------------------------------------------------------------------


def footage_from_video(video: Dict[str, Any], target_short_side: int, orientation: str) -> Optional[Dict[str, Any]]:
    """Editor footage (``BRollFootage`` fields) for a Pexels video, or None."""
    chosen = pick_video_file(video, target_short_side, orientation)
    if not chosen or not str(chosen["link"]).startswith("https://videos.pexels.com/"):
        return None
    try:
        duration = float(video.get("duration") or 0)
    except (TypeError, ValueError):
        return None
    if duration <= 0:
        return None
    user = video.get("user") or {}
    return {
        "provider": "pexels",
        "id": int(video["id"]),
        "width": chosen["width"],
        "height": chosen["height"],
        "duration": duration,
        "fileUrl": chosen["link"],
        "pageUrl": str(video.get("url") or "")[:500],
        "thumbnail": str(video.get("image") or "")[:500],
        "credit": str(user.get("name") or "")[:200],
    }


async def search_footage(
    query: str, orientation: str = "portrait", target_short_side: int = 1080, page: int = 1
) -> List[Dict[str, Any]]:
    videos = await search_pexels_videos(query, orientation, per_page=18, page=page)
    results = []
    for video in videos:
        footage = footage_from_video(video, target_short_side, orientation)
        if footage:
            results.append(footage)
    return results


async def fetch_pexels_video(video_id: int) -> Optional[Dict[str, Any]]:
    """One Pexels video by id (to re-resolve a rendition's download link)."""
    api_key = get_config().pexels_api_key
    if not api_key or time.time() < _rate_limited_until:
        return None
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            response = await client.get(
                PEXELS_VIDEO_URL.format(id=int(video_id)), headers={"Authorization": api_key}
            )
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        video = response.json()
    except ValueError:
        return None
    return video if isinstance(video, dict) else None


async def ensure_footage_file(footage: Dict[str, Any]) -> Optional[Path]:
    """Local copy of editor footage; re-resolves the link if the stored one fails."""
    entry = {
        "pexels_id": footage["id"],
        "width": footage["width"],
        "height": footage["height"],
        "file_url": footage.get("fileUrl"),
    }
    path = await ensure_broll_file(entry)
    if path is not None:
        return path
    video = await fetch_pexels_video(int(footage["id"]))
    for item in (video or {}).get("video_files") or []:
        link = str(item.get("link") or "")
        if (
            item.get("width") == footage["width"]
            and item.get("height") == footage["height"]
            and link.startswith("https://videos.pexels.com/")
        ):
            return await ensure_broll_file({**entry, "file_url": link})
    return None


def placement_to_editor_clip(placement: Dict[str, Any], index: int, clip_duration: float) -> Optional[Dict[str, Any]]:
    """An editor B-roll clip from a generated clip's stored placement.

    The editor's clean.mp4 is rendered from the same ranges as the generated
    clip, so placement seconds are already on its timeline.
    """
    from .editor_document import BROLL_CONTINUATION_MIN_SECONDS, BROLL_MIN_SECONDS, TIME_EPSILON

    try:
        start = max(0.0, float(placement["start"]))
        end = min(clip_duration, start + float(placement["duration"]))
        offset = float(placement.get("footage_offset") or 0.0)
        file_url = str(placement.get("file_url") or "")
        duration = float(placement.get("footage_duration") or 0) or offset + end - start
        fade_in = placement.get("fade_in", True) is not False
        fade_out = placement.get("fade_out", True) is not False
        minimum = BROLL_MIN_SECONDS if fade_in and fade_out else BROLL_CONTINUATION_MIN_SECONDS
        if end - start < minimum - TIME_EPSILON or not file_url.startswith("https://videos.pexels.com/"):
            return None
        return {
            "id": f"broll-{index}-{int(placement['pexels_id'])}",
            "start": round(start, 3),
            "end": round(end, 3),
            "offset": round(offset, 3),
            "query": str(placement.get("query") or "")[:120],
            "fadeIn": fade_in,
            "fadeOut": fade_out,
            "fadeDuration": placement.get("fade_duration"),
            "footage": {
                "provider": "pexels",
                "id": int(placement["pexels_id"]),
                "width": int(placement["width"]),
                "height": int(placement["height"]),
                "duration": round(duration, 3),
                "fileUrl": file_url,
                "pageUrl": str(placement.get("pexels_url") or "")[:500],
                "thumbnail": str(placement.get("thumbnail") or "")[:500],
                "credit": str(placement.get("credit") or "")[:200],
            },
        }
    except (KeyError, TypeError, ValueError):
        return None


def editor_piece_to_placement(piece: Dict[str, Any]) -> Dict[str, Any]:
    """A clip placement (sidecar format) for a rendered editor B-roll piece."""
    footage = piece["footage"]
    footage = footage.model_dump() if hasattr(footage, "model_dump") else footage
    return {
        "start": round(piece["start"], 3),
        "duration": round(piece["duration"], 3),
        "footage_offset": round(piece.get("offset") or 0.0, 3),
        "query": piece.get("query") or "",
        "provider": "pexels",
        "pexels_id": footage["id"],
        "pexels_url": footage.get("pageUrl"),
        "file_url": footage["fileUrl"],
        "width": footage["width"],
        "height": footage["height"],
        "footage_duration": footage["duration"],
        "thumbnail": footage.get("thumbnail"),
        "credit": footage.get("credit"),
        "fade_in": piece.get("fade_in", True),
        "fade_out": piece.get("fade_out", True),
        "fade_duration": piece.get("fade_duration"),
    }
