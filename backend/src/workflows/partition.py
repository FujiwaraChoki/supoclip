"""Deterministic series partitioning: where each part ends.

The model drafts part ends (unit ids) and skips. This solver owns the hard
rules: every part is ``budget.min..budget.max`` seconds of speech, parts are
consecutive (the next part starts at the unit right after the previous end,
so adjacent parts share one exact cut), cuts land on strong boundaries, and
skips never remove more than 40 % of a part (sponsor reads aside). A dynamic program picks the
cheapest cuts near the model's choices; a relaxation ladder keeps it from
failing on awkward material.
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .boundaries import STRONG_BOUNDARY, USABLE_BOUNDARY
from .durations import SERIES_MAX_PARTS, SERIES_MIN_PARTS, Budget
from .errors import WorkflowError
from .language import (
    CLAUSE_MARK_RE,
    forward_hook,
    is_acknowledgement,
    normalize_language_code,
    opens_with_back_reference,
    resolution_closer,
)
from .units import UnitIndex, format_clock

# Skips shorter than this aren't worth a jump cut.
SKIP_MIN_SECONDS = 3.0
# Skips may remove at most this share of a part's source time.
SKIP_MAX_SHARE = 0.4
# Soft cost per second outside the ideal range: over-long parts hurt more in a feed than short ones.
SHORT_PART_COST = 0.15
LONG_PART_COST = 0.4
# A part opening on "he"/"that's why" leaves a feed viewer lost (a question
# just asked and answered by someone else is fine).
BACK_REFERENCE_COST = 2.5
# A part ending on a line that points ahead ("Rule five is next.") is a free cliffhanger;
# one ending on a wrap-up ("So that's the first rule.") closes its own loop; one
# ending on neither leaves the loop to its label.
FORWARD_HOOK_BONUS = 1.5
RESOLUTION_COST = 0.75
QUESTION_BONUS = 1.5
FLAT_END_COST = 0.75
# Staying near the model's end, per 5 s away; a real question or hook may pull further.
LLM_DISTANCE_WEIGHT = 1.5
LOOP_DISTANCE_WEIGHT = 0.5
# Chapter ends are natural cuts; a cut one or two units before one leaves the sign-off to open the next part.
CHAPTER_END_BONUS = 0.25
BEFORE_CHAPTER_END_COST = 1.0
# A part straddling a chapter boundary pays per second of its shorter side (beyond a short teaser).
STRADDLE_MIN_SECONDS = 20.0
STRADDLE_COST_PER_SECOND = 0.03
# A jump across a left-out chapter costs far more: parts break at jumps, yet
# may end on the next chapter's opening question.
STRADDLE_GAP_COST_PER_SECOND = 0.15
# The last part never plays as a montage.
LAST_PART_SKIP_MAX_SHARE = 0.25
# A unit this short, or one that only thanks or agrees, never opens a part.
WEAK_OPENER_MAX_WORDS = 4
WEAK_OPENER_COST = 3.0

_INFEASIBLE = math.inf
_BARE_CONJUNCTIONS = {
    "en": {"and", "but", "or", "so", "because", "that", "which", "if"},
    "de": {"und", "aber", "oder", "weil", "dass", "wenn"},
}


@dataclass
class PartitionConstraints:
    pinned_ends: set[int] = field(default_factory=set)  # unit ids that must end a part
    forbidden_ends: set[int] = field(default_factory=set)  # unit ids that must not


@dataclass
class PartitionResult:
    parts: list[tuple[int, int]]  # (first_unit, last_unit) per part, consecutive on the spine
    skips_by_part: list[list[tuple[int, int]]]  # skips strictly inside each part
    notes: list[dict[str, Any]]
    relaxed: bool  # True when any rule was relaxed to find a solution
    seconds: list[float] = field(default_factory=list)  # speech_seconds of each part
    skips: list[tuple[int, int]] = field(default_factory=list)  # the skips the solution used


def _weak_opener(index: UnitIndex, uid: int) -> bool:
    """A unit that can't open a part: four words or fewer, or one that only thanks or agrees."""
    unit = index.unit(uid)
    if is_acknowledgement(unit.text, index.language):
        return True
    return not index.coarse and unit.w0 >= 0 and unit.w1 - unit.w0 + 1 <= WEAK_OPENER_MAX_WORDS


