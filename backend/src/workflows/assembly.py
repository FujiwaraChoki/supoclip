"""Deterministic assembly: fit topic spans, recaps and trailers to their budgets.

Planners hand over unit ids from the model; the functions here own the hard
constraints (lengths, coherence at the joins, spoilers) so the result never
depends on the model counting seconds. Inputs are plain dataclasses, so this
module never imports the LLM schemas.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Collection, Iterable, Literal, Optional, Sequence

from .boundaries import STRONG_BOUNDARY
from .durations import Budget
from .errors import WorkflowError
from .language import opens_with_back_reference
from .units import UnitIndex

# Junction fades the planners put between stitched ranges. A recap dissolves
# softly; a trailer gets a declick that reads as a hard cut. Totals subtract
# them because each fade overlaps the two sides.
RECAP_JUNCTION_FADE = 0.12
TRAILER_JUNCTION_FADE = 0.04

RECAP_OPENER_MAX_SECONDS = 10.0
# A recap needs this much speech in the whole source to say anything.
RECAP_MIN_SOURCE_SPEECH_SECONDS = 60.0

TRAILER_BEAT_MIN_SECONDS = 1.5
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


@dataclass(frozen=True)
class RecapPointIn:
    label: str
    first: int  # fullest clear statement
    last: int
    core_first: int  # shortest self-contained statement inside it
    core_last: int
    importance: int  # 1 = essential, 2 = important, 3 = nice to have


@dataclass(frozen=True)
class RecapDraftIn:
    title: str
    opener: Optional[tuple[int, int]]  # frames the video; plays first
    points: Sequence[RecapPointIn]  # in video order
    closer: Optional[tuple[int, int]]  # lands the takeaway; None when the last point concludes


@dataclass(frozen=True)
class RecapFixIn:
    beat: int  # 1-based, in the play order of the cut being fixed
    action: Literal["extend_back", "use_full", "use_core", "drop_beat", "none"]


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
class RecapCut:
    beats: list[Beat]  # play order: opener, points in video order, closer
    seconds: float  # estimated rendered length, junction fades included
    notes: list[dict[str, Any]] = field(default_factory=list)
    attention: list[int] = field(default_factory=list)  # 1-based beats the critique should check


def recap_seconds(index: UnitIndex, beats: Sequence[Beat], cleanup: Optional[dict] = None) -> float:
    if not beats:
        return 0.0
    speech = sum(index.speech_seconds(beat.first, beat.last, (), cleanup) for beat in beats)
    return speech - RECAP_JUNCTION_FADE * (len(beats) - 1)


def _outline_points(outline: Any) -> list[Any]:
    points = getattr(outline, "main_points", outline)
    return list(points or [])


def _field(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(name, default)
    return getattr(item, name, default)


def _normalize_points(index: UnitIndex, points: Iterable[RecapPointIn]) -> list[Beat]:
    beats: list[Beat] = []
    for point in points:
        if point.first < 1 or point.last < 1:
            continue  # -1 means "none"; clamping it would invent a point at the start
        full = _span(index, point.first, point.last)
        core = _span(index, point.core_first, point.core_last)
        if min(point.core_first, point.core_last) < 1 or not (full[0] <= core[0] and core[1] <= full[1]):
            core = full
        importance = min(max(int(point.importance), 1), 3)
        beats.append(Beat("point", (point.label or "").strip(), full, core, "core", importance))
    beats.sort(key=lambda beat: beat.full)
    merged: list[Beat] = []
    for beat in beats:
        previous = merged[-1] if merged else None
        # Touching or one unit apart: one statement, not two.
        if previous and beat.full[0] <= previous.full[1] + 2:
            labels = [previous.label] + ([beat.label] if beat.label and beat.label != previous.label else [])
            merged[-1] = replace(
                previous,
                label=" · ".join(label for label in labels if label),
                full=(previous.full[0], max(previous.full[1], beat.full[1])),
                core=(min(previous.core[0], beat.core[0]), max(previous.core[1], beat.core[1])),
                importance=min(previous.importance, beat.importance),
            )
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


def fit_recap(
    index: UnitIndex,
    draft: RecapDraftIn,
    budget: Budget,
    outline: Any = None,
    cleanup: Optional[dict] = None,
) -> RecapCut:
    """Assemble the recap's beats to the budget window.

    Points start as their core statements. While under the target, points
    grow to their full statements (most important first), then uncovered
    outline main points join. While over the window, nice-to-have points go
    first (latest first), then important ones, then full statements shrink
    back to cores. Opener, closer and the last essential point stay.

    ``outline`` is the source outline (anything with ``main_points`` whose
    items carry ``first_unit``, ``last_unit``, ``core_first``, ``core_last``,
    ``importance`` 1-5 and ``statement``), or None.
    """
    if index.speech_seconds(1, len(index)) < RECAP_MIN_SOURCE_SPEECH_SECONDS:
        raise WorkflowError("recap_unsupported", _RECAP_UNSUPPORTED)
    points = _normalize_points(index, draft.points)
    opener, points = _frame_beat(index, "opener", draft.opener, points)
    if opener is not None:
        trimmed = _trim_to(index, opener.full, RECAP_OPENER_MAX_SECONDS, cleanup)
        opener = replace(opener, full=trimmed, core=trimmed) if trimmed else None
    closer, points = _frame_beat(index, "closer", draft.closer, points)
    if closer is not None and points and closer.full[1] < points[-1].full[0]:
        closer = None
    if closer is not None and opener is not None and _overlaps(closer.full, opener.full):
        closer = None

    def assemble(middle: list[Beat]) -> list[Beat]:
        return (
            ([opener] if opener else [])
            + sorted(middle, key=lambda beat: beat.full)
            + ([closer] if closer else [])
        )

    def total(middle: list[Beat]) -> float:
        return recap_seconds(index, assemble(middle), cleanup)

    def closer_to_target(candidate: list[Beat]) -> bool:
        before, after = total(points), total(candidate)
        return after <= budget.max and abs(after - budget.ideal) < abs(before - budget.ideal)

    # Grow toward the target, most important first; a step that would
    # overshoot further than it helps is skipped for a smaller one.
    for position in sorted(range(len(points)), key=lambda position: (points[position].importance, position)):
        if total(points) >= budget.ideal:
            break
        grown = points[:position] + [replace(points[position], span_mode="full")] + points[position + 1 :]
        if closer_to_target(grown):
            points = grown
    for item in _outline_points(outline):
        if total(points) >= budget.ideal:
            break
        first_unit, last_unit = int(_field(item, "first_unit", -1)), int(_field(item, "last_unit", -1))
        if first_unit < 1 or last_unit < 1:
            continue
        full = _span(index, first_unit, last_unit)
        core = _span(index, int(_field(item, "core_first", full[0])), int(_field(item, "core_last", full[1])))
        if not (full[0] <= core[0] and core[1] <= full[1]):
            core = full
        taken = [beat.full for beat in assemble(points)]
        if any(full[0] <= span[1] + 1 and span[0] <= full[1] + 1 for span in taken):
            continue
        if closer is not None and full[1] >= closer.full[0]:
            continue
        importance = 2 if int(_field(item, "importance", 3) or 3) >= 4 else 3
        label = str(_field(item, "statement", "") or "").strip()
        for mode in ("full", "core"):
            candidate = points + [Beat("point", label, full, core, mode, importance, from_outline=True)]
            if closer_to_target(candidate):
                points = candidate
                break

    points = _shrink(index, points, assemble, budget, cleanup)
    beats = assemble(points)
    beats, attention = _bridge_openings(index, beats, budget, cleanup)
    return _recap_cut(index, beats, budget, cleanup, attention)


def _shrink(
    index: UnitIndex, points: list[Beat], assemble, budget: Budget, cleanup: Optional[dict]
) -> list[Beat]:
    def total(middle: list[Beat]) -> float:
        return recap_seconds(index, assemble(middle), cleanup)

    points = sorted(points, key=lambda beat: beat.full)
    for importance in (3, 2):
        for beat in sorted(
            (b for b in points if b.importance == importance), key=lambda b: b.full, reverse=True
        ):
            if total(points) <= budget.max:
                return points
            if len(points) <= 1:
                break  # a recap is never just its opener and closer
            points = [b for b in points if b is not beat]
    for importance in (3, 2, 1):
        for beat in sorted(
            (b for b in points if b.importance == importance and b.span_mode == "full"),
            key=lambda b: b.full,
            reverse=True,
        ):
            if total(points) <= budget.max:
                return points
            points = [replace(b, span_mode="core", lead=0) if b is beat else b for b in points]
    # Still over: essential points go too, latest first, but one always stays.
    for beat in sorted(points, key=lambda b: b.full, reverse=True):
        if total(points) <= budget.max or len(points) <= 1:
            break
        points = [b for b in points if b is not beat]
    return points


def _bridge_openings(
    index: UnitIndex, beats: list[Beat], budget: Budget, cleanup: Optional[dict]
) -> tuple[list[Beat], list[int]]:
    """Give a beat that opens on a back reference the sentence before it, if it fits."""
    attention: list[int] = []
    for position in range(1, len(beats)):
        beat, previous = beats[position], beats[position - 1]
        if beat.first - 1 == previous.last or not opens_with_back_reference(
            index.unit(beat.first).text, index.language
        ):
            continue
        extended = replace(beat, lead=beat.lead + 1)
        others = [other for other_position, other in enumerate(beats) if other_position != position]
        fits = extended.first >= 1 and not any(
            _overlaps((extended.first, extended.last), (o.first, o.last)) for o in others
        )
        candidate = beats[:position] + [extended] + beats[position + 1 :]
        if fits and recap_seconds(index, candidate, cleanup) <= budget.max:
            beats = candidate
        elif beat.droppable:
            attention.append(position + 1)
    return beats, attention


def _recap_cut(
    index: UnitIndex, beats: list[Beat], budget: Budget, cleanup: Optional[dict], attention: list[int]
) -> RecapCut:
    seconds = recap_seconds(index, beats, cleanup)
    # Only a long closer or opener around a single point can still be over:
    # the window is a hard limit, so they give way (closer first).
    for kind in ("closer", "opener"):
        if seconds <= budget.max or len(beats) <= 2:
            break
        if any(beat.kind == kind for beat in beats):
            beats = [beat for beat in beats if beat.kind != kind]
            attention = []
            seconds = recap_seconds(index, beats, cleanup)
    # Opener and closer alone frame nothing: a recap plays at least one point.
    if len(beats) < 2 or not any(beat.carries_point for beat in beats):
        raise WorkflowError("recap_unsupported", _RECAP_UNSUPPORTED)
    if seconds > budget.max:
        raise WorkflowError("recap_unsupported", _RECAP_UNSUPPORTED)
    notes = [{"code": "TARGET_SHORTENED", "params": {}}] if budget.shortened or seconds < budget.min else []
    return RecapCut(beats=beats, seconds=round(seconds, 2), notes=notes, attention=attention)


def apply_recap_fixes(
    index: UnitIndex,
    cut: RecapCut,
    fixes: Sequence[RecapFixIn],
    budget: Budget,
    cleanup: Optional[dict] = None,
) -> RecapCut:
    """Apply the coherence critique's fixes, then re-fit to the window.

    ``extend_back`` adds the unit before a beat when it still fits;
    ``use_full``/``use_core`` switch a point's span; ``drop_beat`` removes a
    droppable point. Invalid fixes are ignored.
    """
    beats = list(cut.beats)
    drops: set[int] = set()
    for fix in fixes:
        position = fix.beat - 1
        if not 0 <= position < len(beats) or fix.action == "none":
            continue
        beat = beats[position]
        if fix.action == "drop_beat":
            carriers = [other for other, b in enumerate(beats) if b.carries_point and other not in drops]
            # Never the last beat that plays a point.
            if beat.droppable and carriers != [position]:
                drops.add(position)
        elif fix.action in ("use_full", "use_core") and beat.kind == "point":
            beats[position] = replace(beat, span_mode="full" if fix.action == "use_full" else "core", lead=0)
        elif fix.action == "extend_back":
            extended = replace(beat, lead=beat.lead + 1)
            others = [(o.first, o.last) for other, o in enumerate(beats) if other != position]
            candidate = beats[:position] + [extended] + beats[position + 1 :]
            if (
                extended.first >= 1
                and not any(_overlaps((extended.first, extended.last), other) for other in others)
                and recap_seconds(index, candidate, cleanup) <= budget.max
            ):
                beats = candidate
    beats = [beat for position, beat in enumerate(beats) if position not in drops]
    opener = [beat for beat in beats if beat.kind == "opener"]
    closer = [beat for beat in beats if beat.kind == "closer"]
    points = [beat for beat in beats if beat.kind == "point"]

    def assemble(middle: list[Beat]) -> list[Beat]:
        return opener + sorted(middle, key=lambda beat: beat.full) + closer

    points = _shrink(index, points, assemble, budget, cleanup)
    return _recap_cut(index, assemble(points), budget, cleanup, [])


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
) -> TrailerCut:
    """Fit the trailer's beats to the window without touching a spoiler.

    ``spoilers`` holds every unit id the trailer must not use: the outline's
    payoffs and arc endings plus the model's own payoffs. Beats touching one
    are dropped; beats continuing each other in source and play order merge;
    the cold open plays first and the button last. Over the window, escalation
    beats go (latest first), then context beyond the first, then beats are
    trimmed. Under it, escalation and context beats grow by a clause. The cut
    may still be ``not usable`` (fewer than 3 beats or outside the window):
    the planner then repairs or falls back.
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

    kept = _merge_continuations(kept)
    kept = _order_roles(index, kept)

    def seconds(beat: TrailerBeat) -> float:
        return index.speech_seconds(beat.first, beat.last, (), cleanup)

    def taken(exclude: int) -> list[tuple[int, int]]:
        return [(b.first, b.last) for position, b in enumerate(kept) if position != exclude]

    # Each beat within its own length limits.
    for position, beat in enumerate(kept):
        limit = TRAILER_COLD_OPEN_MAX_SECONDS if beat.role == "cold_open" else TRAILER_BEAT_MAX_SECONDS
        while seconds(beat) > limit and beat.first < beat.last:
            beat = _shorten(index, beat)
        if seconds(beat) < TRAILER_BEAT_MIN_SECONDS:
            longer = _lengthen(index, beat, blocked, taken(position), limit, cleanup)
            beat = longer or beat
        kept[position] = beat

    total = trailer_seconds(index, kept, cleanup)
    if total > budget.max:
        for role in ("escalation", "context"):
            removable = [
                position
                for position, beat in enumerate(kept)
                if beat.role == role and 0 < position < len(kept) - 1
            ]
            if role == "context":
                first_context = min(removable, default=None)
                removable = [position for position in removable if position != first_context]
            for position in sorted(removable, reverse=True):
                if trailer_seconds(index, kept, cleanup) <= budget.max:
                    break
                kept.pop(position)
        while trailer_seconds(index, kept, cleanup) > budget.max:
            candidates = [
                position
                for position, beat in enumerate(kept)
                if beat.first < beat.last and seconds(_shorten(index, beat)) >= TRAILER_BEAT_MIN_SECONDS
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


def _merge_continuations(beats: list[TrailerBeat]) -> list[TrailerBeat]:
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
    """One unit shorter. A button keeps its last line (the hanging question), so it loses its start."""
    if beat.first >= beat.last:
        return beat
    if beat.role == "button":
        return replace(beat, first=beat.first + 1)
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
        if beat.role != "button" and index.boundary_strength(last) < TRAILER_BEAT_END_STRENGTH:
            continue
        if index.speech_seconds(first, last, (), cleanup) > limit:
            return None
        return replace(beat, first=first, last=last)
    return None
