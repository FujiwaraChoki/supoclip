"""Trailer planner: one short clip that sells the question, never the answer.

The trailer cuts on clause units (a comma or a breath) so beats can stop just
before the answer. A deterministic spoiler set (outline payoffs, the endings
of story arcs, the last stretch of a story or vlog) is off limits before the
model sees anything; the model adds the payoffs it finds; ``fit_trailer``
fits the beats to the window; a spoiler judge reads the result as a viewer.
When the model's trailer can't be made safe, a deterministic trailer is
built from the outline, and when even that can't avoid the ending, the plan
fails loudly (``trailer_unbuildable``).
"""

from __future__ import annotations

import logging
import re
from typing import Any, Iterable, Optional, Sequence

from .. import ai
from .assembly import (
    TRAILER_BEAT_END_STRENGTH,
    TRAILER_BEAT_MAX_SECONDS,
    TRAILER_BEAT_MIN_SECONDS,
    TRAILER_COLD_OPEN_MAX_SECONDS,
    TRAILER_JUNCTION_FADE,
    TrailerBeatIn,
    TrailerCut,
    fit_trailer,
)
from .boundaries import STRONG_BOUNDARY
from .durations import Budget, trailer_budget
from .errors import WorkflowError
from .llm import build_agent, is_cancellation, run, run_with_repair
from .outline import chapter_ranges
from .planning import PlanInput, base_plan, planned_segment, rounded_ranges
from .prompts import trailer_draft_system, trailer_judge_system, transcript_block, with_instructions
from .schemas import SpoilerVerdict, TrailerDraft
from .titles import compose_title
from ..utils.async_helpers import run_in_thread
from .units import UnitIndex, build_units

logger = logging.getLogger(__name__)

ARC_ENDING_SHARE = 0.15
STORY_ENDING_SHARE = 0.08
_STORY_FORMATS = {"story", "vlog"}
MIN_BEATS = 4
MAX_BEATS = 9
FALLBACK_ESCALATION_MAX_SECONDS = 7.0
FALLBACK_BUTTON_MAX_SECONDS = 8.0
FALLBACK_MAX_ESCALATIONS = 3
_OFF_LIMITS_LINE = re.compile(r"^(#(\d+) \S+ \+\S+s )")

_UNBUILDABLE = "We couldn't build a trailer that doesn't give away the ending. Try a Recap instead."
_PLAN_FAILED = "The AI editor couldn't finish this plan. Please try again."


# -- units and spoilers ---------------------------------------------------------------------


async def clause_units(pi: PlanInput) -> UnitIndex:
    """Clause units on the same words; coarse transcripts keep their lines."""
    if pi.index.coarse:
        return pi.index
    return await run_in_thread(build_units, pi.ctx.transcript_data, pi.ctx.transcript_text, granularity="clause")


def to_clauses(sentences: UnitIndex, clauses: UnitIndex, first: int, last: int) -> tuple[int, int]:
    """The clause ids covering sentence units ``first..last`` (same words, finer cuts)."""
    if clauses is sentences:
        return first, last
    start = sentences.unit(first).start
    end = sentences.unit(last).end
    low = clauses.unit_at(start)
    high = clauses.unit_at(max(start, end - 0.001))
    return min(low, high), max(low, high)


def spoiler_sentences(pi: PlanInput) -> set[int]:
    """Sentence ids a trailer must never use, each widened by one sentence."""
    index, outline = pi.index, pi.outline.outline
    count = len(index)
    spans: list[tuple[int, int]] = [(payoff.first_unit, payoff.last_unit) for payoff in outline.payoffs]
    for arc in outline.story_arcs:
        start, end = index.unit(arc.first_unit).start, index.unit(arc.last_unit).end
        spans.append((index.unit_at(start + (1 - ARC_ENDING_SHARE) * (end - start)), arc.last_unit))
    if outline.format in _STORY_FORMATS:
        start, end = index.unit(1).start, index.unit(count).end
        spans.append((index.unit_at(end - STORY_ENDING_SHARE * (end - start)), count))
    blocked: set[int] = set()
    for first, last in spans:
        low, high = sorted((index.clamp_id(first), index.clamp_id(last)))
        blocked.update(range(max(1, low - 1), min(count, high + 1) + 1))
    return blocked


