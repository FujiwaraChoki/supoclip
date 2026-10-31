"""The Source Outline: one cached reading of the whole video.

Series, topic, recap and trailer all plan from the same outline (chapters,
main points, story arcs, payoffs, key topics, central question), so it is
computed once per source and reused across workflows and users. It never sees
creator instructions or performance data, which keeps it shareable.

Short and medium sources are read in one call ("full" mode). Long ones are
mapped window by window and reduced in one comparative call ("long" mode).
Any window that still fails after its retry fails the plan: an outline with a
hole would quietly drop part of the video.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from ..config import get_config
from .contracts import OUTLINE_VERSION, PlanContext
from .boundaries import STRONG_BOUNDARY, nearest_strong_end
from .errors import WorkflowError
from .llm import PlanStats, build_agent, gather_all, is_cancellation, model_name, run, run_retrying
from .prompts import (
    chapter_spans,
    outline_reduce_system,
    outline_system,
    outline_window_system,
    transcript_block,
)
from .schemas import (
    OutlineArc,
    OutlineChapter,
    OutlinePayoff,
    OutlinePoint,
    OutlineReduce,
    OutlineWindow,
    SourceOutline,
)
from .units import UnitIndex, format_clock

logger = logging.getLogger(__name__)

# Whole transcript in one call up to this many words (about 3 h of speech for
# hosted models). Local models get less: their context fills and slows sooner.
FULL_CONTEXT_MAX_WORDS = 40_000
OLLAMA_FULL_CONTEXT_MAX_WORDS = 9_000
# Long mode: a window closes after this much time or these many words, at the
# strongest boundary within the next two minutes.
WINDOW_SECONDS = 20 * 60
WINDOW_MAX_WORDS = 9_000
OLLAMA_WINDOW_MAX_WORDS = 6_000
# Sizing listings: tokens per English word (timing columns included), the
# fixed cost of a line ("#412 41:20 +3.2s B: "), and Latin-script density.
TOKENS_PER_WORD = 1.9
LISTING_LINE_TOKENS = 8.0
LATIN_CHARS_PER_TOKEN = 3.0
LATIN_SCRIPT_END = 0x250
WINDOW_CLOSE_LOOKAHEAD_SECONDS = 120.0
# A leftover tail shorter than this share of a window joins the window before it.
WINDOW_MIN_TAIL_SHARE = 0.25
WINDOW_CONCURRENCY = 3
OUTLINE_CALL_TIMEOUT_SECONDS = 600
# A chapter end moves to a strong cut at most this many units away.
CHAPTER_SNAP_UNITS = 2

MAX_POINTS = 15
MAX_ARCS = 5
MAX_PAYOFFS = 10
MAX_KEY_TOPICS = 12

_INCOMPLETE = "We couldn't read the whole video. Please try again."


@dataclass
class OutlineResult:
    outline: SourceOutline
    mode: str  # "full" | "long"
    cached: bool


def _is_ollama(name: str) -> bool:
    return name.strip().lower().startswith("ollama:")


def full_context_word_limit() -> int:
    configured = get_config().workflow_full_context_max_words
    if configured:
        return int(configured)
    return OLLAMA_FULL_CONTEXT_MAX_WORDS if _is_ollama(model_name(structural=True)) else FULL_CONTEXT_MAX_WORDS


def estimated_tokens(text: str) -> float:
    """Rough model tokens for ``text``: Latin text runs about 3 characters a token,
    other scripts (Devanagari, CJK, Arabic, ...) about one."""
    latin = sum(1 for char in text if ord(char) < LATIN_SCRIPT_END)
    return latin / LATIN_CHARS_PER_TOKEN + (len(text) - latin)


def _unit_words(index: UnitIndex, uid: int) -> float:
    """Context one listing line takes, in English-word equivalents.

    Budgets are configured in words, but a word of Hindi or Japanese costs
    several times the tokens of an English one, so lines are measured by
    their estimated tokens instead of their word count.
    """
    return (estimated_tokens(index.unit(uid).text) + LISTING_LINE_TOKENS) / TOKENS_PER_WORD


def word_count(index: UnitIndex) -> float:
    """Size of the whole listing in English-word equivalents."""
    return sum(_unit_words(index, unit.id) for unit in index.units)


def outline_mode(index: UnitIndex) -> str:
    return "full" if word_count(index) <= full_context_word_limit() else "long"


# -- validation ------------------------------------------------------------------


def _clamp(value: Any, low: int, high: int) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        number = low
    return min(max(number, low), high)


def _span(first: Any, last: Any, low: int, high: int) -> tuple[int, int]:
    a, b = _clamp(first, low, high), _clamp(last, low, high)
    return (a, b) if a <= b else (b, a)


def _tile_chapters(
    chapters: Sequence[OutlineChapter], index: UnitIndex, low: int, high: int
) -> list[OutlineChapter]:
    """Contiguous chapters covering ``low..high``: sorted, deduplicated, ends on strong cuts."""
    tiled: list[OutlineChapter] = []
    previous = low - 1
    for chapter in sorted(chapters, key=lambda item: _clamp(item.end_unit, low, high)):
        end = _clamp(chapter.end_unit, low, high)
        if end < high:
            snapped = nearest_strong_end(index, end, CHAPTER_SNAP_UNITS)
            if snapped is not None and previous < snapped < high:
                end = snapped
        if end <= previous:
            continue
        tiled.append(
            chapter.model_copy(
                update={
                    "end_unit": end,
                    "importance": _clamp(chapter.importance, 1, 5),
                    "title": chapter.title.strip(),
                    "summary": chapter.summary.strip(),
                }
            )
        )
        previous = end
    if not tiled:
        tiled = [OutlineChapter(end_unit=high, title="", summary="", kind="content", importance=3)]
    tiled[-1] = tiled[-1].model_copy(update={"end_unit": high})
    return tiled


def _points(points: Sequence[OutlinePoint], low: int, high: int, limit: int) -> list[OutlinePoint]:
    seen: set[tuple[int, int]] = set()
    result: list[OutlinePoint] = []
    for point in points:
        full = _span(point.first_unit, point.last_unit, low, high)
        core = _span(point.core_first, point.core_last, low, high)
        if not (full[0] <= core[0] and core[1] <= full[1]):
            core = full
        if full in seen or not point.statement.strip():
            continue
        seen.add(full)
        result.append(
            point.model_copy(
                update={
                    "first_unit": full[0],
                    "last_unit": full[1],
                    "core_first": core[0],
                    "core_last": core[1],
                    "importance": _clamp(point.importance, 1, 5),
                    "statement": point.statement.strip(),
                }
            )
        )
    return result[:limit]


def _arcs(arcs: Sequence[OutlineArc], low: int, high: int, limit: int) -> list[OutlineArc]:
    result = []
    for arc in arcs:
        first, last = _span(arc.first_unit, arc.last_unit, low, high)
        result.append(
            arc.model_copy(
                update={"first_unit": first, "last_unit": last, "strength": _clamp(arc.strength, 1, 5)}
            )
        )
    return result[:limit]


def _payoffs(payoffs: Sequence[OutlinePayoff], low: int, high: int, limit: int) -> list[OutlinePayoff]:
    result = []
    seen: set[tuple[int, int]] = set()
    for payoff in payoffs:
        span = _span(payoff.first_unit, payoff.last_unit, low, high)
        if span in seen:
            continue
        seen.add(span)
        result.append(payoff.model_copy(update={"first_unit": span[0], "last_unit": span[1]}))
    return result[:limit]


def _topics(topics: Sequence[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for topic in topics:
        clean = " ".join(str(topic).split()).strip(" .,;:")
        key = clean.casefold()
        if clean and key not in seen:
            seen.add(key)
            result.append(clean)
    return result[:MAX_KEY_TOPICS]


def normalize_outline(outline: SourceOutline, index: UnitIndex) -> SourceOutline:
    """Ids clamped into the transcript, chapters re-tiled, cores inside their points."""
    count = len(index)
    return outline.model_copy(
        update={
            "language": outline.language.strip().lower()[:8],
            "one_line": outline.one_line.strip(),
            "central_question": outline.central_question.strip(),
            "throughline": outline.throughline.strip(),
            "key_topics": _topics(outline.key_topics),
            "chapters": _tile_chapters(outline.chapters, index, 1, count),
            "main_points": _points(outline.main_points, 1, count, MAX_POINTS),
            "story_arcs": _arcs(outline.story_arcs, 1, count, MAX_ARCS),
            "payoffs": _payoffs(outline.payoffs, 1, count, MAX_PAYOFFS),
        }
    )


def normalize_window(window: OutlineWindow, index: UnitIndex, low: int, high: int) -> OutlineWindow:
    return window.model_copy(
        update={
            "chapters": _tile_chapters(window.chapters, index, low, high),
            "main_points": _points(window.main_points, low, high, 6),
            "story_arcs": _arcs(window.story_arcs, low, high, 3),
            "payoffs": _payoffs(window.payoffs, low, high, MAX_PAYOFFS),
            "open_threads": [thread.strip() for thread in window.open_threads if thread.strip()][:8],
        }
    )


# -- long mode --------------------------------------------------------------------


def plan_windows(index: UnitIndex, *, max_words: int, max_seconds: float = WINDOW_SECONDS) -> list[tuple[int, int]]:
    """Consecutive, non-overlapping unit windows for the map step.

    A window closes once it reaches ``max_seconds`` or ``max_words``, at the
    strongest boundary within the next two minutes (speaker changes win
    ties). A short leftover tail joins the window before it.
    """
    count = len(index)
    windows: list[tuple[int, int]] = []
    start, words, uid = 1, 0.0, 1
    while uid <= count:
        words += _unit_words(index, uid)
        elapsed = index.unit(uid).end - index.unit(start).start
        if uid < count and (elapsed >= max_seconds or words >= max_words):
            close = _window_close(index, uid)
            windows.append((start, close))
            start, words, uid = close + 1, 0.0, close + 1
            continue
        uid += 1
    if start <= count:
        tail_seconds = index.unit(count).end - index.unit(start).start
        tail_words = sum(_unit_words(index, item) for item in range(start, count + 1))
        if windows and tail_seconds < WINDOW_MIN_TAIL_SHARE * max_seconds and tail_words < WINDOW_MIN_TAIL_SHARE * max_words:
            windows[-1] = (windows[-1][0], count)
        else:
            windows.append((start, count))
    return windows


def _window_close(index: UnitIndex, uid: int) -> int:
    horizon = index.unit(uid).end + WINDOW_CLOSE_LOOKAHEAD_SECONDS
    best, best_rank = uid, (-1.0, False)
    candidate = uid
    while candidate < len(index) and index.unit(candidate).end <= horizon:
        strength = index.boundary_strength(candidate)
        if strength >= STRONG_BOUNDARY:
            following = index.unit(candidate + 1)
            rank = (strength, following.speaker is not None and following.speaker != index.unit(candidate).speaker)
            if (rank[1], rank[0]) > (best_rank[1], best_rank[0]):
                best, best_rank = candidate, rank
        candidate += 1
    return best


def _window_listing_header(index: UnitIndex, first: int, last: int) -> str:
    start, end = index.unit(first).start, index.unit(last).end
    return f"{format_clock(start)}–{format_clock(end)}"


def _reduce_prompt(index: UnitIndex, windows: list[tuple[int, int]], digests: list[OutlineWindow]) -> tuple[
    str, list[OutlineChapter], list[tuple[int, int]], list[OutlinePoint], list[OutlineArc], list[OutlinePayoff]
]:
    chapters: list[OutlineChapter] = []
    spans: list[tuple[int, int]] = []
    points: list[OutlinePoint] = []
    arcs: list[OutlineArc] = []
    payoffs: list[OutlinePayoff] = []
    threads: list[str] = []
    for (low, high), digest in zip(windows, digests):
        first = low
        for chapter in digest.chapters:
            chapters.append(chapter)
            spans.append((first, chapter.end_unit))
            first = chapter.end_unit + 1
        points.extend(digest.main_points)
        arcs.extend(digest.story_arcs)
        payoffs.extend(digest.payoffs)
        threads.extend(f"({_window_listing_header(index, low, high)}) {thread}" for thread in digest.open_threads)

    lines = [f"The video has units #1–#{len(index)}, {format_clock(index.unit(len(index)).end)} long.", "", "Chapters:"]
    for number, (chapter, (first, last)) in enumerate(zip(chapters, spans), start=1):
        start, end = index.unit(first).start, index.unit(last).end
        title = f"{chapter.title}: " if chapter.title else ""
        lines.append(
            f"C{number} #{first}–#{last} ({format_clock(start)}–{format_clock(end)}, "
            f"{format_clock(end - start)}) {chapter.kind} ★{chapter.importance} — {title}{chapter.summary}"
        )
    lines += ["", "Candidate points:"]
    for number, point in enumerate(points, start=1):
        lines.append(
            f"P{number} #{point.first_unit}–#{point.last_unit} core #{point.core_first}–#{point.core_last} "
            f"★{point.importance} — {point.statement}"
        )
    lines += ["", "Candidate arcs:"]
    for number, arc in enumerate(arcs, start=1):
        lines.append(f"A{number} #{arc.first_unit}–#{arc.last_unit} ★{arc.strength} {arc.title} — {arc.shape}")
    lines += ["", "Candidate payoffs:"]
    for number, payoff in enumerate(payoffs, start=1):
        lines.append(f"Y{number} #{payoff.first_unit}–#{payoff.last_unit} — {payoff.what}")
    lines += ["", "Open threads:"] + [f"- {thread}" for thread in threads]
    return "\n".join(lines), chapters, spans, points, arcs, payoffs


def _nearest(value: int, allowed: Sequence[int]) -> int:
    return min(allowed, key=lambda candidate: (abs(candidate - value), candidate))


def assemble_reduce(
    reduce: OutlineReduce,
    index: UnitIndex,
    chapters: list[OutlineChapter],
    spans: list[tuple[int, int]],
    points: list[OutlinePoint],
    arcs: list[OutlineArc],
    payoffs: list[OutlinePayoff],
) -> SourceOutline:
    """The whole-video outline from the window digests and the reduce call."""
    count = len(index)
    rated = [
        chapter.model_copy(
            update={"importance": _clamp(reduce.chapter_importance[position], 1, 5)}
            if position < len(reduce.chapter_importance)
            else {}
        )
        for position, chapter in enumerate(chapters)
    ]

    ordered: list[OutlinePoint] = []
    for number in reduce.main_point_order:
        if isinstance(number, int) and 1 <= number <= len(points) and points[number - 1] not in ordered:
            ordered.append(points[number - 1])
    if not ordered:
        ordered = sorted(points, key=lambda point: -point.importance)

    starts = sorted({first for first, _ in spans} | {arc.first_unit for arc in arcs})
    ends = sorted({last for _, last in spans} | {arc.last_unit for arc in arcs})
    whole_arcs: list[OutlineArc] = []
    for arc in reduce.story_arcs:
        first, last = _span(arc.first_unit, arc.last_unit, 1, count)
        first, last = _nearest(first, starts), _nearest(last, ends)
        if first <= last:
            whole_arcs.append(arc.model_copy(update={"first_unit": first, "last_unit": last}))

    kept_payoffs: list[OutlinePayoff] = []
    for payoff in reduce.payoffs:
        first, last = _span(payoff.first_unit, payoff.last_unit, 1, count)
        match = next(
            (candidate for candidate in payoffs if candidate.first_unit <= last and first <= candidate.last_unit),
            None,
        )
        if match is not None and match not in kept_payoffs:
            kept_payoffs.append(match)

    outline = SourceOutline(
        analysis=reduce.analysis,
        format=reduce.format,
        language=reduce.language,
        one_line=reduce.one_line,
        central_question=reduce.central_question,
        throughline=reduce.throughline,
        key_topics=reduce.key_topics,
        chapters=rated,
        main_points=ordered,
        story_arcs=whole_arcs,
        payoffs=kept_payoffs,
    )
    return normalize_outline(outline, index)


# -- entry point --------------------------------------------------------------------


async def _load_cached(ctx: PlanContext, index: UnitIndex) -> Optional[OutlineResult]:
    try:
        raw = await ctx.plan_cache.load(ctx.outline_cache_key)
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Outline cache read failed (%s); reading the video again", type(error).__name__)
        return None
    if not raw:
        return None
    try:
        payload = json.loads(raw)
        if (
            payload.get("version") != OUTLINE_VERSION
            or payload.get("units_fingerprint") != index.fingerprint
            or payload.get("mode") not in ("full", "long")
        ):
            return None
        outline = SourceOutline.model_validate(payload["outline"])
    except (TypeError, ValueError, KeyError, AttributeError):
        return None
    return OutlineResult(outline=normalize_outline(outline, index), mode=payload["mode"], cached=True)


async def _store(ctx: PlanContext, index: UnitIndex, result: OutlineResult) -> None:
    payload = json.dumps(
        {
            "version": OUTLINE_VERSION,
            "units_fingerprint": index.fingerprint,
            "mode": result.mode,
            "outline": result.outline.model_dump(),
        }
    )
    try:
        await ctx.plan_cache.store(ctx.outline_cache_key, payload)
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Outline cache write failed (%s)", type(error).__name__)


async def get_outline(ctx: PlanContext, index: UnitIndex, language_name: str, stats: PlanStats) -> OutlineResult:
    """The cached outline for this source, or a fresh one (stored as soon as it is built)."""
    cached = await _load_cached(ctx, index)
    if cached is not None:
        logger.info("Reusing the cached source outline (%s mode)", cached.mode)
        return cached
    mode = outline_mode(index)
    try:
        if mode == "full":
            outline = await _full_outline(ctx, index, language_name, stats)
        else:
            outline = await _long_outline(ctx, index, language_name, stats)
    except WorkflowError:
        raise
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.error("Source outline failed (%s)", type(error).__name__)
        raise WorkflowError("analysis_incomplete", _INCOMPLETE) from error
    result = OutlineResult(outline=outline, mode=mode, cached=False)
    await _store(ctx, index, result)
    return result


async def _full_outline(
    ctx: PlanContext, index: UnitIndex, language_name: str, stats: PlanStats
) -> SourceOutline:
    agent = build_agent(SourceOutline, outline_system(language_name, len(index)), structural=True)
    last = index.unit(len(index))
    prompt = (
        f"The transcript has units #1–#{len(index)}, {format_clock(last.end)} long.\n\n"
        + transcript_block(index.listing())
    )
    result = await run_retrying(
        lambda: run(agent, prompt, timeout=OUTLINE_CALL_TIMEOUT_SECONDS, stats=stats, stage="outline"),
        label="outline",
    )
    return normalize_outline(result.output, index)


async def _long_outline(
    ctx: PlanContext, index: UnitIndex, language_name: str, stats: PlanStats
) -> SourceOutline:
    bulk_model = model_name(structural=False)
    max_words = OLLAMA_WINDOW_MAX_WORDS if _is_ollama(bulk_model) else WINDOW_MAX_WORDS
    windows = plan_windows(index, max_words=max_words)
    total = len(windows)
    semaphore = asyncio.Semaphore(WINDOW_CONCURRENCY)
    done = 0
    await ctx.progress(52, f"Mapping the video (0/{total})...")

    async def map_window(low: int, high: int) -> OutlineWindow:
        nonlocal done
        system = outline_window_system(
            language_name, low, high, len(index), _window_listing_header(index, low, high)
        )
        agent = build_agent(OutlineWindow, system, structural=False)
        prompt = transcript_block(index.listing(range(low, high + 1)))
        async with semaphore:
            result = await run_retrying(
                lambda: run(agent, prompt, timeout=OUTLINE_CALL_TIMEOUT_SECONDS, stats=stats, stage="outline_map"),
                label=f"outline window #{low}–#{high}",
            )
        done += 1
        # Planners report from 54 on, so the map stays below it (progress never goes back).
        await ctx.progress(52 + int(2 * done / total), f"Mapping the video ({done}/{total})...")
        return normalize_window(result.output, index, low, high)

    digests = await gather_all([map_window(low, high) for low, high in windows])
    prompt, chapters, spans, points, arcs, payoffs = _reduce_prompt(index, windows, digests)
    agent = build_agent(OutlineReduce, outline_reduce_system(language_name), structural=True)
    result = await run_retrying(
        lambda: run(agent, prompt, timeout=OUTLINE_CALL_TIMEOUT_SECONDS, stats=stats, stage="outline_reduce"),
        label="outline reduce",
    )
    return assemble_reduce(result.output, index, chapters, spans, points, arcs, payoffs)


def chapter_ranges(outline: SourceOutline, index: UnitIndex) -> list[tuple[int, int]]:
    """(first, last) unit of every outline chapter."""
    return chapter_spans(outline.chapters, len(index))
