"""Topic planner: every strong standalone moment about one typed subject.

Locate (what counts, keywords, chapter relevance) → lexical recall into
regions → extraction per window → deterministic gate, fit and dedupe → one
comparative rerank. Off-topic moments are never used to pad the result: no
qualifying moment means ``topic_not_found`` with suggestions, and a refund.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from .. import ai
from ..config import get_config
from .assembly import fit_span
from .boundaries import STRONG_BOUNDARY
from .durations import Budget, clip_budget
from .errors import WorkflowError
from .language import is_no_space, opens_with_back_reference
from .llm import build_agent, gather_all, is_cancellation, run, run_retrying
from .outline import chapter_ranges
from .planning import PlanInput, base_plan, planned_segment
from .prompts import (
    outline_block,
    topic_extract_system,
    topic_locate_system,
    topic_rerank_system,
    transcript_block,
    with_instructions,
)
from .schemas import TopicExtraction, TopicLocate, TopicMoment
from .titles import compose_title
from .units import UnitIndex, format_clock

logger = logging.getLogger(__name__)

HIT_CONTEXT_SECONDS = 90.0
CHAPTER_CONTEXT_SECONDS = 30.0
WINDOW_SECONDS = 12 * 60
WINDOW_OVERLAP_SECONDS = 60.0
EXTRACT_CONCURRENCY = 3
MIN_RELEVANCE = 70
MIN_STANDALONE = 50
# A moment that opens on "he"/"that's why" needs to stand alone clearly.
MIN_STANDALONE_BACK_REFERENCE = 65
DEDUPE_OVERLAP = 0.5
RERANK_MIN_CANDIDATES = 3
MIN_RANKED_SCORE = 45
RESERVE_CLIPS = 4
THIN_MAX_CLIPS = 2
MENTION_SPACING_SECONDS = 60.0

_PLAN_FAILED = "The AI editor couldn't finish this plan. Please try again."


@dataclass
class _Candidate:
    first: int
    last: int
    evidence: int
    relevance: int
    standalone: int
    hook_title: str
    hook_type: str
    hook: int
    engagement: int
    value: int
    shareability: int
    why: str

    @property
    def subtotal(self) -> int:
        return self.hook + self.engagement + self.value + self.shareability

    def scores(self) -> dict[str, int]:
        return {
            "total": self.subtotal,
            "hook": self.hook,
            "engagement": self.engagement,
            "value": self.value,
            "shareability": self.shareability,
        }


# -- recall -------------------------------------------------------------------------------


def fold(text: str) -> str:
    """Casefolded, NFKD, without combining marks: "Préços" and "precos" match."""
    decomposed = unicodedata.normalize("NFKD", (text or "").casefold())
    return "".join(char for char in decomposed if not unicodedata.combining(char))


def keyword_matchers(keywords: Sequence[str], no_space: bool) -> list[Any]:
    """Whole-word patterns for spaced languages; plain substrings without spaces."""
    matchers: list[Any] = []
    seen: set[str] = set()
    for keyword in keywords:
        folded = " ".join(fold(keyword).split())
        if not folded or folded in seen or (not no_space and len(folded) < 2):
            continue
        seen.add(folded)
        if no_space:
            matchers.append(folded.replace(" ", ""))
        else:
            body = r"\s+".join(re.escape(part) for part in folded.split())
            matchers.append(re.compile(rf"(?<!\w){body}(?!\w)"))
    return matchers


def hit_units(index: UnitIndex, matchers: Sequence[Any]) -> list[int]:
    hits = []
    for unit in index.units:
        text = fold(unit.text)
        compact = text.replace(" ", "")
        for matcher in matchers:
            if (matcher in compact) if isinstance(matcher, str) else matcher.search(text):
                hits.append(unit.id)
                break
    return hits


def _expand(index: UnitIndex, first: int, last: int, seconds: float) -> tuple[int, int]:
    low = index.unit_at(max(0.0, index.unit(first).start - seconds))
    high_time = index.unit(last).end + seconds
    high = last
    while high < len(index) and index.unit(high + 1).start <= high_time:
        high += 1
    return min(low, first), high


def recall_regions(
    index: UnitIndex, hits: Sequence[int], chapter_spans: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Keyword hits ±90 s and relevant chapters ±30 s, merged."""
    regions = [_expand(index, uid, uid, HIT_CONTEXT_SECONDS) for uid in hits]
    regions += [_expand(index, first, last, CHAPTER_CONTEXT_SECONDS) for first, last in chapter_spans]
    merged: list[tuple[int, int]] = []
    for first, last in sorted(regions):
        if merged and first <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], last))
        else:
            merged.append((first, last))
    return merged


