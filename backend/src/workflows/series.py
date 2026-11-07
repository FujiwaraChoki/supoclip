"""Series planner: Part 1..N, consecutive, in source order, sharing exact cuts.

Order of work: outline (cached) → scope (a showrunner picks chapters, when the
video is too long to serialize whole) → finale check → draft (+ at most one
repair) → the first sentence, chosen on its own → deterministic partition →
critique read as a first-time viewer → re-partition with its fixes as
constraints → final copy for the parts as they are. The model only proposes
part ends and skips; ``partition.partition_series`` owns lengths, contiguity
and cuts.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .. import ai
from .boundaries import (
    STRONG_BOUNDARY,
    ends_for_length,
    kept_runs,
    nearest_sentence_start,
    nearest_strong_end,
    strong_ends_around,
)
from .durations import SERIES_MAX_PARTS, SERIES_MIN_PARTS, Budget, part_budget
from .errors import WorkflowError
from .language import forward_hook, is_acknowledgement, opens_with_back_reference, part_label, unresolved_reference
from .llm import build_agent, is_cancellation, run, run_with_repair
from .outline import chapter_ranges
from .partition import SKIP_MIN_SECONDS, PartitionConstraints, PartitionResult, partition_series, validate_skips
from ..media.timeline import seconds_to_mmss
from .planning import PlanInput, base_plan, planned_segment, rounded_ranges
from .prompts import (
    outline_block,
    series_critique_system,
    series_draft_system,
    series_list_skips_system,
    series_cold_open_system,
    series_open_system,
    series_parts_clause,
    series_retitle_system,
    series_scope_system,
    transcript_block,
    with_instructions,
)
from .schemas import SeriesColdOpen, SeriesCopy, SeriesCritique, SeriesDraft, SeriesListSkips, SeriesOpen, SeriesScope
from .titles import compose_title, heard_share, strip_numbering, title_words
from .units import UnitIndex, format_clock

logger = logging.getLogger(__name__)

# Share of a series span expected to survive as parts. Skips are rare, so the
# whole span becomes parts: 10 parts of at most ideal length.
SPAN_COVERAGE = 1.0
# A series uses at least this share of its room while strong chapters remain.
SCOPE_FILL_SHARE = 0.85
# A video that walks through a list keeps every item: more room, and skips instead of chapter cuts.
# The dead weight is marked in its own small call: asking the draft to fit parts
# and find 13 % of a long video in skips ran it out of output tokens, every time.
LIST_PART_SECONDS = 135.0
# The last line of a series is at most this far after a payoff, or it stopped rather than finished.
FINALE_MAX_SECONDS_AFTER_PAYOFF = 45.0
FINALE_NEAR_END_SECONDS = 150.0
# Pulling the ending back never loses a chapter rated above this, nor more than this share.
FINALE_PULLBACK_MAX_STARS = 3
FINALE_PULLBACK_MAX_SHARE = 0.3
# Whole-story mode needs a storyline: one arc covering at least this share of the content.
THROUGHLINE_ARC_SHARE = 0.5
SCOPE_SNAP_UNITS = 3
SPONSOR_SKIP_MAX_SECONDS = 120.0
LAST_PART_SLACK_UNITS = 3
CRITIQUE_MAX_MOVE_UNITS = 4
CRITIQUE_JOIN_MOVE_UNITS = 8
CRITIQUE_MAX_FIXES = 6
# Lines around a join worth showing the critique: a real question or hook this close.
JOIN_CANDIDATE_SECONDS = 25.0
JOIN_CANDIDATE_MAX = 4
# At most one title in three is a question; a title isn't the previous part's last line.
TITLE_QUESTION_SHARE = 3
TITLE_ECHO_SHARE = 0.6
SERIES_OPEN_MAX_CANDIDATES = 24
SERIES_OPEN_MIN_WORDS = 3
# Without a usable draft, a span this short is still partitioned by code alone.
FALLBACK_MAX_SPAN_SECONDS = 40 * 60
SKIP_MAX_SHARE = 0.4
PART_JUNCTION_FADE = 0.10
# Intro and outro chapters rated above this carry story and stay in the series.
FRAMING_MAX_IMPORTANCE = 2
# A cold open is one or two sentences from later in the series.
COLD_OPEN_MIN_SECONDS = 3.0
COLD_OPEN_MAX_SECONDS = 9.0
COLD_OPEN_MAX_UNITS = 3
COLD_OPEN_MAX_CANDIDATES = 24
# Part 1 may step past a slow warm-up, never past story the later parts need:
# at most this much speech before start_unit (or this share of the material).
LEAD_TRIM_MAX_SECONDS = 60.0
LEAD_TRIM_MAX_SHARE = 0.1
# A series never ends on a listener's "Right." or "Yeah.": short replies at the
# very end of the material are left out when the line before them lands.
BACKCHANNEL_MAX_WORDS = 2
BACKCHANNEL_MAX_SECONDS = 1.0

_SKIP_KINDS_AT_EDGES = {"intro", "sponsor", "tangent"}
_SKIP_KINDS_AT_END = {"outro", "sponsor", "tangent"}
# Trailing clauses that hang off the line before ("And for which the margins are wide.").
_TRAILING_FRAGMENTS = ("and for which", "for which", "which is", "which was")
# A definition question promises nothing ("What is X?", "Was ist X?").
_DEFINITION_TITLE_RE = {
    "en": re.compile(r"^(what|how)\s+(is|does|do|are)\b", re.IGNORECASE),
    "de": re.compile(r"^(was|wie)\s+(ist|sind|macht|funktioniert|bedeutet)\b", re.IGNORECASE),
}
_LEFT_OUT = "chapter left out"
_LISTING_LINE = re.compile(r"^#(\d+) \S+ \+\S+s")
_PLAN_FAILED = "The AI editor couldn't finish this plan. Please try again."
_NO_STORY = "We couldn't find a storyline long enough to split into parts. Try Highlights or a Recap."


@dataclass
class _Copy:
    title: str = ""
    delivers: str = ""
    ends_on: str = ""
    post_caption: str = ""
    best_line: int = -1


@dataclass
class _Material:
    span: tuple[int, int]
    forced_skips: list[tuple[int, int]] = field(default_factory=list)
    skip_reasons: dict[tuple[int, int], str] = field(default_factory=dict)

    @property
    def left_out(self) -> list[tuple[int, int]]:
        return [skip for skip in self.forced_skips if self.skip_reasons.get(skip) == _LEFT_OUT]


@dataclass
class _Scope:
    """What the showrunner chose: chapters by number (1-based), and how."""

    keep: list[int]
    spare: list[int]
    story_found: bool
    premise: str = ""
    list_items: int = 0
    span: Optional[tuple[int, int]] = None  # a stretch inside one huge chapter: no chapters to choose from


def _clean_title(title: str) -> str:
    return strip_numbering(ai.sanitize_hook_title(title) or "", list_number=True)


def _clean(text: str) -> str:
    return " ".join((text or "").split())


def span_limit(budget: Budget, part_count: Optional[int]) -> float:
    """Most speech (seconds) a series can tell whole: N parts, or 10 in auto mode."""
    return (part_count or SERIES_MAX_PARTS) * budget.ideal_max / SPAN_COVERAGE


def _framing(chapter: Any, kinds: set[str]) -> bool:
    """A greeting, sign-off or sponsor read at the edge of the story.

    An "outro" the outline rates as important is where the story ends (models
    sometimes label the finale that way), so it stays in.
    """
    return chapter.kind in kinds and (chapter.kind == "sponsor" or chapter.importance <= FRAMING_MAX_IMPORTANCE)


def content_span(pi: PlanInput) -> tuple[int, int]:
    """From the first chapter that isn't intro or sponsor to the last that isn't outro or sponsor."""
    chapters = pi.outline.outline.chapters
    spans = chapter_ranges(pi.outline.outline, pi.index)
    first = next((i for i, c in enumerate(chapters) if not _framing(c, _SKIP_KINDS_AT_EDGES)), None)
    last = next(
        (i for i in range(len(chapters) - 1, -1, -1) if not _framing(chapters[i], _SKIP_KINDS_AT_END)), None
    )
    if first is None or last is None or first > last:
        return 1, len(pi.index)
    return spans[first][0], spans[last][1]


def _clock_range(index: UnitIndex, first: int, last: int) -> dict[str, str]:
    return {"start": format_clock(index.unit(first).start), "end": format_clock(index.unit(last).end)}


def _set_note_params(pi: PlanInput, code: str, params: dict[str, Any]) -> None:
    for note in pi.notes:
        if note.get("code") == code:
            note["params"] = dict(params)


def lead_trim_limit(index: UnitIndex, material: "_Material", cleanup: dict) -> float:
    """Most speech the draft's start_unit may leave out at the front of the material."""
    a, b = material.span
    story = index.speech_seconds(a, b, material.forced_skips, cleanup)
    return max(LEAD_TRIM_MAX_SECONDS, LEAD_TRIM_MAX_SHARE * story)


def _lead_seconds(index: UnitIndex, material: "_Material", start: int, cleanup: dict) -> float:
    a = material.span[0]
    if start <= a:
        return 0.0
    skips = [skip for skip in material.forced_skips if skip[1] < start]
    return index.speech_seconds(a, start - 1, skips, cleanup)


def latest_allowed_start(index: UnitIndex, material: "_Material", start: int, cleanup: dict) -> int:
    """The unit nearest ``start`` that opens a sentence and leaves out no more than the lead limit."""
    a = material.span[0]
    limit = lead_trim_limit(index, material, cleanup)
    best = a
    for uid in range(a, start + 1):
        if _lead_seconds(index, material, uid, cleanup) > limit:
            break
        if uid == a or index.unit(uid).starts_sentence:
            best = uid
    return best