def _to_clause_ids(pi: PlanInput, clauses: UnitIndex, sentence_ids: Iterable[int]) -> set[int]:
    ids: set[int] = set()
    for uid in sentence_ids:
        low, high = to_clauses(pi.index, clauses, uid, uid)
        ids.update(range(low, high + 1))
    return ids


def _shown_clauses(pi: PlanInput, clauses: UnitIndex, spoilers: set[int]) -> Optional[list[int]]:
    """Long mode: the outline's points, arcs and strongest chapters, minus spoilers."""
    if pi.outline.mode == "full":
        return None
    index, outline = pi.index, pi.outline.outline
    sentences: set[int] = set()
    for point in outline.main_points:
        sentences.update(range(point.first_unit, point.last_unit + 1))
    for arc in outline.story_arcs:
        sentences.update(range(arc.first_unit, arc.last_unit + 1))
    for chapter, (first, last) in zip(outline.chapters, chapter_ranges(outline, index)):
        if chapter.importance >= 4:
            sentences.update(range(first, last + 1))
    shown = _to_clause_ids(pi, clauses, sentences) - spoilers
    return sorted(shown) if shown else None


def mark_off_limits(listing: str, off_limits: set[int]) -> str:
    lines = []
    for line in listing.splitlines():
        match = _OFF_LIMITS_LINE.match(line)
        if match and int(match.group(2)) in off_limits:
            line = match.group(1) + "[OFF LIMITS] " + line[match.end() :]
        lines.append(line)
    return "\n".join(lines)


# -- draft --------------------------------------------------------------------------------------


class _DraftRules:
    def __init__(self, clauses: UnitIndex, spoilers: set[int], shown: Optional[Sequence[int]], budget: Budget, cleanup: dict):
        self.clauses = clauses
        self.spoilers = spoilers
        self.shown = set(shown) if shown is not None else None
        self.budget = budget
        self.cleanup = cleanup

    def _valid(self, uid: int) -> bool:
        return 1 <= uid <= len(self.clauses) and (self.shown is None or uid in self.shown)

    def blocked(self, draft: TrailerDraft) -> set[int]:
        blocked = set(self.spoilers)
        for payoff in draft.payoffs:
            low, high = sorted((payoff.first_unit, payoff.last_unit))
            blocked.update(range(max(1, low), min(len(self.clauses), high) + 1))
        return blocked

    def __call__(self, draft: TrailerDraft) -> list[str]:
        issues: list[str] = []
        blocked = self.blocked(draft)
        beats = draft.beats
        if not MIN_BEATS <= len(beats) <= MAX_BEATS:
            issues.append(f"You made {len(beats)} beats; make {MIN_BEATS}-{MAX_BEATS}.")
        spans: list[tuple[int, int, int]] = []
        total = 0.0
        for number, beat in enumerate(beats, start=1):
            bad = [uid for uid in (beat.first_unit, beat.last_unit) if not self._valid(uid)]
            if bad:
                issues.append(f"Beat {number} uses #{bad[0]}, which is not in the transcript you were given.")
                continue
            low, high = sorted((beat.first_unit, beat.last_unit))
            if any(uid in blocked for uid in range(low, high + 1)):
                issues.append(f"Beat {number} (#{low}–#{high}) uses a payoff or an [OFF LIMITS] unit.")
            seconds = self.clauses.speech_seconds(low, high, (), self.cleanup)
            total += seconds
            limit = TRAILER_COLD_OPEN_MAX_SECONDS if number == 1 else TRAILER_BEAT_MAX_SECONDS
            if not TRAILER_BEAT_MIN_SECONDS <= seconds <= limit:
                issues.append(f"Beat {number} is {seconds:.1f}s; it must be {TRAILER_BEAT_MIN_SECONDS:g}-{limit:g}s.")
            spans.append((number, low, high))
        for position, (number, low, high) in enumerate(spans):
            for other, other_low, other_high in spans[position + 1 :]:
                if low <= other_high and other_low <= high:
                    issues.append(f"Beats {number} and {other} overlap.")
        if beats and beats[0].role != "cold_open":
            issues.append("The first beat must be the cold_open.")
        if beats and beats[-1].role != "button":
            issues.append("The last beat must be the button.")
        total -= TRAILER_JUNCTION_FADE * max(0, len(spans) - 1)
        if spans and not self.budget.min <= total <= self.budget.max:
            issues.append(
                f"The trailer is {total:.0f}s; it must be {self.budget.min:.0f}-{self.budget.max:.0f}s."
            )
        return issues


