"""Topic planner: every strong standalone moment about one typed subject.

Locate (what counts, keywords, chapter relevance) → lexical recall into
regions → extraction per window → deterministic gate, fit, dedupe and merge →
a last editor who can move a clip's edges → one comparative rerank → no source
line plays twice. Off-topic moments are never used to pad the result: no
qualifying moment means ``topic_not_found`` with suggestions, and a refund.
"""

from __future__ import annotations

import asyncio
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .. import ai
from ..config import get_config
from .assembly import fit_span
from .boundaries import STRONG_BOUNDARY
from .durations import Budget, topic_budget
from .errors import WorkflowError
from .language import (
    inflects_by_suffix,
    is_acknowledgement,
    is_no_space,
    leans_on_a_list_or_reply,
    opens_with_back_reference,
    unresolved_reference,
)
from .series import PART_JUNCTION_FADE, _is_backchannel, clean_skip, trim_backchannel_tail
from .llm import build_agent, gather_all, is_cancellation, run, run_retrying
from .outline import chapter_ranges
from .planning import PlanInput, base_plan, planned_segment
from .prompts import (
    outline_block,
    topic_extract_system,
    topic_finish_system,
    topic_block,
    topic_locate_system,
    topic_rerank_system,
    transcript_block,
    with_instructions,
)
from .schemas import TopicExtraction, TopicFinish, TopicLocate, TopicMoment, TopicRanking
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
DEDUPE_OVERLAP = 0.5
# A clip that still leans on something unheard after every repair goes under this score.
LEANING_MIN_STANDALONE = 65
# Tightening skips inside a moment: whole sentences, at least 2 s (1 s for a
# thank-you), at most 3 and 35 %. Used only when the moment runs past its ideal length.
TOPIC_SKIP_MIN_SECONDS = 2.0
TOPIC_ACK_SKIP_MIN_SECONDS = 1.0
TOPIC_MAX_SKIPS = 3
TOPIC_SKIP_MAX_SHARE = 0.35
# An opening that points back takes in at most this much before it.
PREPEND_MAX_UNITS = 2
PREPEND_MAX_SECONDS = 15.0
# The topic shows up within this share of the clip's seconds, or the clip starts later.
EARLY_TOPIC_SHARE = 0.4
# A leading one-word unit this short ("Catharsis.", "Yeah.") never opens a clip.
LONE_WORD_MAX_SECONDS = 1.0
# An announced list or a setup line is followed this far to reach its payoff.
PAYOFF_SEARCH_UNITS = 6
# A discourse opener may look this many sentences past the evidence line for a clean start.
LATER_START_EXTRA_UNITS = 4
# Clips this close that lean on each other are one clip.
MERGE_MAX_GAP_SECONDS = 2.0
# "thin" when the distinct material found is under this.
THIN_MATERIAL_SECONDS = 45.0
# The last editor sees this many units around each clip.
FINISH_CONTEXT_UNITS = 6
# The comparative judge's standalone bar: under it a clip waits in the reserve.
MIN_RANKED_STANDALONE = 55
# Every surviving candidate gets the comparative pass: besides ranking, it is
# a second, whole-picture check that each clip is really about the topic.
RERANK_MIN_CANDIDATES = 1
# The comparative judge's on-topic veto: below this the topic is a side note
# (the same bar as the extractor's relevance and the rubric's "substantially").
MIN_RANKED_ON_TOPIC = 70
MIN_RANKED_SCORE = 45
RESERVE_CLIPS = 4
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
    skips: list[tuple[int, int]] = field(default_factory=list)
    retitle: bool = False  # the fitted span differs from the model's: its title may promise what was cut
    duplicate: bool = False  # the rerank says a better clip makes the same point
    weak: bool = False  # the rerank says it needs another clip to make sense
    moment: int = -1  # which extracted moment this came from, so halves of one story can rejoin

    @property
    def reserve_only(self) -> bool:
        return self.duplicate or self.weak

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


def keyword_matchers(keywords: Sequence[str], no_space: bool, word_prefix: bool = False) -> list[Any]:
    """Whole-word patterns for spaced languages; plain substrings without spaces.

    ``word_prefix`` (languages that add suffixes and particles, see
    ``inflects_by_suffix``) matches a keyword at the start of a word, so
    "투자" finds "투자를" and "yatırım" finds "yatırımlar".
    """
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
            matchers.append(re.compile(rf"(?<!\w){body}" + ("" if word_prefix else r"(?!\w)")))
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


