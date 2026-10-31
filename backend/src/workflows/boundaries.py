"""How good a cut is after each unit, and how long a run of units plays.

``boundary_strength`` scores the gap after a unit (0..1). ``SpeechClock``
estimates the rendered length of unit runs after cleanup; every workflow
budget is measured with it. The snapping helpers find nearby cut points for
planners and validators.
"""

from __future__ import annotations

from bisect import bisect_right
from itertools import accumulate
from typing import TYPE_CHECKING, Any, Iterable, Optional, Sequence

from ..clip_cleanup import DEFAULT_PAUSE_THRESHOLD_MS, clip_cleanup_enabled
from ..media.common import PAUSE_RETAINED_SILENCE_SECONDS

# Private timeline helpers, imported so the estimate removes exactly the
# fillers that cleanup removes. Keep in step with build_clip_keep_ranges.
from ..media.timeline import _build_cleanup_phrases, _normalize_cleanup_token
from .language import SENTENCE_END_RE

if TYPE_CHECKING:
    from .units import Unit, UnitIndex

# A cut a viewer won't notice: after a sentence, a speaker change or the end.
STRONG_BOUNDARY = 0.75
# The weakest cut a planner may use without relaxing its rules (a long pause).
USABLE_BOUNDARY = 0.45

_BASE_STRENGTH = {"speaker": 0.6, "pause": 0.55, "clause": 0.35, "forced": 0.1, "eof": 1.0}
# A pause the sentence carries on after ("so even if you are ⏸ not awake"):
# a breath, not a break, so it stays below USABLE_BOUNDARY.
MID_SENTENCE_PAUSE_STRENGTH = 0.4


def boundary_strength(unit: "Unit", next_unit: Optional["Unit"]) -> float:
    """Strength of the gap after ``unit`` (0..1), per the end kind and the silence."""
    kind = unit.end_kind
    if next_unit is None or kind == "eof":
        return 1.0
    if kind == "sentence":
        strength = 0.75 + 0.25 * min(unit.gap_after, 2.0) / 2.0
        if next_unit.speaker is not None and next_unit.speaker != unit.speaker:
            strength = max(strength, 0.9)
    elif kind == "line":
        strength = 0.8 if unit.ends_question or SENTENCE_END_RE.search(unit.text.strip()) else 0.6
    elif kind == "pause" and not next_unit.starts_sentence:
        strength = MID_SENTENCE_PAUSE_STRENGTH
    else:
        strength = _BASE_STRENGTH[kind]
    if unit.ends_question:
        strength += 0.05
    return round(min(1.0, strength), 4)


def cleanup_key(cleanup: Optional[dict[str, Any]]) -> Optional[tuple]:
    """Hashable form of the cleanup settings that change lengths; None when off."""
    if not clip_cleanup_enabled(cleanup):
        return None
    assert cleanup is not None
    return (
        bool(cleanup.get("cut_long_pauses")),
        int(cleanup.get("pause_threshold_ms") or DEFAULT_PAUSE_THRESHOLD_MS),
        bool(cleanup.get("remove_filler_words")),
        tuple(cleanup.get("filtered_words") or ()),
    )


class SpeechClock:
    """Rendered seconds of unit runs under one set of cleanup settings.

    A run ``first..last`` plays from its first unit's cut-in to its last unit's
    cut-out, the same ranges ``UnitIndex.ranges_for`` hands the renderer.
    Cleanup then removes filler words and shortens each pause of at least the
    threshold to about ``PAUSE_RETAINED_SILENCE_SECONDS`` of audible silence.
    Prefix sums make each run O(1), so the partitioner can probe thousands.
    """

    def __init__(self, index: "UnitIndex", cleanup: Optional[dict[str, Any]] = None):
        self._starts = index.cut_in
        self._ends = index.cut_out
        count = len(index.units)
        inner = [0.0] * (count + 1)  # removed inside unit u (1-based)
        joint = [0.0] * (count + 1)  # removed in the gap after unit u
        key = cleanup_key(cleanup)
        if key is not None:
            cut_pauses, threshold_ms, remove_fillers, filtered = key
            threshold = max(0.25, threshold_ms / 1000.0)
            if index.coarse:
                if cut_pauses:
                    for unit, nxt in zip(index.units, index.units[1:]):
                        joint[unit.id] = _pause_excess(nxt.start - unit.end, threshold)
            else:
                self._add_word_removals(
                    index, inner, joint, cut_pauses, threshold, remove_fillers, list(filtered)
                )
        self._inner = list(accumulate(inner))
        self._joint = list(accumulate(joint))

    @staticmethod
    def _add_word_removals(index, inner, joint, cut_pauses, threshold, remove_fillers, filtered) -> None:
        words = index.words
        owner = [0] * len(words)
        for unit in index.units:
            for position in range(unit.w0, unit.w1 + 1):
                owner[position] = unit.id
        fillers: set[int] = set()
        phrases = _build_cleanup_phrases(remove_fillers, filtered, index.cleanup_language)
        if phrases:
            tokens = [_normalize_cleanup_token(word.text) for word in words]
            position = 0
            while position < len(words):
                matched = next(
                    (
                        len(phrase)
                        for phrase in phrases
                        if tokens[position : position + len(phrase)] == phrase
                    ),
                    0,
                )
                if matched:
                    fillers.update(range(position, position + matched))
                    position += matched
                else:
                    position += 1
        for position in fillers:
            inner[owner[position]] += words[position].end - words[position].start
        if not cut_pauses:
            return
        # Cleanup measures pauses between the words it keeps, so a removed
        # filler joins the silences on both sides into one pause.
        spoken = [position for position in range(len(words)) if position not in fillers]
        for current, following in zip(spoken, spoken[1:]):
            excess = _pause_excess(words[following].start - words[current].end, threshold)
            if not excess:
                continue
            unit_id = owner[current]
            if owner[following] == unit_id:
                inner[unit_id] += excess
            else:
                joint[unit_id] += excess

    def run_seconds(self, first: int, last: int) -> float:
        """Rendered length of the consecutive units ``first..last``."""
        if last < first:
            return 0.0
        removed = (self._inner[last] - self._inner[first - 1]) + (
            self._joint[last - 1] - self._joint[first - 1]
        )
        return max(0.0, self._ends[last] - self._starts[first] - removed)