def _seconds_of(index: UnitIndex, first: int, last: int) -> float:
    return index.unit(last).end - index.unit(first).start


def _split_region(index: UnitIndex, first: int, last: int) -> list[tuple[int, int]]:
    """A region longer than a window, in window-sized pieces cut at strong boundaries, 1 min overlap."""
    pieces: list[tuple[int, int]] = []
    start = first
    while start <= last:
        if _seconds_of(index, start, last) <= WINDOW_SECONDS:
            pieces.append((start, last))
            break
        end = start
        best_strong: Optional[int] = None
        while end < last and _seconds_of(index, start, end + 1) <= WINDOW_SECONDS:
            end += 1
            if index.boundary_strength(end) >= STRONG_BOUNDARY:
                best_strong = end
        end = best_strong if best_strong is not None and best_strong > start else end
        pieces.append((start, end))
        if end >= last:
            break
        overlap_from = index.unit(end).end - WINDOW_OVERLAP_SECONDS
        following = end + 1
        while following - 1 > start and index.unit(following - 1).start >= overlap_from:
            following -= 1
        start = max(following, start + 1)
    return pieces


def pack_windows(index: UnitIndex, regions: Sequence[tuple[int, int]]) -> list[list[int]]:
    """Unit ids per extraction window: whole regions packed up to 12 minutes each."""
    pieces = [piece for first, last in regions for piece in _split_region(index, first, last)]
    windows: list[list[tuple[int, int]]] = []
    used = 0.0
    for first, last in pieces:
        length = _seconds_of(index, first, last)
        if windows and used + length <= WINDOW_SECONDS:
            windows[-1].append((first, last))
            used += length
        else:
            windows.append([(first, last)])
            used = length
    return [sorted({uid for first, last in window for uid in range(first, last + 1)}) for window in windows]


# -- gate ----------------------------------------------------------------------------------


def _clamp(value: int, low: int, high: int) -> int:
    return min(max(int(value), low), high)


def _contiguous_run(window: set[int], first: int, last: int, anchor: int) -> tuple[int, int]:
    """``first..last`` cut back to the units shown around ``anchor`` (no hidden stretch inside)."""
    low = anchor
    while low - 1 >= first and low - 1 in window:
        low -= 1
    high = anchor
    while high + 1 <= last and high + 1 in window:
        high += 1
    return low, high


def gate_moment(
    index: UnitIndex,
    moment: TopicMoment,
    window_ids: Sequence[int],
    budget: Budget,
    language: Optional[str],
    cleanup: Optional[dict],
) -> Optional[_Candidate]:
    """A moment that passes the relevance and standalone bars and fits the clip length, else None."""
    low = max(1, min(window_ids) - 1)
    high = min(len(index), max(window_ids) + 1)
    first, last = sorted((_clamp(moment.first_unit, low, high), _clamp(moment.last_unit, low, high)))
    evidence = moment.evidence_unit
    if evidence not in window_ids or not first <= evidence <= last:
        return None  # the proof must be a line this window actually showed
    relevance = _clamp(moment.relevance, 0, 100)
    standalone = _clamp(moment.standalone, 0, 100)
    if relevance < MIN_RELEVANCE or standalone < MIN_STANDALONE:
        return None
    shown = set(window_ids) | {low, high}
    first, last = _contiguous_run(shown, first, last, evidence)
    fitted = fit_span(index, first, last, budget, anchor=evidence, cleanup=cleanup)
    if fitted is None:
        return None
    first, last = fitted
    if standalone < MIN_STANDALONE_BACK_REFERENCE and opens_with_back_reference(index.unit(first).text, language):
        return None
    return _Candidate(
        first=first,
        last=last,
        evidence=evidence,
        relevance=relevance,
        standalone=standalone,
        hook_title=" ".join(moment.hook_title.split()),
        hook_type=moment.hook_type,
        hook=_clamp(moment.hook_score, 0, 25),
        engagement=_clamp(moment.engagement_score, 0, 25),
        value=_clamp(moment.value_score, 0, 25),
        shareability=_clamp(moment.shareability_score, 0, 25),
        why=" ".join(moment.why.split()),
    )


def _overlap(a: _Candidate, b: _Candidate) -> float:
    shared = min(a.last, b.last) - max(a.first, b.first) + 1
    if shared <= 0:
        return 0.0
    return shared / min(a.last - a.first + 1, b.last - b.first + 1)


