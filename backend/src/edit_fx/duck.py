"""Music ducking as a precomputed breakpoint envelope (§9), shared with the browser's WebAudio preview.

The server multiplies the decoded bed by ``10^(envelope_db_at(points, t)/20)`` in numpy; the browser schedules the
same points as gain ramps. Mirrored by ``frontend/src/lib/fx/core/duck.ts`` and pinned by ``shared/fx/fixtures/duck``.
All times are F (final seconds).

Envelope rules:

1. Speech spans are clipped to ``[0, out_duration]``. Spans whose ramps would meet (the gap is at most
   ``attack + release``) are joined, so the bed stays down between close phrases instead of pumping.
2. Base curve: ``gain_db`` with no speech; for each span, a linear ramp from ``gain_db`` at ``start - attack`` to
   ``gain_db + duck_db`` at ``start``, held to ``end``, back to ``gain_db`` at ``end + release``.
3. Fades are an extra dB offset added to the base curve: ``(floor_db - gain_db) * (1 - t / fade_in)`` over the first
   ``fade_in`` seconds and ``(floor_db - gain_db) * (1 - (out - t) / fade_out)`` over the last ``fade_out``. When the
   two fades do not fit they are scaled down proportionally so they never overlap.
4. Breakpoints are 0, ``out_duration``, every base-curve corner and both fade ends inside ``(0, out_duration)``,
   evaluated as ``max(floor_db, base + fade)``. Times are strictly increasing; between points the level is linear
   in dB.
"""

from __future__ import annotations

import bisect
from typing import Sequence


def speech_spans(words_final: Sequence[tuple[float, float]], merge_gap: float = 0.35) -> list[tuple[float, float]]:
    """Word spans (F) joined into phrases: a gap of at most ``merge_gap`` seconds keeps one span."""
    spans: list[tuple[float, float]] = []
    for start, end in sorted((float(s), float(e)) for s, e in words_final):
        if end < start:
            continue
        if spans and start - spans[-1][1] <= merge_gap:
            if end > spans[-1][1]:
                spans[-1] = (spans[-1][0], end)
        else:
            spans.append((start, end))
    return spans


def _interp(points: Sequence[tuple[float, float]], t: float) -> float:
    if t <= points[0][0]:
        return points[0][1]
    if t >= points[-1][0]:
        return points[-1][1]
    index = bisect.bisect_right([p[0] for p in points], t)
    t0, v0 = points[index - 1]
    t1, v1 = points[index]
    if t1 <= t0:
        return v1
    return v0 + (v1 - v0) * (t - t0) / (t1 - t0)


def duck_envelope(spans: Sequence[tuple[float, float]], *, gain_db: float, duck_db: float, attack: float,
                  release: float, fade_in: float, fade_out: float, out_duration: float,
                  floor_db: float = -60.0) -> list[tuple[float, float]]:
    out = max(0.0, float(out_duration))
    if out <= 0:
        return [(0.0, floor_db)]
    fade_in = max(0.0, fade_in)
    fade_out = max(0.0, fade_out)
    if fade_in + fade_out > out:
        scale = out / (fade_in + fade_out)
        fade_in *= scale
        fade_out *= scale

    joined: list[tuple[float, float]] = []
    for start, end in sorted((max(0.0, s), min(out, e)) for s, e in spans):
        if end <= start:
            continue
        if joined and start - joined[-1][1] <= attack + release:
            if end > joined[-1][1]:
                joined[-1] = (joined[-1][0], end)
        else:
            joined.append((start, end))

    ducked = gain_db + duck_db
    base: list[tuple[float, float]] = [(-1e9, gain_db)]
    for start, end in joined:
        base += [(start - attack, gain_db), (start, ducked), (end, ducked), (end + release, gain_db)]
    base.append((1e9, gain_db))

    times = {0.0, out}
    times.update(t for t, _ in base if 0.0 < t < out)
    for t in (fade_in, out - fade_out):
        if 0.0 < t < out:
            times.add(t)

    def fade_offset(t: float) -> float:
        offset = 0.0
        if fade_in > 0 and t < fade_in:
            offset += (floor_db - gain_db) * (1 - t / fade_in)
        if fade_out > 0 and t > out - fade_out:
            offset += (floor_db - gain_db) * (1 - (out - t) / fade_out)
        return offset

    return [(t, max(floor_db, _interp(base, t) + fade_offset(t))) for t in sorted(times)]


def envelope_db_at(points: Sequence[tuple[float, float]], t: float) -> float:
    """The envelope level (dB) at ``t``: linear between points, held flat before the first and after the last."""
    if not points:
        return 0.0
    return _interp(points, t)
