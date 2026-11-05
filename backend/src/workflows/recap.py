"""Recap planner: one clip that covers the video's main points, in order.

Draft (spine, opener, points with full and core spans and setups, closer) →
deterministic assembly to the length window (``assembly.fit_recap``) → a final
cut read by someone who never saw the video, who returns the complete cut:
what it returns plays exactly as written, if it validates. Title and reasoning
are written last, from what plays. The recap is exactly one output, always
inside its window.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Optional, Sequence

from .. import ai
from .assembly import (
    RECAP_JUNCTION_FADE,
    RECAP_OPENER_MAX_SECONDS,
    RECAP_POINT_MAX_SECONDS,
    RECAP_STORY_POINT_MAX_SECONDS,
    Beat,
    RecapCut,
    RecapDraftIn,
    RecapPointIn,
    beat_seconds,
    beat_spans,
    coverage_slices,
    fit_recap,
    missing_slices,
    recap_beat_cap,
    recap_seconds,
)
from .boundaries import STRONG_BOUNDARY
from .durations import Budget, recap_budget
from .errors import WorkflowError
from .llm import build_agent, is_cancellation, run_with_repair
from .outline import chapter_ranges
from .planning import PlanInput, base_plan, planned_segment, rounded_ranges
from .language import is_acknowledgement
from .prompts import (
    outline_block,
    recap_draft_system,
    recap_final_cut_system,
    transcript_block,
    with_instructions,
)
from .schemas import RecapDraft, RecapFinalCut
from .titles import compose_title, said_in, title_words
from .units import UnitIndex, format_clock

logger = logging.getLogger(__name__)

POINT_MIN_SECONDS = 6.0
FRAMING_LABELS = frozenset({"Intro", "Takeaway"})
CORE_MIN_SECONDS = 4.0
# A setup is a short earlier sentence that names what the core leans on.
SETUP_MAX_SECONDS = 8.0
SETUP_SEARCH_SECONDS = 90.0
RECAP_OPENER_MIN_SECONDS = 4.0
# A ★5 chapter needs about this much of the recap to be covered at all.
STAR5_POINT_SECONDS = 8.0
REASONING_MAX_CHARS = 300
# A line this short can't be a word-for-word repeat worth cutting at ("Minutes?" / "Minutes.").
REPEAT_MIN_WORDS = 4
# A beat doesn't end on a reply this short.
TAIL_MIN_WORDS = 3
# Under each beat the final cut sees this much of what doesn't play.
FINAL_CUT_UNITS_BEFORE = 5
FINAL_CUT_UNITS_AFTER = 3
FINAL_CUT_MAX_CANDIDATES = 12
# A setup plays as its own beat in the final cut, so it may run a little over the selection's cap.
FINAL_CUT_EXTRA_BEATS = 2
# Sources shorter than this may recap in two points.
SHORT_SOURCE_SECONDS = 480.0
LISTING_CONTEXT_UNITS = 2
CHAPTER_EDGE_UNITS = 3
LABEL_MAX_WORDS = 6
LABEL_MAX_CHARS = 48
# A range edge inside a sentence moves to the sentence's edge at most this far.
EDGE_SNAP_UNITS = 2
# Intro, outro and banter chapters rated above this carry substance and may be recapped.
FRAMING_MAX_IMPORTANCE = 2
_FRAMING_KINDS = {"intro", "outro", "banter"}

# Openers that talk about the video instead of its content.
_SIGNPOST_RE = {
    "en": re.compile(
        r"\b(this|today's) (video|episode|presentation|chapter|talk)\b|\bwhat i want to do\b|"
        r"\bi'm going to (show|walk|go)\b|\bi'll walk you through\b|\bfollowing chapters\b|\bin this video\b",
        re.IGNORECASE,
    ),
    "de": re.compile(r"\bin diese[mrn]? (video|folge|vortrag|kapitel)\b|\bheute zeige ich\b", re.IGNORECASE),
}
_NUMBER_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
    "zwei": 2, "drei": 3, "vier": 4, "fünf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
}
_LIST_NOUNS = (
    "habits|lessons|reasons|tips|ways|things|steps|rules|consequences|mistakes|secrets|ideas|questions|"
    "gründe|dinge|tipps|regeln|schritte|folgen|fehler|punkte|lektionen"
)
# "seven biggest habits", "3 reasons", "zwei Gründe": a count the recap must keep.
COUNT_RE = re.compile(
    r"\b(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\s+(?:[\w$,]+\s+){0,2}?(" + _LIST_NOUNS + r")\b",
    re.IGNORECASE,
)
# An announced list ("there are 2 consequences", "the first is") needs its next item.
_ANNOUNCE_RE = re.compile(
    r"\b(there are|there's|i have|we have|es gibt)\s+(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\b|\bthe first (is|one)\b",
    re.IGNORECASE,
)
_NEXT_ITEM_RE = re.compile(r"\b(second|next|finally|another|zweitens|zweite[nrs]?|außerdem)\b", re.IGNORECASE)

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


def framing_units(pi: PlanInput) -> dict[int, str]:
    """Units in sponsor reads, and in intros, outros and banter without substance, by chapter kind.

    A speaker's own summary of the video is the opposite of framing, wherever it sits.
    """
    outline = pi.outline.outline
    framing: dict[int, str] = {}
    for chapter, (first, last) in zip(outline.chapters, chapter_ranges(outline, pi.index)):
        if chapter.kind == "sponsor" or (
            chapter.kind in _FRAMING_KINDS and chapter.importance <= FRAMING_MAX_IMPORTANCE
        ):
            framing.update({uid: chapter.kind for uid in range(first, last + 1)})
    for span in outline.self_summaries:
        for uid in range(span.first_unit, span.last_unit + 1):
            if framing.get(uid) != "sponsor":
                framing.pop(uid, None)
    return framing


def _mostly_framing(framing: dict[int, str], first: int, last: int) -> Optional[str]:
    """The framing kind when most of ``first..last`` is a sponsor read, intro, outro or banter."""
    low, high = sorted((first, last))
    kinds = [framing[uid] for uid in range(low, high + 1) if uid in framing]
    if not kinds or 2 * len(kinds) <= high - low + 1:
        return None
    return max(set(kinds), key=kinds.count)


def announced_count(text: str) -> int:
    """The count a line announces ("seven biggest habits" is 7), else 0."""
    match = COUNT_RE.search(text or "")
    if not match:
        return 0
    token = match.group(1).casefold()
    return int(token) if token.isdigit() else _NUMBER_WORDS.get(token, 0)


def signpost(text: str, language: Optional[str]) -> bool:
    """A line about the video itself ("In this video I'll walk you through...")."""
    pattern = _SIGNPOST_RE.get((language or "")[:2])
    return bool(pattern and pattern.search(text or ""))


def named_chapter(chapter: Any, text_words: list[str]) -> bool:
    """Whether a passage says what a chapter is about: a word of its title, literally."""
    return any(said_in(word, text_words) for word in title_words(chapter.title))


def unfinished_list(index: UnitIndex, first: int, last: int) -> Optional[int]:
    """The unit announcing a list inside ``first..last`` that never reaches its next item, else None."""
    for uid in range(first, last + 1):
        if _ANNOUNCE_RE.search(index.unit(uid).text):
            if not any(_NEXT_ITEM_RE.search(index.unit(later).text) for later in range(uid + 1, last + 1)):
                return uid
    return None


class _DraftRules:
    def __init__(self, pi: PlanInput, shown: Optional[Sequence[int]], budget: Optional[Budget] = None):
        self.index = pi.index
        self.cleanup = pi.cleanup
        self.shown = set(shown) if shown is not None else None
        self.min_points = 2 if pi.ctx.source_seconds < SHORT_SOURCE_SECONDS else 3
        self.framing = framing_units(pi)
        self.budget = budget
        self.language = pi.language
        outline = pi.outline.outline
        self.format = outline.format
        self.point_max = RECAP_STORY_POINT_MAX_SECONDS if outline.format in ("story", "vlog") else RECAP_POINT_MAX_SECONDS
        self.arcs = list(outline.story_arcs)
        self.summaries = list(outline.self_summaries)
        self.chapters = [
            (number, chapter, span)
            for number, (chapter, span) in enumerate(zip(outline.chapters, chapter_ranges(outline, pi.index)), start=1)
        ]
        self.slices = _slices(pi, budget) if budget is not None else ()
        self.max_points = pi.setting("max_points")

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
            kind = _mostly_framing(self.framing, *full)
            if kind is not None:
                issues.append(f"Point {number} (#{full[0]}–#{full[1]}) is in a {kind} chapter; recap the substance instead.")
            if not (full[0] <= core[0] and core[1] <= full[1]):
                issues.append(f"Point {number}: the core #{core[0]}–#{core[1]} must lie inside #{full[0]}–#{full[1]}.")
            seconds = self._seconds(*full)
            if not POINT_MIN_SECONDS <= seconds <= self.point_max:
                issues.append(f"Point {number} is {seconds:.0f}s; its full range must be 6-{self.point_max:.0f}s.")
            core_seconds = self._seconds(*core)
            if core_seconds < CORE_MIN_SECONDS:
                issues.append(f"Point {number}'s core is {core_seconds:.1f}s; it must be at least 4s.")
            issues += self._setup_and_subject(number, point, core)
            valid_spans.append((number, full, core))
        for (previous, before, _), (number, after, _) in zip(valid_spans, valid_spans[1:]):
            if after[0] < before[0]:
                issues.append(f"Points must be in video order: point {number} (#{after[0]}) comes before point {previous} (#{before[0]}).")
            elif after[0] <= before[1]:
                issues.append(f"Points {previous} and {number} overlap; each unit belongs to one point.")
        if len(points) < self.min_points:
            issues.append(f"You gave {len(points)} points; give at least {self.min_points}.")
        if self.max_points is not None and len(points) > self.max_points + FINAL_CUT_EXTRA_BEATS:
            issues.append(f"You gave {len(points)} points; the creator wants at most {self.max_points}.")
        for name, span in (("opener", draft.opener), ("closer", draft.closer)):
            if span.first_unit < 1 and span.last_unit < 1:
                continue
            if not (self._valid(span.first_unit) and self._valid(span.last_unit)) or span.first_unit > span.last_unit:
                issues.append(f"The {name} #{span.first_unit}–#{span.last_unit} is not a valid range from the transcript you were given (use -1/-1 for none).")
                continue
            kind = _mostly_framing(self.framing, span.first_unit, span.last_unit)
            if kind is not None:
                issues.append(f"The {name} (#{span.first_unit}–#{span.last_unit}) is in a {kind} chapter; use a line with substance or -1/-1.")
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
        if not 0 <= draft.opener_answered_by <= len(points):
            issues.append(f"opener_answered_by is {draft.opener_answered_by}; give a point's number, or 0.")
        issues += self._editorial(draft, valid_spans)
        return issues

    def _setup_and_subject(self, number: int, point: Any, core: tuple[int, int]) -> list[str]:
        """The setup is short and just before the core, and the point's subject is actually said."""
        index, issues = self.index, []
        setup = setup_span(point)
        heard = index.text(*core).casefold()
        if setup is not None:
            if not (self._valid(setup[0]) and self._valid(setup[1])) or setup[0] > setup[1]:
                issues.append(f"Point {number}'s setup #{setup[0]}–#{setup[1]} is not a valid range (use -1/-1 for none).")
                setup = None
            elif setup[1] >= core[0]:
                issues.append(f"Point {number}'s setup must come before its core.")
                setup = None
            else:
                seconds = self._seconds(*setup)
                if seconds > SETUP_MAX_SECONDS:
                    issues.append(f"Point {number}'s setup is {seconds:.0f}s; it must be at most 8s.")
                if index.unit(core[0]).start - index.unit(setup[1]).end > SETUP_SEARCH_SECONDS:
                    issues.append(f"Point {number}'s setup must come from the 90s before its core.")
                heard = index.text(*setup).casefold() + " " + heard
        subject = " ".join((point.subject or "").casefold().split())
        if subject and subject not in " ".join(heard.split()):
            says = self._unit_saying(subject, core[0])
            hint = f" #{says} says them: use it as the setup, or choose another line." if says else " Choose a line that says them."
            issues.append(f'Point {number} is about "{point.subject.strip()}" but those words never play.{hint}')
        return issues

    def _unit_saying(self, subject: str, before: int) -> Optional[int]:
        """The nearest unit in the 90 s before ``before`` that says the subject word for word."""
        index = self.index
        start = index.unit(before).start
        for uid in range(before - 1, 0, -1):
            unit = index.unit(uid)
            if start - unit.end > SETUP_SEARCH_SECONDS:
                return None
            if subject in " ".join(unit.text.casefold().split()):
                return uid
        return None

    def _editorial(self, draft: RecapDraft, spans: list[tuple[int, tuple[int, int], tuple[int, int]]]) -> list[str]:
        """What makes the recap play well: a real opener, kept promises, coverage, enough material."""
        index, issues = self.index, []
        opener = draft.opener
        if opener.first_unit >= 1 and self._valid(opener.first_unit) and self._valid(opener.last_unit) and opener.first_unit <= opener.last_unit:
            text = index.text(opener.first_unit, opener.last_unit)
            seconds = self._seconds(opener.first_unit, opener.last_unit)
            if signpost(text, self.language):
                issues.append(
                    f"The opener (#{opener.first_unit}–#{opener.last_unit}) talks about the video instead of its content; "
                    "use the central question or the most striking claim, or -1/-1."
                )
            elif seconds < RECAP_OPENER_MIN_SECONDS:
                issues.append(f"The opener is {seconds:.1f}s; it must be a complete sentence of at least 4s, or -1/-1.")
            count = announced_count(text)
            if count > len(draft.points):
                issues.append(
                    f"The opener announces {count} items but you give {len(draft.points)} points; choose an opener without a count."
                )
        closer = draft.closer
        if closer.first_unit >= 1 and self._valid(closer.first_unit) and self._valid(closer.last_unit) and closer.first_unit <= closer.last_unit:
            if signpost(index.text(closer.first_unit, closer.last_unit), self.language):
                issues.append(
                    "The closer talks about the video; use a line that lands the conclusion, or -1/-1."
                )
        for number, _, core in spans:
            announcing = unfinished_list(index, *core)
            if announcing is not None:
                issues.append(
                    f"Point {number}'s core announces a list at #{announcing} but stops before the next item; "
                    "extend the core to the second item or end it before the announcement."
                )
        if self.budget is None:
            return issues
        covered = [full for _, full, _ in spans]
        # A chapter the speaker's own summary names is covered when that summary plays.
        summary_words = [
            word
            for span in self.summaries
            if any(span.first_unit <= full[1] and full[0] <= span.last_unit for full in covered)
            or (closer.first_unit >= 1 and span.first_unit <= closer.last_unit and closer.first_unit <= span.last_unit)
            for word in title_words(index.text(span.first_unit, span.last_unit))
        ]
        missing = [
            (number, chapter, span)
            for number, chapter, span in self.chapters
            if chapter.importance >= 5
            and chapter.kind in ("content", "story", "qa")
            and not any(full[0] <= span[1] and span[0] <= full[1] for full in covered)
            and not named_chapter(chapter, summary_words)
            and (self.shown is None or any(uid in self.shown for uid in range(span[0], span[1] + 1)))
        ]
        star5 = sum(1 for _, chapter, _ in self.chapters if chapter.importance >= 5)
        if missing and star5 * STAR5_POINT_SECONDS <= self.budget.max:
            for number, chapter, (first, last) in missing[:3]:
                times = f"{format_clock(index.unit(first).start)}-{format_clock(index.unit(last).end)}"
                issues.append(f"Chapter C{number} (★5, {times}) has no point; add its clearest statement.")
        issues += self._format_coverage(covered)
        if self.slices:
            for low, high in missing_slices(self.slices, covered):
                if self.shown is None or any(uid in self.shown for uid in range(low, high + 1)):
                    times = f"{format_clock(index.unit(low).start)}-{format_clock(index.unit(high).end)}"
                    issues.append(f"Your points leave out {times} (#{low}–#{high}); add its clearest statement.")
        total = sum(self._seconds(*full) for _, full, _ in spans)
        for span in (draft.opener, draft.closer):
            if span.first_unit >= 1 and self._valid(span.first_unit) and self._valid(span.last_unit) and span.first_unit <= span.last_unit:
                total += self._seconds(span.first_unit, span.last_unit)
        if spans and total < self.budget.min:
            uncovered = [
                f"C{number}"
                for number, chapter, span in self.chapters
                if chapter.importance >= 4 and not any(full[0] <= span[1] and span[0] <= full[1] for full in covered)
            ]
            hint = f" Add points from {', '.join(uncovered[:4])}." if uncovered else " Add more points."
            issues.append(
                f"Your points, opener and closer total {total:.0f}s at their fullest; the recap must be at least "
                f"{self.budget.min:.0f}s.{hint}"
            )
        return issues


    def _format_coverage(self, covered: list[tuple[int, int]]) -> list[str]:
        """A bulletin recap plays every story; a story recap plays its beginning, middle and end."""
        index, issues = self.index, []

        def touched(span: tuple[int, int]) -> bool:
            return any(full[0] <= span[1] and span[0] <= full[1] for full in covered)

        if self.format == "news":
            stories = [
                (number, span)
                for number, chapter, span in self.chapters
                if chapter.kind in ("content", "story") and chapter.importance >= 3
                and (self.shown is None or any(uid in self.shown for uid in range(span[0], span[1] + 1)))
            ]
            if len(stories) * STAR5_POINT_SECONDS <= self.budget.max:
                for number, _ in [(number, span) for number, span in stories if not touched(span)][:3]:
                    issues.append(f"Chapter C{number} is a story of this bulletin with no point; give each story one point.")
        elif self.format in ("story", "vlog") and self.arcs:
            arc = max(self.arcs, key=lambda item: item.strength)
            first, last = sorted((index.clamp_id(arc.first_unit), index.clamp_id(arc.last_unit)))
            start, end = index.unit(first).start, index.unit(last).end
            names = ("beginning", "middle", "end")
            for third, name in enumerate(names):
                low = index.unit_at(start + third * (end - start) / 3)
                high = index.unit_at(start + (third + 1) * (end - start) / 3 - 0.001)
                if not touched((low, high)) and (self.shown is None or any(u in self.shown for u in range(low, high + 1))):
                    issues.append(
                        f"Your points leave out the {name} of the story (#{low}–#{high}); add a plot beat from it."
                    )
        return issues

