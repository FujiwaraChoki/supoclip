"""Recap planner: one clip that covers the video's main points, in order.

Draft (opener, points with full and core spans, closer) → deterministic
assembly to the length window (``assembly.fit_recap``) → one coherence
critique read by someone who never saw the video → fixes applied and the
window re-checked. The recap is exactly one output, always inside its window.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Sequence

from .. import ai
from .assembly import (
    RECAP_JUNCTION_FADE,
    RECAP_OPENER_MAX_SECONDS,
    Beat,
    RecapCut,
    RecapDraftIn,
    RecapFixIn,
    RecapPointIn,
    apply_recap_fixes,
    fit_recap,
)
from .boundaries import STRONG_BOUNDARY
from .durations import Budget, recap_budget
from .errors import WorkflowError
from .llm import build_agent, is_cancellation, run, run_with_repair
from .outline import chapter_ranges
from .planning import PlanInput, base_plan, planned_segment, rounded_ranges
from .prompts import (
    outline_block,
    recap_critique_system,
    recap_draft_system,
    transcript_block,
    with_instructions,
)
from .schemas import RecapCritique, RecapDraft
from .titles import compose_title
from .units import UnitIndex

logger = logging.getLogger(__name__)

POINT_MIN_SECONDS = 6.0
POINT_MAX_SECONDS = 25.0
CORE_MIN_SECONDS = 4.0
# Sources shorter than this may recap in two points.
SHORT_SOURCE_SECONDS = 480.0
LISTING_CONTEXT_UNITS = 2
CHAPTER_EDGE_UNITS = 3
CRITIQUE_MAX_FIXES = 5
LABEL_MAX_WORDS = 6
LABEL_MAX_CHARS = 48
# A range edge inside a sentence moves to the sentence's edge at most this far.
EDGE_SNAP_UNITS = 2

_PLAN_FAILED = "The AI editor couldn't finish this plan. Please try again."


def _shown_units(pi: PlanInput) -> Optional[list[int]]:
    """Units the draft sees: all of them in full mode; in long mode the outline's best material."""
    if pi.outline.mode == "full":
        return None
    index, outline = pi.index, pi.outline.outline
    shown: set[int] = set()

    def add(first: int, last: int) -> None:
        shown.update(range(max(1, first), min(len(index), last) + 1))

    for point in outline.main_points:
        add(point.first_unit - LISTING_CONTEXT_UNITS, point.last_unit + LISTING_CONTEXT_UNITS)
    for chapter, (first, last) in zip(outline.chapters, chapter_ranges(outline, index)):
        if chapter.importance >= 4:
            add(first, min(last, first + CHAPTER_EDGE_UNITS - 1))
            add(max(first, last - CHAPTER_EDGE_UNITS + 1), last)
    for payoff in outline.payoffs:
        add(payoff.first_unit, payoff.last_unit)
    return sorted(shown) if shown else None


class _DraftRules:
    def __init__(self, pi: PlanInput, shown: Optional[Sequence[int]]):
        self.index = pi.index
        self.cleanup = pi.cleanup
        self.shown = set(shown) if shown is not None else None
        self.min_points = 2 if pi.ctx.source_seconds < SHORT_SOURCE_SECONDS else 3

    def _valid(self, uid: int) -> bool:
        if not 1 <= uid <= len(self.index):
            return False
        return self.shown is None or uid in self.shown

    def _seconds(self, first: int, last: int) -> float:
        return self.index.speech_seconds(first, last, (), self.cleanup)

    def __call__(self, draft: RecapDraft) -> list[str]:
        issues: list[str] = []
        points = draft.points
        valid_spans: list[tuple[int, tuple[int, int], tuple[int, int]]] = []
        for number, point in enumerate(points, start=1):
            ids = (point.first_unit, point.last_unit, point.core_first, point.core_last)
            bad = [uid for uid in ids if not self._valid(uid)]
            if bad:
                issues.append(f"Point {number} uses #{bad[0]}, which is not in the transcript you were given.")
                continue
            if point.first_unit > point.last_unit or point.core_first > point.core_last:
                issues.append(f"Point {number} has a reversed range; write first..last in video order.")
                continue
            full, core = (point.first_unit, point.last_unit), (point.core_first, point.core_last)
            if not (full[0] <= core[0] and core[1] <= full[1]):
                issues.append(f"Point {number}: the core #{core[0]}–#{core[1]} must lie inside #{full[0]}–#{full[1]}.")
            seconds = self._seconds(*full)
            if not POINT_MIN_SECONDS <= seconds <= POINT_MAX_SECONDS:
                issues.append(f"Point {number} is {seconds:.0f}s; its full range must be 6-25s.")
            core_seconds = self._seconds(*core)
            if core_seconds < CORE_MIN_SECONDS:
                issues.append(f"Point {number}'s core is {core_seconds:.1f}s; it must be at least 4s.")
            valid_spans.append((number, full, core))
        for (previous, before, _), (number, after, _) in zip(valid_spans, valid_spans[1:]):
            if after[0] < before[0]:
                issues.append(f"Points must be in video order: point {number} (#{after[0]}) comes before point {previous} (#{before[0]}).")
            elif after[0] <= before[1]:
                issues.append(f"Points {previous} and {number} overlap; each unit belongs to one point.")
        if len(points) < self.min_points:
            issues.append(f"You gave {len(points)} points; give at least {self.min_points}.")
        for name, span in (("opener", draft.opener), ("closer", draft.closer)):
            if span.first_unit < 1 and span.last_unit < 1:
                continue
            if not (self._valid(span.first_unit) and self._valid(span.last_unit)) or span.first_unit > span.last_unit:
                issues.append(f"The {name} #{span.first_unit}–#{span.last_unit} is not a valid range from the transcript you were given (use -1/-1 for none).")
                continue
            if name == "opener":
                seconds = self._seconds(span.first_unit, span.last_unit)
                if seconds > RECAP_OPENER_MAX_SECONDS:
                    issues.append(f"The opener is {seconds:.0f}s; it must be at most 10s.")
            for number, full, core in valid_spans:
                overlaps = span.first_unit <= full[1] and full[0] <= span.last_unit
                if overlaps and (span.first_unit, span.last_unit) != core:
                    issues.append(
                        f"The {name} overlaps point {number}; choose a line outside the points, or exactly that point's core."
                    )
        return issues