_PROMISE_RE = {
    "en": re.compile(
        r"\b(there are|there's|i have|we have)\s+(two|2|three|3|four|4)\s+(things|reasons|consequences|ways|mistakes|rules|steps|tips)\b",
        re.IGNORECASE,
    ),
    "de": re.compile(r"\b(zwei|drei|vier)\s+(gründe|dinge|folgen|punkte|fehler|regeln|schritte)\b", re.IGNORECASE),
}
_NEXT_ITEM_RE = {
    "en": re.compile(r"\b(the second|second(ly)?|the next|the other|the third|finally|another)\b", re.IGNORECASE),
    "de": re.compile(r"\b(zweitens|der zweite|die zweite|das zweite|außerdem|drittens)\b", re.IGNORECASE),
}
# Only when the setup is the very end of the line ("It wakes you up because it blocks..." has its answer).
_SETUP_END_RE = re.compile(
    r"\b(the reason is|here's why|der grund ist)\s*[:,.]?$|\b(because|weil)\s*[.,]?$|[:—–-]\s*$|\bthe reason is because\b",
    re.IGNORECASE,
)
# A line that turns on what came before it ("But...", "Then..."): never right after a skip.
_PIVOT_OPENERS = {"en": ("but ", "however", "then ", "so "), "de": ("aber ", "doch ", "dann ")}


def _seconds(index: UnitIndex, first: int, last: int, skips: Sequence[tuple[int, int]], cleanup: Optional[dict]) -> float:
    return index.speech_seconds(first, last, [skip for skip in skips if first < skip[0] and skip[1] < last], cleanup)


def _inside(skips: Sequence[tuple[int, int]], first: int, last: int) -> list[tuple[int, int]]:
    return [skip for skip in skips if first < skip[0] and skip[1] < last]


def leans(text: str, language: Optional[str]) -> bool:
    """A first sentence that needs something the viewer hasn't heard."""
    return opens_with_back_reference(text, language, strict=True) or unresolved_reference(text, language)


def _clean_start(index: UnitIndex, uid: int, language: Optional[str]) -> bool:
    unit = index.unit(uid)
    return unit.starts_sentence and not leans(unit.text, language)


def _pivots(text: str, language: Optional[str]) -> bool:
    opening = " ".join((text or "").casefold().replace(",", " ").split()) + " "
    return opening.startswith(_PIVOT_OPENERS.get((language or "")[:2], ()))


def topic_skips(
    index: UnitIndex,
    spans: Sequence[Any],
    first: int,
    last: int,
    evidence: int,
    cleanup: Optional[dict],
    language: Optional[str] = None,
) -> list[tuple[int, int]]:
    """The model's tightening skips that are safe: whole sentences inside the moment, at most 3 and 35 %.

    The sentence after a skip has to work without it: it can't lean on what
    was removed or pivot on it ("But...", "Then...").
    """
    language = language or index.language
    skips: list[tuple[int, int]] = []
    for span in spans:
        low, high = sorted((int(span.first_unit), int(span.last_unit)))
        if not first < low <= high < last or low <= evidence <= high:
            continue
        if any(low <= other[1] + 1 and other[0] <= high + 1 for other in skips):
            continue
        following = index.unit(high + 1).text
        if leans(following, language) or _pivots(following, language):
            continue
        thanks = low == high and is_acknowledgement(index.unit(low).text, language)
        minimum = TOPIC_ACK_SKIP_MIN_SECONDS if thanks else TOPIC_SKIP_MIN_SECONDS
        if not clean_skip(index, low, high) or index.speech_seconds(low, high, (), cleanup) < minimum:
            continue
        skips.append((low, high))
    skips = sorted(skips)[:TOPIC_MAX_SKIPS]
    whole = index.speech_seconds(first, last, (), cleanup)
    if whole <= 0 or (whole - index.speech_seconds(first, last, skips, cleanup)) / whole > TOPIC_SKIP_MAX_SHARE:
        return []
    return skips


def split_span(
    index: UnitIndex, first: int, last: int, budget: Budget, language: Optional[str], cleanup: Optional[dict]
) -> Optional[tuple[tuple[int, int], tuple[int, int]]]:
    """Two clips from one over-long moment, cut at its clearest beat, each able to stand alone.

    The strongest boundary wins, not the most balanced one. The left half never
    ends on a question or a setup, and the right half opens cleanly. None when
    no cut does that: a half that needs the other half is worse than no clip.
    """
    floor = max(budget.min, budget.ideal_min)
    best: Optional[tuple[tuple[float, float], tuple[int, int], tuple[int, int]]] = None
    for k in range(first, last):
        strength = index.boundary_strength(k)
        if strength < STRONG_BOUNDARY or not _clean_start(index, k + 1, language):
            continue
        unit = index.unit(k)
        if unit.ends_question or _SETUP_END_RE.search(unit.text.strip()):
            continue
        left, right = index.speech_seconds(first, k, (), cleanup), index.speech_seconds(k + 1, last, (), cleanup)
        if floor <= left <= budget.max and floor <= right <= budget.max:
            rank = (-strength, abs(left - right))
            if best is None or rank < best[0]:
                best = (rank, (first, k), (k + 1, last))
    return (best[1], best[2]) if best else None