def _slices(pi: PlanInput, budget: Budget) -> tuple[tuple[int, int], ...]:
    """The stretches of the video this recap has to touch; none when the creator wants best moments only."""
    if pi.setting("coverage") != "whole_video":
        return ()
    return coverage_slices(pi.index, pi.outline.outline, framing_units(pi), budget)


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


def setup_span(point: Any) -> Optional[tuple[int, int]]:
    """A point's setup as a range, None when it has none."""
    if point.setup_first < 1 or point.setup_last < 1:
        return None
    return point.setup_first, point.setup_last


def _main_speaker(index: UnitIndex, first: int, last: int) -> Optional[str]:
    speech: dict[Optional[str], float] = {}
    for uid in range(first, last + 1):
        unit = index.unit(uid)
        speech[unit.speaker] = speech.get(unit.speaker, 0.0) + unit.end - unit.start
    return max(speech, key=speech.get)


def trim_tail(index: UnitIndex, first: int, last: int) -> int:
    """``last`` moved back past a reply, an echo or another speaker's aside, onto a strong end.

    A question the point itself goes on to answer never reaches here: this only
    looks at the very end.
    """
    speaker = _main_speaker(index, first, last)
    end = last
    while end > first:
        unit = index.unit(end)
        short = len(unit.text.split()) < TAIL_MIN_WORDS and not index.no_space
        other = unit.speaker != speaker
        if not (short or other or is_acknowledgement(unit.text, index.language)):
            break
        end -= 1
    return end if end != last and index.boundary_strength(end) >= STRONG_BOUNDARY else last


