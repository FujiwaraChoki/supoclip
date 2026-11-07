"""Deterministic assembly: fit topic spans, recaps and trailers to their budgets.

Planners hand over unit ids from the model; the functions here own the hard
constraints (lengths, coherence at the joins, spoilers) so the result never
depends on the model counting seconds. Inputs are plain dataclasses, so this
module never imports the LLM schemas.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, Collection, Iterable, Literal, Optional, Sequence

from .boundaries import STRONG_BOUNDARY, USABLE_BOUNDARY
from .durations import Budget
from .errors import WorkflowError
from .language import CLAUSE_MARK_RE, opens_with_back_reference, unresolved_reference
from .units import UnitIndex

logger = logging.getLogger(__name__)

# Junction fades the planners put between stitched ranges. A recap dissolves
# softly; a trailer hard-cuts. Totals subtract them because each fade overlaps
# the two sides. Trailers use plain cuts (the renderer concatenates when every
# fade is 0).
RECAP_JUNCTION_FADE = 0.12
TRAILER_JUNCTION_FADE = 0.0

RECAP_OPENER_MAX_SECONDS = 10.0
# A recap needs this much speech in the whole source to say anything.
RECAP_MIN_SOURCE_SPEECH_SECONDS = 60.0

TRAILER_BEAT_MIN_SECONDS = 1.5
# Past the cold open a line may be a hit of under a second ("But here's the thing.").
TRAILER_SHORT_BEAT_MIN_SECONDS = 0.8
TRAILER_BEAT_MAX_SECONDS = 10.0
TRAILER_COLD_OPEN_MAX_SECONDS = 8.0
# Weakest cut a trailer beat may end on: a comma with a breath.
TRAILER_BEAT_END_STRENGTH = 0.35

_RECAP_UNSUPPORTED = "This video doesn't have enough distinct points for a recap. Try Highlights instead."


def _overlaps(a: tuple[int, int], b: tuple[int, int]) -> bool:
    return a[0] <= b[1] and b[0] <= a[1]


def _span(index: UnitIndex, first: int, last: int) -> tuple[int, int]:
    low, high = sorted((index.clamp_id(first), index.clamp_id(last)))
    return low, high


# -- topic clips --------------------------------------------------------------


def fit_span(
    index: UnitIndex,
    first: int,
    last: int,
    budget: Budget,
    *,
    anchor: int,
    cleanup: Optional[dict] = None,
) -> Optional[tuple[int, int]]:
    """Fit ``first..last`` into ``budget.min..budget.max`` seconds, keeping ``anchor``.

    Edges the model put inside a sentence first move to the sentence's
    opening or close, within two units. Too long: trim whole units from the end (the new end must be a strong
    cut), then from the start (the new start must open a sentence). Too short:
    extend forward to the next strong cuts, then back to sentence starts, up
    to ``budget.ideal_min``. Returns None when no fit keeps the anchor.
    """
    first, last = _span(index, first, last)
    anchor = min(max(index.clamp_id(anchor), first), last)

    def seconds(low: int, high: int) -> float:
        return index.speech_seconds(low, high, (), cleanup)

    first, last = _snap_span_edges(index, first, last, anchor, budget.max, seconds)
    length = seconds(first, last)
    if budget.min <= length <= budget.max:
        return first, last

    if length > budget.max:
        ends = [last] + [
            uid for uid in range(last - 1, anchor - 1, -1) if index.boundary_strength(uid) >= STRONG_BOUNDARY
        ]
        starts = [first] + [uid for uid in range(first + 1, anchor + 1) if index.unit(uid).starts_sentence]
        # Keep as much of the opening as possible: a later start only when no
        # end works with an earlier one.
        for start in starts:
            for end in ends:
                length = seconds(start, end)
                if length > budget.max:
                    continue
                if length >= budget.min:
                    return start, end
                # Trimming this end jumped past the window; a later start
                # with a longer end may still land inside it.
                break
        return None

    # Too short: grow forward, then backward, stopping once ideal.
    end = last
    for candidate in range(last + 1, len(index) + 1):
        if seconds(first, end) >= budget.ideal_min:
            break
        if index.boundary_strength(candidate) < STRONG_BOUNDARY:
            continue
        if seconds(first, candidate) > budget.max:
            break
        end = candidate
    start = first
    for candidate in range(first - 1, 0, -1):
        if seconds(start, end) >= budget.ideal_min:
            break
        if not index.unit(candidate).starts_sentence:
            continue
        if seconds(candidate, end) > budget.max:
            break
        start = candidate
    length = seconds(start, end)
    return (start, end) if budget.min <= length <= budget.max else None


SPAN_EDGE_SNAP_UNITS = 2


def _snap_span_edges(
    index: UnitIndex, first: int, last: int, anchor: int, max_seconds: float, seconds
) -> tuple[int, int]:
    """Move a mid-sentence start or end of the model's span to the nearest sentence edge.

    A start goes back to its sentence's opening (else forward, never past the
    anchor); an end goes forward to the sentence's close (else back, never
    before the anchor). Edges stay put when nothing within
    ``SPAN_EDGE_SNAP_UNITS`` works, or when growing would pass ``max_seconds``.
    """
    if not index.unit(first).starts_sentence:
        backward = range(first - 1, max(0, first - SPAN_EDGE_SNAP_UNITS - 1), -1)
        forward = range(first + 1, min(anchor, first + SPAN_EDGE_SNAP_UNITS) + 1)
        first = next(
            (uid for uid in backward if index.unit(uid).starts_sentence and seconds(uid, last) <= max_seconds),
            next((uid for uid in forward if index.unit(uid).starts_sentence), first),
        )
    if index.boundary_strength(last) < STRONG_BOUNDARY:
        forward = range(last + 1, min(len(index), last + SPAN_EDGE_SNAP_UNITS) + 1)
        backward = range(last - 1, max(anchor, last - SPAN_EDGE_SNAP_UNITS) - 1, -1)
        last = next(
            (
                uid
                for uid in forward
                if index.boundary_strength(uid) >= STRONG_BOUNDARY and seconds(first, uid) <= max_seconds
            ),
            next((uid for uid in backward if index.boundary_strength(uid) >= STRONG_BOUNDARY), last),
        )
    return first, last


# -- recap ----------------------------------------------------------------------

# A point beat never runs longer than this, nor than this share of the target:
# one 8-15 s beat per important chapter beats one long beat. A story's scenes
# need room to follow.
RECAP_POINT_MAX_SECONDS = 25.0
RECAP_STORY_POINT_MAX_SECONDS = 35.0
RECAP_POINT_TARGET_SHARE = 0.3
# A beat that opens on an unheard reference takes in at most this much before it.
RECAP_BRIDGE_MAX_UNITS = 3
RECAP_BRIDGE_MAX_SECONDS = 12.0
RECAP_BRIDGE_MIN_WORDS = 4
# Selection values: importance, the outline's stars, the first beat covering a
# chapter, a dangling opening, a fuller statement. Every beat is one more join
# to follow; a later beat in a chapter says less than the first. Length counts
# too: per second away from the target.
_IMPORTANCE_VALUE = {1: 15.0, 2: 5.0, 3: 2.0}
_STAR_VALUE = {5: 8.0, 4: 3.0}
CHAPTER_COVER_VALUE = 6.0
REPEAT_CHAPTER_FACTOR = 0.3
JOIN_COST = 1.5
DANGLING_PENALTY = 4.0
FULL_MODE_VALUE = 0.5
LONG_BEAT_PENALTY = 3.0
LENGTH_PENALTY_PER_SECOND = 0.2
_BUCKET_SECONDS = 0.5
_GROUP_MAX_CANDIDATES = 6
# Every ★5 main point plays when each could have about this much of the recap.
FIVE_STAR_POINT_SECONDS = 8.0
# A story, a vlog or a stream reaches its ending: the last beat starts this far in.
RECAP_LATE_SHARE = 0.75
_ENDING_FORMATS = {"story", "vlog", "other"}
# An opener this close before a point plays with it, as one beat.
OPENER_MERGE_UNITS = 2
# A recap of the whole video touches each of these stretches that holds a chapter rated COVERAGE_MIN_STARS or
# higher. Quarters, not thirds: a beat early in the last third still left out the last 17 minutes of a 57-minute
# episode. A recap of at most 4 beats has no room for quarters and takes thirds.
COVERAGE_SLICES = 4
COVERAGE_SHORT_SLICES = 3
COVERAGE_MIN_STARS = 3


@dataclass(frozen=True)
class RecapPointIn:
    label: str
    first: int  # fullest clear statement
    last: int
    core_first: int  # shortest self-contained statement inside it
    core_last: int
    importance: int  # 1 = essential, 2 = important, 3 = nice to have
    setup: Optional[tuple[int, int]] = None  # a short earlier sentence that plays right before it
    answers_opener: bool = False  # always plays: the opener's question needs it


@dataclass(frozen=True)
class RecapDraftIn:
    title: str
    opener: Optional[tuple[int, int]]  # frames the video; plays first
    points: Sequence[RecapPointIn]  # in video order
    closer: Optional[tuple[int, int]]  # lands the takeaway; None when the last point concludes


BeatKind = Literal["opener", "point", "closer"]


@dataclass(frozen=True)
class Beat:
    kind: BeatKind
    label: str
    full: tuple[int, int]
    core: tuple[int, int]
    span_mode: Literal["full", "core"]
    importance: int
    lead: int = 0  # units added before the span for coherence (extend_back)
    from_outline: bool = False
    covers_point: bool = False  # an opener or closer that contains a point and plays it
    key: int = -1  # the selection candidate this beat came from (-1: opener or closer)
    setup: Optional[tuple[int, int]] = None  # plays right before the beat, with a cut between them

    @property
    def first(self) -> int:
        return (self.full if self.span_mode == "full" else self.core)[0] - self.lead

    @property
    def last(self) -> int:
        return (self.full if self.span_mode == "full" else self.core)[1]

    @property
    def droppable(self) -> bool:
        return self.kind == "point" and self.importance >= 2

    @property
    def carries_point(self) -> bool:
        return self.kind == "point" or self.covers_point


@dataclass
class _RecapCandidate:
    key: int
    beat: Beat  # core mode, no lead
    chapter: int  # 0-based outline chapter of its core, -1 without chapters
    outline_points: frozenset[int]  # outline main point numbers (1-based) whose core it plays
    answers_opener: bool = False


@dataclass
class RecapPool:
    """Everything the selector chooses from, and what the final cut may still bring in."""

    candidates: list[_RecapCandidate]
    opener: Optional[Beat]
    closer: Optional[Beat]
    chapter_stars: list[int]  # importance of each outline chapter
    chapter_spans: list[tuple[int, int]]
    point_stars: dict[int, int]  # outline main point number -> importance 1-5
    framing: frozenset[int] = frozenset()  # sponsor reads, empty intros and outros: a bridge never enters them
    video_format: str = ""
    point_max: float = RECAP_POINT_MAX_SECONDS
    late_from: Optional[int] = None  # a beat starting here reaches the ending; None when any may end it
    slices: tuple[tuple[int, int], ...] = ()  # whole-video coverage: each needs a beat when it can have one
    max_points: Optional[int] = None  # the creator's limit, which never gives way


@dataclass
class RecapCut:
    beats: list[Beat]  # play order: opener, points in video order, closer
    seconds: float  # estimated rendered length, junction fades included
    notes: list[dict[str, Any]] = field(default_factory=list)
    attention: list[int] = field(default_factory=list)  # 1-based beats the critique should check
    pool: Optional[RecapPool] = None


def beat_seconds(index: UnitIndex, beat: Beat, cleanup: Optional[dict] = None) -> float:
    """What the beat plays, its setup included (before the fades at its joins)."""
    seconds = index.speech_seconds(beat.first, beat.last, (), cleanup)
    if beat.setup is not None:
        seconds += index.speech_seconds(beat.setup[0], beat.setup[1], (), cleanup)
    return seconds


def beat_spans(beat: Beat) -> list[tuple[int, int]]:
    """The unit runs a beat plays, in order: its setup, then itself."""
    return ([beat.setup] if beat.setup is not None else []) + [(beat.first, beat.last)]


def _beat_weight(index: UnitIndex, beat: Beat, cleanup: Optional[dict]) -> float:
    return beat_seconds(index, beat, cleanup) - RECAP_JUNCTION_FADE * len(beat_spans(beat))


def recap_seconds(index: UnitIndex, beats: Sequence[Beat], cleanup: Optional[dict] = None) -> float:
    if not beats:
        return 0.0
    return RECAP_JUNCTION_FADE + sum(_beat_weight(index, beat, cleanup) for beat in beats)


def _outline_points(outline: Any) -> list[Any]:
    points = getattr(outline, "main_points", outline)
    return list(points or [])


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _chapter_spans(outline: Any, count: int) -> tuple[list[tuple[int, int]], list[int]]:
    """(first, last) unit and importance of every outline chapter; empty without chapters."""
    spans: list[tuple[int, int]] = []
    stars: list[int] = []
    first = 1
    for chapter in list(_field(outline, "chapters", None) or []):
        last = min(max(int(_field(chapter, "end_unit", count)), first), count)
        spans.append((first, last))
        stars.append(int(_field(chapter, "importance", 3) or 3))
        first = last + 1
    return spans, stars


def _chapter_of(spans: Sequence[tuple[int, int]], uid: int) -> int:
    return next((position for position, (low, high) in enumerate(spans) if low <= uid <= high), -1)


def _normalize_points(
    index: UnitIndex,
    points: Iterable[RecapPointIn],
    cleanup: Optional[dict] = None,
    point_max: float = RECAP_POINT_MAX_SECONDS,
) -> list[Beat]:
    """Draft points as core beats in video order; touching points merge only into one short statement.

    Two points at most one unit apart become one beat when their union still
    fits a point. The merged core is the more important point's core,
    never the union, so cores don't swell. Overlapping points too long to
    merge keep the more important one; merely adjacent ones both stay.
    """
    beats: list[Beat] = []
    for point in points:
        if point.first < 1 or point.last < 1:
            continue  # -1 means "none"; clamping it would invent a point at the start
        full = _span(index, point.first, point.last)
        core = _span(index, point.core_first, point.core_last)
        if min(point.core_first, point.core_last) < 1 or not (full[0] <= core[0] and core[1] <= full[1]):
            core = full
        importance = min(max(int(point.importance), 1), 3)
        beats.append(
            Beat(
                "point", (point.label or "").strip(), full, core, "core",
                1 if point.answers_opener else importance, setup=point.setup,
            )
        )
    beats.sort(key=lambda beat: beat.full)
    merged: list[Beat] = []
    for beat in beats:
        previous = merged[-1] if merged else None
        if previous is None or beat.full[0] > previous.full[1] + 2:
            merged.append(beat)
            continue
        union = (previous.full[0], max(previous.full[1], beat.full[1]))
        main = previous if previous.importance <= beat.importance else beat
        if index.speech_seconds(*union, (), cleanup) <= point_max:
            labels = [previous.label] + ([beat.label] if beat.label and beat.label != previous.label else [])
            merged[-1] = replace(
                previous,
                label=" · ".join(label for label in labels if label),
                full=union,
                core=main.core,
                importance=main.importance,
            )
        elif beat.full[0] <= previous.full[1]:
            merged[-1] = main  # overlapping and too long together: the more important one
        else:
            merged.append(beat)
    return merged


def _trim_to(
    index: UnitIndex, span: tuple[int, int], max_seconds: float, cleanup: Optional[dict]
) -> Optional[tuple[int, int]]:
    """``span`` cut back from its end to the last strong cut within ``max_seconds``."""
    first, last = span
    for end in range(last, first - 1, -1):
        if end != last and index.boundary_strength(end) < STRONG_BOUNDARY:
            continue
        if index.speech_seconds(first, end, (), cleanup) <= max_seconds:
            return first, end
    return None


def _frame_beat(
    index: UnitIndex, kind: BeatKind, span: Optional[tuple[int, int]], points: list[Beat]
) -> tuple[Optional[Beat], list[Beat]]:
    """An opener or closer beat, resolving overlap with the points.

    One that contains a point's core replaces that point (the point would
    otherwise play twice); one that only grazes a point is dropped.
    """
    if span is None or span[0] < 1:
        return None, points
    span = _span(index, *span)
    kept: list[Beat] = []
    for point in points:
        if not _overlaps(span, point.full):
            kept.append(point)
        elif span[0] <= point.core[0] and point.core[1] <= span[1]:
            continue
        else:
            return None, points
    label = "Intro" if kind == "opener" else "Takeaway"
    return Beat(kind, label, span, span, "full", 1, covers_point=len(kept) < len(points)), kept


def dangling(index: UnitIndex, uid: int) -> bool:
    """Whether a beat starting at ``uid`` leans on something the viewer hasn't heard."""
    return unresolved_reference(index.unit(uid).text, index.language)