def trim_front(
    index: UnitIndex, first: int, last: int, anchor: int, budget: Budget, language: Optional[str], cleanup: Optional[dict]
) -> Optional[tuple[int, int]]:
    """``first..last`` started later, at a clean sentence start no later than ``anchor``, to fit the maximum."""
    for start in range(first + 1, anchor + 1):
        if not _clean_start(index, start, language):
            continue
        length = index.speech_seconds(start, last, (), cleanup)
        if length <= budget.max:
            return (start, last) if length >= budget.min else None
    return None


def _trim_head(index: UnitIndex, first: int, last: int, evidence: int) -> int:
    """Past leading listener replies and lone one-word lines ("Yeah.", "Catharsis.")."""
    while first < last and first != evidence:
        unit = index.unit(first)
        lone = not index.coarse and unit.w0 >= 0 and unit.w0 == unit.w1 and unit.end - unit.start <= LONE_WORD_MAX_SECONDS
        if not (lone or _is_backchannel(index, first)):
            break
        first += 1
    return first


def repair_opening(
    index: UnitIndex,
    first: int,
    last: int,
    evidence: int,
    shown: set[int],
    skips: Sequence[tuple[int, int]],
    budget: Budget,
    language: Optional[str],
    cleanup: Optional[dict],
) -> Optional[int]:
    """A start that doesn't lean on anything unheard.

    A pronoun or "this" may have its referent in the sentence before, so that
    comes first. A list position or a bare reply doesn't: the line before it is
    the previous thought, so only a later clean start fixes it.
    """
    opening = index.unit(first).text
    if not leans(opening, language):
        return first
    position = leans_on_a_list_or_reply(opening, language)
    if not position:
        for back in range(1, PREPEND_MAX_UNITS + 1):
            start = first - back
            if start < 1 or start not in shown:
                break
            if index.speech_seconds(start, first - 1, (), cleanup) > PREPEND_MAX_SECONDS:
                break
            if _clean_start(index, start, language) and _seconds(index, start, last, skips, cleanup) <= budget.max:
                return start
    limit = max(evidence, first + LATER_START_EXTRA_UNITS) if position else evidence
    for start in range(first + 1, min(limit, last) + 1):
        if _clean_start(index, start, language) and _seconds(index, start, last, skips, cleanup) >= budget.min:
            return start
    return None


def _next_strong_end(index: UnitIndex, uid: int, limit: int) -> Optional[int]:
    return next((u for u in range(uid, min(len(index), limit) + 1) if index.boundary_strength(u) >= STRONG_BOUNDARY), None)


def _capped(limit: int, ceiling: Optional[int]) -> int:
    return limit if ceiling is None else min(limit, ceiling)


def repair_ending(
    index: UnitIndex,
    first: int,
    last: int,
    evidence: int,
    skips: Sequence[tuple[int, int]],
    budget: Budget,
    language: Optional[str],
    cleanup: Optional[dict],
    ceiling: Optional[int] = None,
) -> Optional[tuple[int, int]]:
    """An end on the payoff: every announced item, no setup without its answer, no trailing reply.

    An announced list ("there are 2 consequences") without its next item
    extends to the sentence holding it, starting later if needed to stay
    within the maximum; a last line that only sets up ("the reason is...")
    extends to the next strong cut. ``ceiling``: the last unit it may reach
    (the left half of a split never grows into the right half). None when
    neither fits.
    """
    code = (language or "")[:2]
    promise, next_item = _PROMISE_RE.get(code), _NEXT_ITEM_RE.get(code)
    if promise is not None and next_item is not None:
        for uid in range(first, last + 1):
            if not promise.search(index.unit(uid).text):
                continue
            if any(next_item.search(index.unit(later).text) for later in range(uid + 1, last + 1)):
                break
            item = next(
                (
                    u
                    for u in range(last + 1, min(len(index), _capped(last + PAYOFF_SEARCH_UNITS, ceiling)) + 1)
                    if next_item.search(index.unit(u).text)
                ),
                None,
            )
            end = _next_strong_end(index, item, _capped(item + PAYOFF_SEARCH_UNITS, ceiling)) if item is not None else None
            if end is None:
                return None
            last = end
            while _seconds(index, first, last, skips, cleanup) > budget.max:
                later = next((u for u in range(first + 1, min(uid, evidence) + 1) if _clean_start(index, u, language)), None)
                if later is None:
                    return None
                first = later
            break
    if _SETUP_END_RE.search(index.unit(last).text.strip()):
        end = _next_strong_end(index, last + 1, _capped(last + PAYOFF_SEARCH_UNITS, ceiling))
        if end is None or _seconds(index, first, end, skips, cleanup) > budget.max:
            return None
        last = end
    last = trim_backchannel_tail(index, first, last)
    unit = index.unit(last)
    if unit.ends_question and unit.speaker is not None:
        speech: dict[Optional[str], float] = {}
        for uid in range(first, last + 1):
            speech[index.unit(uid).speaker] = speech.get(index.unit(uid).speaker, 0.0) + index.unit(uid).end - index.unit(uid).start
        if max(speech, key=speech.get) != unit.speaker:
            # Someone else's question opens the next topic: end before it.
            last = next((u for u in range(last - 1, first - 1, -1) if index.boundary_strength(u) >= STRONG_BOUNDARY), first)
    return first, last