def _snap(index: UnitIndex, first: int, last: int, floor: int, ceiling: int) -> tuple[int, int]:
    """Edges moved onto the sentence they cut into, within a few units and ``floor..ceiling``.

    A start goes back to its sentence's opening (else forward to the next
    one); an end goes forward to the sentence's close (else back).
    """
    if not index.unit(first).starts_sentence:
        back = [uid for uid in range(first - 1, max(floor, first - EDGE_SNAP_UNITS) - 1, -1)]
        ahead = [uid for uid in range(first + 1, min(last, first + EDGE_SNAP_UNITS) + 1)]
        first = next((uid for uid in back + ahead if index.unit(uid).starts_sentence), first)
    if index.boundary_strength(last) < STRONG_BOUNDARY:
        ahead = [uid for uid in range(last + 1, min(ceiling, last + EDGE_SNAP_UNITS) + 1)]
        back = [uid for uid in range(last - 1, max(first, last - EDGE_SNAP_UNITS) - 1, -1)]
        last = next((uid for uid in ahead + back if index.boundary_strength(uid) >= STRONG_BOUNDARY), last)
    return first, last


def _draft_in(index: UnitIndex, draft: RecapDraft) -> RecapDraftIn:
    """The draft for the assembler, every range starting and ending on whole sentences."""
    count = len(index)

    def span(value: Any) -> Optional[tuple[int, int]]:
        if value.first_unit < 1 or value.last_unit < 1:
            return None
        first, last = sorted((index.clamp_id(value.first_unit), index.clamp_id(value.last_unit)))
        return _snap(index, first, last, 1, count)

    usable = sorted(
        (point for point in draft.points if point.first_unit >= 1 and point.last_unit >= 1),
        key=lambda point: min(point.first_unit, point.last_unit),
    )
    points: list[RecapPointIn] = []
    for position, point in enumerate(usable):
        first, last = sorted((index.clamp_id(point.first_unit), index.clamp_id(point.last_unit)))
        floor = points[-1].last + 1 if points else 1
        following = usable[position + 1] if position + 1 < len(usable) else None
        ceiling = min(following.first_unit, following.last_unit) - 1 if following else count
        first, last = _snap(index, first, last, min(floor, first), max(ceiling, last))
        core_first, core_last = sorted((index.clamp_id(point.core_first), index.clamp_id(point.core_last)))
        if point.core_first >= 1 and first <= core_first <= core_last <= last:
            core_first, core_last = _snap(index, core_first, core_last, first, last)
        else:
            core_first, core_last = first, last
        points.append(
            RecapPointIn(
                label=_label(point.label),
                first=first,
                last=last,
                core_first=core_first,
                core_last=core_last,
                importance=point.importance,
            )
        )
    return RecapDraftIn(title=draft.title, opener=span(draft.opener), points=points, closer=span(draft.closer))


def _label(text: str) -> str:
    return compose_title(text, max_chars=LABEL_MAX_CHARS, max_words=LABEL_MAX_WORDS) or ""


async def _draft(pi: PlanInput, budget: Budget) -> RecapDraft:
    index, outline = pi.index, pi.outline.outline
    shown = _shown_units(pi)
    rules = _DraftRules(pi, shown)
    system = recap_draft_system(pi.language_name, budget, outline.central_question)
    prompt = with_instructions(
        outline_block(outline, index) + "\n\n" + transcript_block(index.listing(shown)), pi.instructions
    )
    agent = build_agent(RecapDraft, system, structural=True)
    try:
        draft, residual = await run_with_repair(agent, prompt, rules, stats=pi.stats, stage="recap_draft")
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.error("Recap draft failed (%s)", type(error).__name__)
        raise WorkflowError("plan_failed", _PLAN_FAILED) from error
    if residual:
        logger.info("Recap draft keeps %d issue(s) for the assembler", len(residual))
    return draft