def soft_end(index: UnitIndex, uid: int) -> bool:
    """A line that can't end a part: it stops on a comma, or on a bare conjunction."""
    text = index.unit(uid).text.strip()
    if CLAUSE_MARK_RE.search(text):
        return True
    words = _BARE_CONJUNCTIONS.get(normalize_language_code(index.language) or "", ())
    return bool(text) and text.split()[-1].casefold().strip(".,;:!?…") in words


def validate_skips(
    index: UnitIndex, span: tuple[int, int], skips: Sequence[tuple[int, int]]
) -> list[tuple[int, int]]:
    """Skips the partitioner can use: inside the span, merged, >= 3 s, never at the span's edges."""
    first, last = span
    clamped: list[tuple[int, int]] = []
    for skip_first, skip_last in skips:
        low, high = sorted((int(skip_first), int(skip_last)))
        low, high = max(low, first), min(high, last)
        if low <= high:
            clamped.append((low, high))
    merged: list[tuple[int, int]] = []
    for low, high in sorted(clamped):
        if merged and low <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    return [
        (low, high)
        for low, high in merged
        if low > first and high < last and index.speech_seconds(low, high) >= SKIP_MIN_SECONDS
    ]


class _Spine:
    """Span units minus skipped units, with O(1) lengths of any stretch of it.

    ``exempt`` skips (sponsor reads) are removed like any other but never
    count toward a part's skipped share: they are not story the part loses.
    """

    def __init__(
        self,
        index: UnitIndex,
        span: tuple[int, int],
        skips: list[tuple[int, int]],
        cleanup: Optional[dict],
        exempt: frozenset[tuple[int, int]] = frozenset(),
    ):
        skipped = {uid for low, high in skips for uid in range(low, high + 1)}
        self.ids = [uid for uid in range(span[0], span[1] + 1) if uid not in skipped]
        self.position = {uid: position for position, uid in enumerate(self.ids)}
        clock = index.clock(cleanup)
        self.run_of: list[int] = []
        runs: list[list[int]] = []
        for uid in self.ids:
            if runs and uid == runs[-1][1] + 1:
                runs[-1][1] = uid
            else:
                runs.append([uid, uid])
            self.run_of.append(len(runs) - 1)
        self._runs = runs
        self._clock = clock
        self._run_prefix = [0.0]
        for low, high in runs:
            self._run_prefix.append(self._run_prefix[-1] + clock.run_seconds(low, high))
        # Source seconds skipped between run r and run r + 1 (one skip each:
        # skips are merged and never touch).
        self._break_prefix = [0.0]
        for (_, high), (low, _) in zip(runs, runs[1:]):
            skipped_seconds = 0.0 if (high + 1, low - 1) in exempt else index.cut_out[low - 1] - index.cut_in[high + 1]
            self._break_prefix.append(self._break_prefix[-1] + skipped_seconds)
        self._cut_in = index.cut_in
        self._cut_out = index.cut_out

    def __len__(self) -> int:
        return len(self.ids)

    def seconds(self, p: int, q: int) -> float:
        first, last = self.ids[p], self.ids[q]
        run_p, run_q = self.run_of[p], self.run_of[q]
        if run_p == run_q:
            return self._clock.run_seconds(first, last)
        head = self._clock.run_seconds(first, self._runs[run_p][1])
        tail = self._clock.run_seconds(self._runs[run_q][0], last)
        return head + (self._run_prefix[run_q] - self._run_prefix[run_p + 1]) + tail

    def skipped_share(self, p: int, q: int) -> float:
        run_p, run_q = self.run_of[p], self.run_of[q]
        if run_p == run_q:
            return 0.0
        skipped = self._break_prefix[run_q] - self._break_prefix[run_p]
        total = self._cut_out[self.ids[q]] - self._cut_in[self.ids[p]]
        return skipped / total if total > 0 else 0.0

    def skips_inside(self, p: int, q: int, skips: list[tuple[int, int]]) -> list[tuple[int, int]]:
        first, last = self.ids[p], self.ids[q]
        return [(low, high) for low, high in skips if first < low and high < last]