def _beats_in(draft: TrailerDraft, excluded: Sequence[int] = ()) -> list[TrailerBeatIn]:
    return [
        TrailerBeatIn(role=beat.role, first=beat.first_unit, last=beat.last_unit, why=" ".join(beat.why.split()))
        for position, beat in enumerate(draft.beats)
        if position not in excluded
    ]


async def _draft(
    pi: PlanInput, clauses: UnitIndex, spoilers: set[int], budget: Budget
) -> tuple[Optional[TrailerDraft], _DraftRules]:
    shown = _shown_clauses(pi, clauses, spoilers)
    rules = _DraftRules(clauses, spoilers, shown, budget, pi.cleanup)
    listing = clauses.listing(shown)
    if shown is None:
        listing = mark_off_limits(listing, spoilers)
    system = trailer_draft_system(pi.language_name, budget, pi.outline.outline.central_question)
    prompt = with_instructions(transcript_block(listing), pi.instructions)
    agent = build_agent(TrailerDraft, system, structural=True)
    try:
        draft, residual = await run_with_repair(agent, prompt, rules, stats=pi.stats, stage="trailer_draft")
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Trailer draft failed (%s); building the trailer from the outline", type(error).__name__)
        return None, rules
    if residual:
        logger.info("Trailer draft keeps %d issue(s) for the fitter", len(residual))
    return draft, rules


# -- spoiler judge ---------------------------------------------------------------------------------


def _reveals(pi: PlanInput, draft: Optional[TrailerDraft]) -> list[str]:
    reveals = [payoff.what.strip() for payoff in pi.outline.outline.payoffs if payoff.what.strip()]
    if draft is not None:
        reveals += [payoff.what.strip() for payoff in draft.payoffs if payoff.what.strip()]
    seen: set[str] = set()
    unique = []
    for reveal in reveals:
        if reveal.casefold() not in seen:
            seen.add(reveal.casefold())
            unique.append(reveal)
    return unique


def judge_prompt(pi: PlanInput, clauses: UnitIndex, cut: TrailerCut, reveals: Sequence[str]) -> str:
    sections = []
    if reveals:
        sections.append("What the full video reveals:\n" + "\n".join(f"- {reveal}" for reveal in reveals))
    if pi.outline.outline.central_question:
        sections.append(f"The video's central question: {pi.outline.outline.central_question}")
    beats = "\n".join(
        f"Beat {number} ({beat.role.replace('_', ' ')}): {clauses.text(beat.first, beat.last)}"
        for number, beat in enumerate(cut.beats, start=1)
    )
    sections.append(f"<clip>\n{beats}\n</clip>")
    return "\n\n".join(sections)


