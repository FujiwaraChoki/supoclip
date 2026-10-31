"""Series planner: Part 1..N, consecutive, in source order, sharing exact cuts.

Order of work: outline (cached) → scope (only when the video is too long to
serialize whole) → draft (+ at most one repair) → deterministic partition →
critique read as a first-time viewer → re-partition with its fixes as
constraints → retitle when the parts moved. The model only proposes part ends
and skips; ``partition.partition_series`` owns lengths, contiguity and cuts.
"""

from __future__ import annotations

import logging
import math
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
from .language import part_label
from .llm import build_agent, is_cancellation, run, run_with_repair
from .outline import chapter_ranges
from .partition import PartitionConstraints, PartitionResult, partition_series, validate_skips
from .planning import PlanInput, base_plan, planned_segment, rounded_ranges
from .prompts import (
    outline_block,
    series_critique_system,
    series_draft_system,
    series_parts_clause,
    series_retitle_system,
    series_scope_system,
    transcript_block,
    with_instructions,
)
from .schemas import SeriesCopy, SeriesCritique, SeriesDraft, SeriesScope
from .titles import compose_title, strip_numbering
from .units import UnitIndex, format_clock

logger = logging.getLogger(__name__)

# Share of a series span expected to survive as parts (skips, a trimmed tail).
SPAN_COVERAGE = 0.7
SCOPE_SNAP_UNITS = 3
SPONSOR_SKIP_MAX_SECONDS = 120.0
LAST_PART_SLACK_UNITS = 3
CRITIQUE_MAX_MOVE_UNITS = 4
CRITIQUE_MAX_FIXES = 6
# Without a usable draft, a span this short is still partitioned by code alone.
FALLBACK_MAX_SPAN_SECONDS = 40 * 60
SKIP_MAX_SHARE = 0.4
PART_JUNCTION_FADE = 0.10
# Intro and outro chapters rated above this carry story and stay in the series.
FRAMING_MAX_IMPORTANCE = 2

_SKIP_KINDS_AT_EDGES = {"intro", "sponsor"}
_SKIP_KINDS_AT_END = {"outro", "sponsor"}
_PLAN_FAILED = "The AI editor couldn't finish this plan. Please try again."
_NO_STORY = "We couldn't find a storyline long enough to split into parts. Try Highlights or a Recap."


@dataclass
class _Copy:
    title: str = ""
    delivers: str = ""
    ends_on: str = ""
    post_caption: str = ""


@dataclass
class _Material:
    span: tuple[int, int]
    forced_skips: list[tuple[int, int]] = field(default_factory=list)
    skip_reasons: dict[tuple[int, int], str] = field(default_factory=dict)


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


async def _scope(pi: PlanInput, budget: Budget, limit: float) -> tuple[int, int]:
    """Arc mode: the one stretch that makes the best series (one model call)."""
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
        scope = (await run(agent, prompt, stats=pi.stats, stage="series_scope")).output
        first, last = sorted((index.clamp_id(scope.first_unit), index.clamp_id(scope.last_unit)))
        story_found = scope.story_found
    except Exception as error:
        if is_cancellation(error):
            raise
        arcs = sorted(outline.story_arcs, key=lambda arc: -arc.strength)
        if not arcs:
            logger.error("Series scope failed (%s) and the outline has no story arc", type(error).__name__)
            raise WorkflowError("plan_failed", _PLAN_FAILED) from error
        logger.warning("Series scope failed (%s); using the outline's strongest arc", type(error).__name__)
        first, last = arcs[0].first_unit, arcs[0].last_unit
        story_found = arcs[0].strength > 1

    first = nearest_sentence_start(index, first, SCOPE_SNAP_UNITS) or first
    last = nearest_strong_end(index, last, SCOPE_SNAP_UNITS) or last
    if last < first:
        first, last = last, first
    if index.speech_seconds(first, last, (), pi.cleanup) > limit:
        last = _trim_to_limit(index, first, last, limit, pi.cleanup)
    pi.note("STORY_ARC_SELECTED", _clock_range(index, first, last))
    if not story_found and all(arc.strength <= 1 for arc in outline.story_arcs):
        pi.note("NO_SINGLE_STORY")
        if index.speech_seconds(first, last, (), pi.cleanup) < 2 * budget.min:
            raise WorkflowError("no_story", _NO_STORY)
    return first, last