def early_on_topic(
    index: UnitIndex,
    first: int,
    last: int,
    evidence: int,
    hits: set[int],
    skips: Sequence[tuple[int, int]],
    budget: Budget,
    language: Optional[str],
    cleanup: Optional[dict],
) -> Optional[int]:
    """A start from which the topic shows up early (evidence or a keyword in the first 40 %), else None."""
    total = _seconds(index, first, last, skips, cleanup)
    marks = sorted({evidence} | {uid for uid in hits if first <= uid <= last})
    if total <= 0 or not marks:
        return first
    if index.speech_seconds(first, marks[0], (), cleanup) - index.speech_seconds(marks[0], marks[0], (), cleanup) <= EARLY_TOPIC_SHARE * total:
        return first
    start = next(
        (u for u in range(marks[0], first - 1, -1) if _clean_start(index, u, language)), None
    )
    if start is None or start <= first or _seconds(index, start, last, skips, cleanup) < budget.min:
        return None
    return start


def gate_candidates(
    index: UnitIndex,
    moment: TopicMoment,
    window_ids: Sequence[int],
    budget: Budget,
    language: Optional[str],
    cleanup: Optional[dict],
    hits: Sequence[int] = (),
) -> list[_Candidate]:
    """The clips one extracted moment yields after the bars, fitting, and opening and ending repairs.

    An over-long moment splits into two clips at a strong cut, else starts
    later (keeping the model's end, where the payoff is), else goes; it is
    never cut back to its setup.
    """
    low = max(1, min(window_ids) - 1)
    high = min(len(index), max(window_ids) + 1)
    first, last = sorted((_clamp(moment.first_unit, low, high), _clamp(moment.last_unit, low, high)))
    evidence = moment.evidence_unit
    if evidence not in window_ids or not first <= evidence <= last:
        return []  # the proof must be a line this window actually showed
    relevance = _clamp(moment.relevance, 0, 100)
    standalone = _clamp(moment.standalone, 0, 100)
    if relevance < MIN_RELEVANCE or standalone < MIN_STANDALONE:
        return []
    shown = set(window_ids) | {low, high}
    first, last = _contiguous_run(shown, first, last, evidence)
    model_span = (first, last)
    raw = index.speech_seconds(first, last, (), cleanup)
    # A moment that already fits plays whole: skips exist to make room, and tend to cut the setup.
    skips = (
        topic_skips(index, getattr(moment, "skips", []) or [], first, last, evidence, cleanup, language)
        if raw > budget.ideal_max
        else []
    )
    spans: list[tuple[int, int, list[tuple[int, int]], bool]] = []  # (first, last, skips, keeps the evidence)
    length = index.speech_seconds(first, last, skips, cleanup)
    split = False
    if skips and budget.min <= length <= budget.max:
        spans.append((first, last, skips, True))
    elif raw > budget.max:
        halves = split_span(index, first, last, budget, language, cleanup)
        if halves is not None:
            split = True
            spans += [(a, b, [], a <= evidence <= b) for a, b in halves]
        else:
            trimmed = trim_front(index, first, last, evidence, budget, language, cleanup)
            if trimmed is not None:
                spans.append((trimmed[0], trimmed[1], [], True))
    else:
        fitted = fit_span(index, first, last, budget, anchor=evidence, cleanup=cleanup)
        if fitted is not None:
            spans.append((fitted[0], fitted[1], [], True))

    candidates: list[_Candidate] = []
    hit_set = set(hits)
    for span_first, span_last, span_skips, has_evidence in spans:
        anchor = evidence if has_evidence else span_first
        span_first = _trim_head(index, span_first, span_last, anchor)
        repaired = repair_opening(index, span_first, span_last, anchor, shown, span_skips, budget, language, cleanup)
        if repaired is None:
            # Nothing near fixes it. A strong score goes on to the last editor; a weak one stops here.
            if standalone < LEANING_MIN_STANDALONE:
                continue
            repaired = span_first
        span_first = repaired
        ended = repair_ending(
            index, span_first, span_last, anchor, span_skips, budget, language, cleanup, ceiling=span_last if split else None
        )
        if ended is None:
            continue
        span_first, span_last = ended
        start = early_on_topic(index, span_first, span_last, anchor, hit_set, span_skips, budget, language, cleanup)
        if start is None:
            continue
        span_first = start
        span_skips = _inside(span_skips, span_first, span_last)
        if not budget.min <= _seconds(index, span_first, span_last, span_skips, cleanup) <= budget.max:
            continue
        if not has_evidence and not any(span_first <= uid <= span_last for uid in hit_set):
            continue  # the other half of a split must still name the topic
        moved = abs(span_first - model_span[0]) > 1 or abs(span_last - model_span[1]) > 1
        candidates.append(
            _Candidate(
                first=span_first,
                last=span_last,
                evidence=anchor,
                relevance=relevance,
                standalone=standalone,
                hook_title=" ".join(moment.hook_title.split()) if has_evidence else "",
                hook_type=moment.hook_type,
                hook=_clamp(moment.hook_score, 0, 25),
                engagement=_clamp(moment.engagement_score, 0, 25),
                value=_clamp(moment.value_score, 0, 25),
                shareability=_clamp(moment.shareability_score, 0, 25),
                why=" ".join(moment.why.split()),
                skips=span_skips,
                retitle=moved or not has_evidence,
                moment=id(moment),
            )
        )
    return candidates