def _pause_excess(gap: float, threshold: float) -> float:
    if gap < threshold:
        return 0.0
    return max(0.0, gap - PAUSE_RETAINED_SILENCE_SECONDS)


def kept_runs(first: int, last: int, skips: Iterable[tuple[int, int]] = ()) -> list[tuple[int, int]]:
    """Consecutive unit runs of ``first..last`` once the skipped units are removed."""
    runs: list[tuple[int, int]] = []
    cursor = first
    for skip_first, skip_last in sorted((min(a, b), max(a, b)) for a, b in skips):
        if skip_last < cursor or skip_first > last:
            continue
        if skip_first > cursor:
            runs.append((cursor, skip_first - 1))
        cursor = max(cursor, skip_last + 1)
    if cursor <= last:
        runs.append((cursor, last))
    return runs


def nearest_strong_end(
    index: "UnitIndex", uid: int, within: int, *, minimum: float = STRONG_BOUNDARY
) -> Optional[int]:
    """The unit closest to ``uid`` (at most ``within`` away) whose end is a strong cut.

    Ties go to the earlier unit: ending a beat early beats running into the next thought.
    """
    for distance in range(within + 1):
        for candidate in (uid - distance, uid + distance):
            if 1 <= candidate <= len(index.units) and index.boundary_strength(candidate) >= minimum:
                return candidate
    return None


def nearest_sentence_start(index: "UnitIndex", uid: int, within: int) -> Optional[int]:
    """The unit closest to ``uid`` (at most ``within`` away) that opens a sentence. Ties go earlier."""
    for distance in range(within + 1):
        for candidate in (uid - distance, uid + distance):
            if 1 <= candidate <= len(index.units) and index.unit(candidate).starts_sentence:
                return candidate
    return None


def strong_ends_around(
    index: "UnitIndex", uid: int, *, minimum: float = STRONG_BOUNDARY
) -> tuple[Optional[int], Optional[int]]:
    """The nearest strong end before ``uid`` and the nearest at or after it."""
    before = next((u for u in range(uid - 1, 0, -1) if index.boundary_strength(u) >= minimum), None)
    after = next((u for u in range(uid, len(index.units) + 1) if index.boundary_strength(u) >= minimum), None)
    return before, after


def ends_for_length(
    index: "UnitIndex",
    first: int,
    min_seconds: float,
    max_seconds: float,
    *,
    skips: Sequence[tuple[int, int]] = (),
    cleanup: Optional[dict[str, Any]] = None,
) -> Optional[tuple[int, int]]:
    """The first and last unit a piece starting at ``first`` may end on to last min..max seconds."""
    low: Optional[int] = None
    high: Optional[int] = None
    for end in range(first, len(index.units) + 1):
        seconds = index.speech_seconds(first, end, skips, cleanup)
        if seconds > max_seconds:
            break
        if seconds >= min_seconds:
            low = end if low is None else low
            high = end
    return (low, high) if low is not None and high is not None else None


def unit_index_at(starts: Sequence[float], seconds: float) -> int:
    """0-based position of the last start at or before ``seconds`` (0 when before all)."""
    return max(0, bisect_right(starts, seconds) - 1)