def _bridge_lead(
    index: UnitIndex, first: int, floor: int, cleanup: Optional[dict], framing: Collection[int] = ()
) -> Optional[int]:
    """Units to add before ``first`` so the beat opens on a sentence that stands alone.

    Goes back to the start of the containing sentence, then on while the new
    first unit still dangles: at most three units and 12 s, never below
    ``floor`` (the end of the material before it), never into a sponsor read
    or a greeting, and never onto a line too short to name anything.
    None when nothing works.
    """
    for start in range(first - 1, max(floor, first - RECAP_BRIDGE_MAX_UNITS) - 1, -1):
        if start in framing or index.speech_seconds(start, first - 1, (), cleanup) > RECAP_BRIDGE_MAX_SECONDS:
            return None
        unit = index.unit(start)
        if unit.starts_sentence and not dangling(index, start) and len(unit.text.split()) >= RECAP_BRIDGE_MIN_WORDS:
            return first - start
    return None


def _floor(pool: RecapPool, beat: Beat) -> int:
    """First unit a bridge may reach: just after any other material that ends before the beat."""
    spans = [c.beat.full for c in pool.candidates if c.beat.full != beat.full]
    spans += [b.full for b in (pool.opener, pool.closer) if b is not None]
    return max([high + 1 for low, high in spans if high < beat.full[0] and low < beat.full[0]], default=1)