def _draft_in(index: UnitIndex, draft: RecapDraft, framing: Optional[dict[int, str]] = None) -> RecapDraftIn:
    """The draft for the assembler, every range starting and ending on whole sentences.

    Ranges that are mostly a sponsor read, an empty intro or outro, or banter
    are left out (the repair round already asked for substance instead),
    unless that would leave no point at all.
    """
    count = len(index)
    framing = framing or {}

    def framed(value: Any) -> bool:
        return _mostly_framing(framing, index.clamp_id(value.first_unit), index.clamp_id(value.last_unit)) is not None

    def span(value: Any) -> Optional[tuple[int, int]]:
        if value.first_unit < 1 or value.last_unit < 1 or framed(value):
            return None
        first, last = sorted((index.clamp_id(value.first_unit), index.clamp_id(value.last_unit)))
        return _snap(index, first, last, 1, count)

    usable = sorted(
        (point for point in draft.points if point.first_unit >= 1 and point.last_unit >= 1),
        key=lambda point: min(point.first_unit, point.last_unit),
    )
    substantive = [point for point in usable if not framed(point)]
    if substantive:
        usable = substantive
    answering = (
        draft.points[draft.opener_answered_by - 1] if 1 <= draft.opener_answered_by <= len(draft.points) else None
    )
    points: list[RecapPointIn] = []
    for position, point in enumerate(usable):
        first, last = sorted((index.clamp_id(point.first_unit), index.clamp_id(point.last_unit)))
        floor = points[-1].last + 1 if points else 1
        following = usable[position + 1] if position + 1 < len(usable) else None
        ceiling = min(following.first_unit, following.last_unit) - 1 if following else count
        first, last = _snap(index, first, last, min(floor, first), max(ceiling, last))
        last = _without_repeats(index, first, last)
        last = trim_tail(index, first, last)
        core_first, core_last = sorted((index.clamp_id(point.core_first), index.clamp_id(point.core_last)))
        core_last = min(core_last, last)
        if point.core_first >= 1 and first <= core_first <= core_last <= last:
            core_first, core_last = _snap(index, core_first, core_last, first, last)
            core_last = trim_tail(index, core_first, core_last)
        else:
            core_first, core_last = first, last
        setup = setup_span(point)
        if setup is not None:
            low, high = sorted((index.clamp_id(setup[0]), index.clamp_id(setup[1])))
            # A setup never replays the point before it, and is pointless when the core already starts there.
            setup = (low, high) if floor <= low and high < first else None
        points.append(
            RecapPointIn(
                label=_label(point.label),
                first=first,
                last=last,
                core_first=core_first,
                core_last=core_last,
                importance=point.importance,
                setup=setup,
                answers_opener=point is answering,
            )
        )
    return RecapDraftIn(title=draft.title, opener=span(draft.opener), points=points, closer=span(draft.closer))