def gate_moment(
    index: UnitIndex,
    moment: TopicMoment,
    window_ids: Sequence[int],
    budget: Budget,
    language: Optional[str],
    cleanup: Optional[dict],
    hits: Sequence[int] = (),
) -> Optional[_Candidate]:
    """The first clip ``gate_candidates`` yields, else None."""
    candidates = gate_candidates(index, moment, window_ids, budget, language, cleanup, hits)
    return candidates[0] if candidates else None


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


def _gap_seconds(index: UnitIndex, left: _Candidate, right: _Candidate) -> float:
    return index.unit(right.first).start - index.unit(left.last).end


def merge_adjacent(
    index: UnitIndex, candidates: Sequence[_Candidate], budget: Budget, language: Optional[str], cleanup: Optional[dict]
) -> list[_Candidate]:
    """Neighbours that are really one story become one clip.

    Within 2 s of each other and inside the budget together, when they came
    from one moment or the right one opens leaning on the left. The merged clip
    gets a new title: neither half's promise covers it.
    """
    merged: list[_Candidate] = []
    for candidate in sorted(candidates, key=lambda item: item.first):
        previous = merged[-1] if merged else None
        if (
            previous is not None
            and candidate.first > previous.last
            and _gap_seconds(index, previous, candidate) <= MERGE_MAX_GAP_SECONDS
            and (
                candidate.moment == previous.moment
                or leans(index.unit(candidate.first).text, language)
            )
        ):
            skips = previous.skips + candidate.skips
            if _seconds(index, previous.first, candidate.last, skips, cleanup) <= budget.max:
                merged[-1] = _Candidate(
                    first=previous.first,
                    last=candidate.last,
                    evidence=previous.evidence,
                    relevance=max(previous.relevance, candidate.relevance),
                    standalone=max(previous.standalone, candidate.standalone),
                    hook_title=previous.hook_title or candidate.hook_title,
                    hook_type=previous.hook_type,
                    hook=max(previous.hook, candidate.hook),
                    engagement=max(previous.engagement, candidate.engagement),
                    value=max(previous.value, candidate.value),
                    shareability=max(previous.shareability, candidate.shareability),
                    why=previous.why,
                    skips=skips,
                    retitle=True,
                    moment=previous.moment,
                )
                continue
        merged.append(candidate)
    return merged


def _played_units(candidate: _Candidate) -> set[int]:
    skipped = {uid for low, high in candidate.skips for uid in range(low, high + 1)}
    return {uid for uid in range(candidate.first, candidate.last + 1) if uid not in skipped}


def _trim_outside(
    index: UnitIndex,
    candidate: _Candidate,
    other: _Candidate,
    budget: Budget,
    language: Optional[str],
    cleanup: Optional[dict],
    hits: set[int],
) -> Optional[_Candidate]:
    """``candidate`` cut so it no longer plays what ``other`` plays, still in budget and on topic; else None."""
    first, last = candidate.first, candidate.last
    if first < other.first and last <= other.last:
        last = next(
            (uid for uid in range(other.first - 1, first - 1, -1) if index.boundary_strength(uid) >= STRONG_BOUNDARY),
            None,
        )
        if last is None:
            return None
    elif other.first <= first and other.last < last:
        first = next((uid for uid in range(other.last + 1, last + 1) if _clean_start(index, uid, language)), None)
        if first is None:
            return None
    else:
        return None  # it surrounds the other clip, or sits inside it
    skips = _inside(candidate.skips, first, last)
    if not budget.min <= _seconds(index, first, last, skips, cleanup) <= budget.max:
        return None
    if not (first <= candidate.evidence <= last) and not any(first <= uid <= last for uid in hits):
        return None
    return _with_span(candidate, first, last, skips)


def _with_span(candidate: _Candidate, first: int, last: int, skips: list[tuple[int, int]]) -> _Candidate:
    fields = {**candidate.__dict__, "first": first, "last": last, "skips": skips, "retitle": True}
    return _Candidate(**fields)