def _beat_name(beat: Beat) -> str:
    if beat.kind == "opener":
        return "Intro"
    if beat.kind == "closer":
        return "Takeaway"
    return f'Point "{beat.label}"' if beat.label else "Point"


def critique_prompt(pi: PlanInput, cut: RecapCut) -> str:
    index, outline = pi.index, pi.outline.outline
    lines: list[str] = []
    for number, beat in enumerate(cut.beats, start=1):
        if number > 1:
            before = index.unit(beat.first - 1).text if beat.first > 1 else ""
            lines.append(f"‖ [{before}]" if before else "‖ [start of the video]")
        seconds = index.speech_seconds(beat.first, beat.last, (), pi.cleanup)
        lines.append(f"{number}. {_beat_name(beat)} ({seconds:.0f}s): {index.text(beat.first, beat.last)}")
    sections = ["<clip>\n" + "\n".join(lines) + "\n</clip>"]
    missing = [
        point
        for point in outline.main_points
        if not any(point.first_unit <= beat.last and beat.first <= point.last_unit for beat in cut.beats)
    ]
    if missing:
        sections.append(
            "Main points of the video not included:\n"
            + "\n".join(f"- ★{point.importance} {point.statement}" for point in missing)
        )
    if cut.attention:
        sections.append(
            "Check especially beat(s) "
            + ", ".join(str(number) for number in cut.attention)
            + ": they open on a reference to something earlier."
        )
    return with_instructions("\n\n".join(sections), pi.instructions)


async def _critique(pi: PlanInput, cut: RecapCut, budget: Budget) -> RecapCut:
    agent = build_agent(RecapCritique, recap_critique_system(pi.language_name), structural=True)
    try:
        critique = (await run(agent, critique_prompt(pi, cut), stats=pi.stats, stage="recap_critique")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Recap critique failed (%s); keeping the assembled recap", type(error).__name__)
        return cut
    fixes = [
        RecapFixIn(beat=fix.beat, action=fix.action)
        for fix in critique.fixes[:CRITIQUE_MAX_FIXES]
        if fix.action != "none"
    ]
    if critique.verdict != "fix" or not fixes:
        return cut
    try:
        fixed = apply_recap_fixes(pi.index, cut, fixes, budget, pi.cleanup)
    except WorkflowError:
        logger.warning("Recap critique fixes left no point; keeping the assembled recap")
        return cut
    pi.stats.critique_fixes += len(fixes)
    return fixed


async def plan_recap(pi: PlanInput) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index, spec = pi.index, pi.ctx.spec
    budget = recap_budget(spec.clip_length, pi.ctx.source_seconds)
    await pi.ctx.progress(54, "Mapping the main points...")
    draft = await _draft(pi, budget)
    await pi.ctx.progress(58, "Choosing what to keep...")
    cut = fit_recap(index, _draft_in(index, draft), budget, pi.outline.outline, pi.cleanup)
    await pi.ctx.progress(62, "Checking the edit...")
    cut = await _critique(pi, cut, budget)

    for note in cut.notes:
        pi.note(note["code"], note.get("params"))
    if index.coarse:
        pi.note("COARSE_TRANSCRIPT")
    # A recap always carries a title; without the draft's, the outline's one-liner (trimmed) stands in.
    title = ai.sanitize_hook_title(draft.title) or compose_title(pi.outline.outline.one_line) or ""
    return _segments(pi, cut, title, budget)


def _segments(pi: PlanInput, cut: RecapCut, title: str, budget: Budget) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = pi.index
    ranges = [index.range_seconds(beat.first, beat.last) for beat in cut.beats]
    labels = [beat.label if beat.kind != "point" else (_label(beat.label) or beat.label) for beat in cut.beats]
    meta_points = [
        {
            "label": label,
            "kind": beat.kind,
            "range_index": position,
            "droppable": beat.droppable,
            "output_start": None,
        }
        for position, (beat, label) in enumerate(zip(cut.beats, labels))
    ]
    segment = planned_segment(
        ranges=ranges,
        junction_fades=[RECAP_JUNCTION_FADE] * (len(ranges) - 1),
        max_seconds=budget.render_cap,
        required=True,
        text=" … ".join(index.text(beat.first, beat.last) for beat in cut.beats),
        hook_title=compose_title(title),
        reasoning=pi.outline.outline.one_line,
        workflow_role="recap",
        meta={"points": meta_points},
    )
    plan = base_plan(pi, title=title)
    plan["points"] = [
        {"label": label, "kind": beat.kind, "ranges": rounded_ranges([span]), "output_start": None}
        for beat, label, span in zip(cut.beats, labels, ranges)
    ]
    plan["seconds"] = cut.seconds
    return [segment], plan