def _normalized(text: str) -> str:
    return re.sub(r"[^\w]+", " ", (text or "").casefold()).strip()


def _without_repeats(index: UnitIndex, first: int, last: int) -> int:
    """``last`` moved back before the first unit that repeats an earlier unit of the span word for word.

    Short lines don't count: an echo ("Minutes?" / "Minutes.") is conversation, not a repeat.
    """
    seen: set[str] = set()
    for uid in range(first, last + 1):
        text = _normalized(index.unit(uid).text)
        if len(text.split()) < REPEAT_MIN_WORDS:
            continue
        if text in seen and uid > first:
            return uid - 1
        seen.add(text)
    return last


def _label(text: str) -> str:
    return compose_title(text, max_chars=LABEL_MAX_CHARS, max_words=LABEL_MAX_WORDS) or ""


async def _draft(pi: PlanInput, budget: Budget) -> RecapDraft:
    index, outline = pi.index, pi.outline.outline
    shown = _shown_units(pi)
    rules = _DraftRules(pi, shown, budget)
    system = recap_draft_system(pi.language_name, budget, outline.central_question, outline.format)
    prompt = with_instructions(
        outline_block(outline, index) + "\n\n" + transcript_block(index.listing(shown)), pi.instructions
    )
    summaries = pi.outline.outline.self_summaries
    if summaries:
        prompt += "\n\n<self_summary>\n" + "\n".join(
            f"#{span.first_unit}–#{span.last_unit}: {index.text(span.first_unit, span.last_unit)}" for span in summaries
        ) + "\n</self_summary>"
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