# What a part may end on, strictest first: a strong cut, a usable pause, then anything.
CUTS_STRONG, CUTS_USABLE, CUTS_ALL = "strong", "usable", "all"


@dataclass(frozen=True)
class _Attempt:
    skips: tuple[tuple[int, int], ...]
    constrained: bool
    cuts: str
    counts: tuple[int, ...]
    early_end: bool = False


class _Solver:
    def __init__(
        self,
        index: UnitIndex,
        span: tuple[int, int],
        budget: Budget,
        llm_ends: Sequence[int],
        constraints: PartitionConstraints,
        cleanup: Optional[dict],
        forced: frozenset[tuple[int, int]] = frozenset(),
        chapter_ends: Sequence[int] = (),
        jumps: Sequence[tuple[int, int]] = (),
        natural_endings: bool = False,
    ):
        self.natural_endings = natural_endings
        self.index = index
        self.span = span
        self.budget = budget
        self.constraints = constraints
        self.cleanup = cleanup
        self.forced = forced
        self.chapter_ends = sorted(set(chapter_ends))
        self._chapter_end_set = set(self.chapter_ends)
        self.jumps = sorted(jumps)
        # Ends that would make the next part open on "Yeah." or "Thank you.".
        self.weak_open_before = {
            uid - 1
            for uid in range(span[0] + 1, span[1] + 1)
            if _weak_opener(index, uid)
        }
        ends = sorted({min(max(int(uid), span[0]), span[1]) for uid in llm_ends})
        self.llm_ends = set(ends)
        self.llm_end_times = [index.unit(uid).end for uid in ends]
        self._spines: dict[tuple, _Spine] = {}

    def spine(self, skips: tuple[tuple[int, int], ...]) -> _Spine:
        spine = self._spines.get(skips)
        if spine is None:
            spine = self._spines[skips] = _Spine(self.index, self.span, list(skips), self.cleanup, self.forced)
        return spine

    def _cut_cost(self, spine: _Spine, q: int, all_cuts: bool) -> float:
        index = self.index
        uid = spine.ids[q]
        unit = index.unit(uid)
        following = index.unit(spine.ids[q + 1]) if q + 1 < len(spine) else None
        strength = index.boundary_strength(uid)
        chapter_end = uid in self._chapter_end_set
        asks = unit.ends_question and following is not None and (following.speaker != unit.speaker or not chapter_end)
        # "Rule two is next. Right.": the listener's reply doesn't close the hook before it.
        spoken = unit
        if q > 0 and is_acknowledgement(unit.text, index.language):
            spoken = index.unit(spine.ids[q - 1])
        hook = forward_hook(spoken.text, index.language)
        cost = 4.0 * (1.0 - strength)
        if all_cuts and strength < USABLE_BOUNDARY:
            cost *= 2.0
        if self.llm_end_times:
            end = unit.end
            nearest = min(abs(end - time) for time in self.llm_end_times)
            weight = LOOP_DISTANCE_WEIGHT if (asks or hook) and not self.natural_endings else LLM_DISTANCE_WEIGHT
            cost += weight * min(3.0, nearest / 5.0)
        if uid in self.llm_ends:
            cost -= 1.0
        if self.natural_endings:
            # The creator wants parts that finish their thought: a closing line is the goal.
            if resolution_closer(unit.text, index.language):
                cost -= QUESTION_BONUS
            elif asks or hook:
                cost += RESOLUTION_COST
        else:
            if asks:
                # An open question, not a host's segue into the next chapter.
                cost -= QUESTION_BONUS
            if hook:
                cost -= FORWARD_HOOK_BONUS
            elif resolution_closer(unit.text, index.language):
                cost += RESOLUTION_COST
            elif not asks:
                cost += FLAT_END_COST
        if chapter_end:
            cost -= CHAPTER_END_BONUS
        elif uid + 1 in self._chapter_end_set or uid + 2 in self._chapter_end_set:
            cost += BEFORE_CHAPTER_END_COST
        if following is not None and opens_with_back_reference(following.text, index.language):
            answering = (
                unit.ends_question and following.speaker is not None and following.speaker != unit.speaker
            )
            if not answering:
                cost += BACK_REFERENCE_COST
        if uid in self.weak_open_before:
            cost += WEAK_OPENER_COST
        return cost

    def _straddle_cost(self, spine: _Spine, p: int, q: int) -> float:
        """Cost of a part running across chapter boundaries and jumps, by the shorter side of each."""
        first, last = spine.ids[p], spine.ids[q]
        cost = 0.0
        for low, high in self.jumps:
            if not (first < low and high < last):
                continue
            before = bisect_left(spine.ids, low) - 1
            shorter = min(spine.seconds(p, before), spine.seconds(before + 1, q))
            if shorter > STRADDLE_MIN_SECONDS:
                cost += STRADDLE_GAP_COST_PER_SECOND * (shorter - STRADDLE_MIN_SECONDS)
        if not self.chapter_ends:
            return cost
        for end in self.chapter_ends[bisect_left(self.chapter_ends, first) :]:
            if end >= last:
                break
            if end not in spine.position:
                continue
            split = spine.position[end]
            shorter = min(spine.seconds(p, split), spine.seconds(split + 1, q))
            if shorter > STRADDLE_MIN_SECONDS:
                cost += STRADDLE_COST_PER_SECOND * shorter
        return cost

    def solve(self, attempt: _Attempt) -> Optional[tuple[int, list[tuple[int, int]], float]]:
        """Best (part count, spine position pairs, cost) for one rung of the ladder, or None."""
        spine = self.spine(attempt.skips)
        size = len(spine)
        if not size:
            return None
        budget = self.budget
        forbidden = self.constraints.forbidden_ends if attempt.constrained else set()
        pinned = sorted(
            spine.position[uid]
            for uid in (self.constraints.pinned_ends if attempt.constrained else ())
            if uid in spine.position and uid != spine.ids[-1]
        )
        pinned_prefix = [0] * (size + 1)
        pinned_set = set(pinned)
        for position in range(size):
            pinned_prefix[position + 1] = pinned_prefix[position] + (position in pinned_set)

        def can_end(q: int) -> bool:
            uid = spine.ids[q]
            if uid in forbidden:
                return False
            if attempt.cuts == CUTS_ALL:
                return True
            floor = STRONG_BOUNDARY if attempt.cuts == CUTS_STRONG else USABLE_BOUNDARY
            return (
                self.index.boundary_strength(uid) >= floor
                and uid not in self.weak_open_before
                and not soft_end(self.index, uid)
            )

        if attempt.early_end:
            finals = [
                q
                for q in range(size)
                if self.index.boundary_strength(spine.ids[q]) >= STRONG_BOUNDARY
                and spine.ids[q] not in forbidden
            ]
        else:
            finals = [size - 1]
        final_set = set(finals)
        ends = [q for q in range(size) if can_end(q) or q in final_set]
        cut_cost = {
            q: (
                0.0 if q == size - 1 and not attempt.early_end else self._cut_cost(spine, q, attempt.cuts == CUTS_ALL)
            )
            for q in ends
        }

        # Feasible parts (p..q) and their cost, shared by every part count.
        options: dict[int, list[tuple[int, float]]] = {}
        for q in ends:
            feasible: list[tuple[int, float]] = []
            for p in range(q, -1, -1):
                seconds = spine.seconds(p, q)
                if seconds > budget.max:
                    break
                if seconds < budget.min or (p and not can_end(p - 1)):
                    continue
                if pinned_prefix[q] - pinned_prefix[p] > 0:
                    # A pinned end strictly inside this part would be skipped over.
                    break
                share = spine.skipped_share(p, q)
                if share > SKIP_MAX_SHARE or (q in final_set and share > LAST_PART_SKIP_MAX_SHARE):
                    continue
                soft = SHORT_PART_COST * max(0.0, budget.ideal_min - seconds) + LONG_PART_COST * max(
                    0.0, seconds - budget.ideal_max
                )
                feasible.append((p, soft + cut_cost[q] + self._straddle_cost(spine, p, q)))
            if feasible:
                options[q] = feasible

        max_count = max(attempt.counts)
        cost = [[_INFEASIBLE] * size for _ in range(max_count + 1)]
        back = [[-1] * size for _ in range(max_count + 1)]
        for n in range(1, max_count + 1):
            row, previous = cost[n], cost[n - 1]
            for q, feasible in options.items():
                best, best_p = _INFEASIBLE, -1
                for p, part_cost in feasible:
                    if n == 1:
                        if p != 0:
                            continue
                        total = part_cost
                    else:
                        if p == 0:
                            continue
                        before = previous[p - 1]
                        if before == _INFEASIBLE:
                            continue
                        total = before + part_cost
                    if total < best:
                        best, best_p = total, p
                row[q], back[n][q] = best, best_p

        best_choice: Optional[tuple[tuple[float, float], int, int]] = None  # (rank, n, final q)
        reference = self._reference_count(spine)
        for n in attempt.counts:
            candidates = [q for q in finals if cost[n][q] < _INFEASIBLE]
            if not candidates:
                continue
            # An early end covers as much of the material as it can.
            q = max(candidates) if attempt.early_end else candidates[0]
            score = cost[n][q]
            if len(attempt.counts) > 1:
                score += 2.0 * abs(n - reference)
            rank = (-q if attempt.early_end else 0, score)
            if best_choice is None or rank < best_choice[0]:
                best_choice = (rank, n, q)
        if best_choice is None:
            return None
        (_, score), count, q = best_choice
        pairs: list[tuple[int, int]] = []
        for n in range(count, 0, -1):
            p = back[n][q]
            pairs.append((p, q))
            q = p - 1
        pairs.reverse()
        return count, pairs, score

    def _reference_count(self, spine: _Spine) -> int:
        """Part count an auto series leans toward: the model's, else total / ideal length."""
        if self.llm_ends:
            return len(self.llm_ends)
        total = spine.seconds(0, len(spine) - 1)
        return max(SERIES_MIN_PARTS, round(total / max(1.0, self.budget.ideal)))