def clean_skip(index: UnitIndex, low: int, high: int) -> bool:
    """A skip the viewer won't hear: the line before it ends a sentence and the line after starts one.

    Cutting "when it comes to sleep aids, [skipped] We know..." leaves a
    sentence with no end; such a skip is not used.
    """
    if low <= 1 or high >= len(index):
        return False
    return index.boundary_strength(low - 1) >= STRONG_BOUNDARY and index.unit(high + 1).starts_sentence


def _is_backchannel(index: UnitIndex, uid: int) -> bool:
    unit = index.unit(uid)
    if index.coarse or unit.w0 < 0:
        return False
    if unit.w1 - unit.w0 + 1 <= BACKCHANNEL_MAX_WORDS and unit.end - unit.start <= BACKCHANNEL_MAX_SECONDS:
        return True
    words = unit.w1 - unit.w0 + 1
    if words <= 3 and is_acknowledgement(unit.text, index.language):
        return True  # "Right." "Yeah, right." at the very end
    opening = " ".join(unit.text.casefold().replace(",", " ").split())
    return opening.startswith(_TRAILING_FRAGMENTS)


def trim_backchannel_tail(index: UnitIndex, first: int, last: int) -> int:
    """``last`` moved back past trailing one- or two-word replies onto a strong end."""
    end = last
    while end > first and _is_backchannel(index, end):
        end -= 1
    return end if end != last and index.boundary_strength(end) >= STRONG_BOUNDARY else last


def _trim_to_limit(index: UnitIndex, first: int, last: int, limit: float, cleanup: dict) -> int:
    """The last strong end at or before ``last`` that keeps ``first..end`` within ``limit`` seconds."""
    best_any: Optional[int] = None
    for end in range(last, first - 1, -1):
        if index.speech_seconds(first, end, (), cleanup) > limit:
            continue
        if index.boundary_strength(end) >= STRONG_BOUNDARY:
            return end
        if best_any is None:
            best_any = end
    return best_any if best_any is not None else first


def _chapters(pi: PlanInput) -> list[tuple[int, Any, tuple[int, int]]]:
    """(number, chapter, (first, last)) for every outline chapter."""
    outline = pi.outline.outline
    return [
        (number, chapter, span)
        for number, (chapter, span) in enumerate(zip(outline.chapters, chapter_ranges(outline, pi.index)), start=1)
    ]


def chapter_ends(pi: PlanInput) -> list[int]:
    return [span[1] for _, _, span in _chapters(pi)]


def has_throughline(pi: PlanInput, content: tuple[int, int]) -> bool:
    """Whether the content is one storyline: not a news bulletin, and not several stories none of which dominates."""
    outline, index = pi.outline.outline, pi.index
    if outline.format == "news":
        return False
    arcs = outline.story_arcs
    if len(arcs) < 2:
        return True
    total = index.unit(content[1]).end - index.unit(content[0]).start
    if total <= 0:
        return True
    best = 0.0
    for arc in arcs:
        low, high = max(arc.first_unit, content[0]), min(arc.last_unit, content[1])
        if low <= high:
            best = max(best, (index.unit(high).end - index.unit(low).start) / total)
    return best >= THROUGHLINE_ARC_SHARE


def _chapter_seconds(pi: PlanInput, span: tuple[int, int]) -> float:
    return pi.index.speech_seconds(span[0], span[1], (), pi.cleanup)


def _strong_arcs(pi: PlanInput) -> list[list[int]]:
    """The chapters of each ★4-5 arc, strongest first. Stories only: elsewhere an arc doesn't bind chapters."""
    outline = pi.outline.outline
    if outline.format not in ("story", "vlog"):
        return []
    chapters = _chapters(pi)
    return [
        [number for number, _, cs in chapters if cs[0] <= arc.last_unit and arc.first_unit <= cs[1]]
        for arc in sorted(outline.story_arcs, key=lambda item: -item.strength)
        if arc.strength >= 4
    ]


def _arc_chapters(pi: PlanInput, keep: Sequence[int]) -> list[int]:
    """Chapters a kept story needs: the rest of any ★4-5 arc it has started."""
    needed: list[int] = []
    for inside in _strong_arcs(pi):
        if any(number in keep for number in inside):
            needed += [number for number in inside if number not in keep and number not in needed]
    return needed


def _choose_by_value(
    pi: PlanInput, candidates: Sequence[tuple[int, Any, tuple[int, int]]], capacity: float
) -> set[int]:
    """The chapters worth the most within ``capacity`` seconds (a knapsack): value (stars - 1)² per second."""
    best: dict[int, tuple[float, frozenset[int]]] = {0: (0.0, frozenset())}
    room = int(max(0.0, capacity))
    for number, chapter, cs in candidates:
        seconds = _chapter_seconds(pi, cs)
        weight = int(math.ceil(seconds))
        value = (chapter.importance - 1) ** 2 * seconds
        updated = dict(best)
        for used, (total, chosen) in best.items():
            if used + weight > room:
                continue
            current = updated.get(used + weight)
            if current is None or total + value > current[0]:
                updated[used + weight] = (total + value, chosen | {number})
        best = updated
    return set(max(best.values(), key=lambda item: item[0])[1])


def _hook_and_finale(pi: PlanInput, span: tuple[int, int]) -> tuple[Optional[int], Optional[int]]:
    """The chapters a series can't lose inside ``span``: its first strong chapter and its last payoff."""
    chapters = _chapters(pi)
    hook = next(
        (number for number, chapter, cs in chapters
         if cs[1] >= span[0] and cs[0] <= span[1] and chapter.kind in ("content", "story") and chapter.importance >= 4),
        None,
    )
    payoffs = [payoff.last_unit for payoff in pi.outline.outline.payoffs if span[0] <= payoff.last_unit <= span[1]]
    finale = next((number for number, _, cs in chapters if payoffs and cs[0] <= max(payoffs) <= cs[1]), None)
    return hook, finale


def _fit_chapters(pi: PlanInput, keep: Sequence[int], limit: float, *, spare: Sequence[int] = ()) -> list[int]:
    """The showrunner's chapters inside the limit: whole arcs, then the room filled from the spare list.

    Over the limit the hook and finale stay and a knapsack chooses the rest.
    Under 85 % of it, spare chapters come in, best first, while they fit.
    """
    chapters = {number: (chapter, cs) for number, chapter, cs in _chapters(pi)}
    kept = sorted({number for number in keep if number in chapters})
    if not kept:
        return []
    for number in _arc_chapters(pi, kept):
        extra = _chapter_seconds(pi, chapters[number][1])
        if sum(_chapter_seconds(pi, chapters[item][1]) for item in kept) + extra <= limit:
            kept.append(number)
    kept.sort()

    def total(numbers: Sequence[int]) -> float:
        return sum(_chapter_seconds(pi, chapters[number][1]) for number in numbers)

    if total(kept) > limit:
        span = (chapters[kept[0]][1][0], chapters[kept[-1]][1][1])
        hook, finale = _hook_and_finale(pi, span)
        must = [number for number in kept if number in (hook, finale)]
        rest = [(number, *chapters[number]) for number in kept if number not in must]
        chosen = sorted(set(must) | _choose_by_value(pi, rest, limit - total(must)))
        # Every chapter bigger than the room: the strongest one stays, to be narrowed to an arc.
        kept = chosen or [max(kept, key=lambda number: chapters[number][0].importance)]
    for number in spare:
        if total(kept) >= SCOPE_FILL_SHARE * limit:
            break
        if number in chapters and number not in kept and total(kept) + _chapter_seconds(pi, chapters[number][1]) <= limit:
            kept = sorted(kept + [number])
    return kept


def _chapters_in_content(pi: PlanInput, content: tuple[int, int]) -> list[tuple[int, Any, tuple[int, int]]]:
    return [
        item
        for item in _chapters(pi)
        if content[0] <= item[2][0] and item[2][1] <= content[1] and item[1].kind != "sponsor"
    ]


def _fallback_keep(pi: PlanInput, content: tuple[int, int], limit: float) -> list[int]:
    """Without a usable scope answer: the most valuable chapters of the content that fit."""
    candidates = _chapters_in_content(pi, content)
    if not candidates:
        return []
    hook, finale = _hook_and_finale(pi, content)
    must = [item for item in candidates if item[0] in (hook, finale)]
    room = limit - sum(_chapter_seconds(pi, item[2]) for item in must)
    rest = [item for item in candidates if item not in must]
    return sorted({item[0] for item in must} | _choose_by_value(pi, rest, room))


def _narrowed(pi: PlanInput, first: int, last: int, limit: float) -> tuple[int, int]:
    """One chapter bigger than the whole limit: its strongest arc, cut to the last strong end that fits."""
    index = pi.index
    arcs = [
        arc
        for arc in pi.outline.outline.story_arcs
        if arc.first_unit <= last and first <= arc.last_unit
    ]
    if arcs:
        arc = max(arcs, key=lambda item: item.strength)
        first, last = max(first, arc.first_unit), min(last, arc.last_unit)
    first = nearest_sentence_start(index, first, SCOPE_SNAP_UNITS) or first
    return first, _trim_to_limit(index, first, last, limit, pi.cleanup)