def _listing_lines(index: UnitIndex, first: int, last: int, indent: str = "   ") -> list[str]:
    if last < first:
        return []
    return [indent + line for line in index.listing(range(first, last + 1)).splitlines()]


def final_cut_prompt(pi: PlanInput, cut: RecapCut) -> str:
    """Every beat with what surrounds it, the total, and what was left out."""
    index, outline = pi.index, pi.outline.outline
    lines: list[str] = []
    for number, beat in enumerate(cut.beats, start=1):
        seconds = beat_seconds(index, beat, pi.cleanup)
        lines.append(f"Beat {number}: {_beat_name(beat)} ({seconds:.1f}s)")
        spans = beat_spans(beat)
        before_first = spans[0][0]
        before = _listing_lines(index, max(1, before_first - FINAL_CUT_UNITS_BEFORE), before_first - 1)
        if before:
            lines += ["  before (does not play):"] + before
        lines.append("  PLAYS:")
        for low, high in spans:
            lines += _listing_lines(index, low, high)
        after = _listing_lines(index, beat.last + 1, min(len(index), beat.last + FINAL_CUT_UNITS_AFTER))
        if after:
            lines += ["  after (does not play):"] + after
    lines.append(f"Total: {recap_seconds(index, cut.beats, pi.cleanup):.1f}s")
    sections = ["<clip>\n" + "\n".join(lines) + "\n</clip>"]
    played = [span for beat in cut.beats for span in beat_spans(beat)]

    def plays(first: int, last: int) -> bool:
        return any(first <= high and low <= last for low, high in played)

    unused: list[str] = []
    for number, point in enumerate(outline.main_points, start=1):
        if plays(point.first_unit, point.last_unit):
            continue
        seconds = index.speech_seconds(point.core_first, point.core_last, (), pi.cleanup)
        unused.append(f"- #{point.core_first}–#{point.core_last} ({seconds:.1f}s) P{number} ★{point.importance} {point.statement}")
    if cut.pool is not None:
        for candidate in cut.pool.candidates:
            if plays(candidate.beat.core[0], candidate.beat.core[1]) or candidate.beat.from_outline:
                continue
            low, high = candidate.beat.core
            seconds = index.speech_seconds(low, high, (), pi.cleanup)
            unused.append(f"- #{low}–#{high} ({seconds:.1f}s) {candidate.beat.label}")
    for span in outline.self_summaries:
        if not plays(span.first_unit, span.last_unit):
            seconds = index.speech_seconds(span.first_unit, span.last_unit, (), pi.cleanup)
            unused.append(f"- #{span.first_unit}–#{span.last_unit} ({seconds:.1f}s) the speaker sums the video up")
    if unused:
        sections.append("Unused candidates:\n" + "\n".join(unused[:FINAL_CUT_MAX_CANDIDATES]))
    if cut.attention:
        sections.append(
            "Check especially beat(s) "
            + ", ".join(str(number) for number in cut.attention)
            + ": they open on a reference to something earlier."
        )
    return with_instructions("\n\n".join(sections), pi.instructions)