def _candidate_value(pool: RecapPool, candidate: _RecapCandidate) -> float:
    """What playing this candidate is worth: its importance, plus the outline's stars for what it plays."""
    stars = [pool.point_stars.get(number, 0) for number in candidate.outline_points]
    return _IMPORTANCE_VALUE.get(candidate.beat.importance, 2.0) + _STAR_VALUE.get(max(stars, default=0), 0.0)


def _variants(
    index: UnitIndex, pool: RecapPool, candidate: _RecapCandidate, budget: Budget, cleanup: Optional[dict]
) -> list[tuple[Beat, float]]:
    """Ways to play one candidate with their value: core or full, bridged or not."""
    base = candidate.beat
    cap = max(4.0, min(pool.point_max, RECAP_POINT_TARGET_SHARE * (budget.target or budget.ideal)))
    floor = _floor(pool, base)
    value = _candidate_value(pool, candidate)
    variants: list[tuple[Beat, float]] = []
    for mode in ("core", "full"):
        beat = replace(base, span_mode=mode, lead=0)
        if mode == "full" and beat.full == beat.core:
            continue
        bonus = value + (FULL_MODE_VALUE if mode == "full" else 0.0)
        if mode == "full" and index.speech_seconds(beat.first, beat.last, (), cleanup) > cap:
            bonus -= LONG_BEAT_PENALTY  # still usable when the window needs the length
        if beat.setup is None and dangling(index, beat.first):
            lead = _bridge_lead(index, beat.first, floor, cleanup, pool.framing)
            if lead:
                variants.append((replace(beat, lead=lead), bonus))
            variants.append((beat, bonus - DANGLING_PENALTY))
        else:
            variants.append((beat, bonus))
    return variants


def _groups(pool: RecapPool, fixed: Sequence[Beat]) -> list[tuple[Any, list[_RecapCandidate], float, Optional[int]]]:
    """Candidates by chapter: (key, members best first, bonus for covering the chapter, most beats it may play).

    A news story plays once (the lead story twice): one point per story.
    """
    covered = {_chapter_of(pool.chapter_spans, uid) for beat in fixed for uid in (beat.first, beat.last)}
    news = pool.video_format == "news"
    bonus_stars = 3 if news else 4
    lead_story = next(
        (position for position, stars in enumerate(pool.chapter_stars) if stars >= bonus_stars), None
    )
    groups: dict[Any, list[_RecapCandidate]] = {}
    for candidate in sorted(pool.candidates, key=lambda item: item.beat.importance):
        key: Any = ("chapter", candidate.chapter) if candidate.chapter >= 0 else ("alone", candidate.key)
        if len(groups.get(key, [])) >= _GROUP_MAX_CANDIDATES:
            key = ("alone", candidate.key)
        groups.setdefault(key, []).append(candidate)
    built = []
    for key, members in groups.items():
        bonus = 0.0
        limit: Optional[int] = None
        if key[0] == "chapter":
            chapter = key[1]
            if pool.chapter_stars[chapter] >= bonus_stars and chapter not in covered:
                bonus = CHAPTER_COVER_VALUE
            if news:
                limit = 2 if chapter == lead_story else 1
        built.append((key, members, bonus, limit))
    return built