def _skip_variants(
    skips: list[tuple[int, int]], index: UnitIndex, forced: frozenset[tuple[int, int]]
) -> list[tuple[tuple[int, int], ...]]:
    """The skips, then with the shortest optional ones dropped one at a time; forced skips always stay."""
    remaining = sorted((skip for skip in skips if skip not in forced), key=lambda skip: index.speech_seconds(*skip))
    kept = [skip for skip in skips if skip in forced]
    variants = []
    while True:
        variants.append(tuple(sorted(remaining + kept)))
        if not remaining:
            return variants
        remaining = remaining[1:]


def partition_series(
    index: UnitIndex,
    span: tuple[int, int],
    skips: Sequence[tuple[int, int]],
    budget: Budget,
    part_count: Optional[int],
    llm_ends: Sequence[int],
    constraints: Optional[PartitionConstraints] = None,
    cleanup: Optional[dict] = None,
    forced_skips: Sequence[tuple[int, int]] = (),
    chapter_ends: Sequence[int] = (),
    jumps: Sequence[tuple[int, int]] = (),
    natural_endings: bool = False,
) -> PartitionResult:
    """Split ``span`` (unit ids) into consecutive series parts.

    ``natural_endings`` prefers cuts where a thought closes over open questions.

    ``part_count`` None picks 2..max parts, leaning toward the model's count
    (``len(llm_ends)``). Relaxation ladder when nothing fits: drop the critique
    constraints, drop skips (shortest first), allow a usable pause as a cut,
    allow any unit (weak cuts cost double), reduce an explicit part count (note
    PART_COUNT_REDUCED), and finally end the series early at a strong
    boundary when the material is too long for the parts (note
    STORY_ARC_SELECTED). ``forced_skips`` (sponsor reads, also listed in
    ``skips`` or not) are never dropped and never count toward the 40 %
    share. ``chapter_ends`` (last unit of each outline chapter) make cuts
    prefer chapter ends and parts avoid straddling chapters; ``jumps`` (left-out
    chapters among the forced skips) make parts break where the story jumps. Raises
    ``source_too_short`` only when not even two parts fit with every optional
    skip dropped.
    """
    constraints = constraints or PartitionConstraints()
    first, last = sorted((index.clamp_id(span[0]), index.clamp_id(span[1])))
    span = (first, last)
    valid_skips = validate_skips(index, span, [*skips, *forced_skips])
    forced_units = {uid for low, high in validate_skips(index, span, forced_skips) for uid in range(low, high + 1)}
    # A model skip merged into a sponsor read is kept with it.
    forced = frozenset(skip for skip in valid_skips if any(uid in forced_units for uid in range(skip[0], skip[1] + 1)))
    solver = _Solver(index, span, budget, llm_ends, constraints, cleanup, forced, chapter_ends, jumps, natural_endings)
    variants = _skip_variants(valid_skips, index, forced)

    # The least restrictive rung (only the forced skips) decides whether two
    # parts can fit at all; optional skips are dropped before giving up.
    loosest = solver.spine(variants[-1])
    total = loosest.seconds(0, len(loosest) - 1)
    max_auto = min(SERIES_MAX_PARTS, math.floor(total / budget.min)) if budget.min > 0 else SERIES_MAX_PARTS
    if max_auto < SERIES_MIN_PARTS:
        raise _too_short(total)

    if part_count is None:
        counts: tuple[int, ...] = tuple(range(SERIES_MIN_PARTS, max_auto + 1))
        too_long_counts = tuple(range(SERIES_MIN_PARTS, SERIES_MAX_PARTS + 1))
    else:
        counts = (min(max(int(part_count), SERIES_MIN_PARTS), SERIES_MAX_PARTS),)
        too_long_counts = counts

    constrained_options = (
        (True, False) if (constraints.pinned_ends or constraints.forbidden_ends) else (False,)
    )
    ladder: list[tuple[_Attempt, Optional[dict]]] = []
    for cuts in (CUTS_STRONG, CUTS_USABLE, CUTS_ALL):
        for variant_number, variant in enumerate(variants):
            for constrained in constrained_options:
                if variant_number and constrained:
                    continue
                ladder.append((_Attempt(variant, constrained, cuts, counts), None))
    if part_count is not None:
        requested = counts[0]
        for reduced in range(requested - 1, SERIES_MIN_PARTS - 1, -1):
            note = {"code": "PART_COUNT_REDUCED", "params": {"requested": requested, "actual": reduced}}
            for cuts in (CUTS_STRONG, CUTS_USABLE, CUTS_ALL):
                for variant in variants:
                    ladder.append((_Attempt(variant, False, cuts, (reduced,)), note))
    for cuts in (CUTS_STRONG, CUTS_USABLE, CUTS_ALL):
        for variant in variants:
            ladder.append((_Attempt(variant, False, cuts, too_long_counts, early_end=True), None))

    for rung, (attempt, note) in enumerate(ladder):
        solved = solver.solve(attempt)
        if solved is None:
            continue
        _, pairs, _ = solved
        spine = solver.spine(attempt.skips)
        used_skips = list(attempt.skips)
        parts = [(spine.ids[p], spine.ids[q]) for p, q in pairs]
        notes: list[dict[str, Any]] = [note] if note else []
        if attempt.early_end:
            notes.append(
                {
                    "code": "STORY_ARC_SELECTED",
                    "params": {
                        "start": format_clock(index.unit(parts[0][0]).start),
                        "end": format_clock(index.unit(parts[-1][1]).end),
                    },
                }
            )
        return PartitionResult(
            parts=parts,
            skips_by_part=[spine.skips_inside(p, q, used_skips) for p, q in pairs],
            notes=notes,
            relaxed=rung > 0,
            seconds=[round(spine.seconds(p, q), 2) for p, q in pairs],
            skips=used_skips,
        )
    raise _too_short(total)


def _too_short(total: float) -> WorkflowError:
    seconds = int(round(total))
    return WorkflowError(
        "source_too_short",
        f"This video has about {seconds} seconds of story, too little for a series. Try Highlights instead.",
        details={"story_seconds": seconds},
    )
