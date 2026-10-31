"""Deterministic series partitioning: where each part ends.

The model drafts part ends (unit ids) and skips. This solver owns the hard
rules: every part is ``budget.min..budget.max`` seconds of speech, parts are
consecutive (the next part starts at the unit right after the previous end,
so adjacent parts share one exact cut), cuts land on strong boundaries, and
skips never remove more than 40 % of a part. A dynamic program picks the
cheapest cuts near the model's choices; a relaxation ladder keeps it from
failing on awkward material.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from .boundaries import STRONG_BOUNDARY, USABLE_BOUNDARY
from .durations import SERIES_MAX_PARTS, SERIES_MIN_PARTS, Budget
from .errors import WorkflowError
from .language import opens_with_back_reference
from .units import UnitIndex, format_clock

# Skips shorter than this aren't worth a jump cut.
SKIP_MIN_SECONDS = 3.0
# Skips may remove at most this share of a part's source time.
SKIP_MAX_SHARE = 0.4

_INFEASIBLE = math.inf


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
    """Span units minus skipped units, with O(1) lengths of any stretch of it."""

    def __init__(
        self, index: UnitIndex, span: tuple[int, int], skips: list[tuple[int, int]], cleanup: Optional[dict]
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
        # Source seconds skipped between run r and run r + 1.
        self._break_prefix = [0.0]
        for (_, high), (low, _) in zip(runs, runs[1:]):
            self._break_prefix.append(
                self._break_prefix[-1] + index.cut_out[low - 1] - index.cut_in[high + 1]
            )
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


@dataclass(frozen=True)
class _Attempt:
    skips: tuple[tuple[int, int], ...]
    constrained: bool
    all_cuts: bool
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
    ):
        self.index = index
        self.span = span
        self.budget = budget
        self.constraints = constraints
        self.cleanup = cleanup
        ends = sorted({min(max(int(uid), span[0]), span[1]) for uid in llm_ends})
        self.llm_ends = set(ends)
        self.llm_end_times = [index.unit(uid).end for uid in ends]
        self._spines: dict[tuple, _Spine] = {}

    def spine(self, skips: tuple[tuple[int, int], ...]) -> _Spine:
        spine = self._spines.get(skips)
        if spine is None:
            spine = self._spines[skips] = _Spine(self.index, self.span, list(skips), self.cleanup)
        return spine

    def _cut_cost(self, spine: _Spine, q: int, all_cuts: bool) -> float:
        index = self.index
        uid = spine.ids[q]
        strength = index.boundary_strength(uid)
        cost = 4.0 * (1.0 - strength)
        if all_cuts and strength < USABLE_BOUNDARY:
            cost *= 2.0
        if self.llm_end_times:
            end = index.unit(uid).end
            nearest = min(abs(end - time) for time in self.llm_end_times)
            cost += 1.5 * min(3.0, nearest / 5.0)
        if uid in self.llm_ends:
            cost -= 1.0
        if index.unit(uid).ends_question:
            cost -= 0.5
        if q + 1 < len(spine) and opens_with_back_reference(
            index.unit(spine.ids[q + 1]).text, index.language
        ):
            cost += 0.5
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
            return attempt.all_cuts or self.index.boundary_strength(uid) >= USABLE_BOUNDARY

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
                0.0 if q == size - 1 and not attempt.early_end else self._cut_cost(spine, q, attempt.all_cuts)
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
                if spine.skipped_share(p, q) > SKIP_MAX_SHARE:
                    continue
                soft = 0.15 * (max(0.0, budget.ideal_min - seconds) + max(0.0, seconds - budget.ideal_max))
                feasible.append((p, soft + cut_cost[q]))
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


def _skip_variants(skips: list[tuple[int, int]], index: UnitIndex) -> list[tuple[tuple[int, int], ...]]:
    """The skips, then the skips with the shortest ones dropped one at a time, down to none."""
    remaining = sorted(skips, key=lambda skip: index.speech_seconds(*skip))
    variants = []
    while True:
        variants.append(tuple(sorted(remaining)))
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
) -> PartitionResult:
    """Split ``span`` (unit ids) into consecutive series parts.

    ``part_count`` None picks 2..max parts, leaning toward the model's count
    (``len(llm_ends)``). Relaxation ladder when nothing fits: drop the critique
    constraints, drop skips (shortest first), allow any unit as a cut (weak
    cuts cost double), reduce an explicit part count (note
    PART_COUNT_REDUCED), and finally end the series early at a strong
    boundary when the material is too long for the parts (note
    STORY_ARC_SELECTED). Raises ``source_too_short`` when not even two parts fit.
    """
    constraints = constraints or PartitionConstraints()
    first, last = sorted((index.clamp_id(span[0]), index.clamp_id(span[1])))
    span = (first, last)
    valid_skips = validate_skips(index, span, skips)
    solver = _Solver(index, span, budget, llm_ends, constraints, cleanup)

    full = solver.spine(tuple(valid_skips))
    total = full.seconds(0, len(full) - 1)
    max_auto = min(SERIES_MAX_PARTS, math.floor(total / budget.min)) if budget.min > 0 else SERIES_MAX_PARTS
    if max_auto < SERIES_MIN_PARTS:
        raise _too_short(total)

    if part_count is None:
        counts: tuple[int, ...] = tuple(range(SERIES_MIN_PARTS, max_auto + 1))
        too_long_counts = tuple(range(SERIES_MIN_PARTS, SERIES_MAX_PARTS + 1))
    else:
        counts = (min(max(int(part_count), SERIES_MIN_PARTS), SERIES_MAX_PARTS),)
        too_long_counts = counts

    variants = _skip_variants(valid_skips, index)
    constrained_options = (
        (True, False) if (constraints.pinned_ends or constraints.forbidden_ends) else (False,)
    )
    ladder: list[tuple[_Attempt, Optional[dict]]] = []
    for all_cuts in (False, True):
        for variant_number, variant in enumerate(variants):
            for constrained in constrained_options:
                if variant_number and constrained:
                    continue
                ladder.append((_Attempt(variant, constrained, all_cuts, counts), None))
    if part_count is not None:
        requested = counts[0]
        for reduced in range(requested - 1, SERIES_MIN_PARTS - 1, -1):
            note = {"code": "PART_COUNT_REDUCED", "params": {"requested": requested, "actual": reduced}}
            for all_cuts in (False, True):
                for variant in variants:
                    ladder.append((_Attempt(variant, False, all_cuts, (reduced,)), note))
    for all_cuts in (False, True):
        for variant in variants:
            ladder.append((_Attempt(variant, False, all_cuts, too_long_counts, early_end=True), None))

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