def dedupe(candidates: Sequence[_Candidate]) -> list[_Candidate]:
    """Best first by relevance then subscores; drop any that mostly repeats a kept one."""
    kept: list[_Candidate] = []
    for candidate in sorted(candidates, key=lambda item: (-item.relevance, -item.subtotal, item.first)):
        if all(_overlap(candidate, other) <= DEDUPE_OVERLAP for other in kept):
            kept.append(candidate)
    return kept


# -- model passes ----------------------------------------------------------------------------


async def _locate(pi: PlanInput, topic: str) -> TopicLocate:
    system = topic_locate_system(pi.language_name, topic)
    prompt = with_instructions(
        outline_block(pi.outline.outline, pi.index, include=("chapters", "points", "topics")),
        pi.instructions,
    )
    agent = build_agent(TopicLocate, system, structural=True)
    try:
        result = await run_retrying(
            lambda: run(agent, prompt, stats=pi.stats, stage="topic_locate"), label="topic locate"
        )
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.error("Topic locate failed (%s)", type(error).__name__)
        raise WorkflowError("plan_failed", _PLAN_FAILED) from error
    return result.output


async def _extract(
    pi: PlanInput, locate: TopicLocate, windows: list[list[int]], budget: Budget
) -> list[tuple[list[int], TopicExtraction]]:
    agent = build_agent(TopicExtraction, topic_extract_system(pi.language_name, locate, budget), structural=False)
    semaphore = asyncio.Semaphore(EXTRACT_CONCURRENCY)

    async def extract(window: list[int]) -> tuple[list[int], TopicExtraction]:
        prompt = with_instructions(transcript_block(pi.index.listing(window)), pi.instructions)
        async with semaphore:
            result = await run_retrying(
                lambda: run(agent, prompt, stats=pi.stats, stage="topic_extract"), label="topic window"
            )
        return window, result.output

    try:
        return await gather_all([extract(window) for window in windows])
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.error("Topic extraction failed (%s)", type(error).__name__)
        raise WorkflowError("analysis_incomplete", "We couldn't read the whole video. Please try again.") from error


def _as_transcript_segment(index: UnitIndex, candidate: _Candidate) -> ai.TranscriptSegment:
    start, end = index.range_seconds(candidate.first, candidate.last)
    return ai.TranscriptSegment(
        start_time=ai._format_transcript_timestamp(int(start)),
        end_time=ai._format_transcript_timestamp(int(round(end))),
        text=index.text(candidate.first, candidate.last),
        relevance_score=candidate.relevance / 100,
        reasoning=candidate.why or "On topic.",
        virality=ai.ViralityAnalysis(
            hook_score=candidate.hook,
            engagement_score=candidate.engagement,
            value_score=candidate.value,
            shareability_score=candidate.shareability,
            total_score=candidate.subtotal,
            hook_type=candidate.hook_type,
            virality_reasoning=candidate.why or "On topic.",
        ),
        hook_title=candidate.hook_title or None,
    )


async def _rerank(pi: PlanInput, canonical: str, candidates: list[_Candidate]) -> Optional[list[_Candidate]]:
    """Comparative order with calibrated scores (written onto the candidates), or None on failure."""
    segments = [_as_transcript_segment(pi.index, candidate) for candidate in candidates]
    owners = {id(segment): candidate for segment, candidate in zip(segments, candidates)}
    prompt = ai.build_candidate_rerank_prompt(
        segments, pi.ctx.performance_context, instructions=pi.instructions or None
    )
    try:
        agent = build_agent(ai.CandidateRanking, topic_rerank_system(pi.language_name, canonical), structural=True)
        result = await run(agent, prompt, timeout=ai.RERANK_TIMEOUT_SECONDS, stats=pi.stats, stage="topic_rerank")
        ranked = ai.apply_candidate_ranking(segments, result.output)
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Topic rerank failed (%s); keeping first-pass order", type(error).__name__)
        return None
    ordered = []
    for segment in ranked:
        candidate = owners[id(segment)]
        virality = segment.virality
        candidate.hook = virality.hook_score
        candidate.engagement = virality.engagement_score
        candidate.value = virality.value_score
        candidate.shareability = virality.shareability_score
        ordered.append(candidate)
    return ordered


# -- not found ---------------------------------------------------------------------------------


def _suggestions(topic: str, locate: Optional[TopicLocate], key_topics: Sequence[str]) -> list[str]:
    seen = {fold(topic).strip()}
    suggested: list[str] = []
    for item in list(locate.suggested_topics if locate else []) + list(key_topics):
        clean = " ".join(str(item).split()).strip(" .,;:")
        key = fold(clean)
        if clean and key not in seen:
            seen.add(key)
            suggested.append(clean)
    return suggested