def _material(pi: PlanInput, span: tuple[int, int]) -> _Material:
    """The span, plus sponsor reads inside it as forced skips."""
    material = _Material(span=span)
    chapters = pi.outline.outline.chapters
    for chapter, (first, last) in zip(chapters, chapter_ranges(pi.outline.outline, pi.index)):
        if chapter.kind != "sponsor" or not (span[0] < first and last < span[1]):
            continue
        if pi.index.speech_seconds(first, last, (), pi.cleanup) <= SPONSOR_SKIP_MAX_SECONDS:
            material.forced_skips.append((first, last))
            material.skip_reasons[(first, last)] = "sponsor read"
    if material.forced_skips:
        pi.note("SPONSOR_SKIPPED")
    return material


# -- draft -------------------------------------------------------------------------------


class _DraftRules:
    """Validator for the draft, worded as the fixes it asks for."""

    def __init__(self, pi: PlanInput, material: _Material, budget: Budget, part_count: Optional[int], max_auto: int):
        self.pi = pi
        self.index = pi.index
        self.material = material
        self.budget = budget
        self.part_count = part_count
        self.max_auto = max_auto

    def skips(self, draft: SeriesDraft) -> list[tuple[int, int]]:
        return validate_skips(
            self.index,
            self.material.span,
            [(skip.first_unit, skip.last_unit) for skip in draft.skips] + self.material.forced_skips,
        )

    def __call__(self, draft: SeriesDraft) -> list[str]:
        index, budget, cleanup = self.index, self.budget, self.pi.cleanup
        a, b = self.material.span
        issues: list[str] = []
        start = draft.start_unit
        if not a <= start <= b:
            issues.append(f"start_unit #{start} is outside the material (#{a}–#{b}).")
            start = min(max(start, a), b)
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
                whole = index.speech_seconds(first, end, (), cleanup)
                share = (whole - seconds) / whole if whole > 0 else 0.0
                if share > SKIP_MAX_SHARE:
                    issues.append(f"Skips remove {share * 100:.0f}% of part {number}; the limit is 40%.")
            if number == count:
                if not (b - LAST_PART_SLACK_UNITS <= end <= b and (end == b or index.boundary_strength(end) >= STRONG_BOUNDARY)):
                    issues.append(
                        f"The last part must end at the end of the material (#{b}) or on a complete "
                        f"sentence at most {LAST_PART_SLACK_UNITS} units earlier."
                    )
                if part.ends_on.strip():
                    issues.append(f"Part {number} (the last part) must have an empty ends_on.")
            previous = end
        return issues

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


async def _draft(
    pi: PlanInput, material: _Material, budget: Budget, part_count: Optional[int], max_auto: int
) -> tuple[Optional[SeriesDraft], _DraftRules]:
    index = pi.index
    rules = _DraftRules(pi, material, budget, part_count, max_auto)
    system = series_draft_system(pi.language_name, budget, series_parts_clause(part_count, max_auto))
    a, b = material.span
    speech = index.speech_seconds(a, b, material.forced_skips, pi.cleanup)
    lines = [
        outline_block(pi.outline.outline, index),
        f"The material is units #{a}–#{b} ({format_clock(speech)} of speech).",
    ]
    if material.forced_skips:
        lines.append(
            "Forced skips (already removed, do not use): "
            + ", ".join(f"#{first}–#{last}" for first, last in material.forced_skips)
        )
    lines.append(transcript_block(_listing_without(index, material.span, material.forced_skips)))
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


def _partition_span(index: UnitIndex, material: _Material, draft: Optional[SeriesDraft]) -> tuple[int, int]:
    a, b = material.span
    if draft is None or not draft.parts:
        return a, b
    start = min(max(draft.start_unit, a), b)
    start = nearest_sentence_start(index, start, SCOPE_SNAP_UNITS) or start
    start = min(max(start, a), b)
    end = draft.parts[-1].end_unit
    if b - LAST_PART_SLACK_UNITS <= end < b and index.boundary_strength(end) >= STRONG_BOUNDARY:
        b = end
    return start, b


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


def _critique_prompt(
    pi: PlanInput, result: PartitionResult, copies: list[_Copy], series_title: str, reasons: dict[tuple[int, int], str]
) -> str:
    sections = [f'Series: "{series_title}", {len(result.parts)} parts.']
    for number, ((first, last), skips, seconds, copy) in enumerate(
        zip(result.parts, result.skips_by_part, result.seconds, copies), start=1
    ):
        header = f'=== PART {number} ({seconds:.0f}s): "{copy.title}"'
        if copy.delivers:
            header += f" — delivers: {copy.delivers}"
        if copy.ends_on:
            header += f" — ends on: {copy.ends_on}"
        sections.append(header + " ===\n" + _part_listing(pi.index, first, last, skips, reasons))
    return with_instructions(transcript_block("\n\n".join(sections)), pi.instructions)