async def _judge(pi: PlanInput, clauses: UnitIndex, cut: TrailerCut, reveals: Sequence[str]) -> Optional[SpoilerVerdict]:
    agent = build_agent(SpoilerVerdict, trailer_judge_system(pi.language_name), structural=True)
    try:
        return (await run(agent, judge_prompt(pi, clauses, cut, reveals), stats=pi.stats, stage="trailer_judge")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Spoiler judge failed (%s); keeping the fitted trailer", type(error).__name__)
        return None


def _beat_source(cut: TrailerCut, position: int, draft: TrailerDraft) -> Optional[int]:
    """Index into the draft beats of the cut's beat ``position`` (overlap by units)."""
    beat = cut.beats[position]
    for index, drafted in enumerate(draft.beats):
        low, high = sorted((drafted.first_unit, drafted.last_unit))
        if low <= beat.last and beat.first <= high:
            return index
    return None


# -- deterministic fallback ---------------------------------------------------------------------------


def _head(clauses: UnitIndex, first: int, last: int, max_seconds: float, cleanup: dict) -> Optional[tuple[int, int]]:
    """``first..end`` within ``max_seconds``, ending on the strongest cut available."""
    best: Optional[tuple[int, int]] = None
    best_tier = 0
    for end in range(first, last + 1):
        seconds = clauses.speech_seconds(first, end, (), cleanup)
        if seconds > max_seconds:
            break
        strength = clauses.boundary_strength(end)
        tier = 2 if strength >= STRONG_BOUNDARY else 1 if strength >= TRAILER_BEAT_END_STRENGTH else 0
        # The strongest cut wins; among equals, the longest beat.
        if seconds >= TRAILER_BEAT_MIN_SECONDS and tier and tier >= best_tier:
            best, best_tier = (first, end), tier
    return best


def fallback_trailer(pi: PlanInput, clauses: UnitIndex, spoilers: set[int], budget: Budget) -> TrailerCut:
    """Cold open, escalations and a hanging question built from the outline alone."""
    outline = pi.outline.outline
    cores = []
    for point in sorted(outline.main_points, key=lambda item: -item.importance):
        low, high = to_clauses(pi.index, clauses, point.core_first, point.core_last)
        if not any(uid in spoilers for uid in range(low, high + 1)):
            cores.append((low, high))
    beats: list[TrailerBeatIn] = []
    if cores:
        head = _head(clauses, cores[0][0], cores[0][1], TRAILER_COLD_OPEN_MAX_SECONDS, pi.cleanup)
        if head:
            beats.append(TrailerBeatIn("cold_open", *head, why="strongest point"))
    escalations = sorted(cores[1 : 1 + FALLBACK_MAX_ESCALATIONS], key=lambda span: span[0])
    for low, high in escalations:
        head = _head(clauses, low, high, FALLBACK_ESCALATION_MAX_SECONDS, pi.cleanup)
        if head:
            beats.append(TrailerBeatIn("escalation", *head, why="main point"))
    first_payoff = min(
        (to_clauses(pi.index, clauses, payoff.first_unit, payoff.last_unit)[0] for payoff in outline.payoffs),
        default=len(clauses) + 1,
    )
    taken = {uid for beat in beats for uid in range(beat.first, beat.last + 1)}
    for uid in range(first_payoff - 1, 0, -1):
        unit = clauses.unit(uid)
        if not unit.ends_question or uid in spoilers or uid in taken:
            continue
        first = uid
        while (
            first > 1
            and clauses.speech_seconds(first, uid, (), pi.cleanup) < TRAILER_BEAT_MIN_SECONDS
            and first - 1 not in spoilers
            and first - 1 not in taken
        ):
            first -= 1
        if clauses.speech_seconds(first, uid, (), pi.cleanup) <= FALLBACK_BUTTON_MAX_SECONDS:
            beats.append(TrailerBeatIn("button", first, uid, why="open question"))
            break
    if len(beats) < 3:
        raise WorkflowError("trailer_unbuildable", _UNBUILDABLE)
    cut = fit_trailer(clauses, beats, spoilers, budget, pi.cleanup)
    if not cut.usable:
        raise WorkflowError("trailer_unbuildable", _UNBUILDABLE)
    return cut


# -- entry point --------------------------------------------------------------------------------------


async def plan_trailer(pi: PlanInput) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    spec = pi.ctx.spec
    budget = trailer_budget(spec.clip_length)
    await pi.ctx.progress(54, "Mapping the story...")
    clauses = await clause_units(pi)
    spoilers = _to_clause_ids(pi, clauses, spoiler_sentences(pi))

    await pi.ctx.progress(58, "Writing the trailer...")
    draft, rules = await _draft(pi, clauses, spoilers, budget)
    cut: Optional[TrailerCut] = None
    blocked = set(spoilers)
    if draft is not None:
        blocked = rules.blocked(draft)
        cut = fit_trailer(clauses, _beats_in(draft), blocked, budget, pi.cleanup)
        if not cut.usable:
            logger.info("Drafted trailer doesn't fit (%d beats, %.1fs)", len(cut.beats), cut.seconds)
            cut = None

    title = ai.sanitize_hook_title(draft.title) if draft is not None else None
    if cut is not None:
        await pi.ctx.progress(62, "Checking for spoilers...")
        cut, blocked = await _check_spoilers(pi, clauses, cut, draft, blocked, budget)
    if cut is None:
        logger.info("Building the trailer from the outline")
        cut = fallback_trailer(pi, clauses, blocked, budget)
    # The central question is the curiosity the trailer sells: the title when the draft has none.
    title = title or ai.sanitize_hook_title(pi.outline.outline.central_question)

    if pi.index.coarse:
        pi.note("COARSE_TRANSCRIPT")
    return _segments(pi, clauses, cut, title or "", budget)


async def _check_spoilers(
    pi: PlanInput,
    clauses: UnitIndex,
    cut: TrailerCut,
    draft: TrailerDraft,
    blocked: set[int],
    budget: Budget,
) -> tuple[Optional[TrailerCut], set[int]]:
    """The judged cut, re-fitted once after a leak or a resolved button; None when it stays unsafe.

    Also returns the blocked units, grown by any beat the judge caught
    leaking, so the outline fallback never reuses a line that gives the
    ending away.
    """
    reveals = _reveals(pi, draft)
    verdict = await _judge(pi, clauses, cut, reveals)
    if verdict is None or (not verdict.reveals_payoff and verdict.button_unresolved):
        return cut, blocked
    excluded: list[int] = []
    if verdict.reveals_payoff:
        position = verdict.offending_beat - 1
        if 0 <= position < len(cut.beats):
            offending = cut.beats[position]
            blocked = blocked | set(range(offending.first, offending.last + 1))
        source = _beat_source(cut, position, draft) if 0 <= position < len(cut.beats) else None
        if source is None:
            return None, blocked
        excluded.append(source)
    beats = _beats_in(draft, excluded)
    if not verdict.button_unresolved:
        # The button resolves: hand the role to a beat that ends on a question.
        button = cut.beats[-1]
        beats = [
            TrailerBeatIn("escalation", beat.first, beat.last, beat.why)
            if beat.role == "button" or (beat.first <= button.last and button.first <= beat.last)
            else beat
            for beat in beats
        ]
    refit = fit_trailer(clauses, beats, blocked, budget, pi.cleanup)
    if not refit.usable:
        return None, blocked
    if not verdict.button_unresolved and not clauses.unit(refit.beats[-1].last).ends_question:
        return None, blocked
    pi.stats.critique_fixes += 1
    second = await _judge(pi, clauses, refit, reveals)
    if second is None:
        return refit, blocked
    if second.reveals_payoff:
        if 0 <= second.offending_beat - 1 < len(refit.beats):
            leaked = refit.beats[second.offending_beat - 1]
            blocked = blocked | set(range(leaked.first, leaked.last + 1))
        return None, blocked
    if not second.button_unresolved and not clauses.unit(refit.beats[-1].last).ends_question:
        return None, blocked  # the ending still resolves and no question can take over
    return refit, blocked


def _segments(
    pi: PlanInput, clauses: UnitIndex, cut: TrailerCut, title: str, budget: Budget
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranges = [clauses.range_seconds(beat.first, beat.last) for beat in cut.beats]
    segment = planned_segment(
        ranges=ranges,
        junction_fades=[TRAILER_JUNCTION_FADE] * (len(ranges) - 1),
        max_seconds=budget.render_cap,
        required=True,
        text=" … ".join(clauses.text(beat.first, beat.last) for beat in cut.beats),
        hook_title=compose_title(title),
        reasoning=pi.outline.outline.central_question,
        workflow_role="trailer",
        meta={
            "beats": [
                {"role": beat.role, "range_index": position, "output_start": None}
                for position, beat in enumerate(cut.beats)
            ]
        },
    )
    plan = base_plan(pi, title=title)
    plan["beats"] = [
        {"role": beat.role, "ranges": rounded_ranges([span]), "output_start": None, "why": beat.why}
        for beat, span in zip(cut.beats, ranges)
    ]
    plan["seconds"] = cut.seconds
    return [segment], plan
