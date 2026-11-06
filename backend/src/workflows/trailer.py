"""Trailer planner: one short clip that sells one question, never the answer.

The trailer cuts on clause units (a comma or a breath) so beats can stop just
before the answer. A deterministic spoiler set (outline payoffs, the endings
of story arcs, the last stretch of a story or vlog) is off limits before the
model sees anything; the model adds the payoffs it finds and may reclaim
setups the sentence-level marking caught; ``fit_trailer`` fits the beats to
the window; a judge reads the result as a viewer, and a weak one is redrafted
once. Titles come last and must be heard in the final beats. When the model's
trailer can't be made safe or fit, a deterministic trailer is built from what
survives plus the outline (stats records ``TRAILER_OUTLINE_FALLBACK``), and
when even that can't avoid the ending, the plan fails loudly
(``trailer_unbuildable``).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Collection, Iterable, Optional, Sequence

from .. import ai
from .assembly import (
    TRAILER_BEAT_MAX_SECONDS,
    TRAILER_BEAT_MIN_SECONDS,
    TRAILER_COLD_OPEN_MAX_SECONDS,
    TRAILER_JUNCTION_FADE,
    TRAILER_SHORT_BEAT_MIN_SECONDS,
    TrailerBeatIn,
    TrailerCut,
    fit_trailer,
    trailer_beat_min,
    trailer_end_ok,
    trailer_start_ok,
)
from .boundaries import STRONG_BOUNDARY
from .durations import Budget, trailer_budget
from .errors import WorkflowError
from .language import normalize_language_code, opens_with_back_reference
from .llm import build_agent, is_cancellation, run, run_with_repair
from .outline import chapter_ranges
from .planning import PlanInput, base_plan, planned_segment, rounded_ranges
from .recap import framing_units, signpost
from .prompts import (
    trailer_button_system,
    trailer_draft_system,
    trailer_judge_system,
    trailer_story_block,
    trailer_titles_system,
    transcript_block,
    with_instructions,
)
from .schemas import ButtonPick, SpoilerVerdict, TrailerDraft, TrailerTitles
from .titles import compose_title, heard_share
from ..utils.async_helpers import run_in_thread
from .units import UnitIndex, build_units

logger = logging.getLogger(__name__)

ARC_ENDING_SHARE = 0.15
STORY_ENDING_SHARE = 0.08
_STORY_FORMATS = {"story", "vlog"}
MIN_BEATS = 4
MAX_BEATS = 6
TITLE_CANDIDATES = 3
BUTTON_CANDIDATES = 12
BUTTON_PICK_CANDIDATES = 20
BUTTON_MAX_SECONDS = 8.0
BUTTON_STRONG_END = 0.9
MAX_RECLAIMED = 5
# At least this share of a title's content words must be heard in the beats.
TITLE_HEARD_SHARE = 0.5
# Below this the judge's viewer isn't hooked: one guided redraft (never a worse cut).
CURIOSITY_BAR = 65
# All beats this close in length play as a monotone.
RHYTHM_MIN_SPREAD_SECONDS = 1.5
RHYTHM_MAX_MEDIAN_SECONDS = 5.0
RHYTHM_MIN_BEATS = 5
FALLBACK_ESCALATION_MAX_SECONDS = TRAILER_BEAT_MAX_SECONDS
FALLBACK_BUTTON_MAX_SECONDS = 8.0
FALLBACK_MAX_ESCALATIONS = 3
_OFF_LIMITS_LINE = re.compile(r"^(#(\d+) \S+ \+\S+s )")

# A cold open never starts by leaning on what came before.
_LEANING_FIRST_WORDS = {"and", "but", "und", "aber", "y", "pero", "e", "mas", "et", "mais"}
# A definition sells nothing: an outline point opening like this comes last in the fallback.
_DEFINITION_RE = {
    "en": re.compile(r"\b(is|are) (a|an|the|called)\b|\brefers? to\b|\bstands? for\b|\bmeans\b", re.IGNORECASE),
    "de": re.compile(r"\b(ist|sind) (ein|eine|der|die|das)\b|\bheißt\b|\bbezeichnet\b|\bbedeutet\b", re.IGNORECASE),
}
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


def _spoiler_spans(pi: PlanInput) -> list[tuple[int, int]]:
    index, outline = pi.index, pi.outline.outline
    count = len(index)
    spans: list[tuple[int, int]] = [(payoff.first_unit, payoff.last_unit) for payoff in outline.payoffs]
    for arc in outline.story_arcs:
        start, end = index.unit(arc.first_unit).start, index.unit(arc.last_unit).end
        spans.append((index.unit_at(start + (1 - ARC_ENDING_SHARE) * (end - start)), arc.last_unit))
    if outline.format in _STORY_FORMATS:
        start, end = index.unit(1).start, index.unit(count).end
        spans.append((index.unit_at(end - STORY_ENDING_SHARE * (end - start)), count))
    return spans


def spoiler_sentences(pi: PlanInput) -> set[int]:
    """Sentence ids a trailer must never use, each widened by one sentence after it.

    Only forward: the line right before a reveal is the setup, the trailer's
    best button. The judge and the model's own payoffs catch real leaks.
    """
    count = len(pi.index)
    blocked: set[int] = set()
    for first, last in _spoiler_spans(pi):
        low, high = sorted((pi.index.clamp_id(first), pi.index.clamp_id(last)))
        blocked.update(range(max(1, low), min(count, high + 1) + 1))
    return blocked


def setup_sentences(pi: PlanInput, spoilers: set[int]) -> set[int]:
    """The sentence right before each outline payoff, when it isn't off limits itself."""
    setups = set()
    for payoff in pi.outline.outline.payoffs:
        uid = pi.index.clamp_id(min(payoff.first_unit, payoff.last_unit)) - 1
        if uid >= 1 and uid not in spoilers:
            setups.add(uid)
    return setups


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