class _FinalCutRules:
    """What makes a returned cut playable: valid ids, whole sentences, no overlap, inside the window."""

    def __init__(self, pi: PlanInput, cut: RecapCut, budget: Budget):
        self.pi = pi
        self.index = pi.index
        self.cut = cut
        self.budget = budget
        self.framing = framing_units(pi)
        played = [span for beat in cut.beats for span in beat_spans(beat)]
        # A stretch the assembled cut reaches stays reached; one it couldn't reach isn't asked of the reader.
        self.slices = [part for part in _slices(pi, budget) if not missing_slices([part], played)]
        self.max_points = pi.setting("max_points")

    def beats(self, final: RecapFinalCut) -> list[Beat]:
        """The returned beats as a recap; a mid-sentence edge moves onto its sentence, or the beat stays broken."""
        index, count = self.index, len(self.index)
        beats: list[Beat] = []
        total = len(final.beats)
        for position, entry in enumerate(final.beats):
            if not (1 <= entry.first_unit <= count and 1 <= entry.last_unit <= count) or entry.first_unit > entry.last_unit:
                continue
            first, last = _snap(index, entry.first_unit, entry.last_unit, 1, count)
            origin = self._origin(first, last)
            kind = "point"
            label = _label(entry.label) or (origin.label if origin is not None else "")
            # "Intro" means the start of the recap. Anywhere else it would burn in
            # as the name of a point, so the beat has to say what it is instead.
            if label in FRAMING_LABELS:
                label = ""
            importance = origin.importance if origin is not None else 2
            if origin is not None and origin.kind == "opener" and position == 0:
                kind, label, importance = "opener", origin.label, 1
            elif origin is not None and origin.kind == "closer" and position == total - 1:
                kind, label, importance = "closer", origin.label, 1
            beats.append(Beat(kind, label, (first, last), (first, last), "full", importance, covers_point=kind != "point"))
        return beats

    def _origin(self, first: int, last: int) -> Optional[Beat]:
        best, overlap = None, 0
        for beat in self.cut.beats:
            shared = max(
                min(last, high) - max(first, low) + 1 for low, high in beat_spans(beat)
            )
            if shared > overlap:
                best, overlap = beat, shared
        return best

    def __call__(self, final: RecapFinalCut) -> list[str]:
        index, issues = self.index, []
        count = len(index)
        for number, entry in enumerate(final.beats, start=1):
            if not (1 <= entry.first_unit <= count and 1 <= entry.last_unit <= count):
                issues.append(f"Beat {number} uses an id outside the transcript (#1–#{count}).")
            elif entry.first_unit > entry.last_unit:
                issues.append(f"Beat {number} has a reversed range; write first..last.")
        beats = self.beats(final)
        for number, beat in enumerate(beats, start=1):
            kind = _mostly_framing(self.framing, beat.first, beat.last)
            if kind is not None:
                issues.append(f"Beat {number} (#{beat.first}–#{beat.last}) is in a {kind} chapter; use substance.")
            if not index.unit(beat.first).starts_sentence:
                issues.append(f"Beat {number} starts mid-sentence at #{beat.first}.")
            if index.boundary_strength(beat.last) < STRONG_BOUNDARY:
                issues.append(f"Beat {number} ends mid-sentence at #{beat.last}.")
            if beat.kind == "point" and not beat.label:
                issues.append(f"Beat {number} needs a label of its own that says what the point is.")
            for other, previous in enumerate(beats[: number - 1], start=1):
                if beat.first <= previous.last and previous.first <= beat.last:
                    issues.append(f"Beats {other} and {number} overlap.")
        if len(beats) < 2:
            issues.append("A recap plays at least two beats.")
        most = recap_beat_cap(self.budget) + FINAL_CUT_EXTRA_BEATS
        if len(beats) > most:
            issues.append(f"The cut has {len(beats)} beats; it can play at most {most}. Fewer complete beats follow better.")
        points = sum(1 for beat in beats if beat.kind == "point")
        if self.max_points is not None and points > self.max_points:
            issues.append(f"The cut has {points} points; the creator wants at most {self.max_points}.")
        for low, high in missing_slices(self.slices, [(beat.first, beat.last) for beat in beats]):
            times = f"{format_clock(index.unit(low).start)}-{format_clock(index.unit(high).end)}"
            issues.append(f"The cut plays nothing from {times} (#{low}–#{high}); the recap covers the whole video, so keep a beat from there.")
        seconds = recap_seconds(index, beats, self.pi.cleanup)
        floor = self.budget.min if self.cut.seconds >= self.budget.min else min(self.cut.seconds, self.budget.min)
        if not floor <= seconds <= self.budget.max:
            issues.append(
                f"The cut totals {seconds:.1f}s; it must be {self.budget.min:.0f}-{self.budget.max:.0f}s."
            )
        return issues