async def _scope(pi: PlanInput, budget: Budget, limit: float, content: tuple[int, int]) -> _Scope:
    """Arc mode: the chapters that make the best series (one model call)."""
    index, outline = pi.index, pi.outline.outline
    parts = pi.ctx.spec.options.part_count
    arc_hint = (
        f"enough for {parts} parts of {budget.ideal_min:.0f}-{budget.ideal_max:.0f} seconds"
        if parts
        else f"room for up to {SERIES_MAX_PARTS} parts of {budget.ideal_min:.0f}-{budget.ideal_max:.0f} seconds"
    )
    system = series_scope_system(pi.language_name, max(1, int(limit // 60)), arc_hint)
    prompt = with_instructions(
        f"The video has units #1–#{len(index)}.\n\n" + outline_block(outline, index), pi.instructions
    )
    agent = build_agent(SeriesScope, system, structural=True)
    try:
        answer = (await run(agent, prompt, stats=pi.stats, stage="series_scope")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Series scope failed (%s); choosing chapters from the outline", type(error).__name__)
        keep = _fallback_keep(pi, content, limit)
        if not keep:
            logger.error("The outline has no usable chapter either")
            raise WorkflowError("plan_failed", _PLAN_FAILED) from error
        return _Scope(keep=keep, spare=[], story_found=any(arc.strength > 1 for arc in outline.story_arcs))
    # Chapter numbers only (never transcript text): whether the model or the rules below cut a chapter.
    logger.info("Series scope chose %s (spare %s, list %s)", answer.keep_chapters, answer.spare_chapters, answer.list_items)
    usable = {item[0] for item in _chapters_in_content(pi, content)}
    keep = [number for number in answer.keep_chapters if number in usable]
    spare = [number for number in answer.spare_chapters if number in usable and number not in keep]
    return _Scope(
        keep=keep,
        spare=spare,
        story_found=answer.story_found,
        premise=_clean(answer.premise),
        list_items=max(0, answer.list_items),
    )


def _material_from_scope(pi: PlanInput, scope: _Scope, budget: Budget, limit: float) -> _Material:
    """The kept chapters as one span, with everything between them as forced skips."""
    chapters = {number: cs for number, _, cs in _chapters(pi)}
    index = pi.index
    keep = sorted(scope.keep)
    if scope.list_items:
        # Every item stays: the whole stretch of the list, shortened by skips in the draft.
        keep = list(range(keep[0], keep[-1] + 1)) if keep else []
    else:
        keep = _fit_chapters(pi, keep, limit, spare=scope.spare)
    if not keep:
        if not scope.story_found and all(arc.strength <= 1 for arc in pi.outline.outline.story_arcs):
            raise WorkflowError("no_story", _NO_STORY)
        keep = _fallback_keep(pi, content_span(pi), limit)
    if not keep:
        raise WorkflowError("no_story", _NO_STORY)
    if len(keep) == 1 and _chapter_seconds(pi, chapters[keep[0]]) > limit:
        return _material(pi, _narrowed(pi, *chapters[keep[0]], limit), limit)
    span = (chapters[keep[0]][0], chapters[keep[-1]][1])
    material = _material(pi, span)
    for number in range(keep[0] + 1, keep[-1]):
        if number in keep or chapters[number] in material.forced_skips:
            continue
        material.forced_skips.append(chapters[number])
        material.skip_reasons[chapters[number]] = _LEFT_OUT
    material.forced_skips.sort()
    if index.speech_seconds(*material.span, material.forced_skips, pi.cleanup) > limit and not scope.list_items:
        return _material(pi, material.span, limit)
    return material


async def _list_skips(pi: PlanInput, material: _Material, limit: float) -> _Material:
    """A list that runs long loses its dead weight first, so the draft only has to place parts."""
    index, cleanup = pi.index, pi.cleanup
    speech = index.speech_seconds(*material.span, material.forced_skips, cleanup)
    if speech <= limit:
        return material
    agent = build_agent(SeriesListSkips, series_list_skips_system(pi.language_name, speech - limit), structural=True)
    prompt = with_instructions(
        outline_block(pi.outline.outline, index, include=("chapters", "points", "payoffs"))
        + "\n\n"
        + transcript_block(_listing_without(index, material.span, material.forced_skips)),
        pi.instructions,
    )
    try:
        answer = (await run(agent, prompt, stats=pi.stats, stage="series_list_skips")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Series list skips failed (%s); the draft gets the whole list", type(error).__name__)
        return material
    skips = list(material.forced_skips)
    reasons = dict(material.skip_reasons)
    for skip in answer.skips:
        if index.speech_seconds(*material.span, skips, cleanup) <= limit:
            break
        low, high = sorted((skip.first_unit, skip.last_unit))
        if (
            not material.span[0] < low <= high < material.span[1]
            or any(low <= other[1] + 1 and other[0] <= high + 1 for other in skips)
            or not clean_skip(index, low, high)
            or protected_skip(pi, low, high) is not None
            or index.speech_seconds(low, high, (), cleanup) < SKIP_MIN_SECONDS
        ):
            continue
        skips.append((low, high))
        reasons[(low, high)] = _clean(skip.reason) or "dead weight"
    return _Material(span=material.span, forced_skips=sorted(skips), skip_reasons=reasons)


def _note_left_out(pi: PlanInput, material: _Material) -> None:
    titles = []
    for number, chapter, cs in _chapters(pi):
        if cs in material.left_out:
            titles.append(chapter.title.strip() or f"C{number}")
    if titles:
        pi.note("CHAPTERS_LEFT_OUT", {"chapters": ", ".join(titles)})


def _land_on(pi: PlanInput, end: int) -> int:
    """A strong cut on or right next to the payoff's last unit."""
    if pi.index.boundary_strength(end) >= STRONG_BOUNDARY:
        return end
    return nearest_strong_end(pi.index, end, SCOPE_SNAP_UNITS) or end


def validate_finale(
    pi: PlanInput,
    material: _Material,
    budget: Budget,
    limit: float,
    *,
    allowed: Optional[Sequence[int]] = None,
    force: bool = False,
) -> _Material:
    """A series that ends on or soon after a payoff, not wherever the budget ran out.

    Otherwise it extends by the next chapter holding a payoff while within the
    limit (only chapters in ``allowed``, when given), else pulls back to the
    latest payoff unless that gives up a strong chapter or too much of the
    series. ``force`` (the critique says the finale doesn't land) tries the
    same even if a payoff is near the end.
    """
    index, outline = pi.index, pi.outline.outline
    a, b = material.span
    played = [
        payoff.last_unit
        for payoff in outline.payoffs
        if a <= payoff.last_unit <= b and not any(low <= payoff.last_unit <= high for low, high in material.forced_skips)
    ]

    def tail_seconds(payoff_end: int) -> float:
        if payoff_end >= b:
            return 0.0
        return index.speech_seconds(payoff_end + 1, b, _skips_inside(material, payoff_end + 1, b), pi.cleanup)

    if not force and any(tail_seconds(end) <= FINALE_MAX_SECONDS_AFTER_PAYOFF for end in played):
        return material
    speech = index.speech_seconds(a, b, material.forced_skips, pi.cleanup)
    chapters = _chapters(pi)
    for number, chapter, cs in chapters:
        if cs[0] <= b or chapter.kind == "sponsor" or _framing(chapter, _SKIP_KINDS_AT_END):
            continue
        if allowed is not None and number not in allowed:
            continue
        if not any(cs[0] <= payoff.last_unit <= cs[1] for payoff in outline.payoffs):
            continue
        if speech + _chapter_seconds(pi, cs) > limit:
            continue
        extended = _Material(span=(a, cs[1]), forced_skips=list(material.forced_skips), skip_reasons=dict(material.skip_reasons))
        if cs[0] > b + 1:
            gap = (b + 1, cs[0] - 1)
            extended.forced_skips.append(gap)
            extended.skip_reasons[gap] = _LEFT_OUT
        return extended
    if not played:
        return material
    end = _land_on(pi, max(played))
    if end >= b or index.boundary_strength(end) < STRONG_BOUNDARY:
        return material
    lost = index.speech_seconds(end + 1, b, _skips_inside(material, end + 1, b), pi.cleanup)
    strong_chapter = any(
        chapter.importance > FINALE_PULLBACK_MAX_STARS and cs[1] > end and cs[0] <= b and cs not in material.forced_skips
        for _, chapter, cs in chapters
    )
    if strong_chapter or lost > FINALE_PULLBACK_MAX_SHARE * speech or speech - lost < 2 * budget.min:
        return material
    return _Material(
        span=(a, end),
        forced_skips=[skip for skip in material.forced_skips if skip[1] < end],
        skip_reasons=dict(material.skip_reasons),
    )


def _skips_inside(material: _Material, first: int, last: int) -> list[tuple[int, int]]:
    return [skip for skip in material.forced_skips if first <= skip[0] and skip[1] <= last]


def _leave_out_padding(pi: PlanInput, material: _Material, budget: Budget) -> _Material:
    """A whole-story series still drops chapters rated ★1-2 that no ★4-5 arc runs through: no filler parts.

    Not in a story or a vlog, where a weak chapter may still carry the plot, and
    not when too little would be left for a series.
    """
    outline, index = pi.outline.outline, pi.index
    if outline.format in ("story", "vlog"):
        return material
    arcs = [arc for arc in outline.story_arcs if arc.strength >= 4]
    span = material.span
    dropped = [
        cs
        for _, chapter, cs in _chapters(pi)
        if chapter.importance <= FRAMING_MAX_IMPORTANCE
        and span[0] < cs[0]
        and cs[1] < span[1]
        and cs not in material.forced_skips
        and not any(cs[0] <= arc.last_unit and arc.first_unit <= cs[1] for arc in arcs)
    ]
    skips = sorted(material.forced_skips + dropped)
    if not dropped or index.speech_seconds(*span, skips, pi.cleanup) < 3 * budget.min:
        return material
    reasons = {**material.skip_reasons, **{cs: _LEFT_OUT for cs in dropped}}
    return _Material(span=span, forced_skips=skips, skip_reasons=reasons)


def _skipped_seconds(index: UnitIndex, material: "_Material", cleanup: dict) -> float:
    return sum(index.speech_seconds(low, high, (), cleanup) for low, high in material.forced_skips)


def _material(pi: PlanInput, span: tuple[int, int], limit: Optional[float] = None) -> _Material:
    """The span, plus sponsor reads (and, over ``limit``, the least valuable whole chapters) inside it as forced skips.

    A knapsack chooses what stays: value (stars - 1)² × seconds. The series'
    hook and finale (the first content chapter rated 4 or more and the chapter
    holding the span's last payoff) always stay, and so does a chapter of a
    story's ★4-5 arc whenever that fits. Only when that is not enough does the
    span lose chapters from its front.
    """
    index = pi.index
    material = _Material(span=span)
    chapters = _chapters(pi)
    for _, chapter, (first, last) in chapters:
        if chapter.kind != "sponsor" or not (span[0] < first and last < span[1]):
            continue
        if index.speech_seconds(first, last, (), pi.cleanup) <= SPONSOR_SKIP_MAX_SECONDS:
            material.forced_skips.append((first, last))
            material.skip_reasons[(first, last)] = "sponsor read"
    if material.forced_skips:
        pi.note("SPONSOR_SKIPPED")
    if limit is None:
        return material

    def speech() -> float:
        return index.speech_seconds(*material.span, material.forced_skips, pi.cleanup)

    if speech() <= limit:
        return material
    inside = [(number, chapter, cs) for number, chapter, cs in chapters if span[0] < cs[0] and cs[1] < span[1]]
    hook, finale = _hook_and_finale(pi, span)
    droppable = [item for item in inside if item[0] not in (hook, finale) and item[1].kind != "sponsor"]
    arc_numbers = {number for inside in _strong_arcs(pi) for number in inside}
    room = limit - index.speech_seconds(*span, [item[2] for item in droppable] + material.forced_skips, pi.cleanup)
    protected = [item for item in droppable if item[0] in arc_numbers]
    if sum(_chapter_seconds(pi, item[2]) for item in protected) <= room:
        room -= sum(_chapter_seconds(pi, item[2]) for item in protected)
        droppable = [item for item in droppable if item[0] not in arc_numbers]
    stays = _choose_by_value(pi, droppable, room) if droppable else set()
    for number, chapter, cs in droppable:
        if number in stays:
            continue
        material.forced_skips.append(cs)
        material.skip_reasons[cs] = _LEFT_OUT
    material.forced_skips.sort()
    hook_span = next((cs for number, _, cs in chapters if number == hook), None)
    while speech() > limit:
        starts = [
            cs[0]
            for _, _, cs in chapters
            if material.span[0] < cs[0] < material.span[1] and (hook_span is None or cs[0] <= hook_span[0])
        ]
        if not starts:
            break
        start = starts[0]
        material.span = (start, material.span[1])
        material.forced_skips = [skip for skip in material.forced_skips if skip[0] > start]
    if speech() > limit:
        # No chapter structure left to cut by: end at the last strong cut that fits.
        a, b = material.span
        end = _trim_to_limit(index, a, b, limit + _skipped_seconds(index, material, pi.cleanup), pi.cleanup)
        material.span = (a, end)
        material.forced_skips = [skip for skip in material.forced_skips if skip[1] < end]
    return material


# -- draft -------------------------------------------------------------------------------


def protected_skip(pi: PlanInput, low: int, high: int) -> Optional[str]:
    """What a skip of ``low..high`` would take from the series: an outline payoff, or the core of a ★4-5 point."""
    outline = pi.outline.outline
    for payoff in outline.payoffs:
        if payoff.first_unit <= high and low <= payoff.last_unit:
            return payoff.what.strip() or "a payoff"
    for point in outline.main_points:
        if point.importance >= 4 and point.core_first <= high and low <= point.core_last:
            return point.statement.strip() or "a main point"
    return None


def title_issues(titles: Sequence[str], before: Sequence[str], language: Optional[str]) -> list[str]:
    """What keeps part titles from stopping a stranger. ``before``: the line each part follows ("" for Part 1)."""
    issues: list[str] = []
    named = [title for title in titles if title.strip()]
    questions = [title for title in named if title.rstrip().endswith(("?", "？", "؟"))]
    allowed = math.ceil(len(titles) / TITLE_QUESTION_SHARE)
    if len(questions) > allowed:
        issues.append(
            f"{len(questions)} of {len(titles)} part titles are questions; at most {allowed} may be. Build the others "
            "from the strongest concrete thing in the part: a number, a name, a surprising claim, a stake."
        )
    definition = _DEFINITION_TITLE_RE.get((language or "")[:2])
    for number, (title, line) in enumerate(zip(titles, before), start=1):
        if definition is not None and definition.search(title.strip()):
            issues.append(f'Part {number}\'s title "{title}" is a definition question; name what the part reveals.')
        if line and title_words(title) and heard_share(title, line) >= TITLE_ECHO_SHARE:
            issues.append(f'Part {number}\'s title "{title}" repeats the previous part\'s last line; a stranger has not heard it.')
    return issues


class _DraftRules:
    """Validator for the draft, worded as the fixes it asks for."""

    def __init__(
        self,
        pi: PlanInput,
        material: _Material,
        budget: Budget,
        part_count: Optional[int],
        max_auto: int,
        limit: Optional[float] = None,
    ):
        self.pi = pi
        self.index = pi.index
        self.material = material
        self.budget = budget
        self.part_count = part_count
        self.max_auto = max_auto
        self.limit = limit

    def skips(self, draft: SeriesDraft) -> list[tuple[int, int]]:
        drafted = [tuple(sorted((skip.first_unit, skip.last_unit))) for skip in draft.skips]
        return validate_skips(
            self.index,
            self.material.span,
            [
                skip
                for skip in drafted
                if clean_skip(self.index, *skip) and protected_skip(self.pi, *skip) is None
            ]
            + self.material.forced_skips,
        )

    def __call__(self, draft: SeriesDraft) -> list[str]:
        index, budget, cleanup = self.index, self.budget, self.pi.cleanup
        a, b = self.material.span
        issues: list[str] = []
        start = draft.start_unit
        if not a <= start <= b:
            issues.append(f"start_unit #{start} is outside the material (#{a}–#{b}).")
            start = min(max(start, a), b)
        lead = _lead_seconds(index, self.material, start, cleanup)
        if lead > lead_trim_limit(index, self.material, cleanup):
            allowed = latest_allowed_start(index, self.material, start, cleanup)
            issues.append(
                f"start_unit #{start} leaves out {lead:.0f}s of the story before it. The series tells the "
                f"whole story: start at #{allowed} or earlier, on the first sentence that hooks."
            )
        count = len(draft.parts)
        if self.part_count is not None and count != self.part_count:
            issues.append(f"You made {count} parts; make exactly {self.part_count}.")
        elif self.part_count is None and not SERIES_MIN_PARTS <= count <= self.max_auto:
            issues.append(f"You made {count} parts; choose between {SERIES_MIN_PARTS} and {self.max_auto}.")

        skips = self.skips(draft)
        for skip in draft.skips:
            low, high = sorted((skip.first_unit, skip.last_unit))
            if any(low <= edge <= high for edge in self._edges(draft, start)):
                issues.append(f"Skip #{low}–#{high} contains a part's first or last unit.")
            elif a < low and high < b and not clean_skip(index, low, high):
                issues.append(
                    f"Skip #{low}–#{high} cuts into a sentence; a skip starts after a unit that ends a "
                    "sentence and ends right before a unit that starts one."
                )
            else:
                what = protected_skip(self.pi, low, high)
                if what is not None:
                    issues.append(
                        f"Skip #{low}–#{high} removes a payoff the series depends on: {what}. "
                        "Remove a second example or aside instead."
                    )

        if self.limit is not None:
            # What the parts can actually hold: past this the solver would have to end early.
            room = (self.part_count or SERIES_MAX_PARTS) * budget.max
            total = index.speech_seconds(a, b, skips, cleanup)
            if total > room:
                issues.append(
                    f"After your skips the material is {total:.0f}s; the series must stay under {room:.0f}s. "
                    f"Skip about {total - room:.0f}s more of the weakest stretches."
                )

        previous = start - 1
        for number, part in enumerate(draft.parts, start=1):
            end = part.end_unit
            if end <= previous:
                before = f"part {number - 1}'s end #{previous}" if number > 1 else f"the start #{start}"
                issues.append(f"Part {number} ends at #{end}, which is not after {before}.")
                continue
            end = min(end, b)
            first = previous + 1
            inside = [skip for skip in skips if first < skip[0] and skip[1] < end]
            seconds = index.speech_seconds(first, end, inside, cleanup)
            if not budget.min <= seconds <= budget.max:
                window = ends_for_length(index, first, budget.min, budget.max, skips=inside, cleanup=cleanup)
                move = f" Move its end between #{window[0]} and #{window[1]}." if window else ""
                issues.append(
                    f"Part {number} is {seconds:.0f}s of speech; it must be "
                    f"{budget.min:.0f}-{budget.max:.0f}s.{move}"
                )
            if number < count and index.boundary_strength(end) < STRONG_BOUNDARY:
                before, after = strong_ends_around(index, end)
                options = " or ".join(f"#{uid}" for uid in (before, after) if uid is not None)
                issues.append(
                    f"Part {number} ends at #{end}, inside a sentence."
                    + (f" End it at {options}." if options else "")
                )
            if inside:
                # Sponsor reads are removed whatever the parts look like; the
                # 40% limit is on the story a part loses.
                sponsor = [skip for skip in inside if self._forced(skip)]
                whole = index.speech_seconds(first, end, sponsor, cleanup)
                share = (whole - seconds) / whole if whole > 0 else 0.0
                if share > SKIP_MAX_SHARE:
                    issues.append(f"Skips remove {share * 100:.0f}% of part {number}; the limit is 40%.")
            if number == count:
                if not (b - LAST_PART_SLACK_UNITS <= end <= b and (end == b or index.boundary_strength(end) >= STRONG_BOUNDARY)):
                    issues.append(
                        f"The last part must end at the end of the material (#{b}) or on a complete "
                        f"sentence at most {LAST_PART_SLACK_UNITS} units earlier."
                    )
                payoff = self._finale_payoff()
                if payoff is not None and count > 1 and payoff.last_unit < first:
                    issues.append(
                        f"The last part must contain the series payoff (#{payoff.first_unit}–#{payoff.last_unit}: "
                        f"{payoff.what.strip()}); end part {number - 1} before #{payoff.first_unit}."
                    )
            previous = end
        return issues

    def _finale_payoff(self) -> Any:
        """The outline payoff that lands the series: the latest one that a last part can reach.

        That is any payoff in the material's final quarter, or failing that the
        last one, when it is close enough to the end to fit in the last part.
        """
        a, b = self.material.span
        index = self.index
        if b <= a:
            return None
        threshold = index.unit(a).start + 0.75 * (index.unit(b).end - index.unit(a).start)
        threshold = min(threshold, index.unit(b).end - FINALE_NEAR_END_SECONDS)
        payoffs = [
            payoff
            for payoff in self.pi.outline.outline.payoffs
            if a <= payoff.last_unit <= b and index.unit(min(max(payoff.last_unit, 1), len(index))).end >= threshold
        ]
        return max(payoffs, key=lambda payoff: payoff.last_unit, default=None)

    def _forced(self, skip: tuple[int, int]) -> bool:
        return any(skip[0] <= low and high <= skip[1] for low, high in self.material.forced_skips)

    @staticmethod
    def _edges(draft: SeriesDraft, start: int) -> set[int]:
        edges = {start}
        previous = start - 1
        for part in draft.parts:
            edges.update({previous + 1, part.end_unit})
            previous = part.end_unit
        return edges


def _listing_without(index: UnitIndex, span: tuple[int, int], skips: Sequence[tuple[int, int]]) -> str:
    ids = [uid for first, last in kept_runs(span[0], span[1], skips) for uid in range(first, last + 1)]
    return index.listing(ids)


def clock_listing(
    index: UnitIndex, span: tuple[int, int], skips: Sequence[tuple[int, int]], cleanup: Optional[dict] = None
) -> str:
    """The material's listing with a running speech clock on every line a part may end on.

    ``#141 15:25 +4.1s Σ6:12 ...``: Σ is the speech from the start of the
    material to the end of that line, so a part's length is a subtraction,
    never a sum the model has to do. ``◆`` marks a question.
    """
    runs = kept_runs(span[0], span[1], skips)
    sigma: dict[int, float] = {}
    before = 0.0
    for low, high in runs:
        for uid in range(low, high + 1):
            sigma[uid] = before + index.speech_seconds(low, uid, (), cleanup)
        before += index.speech_seconds(low, high, (), cleanup)
    lines = []
    for line in _listing_without(index, span, skips).splitlines():
        match = _LISTING_LINE.match(line)
        if match:
            uid = int(match.group(1))
            if uid in sigma and index.boundary_strength(uid) >= STRONG_BOUNDARY:
                line = line[: match.end()] + f" Σ{format_clock(sigma[uid])}" + line[match.end() :]
            if index.unit(uid).ends_question:
                line += " ◆"
        lines.append(line)
    return "\n".join(lines)


async def _draft(
    pi: PlanInput,
    material: _Material,
    budget: Budget,
    part_count: Optional[int],
    max_auto: int,
    limit: Optional[float] = None,
) -> tuple[Optional[SeriesDraft], _DraftRules]:
    index = pi.index
    rules = _DraftRules(pi, material, budget, part_count, max_auto, limit)
    system = series_draft_system(
        pi.language_name, budget, series_parts_clause(part_count, max_auto), best_of=bool(material.left_out)
    )
    a, b = material.span
    speech = index.speech_seconds(a, b, material.forced_skips, pi.cleanup)
    lines = [
        outline_block(pi.outline.outline, index),
        f"The material is units #{a}–#{b} ({format_clock(speech)} of speech).",
    ]
    if limit is not None and speech > limit:
        lines.append(
            f"The material is {speech:.0f}s; the series must stay under {limit:.0f}s. Skip about "
            f"{speech - limit:.0f}s of the weakest stretches (second examples, asides, audience banter, "
            "restatements), whole sentences only. A few large skips beat many small ones."
        )
    if material.forced_skips:
        lines.append(
            "Forced skips (already removed, do not use): "
            + ", ".join(f"#{first}–#{last}" for first, last in material.forced_skips)
        )
    lines.append(transcript_block(clock_listing(index, material.span, material.forced_skips, pi.cleanup)))
    prompt = with_instructions("\n\n".join(lines), pi.instructions)
    agent = build_agent(SeriesDraft, system, structural=True)
    for attempt in (1, 2):
        try:
            draft, residual = await run_with_repair(agent, prompt, rules, stats=pi.stats, stage="series_draft")
            if residual:
                logger.info("Series draft keeps %d issue(s) for the partitioner", len(residual))
            return draft, rules
        except Exception as error:
            if is_cancellation(error):
                raise
            logger.warning("Series draft failed (%s), attempt %d", type(error).__name__, attempt)
    return None, rules


# -- partition and critique ---------------------------------------------------------------


def _partition_span(
    index: UnitIndex, material: _Material, draft: Optional[SeriesDraft], cleanup: dict
) -> tuple[int, int]:
    a, b = material.span
    if draft is None or not draft.parts:
        return a, trim_backchannel_tail(index, a, b)
    start = min(max(draft.start_unit, a), b)
    start = nearest_sentence_start(index, start, SCOPE_SNAP_UNITS) or start
    start = min(max(start, a), b)
    if _lead_seconds(index, material, start, cleanup) > lead_trim_limit(index, material, cleanup):
        # A start past the warm-up would drop story the parts depend on.
        start = latest_allowed_start(index, material, start, cleanup)
    for low, high in material.forced_skips:
        if low <= start <= high:
            start = high + 1  # a start inside a left-out stretch would bring all of it back
    end = draft.parts[-1].end_unit
    if b - LAST_PART_SLACK_UNITS <= end < b and index.boundary_strength(end) >= STRONG_BOUNDARY:
        b = end
    return start, trim_backchannel_tail(index, start, b)


def _part_listing(
    index: UnitIndex, first: int, last: int, skips: Sequence[tuple[int, int]], reasons: dict[tuple[int, int], str]
) -> str:
    blocks: list[str] = []
    runs = kept_runs(first, last, skips)
    for position, (low, high) in enumerate(runs):
        blocks.append(index.listing(range(low, high + 1)))
        if position + 1 < len(runs):
            gap = next((skip for skip in skips if high < skip[0] and skip[1] < runs[position + 1][0]), None)
            reason = reasons.get(gap, "") if gap else ""
            blocks.append(f"··· skipped: {reason or 'cut'} ···")
    return "\n".join(blocks)


def _edge_text(index: UnitIndex, uids: Sequence[int]) -> str:
    return " … ".join(f"#{uid} {index.unit(uid).text}" for uid in uids)


def _loop_candidates(index: UnitIndex, end: int, spine: Sequence[int]) -> list[int]:
    """Lines near a part's end that really ask or point ahead, on a cut the part could use."""
    around = index.unit(end).end
    found = [
        uid
        for uid in spine
        if uid != end
        and abs(index.unit(uid).end - around) <= JOIN_CANDIDATE_SECONDS
        and index.boundary_strength(uid) >= STRONG_BOUNDARY
        and (index.unit(uid).ends_question or forward_hook(index.unit(uid).text, index.language))
    ]
    return sorted(found, key=lambda uid: abs(index.unit(uid).end - around))[:JOIN_CANDIDATE_MAX]


def _critique_prompt(
    pi: PlanInput, result: PartitionResult, copies: list[_Copy], series_title: str, reasons: dict[tuple[int, int], str]
) -> str:
    """Every part as heard, then each join (end ‖ next start), the series' last lines and what it leaves out.

    The parts carry only their titles: copy written before the cuts were
    final ("ends on: ...") would tell the critique a loop is open when the
    cut doesn't contain one.
    """
    index, outline = pi.index, pi.outline.outline
    sections = [f'Series: "{series_title}", {len(result.parts)} parts.']
    if outline.central_question:
        sections.append(f"CENTRAL QUESTION: {outline.central_question}")
    spine = [
        uid
        for (first, last), skips in zip(result.parts, result.skips_by_part)
        for low, high in kept_runs(first, last, skips)
        for uid in range(low, high + 1)
    ]
    for number, ((first, last), skips, seconds, copy) in enumerate(
        zip(result.parts, result.skips_by_part, result.seconds, copies), start=1
    ):
        header = f'=== PART {number} ({seconds:.0f}s): "{copy.title}" ==='
        sections.append(header + "\n" + _part_listing(index, first, last, skips, reasons))
    joins = []
    for number, ((_, end), (start, next_last), part_skips, next_skips) in enumerate(
        zip(result.parts, result.parts[1:], result.skips_by_part, result.skips_by_part[1:]), start=1
    ):
        tail = [uid for low, high in kept_runs(result.parts[number - 1][0], end, part_skips) for uid in range(low, high + 1)][-2:]
        head = [uid for low, high in kept_runs(start, next_last, next_skips) for uid in range(low, high + 1)][:2]
        joins.append(f"JOIN {number}→{number + 1}: {_edge_text(index, tail)} ‖ {_edge_text(index, head)}")
        candidates = _loop_candidates(index, end, spine)
        if candidates:
            joins.append("  CANDIDATES: " + _edge_text(index, candidates))
    if joins:
        sections.append("JOINS\n" + "\n".join(joins))
    first, last = result.parts[-1]
    ending = [uid for low, high in kept_runs(first, last, result.skips_by_part[-1]) for uid in range(low, high + 1)][-3:]
    sections.append(f"SERIES END (last part): {_edge_text(index, ending)}")
    following = list(range(last + 1, min(len(index), last + 3) + 1))
    if following:
        sections.append(f"NEXT LINES AFTER THE SERIES (not played): {_edge_text(index, following)}")
    left_out = [
        chapter.title.strip() or chapter.summary.strip()
        for _, chapter, cs in _chapters(pi)
        if not any(low <= cs[1] and cs[0] <= high for low, high in kept_runs(result.parts[0][0], result.parts[-1][1], result.skips))
        and chapter.kind not in ("intro", "outro", "sponsor")
    ]
    if left_out:
        sections.append("NOT IN THIS SERIES: " + "; ".join(item for item in left_out if item))
    return with_instructions(transcript_block("\n\n".join(sections)), pi.instructions)


@dataclass
class _CritiqueResult:
    constraints: PartitionConstraints
    skips: list[tuple[int, int]]
    ends: list[int]
    titles: dict[int, str]
    applied: int
    span_end: Optional[int] = None  # the last part ends earlier: the series' new last unit
    flat: set[int] = field(default_factory=set)  # parts whose ending leaves nothing open


def _apply_critique(
    pi: PlanInput,
    critique: SeriesCritique,
    result: PartitionResult,
    skips: list[tuple[int, int]],
    forced: list[tuple[int, int]],
    span: tuple[int, int],
) -> _CritiqueResult:
    """Critique fixes and join checks as solver constraints, skips, model ends and new titles."""
    out = _CritiqueResult(PartitionConstraints(), list(skips), [last for _, last in result.parts], {}, 0)
    count = len(result.parts)

    def move(part: int, target: int, reach: int = CRITIQUE_MAX_MOVE_UNITS) -> bool:
        first, last = result.parts[part - 1]
        if target == last or abs(target - last) > reach or not span[0] <= target <= span[1]:
            return False
        if part == count:
            # The series may end earlier on its payoff, never later.
            if not (first < target < last) or pi.index.boundary_strength(target) < STRONG_BOUNDARY:
                return False
            out.span_end = target
            out.ends = [target if end == last else end for end in out.ends]
            return True
        if not span[0] <= target < span[1] or pi.index.boundary_strength(target) < STRONG_BOUNDARY:
            return False
        out.constraints.pinned_ends.add(target)
        out.constraints.forbidden_ends.add(last)
        out.ends = [target if end == last else end for end in out.ends]
        return True

    for join in critique.joins:
        if not 1 <= join.part < count:
            continue
        if join.leaves_open == "none" or not join.next_part_answers or not join.next_opens_cold:
            if join.better_end >= 1 and move(join.part, join.better_end, CRITIQUE_JOIN_MOVE_UNITS):
                out.applied += 1
                continue
            if join.leaves_open == "none":
                out.flat.add(join.part)
    for uid in critique.unkept_promises:
        # Announced and never delivered: the line goes, when that leaves the sentences around it whole.
        inside = next(((f, l) for f, l in result.parts if f < uid < l), None)
        if inside is None or not clean_skip(pi.index, uid, uid) or protected_skip(pi, uid, uid) is not None:
            continue
        out.skips.append((uid, uid))
        out.applied += 1
    for fix in critique.fixes[:CRITIQUE_MAX_FIXES]:
        if fix.action == "none" or not 1 <= fix.part <= count:
            continue
        first, last = result.parts[fix.part - 1]
        if fix.action in ("end_earlier", "end_later"):
            target = fix.unit
            if (fix.action == "end_earlier") != (target < last) or (fix.part == count and fix.action == "end_later"):
                logger.info("Ignoring series fix %s on part %d", fix.action, fix.part)
                continue
            if not move(fix.part, target):
                logger.info("Ignoring series fix %s on part %d", fix.action, fix.part)
                continue
        elif fix.action == "drop_units":
            low, high = sorted((fix.unit, fix.last_unit))
            if (
                not first < low <= high < last
                or not clean_skip(pi.index, low, high)
                or protected_skip(pi, low, high) is not None
            ):
                logger.info("Ignoring series fix drop_units on part %d", fix.part)
                continue
            out.skips.append((low, high))
        elif fix.action == "restore_units":
            low, high = sorted((fix.unit, fix.last_unit))
            before = len(out.skips)
            out.skips = [skip for skip in out.skips if skip in forced or skip[1] < low or high < skip[0]]
            if len(out.skips) == before:
                continue
        elif fix.action == "retitle":
            title = _clean_title(fix.new_title)
            if not title:
                continue
            out.titles[fix.part] = title
        out.applied += 1
    return out


async def _critique(
    pi: PlanInput,
    result: PartitionResult,
    copies: list[_Copy],
    series_title: str,
    reasons: dict[tuple[int, int], str],
) -> Optional[SeriesCritique]:
    agent = build_agent(SeriesCritique, series_critique_system(pi.language_name), structural=True)
    try:
        return (
            await run(agent, _critique_prompt(pi, result, copies, series_title, reasons), stats=pi.stats, stage="series_critique")
        ).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Series critique failed (%s); keeping the solved parts", type(error).__name__)
        return None


# -- copy -----------------------------------------------------------------------------------


def _draft_ranges(draft: SeriesDraft, span: tuple[int, int]) -> list[tuple[int, int]]:
    ranges = []
    previous = span[0] - 1
    for part in draft.parts:
        end = max(part.end_unit, previous + 1)
        ranges.append((previous + 1, end))
        previous = end
    return ranges


def _mapped_copy(draft: SeriesDraft, span: tuple[int, int], parts: list[tuple[int, int]]) -> list[_Copy]:
    """Draft copy for each final part: the draft part it overlaps most."""
    drafted = _draft_ranges(draft, span)
    copies = []
    for first, last in parts:
        overlaps = [max(0, min(last, high) - max(first, low) + 1) for low, high in drafted]
        best = max(range(len(drafted)), key=lambda position: overlaps[position]) if drafted else None
        if best is None or overlaps[best] == 0:
            copies.append(_Copy())
            continue
        part = draft.parts[best]
        copies.append(
            _Copy(title=_clean_title(part.title), best_line=part.best_line if first <= part.best_line <= last else -1)
        )
    return copies


def _part_tail(result: PartitionResult, number: int) -> list[int]:
    """The last two units a part plays."""
    (first, last), skips = result.parts[number - 1], result.skips_by_part[number - 1]
    return [uid for low, high in kept_runs(first, last, skips) for uid in range(low, high + 1)][-2:]


def _last_lines(index: UnitIndex, result: PartitionResult) -> list[str]:
    """For each part, the line it follows ("" for Part 1)."""
    return [""] + [index.unit(_part_tail(result, number)[-1]).text for number in range(1, len(result.parts))]


async def _retitle(pi: PlanInput, result: PartitionResult, copies: list[_Copy], series_title: str) -> Optional[SeriesCopy]:
    """Copy for the parts as they are: the cuts are final, so it can quote and name what really plays."""
    agent = build_agent(SeriesCopy, series_retitle_system(pi.language_name), structural=True)
    sections = [f"{len(result.parts)} parts."]
    for number, ((first, last), skips, seconds) in enumerate(zip(result.parts, result.skips_by_part, result.seconds), start=1):
        best = copies[number - 1].best_line if number <= len(copies) else -1
        header = f"=== PART {number} ({seconds:.0f}s) ==="
        if first <= best <= last:
            header += f"\nBest line: {pi.index.unit(best).text}"
        sections.append(header + "\n" + _part_listing(pi.index, first, last, skips, {}))
    prompt = with_instructions(transcript_block("\n\n".join(sections)), pi.instructions)
    before = _last_lines(pi.index, result)

    def validate(answer: SeriesCopy) -> list[str]:
        titles = {entry.part: _clean_title(entry.title) for entry in answer.parts}
        ordered = [titles.get(number, "") for number in range(1, len(result.parts) + 1)]
        return title_issues(ordered, before, pi.language)

    # This is the only copy a series gets, so one bad answer doesn't sink it.
    for attempt in (1, 2):
        try:
            answer, _ = await run_with_repair(agent, prompt, validate, stats=pi.stats, stage="series_retitle")
            return answer
        except Exception as error:
            if is_cancellation(error):
                raise
            logger.warning("Series retitle failed (%s), attempt %d", type(error).__name__, attempt)
    return None


def _apply_retitle(copy: SeriesCopy, copies: list[_Copy]) -> tuple[list[_Copy], str]:
    """The retitled copy; a field the model left empty keeps the mapped draft's text, except an open question."""
    updated = list(copies)
    for entry in copy.parts:
        if 1 <= entry.part <= len(updated):
            old = updated[entry.part - 1]
            updated[entry.part - 1] = _Copy(
                _clean_title(entry.title) or old.title,
                _clean(entry.delivers) or old.delivers,
                _clean(entry.ends_on),
                _clean(entry.post_caption) or old.post_caption,
                old.best_line,
            )
    return updated, _clean_title(copy.series_title)


def ground_ends_on(pi: PlanInput, result: PartitionResult, copies: list[_Copy], flat_ends: set[int]) -> None:
    """Never ship a cliffhanger the cut doesn't contain: ends_on quotes the part's last two lines, or goes."""
    index = pi.index
    for number, copy in enumerate(copies[:-1], start=1):
        if not copy.ends_on:
            continue
        if result.parts[number - 1][1] in flat_ends:
            copy.ends_on = ""
            continue
        tail = " ".join(index.text(uid, uid) for uid in _part_tail(result, number))
        if title_words(copy.ends_on) and title_words(tail) and heard_share(copy.ends_on, tail) == 0.0:
            copy.ends_on = ""
    if copies:
        copies[-1].ends_on = ""


def open_candidates(index: UnitIndex, material: _Material, start: int, cleanup: dict) -> list[int]:
    """Sentence starts inside the lead limit that could open the series, the draft's choice included."""
    a, b = material.span
    limit = lead_trim_limit(index, material, cleanup)
    skipped = {uid for low, high in material.forced_skips for uid in range(low, high + 1)}
    found: list[int] = []
    for uid in range(a, b + 1):
        if uid in skipped:
            continue
        if _lead_seconds(index, material, uid, cleanup) > limit:
            break
        unit = index.unit(uid)
        if uid == start or (
            (uid == a or unit.starts_sentence)
            and len(unit.text.split()) >= SERIES_OPEN_MIN_WORDS
            and not opens_with_back_reference(unit.text, index.language, strict=True)
            and not unresolved_reference(unit.text, index.language)
        ):
            found.append(uid)
    if len(found) > SERIES_OPEN_MAX_CANDIDATES:
        kept = found[:SERIES_OPEN_MAX_CANDIDATES]
        if start in found and start not in kept:
            kept[-1] = start
        return kept
    return found


async def _series_open(pi: PlanInput, material: _Material, start: int) -> int:
    """The first sentence, chosen on its own: the one that stops a stranger within the lead limit."""
    index = pi.index
    candidates = open_candidates(index, material, start, pi.cleanup)
    if len(candidates) < 2:
        return start
    lines = "\n".join(f"#{uid}: {index.unit(uid).text}" for uid in candidates)
    agent = build_agent(SeriesOpen, series_open_system(pi.language_name), structural=True)
    prompt = with_instructions(f"The current first line is #{start}.\n\nCandidates:\n{lines}", pi.instructions)
    try:
        chosen = (await run(agent, prompt, stats=pi.stats, stage="series_open")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Series opening pick failed (%s); keeping the draft's", type(error).__name__)
        return start
    logger.info("Series opens on #%s (draft #%s), stop-scroll %s", chosen.start_unit, start, chosen.stop_scroll_score)
    return chosen.start_unit if chosen.start_unit in candidates else start


@dataclass(frozen=True)
class _ColdOpen:
    first: int
    last: int
    from_part: int
    seconds: float


def cold_open_candidates(pi: PlanInput, result: PartitionResult, budget: Budget) -> list[_ColdOpen]:
    """Lines a series could flash forward to before Part 1: whole sentences from later parts.

    Never from the last part of a longer series, never a payoff, never one that
    leans on what came before, and never so long that Part 1 leaves its budget.
    """
    index, cleanup = pi.index, pi.cleanup
    payoffs = {
        uid for payoff in pi.outline.outline.payoffs for uid in range(payoff.first_unit, payoff.last_unit + 1)
    }
    room = budget.max - float(result.seconds[0])
    count = len(result.parts)
    later = range(2, count + 1) if count == 2 else range(2, count)
    found: list[_ColdOpen] = []
    for number in later:
        first_unit, last_unit = result.parts[number - 1]
        skipped = {uid for low, high in result.skips_by_part[number - 1] for uid in range(low, high + 1)}
        # A two-part series keeps its second half for the ending.
        stop = (first_unit + last_unit) // 2 if count == 2 else last_unit
        for first in range(first_unit, stop + 1):
            unit = index.unit(first)
            if (
                first in skipped
                or first in payoffs
                or not unit.starts_sentence
                or opens_with_back_reference(unit.text, index.language, strict=True)
                or unresolved_reference(unit.text, index.language)
            ):
                continue
            for last in range(first, min(stop, first + COLD_OPEN_MAX_UNITS - 1) + 1):
                if last in skipped or last in payoffs:
                    break
                seconds = index.speech_seconds(first, last, (), cleanup)
                if seconds > min(COLD_OPEN_MAX_SECONDS, room):
                    break
                if seconds >= COLD_OPEN_MIN_SECONDS and index.boundary_strength(last) >= STRONG_BOUNDARY:
                    found.append(_ColdOpen(first, last, number, seconds))
                    break
    if len(found) <= COLD_OPEN_MAX_CANDIDATES:
        return found
    # Evenly across the series: the prompt can't read them all.
    step = len(found) / COLD_OPEN_MAX_CANDIDATES
    return [found[int(position * step)] for position in range(COLD_OPEN_MAX_CANDIDATES)]


async def _cold_open(pi: PlanInput, result: PartitionResult, budget: Budget, series_title: str) -> Optional[_ColdOpen]:
    """The flash-forward before Part 1, chosen by the model; None leaves Part 1 as it is, and says so."""
    index = pi.index
    candidates = cold_open_candidates(pi, result, budget)
    chosen: Optional[_ColdOpen] = None
    if candidates:
        lines = "\n".join(
            f"{number}. (Part {item.from_part}) {index.text(item.first, item.last)}"
            for number, item in enumerate(candidates, start=1)
        )
        opening = index.text(*result.parts[0])[:400]
        prompt = with_instructions(
            f"Series: {series_title}\n\nPart 1 starts: {opening}\n\nCandidates:\n{lines}", pi.instructions
        )
        agent = build_agent(SeriesColdOpen, series_cold_open_system(pi.language_name), structural=True)
        try:
            picked = (await run(agent, prompt, stats=pi.stats, stage="series_cold_open")).output
        except Exception as error:
            if is_cancellation(error):
                raise
            logger.warning("Series cold open pick failed (%s)", type(error).__name__)
        else:
            if 1 <= picked.candidate <= len(candidates):
                chosen = candidates[picked.candidate - 1]
    if chosen is None:
        pi.note("COLD_OPEN_SKIPPED")
    return chosen


def _start_to_finish(pi: PlanInput, content: tuple[int, int], limit: float) -> _Material:
    """The video in order from its first content, as far as the parts can hold: no chapter is chosen or dropped."""
    index = pi.index
    end = _trim_to_limit(index, content[0], content[1], limit, pi.cleanup)
    if end < content[1]:
        pi.note("STORY_ARC_SELECTED", _clock_range(index, content[0], end))
    return _material(pi, (content[0], end))


# -- entry point ------------------------------------------------------------------------------


async def plan_series(pi: PlanInput) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index, spec = pi.index, pi.ctx.spec
    budget = part_budget(spec.clip_length)
    requested = spec.options.part_count
    await pi.ctx.progress(54, "Mapping the story...")

    content = content_span(pi)
    limit = span_limit(budget, requested)
    fits = index.speech_seconds(*content, (), pi.cleanup) <= limit
    scope: Optional[_Scope] = None
    start_to_finish = pi.setting("coverage") == "start_to_finish"
    natural_endings = pi.setting("endings") == "natural"
    if start_to_finish:
        material = _start_to_finish(pi, content, limit)
    elif fits and has_throughline(pi, content):
        material = _leave_out_padding(pi, _material(pi, content, limit), budget)
    else:
        if fits:
            # A bulletin or a compilation: one story becomes the series, not all of them.
            pi.note("NO_SINGLE_STORY")
        scope = await _scope(pi, budget, limit, content)
        if scope.list_items:
            limit = SERIES_MAX_PARTS * LIST_PART_SECONDS if requested is None else limit
        material = _material_from_scope(pi, scope, budget, limit)
        pi.note("STORY_ARC_SELECTED", _clock_range(index, *material.span))
        if not scope.story_found and all(arc.strength <= 1 for arc in pi.outline.outline.story_arcs):
            pi.note("NO_SINGLE_STORY")
            if index.speech_seconds(*material.span, material.forced_skips, pi.cleanup) < 2 * budget.min:
                raise WorkflowError("no_story", _NO_STORY)
    allowed = (scope.keep + scope.spare) if scope is not None else None
    # A list ends on its last item, wherever the payoffs are.
    listed = scope is not None and bool(scope.list_items)
    if start_to_finish:
        pass  # the creator asked for the video as it runs: the finale is wherever the room ends
    elif not listed:
        material = validate_finale(pi, material, budget, limit, allowed=allowed)
    else:
        material = await _list_skips(pi, material, limit)
        limit = (requested or SERIES_MAX_PARTS) * budget.max  # the draft answers only to what the parts can hold
    _note_left_out(pi, material)
    ends_of_chapters = chapter_ends(pi)

    # Fails fast (source_too_short) before any draft when two parts can't fit.
    partition_series(
        index, material.span, [], budget, None, [], None, pi.cleanup, forced_skips=material.forced_skips,
        jumps=material.left_out,
    )
    story = index.speech_seconds(*material.span, material.forced_skips, pi.cleanup)
    max_auto = max(SERIES_MIN_PARTS, min(SERIES_MAX_PARTS, math.floor(story / budget.min)))
    draft_count = min(requested, max_auto) if requested is not None else None

    await pi.ctx.progress(58, "Planning parts...")
    draft, rules = await _draft(pi, material, budget, draft_count, max_auto, limit)
    if draft is None:
        if story > FALLBACK_MAX_SPAN_SECONDS:
            raise WorkflowError("plan_failed", _PLAN_FAILED)
        logger.warning("Series draft unavailable; partitioning on the transcript alone")
        pi.stats.fallbacks.append("SERIES_DRAFT_FALLBACK")
    else:
        draft.start_unit = await _series_open(pi, material, draft.start_unit)

    span = _partition_span(index, material, draft, pi.cleanup)
    skips = rules.skips(draft) if draft is not None else validate_skips(index, span, material.forced_skips)
    reasons = dict(material.skip_reasons)
    if draft is not None:
        for skip in draft.skips:
            reasons.setdefault(tuple(sorted((skip.first_unit, skip.last_unit))), _clean(skip.reason))
    llm_ends = [part.end_unit for part in draft.parts] if draft is not None else []
    result = partition_series(
        index, span, skips, budget, requested, llm_ends, None, pi.cleanup,
        forced_skips=material.forced_skips, chapter_ends=ends_of_chapters, jumps=material.left_out,
        natural_endings=natural_endings,
    )
    # Unit ids only (never transcript text), so an eval can tell who moved a cut.
    logger.info(
        "Series draft ends %s; solved ends %s", llm_ends, [last for _, last in result.parts]
    )

    series_title = _clean_title(draft.series_title) if draft is not None else ""
    copies = _mapped_copy(draft, span, result.parts) if draft is not None else [_Copy() for _ in result.parts]
    titled_parts: Optional[list[tuple[int, int]]] = None
    if draft is None:
        # Without a draft there is no copy to critique: write it for the solved parts first.
        retitled = await _retitle(pi, result, copies, series_title)
        if retitled is not None:
            copies, new_title = _apply_retitle(retitled, copies)
            series_title = new_title or series_title
            titled_parts = list(result.parts)
    titles: dict[int, str] = {}
    flat_ends: set[int] = set()
    critiqued_parts = len(result.parts)
    await pi.ctx.progress(62, "Checking every cut...")
    critique = await _critique(pi, result, copies, series_title, reasons)
    if critique is not None:
        fixed = _apply_critique(pi, critique, result, result.skips, material.forced_skips, span)
        titles = fixed.titles
        flat_ends = {result.parts[number - 1][1] for number in fixed.flat}
        pi.stats.critique_fixes += fixed.applied
        logger.info(
            "Series critique fixes %s",
            [(fix.part, fix.action, fix.unit, fix.last_unit) for fix in critique.fixes[:CRITIQUE_MAX_FIXES]],
        )
        constraints = fixed.constraints
        revised_span = (span[0], fixed.span_end) if fixed.span_end is not None else span
        revised_skips = list(fixed.skips)
        forced = list(material.forced_skips)
        jumps = list(material.left_out)
        if not critique.finale_lands and fixed.span_end is None and not listed and not start_to_finish:
            # The viewer says the series stops rather than finishes: one more look at where it ends.
            refinaled = validate_finale(pi, _Material(span=(span[0], span[1]), forced_skips=forced, skip_reasons=material.skip_reasons), budget, limit, allowed=allowed, force=True)
            if refinaled.span != revised_span:
                revised_span = refinaled.span
                new_forced = [skip for skip in refinaled.forced_skips if skip not in forced]
                revised_skips += new_forced
                forced = refinaled.forced_skips
                jumps = refinaled.left_out
                reasons.update(refinaled.skip_reasons)
        if constraints.pinned_ends or constraints.forbidden_ends or revised_skips != result.skips or revised_span != span:
            try:
                revised = partition_series(
                    index, revised_span, [skip for skip in revised_skips if skip[1] < revised_span[1]], budget,
                    requested, fixed.ends, constraints, pi.cleanup,
                    forced_skips=[skip for skip in forced if skip[1] < revised_span[1]],
                    chapter_ends=ends_of_chapters,
                    jumps=[skip for skip in jumps if skip[1] < revised_span[1]],
                    natural_endings=natural_endings,
                )
            except WorkflowError as error:
                # The critique only polishes a plan that already works.
                logger.warning("Series critique fixes left no valid parts (%s); keeping the solved parts", error.code)
            else:
                result, span = revised, revised_span
                if draft is not None:
                    copies = _mapped_copy(draft, span, result.parts)
                logger.info("Series ends after the critique %s", [last for _, last in result.parts])

    # The critique's titles hold unless the final copy replaces them; they name
    # parts by number, so only while the count is unchanged.
    if titles and len(copies) == critiqued_parts:
        for number, title in titles.items():
            if number <= len(copies):
                copies[number - 1].title = title
    # The cuts are final: copy written now can only name what really plays.
    if result.parts != titled_parts:
        retitled = await _retitle(pi, result, copies, series_title)
        if retitled is not None:
            copies, new_title = _apply_retitle(retitled, copies)
            series_title = new_title or series_title
    ground_ends_on(pi, result, copies, flat_ends)
    weak = [last for _, last in result.parts[:-1] if index.boundary_strength(last) < STRONG_BOUNDARY]
    if weak:
        logger.warning("Series ends on %d weak cut(s): %s", len(weak), weak)
        pi.stats.fallbacks.append("SERIES_WEAK_CUT")

    for note in result.notes:
        pi.note(note["code"], note.get("params"))
    if any(note["code"] == "STORY_ARC_SELECTED" for note in pi.notes):
        # The stretch the parts actually cover: the solver may end it early.
        _set_note_params(pi, "STORY_ARC_SELECTED", _clock_range(index, result.parts[0][0], result.parts[-1][1]))
    if index.coarse:
        pi.note("COARSE_TRANSCRIPT")
    premise = (_clean(draft.premise) if draft is not None else "") or (scope.premise if scope is not None else "")
    cold_open = await _cold_open(pi, result, budget, series_title) if pi.setting("cold_open") else None
    return _segments(pi, result, copies, series_title, budget, premise, cold_open)


def _segments(
    pi: PlanInput,
    result: PartitionResult,
    copies: list[_Copy],
    series_title: str,
    budget: Budget,
    premise: str = "",
    cold_open: Optional[_ColdOpen] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = pi.index
    count = len(result.parts)
    segments: list[dict[str, Any]] = []
    plan_parts: list[dict[str, Any]] = []
    for number, ((first, last), skips, seconds, copy) in enumerate(
        zip(result.parts, result.skips_by_part, result.seconds, copies), start=1
    ):
        ranges = index.ranges_for(first, last, skips)
        prefix = part_label(pi.language, number) if pi.setting("part_labels") else ""
        hook_title = compose_title(copy.title, prefix=prefix)
        fades = [PART_JUNCTION_FADE] * (len(ranges) - 1)
        text = index.text(first, last, skips)
        extra_meta: dict[str, Any] = {}
        own_start = ranges[0]
        if number == 1 and cold_open is not None:
            flash = index.ranges_for(cold_open.first, cold_open.last)
            # The jump into the story is a hard cut; the line plays again when the series gets there.
            fades = [PART_JUNCTION_FADE] * (len(flash) - 1) + [0.0] + fades
            ranges = list(flash) + list(ranges)
            text = f"{index.text(cold_open.first, cold_open.last)} … {text}"
            extra_meta["cold_open"] = {
                "first_unit": cold_open.first,
                "last_unit": cold_open.last,
                "range_count": len(flash),
                "from_part": cold_open.from_part,
            }
        segments.append(
            planned_segment(
                ranges=ranges,
                junction_fades=fades,
                max_seconds=budget.render_cap,
                required=True,
                text=text,
                hook_title=hook_title,
                reasoning=copy.delivers,
                workflow_role="part",
                part_number=number,
                meta={
                    "series_title": series_title,
                    "part_count": count,
                    "title": copy.title,
                    "delivers": copy.delivers,
                    "ends_on": copy.ends_on,
                    "post_caption": copy.post_caption,
                    "first_unit": first,
                    "last_unit": last,
                    **extra_meta,
                },
            )
        )
        if extra_meta:
            # The part still starts where its story starts, in labels and file names.
            segments[-1]["start_time"] = seconds_to_mmss(own_start[0])
            segments[-1]["end_time"] = seconds_to_mmss(own_start[1])
        plan_parts.append(
            {
                "part_number": number,
                "title": copy.title,
                "delivers": copy.delivers,
                "ends_on": copy.ends_on,
                "post_caption": copy.post_caption,
                "first_unit": first,
                "last_unit": last,
                "ranges": rounded_ranges(ranges),
                "seconds": round(seconds + (cold_open.seconds if number == 1 and cold_open else 0.0), 1),
                **extra_meta,
            }
        )
    plan = base_plan(pi, title=series_title)
    if premise:
        plan["summary"] = premise  # what this series is about, not what the whole video is
    plan["parts"] = plan_parts
    # Every unit the series leaves out between its first and last part (I2 audits use these).
    plan["skips"] = [[first, last] for first, last in result.skips]
    return segments, plan