def _apply_critique(
    pi: PlanInput,
    critique: SeriesCritique,
    result: PartitionResult,
    skips: list[tuple[int, int]],
    forced: list[tuple[int, int]],
    span: tuple[int, int],
) -> tuple[PartitionConstraints, list[tuple[int, int]], list[int], dict[int, str], int]:
    """Critique fixes as solver constraints, skips, model ends and new titles."""
    constraints = PartitionConstraints()
    skips = list(skips)
    ends = [last for _, last in result.parts]
    titles: dict[int, str] = {}
    applied = 0
    for fix in critique.fixes[:CRITIQUE_MAX_FIXES]:
        if fix.action == "none" or not 1 <= fix.part <= len(result.parts):
            continue
        first, last = result.parts[fix.part - 1]
        if fix.action in ("end_earlier", "end_later"):
            target = fix.unit
            if (
                fix.part == len(result.parts)
                or target == last
                or abs(target - last) > CRITIQUE_MAX_MOVE_UNITS
                or not span[0] <= target < span[1]
                or (fix.action == "end_earlier") != (target < last)
            ):
                logger.info("Ignoring series fix %s on part %d", fix.action, fix.part)
                continue
            constraints.pinned_ends.add(target)
            constraints.forbidden_ends.add(last)
            ends = [target if end == last else end for end in ends]
        elif fix.action == "drop_units":
            low, high = sorted((fix.unit, fix.last_unit))
            if not first < low <= high < last:
                continue
            skips.append((low, high))
        elif fix.action == "restore_units":
            low, high = sorted((fix.unit, fix.last_unit))
            before = len(skips)
            skips = [skip for skip in skips if skip in forced or skip[1] < low or high < skip[0]]
            if len(skips) == before:
                continue
        elif fix.action == "retitle":
            title = _clean_title(fix.new_title)
            if not title:
                continue
            titles[fix.part] = title
        applied += 1
    return constraints, skips, ends, titles, applied


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
        copies.append(_Copy(_clean_title(part.title), _clean(part.delivers), _clean(part.ends_on), _clean(part.post_caption)))
    return copies


def _needs_retitle(draft: Optional[SeriesDraft], parts: list[tuple[int, int]]) -> bool:
    if draft is None or len(draft.parts) != len(parts):
        return True
    drafted = [part.end_unit for part in draft.parts]
    return any(min(abs(last - end) for end in drafted) > 1 for _, last in parts)