def _with_beats(pi: PlanInput, cut: RecapCut, beats: list[Beat]) -> RecapCut:
    seconds = recap_seconds(pi.index, beats, pi.cleanup)
    return RecapCut(beats=beats, seconds=round(seconds, 2), notes=cut.notes, attention=[], pool=cut.pool)


async def _final_cut(pi: PlanInput, cut: RecapCut, budget: Budget) -> tuple[RecapCut, str, str]:
    """The final cut, its title and what it says ("" when the pass failed: the assembled cut stays).

    The model returns the whole cut and what validates plays as written. A cut
    it changed gets one more read with the same eyes, applied only if it still
    finds a fix and that fix validates.
    """
    system = recap_final_cut_system(
        pi.language_name, budget, pi.outline.outline.format, max_beats=recap_beat_cap(budget) + FINAL_CUT_EXTRA_BEATS
    )
    agent = build_agent(RecapFinalCut, system, structural=True)
    title, why = "", ""
    for round_number in (1, 2):
        rules = _FinalCutRules(pi, cut, budget)
        try:
            final, residual = await run_with_repair(
                agent, final_cut_prompt(pi, cut), rules, stats=pi.stats, stage="recap_final_cut"
            )
        except Exception as error:
            if is_cancellation(error):
                raise
            logger.warning("Recap final cut failed (%s); keeping the current cut", type(error).__name__)
            break
        if residual:
            logger.info("Recap final cut keeps %d issue(s); keeping the current cut", len(residual))
            break
        beats = rules.beats(final)
        same = [(b.first, b.last) for b in beats] == [
            (low, high) for beat in cut.beats for low, high in beat_spans(beat)
        ]
        if round_number == 2 and final.verdict != "fix":
            break
        title, why = final.title, final.why
        if same:
            break
        pi.stats.critique_fixes += 1
        cut = _with_beats(pi, cut, beats)
    return cut, title, why


def _played_text(index: UnitIndex, cut: RecapCut) -> str:
    return " ".join(index.text(low, high) for beat in cut.beats for low, high in beat_spans(beat))


def title_problem(title: str, cut: RecapCut, index: UnitIndex, language: Optional[str]) -> Optional[str]:
    """Why a title promises what the recap doesn't play (a count, a word it never says), else None."""
    if not title:
        return "empty"
    count = announced_count(title)
    points = sum(1 for beat in cut.beats if beat.carries_point)
    if count and count > points:
        return f"announces {count} items, plays {points}"
    played = title_words(_played_text(index, cut))
    source = _source_names(index)
    checked = unheard = 0
    for position, token in enumerate(title.split()):
        words = title_words(token)
        if not words or len(words[0]) < TITLE_WORD_MIN_CHECKED:
            continue
        checked += 1
        if said_in(words[0], played):
            continue
        name = token.strip(_TITLE_EDGES)
        # A name the video says elsewhere is a promise this recap doesn't keep.
        if position > 0 and name[:1].isupper() and name in source:
            return f"names {name}, which doesn't play"
        unheard += 1
    if unheard > (TITLE_UNHEARD_ALLOWED if checked >= TITLE_UNHEARD_MIN_WORDS else 0):
        return f"{unheard} of its {checked} words don't play"
    return None


