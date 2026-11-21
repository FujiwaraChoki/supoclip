"""The time warp: speed ramps and freeze frames as one monotone map from edit time E to final time F (§10).

Everything is rendered in E; the warp is applied afterwards as a post-map (P2 frame resampling, P3 ASS and audio,
the fxbus). Mirrored by ``frontend/src/lib/fx/core/warp.ts`` and pinned by ``shared/fx/fixtures/warp``.

Pieces cover ``[0, edit_duration]`` in play order:

- a linear piece maps ``[e0, e1]`` to ``[o0, o0 + (e1 - e0) / rate]`` (``rate`` = edit seconds per final second;
  1 for normal playback);
- a hold piece (``rate == 0``, ``e0 == e1``) repeats the frame at ``e0`` for ``hold`` final seconds.

Quantisation (G4): freeze instants, holds and speed edges snap to the 30 fps grid with ``q(x) = floor(x*30 + 0.5)/30``
(round half up, identical to JavaScript's ``Math.round`` for the non-negative times used here; Python's banker's
``round`` is deliberately not used). A speed piece's final length is a whole number of frames (at least one) and its
rate is adjusted to fit, so ``out_duration`` is a whole number of frames whenever ``edit_duration`` is.

Outside ``[0, edit_duration]`` both maps extrapolate with rate 1, so ``Warp((), 0, 0)`` is an exact identity.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, Sequence

from .contracts import FPS, FRAME, FxCue

logger = logging.getLogger(__name__)

EPS = 1e-9


def quantize(x: float) -> float:
    """Snap seconds to the 30 fps grid, rounding half up (mirrors ``Math.round(x * 30) / 30``)."""
    return math.floor(x * FPS + 0.5) / FPS


@dataclass(frozen=True)
class WarpPiece:
    e0: float; e1: float; o0: float; rate: float; hold: float = 0.0   # rate 0 ⇒ hold piece (e0 == e1)

    @property
    def is_hold(self) -> bool:
        return self.rate == 0

    @property
    def length(self) -> float:
        """Final (F) seconds this piece occupies."""
        return self.hold if self.rate == 0 else (self.e1 - self.e0) / self.rate


@dataclass(frozen=True)
class Warp:
    pieces: tuple[WarpPiece, ...]
    edit_duration: float
    out_duration: float

    def to_output(self, e: float) -> float:
        """E → F. At a freeze instant returns the START of the hold."""
        if e <= 0:
            return e
        for piece in self.pieces:
            if piece.rate == 0:
                if abs(e - piece.e0) <= EPS:
                    return piece.o0
            elif piece.e0 <= e <= piece.e1:
                return piece.o0 + (e - piece.e0) / piece.rate
        return self.out_duration + (e - self.edit_duration)

    def to_edit(self, o: float) -> float:
        """F → E. Monotone non-decreasing; constant across holds."""
        if o <= 0:
            return o
        for piece in self.pieces:
            if piece.o0 <= o < piece.o0 + piece.length:
                if piece.rate == 0:
                    return piece.e0
                return piece.e0 + (o - piece.o0) * piece.rate
        return self.edit_duration + (o - self.out_duration)

    @property
    def nontrivial(self) -> bool:
        return any(piece.rate != 1 or piece.hold > 0 for piece in self.pieces)

    def to_json(self) -> list[dict]:
        """``[{e0,e1,o0,rate,hold}]`` (== ``WarpPieceJson``); ``[]`` for an identity warp."""
        if not self.nontrivial:
            return []
        return [
            {"e0": piece.e0, "e1": piece.e1, "o0": piece.o0, "rate": piece.rate, "hold": piece.hold}
            for piece in self.pieces
        ]


def identity_warp(edit_duration: float) -> Warp:
    duration = max(0.0, float(edit_duration))
    pieces = (WarpPiece(0.0, duration, 0.0, 1.0),) if duration > 0 else ()
    return Warp(pieces, duration, duration)


class _Builder:
    def __init__(self) -> None:
        self.pieces: list[WarpPiece] = []
        self.o = 0.0

    def linear(self, e0: float, e1: float, rate: float) -> None:
        if e1 - e0 <= EPS:
            return
        if rate == 1:
            length, actual = e1 - e0, 1.0
        else:
            length = max(FRAME, quantize((e1 - e0) / rate))
            actual = (e1 - e0) / length
        self.pieces.append(WarpPiece(e0, e1, self.o, actual))
        self.o += length

    def hold(self, e: float, hold: float) -> None:
        self.pieces.append(WarpPiece(e, e, self.o, 0.0, hold))
        self.o += hold


def build_warp(cues: Iterable[FxCue], edit_duration: float) -> Warp:
    """The warp for the speed and freeze cues among ``cues`` (E time); other kinds are ignored.

    - Speed cues are clipped to ``[0, edit_duration]`` and their edges quantised. Taken in ``(start, id)`` order, an
      overlapping later cue starts where the earlier one ends (earlier start wins); rate 1 or empty spans are no-ops.
    - Freezes: instant and hold quantised; a freeze strictly inside a speed span is dropped; a second freeze at the
      same instant is dropped.
    """
    duration = max(0.0, float(edit_duration))
    cue_list = list(cues)

    spans: list[tuple[float, float, float]] = []
    last_end = 0.0
    for cue in sorted((c for c in cue_list if c.kind == "speed"), key=lambda c: (c.start, c.id)):
        a = quantize(min(max(cue.start, 0.0), duration))
        b = min(quantize(min(max(cue.end, 0.0), duration)), duration)
        a = max(a, last_end)
        if cue.rate == 1 or b - a <= EPS:
            continue
        spans.append((a, b, float(cue.rate)))
        last_end = b

    freezes: list[tuple[float, float]] = []
    for cue in sorted((c for c in cue_list if c.kind == "freeze"), key=lambda c: (c.start, c.id)):
        e = min(quantize(min(max(cue.start, 0.0), duration)), duration)
        hold = quantize(cue.hold)
        if hold <= EPS:
            continue
        if any(a + EPS < e < b - EPS for a, b, _ in spans):
            continue
        if any(abs(e - other) <= EPS for other, _ in freezes):
            continue
        freezes.append((e, hold))

    # Freezes sort before a speed span starting at the same instant (the hold shows the span's first frame).
    events: list[tuple[float, int, tuple]] = [(e, 0, (e, hold)) for e, hold in freezes]
    events += [(a, 1, (a, b, rate)) for a, b, rate in spans]
    events.sort(key=lambda item: (item[0], item[1]))

    builder = _Builder()
    cursor = 0.0
    for _, kind, data in events:
        if kind == 0:
            e, hold = data
            builder.linear(cursor, e, 1.0)
            builder.hold(e, hold)
            cursor = max(cursor, e)
        else:
            a, b, rate = data
            builder.linear(cursor, a, 1.0)
            builder.linear(a, b, rate)
            cursor = b
    builder.linear(cursor, duration, 1.0)
    return Warp(tuple(builder.pieces), duration, builder.o)


def warp_from_json(pieces: Sequence[dict] | None, edit_duration: float | None = None) -> Warp:
    """Rebuild a stored warp (sidecar ``edit_fx_warp`` / ``FxScene.warp``). ``[]`` or ``None`` is identity.

    Lenient: a malformed list (bad numbers, gaps, non-monotone pieces) is logged and read as identity, never raised.
    """
    if not pieces:
        return identity_warp(edit_duration or 0.0)
    try:
        parsed = tuple(
            WarpPiece(float(p["e0"]), float(p["e1"]), float(p["o0"]), float(p["rate"]), float(p.get("hold", 0.0)))
            for p in pieces
        )
        e_cursor = o_cursor = 0.0
        for piece in parsed:
            values = (piece.e0, piece.e1, piece.o0, piece.rate, piece.hold)
            if not all(math.isfinite(v) for v in values) or piece.rate < 0 or piece.hold < 0:
                raise ValueError("non-finite or negative piece")
            if piece.rate == 0 and (abs(piece.e1 - piece.e0) > 1e-6 or piece.hold <= 0):
                raise ValueError("bad hold piece")
            if piece.rate > 0 and piece.e1 <= piece.e0:
                raise ValueError("empty linear piece")
            if abs(piece.e0 - e_cursor) > 1e-6 or abs(piece.o0 - o_cursor) > 1e-6:
                raise ValueError("pieces are not contiguous")
            e_cursor, o_cursor = piece.e1, piece.o0 + piece.length
    except (KeyError, TypeError, ValueError, AttributeError, ZeroDivisionError) as exc:
        logger.warning("fx_warp_unreadable (%s); using identity", exc)
        return identity_warp(edit_duration or 0.0)
    return Warp(parsed, e_cursor, o_cursor)


def slice_warp(warp: Warp, f0: float, f1: float) -> Warp:
    """The part of ``warp`` between final times ``f0`` and ``f1``, rebased to start at 0 in both clocks.

    Used for the ``edit_fx_warp`` of a trimmed or split FX clip (§13.2). A hold cut by ``f0`` or ``f1`` keeps the
    remaining hold exactly (the cut mp4 holds that many frozen frames), so its F length always equals ``f1 - f0``.
    """
    f0 = min(max(0.0, f0), warp.out_duration)
    f1 = min(max(f0, f1), warp.out_duration)
    e_base = warp.to_edit(f0)
    pieces = warp.pieces or identity_warp(warp.edit_duration).pieces
    out: list[WarpPiece] = []
    for piece in pieces:
        oa = max(piece.o0, f0)
        ob = min(piece.o0 + piece.length, f1)
        if ob - oa <= EPS:
            continue
        if piece.rate == 0:
            out.append(WarpPiece(piece.e0 - e_base, piece.e0 - e_base, oa - f0, 0.0, ob - oa))
        else:
            ea = piece.e0 + (oa - piece.o0) * piece.rate
            eb = piece.e0 + (ob - piece.o0) * piece.rate
            out.append(WarpPiece(ea - e_base, eb - e_base, oa - f0, piece.rate))
    edit_duration = warp.to_edit(f1) - e_base
    if f1 >= warp.out_duration - EPS:
        edit_duration = warp.edit_duration - e_base
    return Warp(tuple(out), max(0.0, edit_duration), f1 - f0)