async def _retitle(pi: PlanInput, result: PartitionResult, copies: list[_Copy], series_title: str) -> Optional[SeriesCopy]:
    agent = build_agent(SeriesCopy, series_retitle_system(pi.language_name), structural=True)
    sections = [f"{len(result.parts)} parts."]
    for number, ((first, last), skips, seconds) in enumerate(zip(result.parts, result.skips_by_part, result.seconds), start=1):
        sections.append(f"=== PART {number} ({seconds:.0f}s) ===\n" + _part_listing(pi.index, first, last, skips, {}))
    prompt = with_instructions(transcript_block("\n\n".join(sections)), pi.instructions)
    try:
        return (await run(agent, prompt, stats=pi.stats, stage="series_retitle")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Series retitle failed (%s); keeping the mapped titles", type(error).__name__)
        return None


def _apply_retitle(copy: SeriesCopy, copies: list[_Copy]) -> tuple[list[_Copy], str]:
    """The retitled copy; a field the model left empty keeps the mapped draft's text."""
    updated = list(copies)
    for entry in copy.parts:
        if 1 <= entry.part <= len(updated):
            old = updated[entry.part - 1]
            updated[entry.part - 1] = _Copy(
                _clean_title(entry.title) or old.title,
                _clean(entry.delivers) or old.delivers,
                _clean(entry.ends_on) or old.ends_on,
                _clean(entry.post_caption) or old.post_caption,
            )
    return updated, _clean_title(copy.series_title)


# -- entry point ------------------------------------------------------------------------------


async def plan_series(pi: PlanInput) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index, spec = pi.index, pi.ctx.spec
    budget = part_budget(spec.clip_length)
    requested = spec.options.part_count
    await pi.ctx.progress(54, "Mapping the story...")

    content = content_span(pi)
    limit = span_limit(budget, requested)
    if index.speech_seconds(*content, (), pi.cleanup) <= limit:
        span = content
    else:
        span = await _scope(pi, budget, limit)
    material = _material(pi, span)

    # Fails fast (source_too_short) before any draft when two parts can't fit.
    partition_series(index, material.span, material.forced_skips, budget, None, [], None, pi.cleanup)
    story = index.speech_seconds(*material.span, material.forced_skips, pi.cleanup)
    max_auto = max(SERIES_MIN_PARTS, min(SERIES_MAX_PARTS, math.floor(story / budget.min)))
    draft_count = min(requested, max_auto) if requested is not None else None

    await pi.ctx.progress(58, "Planning parts...")
    draft, rules = await _draft(pi, material, budget, draft_count, max_auto)
    if draft is None:
        if story > FALLBACK_MAX_SPAN_SECONDS:
            raise WorkflowError("plan_failed", _PLAN_FAILED)
        logger.warning("Series draft unavailable; partitioning on the transcript alone")

    span = _partition_span(index, material, draft)
    skips = rules.skips(draft) if draft is not None else validate_skips(index, span, material.forced_skips)
    reasons = dict(material.skip_reasons)
    if draft is not None:
        for skip in draft.skips:
            reasons.setdefault(tuple(sorted((skip.first_unit, skip.last_unit))), _clean(skip.reason))
    llm_ends = [part.end_unit for part in draft.parts] if draft is not None else []
    result = partition_series(index, span, skips, budget, requested, llm_ends, None, pi.cleanup)

    series_title = _clean_title(draft.series_title) if draft is not None else ""
    copies = _mapped_copy(draft, span, result.parts) if draft is not None else [_Copy() for _ in result.parts]
    titles: dict[int, str] = {}
    critiqued_parts = len(result.parts)
    if draft is not None:
        await pi.ctx.progress(62, "Checking every cut...")
        critique = await _critique(pi, result, copies, series_title, reasons)
        if critique is not None and critique.verdict == "fix" and critique.fixes:
            constraints, fixed_skips, ends, titles, applied = _apply_critique(
                pi, critique, result, result.skips, material.forced_skips, span
            )
            pi.stats.critique_fixes += applied
            if constraints.pinned_ends or constraints.forbidden_ends or fixed_skips != result.skips:
                result = partition_series(index, span, fixed_skips, budget, requested, ends, constraints, pi.cleanup)
                copies = _mapped_copy(draft, span, result.parts)

    # The critique's titles hold unless a full retitle (parts moved) replaces
    # them; they name parts by number, so only while the count is unchanged.
    if titles and len(copies) == critiqued_parts:
        for number, title in titles.items():
            if number <= len(copies):
                copies[number - 1].title = title
    if _needs_retitle(draft, result.parts):
        retitled = await _retitle(pi, result, copies, series_title)
        if retitled is not None:
            copies, new_title = _apply_retitle(retitled, copies)
            series_title = new_title or series_title
    if copies:
        copies[-1].ends_on = ""

    for note in result.notes:
        pi.note(note["code"], note.get("params"))
    if any(note["code"] == "STORY_ARC_SELECTED" for note in pi.notes):
        # The stretch the parts actually cover: the solver may end it early.
        _set_note_params(pi, "STORY_ARC_SELECTED", _clock_range(index, result.parts[0][0], result.parts[-1][1]))
    if index.coarse:
        pi.note("COARSE_TRANSCRIPT")
    return _segments(pi, result, copies, series_title, budget)


def _segments(
    pi: PlanInput, result: PartitionResult, copies: list[_Copy], series_title: str, budget: Budget
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = pi.index
    count = len(result.parts)
    segments: list[dict[str, Any]] = []
    plan_parts: list[dict[str, Any]] = []
    for number, ((first, last), skips, seconds, copy) in enumerate(
        zip(result.parts, result.skips_by_part, result.seconds, copies), start=1
    ):
        ranges = index.ranges_for(first, last, skips)
        hook_title = compose_title(copy.title, prefix=part_label(pi.language, number))
        segments.append(
            planned_segment(
                ranges=ranges,
                junction_fades=[PART_JUNCTION_FADE] * (len(ranges) - 1),
                max_seconds=budget.render_cap,
                required=True,
                text=index.text(first, last, skips),
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
                },
            )
        )
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
                "seconds": round(seconds, 1),
            }
        )
    plan = base_plan(pi, title=series_title)
    plan["parts"] = plan_parts
    # Every unit the series leaves out between its first and last part (I2 audits use these).
    plan["skips"] = [[first, last] for first, last in result.skips]
    return segments, plan