_TITLE_EDGES = "\"'`“”‘’«»,.;:!?¿¡…—–-()[]"
# Every title word this long has to be said in the recap; a longer title may
# bring one word of its own (a verb the speaker didn't use).
TITLE_WORD_MIN_CHECKED = 5
TITLE_UNHEARD_ALLOWED = 1
TITLE_UNHEARD_MIN_WORDS = 3


def _source_names(index: UnitIndex) -> set[str]:
    """Capitalized words the speakers use mid-sentence: names of people, places and things."""
    names: set[str] = set()
    for unit in index.units:
        tokens = re.findall(r"[^\W\d_][\w'-]*", unit.text)
        names.update(token for token in tokens[1:] if token[0].isupper() and token.upper() != token)
    return names


def _recap_title(pi: PlanInput, draft: RecapDraft, cut: RecapCut, final_title: str) -> str:
    """The first candidate title that promises only what plays, else one made from what plays."""
    first_point = next((beat.label for beat in cut.beats if beat.kind == "point" and beat.label), "")
    for candidate in (final_title, draft.title, first_point):
        title = ai.sanitize_hook_title(candidate) or compose_title(candidate or "") or ""
        if not title:
            continue
        problem = title_problem(title, cut, pi.index, pi.language)
        if problem is None:
            return title
        logger.info("Recap title candidate rejected (%s)", problem)
    opening = next((beat for beat in cut.beats if beat.kind == "point"), cut.beats[0])
    return compose_title(pi.index.text(opening.first, opening.last)) or ""


async def plan_recap(pi: PlanInput) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index, spec = pi.index, pi.ctx.spec
    budget = recap_budget(spec.clip_length, pi.ctx.source_seconds, pi.setting("target_seconds"))
    await pi.ctx.progress(54, "Mapping the main points...")
    draft = await _draft(pi, budget)
    await pi.ctx.progress(58, "Choosing what to keep...")
    framing = framing_units(pi)
    cut = fit_recap(
        index, _draft_in(index, draft, framing), budget, pi.outline.outline, pi.cleanup, framing,
        whole_video=pi.setting("coverage") == "whole_video",
        max_points=pi.setting("max_points"),
    )
    await pi.ctx.progress(62, "Checking the edit...")
    cut, final_title, why = await _final_cut(pi, cut, budget)

    for note in cut.notes:
        pi.note(note["code"], note.get("params"))
    if index.coarse:
        pi.note("COARSE_TRANSCRIPT")
    # A recap always carries a title and a reasoning that speak only of what plays.
    title = _recap_title(pi, draft, cut, final_title)
    reasoning = _reasoning(why) or pi.outline.outline.one_line
    plan_segments, plan = _segments(pi, cut, title, budget, reasoning)
    if draft.spine.strip():
        plan["summary"] = " ".join(draft.spine.split())
    return plan_segments, plan


def _reasoning(text: str) -> str:
    # The model sometimes trails off into markup ("...asleep.}</br>").
    clean = " ".join(re.split(r"[{}<>]", text or "")[0].split())
    if len(clean) <= REASONING_MAX_CHARS:
        return clean
    return clean[:REASONING_MAX_CHARS].rsplit(" ", 1)[0]


def _segments(
    pi: PlanInput, cut: RecapCut, title: str, budget: Budget, reasoning: str = ""
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    index = pi.index
    ranges: list[tuple[float, float]] = []
    meta_points: list[dict[str, Any]] = []
    plan_points: list[dict[str, Any]] = []
    for beat in cut.beats:
        label = beat.label if beat.kind != "point" else (_label(beat.label) or beat.label)
        beat_ranges = [span for low, high in beat_spans(beat) for span in index.ranges_for(low, high)]
        # One entry per range: a setup, or a long silence inside the beat, is its own range.
        for span in beat_ranges:
            meta_points.append(
                {
                    "label": label,
                    "kind": beat.kind,
                    "range_index": len(ranges),
                    "droppable": beat.droppable,
                    "output_start": None,
                }
            )
            ranges.append(span)
        plan_points.append({"label": label, "kind": beat.kind, "ranges": rounded_ranges(beat_ranges), "output_start": None})
    segment = planned_segment(
        ranges=ranges,
        junction_fades=[RECAP_JUNCTION_FADE] * (len(ranges) - 1),
        max_seconds=budget.render_cap,
        required=True,
        text=" … ".join(
            " ".join(index.text(low, high) for low, high in beat_spans(beat)) for beat in cut.beats
        ),
        hook_title=compose_title(title),
        reasoning=reasoning or pi.outline.outline.one_line,
        workflow_role="recap",
        meta={"points": meta_points, **({} if pi.setting("on_screen_points") else {"on_screen_points": False})},
    )
    plan = base_plan(pi, title=title)
    plan["points"] = plan_points
    plan["seconds"] = cut.seconds
    return [segment], plan