def _mentions(index: UnitIndex, hits: Sequence[int]) -> list[str]:
    mentions: list[str] = []
    last_time: Optional[float] = None
    for uid in hits:
        start = index.unit(uid).start
        if last_time is None or start - last_time >= MENTION_SPACING_SECONDS:
            mentions.append(format_clock(start))
            last_time = start
        if len(mentions) == 3:
            break
    return mentions


def topic_not_found(topic: str, mentions: list[str], suggested: list[str]) -> WorkflowError:
    if mentions:
        message = f'We found only passing mentions of "{topic}" (at {", ".join(mentions)}), not full moments.'
    else:
        message = f'This video doesn\'t really talk about "{topic}".'
    if suggested:
        message += f' It\'s mostly about {", ".join(suggested[:3])}.'
    return WorkflowError(
        "topic_not_found",
        message,
        details={"topic": topic, "mentions": mentions, "suggested_topics": suggested[:6]},
    )


# -- entry point ---------------------------------------------------------------------------------


async def plan_topic(pi: PlanInput) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index, spec, outline = pi.index, pi.ctx.spec, pi.outline.outline
    topic = spec.options.topic
    budget = clip_budget(spec.clip_length)
    await pi.ctx.progress(54, f"Finding where '{topic}' comes up...")
    locate = await _locate(pi, topic)
    canonical = " ".join(locate.canonical.split()) or topic

    no_space = index.no_space or is_no_space(pi.language, index.unit(1).text)
    matchers = keyword_matchers([topic, canonical, *locate.keywords], no_space)
    hits = hit_units(index, matchers)
    spans = chapter_ranges(outline, index)
    relevant_chapters = [
        spans[entry.chapter - 1]
        for entry in locate.chapters
        if 1 <= entry.chapter <= len(spans) and entry.relevance >= 2
    ]
    suggested = _suggestions(topic, locate, outline.key_topics)
    regions = recall_regions(index, hits, relevant_chapters)
    if not regions:
        raise topic_not_found(topic, _mentions(index, hits), suggested)

    windows = pack_windows(index, regions)
    await pi.ctx.progress(58, "Picking the strongest moments...")
    extractions = await _extract(pi, locate, windows, budget)
    candidates = [
        candidate
        for window, extraction in extractions
        for moment in extraction.moments
        if (candidate := gate_moment(index, moment, window, budget, pi.language, pi.cleanup)) is not None
    ]
    candidates = dedupe(candidates)
    if not candidates:
        raise topic_not_found(topic, _mentions(index, hits), suggested)

    ranked: Optional[list[_Candidate]] = None
    if len(candidates) >= RERANK_MIN_CANDIDATES:
        ranked = await _rerank(pi, canonical, candidates)
    if ranked is not None:
        eligible = [candidate for candidate in ranked if candidate.subtotal >= MIN_RANKED_SCORE]
    else:
        eligible = candidates  # already relevance-first; each passed the bars on its own
    if not eligible:
        raise topic_not_found(topic, _mentions(index, hits), suggested)

    limit = max(1, int(get_config().workflow_topic_max_clips))
    selected, reserve = eligible[:limit], eligible[limit : limit + RESERVE_CLIPS]
    coverage = "thin" if len(selected) <= THIN_MAX_CLIPS else "strong"
    if coverage == "thin":
        pi.note("TOPIC_THIN", {"count": len(selected), "found": len(selected)})
    if index.coarse:
        pi.note("COARSE_TRANSCRIPT")

    segments = [
        _segment(pi, candidate, canonical, budget, reserve=False) for candidate in selected
    ] + [_segment(pi, candidate, canonical, budget, reserve=True) for candidate in reserve]
    plan = base_plan(pi, title=canonical)
    plan["topic"] = {
        "query": topic,
        "canonical": canonical,
        "coverage": coverage,
        "found": len(selected),
        "suggested_topics": suggested[:6],
    }
    return segments, plan


def _segment(pi: PlanInput, candidate: _Candidate, canonical: str, budget: Budget, *, reserve: bool) -> dict[str, Any]:
    index = pi.index
    meta: dict[str, Any] = {"topic": canonical, "topic_relevance": candidate.relevance, "why": candidate.why}
    if reserve:
        meta["reserve"] = True
    return planned_segment(
        ranges=[index.range_seconds(candidate.first, candidate.last)],
        junction_fades=[],
        max_seconds=budget.render_cap,
        required=False,
        text=index.text(candidate.first, candidate.last),
        hook_title=compose_title(candidate.hook_title),
        reasoning=candidate.why,
        workflow_role="topic",
        meta=meta,
        relevance_score=candidate.relevance / 100,
        scores=candidate.scores(),
        hook_type=candidate.hook_type,
    )