def _select(
    index: UnitIndex,
    pool: RecapPool,
    fixed: list[Beat],
    forced: Collection[int],
    max_beats: Optional[int],
    budget: Budget,
    cleanup: Optional[dict],
) -> Optional[tuple[list[Beat], float, bool]]:
    """The best set of point beats around ``fixed`` (opener/closer): (points, total, inside the window).

    An exact group knapsack over 0.5 s buckets: candidates in one chapter form
    a group, the first beat covering a ★4-5 chapter earns its bonus, a later
    beat in the chapter counts for less, and every beat pays for its join. At
    least one essential point plays when one exists, at least one point unless
    an opener or closer plays one, and a story reaches its ending. Inside the
    window the highest value wins, less a small cost per second away from the
    target; when nothing reaches the window, the longest set under the maximum.
    None when even ``fixed`` alone is too long or ``forced`` can't fit.
    """
    base = RECAP_JUNCTION_FADE + sum(_beat_weight(index, beat, cleanup) for beat in fixed)
    room = budget.max - base
    if room < -1e-9:
        return None
    usable = pool.candidates
    essential_exists = any(c.beat.importance == 1 for c in usable)
    needs_point = not any(beat.covers_point for beat in fixed)
    room_for_beats = None if max_beats is None else max_beats - len(fixed)
    late_from = pool.late_from

    def is_late(beat: Beat) -> bool:
        return late_from is not None and beat.first >= late_from

    late_exists = late_from is not None and (
        any(is_late(beat) for beat in fixed) or any(is_late(c.beat) for c in usable)
    )
    fixed_late = any(beat.kind == "closer" and is_late(beat) for beat in fixed)

    # state: (bucket, has_essential, beats chosen, reaches the ending) -> (value, exact_weight, chosen beats)
    states: dict[tuple[int, bool, int, bool], tuple[float, float, tuple[Beat, ...]]] = {
        (0, False, 0, fixed_late): (0.0, 0.0, ())
    }
    for group_key, members, chapter_bonus, limit in _groups(pool, fixed):
        # The best combination per (bucket, essential, count, late) is all the outer table needs.
        combos: list[tuple[float, float, tuple[Beat, ...], bool, bool]] = [(0.0, 0.0, (), False, False)]
        for candidate in members:
            variants = _variants(index, pool, candidate, budget, cleanup)
            weighed = [(beat, value, _beat_weight(index, beat, cleanup)) for beat, value in variants]
            best: dict[tuple[int, bool, int, bool], tuple[float, float, tuple[Beat, ...], bool, bool]] = {}

            def keep(combo: tuple[float, float, tuple[Beat, ...], bool, bool]) -> None:
                signature = (int(round(combo[1] / _BUCKET_SECONDS)), combo[3], len(combo[2]), combo[4])
                if signature not in best or combo[0] > best[signature][0] + 1e-9:
                    best[signature] = combo

            for value, weight, chosen, essential, late in combos:
                if candidate.key not in forced or not variants:
                    keep((value, weight, chosen, essential, late))
                if limit is not None and len(chosen) >= limit:
                    continue
                factor = REPEAT_CHAPTER_FACTOR if chosen else 1.0
                for beat, beat_value, beat_weight in weighed:
                    if weight + beat_weight > room + 1e-9:
                        continue
                    if any(_overlaps((beat.first, beat.last), (other.first, other.last)) for other in chosen):
                        continue
                    keep(
                        (
                            value + factor * beat_value - JOIN_COST,
                            weight + beat_weight,
                            chosen + (beat,),
                            essential or beat.importance == 1,
                            late or is_late(beat),
                        )
                    )
            combos = list(best.values())
        updated: dict[tuple[int, bool, int, bool], tuple[float, float, tuple[Beat, ...]]] = {}
        for (_, has_essential, count, has_late), (value, weight, chosen) in states.items():
            for combo_value, combo_weight, combo, combo_essential, combo_late in combos:
                total_weight = weight + combo_weight
                total_count = count + len(combo)
                if total_weight > room + 1e-9 or (room_for_beats is not None and total_count > room_for_beats):
                    continue
                state = (
                    int(round(total_weight / _BUCKET_SECONDS)),
                    has_essential or combo_essential,
                    total_count,
                    has_late or combo_late,
                )
                total_value = value + combo_value + (chapter_bonus if combo else 0.0)
                current = updated.get(state)
                if current is None or total_value > current[0] + 1e-9:
                    updated[state] = (total_value, total_weight, chosen + combo)
        states = updated

    def ranked(require_essential: bool, require_late: bool) -> Optional[tuple[list[Beat], float, bool]]:
        inside, under = None, None
        for (_, has_essential, count, has_late), (value, weight, chosen) in states.items():
            if (needs_point and not count) or count + len(fixed) < 2:
                continue  # a recap plays a point, and one beat alone is a clip
            if (require_essential and not has_essential) or (require_late and not has_late):
                continue
            total = base + weight
            if total > budget.max + 1e-9:
                continue
            if total >= budget.min - 1e-9:
                score = value - LENGTH_PENALTY_PER_SECOND * abs(total - budget.ideal)
                if inside is None or score > inside[0]:
                    inside = (score, list(chosen), total)
            elif under is None or (total, value) > (under[0], under[1]):
                under = (total, value, list(chosen))
        if inside is not None:
            return inside[1], inside[2], True
        if under is not None:
            return under[2], under[0], False
        return None

    result: Optional[tuple[list[Beat], float, bool]] = None
    for require_essential, require_late in ((True, True), (True, False), (False, True), (False, False)):
        if (require_essential and not essential_exists) or (require_late and not late_exists):
            continue
        attempt = ranked(require_essential, require_late)
        if attempt is None:
            continue
        if attempt[2]:
            return attempt
        if result is None or attempt[1] > result[1]:
            result = attempt
    return result