def mark_off_limits(listing: str, off_limits: set[int], setups: Iterable[int] = ()) -> str:
    setups = set(setups)
    lines = []
    for line in listing.splitlines():
        match = _OFF_LIMITS_LINE.match(line)
        if match and int(match.group(2)) in off_limits:
            line = match.group(1) + "[OFF LIMITS] " + line[match.end() :]
        elif match and int(match.group(2)) in setups:
            line = match.group(1) + "[SETUP] " + line[match.end() :]
        lines.append(line)
    return "\n".join(lines)


def _main_speaker(clauses: UnitIndex) -> Optional[str]:
    speech: dict[str, float] = {}
    for unit in clauses.units:
        if unit.speaker:
            speech[unit.speaker] = speech.get(unit.speaker, 0.0) + unit.end - unit.start
    return max(speech, key=speech.get) if speech else None


def _spread(items: Sequence[tuple[int, int, int]], count: int) -> list[tuple[int, int, int]]:
    """``count`` items evenly across the list, so they come from the whole video."""
    if count <= 0:
        return []
    if len(items) <= count:
        return list(items)
    step = len(items) / count
    return [items[int(position * step)] for position in range(count)]


def button_candidates(
    pi: PlanInput,
    clauses: UnitIndex,
    blocked: set[int],
    setups: set[int],
    shown: Optional[Sequence[int]],
    limit: int = BUTTON_CANDIDATES,
    questions_only: bool = False,
) -> list[tuple[int, int]]:
    """Short lines a trailer could end on: questions and hard stops, clear of spoilers.

    Each is at most 8 s, ends on a question or a strong cut, starts where a
    listener can follow, and is said by the main speaker or sits inside a
    main point, an arc or a setup line. Best first: setup lines, questions,
    then hard stops, each sampled across the whole video. A short line takes
    in the clause before it when it needs the room.
    """
    outline = pi.outline.outline
    inside: set[int] = set(setups)
    for point in outline.main_points:
        low, high = to_clauses(pi.index, clauses, point.first_unit, point.last_unit)
        inside.update(range(low, high + 1))
    for arc in outline.story_arcs:
        low, high = to_clauses(pi.index, clauses, arc.first_unit, arc.last_unit)
        inside.update(range(low, high + 1))
    allowed = set(shown) if shown is not None else None
    speaker = _main_speaker(clauses)
    housekeeping = _to_clause_ids(pi, clauses, framing_units(pi))
    found: list[tuple[int, int, int]] = []
    for uid in range(1, len(clauses) + 1):
        unit = clauses.unit(uid)
        if uid in blocked or uid in housekeeping or (allowed is not None and uid not in allowed):
            continue
        strength = clauses.boundary_strength(uid)
        if not (unit.ends_question or strength >= BUTTON_STRONG_END or (uid in setups and strength >= STRONG_BOUNDARY)):
            continue
        if questions_only and not unit.ends_question:
            continue
        if not (unit.speaker == speaker or uid in inside):
            continue
        first = uid
        while (
            first > 1
            and (
                not trailer_start_ok(clauses, first)
                or clauses.speech_seconds(first, uid, (), pi.cleanup) < TRAILER_SHORT_BEAT_MIN_SECONDS
            )
            and first - 1 not in blocked
            and clauses.speech_seconds(first - 1, uid, (), pi.cleanup) <= BUTTON_MAX_SECONDS
        ):
            first -= 1
        seconds = clauses.speech_seconds(first, uid, (), pi.cleanup)
        if not trailer_start_ok(clauses, first) or not TRAILER_SHORT_BEAT_MIN_SECONDS <= seconds <= BUTTON_MAX_SECONDS:
            continue
        rank = 0 if uid in setups or any(u in setups for u in range(first, uid + 1)) else 1 if unit.ends_question else 2
        found.append((rank, first, uid))
    # Best first: setup lines, then questions, then hard stops (the prompt lists them in video order).
    chosen: list[tuple[int, int, int]] = []
    for rank in (0, 1, 2):
        group = sorted(item for item in found if item[0] == rank)
        chosen += _spread(group, limit - len(chosen))
    return [(first, last) for _, first, last in chosen]


def chapter_lines(pi: PlanInput, clauses: UnitIndex, blocked: set[int]) -> list[str]:
    """The video's map for the trailer drafter: chapters and main points, in clause ids."""
    outline, index = pi.outline.outline, pi.index
    lines = []
    for number, (chapter, (first, last)) in enumerate(zip(outline.chapters, chapter_ranges(outline, index)), start=1):
        low, high = to_clauses(index, clauses, first, last)
        lines.append(
            f"Chapter {number} (#{low}-#{high}, importance {chapter.importance}): {chapter.title.strip()} — {chapter.summary.strip()}"
        )
    for point in outline.main_points:
        low, high = to_clauses(index, clauses, point.core_first, point.core_last)
        if any(uid in blocked for uid in range(low, high + 1)):
            continue
        lines.append(f"Main point (#{low}-#{high}): {point.statement.strip()}")
    return lines


# -- draft --------------------------------------------------------------------------------------


