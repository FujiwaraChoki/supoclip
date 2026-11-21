"""Baseline pushes (§8.5 rule 8): the FX path's replacement for the legacy Ken Burns punch-in.

The FX path always renders the vertical plan with ``allow_kenburns=False``, so this is the only slow zoom source
(AR-M9).
Callers add the pushes once the layout is known: generation iff ``layout_report["kenburns_eligible"]`` and
(``style.zooms`` or style-only); the studio iff the session layout filter has no ``zoompan``.

Every stretch of at least 6 s of E with no zoom cue gets a gentle push 1.00 -> 1.05 (id ``kb{n}``, anchor face), like
the legacy linear punch-in. Stretches never cover B-roll windows, reaction windows or the 0.4 s around a transition
(rule 4). A very long stretch is split at a cut so the motion stays visible and the snap back to 1.0 hides behind
the cut. Pure, deterministic, ids in time order, idempotent (existing auto ``kb{n}`` cues are recomputed).
"""

from __future__ import annotations

import re
from typing import Sequence

from .contracts import FxPlan, ZoomCue
from .rng import cue_seed

KB_ID = re.compile(r"kb\d+")
MIN_STRETCH = 6.0          # seconds of E without a zoom before a push is added
PUSH_SCALE = 1.05
SPLIT_MIN_PIECE = 10.0     # a long stretch is split at a cut only when the piece before it is at least this long
SPLIT_MIN_REST = 8.0       # ... and what remains after the cut is at least this long
TRANSITION_GUARD = 0.4     # rule 4: no zoom within 0.4 s of a transition
EPS = 1e-6


def _merge(intervals: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if merged and start <= merged[-1][1] + EPS:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _free(duration: float, blocked: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    free: list[tuple[float, float]] = []
    cursor = 0.0
    for start, end in _merge([(max(0.0, a), min(duration, b)) for a, b in blocked]):
        if start > cursor:
            free.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < duration:
        free.append((cursor, duration))
    return free


def _split(stretch: tuple[float, float], cuts: Sequence[float]) -> list[tuple[float, float]]:
    start, end = stretch
    pieces: list[tuple[float, float]] = []
    piece_start = start
    for cut in sorted(c for c in cuts if start + EPS < c < end - EPS):
        if cut - piece_start >= SPLIT_MIN_PIECE - EPS and end - cut >= SPLIT_MIN_REST - EPS:
            pieces.append((piece_start, cut))
            piece_start = cut
    pieces.append((piece_start, end))
    return pieces


def baseline_pushes(plan: FxPlan, *, cuts: Sequence[float], broll_windows: Sequence[tuple[float, float]]) -> FxPlan:
    """§8.5 rule 8: fill every ≥ 6 s stretch of E with no punch/push with a push 1.00 -> 1.05 (`kb{n}`, anchor face)."""
    duration = float(plan.edit_duration)
    kept = [c for c in plan.cues if not (c.origin == "auto" and KB_ID.fullmatch(c.id))]
    blocked: list[tuple[float, float]] = [(c.start, c.end) for c in kept if c.kind == "zoom"]
    blocked += [(float(a), float(b)) for a, b in broll_windows]
    blocked += [(c.start, c.end) for c in kept if c.kind == "reaction"]
    blocked += [(c.start - TRANSITION_GUARD, c.start + TRANSITION_GUARD) for c in kept if c.kind == "transition"]

    taken = {c.id for c in kept}
    pushes: list[ZoomCue] = []
    n = 0
    for stretch in _free(duration, blocked):
        if stretch[1] - stretch[0] < MIN_STRETCH - EPS:
            continue
        for start, end in _split(stretch, [float(c) for c in cuts]):
            if end - start < MIN_STRETCH - EPS:
                continue
            while f"kb{n}" in taken:
                n += 1
            cue_id = f"kb{n}"
            taken.add(cue_id)
            n += 1
            pushes.append(ZoomCue(id=cue_id, start=round(start, 3), end=round(end, 3),
                                  seed=cue_seed(plan.seed, cue_id, "push"), mode="push", scale=PUSH_SCALE,
                                  anchor="face"))

    cues = sorted([*kept, *pushes], key=lambda c: (c.start, c.id))
    if len(cues) > 400:
        return plan.model_copy(update={"cues": tuple(kept)})
    return plan.model_copy(update={"cues": tuple(cues)})