def resolve_overlaps(
    index: UnitIndex,
    ordered: Sequence[_Candidate],
    budget: Budget,
    language: Optional[str],
    cleanup: Optional[dict],
    hits: Sequence[int] = (),
) -> list[_Candidate]:
    """No source line plays twice: in rank order, a later clip loses what an earlier one already plays.

    It is trimmed to the nearest strong end or clean start outside the shared
    lines; if that breaks the budget or loses the topic, it is left out.
    """
    hit_set = set(hits)
    kept: list[_Candidate] = []
    for candidate in ordered:
        current: Optional[_Candidate] = candidate
        for other in kept:
            if current is None or not (_played_units(current) & _played_units(other)):
                continue
            current = _trim_outside(index, current, other, budget, language, cleanup, hit_set)
        if current is not None:
            kept.append(current)
    return kept


# -- model passes ----------------------------------------------------------------------------


async def _locate(pi: PlanInput, topic: str) -> TopicLocate:
    system = topic_locate_system(pi.language_name)
    prompt = with_instructions(
        topic_block(topic)
        + "\n\n"
        + outline_block(pi.outline.outline, pi.index, include=("chapters", "points", "topics")),
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


def _finish_prompt(pi: PlanInput, candidates: Sequence[_Candidate]) -> str:
    index = pi.index
    blocks: list[str] = []
    for clip_id, candidate in enumerate(candidates):
        seconds = _seconds(index, candidate.first, candidate.last, candidate.skips, pi.cleanup)
        played = sorted(_played_units(candidate))
        before = range(max(1, candidate.first - FINISH_CONTEXT_UNITS), candidate.first)
        after = range(candidate.last + 1, min(len(index), candidate.last + FINISH_CONTEXT_UNITS) + 1)
        lines = [f'=== CLIP {clip_id} ({seconds:.0f}s) ===']
        if len(before):
            lines += ["before (does not play):", index.listing(before)]
        lines += ["clip:", index.listing(played)]
        if len(after):
            lines += ["after (does not play):", index.listing(after)]
        blocks.append("\n".join(lines))
    return with_instructions(transcript_block("\n\n".join(blocks)), pi.instructions)


def apply_finish(
    pi: PlanInput,
    candidates: list[_Candidate],
    finish: TopicFinish,
    budget: Budget,
    hits: Sequence[int],
) -> list[_Candidate]:
    """The last editor's edits, each checked; an invalid one keeps the clip as it was.

    Edges stay among the units shown, on whole sentences, in budget, on topic
    (the evidence or a keyword stays inside) and clear of every other clip. A
    dropped clip goes, unless that would leave nothing at all.
    """
    index, cleanup = pi.index, pi.cleanup
    hit_set = set(hits)
    updated = list(candidates)
    dropped: set[int] = set()
    for entry in finish.clips:
        if not 0 <= entry.clip_id < len(candidates):
            continue
        candidate = candidates[entry.clip_id]
        if entry.drop:
            dropped.add(entry.clip_id)
            continue
        first, last = entry.first_unit, entry.last_unit
        if (first, last) == (candidate.first, candidate.last):
            continue
        low = max(1, candidate.first - FINISH_CONTEXT_UNITS)
        high = min(len(index), candidate.last + FINISH_CONTEXT_UNITS)
        if not (low <= first <= last <= high):
            continue
        if not index.unit(first).starts_sentence or index.boundary_strength(last) < STRONG_BOUNDARY:
            continue
        skips = _inside(candidate.skips, first, last)
        if not budget.min <= _seconds(index, first, last, skips, cleanup) <= budget.max:
            continue
        if not (first <= candidate.evidence <= last) and not any(first <= uid <= last for uid in hit_set):
            continue
        if any(
            position != entry.clip_id and first <= other.last and other.first <= last
            for position, other in enumerate(updated)
        ):
            continue
        updated[entry.clip_id] = _with_span(candidate, first, last, skips)
        pi.stats.critique_fixes += 1
    if len(dropped) >= len(candidates):
        # Nothing left to rank means "not found"; that is the rerank's call to make, not this editor's.
        return updated
    return [candidate for position, candidate in enumerate(updated) if position not in dropped]


async def _finish(
    pi: PlanInput, candidates: list[_Candidate], budget: Budget, hits: Sequence[int]
) -> list[_Candidate]:
    """One editor reads every clip as a stranger and may move its edges or drop it."""
    agent = build_agent(TopicFinish, topic_finish_system(pi.language_name, budget), structural=True)
    try:
        finish = (await run(agent, _finish_prompt(pi, candidates), stats=pi.stats, stage="topic_finish")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Topic finish failed (%s); keeping the clips as they are", type(error).__name__)
        return candidates
    return apply_finish(pi, candidates, finish, budget, hits)


def _as_transcript_segment(index: UnitIndex, candidate: _Candidate) -> ai.TranscriptSegment:
    start, end = index.range_seconds(candidate.first, candidate.last)
    return ai.TranscriptSegment(
        start_time=ai._format_transcript_timestamp(int(start)),
        end_time=ai._format_transcript_timestamp(int(round(end))),
        text=index.text(candidate.first, candidate.last, candidate.skips),
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
    """Comparative order with calibrated scores (written onto the candidates), or None on failure.

    Candidates the judge rates as not really about the topic are left out:
    a viral clip on another subject never pads a topic result.
    """
    segments = [_as_transcript_segment(pi.index, candidate) for candidate in candidates]
    prompt = ai.build_candidate_rerank_prompt(
        segments, pi.ctx.performance_context, instructions=pi.instructions or None
    )
    try:
        agent = build_agent(TopicRanking, topic_rerank_system(pi.language_name, canonical), structural=True)
        result = await run(agent, prompt, timeout=ai.RERANK_TIMEOUT_SECONDS, stats=pi.stats, stage="topic_rerank")
        ranking: TopicRanking = result.output
        off_topic = {
            item.candidate_id for item in ranking.ranking if _clamp(item.on_topic, 0, 100) < MIN_RANKED_ON_TOPIC
        }
        for item in ranking.ranking:
            if not 0 <= item.candidate_id < len(candidates):
                continue
            candidate = candidates[item.candidate_id]
            title = ai.sanitize_hook_title(item.title or "") or ""
            # The extractor's title is often sharper; it stays when it still says what plays.
            if title and (candidate.retitle or not _title_is_grounded(pi.index, candidate)):
                # Written for the clip as it plays: the final cut, not the model's first span.
                candidate.hook_title = title
                candidate.retitle = False
            if item.same_point_as != item.candidate_id and 0 <= item.same_point_as < len(candidates):
                candidate.duplicate = True
            if _clamp(item.standalone, 0, 100) < MIN_RANKED_STANDALONE:
                candidate.weak = True
        kept = [position for position in range(len(candidates)) if position not in off_topic]
        if not kept:
            logger.info("Topic rerank found none of %d candidate(s) on topic", len(candidates))
            return []
        renumber = {position: number for number, position in enumerate(kept)}
        ranked = ai.apply_candidate_ranking(
            [segments[position] for position in kept],
            ai.CandidateRanking(
                ranking=[
                    ai.RankedCandidate(
                        candidate_id=renumber[item.candidate_id],
                        score=_clamp(item.score, 0, 100),
                        reason=item.reason,
                    )
                    for item in ranking.ranking
                    if item.candidate_id in renumber
                ]
            ),
        )
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Topic rerank failed (%s); keeping first-pass order", type(error).__name__)
        return None
    if off_topic:
        logger.info("Topic rerank left out %d off-topic candidate(s)", len(off_topic & set(range(len(candidates)))))
    owners = {id(segment): candidate for segment, candidate in zip(segments, candidates)}
    ordered = []
    for segment in ranked:
        candidate = owners[id(segment)]
        virality = segment.virality
        candidate.hook = virality.hook_score
        candidate.engagement = virality.engagement_score
        candidate.value = virality.value_score
        candidate.shareability = virality.shareability_score
        ordered.append(candidate)
    # The same point twice, or a clip that needs another clip, goes to the
    # reserve, never into the main selection (unless nothing else stands).
    if ordered and all(c.reserve_only for c in ordered):
        promoted = next((c for c in ordered if not c.duplicate), ordered[0])
        promoted.duplicate = promoted.weak = False
    return [c for c in ordered if not c.reserve_only] + [c for c in ordered if c.reserve_only]


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
    budget = topic_budget(spec.clip_length)
    await pi.ctx.progress(54, f"Finding where '{topic}' comes up...")
    locate = await _locate(pi, topic)
    canonical = " ".join(locate.canonical.split()) or topic
    suggested_early = _suggestions(topic, locate, outline.key_topics)
    if locate.fit == "different":
        # The video only shares a word with the topic ("crime" in a sermon about sin).
        raise topic_not_found(topic, [], suggested_early)
    if locate.fit == "broader" and pi.setting("match") == "strict":
        # The creator asked for this topic or nothing: no clips about something near it.
        raise topic_not_found(topic, [], suggested_early)
    if locate.fit == "broader":
        pi.note("TOPIC_BROADENED", {"topic": topic, "interpretation": " ".join(locate.interpretation.split())})
        canonical = topic.strip() or canonical

    no_space = index.no_space or is_no_space(pi.language, index.unit(1).text)
    sample = " ".join(unit.text for unit in index.units[:40])
    matchers = keyword_matchers(
        [topic, canonical, *locate.keywords], no_space, word_prefix=inflects_by_suffix(pi.language, sample)
    )
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
        for candidate in gate_candidates(index, moment, window, budget, pi.language, pi.cleanup, hits)
    ]
    candidates = merge_adjacent(index, dedupe(candidates), budget, pi.language, pi.cleanup)
    if not candidates:
        raise topic_not_found(topic, _mentions(index, hits), suggested)
    candidates = sorted(candidates, key=lambda item: (-item.relevance, -item.subtotal, item.first))
    await pi.ctx.progress(60, "Reading each clip as a stranger...")
    candidates = await _finish(pi, candidates, budget, hits)

    ranked: Optional[list[_Candidate]] = None
    if len(candidates) >= RERANK_MIN_CANDIDATES:
        ranked = await _rerank(pi, canonical, candidates)
    if ranked is not None:
        eligible = [candidate for candidate in ranked if candidate.subtotal >= MIN_RANKED_SCORE]
    else:
        eligible = candidates  # already relevance-first; each passed the bars on its own
    eligible = resolve_overlaps(index, eligible, budget, pi.language, pi.cleanup, hits)
    if not eligible:
        raise topic_not_found(topic, _mentions(index, hits), suggested)

    for candidate in eligible:
        if candidate.retitle:
            candidate.hook_title = grounded_title(index, candidate)
    limit = max(1, int(get_config().workflow_topic_max_clips))
    if pi.setting("max_clips") is not None:
        limit = min(limit, int(pi.setting("max_clips")))
    main = [candidate for candidate in eligible if not candidate.reserve_only]
    selected = main[:limit]
    reserve = [candidate for candidate in eligible if candidate not in selected][:RESERVE_CLIPS]
    coverage = coverage_of(index, selected, pi.cleanup)
    # Fewer clips because the creator asked for fewer isn't a thin topic.
    if coverage == "thin" and len(main) <= limit:
        pi.note("TOPIC_THIN", {"count": len(selected), "found": len(selected)})
    if index.coarse:
        pi.note("COARSE_TRANSCRIPT")

    segments = [
        _segment(pi, candidate, canonical, budget, reserve=False) for candidate in selected
    ] + [_segment(pi, candidate, canonical, budget, reserve=True) for candidate in reserve]
    # A typed topic that only fits broadly is shown as a name, never as a lowercase query.
    plan = base_plan(pi, title=canonical[:1].upper() + canonical[1:] if locate.fit == "broader" else canonical)
    plan["topic"] = {
        "query": topic,
        "canonical": canonical,
        "coverage": coverage,
        "found": len(selected),
        "suggested_topics": suggested[:6],
    }
    return segments, plan


def coverage_of(index: UnitIndex, selected: Sequence[_Candidate], cleanup: Optional[dict]) -> str:
    """"thin" when the distinct material is under 45 s, else "strong".

    Material, not clips: one video-long recipe is plenty.
    """
    covered: set[int] = set()
    for candidate in selected:
        skipped = {uid for low, high in candidate.skips for uid in range(low, high + 1)}
        covered.update(uid for uid in range(candidate.first, candidate.last + 1) if uid not in skipped)
    seconds = sum(index.speech_seconds(uid, uid, (), cleanup) for uid in covered)
    return "thin" if seconds < THIN_MATERIAL_SECONDS else "strong"


def _content_words(text: str) -> set[str]:
    return {word for word in re.findall(r"[^\W\d_]{4,}", fold(text))}


def _title_is_grounded(index: UnitIndex, candidate: _Candidate) -> bool:
    said = _content_words(index.text(candidate.first, candidate.last, candidate.skips))
    return bool(candidate.hook_title) and _content_words(candidate.hook_title) <= said


def grounded_title(index: UnitIndex, candidate: _Candidate) -> str:
    """The extractor's title when every content word in it is said in the clip, else one from what it says."""
    title = candidate.hook_title
    if _title_is_grounded(index, candidate):
        return title
    return compose_title(candidate.why) or compose_title(index.text(candidate.first, candidate.first)) or title


def _segment(pi: PlanInput, candidate: _Candidate, canonical: str, budget: Budget, *, reserve: bool) -> dict[str, Any]:
    index = pi.index
    meta: dict[str, Any] = {"topic": canonical, "topic_relevance": candidate.relevance, "why": candidate.why}
    if reserve:
        meta["reserve"] = True
    ranges = index.ranges_for(candidate.first, candidate.last, candidate.skips)
    return planned_segment(
        ranges=ranges,
        junction_fades=[PART_JUNCTION_FADE] * (len(ranges) - 1),
        max_seconds=budget.render_cap,
        required=False,
        text=index.text(candidate.first, candidate.last, candidate.skips),
        hook_title=compose_title(candidate.hook_title),
        reasoning=candidate.why,
        workflow_role="topic",
        meta=meta,
        relevance_score=candidate.relevance / 100,
        scores=candidate.scores(),
        hook_type=candidate.hook_type,
    )