class _DraftRules:
    def __init__(
        self,
        clauses: UnitIndex,
        spoilers: set[int],
        shown: Optional[Sequence[int]],
        budget: Budget,
        cleanup: dict,
        *,
        candidates: Sequence[tuple[int, int]] = (),
        story: bool = False,
        relaxed: bool = False,
        question_ending: bool = False,
    ):
        self.relaxed = relaxed  # the creator accepts spoilers: the model's own reveals aren't off limits either
        self.question_ending = question_ending
        self.clauses = clauses
        self.spoilers = spoilers
        self.shown = set(shown) if shown is not None else None
        self.budget = budget
        self.cleanup = cleanup
        self.candidates = list(candidates)
        self.story = story  # a story's middle beats keep the video's order, and its ending stays strictly hidden

    def _valid(self, uid: int) -> bool:
        return 1 <= uid <= len(self.clauses) and (self.shown is None or uid in self.shown)

    def reclaimed(self, draft: TrailerDraft) -> set[int]:
        """[OFF LIMITS] clauses the model says only raise the question: never in a story or a vlog."""
        if self.story:
            return set()
        reclaimed: set[int] = set()
        for span in draft.reclaimed[:MAX_RECLAIMED]:
            low, high = sorted((span.first_unit, span.last_unit))
            reclaimed.update(range(max(1, low), min(len(self.clauses), high) + 1))
        return reclaimed & self.spoilers

    def blocked(self, draft: TrailerDraft) -> set[int]:
        blocked = self.spoilers - self.reclaimed(draft)
        if self.relaxed:
            return blocked
        for payoff in draft.payoffs:
            low, high = sorted((payoff.first_unit, payoff.last_unit))
            blocked.update(range(max(1, low), min(len(self.clauses), high) + 1))
        return blocked

    def __call__(self, draft: TrailerDraft) -> list[str]:
        issues: list[str] = []
        clauses = self.clauses
        blocked = self.blocked(draft)
        beats = draft.beats
        if not MIN_BEATS <= len(beats) <= MAX_BEATS:
            issues.append(f"You made {len(beats)} beats; make {MIN_BEATS}-{MAX_BEATS}.")
        if self.question_ending and self.candidates and beats:
            last = beats[-1].last_unit
            if not (1 <= last <= len(clauses) and clauses.unit(last).ends_question):
                issues.append("The creator wants the trailer to end on a question; end on one of the button candidates.")
        spans: list[tuple[int, int, int]] = []
        lengths: list[float] = []
        total = 0.0
        for number, beat in enumerate(beats, start=1):
            bad = [uid for uid in (beat.first_unit, beat.last_unit) if not self._valid(uid)]
            if bad:
                issues.append(f"Beat {number} uses #{bad[0]}, which is not in the transcript you were given.")
                continue
            low, high = sorted((beat.first_unit, beat.last_unit))
            if any(uid in blocked for uid in range(low, high + 1)):
                issues.append(f"Beat {number} (#{low}–#{high}) uses a payoff or an [OFF LIMITS] unit.")
            seconds = clauses.speech_seconds(low, high, (), self.cleanup)
            total += seconds
            lengths.append(seconds)
            limit = TRAILER_COLD_OPEN_MAX_SECONDS if number == 1 else TRAILER_BEAT_MAX_SECONDS
            minimum = TRAILER_BEAT_MIN_SECONDS if number == 1 else TRAILER_SHORT_BEAT_MIN_SECONDS
            if not minimum <= seconds <= limit:
                issues.append(f"Beat {number} is {seconds:.1f}s; it must be {minimum:g}-{limit:g}s.")
            text = clauses.text(low, high)
            if signpost(text, clauses.language):
                issues.append(f"Beat {number} talks about the video instead of its content; use a line from the story itself.")
            if not trailer_start_ok(clauses, low):
                opening = " ".join(clauses.unit(low).text.split()[:6])
                issues.append(f'Beat {number} starts mid-sentence ("{opening}…"); start on the unit that opens that sentence.')
            role = "button" if number == len(beats) else beat.role
            if not trailer_end_ok(clauses, high, role):
                ending = " ".join(clauses.unit(high).text.split()[-6:])
                if role == "button":
                    hint = self._candidate_hint()
                    issues.append(
                        f'The button ends on "…{ending}", which neither asks nor lands. End on a question or a line '
                        f"that stops on a complete hanging clause.{hint}"
                    )
                else:
                    issues.append(f'Beat {number} stops mid-phrase ("…{ending}"); end it where its sentence ends.')
            issues += self._stranger(number, role, low, spans)
            spans.append((number, low, high))
        for position, (number, low, high) in enumerate(spans):
            for other, other_low, other_high in spans[position + 1 :]:
                if low <= other_high and other_low <= high:
                    issues.append(f"Beats {number} and {other} overlap.")
        if beats and beats[0].role != "cold_open":
            issues.append("The first beat must be the cold_open.")
        if beats and beats[-1].role != "button":
            issues.append("The last beat must be the button.")
        if self.story:
            middle = [span for span in spans if 1 < span[0] < len(beats)]
            for (previous, previous_low, _), (number, low, _) in zip(middle, middle[1:]):
                if low < previous_low:
                    issues.append(
                        f"Beats between the cold_open and the button play in source order: beat {number} (#{low}) "
                        f"comes before beat {previous} (#{previous_low}) in the video."
                    )
                    break
        issues += self._rhythm(lengths)
        total -= TRAILER_JUNCTION_FADE * max(0, len(spans) - 1)
        if spans and not self.budget.min <= total <= self.budget.max:
            issues.append(
                f"The trailer is {total:.0f}s; it must be {self.budget.min:.0f}-{self.budget.max:.0f}s."
            )
        elif spans and not issues:
            # What the fitter makes of it decides whether this draft ever plays.
            cut = fit_trailer(clauses, _beats_in(draft), blocked, self.budget, self.cleanup, source_order=self.story)
            if not cut.usable:
                issues.append(
                    f"After fitting, the trailer has {len(cut.beats)} beats and {cut.seconds:.0f}s; it must have at least "
                    f"3 and be {self.budget.min:.0f}-{self.budget.max:.0f}s, opening on the cold_open and ending on the button."
                )
        return issues

    def _stranger(self, number: int, role: str, low: int, earlier: Sequence[tuple[int, int, int]]) -> list[str]:
        """A beat a viewer of only this trailer can't resolve. A middle beat may lean on the beat it continues."""
        clauses, issues = self.clauses, []
        text = clauses.unit(low).text
        first_word = next(iter(text.casefold().split()), "").strip(",.;:!?…—–-")
        if number == 1 and first_word in _LEANING_FIRST_WORDS:
            issues.append(f'The cold open opens on "{first_word}", leaning on something unheard; start on a line that stands alone.')
        continues = bool(earlier) and role != "button" and 0 < low - earlier[-1][2] <= 2
        if not continues and opens_with_back_reference(text, clauses.language, strict=True):
            issues.append(
                f'Beat {number} opens on "{" ".join(text.split()[:5])}…", which leans on something this trailer never '
                "says; drop it or find the line that names the thing."
            )
        return issues

    def _rhythm(self, lengths: Sequence[float]) -> list[str]:
        if len(lengths) < RHYTHM_MIN_BEATS:
            return []
        ordered = sorted(lengths)
        median = ordered[len(ordered) // 2]
        if median > RHYTHM_MAX_MEDIAN_SECONDS or ordered[-1] - ordered[0] < RHYTHM_MIN_SPREAD_SECONDS:
            return ["Every beat is about the same length; vary them: one 6-8 s line, then 2-4 s hits."]
        return []

    def _candidate_hint(self) -> str:
        if not self.candidates:
            return ""
        listed = ", ".join(f"#{first}-#{last}" for first, last in self.candidates[:6])
        return f" Candidates: {listed}."


def _beats_in(draft: TrailerDraft, excluded: Collection[int] = ()) -> list[TrailerBeatIn]:
    return [
        TrailerBeatIn(role=beat.role, first=beat.first_unit, last=beat.last_unit, why=" ".join(beat.why.split()))
        for position, beat in enumerate(draft.beats)
        if position not in excluded
    ]


@dataclass
class _Drafter:
    """What it takes to ask for this trailer again, and the draft in play."""

    agent: Any
    prompt: str
    rules: _DraftRules
    current: Optional[TrailerDraft] = None
    reclaimed: set[int] = field(default_factory=set)  # clauses set free by any draft this plan used

    def use(self, draft: TrailerDraft) -> None:
        self.current = draft
        self.reclaimed |= self.rules.reclaimed(draft)


async def _draft(
    pi: PlanInput, clauses: UnitIndex, spoilers: set[int], budget: Budget
) -> tuple[Optional[TrailerDraft], _Drafter]:
    shown = _shown_clauses(pi, clauses, spoilers)
    outline = pi.outline.outline
    setups = _to_clause_ids(pi, clauses, setup_sentences(pi, spoiler_sentences(pi))) - spoilers
    question_ending = pi.setting("ending") == "question"
    candidates = button_candidates(pi, clauses, spoilers, setups, shown, questions_only=question_ending)
    rules = _DraftRules(
        clauses, spoilers, shown, budget, pi.cleanup, candidates=candidates, story=outline.format in _STORY_FORMATS,
        relaxed=pi.setting("spoilers") == "relaxed", question_ending=question_ending,
    )
    listing = clauses.listing(shown)
    if shown is None:
        listing = mark_off_limits(listing, spoilers, setups)
    system = trailer_draft_system(pi.language_name, budget, outline.central_question, outline.format)
    sections = [trailer_story_block(outline, chapter_lines(pi, clauses, spoilers))]
    if candidates:
        sections.append(
            "<button_candidates>\n"
            + "\n".join(f'#{first}-#{last} "{clauses.text(first, last)}"' for first, last in sorted(candidates))
            + "\n</button_candidates>"
        )
    sections.append(transcript_block(listing))
    prompt = with_instructions("\n\n".join(sections), pi.instructions)
    agent = build_agent(TrailerDraft, system, structural=True)
    drafter = _Drafter(agent, prompt, rules)
    try:
        draft, residual = await run_with_repair(agent, prompt, rules, stats=pi.stats, stage="trailer_draft")
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Trailer draft failed (%s); building the trailer from the outline", type(error).__name__)
        return None, drafter
    if residual:
        logger.info("Trailer draft keeps %d issue(s) for the fitter", len(residual))
    drafter.use(draft)
    return draft, drafter


def draft_titles(draft: Optional[TrailerDraft]) -> list[str]:
    """The drafted title candidates, cleaned, unique, at most three."""
    return clean_titles(draft.title_candidates if draft is not None else [])


def clean_titles(candidates: Sequence[str]) -> list[str]:
    titles: list[str] = []
    for candidate in candidates:
        title = ai.sanitize_hook_title(candidate) or ""
        if title and title.casefold() not in {t.casefold() for t in titles}:
            titles.append(title)
    return titles[:TITLE_CANDIDATES]


def beats_text(clauses: UnitIndex, cut: TrailerCut) -> str:
    return " ".join(clauses.text(beat.first, beat.last) for beat in cut.beats)


def title_is_heard(title: str, clauses: UnitIndex, cut: TrailerCut) -> bool:
    """A title may promise only what plays: most of its content words are said in the beats."""
    return clauses.no_space or heard_share(title, beats_text(clauses, cut)) >= TITLE_HEARD_SHARE


def _picked_title(verdict: SpoilerVerdict, titles: Sequence[str]) -> str:
    """The title the judge found most intriguing without revealing; "" when none qualifies."""
    if 1 <= verdict.best_title <= len(titles):
        return titles[verdict.best_title - 1]
    return ""


async def _final_title(
    pi: PlanInput, clauses: UnitIndex, cut: TrailerCut, title: str, thread: str
) -> str:
    """The cleared title while the beats still say it, else one written for the beats that play."""
    if title and title_is_heard(title, clauses, cut):
        return title
    beats = "\n".join(
        f"Beat {number}: {clauses.text(beat.first, beat.last)}" for number, beat in enumerate(cut.beats, start=1)
    )
    prompt = with_instructions(f"The trailer's thread: {thread}\n\n<clip>\n{beats}\n</clip>", pi.instructions)
    agent = build_agent(TrailerTitles, trailer_titles_system(pi.language_name), structural=True)
    try:
        written = (await run(agent, prompt, stats=pi.stats, stage="trailer_titles")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Trailer titles failed (%s)", type(error).__name__)
        return ""
    return next((item for item in clean_titles(written.titles) if title_is_heard(item, clauses, cut)), "")


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


def judge_prompt(
    pi: PlanInput,
    clauses: UnitIndex,
    cut: TrailerCut,
    reveals: Sequence[str],
    titles: Sequence[str] = (),
    thread: str = "",
) -> str:
    sections = []
    if reveals:
        sections.append("What the full video reveals:\n" + "\n".join(f"- {reveal}" for reveal in reveals))
    if pi.outline.outline.central_question:
        sections.append(f"The video's central question: {pi.outline.outline.central_question}")
    if thread:
        sections.append(f"The question this trailer sells (its thread): {thread}")
    beats = "\n".join(
        f"Beat {number} ({beat.role.replace('_', ' ')}): {clauses.text(beat.first, beat.last)}"
        for number, beat in enumerate(cut.beats, start=1)
    )
    heading = (
        "Titles:\n" + "\n".join(f"{number}. {title}" for number, title in enumerate(titles, start=1)) + "\n"
        if titles
        else "Titles: none (best_title -1)\n"
    )
    sections.append(f"<clip>\n{heading}{beats}\n</clip>")
    return "\n\n".join(sections)


async def _judge(
    pi: PlanInput, clauses: UnitIndex, cut: TrailerCut, reveals: Sequence[str], titles: Sequence[str], thread: str = ""
) -> Optional[SpoilerVerdict]:
    agent = build_agent(SpoilerVerdict, trailer_judge_system(pi.language_name), structural=True)
    try:
        prompt = judge_prompt(pi, clauses, cut, reveals, titles, thread)
        verdict = (await run(agent, prompt, stats=pi.stats, stage="trailer_judge")).output
        if pi.setting("spoilers") == "relaxed":
            # The judge's other notes stand; giving the ending away is what the creator allowed.
            return verdict.model_copy(update={"reveals_payoff": False, "offending_beat": -1})
        return verdict
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Spoiler judge failed (%s); keeping the fitted trailer", type(error).__name__)
        return None


# -- deterministic fallback ---------------------------------------------------------------------------


def _head(
    clauses: UnitIndex, first: int, last: int, max_seconds: float, cleanup: dict, role: str = "escalation"
) -> Optional[tuple[int, int]]:
    """A piece of ``first..last`` within ``max_seconds`` that starts where a listener can follow and
    ends on a line that lands: from the earliest such start, the longest such piece; else None."""
    for start in range(first, last + 1):
        if not trailer_start_ok(clauses, start):
            continue
        best: Optional[tuple[int, int]] = None
        for end in range(start, last + 1):
            seconds = clauses.speech_seconds(start, end, (), cleanup)
            if seconds > max_seconds:
                break
            if seconds >= trailer_beat_min(role) and trailer_end_ok(clauses, end, role):
                best = (start, end)
        if best is not None:
            return best
    return None


def definition_shaped(text: str, language: Optional[str]) -> bool:
    """A line that explains ("X is a kind of Y") rather than sells."""
    pattern = _DEFINITION_RE.get(normalize_language_code(language) or "")
    return bool(pattern and pattern.search(" ".join((text or "").split()[:8])))


def fallback_trailer(
    pi: PlanInput,
    clauses: UnitIndex,
    spoilers: set[int],
    budget: Budget,
    survivors: Sequence[TrailerBeatIn] = (),
) -> TrailerCut:
    """Cold open, escalations and a hanging question: what survived of the draft first, then the outline.

    The outline's points rank by importance after setting definition-shaped
    ones aside, since importance alone selects explanations.
    """
    outline = pi.outline.outline
    usable = [
        beat
        for beat in survivors
        if beat.first >= 1 and beat.last >= beat.first and not any(uid in spoilers for uid in range(beat.first, beat.last + 1))
    ]
    kept_cold = next((beat for beat in usable if beat.role == "cold_open"), None)
    kept_button = next((beat for beat in usable if beat.role == "button"), None)
    kept_middle = [beat for beat in usable if beat.role in ("context", "escalation")]
    taken_spans = [(beat.first, beat.last) for beat in usable]
    cores = []
    for point in sorted(outline.main_points, key=lambda item: -item.importance):
        low, high = to_clauses(pi.index, clauses, point.core_first, point.core_last)
        if any(uid in spoilers for uid in range(low, high + 1)):
            continue
        if any(low <= span[1] and span[0] <= high for span in taken_spans):
            continue
        cores.append((low, high))
    cores.sort(key=lambda span: definition_shaped(clauses.text(*span), clauses.language))
    beats: list[TrailerBeatIn] = [kept_cold] if kept_cold else []
    rest = list(cores)
    while rest and not beats:
        low, high = rest.pop(0)
        head = _head(clauses, low, high, TRAILER_COLD_OPEN_MAX_SECONDS, pi.cleanup, "cold_open")
        if head:
            beats.append(TrailerBeatIn("cold_open", *head, why="strongest point"))
    escalations: list[tuple[int, int]] = []
    for low, high in rest:
        if len(escalations) + len(kept_middle) >= FALLBACK_MAX_ESCALATIONS:
            break
        head = _head(clauses, low, high, FALLBACK_ESCALATION_MAX_SECONDS, pi.cleanup)
        if head:
            escalations.append(head)
    beats += kept_middle
    for head in sorted(escalations):
        beats.append(TrailerBeatIn("escalation", *head, why="main point"))
    if kept_button is not None:
        beats.append(kept_button)
    first_payoff = min(
        (to_clauses(pi.index, clauses, payoff.first_unit, payoff.last_unit)[0] for payoff in outline.payoffs),
        default=len(clauses) + 1,
    )
    taken = {uid for beat in beats for uid in range(beat.first, beat.last + 1)}
    for uid in range(first_payoff - 1, 0, -1):
        if kept_button is not None:
            break
        unit = clauses.unit(uid)
        if not unit.ends_question or uid in spoilers or uid in taken:
            continue
        first = uid
        while (
            first > 1
            and (
                clauses.speech_seconds(first, uid, (), pi.cleanup) < TRAILER_SHORT_BEAT_MIN_SECONDS
                or not trailer_start_ok(clauses, first)
            )
            and first - 1 not in spoilers
            and first - 1 not in taken
        ):
            first -= 1
        if clauses.speech_seconds(first, uid, (), pi.cleanup) <= FALLBACK_BUTTON_MAX_SECONDS and trailer_start_ok(clauses, first):
            beats.append(TrailerBeatIn("button", first, uid, why="open question"))
            break
    if not beats or beats[-1].role != "button":
        # No safe question: the strongest line a trailer could stop on.
        taken = {uid for beat in beats for uid in range(beat.first, beat.last + 1)}
        for low, high in button_candidates(pi, clauses, spoilers, set(), None):
            if not any(uid in taken or uid in spoilers for uid in range(low, high + 1)):
                beats.append(TrailerBeatIn("button", low, high, why="hanging line"))
                break
    if len(beats) < 3:
        raise WorkflowError("trailer_unbuildable", _UNBUILDABLE)
    cut = fit_trailer(clauses, beats, spoilers, budget, pi.cleanup, source_order=outline.format in _STORY_FORMATS)
    if not cut.usable:
        raise WorkflowError("trailer_unbuildable", _UNBUILDABLE)
    return cut


# -- entry point --------------------------------------------------------------------------------------


async def plan_trailer(pi: PlanInput) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    spec = pi.ctx.spec
    budget = trailer_budget(spec.clip_length, pi.setting("target_seconds"))
    await pi.ctx.progress(54, "Mapping the story...")
    clauses = await clause_units(pi)
    relaxed = pi.setting("spoilers") == "relaxed"
    spoilers: set[int] = set() if relaxed else _to_clause_ids(pi, clauses, spoiler_sentences(pi))
    story = pi.outline.outline.format in _STORY_FORMATS

    await pi.ctx.progress(58, "Writing the trailer...")
    draft, drafter = await _draft(pi, clauses, spoilers, budget)
    cut: Optional[TrailerCut] = None
    blocked = set(spoilers)
    survivors: list[TrailerBeatIn] = []
    if draft is not None:
        blocked = drafter.rules.blocked(draft)
        survivors = _beats_in(draft)
        cut = fit_trailer(clauses, survivors, blocked, budget, pi.cleanup, source_order=story)
        if not cut.usable:
            logger.info("Drafted trailer doesn't fit (%d beats, %.1fs)", len(cut.beats), cut.seconds)
            cut = await _rescue(pi, clauses, drafter, survivors, budget, blocked)
            draft = drafter.current
            blocked = drafter.rules.blocked(draft)
            survivors = _beats_in(draft)

    # Only a title the judge picked and the beats still say is burned in.
    title = ""
    await pi.ctx.progress(62, "Checking for spoilers...")
    if cut is not None:
        cut, blocked, title, survivors = await _check_spoilers(pi, clauses, cut, drafter, blocked, budget)
    if cut is None:
        logger.info("Building the trailer from what survived and the outline")
        pi.stats.fallbacks.append("TRAILER_OUTLINE_FALLBACK")
        cut, title = await _check_fallback(pi, clauses, drafter.current, blocked, budget, survivors)
    thread = _thread(drafter.current)
    if pi.setting("ending") == "question":
        cut = await _end_on_question(pi, clauses, cut, blocked, thread, budget, story)
    title = await _final_title(pi, clauses, cut, title, thread)
    # The central question is the curiosity the trailer sells: the title when
    # the draft has none, or nothing the beats say makes one.
    title = title or ai.sanitize_hook_title(pi.outline.outline.central_question)

    if pi.index.coarse:
        pi.note("COARSE_TRANSCRIPT")
    segments, plan = _segments(pi, clauses, cut, title or "", budget)
    played = {uid for beat in cut.beats for uid in range(beat.first, beat.last + 1)}
    if drafter.reclaimed & played:
        # The eval's spoiler check reads these: clauses set free on purpose, and only these.
        plan["reclaimed"] = sorted(drafter.reclaimed & played)
    if thread:
        plan["summary"] = thread
    return segments, plan


async def _end_on_question(
    pi: PlanInput, clauses: UnitIndex, cut: TrailerCut, blocked: set[int], thread: str, budget: Budget, story: bool
) -> TrailerCut:
    """The cut, ending on a question when the video has one that fits; else as it is, recorded in the stats."""
    if clauses.unit(cut.beats[-1].last).ends_question:
        return cut
    picked = await _pick_button(pi, clauses, cut, blocked, thread, questions_only=True)
    if picked is not None:
        beats = [
            TrailerBeatIn(beat.role, beat.first, beat.last, beat.why)
            for beat in cut.beats[:-1]
            if not (beat.first <= picked[1] and picked[0] <= beat.last)
        ]
        beats.append(TrailerBeatIn("button", *picked, why="hanging line"))
        refit = fit_trailer(clauses, beats, blocked, budget, pi.cleanup, source_order=story)
        if refit.usable and clauses.unit(refit.beats[-1].last).ends_question:
            return refit
    pi.stats.fallbacks.append("TRAILER_NO_QUESTION_ENDING")
    return cut


def _thread(draft: Optional[TrailerDraft]) -> str:
    return " ".join(draft.thread.split()) if draft is not None else ""


async def _rescue(
    pi: PlanInput,
    clauses: UnitIndex,
    drafter: _Drafter,
    keep: Sequence[TrailerBeatIn],
    budget: Budget,
    blocked: set[int],
) -> Optional[TrailerCut]:
    """One more call before the fallback: keep what survives and ask for what is missing.

    The new draft takes over when it fits; None when it still doesn't.
    """
    rules, draft = drafter.rules, drafter.current
    usable = [beat for beat in keep if not any(uid in blocked for uid in range(beat.first, beat.last + 1))]
    cut = fit_trailer(clauses, usable, blocked, budget, pi.cleanup, source_order=rules.story)
    missing = max(budget.min - cut.seconds, TRAILER_SHORT_BEAT_MIN_SECONDS)
    kept_lines = "\n".join(f"{beat.role} #{beat.first}–#{beat.last}" for beat in usable)
    prompt = (
        drafter.prompt
        + f"\n\nYOUR PREVIOUS ANSWER:\n{draft.model_dump_json()}\n\nThese beats survived:\n{kept_lines}\n\n"
        + f"After fitting, the trailer is {cut.seconds:.0f}s; it must be {budget.min:.0f}-{budget.max:.0f}s, open on the "
        + f"cold_open and end on the button. Keep these beats. Add what is missing: at least {missing:.0f}s on the "
        + "thread, or the missing cold_open or button. Return the complete corrected answer in the same format."
    )
    try:
        fixed = (await run(drafter.agent, prompt, stats=pi.stats, stage="trailer_rescue")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Trailer rescue failed (%s)", type(error).__name__)
        return None
    extra = blocked - rules.blocked(draft)
    rescued = fit_trailer(clauses, _beats_in(fixed), rules.blocked(fixed) | extra, budget, pi.cleanup, source_order=rules.story)
    if not rescued.usable:
        return None
    drafter.use(fixed)
    return rescued


async def _check_fallback(
    pi: PlanInput,
    clauses: UnitIndex,
    draft: Optional[TrailerDraft],
    blocked: set[int],
    budget: Budget,
    survivors: Sequence[TrailerBeatIn],
) -> tuple[TrailerCut, str]:
    """The fallback, judged like a drafted cut, and the title the judge picked ("" when none).

    A beat the judge catches leaking is blocked and the fallback rebuilt once;
    one that still leaks makes the trailer unbuildable rather than ship the
    ending. A title is never kept across a change of beats.
    """
    reveals = _reveals(pi, draft)
    titles = draft_titles(draft)
    thread = _thread(draft)
    cut = fallback_trailer(pi, clauses, blocked, budget, survivors)
    title = ""
    for attempt in range(2):
        heard = [item for item in titles if title_is_heard(item, clauses, cut)]
        verdict = await _judge(pi, clauses, cut, reveals, heard, thread)
        if verdict is None:
            return cut, ""
        title = _picked_title(verdict, heard)
        if not verdict.reveals_payoff:
            return cut, title
        position = verdict.offending_beat - 1
        if attempt or not 0 <= position < len(cut.beats):
            break
        leaked = cut.beats[position]
        blocked = blocked | set(range(leaked.first, leaked.last + 1))
        cut = fallback_trailer(pi, clauses, blocked, budget, survivors)
    raise WorkflowError("trailer_unbuildable", _UNBUILDABLE)


def _verdict_beat(verdict_beat: int, cut: TrailerCut) -> int:
    position = verdict_beat - 1
    return position if 0 <= position < len(cut.beats) else -1


async def _pick_button(
    pi: PlanInput, clauses: UnitIndex, cut: TrailerCut, blocked: set[int], thread: str, questions_only: bool = False
) -> Optional[tuple[int, int]]:
    """A better last line, chosen by the model from candidates across the video; None keeps the drafter's."""
    setups = _to_clause_ids(pi, clauses, setup_sentences(pi, spoiler_sentences(pi))) - blocked
    others = [(beat.first, beat.last) for beat in cut.beats[:-1]]
    candidates = [
        (low, high)
        for low, high in button_candidates(
            pi, clauses, blocked, setups, _shown_clauses(pi, clauses, blocked), limit=BUTTON_PICK_CANDIDATES,
            questions_only=questions_only or pi.setting("ending") == "question",
        )
        if not any(low <= span[1] and span[0] <= high for span in others)
    ]
    if not candidates:
        return None
    current = clauses.text(cut.beats[-1].first, cut.beats[-1].last)
    beats = "\n".join(f"- {clauses.text(beat.first, beat.last)}" for beat in cut.beats[:-1])
    lines = "\n".join(f"{number}. {clauses.text(low, high)}" for number, (low, high) in enumerate(candidates, start=1))
    prompt = with_instructions(
        f"The trailer's thread: {thread}\n\n<clip>\n{beats}\nCurrent last line: {current}\n</clip>\n\nCandidates:\n{lines}",
        pi.instructions,
    )
    agent = build_agent(ButtonPick, trailer_button_system(pi.language_name), structural=True)
    try:
        picked = (await run(agent, prompt, stats=pi.stats, stage="trailer_button")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Trailer button pick failed (%s)", type(error).__name__)
        return None
    return candidates[picked.candidate - 1] if 1 <= picked.candidate <= len(candidates) else None


async def _check_spoilers(
    pi: PlanInput,
    clauses: UnitIndex,
    cut: TrailerCut,
    drafter: _Drafter,
    blocked: set[int],
    budget: Budget,
) -> tuple[Optional[TrailerCut], set[int], str, list[TrailerBeatIn]]:
    """The judged cut, re-fitted once after a problem, redrafted once when it fails to hook.

    Returns the cut (None when it stays unsafe or can't fit), the blocked
    units, grown by any beat the judge caught leaking, so the fallback never
    reuses a line that gives the ending away, the title the judge picked ("" when
    no candidate qualifies or nobody could check), and the beats that survived.
    A weak button alone never sends a trailer to the fallback, which is worse.
    """
    draft = drafter.current
    thread = _thread(draft)
    reveals = _reveals(pi, draft)
    titles = [item for item in draft_titles(draft) if title_is_heard(item, clauses, cut)]
    verdict = await _judge(pi, clauses, cut, reveals, titles, thread)
    survivors = _beats_in(draft)
    if verdict is None:
        return cut, blocked, "", survivors
    title = _picked_title(verdict, titles)
    current: TrailerCut = cut
    current_verdict = verdict
    source_order = drafter.rules.story

    def needs_refit(judged: SpoilerVerdict, judged_cut: TrailerCut) -> bool:
        return (
            judged.reveals_payoff
            or not judged.button_unresolved
            or _verdict_beat(judged.chopped_beat, judged_cut) >= 0
            or _verdict_beat(judged.unclear_beat, judged_cut) >= 0
        )

    if needs_refit(verdict, cut):
        beats = [TrailerBeatIn(beat.role, beat.first, beat.last, beat.why) for beat in cut.beats]
        removed: set[int] = set()
        if verdict.reveals_payoff:
            leak = _verdict_beat(verdict.offending_beat, cut)
            if leak < 0:
                return None, blocked, title, survivors
            offending = cut.beats[leak]
            blocked = blocked | set(range(offending.first, offending.last + 1))
            removed.add(leak)
        # A beat that sounds broken or can't be followed goes; its units stay usable.
        for position in (_verdict_beat(verdict.chopped_beat, cut), _verdict_beat(verdict.unclear_beat, cut)):
            if position >= 0:
                removed.add(position)
        kept = [beat for position, beat in enumerate(beats) if position not in removed]
        survivors = kept
        picked: Optional[tuple[int, int]] = None
        if not verdict.button_unresolved and kept and kept[-1].role == "button":
            picked = await _pick_button(pi, clauses, cut, blocked, thread)
            if picked is not None:
                kept = [beat for beat in kept[:-1] if not (beat.first <= picked[1] and picked[0] <= beat.last)]
                kept.append(TrailerBeatIn("button", *picked, why="hanging line"))
                survivors = kept
        if not removed and picked is None:
            pi.stats.fallbacks.append("TRAILER_WEAK_BUTTON")  # nothing better to end on: same cut, no second reading
        else:
            refit = fit_trailer(clauses, kept, blocked, budget, pi.cleanup, source_order=source_order)
            if not refit.usable:
                rescued = await _rescue(pi, clauses, drafter, kept, budget, blocked)
                if rescued is None:
                    return None, blocked, title, survivors
                blocked = blocked | drafter.rules.blocked(drafter.current)
                refit = rescued
                thread = _thread(drafter.current)
                reveals = _reveals(pi, drafter.current)
            pi.stats.critique_fixes += 1
            second = await _judge(pi, clauses, refit, reveals, (title,) if title else (), thread)
            if second is None:
                return refit, blocked, title, survivors
            if title and second.best_title != 1:
                title = ""  # the refit made the cleared title tell the ending, or stop delivering
            if second.reveals_payoff:
                position = _verdict_beat(second.offending_beat, refit)
                if position >= 0:
                    leaked = refit.beats[position]
                    blocked = blocked | set(range(leaked.first, leaked.last + 1))
                    survivors = [beat for beat in survivors if not (beat.first <= leaked.last and leaked.first <= beat.last)]
                return None, blocked, title, survivors
            if not second.button_unresolved:
                logger.info("Trailer button still doesn't hang after the refit; keeping the cut")
                pi.stats.fallbacks.append("TRAILER_WEAK_BUTTON")
            current, current_verdict = refit, second
            repeat = [
                position
                for position in (_verdict_beat(second.chopped_beat, refit), _verdict_beat(second.unclear_beat, refit))
                if position >= 0 and 0 < position < len(refit.beats) - 1
            ]
            if repeat:
                # No third reading: a second complaint about a middle beat removes it, if the cut survives that.
                lean = fit_trailer(
                    clauses,
                    [
                        TrailerBeatIn(beat.role, beat.first, beat.last, beat.why)
                        for position, beat in enumerate(refit.beats)
                        if position not in repeat
                    ],
                    blocked,
                    budget,
                    pi.cleanup,
                    source_order=source_order,
                )
                if lean.usable:
                    current = lean
    current, title, survivors = await _taste_gate(
        pi, clauses, current, current_verdict, drafter, blocked, budget, reveals, title, thread, survivors
    )
    return current, blocked, title, survivors


async def _taste_gate(
    pi: PlanInput,
    clauses: UnitIndex,
    cut: TrailerCut,
    verdict: SpoilerVerdict,
    drafter: _Drafter,
    blocked: set[int],
    budget: Budget,
    reveals: Sequence[str],
    title: str,
    thread: str,
    survivors: list[TrailerBeatIn],
) -> tuple[TrailerCut, str, list[TrailerBeatIn]]:
    """One guided redraft when the viewer isn't hooked; the cut with the higher curiosity wins, never a lower one."""
    if verdict.curiosity >= CURIOSITY_BAR and verdict.thread_holds:
        return cut, title, survivors
    weakest = _verdict_beat(verdict.weakest_beat, cut)
    replace_line = f" Replace beat {weakest + 1}." if weakest >= 0 else ""
    beats = "\n".join(
        f"{number}. {beat.role} #{beat.first}–#{beat.last}: {clauses.text(beat.first, beat.last)}"
        for number, beat in enumerate(cut.beats, start=1)
    )
    prompt = (
        drafter.prompt
        + f"\n\nYOUR PREVIOUS TRAILER:\n{beats}\n\nA viewer who never saw the video said (curiosity {verdict.curiosity}/100): "
        + f"{verdict.reasoning.strip()}{replace_line} Make every beat serve one thread. Return the complete answer in the same format."
    )
    try:
        redrafted = (await run(drafter.agent, prompt, stats=pi.stats, stage="trailer_redraft")).output
    except Exception as error:
        if is_cancellation(error):
            raise
        logger.warning("Trailer redraft failed (%s)", type(error).__name__)
        return cut, title, survivors
    rules = drafter.rules
    new_blocked = rules.blocked(redrafted) | (blocked - rules.blocked(drafter.current))
    beats_in = _beats_in(redrafted)
    second = fit_trailer(clauses, beats_in, new_blocked, budget, pi.cleanup, source_order=rules.story)
    if not second.usable:
        return cut, title, survivors
    titles = [item for item in clean_titles(redrafted.title_candidates) if title_is_heard(item, clauses, second)]
    judged = await _judge(pi, clauses, second, reveals, titles, " ".join(redrafted.thread.split()) or thread)
    if judged is None or judged.reveals_payoff or judged.curiosity <= verdict.curiosity:
        return cut, title, survivors
    drafter.use(redrafted)
    pi.stats.critique_fixes += 1
    return second, _picked_title(judged, titles), beats_in


def _segments(
    pi: PlanInput, clauses: UnitIndex, cut: TrailerCut, title: str, budget: Budget
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranges: list[tuple[float, float]] = []
    meta_beats: list[dict[str, Any]] = []
    plan_beats: list[dict[str, Any]] = []
    for beat in cut.beats:
        beat_ranges = clauses.ranges_for(beat.first, beat.last)
        # One entry per range: a long silence inside a beat splits it.
        for span in beat_ranges:
            meta_beats.append({"role": beat.role, "range_index": len(ranges), "output_start": None})
            ranges.append(span)
        plan_beats.append({"role": beat.role, "ranges": rounded_ranges(beat_ranges), "output_start": None, "why": beat.why})
    segment = planned_segment(
        ranges=ranges,
        junction_fades=[TRAILER_JUNCTION_FADE] * (len(ranges) - 1),
        max_seconds=budget.render_cap,
        required=True,
        text=" … ".join(clauses.text(beat.first, beat.last) for beat in cut.beats),
        hook_title=compose_title(title),
        reasoning=pi.outline.outline.central_question,
        workflow_role="trailer",
        meta={"beats": meta_beats},
    )
    plan = base_plan(pi, title=title)
    plan["beats"] = plan_beats
    plan["seconds"] = cut.seconds
    return [segment], plan