def recap_beat_cap(budget: Budget) -> int:
    """Most beats a recap of this length can play and still follow: 4 at 45 s, 6 at 60 s, 8 at 90 s, 11 at 120 s."""
    return int((budget.target or budget.ideal) // 12) + 1


def _merge_opener(opener: Optional[Beat], points: Sequence[Beat]) -> tuple[Optional[Beat], list[Beat]]:
    """An opener that runs straight into a point plays with it, as one beat at the opener's slot."""
    points = list(points)
    if opener is None or len(points) < 2:
        return opener, points
    for position, point in enumerate(points):
        if point.setup is None and 0 < point.first - opener.last <= OPENER_MERGE_UNITS + 1:
            points.pop(position)
            return replace(opener, full=(opener.first, point.last), core=(opener.first, point.last), covers_point=True), points
    return opener, points


def _assemble(
    index: UnitIndex,
    opener: Optional[Beat],
    points: Sequence[Beat],
    closer: Optional[Beat],
    budget: Budget,
    cleanup: Optional[dict],
) -> list[Beat]:
    def ordered(first: Optional[Beat], middle: Sequence[Beat]) -> list[Beat]:
        return ([first] if first else []) + sorted(middle, key=lambda beat: beat.full) + ([closer] if closer else [])

    merged = ordered(*_merge_opener(opener, points))
    # Whatever lay between them now plays too: only when that still fits.
    return merged if recap_seconds(index, merged, cleanup) <= budget.max else ordered(opener, points)


def _choose(
    index: UnitIndex, pool: RecapPool, forced: Collection[int], budget: Budget, cleanup: Optional[dict]
) -> list[Beat]:
    """Select around the opener and closer; when they can't fit, the closer gives way, then the opener.

    The beat cap gives way before they do, when the window can't be reached under it.
    """
    opener, closer = pool.opener, pool.closer
    attempts = [(opener, closer), (opener, None), (None, closer), (None, None)]
    best: Optional[tuple[list[Beat], float, Optional[Beat], Optional[Beat]]] = None
    tried: list[tuple[int, int]] = []
    for first, last in attempts:
        signature = (id(first), id(last))
        if signature in tried:
            continue
        tried.append(signature)
        fixed = [beat for beat in (first, last) if beat is not None]
        cap = recap_beat_cap(budget)
        limits = (pool.max_points + len(fixed),) if pool.max_points else (cap, cap + 2, cap + 4, None)
        for max_beats in limits:
            chosen = _select(index, pool, fixed, forced, max_beats, budget, cleanup)
            if chosen is None:
                continue
            points, total, inside = chosen
            if not points and not any(beat.covers_point for beat in fixed):
                continue
            if len(points) + len(fixed) < 2:
                continue  # one beat is a clip, not a recap
            if inside:
                return _assemble(index, first, points, last, budget, cleanup)
            if best is None or total > best[1]:
                best = (points, total, first, last)
    if best is None:
        raise WorkflowError("recap_unsupported", _RECAP_UNSUPPORTED)
    return _assemble(index, best[2], best[0], best[3], budget, cleanup)


def _outline_candidates(
    index: UnitIndex, outline: Any, taken: list[tuple[int, int]], closer: Optional[Beat]
) -> list[tuple[Beat, int]]:
    """Uncovered outline main points as candidates, with their outline number."""
    found: list[tuple[Beat, int]] = []
    for number, item in enumerate(_outline_points(outline), start=1):
        first_unit, last_unit = int(_field(item, "first_unit", -1)), int(_field(item, "last_unit", -1))
        if first_unit < 1 or last_unit < 1:
            continue
        full = _span(index, first_unit, last_unit)
        core = _span(index, int(_field(item, "core_first", full[0])), int(_field(item, "core_last", full[1])))
        if not (full[0] <= core[0] and core[1] <= full[1]):
            core = full
        if any(full[0] <= span[1] + 1 and span[0] <= full[1] + 1 for span in taken):
            continue
        if closer is not None and full[1] >= closer.full[0]:
            continue
        importance = 2 if int(_field(item, "importance", 3) or 3) >= 4 else 3
        label = str(_field(item, "statement", "") or "").strip()
        found.append((Beat("point", label, full, core, "core", importance, from_outline=True), number))
        taken = taken + [full]
    return found


def coverage_slices(
    index: UnitIndex, outline: Any, framing: Collection[int] = (), budget: Optional[Budget] = None
) -> tuple[tuple[int, int], ...]:
    """The video's substance in equal stretches of time, as unit ranges: a recap of the whole video touches each.

    Sponsor reads, empty intros and outros don't count, and a stretch holding
    only minor chapters (rated under 3) has nothing it must contribute.
    """
    content = [uid for uid in range(1, len(index) + 1) if uid not in framing]
    count = COVERAGE_SHORT_SLICES if budget is not None and recap_beat_cap(budget) <= 4 else COVERAGE_SLICES
    if len(content) < count:
        return ()
    start, end = index.unit(content[0]).start, index.unit(content[-1]).end
    spans, stars = _chapter_spans(outline, len(index))
    slices: list[tuple[int, int]] = []
    for stretch in range(count):
        low = max(content[0], index.unit_at(start + stretch * (end - start) / count))
        high = min(content[-1], index.unit_at(start + (stretch + 1) * (end - start) / count - 0.001))
        minor = spans and not any(
            star >= COVERAGE_MIN_STARS and _overlaps(span, (low, high)) for span, star in zip(spans, stars)
        )
        if low <= high and not minor:
            slices.append((low, high))
    return tuple(slices)


def missing_slices(
    slices: Sequence[tuple[int, int]], played: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    return [part for part in slices if not any(_overlaps(part, span) for span in played)]


def fit_recap(
    index: UnitIndex,
    draft: RecapDraftIn,
    budget: Budget,
    outline: Any = None,
    cleanup: Optional[dict] = None,
    framing: Collection[int] = (),
    *,
    whole_video: bool = False,
    max_points: Optional[int] = None,
) -> RecapCut:
    """Assemble the recap's beats to the budget window.

    Candidates are the draft's points (core or full statement) and the
    outline's uncovered main points. One exact selection (``_select``) picks
    the set inside ``budget.min..budget.max`` with the most value: essential
    points first, then ★5 outline points, one beat for every ★4-5 chapter, no
    dangling openings (a beat that opens on an unheard reference takes in the
    sentence before it when it can). The opener plays first and the closer
    last; the opener plays in source order instead when it sits in the first
    point's chapter, or comes from after the midpoint without asking a question.

    ``outline`` is the source outline (anything with ``main_points`` whose
    items carry ``first_unit``, ``last_unit``, ``core_first``, ``core_last``,
    ``importance`` 1-5 and ``statement``, and optionally ``chapters`` with
    ``end_unit`` and ``importance``), or None.

    ``whole_video`` makes the best candidate of every stretch of the video play
    (``coverage_slices``). ``max_points`` limits the points, opener and closer
    aside, even when that leaves the recap under its window.
    """
    if index.speech_seconds(1, len(index)) < RECAP_MIN_SOURCE_SPEECH_SECONDS:
        raise WorkflowError("recap_unsupported", _RECAP_UNSUPPORTED)
    video_format = str(_field(outline, "format", "") or "")
    point_max = RECAP_STORY_POINT_MAX_SECONDS if video_format in ("story", "vlog") else RECAP_POINT_MAX_SECONDS
    points = _normalize_points(index, draft.points, cleanup, point_max)
    opener_span = draft.opener
    if opener_span is not None and opener_span[0] >= 1:
        # Cut the opener to length before it absorbs points: it may only
        # replace a point whose core it still plays after the cut.
        opener_span = _trim_to(index, _span(index, *opener_span), RECAP_OPENER_MAX_SECONDS, cleanup)
    opener, points = _frame_beat(index, "opener", opener_span, points)
    closer, points = _frame_beat(index, "closer", draft.closer, points)
    if closer is not None and points and closer.full[1] < points[-1].full[0]:
        closer = None
    if closer is not None and opener is not None and _overlaps(closer.full, opener.full):
        closer = None
    spans, stars = _chapter_spans(outline, len(index))
    if opener is not None and points and spans:
        chapter = _chapter_of(spans, opener.full[0])
        if chapter >= 0 and chapter == _chapter_of(spans, points[0].core[0]) and opener.full[0] > points[0].full[0]:
            # A line from the first point's own story plays where it was said,
            # so the story never runs backwards.
            points = sorted(points + [replace(opener, kind="point", covers_point=False)], key=lambda beat: beat.full)
            opener = None
    if opener is not None and _late_opener_plays_in_order(index, opener, outline):
        points = sorted(points + [replace(opener, kind="point", covers_point=False)], key=lambda beat: beat.full)
        opener = None

    taken = [beat.full for beat in points] + [beat.full for beat in (opener, closer) if beat is not None]
    extra = _outline_candidates(index, outline, taken, closer)
    outline_cores = [
        (number, _span(index, int(_field(item, "core_first", 1)), int(_field(item, "core_last", 1))), int(_field(item, "importance", 3) or 3))
        for number, item in enumerate(_outline_points(outline), start=1)
        if int(_field(item, "core_first", -1)) >= 1 and int(_field(item, "core_last", -1)) >= 1
    ]

    def covered_points(beat: Beat) -> frozenset[int]:
        return frozenset(number for number, core, _ in outline_cores if _overlaps(core, beat.full))

    candidates: list[_RecapCandidate] = []
    answering = {
        (min(point.core_first, point.core_last), max(point.core_first, point.core_last))
        for point in draft.points
        if point.answers_opener
    }
    for beat in points:
        key = len(candidates)
        candidates.append(
            _RecapCandidate(
                key, replace(beat, key=key), _chapter_of(spans, beat.core[0]), covered_points(beat),
                answers_opener=any(_overlaps(beat.full, span) for span in answering),
            )
        )
    for beat, number in extra:
        key = len(candidates)
        candidates.append(
            _RecapCandidate(key, replace(beat, key=key), _chapter_of(spans, beat.core[0]), frozenset({number}))
        )
    pool = RecapPool(
        candidates=candidates,
        opener=opener,
        closer=closer,
        chapter_stars=stars,
        chapter_spans=spans,
        point_stars={number: star for number, _, star in outline_cores},
        framing=frozenset(framing),
        video_format=video_format,
        point_max=point_max,
        late_from=_late_from(index, outline, video_format, spans, stars, framing),
        slices=coverage_slices(index, outline, framing, budget) if whole_video else (),
        max_points=max_points,
    )
    forced = _must_play(pool, budget)
    try:
        beats = _choose(index, pool, forced, budget, cleanup)
        if forced and recap_seconds(index, beats, cleanup) < budget.min:
            raise WorkflowError("recap_unsupported", _RECAP_UNSUPPORTED)
    except WorkflowError:
        if not forced:
            raise
        beats = _choose(index, pool, frozenset(), budget, cleanup)
    return _recap_cut(index, beats, budget, cleanup, pool)


def _late_opener_plays_in_order(index: UnitIndex, opener: Beat, outline: Any) -> bool:
    """A line from after the midpoint opens only as a question that gives nothing away."""
    midpoint = index.unit(len(index)).end / 2
    if index.unit(opener.full[0]).start <= midpoint:
        return False
    if not index.unit(opener.full[1]).ends_question:
        return True
    return any(
        _overlaps(opener.full, _span(index, int(_field(payoff, "first_unit", 1)), int(_field(payoff, "last_unit", 1))))
        for payoff in list(_field(outline, "payoffs", None) or [])
    )


def _late_from(
    index: UnitIndex,
    outline: Any,
    video_format: str,
    spans: Sequence[tuple[int, int]],
    stars: Sequence[int],
    framing: Collection[int],
) -> Optional[int]:
    """Where a story's ending starts: 75 % of the source, or its last substantial chapter when that starts earlier."""
    if video_format not in _ENDING_FORMATS or not spans:
        return None
    late = index.unit_at(RECAP_LATE_SHARE * index.unit(len(index)).end)
    for (first, last), star in zip(reversed(spans), reversed(stars)):
        if star >= 3 and not all(uid in framing for uid in range(first, last + 1)):
            return min(late, first)
    return late


def _must_play(pool: RecapPool, budget: Budget) -> frozenset[int]:
    """Candidates the recap can't leave out: what answers its opener, and every ★5 outline point that fits."""
    forced = {candidate.key for candidate in pool.candidates if candidate.answers_opener}
    five_star = [number for number, stars in pool.point_stars.items() if stars >= 5]
    if five_star and len(five_star) * FIVE_STAR_POINT_SECONDS <= budget.max:
        for number in five_star:
            carrier = next((c for c in pool.candidates if number in c.outline_points), None)
            if carrier is not None:
                forced.add(carrier.key)
    # Every stretch of the video plays, by its best candidate (an opener or closer from there counts).
    framed = [beat.full for beat in (pool.opener, pool.closer) if beat is not None]
    chosen = {c.key: c.beat.full for c in pool.candidates if c.key in forced}
    for part in missing_slices(pool.slices, framed + list(chosen.values())):
        inside = [c for c in pool.candidates if _overlaps(c.beat.core, part)]
        if inside:
            best = max(inside, key=lambda c: (_candidate_value(pool, c), -c.beat.first))
            forced.add(best.key)
    if pool.max_points is not None and len(forced) > pool.max_points:
        ranked = sorted(
            (c for c in pool.candidates if c.key in forced), key=lambda c: -_candidate_value(pool, c)
        )
        forced = {c.key for c in ranked[: pool.max_points]}
    return frozenset(forced)


def _attention(index: UnitIndex, beats: list[Beat]) -> list[int]:
    """1-based beats that still open on an unheard reference and could be dropped."""
    return [
        position + 1
        for position in range(1, len(beats))
        if beats[position].droppable
        and beats[position].first - 1 != beats[position - 1].last
        and dangling(index, beats[position].first)
    ]


def _recap_cut(
    index: UnitIndex, beats: list[Beat], budget: Budget, cleanup: Optional[dict], pool: Optional[RecapPool]
) -> RecapCut:
    seconds = recap_seconds(index, beats, cleanup)
    # Opener and closer alone frame nothing: a recap plays at least one point.
    if len(beats) < 2 or not any(beat.carries_point for beat in beats):
        raise WorkflowError("recap_unsupported", _RECAP_UNSUPPORTED)
    if seconds > budget.max + 1e-6:
        raise WorkflowError("recap_unsupported", _RECAP_UNSUPPORTED)
    notes: list[dict[str, Any]] = []
    if budget.shortened:
        notes.append({"code": "TARGET_SHORTENED", "params": {}})
    elif seconds < budget.min:
        # Never shown to users ("the video is short" would be false here); the eval flags it.
        logger.warning("Recap is %.1fs, under its %.1fs window", seconds, budget.min)
        notes.append({"code": "RECAP_UNDER_WINDOW", "params": {"seconds": round(seconds, 1), "min": budget.min}})
    return RecapCut(
        beats=beats, seconds=round(seconds, 2), notes=notes, attention=_attention(index, beats), pool=pool
    )


# -- trailer --------------------------------------------------------------------

TrailerRole = Literal["cold_open", "context", "escalation", "button"]


@dataclass(frozen=True)
class TrailerBeatIn:
    role: TrailerRole
    first: int
    last: int
    why: str = ""


@dataclass(frozen=True)
class TrailerBeat:
    role: TrailerRole
    first: int
    last: int
    why: str = ""


@dataclass
class TrailerCut:
    beats: list[TrailerBeat]  # play order; may jump around the source
    seconds: float  # estimated rendered length, junction fades included
    budget: Budget
    dropped: list[TrailerBeatIn] = field(default_factory=list)  # beats that touched a spoiler

    @property
    def fits(self) -> bool:
        return self.budget.min <= self.seconds <= self.budget.max

    @property
    def usable(self) -> bool:
        """At least 3 beats inside the window, opening cold and ending on the button."""
        return (
            len(self.beats) >= 3
            and self.fits
            and self.beats[0].role == "cold_open"
            and self.beats[-1].role == "button"
        )


# Share of coarse lines that must end on punctuation before a line without it counts as mid-phrase.
_COARSE_PUNCTUATED_SHARE = 0.3
_end_floors: dict[str, float] = {}


def _end_floor(index: UnitIndex) -> float:
    """Weakest end a trailer beat may stop on: a sentence end (coarse lines without punctuation when none have it)."""
    if not index.coarse:
        return STRONG_BOUNDARY
    floor = _end_floors.get(index.fingerprint)
    if floor is None:
        strong = sum(1 for uid in range(1, len(index) + 1) if index.boundary_strength(uid) >= STRONG_BOUNDARY)
        floor = STRONG_BOUNDARY if strong >= _COARSE_PUNCTUATED_SHARE * len(index) else 0.6
        _end_floors[index.fingerprint] = floor
    return floor


def trailer_beat_min(role: str) -> float:
    return TRAILER_BEAT_MIN_SECONDS if role == "cold_open" else TRAILER_SHORT_BEAT_MIN_SECONDS


def trailer_start_ok(index: UnitIndex, uid: int) -> bool:
    """A beat may start here: a sentence opens, or a clause that stands alone after a real pause."""
    if uid <= 1 or index.unit(uid).starts_sentence:
        return True
    if index.coarse and _end_floor(index) >= STRONG_BOUNDARY:
        return False  # a punctuated line that continues the one before it starts mid-sentence
    return index.boundary_strength(uid - 1) >= USABLE_BOUNDARY and not opens_with_back_reference(
        index.unit(uid).text, index.language
    )


def trailer_end_ok(index: UnitIndex, uid: int, role: str) -> bool:
    """A beat may end here: a sentence ends; only the button may stop on a hanging clause or a question."""
    unit = index.unit(uid)
    if unit.end_kind == "forced":
        return False
    strength = index.boundary_strength(uid)
    if strength >= _end_floor(index) or unit.ends_question:
        return True
    return (
        role == "button"
        and strength >= TRAILER_BEAT_END_STRENGTH
        and bool(CLAUSE_MARK_RE.search(unit.text.strip()))
    )


def _resnap(
    index: UnitIndex, beat: TrailerBeat, blocked: set[int], taken: list[tuple[int, int]], limit: float, cleanup: Optional[dict]
) -> Optional[TrailerBeat]:
    """The beat with a start a listener can follow and an end that lands, within two units; None if impossible."""

    def free(uid: int) -> bool:
        return 1 <= uid <= len(index) and uid not in blocked and not any(low <= uid <= high for low, high in taken)

    first, last = beat.first, beat.last
    if not trailer_start_ok(index, first):
        back = [uid for uid in range(first - 1, first - 3, -1) if free(uid) and all(free(u) for u in range(uid, first))]
        start = next(
            (uid for uid in back if index.unit(uid).starts_sentence and index.speech_seconds(uid, last, (), cleanup) <= limit),
            None,
        )
        if start is None:
            start = next((uid for uid in range(first + 1, last + 1) if index.unit(uid).starts_sentence), None)
        if start is None:
            return None
        first = start
    if not trailer_end_ok(index, last, beat.role):
        if beat.role == "button":
            end = next((uid for uid in range(last - 1, first - 1, -1) if trailer_end_ok(index, uid, beat.role)), None)
        else:
            ahead = [
                uid
                for uid in range(last + 1, last + 3)
                if all(free(u) for u in range(last + 1, uid + 1)) and index.speech_seconds(first, uid, (), cleanup) <= limit
            ]
            end = next((uid for uid in ahead if trailer_end_ok(index, uid, beat.role)), None)
            if end is None:
                end = next((uid for uid in range(last - 1, first - 1, -1) if trailer_end_ok(index, uid, beat.role)), None)
        if end is None:
            return None
        last = end
    return replace(beat, first=first, last=last)


def trailer_seconds(index: UnitIndex, beats: Sequence[TrailerBeat], cleanup: Optional[dict] = None) -> float:
    if not beats:
        return 0.0
    speech = sum(index.speech_seconds(beat.first, beat.last, (), cleanup) for beat in beats)
    return speech - TRAILER_JUNCTION_FADE * (len(beats) - 1)


def fit_trailer(
    index: UnitIndex,
    beats: Sequence[TrailerBeatIn],
    spoilers: Collection[int],
    budget: Budget,
    cleanup: Optional[dict] = None,
    *,
    source_order: bool = True,
) -> TrailerCut:
    """Fit the trailer's beats to the window without touching a spoiler.

    ``spoilers`` holds every unit id the trailer must not use: the outline's
    payoffs and arc endings plus the model's own payoffs. Beats touching one
    are dropped; beats continuing each other in source and play order merge
    when the result still fits a beat; the cold open plays first and the button
    last. ``source_order`` keeps the middle beats in the video's order (stories);
    otherwise they keep the model's order of rising tension. Over the window,
    the longest middle beats go, then beats are trimmed. Under it, escalation
    and context beats grow by a clause. The cut may still be ``not usable``
    (fewer than 3 beats or outside the window): the planner then repairs or
    falls back.
    """
    blocked = set(spoilers)
    dropped: list[TrailerBeatIn] = []
    kept: list[TrailerBeat] = []
    for beat in beats:
        if beat.first < 1 or beat.last < 1:
            continue  # -1 means "none"; clamping it would invent a beat at the start
        first, last = _span(index, beat.first, beat.last)
        if any(uid in blocked for uid in range(first, last + 1)):
            dropped.append(beat)
            continue
        if any(_overlaps((first, last), (other.first, other.last)) for other in kept):
            continue
        kept.append(TrailerBeat(beat.role, first, last, beat.why))

    # In a story only the cold open (a flash-forward) and the button jump in
    # time; the beats between them keep the video's order, so neighbours merge.
    if source_order and len(kept) >= 3:
        kept = [kept[0]] + sorted(kept[1:-1], key=lambda beat: beat.first) + [kept[-1]]
    kept = _merge_continuations(index, kept, cleanup)
    kept = _order_roles(index, kept)

    def seconds(beat: TrailerBeat) -> float:
        return index.speech_seconds(beat.first, beat.last, (), cleanup)

    def taken(exclude: int) -> list[tuple[int, int]]:
        return [(b.first, b.last) for position, b in enumerate(kept) if position != exclude]

    # Every beat starts where a listener can follow and ends where a line lands.
    snapped: list[TrailerBeat] = []
    for position, beat in enumerate(kept):
        limit = TRAILER_COLD_OPEN_MAX_SECONDS if beat.role == "cold_open" else TRAILER_BEAT_MAX_SECONDS
        fixed = _resnap(index, beat, blocked, [(b.first, b.last) for b in snapped] + taken(position)[position:], limit, cleanup)
        if fixed is not None:
            snapped.append(fixed)
    kept = snapped

    # Each beat within its own length limits.
    for position, beat in enumerate(kept):
        limit = TRAILER_COLD_OPEN_MAX_SECONDS if beat.role == "cold_open" else TRAILER_BEAT_MAX_SECONDS
        while seconds(beat) > limit and beat.first < beat.last:
            beat = _shorten(index, beat)
        if seconds(beat) < trailer_beat_min(beat.role):
            longer = _lengthen(index, beat, blocked, taken(position), limit, cleanup)
            beat = longer or beat
        kept[position] = beat

    total = trailer_seconds(index, kept, cleanup)
    if total > budget.max:
        # The longest middle beat goes first: never by its place in the video,
        # which would keep a slow setup over a late hit.
        while len(kept) > 3 and trailer_seconds(index, kept, cleanup) > budget.max:
            kept.pop(max(range(1, len(kept) - 1), key=lambda position: seconds(kept[position])))
        while trailer_seconds(index, kept, cleanup) > budget.max:
            candidates = [
                position
                for position, beat in enumerate(kept)
                if beat.first < beat.last and seconds(_shorten(index, beat)) >= trailer_beat_min(beat.role)
            ]
            if not candidates:
                break
            position = max(candidates, key=lambda position: seconds(kept[position]))
            kept[position] = _shorten(index, kept[position])
    elif total < budget.min:
        for roles in (("escalation",), ("context",)):
            progress = True
            while progress and trailer_seconds(index, kept, cleanup) < budget.min:
                progress = False
                for position, beat in enumerate(kept):
                    if beat.role not in roles:
                        continue
                    longer = _lengthen(
                        index, beat, blocked, taken(position), TRAILER_BEAT_MAX_SECONDS, cleanup
                    )
                    if longer is None:
                        continue
                    trial = kept[:position] + [longer] + kept[position + 1 :]
                    if trailer_seconds(index, trial, cleanup) > budget.max:
                        continue
                    kept[position] = longer
                    progress = True
                    if trailer_seconds(index, kept, cleanup) >= budget.min:
                        break

    return TrailerCut(
        beats=kept, seconds=round(trailer_seconds(index, kept, cleanup), 2), budget=budget, dropped=dropped
    )


def _merge_continuations(index: UnitIndex, beats: list[TrailerBeat], cleanup: Optional[dict]) -> list[TrailerBeat]:
    """Beats that continue each other become one, unless the result no longer fits a beat."""
    merged: list[TrailerBeat] = []
    for beat in beats:
        previous = merged[-1] if merged else None
        if previous is not None and beat.first == previous.last + 1:
            role: TrailerRole = (
                "button"
                if beat.role == "button"
                else "cold_open"
                if previous.role == "cold_open"
                else previous.role
            )
            limit = TRAILER_COLD_OPEN_MAX_SECONDS if role == "cold_open" else TRAILER_BEAT_MAX_SECONDS
            if index.speech_seconds(previous.first, beat.last, (), cleanup) > limit:
                merged.append(beat)
                continue
            why = "; ".join(text for text in (previous.why, beat.why) if text)
            merged[-1] = TrailerBeat(role, previous.first, beat.last, why)
        else:
            merged.append(beat)
    return merged


def _order_roles(index: UnitIndex, beats: list[TrailerBeat]) -> list[TrailerBeat]:
    """One cold open first and one button last, borrowing the best candidates."""
    if not beats:
        return beats
    beats = list(beats)
    if beats[0].role != "cold_open":
        cold = next((position for position, beat in enumerate(beats) if beat.role == "cold_open"), None)
        if cold is not None:
            beats.insert(0, beats.pop(cold))
        else:
            beats[0] = replace(beats[0], role="cold_open")
    if len(beats) > 1 and beats[-1].role != "button":
        button = next(
            (position for position in range(len(beats) - 1, 0, -1) if beats[position].role == "button"), None
        )
        if button is None:
            button = next(
                (
                    position
                    for position in range(len(beats) - 1, 0, -1)
                    if index.unit(beats[position].last).ends_question
                ),
                None,
            )
        if button is None:
            escalations = [
                position for position in range(1, len(beats)) if beats[position].role == "escalation"
            ]
            button = max(escalations, key=lambda position: beats[position].first, default=None)
        moved = beats.pop(button) if button is not None else beats.pop()
        beats.append(replace(moved, role="button"))
    # Any other cold open or button becomes a middle beat.
    for position in range(1, len(beats) - 1):
        if beats[position].role == "cold_open":
            beats[position] = replace(beats[position], role="context")
        elif beats[position].role == "button":
            beats[position] = replace(beats[position], role="escalation")
    return beats


def _shorten(index: UnitIndex, beat: TrailerBeat) -> TrailerBeat:
    """Shorter by at least one unit. A button keeps its last line (the hanging question), so it
    starts at the next sentence; other beats end on the last earlier line that lands."""
    if beat.first >= beat.last:
        return beat
    if beat.role == "button":
        start = next((uid for uid in range(beat.first + 1, beat.last + 1) if index.unit(uid).starts_sentence), None)
        return replace(beat, first=start if start is not None else beat.first + 1)
    for end in range(beat.last - 1, beat.first - 1, -1):
        if trailer_end_ok(index, end, beat.role):
            return replace(beat, last=end)
    for end in range(beat.last - 1, beat.first - 1, -1):
        if index.boundary_strength(end) >= TRAILER_BEAT_END_STRENGTH:
            return replace(beat, last=end)
    return replace(beat, last=beat.last - 1)


def _lengthen(
    index: UnitIndex,
    beat: TrailerBeat,
    blocked: set[int],
    taken: list[tuple[int, int]],
    limit: float,
    cleanup: Optional[dict],
) -> Optional[TrailerBeat]:
    """One or two clauses longer, ending on a usable cut, clear of spoilers and other beats.

    A button grows backward so it still ends on its hanging line.
    """
    for step in (1, 2):
        if beat.role == "button":
            first, last = beat.first - step, beat.last
            added = range(first, beat.first)
        else:
            first, last = beat.first, beat.last + step
            added = range(beat.last + 1, last + 1)
        if first < 1 or last > len(index):
            return None
        if any(uid in blocked for uid in added) or any(_overlaps((first, last), span) for span in taken):
            return None
        if beat.role != "button" and not trailer_end_ok(index, last, beat.role):
            continue
        if beat.role == "button" and not index.unit(first).starts_sentence:
            continue
        if index.speech_seconds(first, last, (), cleanup) > limit:
            return None
        return replace(beat, first=first, last=last)
    return None
