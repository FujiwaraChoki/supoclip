"""Carrying an FX plan, its beats and face track through trim, split and merge of a rendered clip (§13.2).

These edits cut the rendered mp4, so the burned effects survive; the sidecar must describe the new mp4. Cut points
arrive in F (UI offsets, probed durations) and are converted to E with the clip's *applied* warp before these
helpers run. For a generated clip E == S.
"""

from __future__ import annotations

from typing import Sequence

from .contracts import INSTANT_KINDS, EditBeats, FxCue, FxPlan, Sensitivity, Timing
from .mapping import BEAT_ID_MAX, CUE_ID_MAX
from .rng import fnv1a32
from .warp import EPS, Warp, WarpPiece, quantize

MIN_SPAN = 0.04             # span pieces shorter than this are dropped (as in mapping)
MIN_HOLD = 0.2              # FreezeCue.hold lower bound
MAX_HOLD = 2.5              # FreezeCue.hold upper bound
MAX_CUES = 400
FACE_TRACK_DECIMALS = 3


def _clip_span(start: float, end: float, s0: float, s1: float) -> tuple[float, float] | None:
    a, b = max(start, s0), min(end, s1)
    if end - start <= EPS:                      # zero-length span: kept when it lies inside the window
        return (start - s0, start - s0) if s0 <= start < s1 else None
    if b - a < min(MIN_SPAN, end - start) - EPS:
        return None
    return a - s0, b - s0


def _hold_piece(warp: Warp, e: float):
    for piece in warp.pieces:
        if piece.rate == 0 and abs(piece.e0 - e) <= 1e-6:
            return piece
    return None


def _shift_freeze(cue: FxCue, s0: float, s1: float, warp: Warp, f0: float, f1: float) -> FxCue | None:
    e = cue.start
    piece = _hold_piece(warp, quantize(e)) or _hold_piece(warp, e)
    if piece is None:
        # The freeze was not rendered (safe/none level, FX_WARP=off): keep the intent like any instant.
        return cue.model_copy(update={"start": e - s0, "end": e - s0}) if s0 <= e < s1 else None
    if not (s0 - EPS <= e <= s1 + EPS):
        return None
    remaining = min(piece.o0 + piece.hold, f1) - max(piece.o0, f0)
    hold = quantize(max(0.0, remaining))
    if hold < MIN_HOLD - EPS:
        return None
    at = min(max(e - s0, 0.0), s1 - s0)
    return cue.model_copy(update={"start": at, "end": at, "hold": min(hold, MAX_HOLD)})


def shift_plan(plan: FxPlan, s0: float, s1: float, *, applied_warp: Warp, f0: float, f1: float) -> FxPlan:
    """Keep ``[s0, s1)`` of the plan, rebased to 0.

    Span cues are clipped (pieces under 40 ms dropped) and instants outside the window dropped. A freeze cut by the
    window keeps the F part of its hold that is still in ``[f0, f1)`` (quantised; dropped under 0.2 s), so the sidecar
    E/F lengths agree with the cut mp4 (AR-M15). ``music.offset += f0``; ``edit_duration = s1 - s0``.
    """
    s0 = min(max(0.0, s0), plan.edit_duration)
    s1 = min(max(s0, s1), plan.edit_duration)
    cues: list[FxCue] = []
    for cue in plan.cues:
        if cue.kind == "freeze":
            shifted = _shift_freeze(cue, s0, s1, applied_warp, f0, f1)
        elif cue.kind in INSTANT_KINDS:
            shifted = cue.model_copy(update={"start": cue.start - s0, "end": cue.start - s0}) \
                if s0 <= cue.start < s1 else None
        else:
            span = _clip_span(cue.start, cue.end, s0, s1)
            shifted = cue.model_copy(update={"start": span[0], "end": span[1]}) if span else None
        if shifted is not None:
            cues.append(shifted)
    cues.sort(key=lambda cue: (cue.start, cue.id))
    music = plan.music.model_copy(update={"offset": plan.music.offset + max(0.0, f0)}) if plan.music else None
    return FxPlan.model_validate({
        **plan.model_dump(),
        "edit_duration": s1 - s0,
        "cues": [cue.model_dump() for cue in cues],
        "music": music.model_dump() if music else None,
    })


def shift_beats(beats: EditBeats, s0: float, s1: float) -> EditBeats:
    """Keep the beats overlapping ``[s0, s1)``, clipped and rebased. A (source) times and word identities stay, so
    a later re-render can re-anchor them; ``timeline_hash`` is cleared because the clip timeline changed."""
    kept = []
    for beat in beats.beats:
        span = _clip_span(beat.start, beat.end, s0, s1)
        if span is not None:
            kept.append(beat.model_dump() | {"start": span[0], "end": span[1]})
    hook_end = beats.hook_end
    if hook_end is not None:
        hook_end = min(hook_end, s1) - s0 if hook_end > s0 + EPS else None
    reactions = [
        {**reaction.model_dump(), "at": reaction.at - s0} for reaction in beats.reactions if s0 <= reaction.at < s1
    ]
    return EditBeats.model_validate({
        **beats.model_dump(),
        "timeline_hash": "",
        "hook_end": hook_end,
        "beats": kept,
        "reactions": reactions,
    })


def shift_face_track(track: dict | None, s0: float, s1: float) -> dict | None:
    """Keep the face samples of ``[s0, s1]`` plus one sample period either side (for interpolation at the edges),
    rebased to 0 with 3-decimal times. Lenient: anything unreadable gives None (the track is recomputed)."""
    if not isinstance(track, dict):
        return None
    try:
        fps = float(track.get("fps", 6) or 6)
        margin = 1.0 / fps if fps > 0 else 0.0
        samples = []
        for sample in track.get("samples") or ():
            t = float(sample[0])
            if s0 - margin - EPS <= t <= s1 + margin + EPS:
                samples.append([round(t - s0, FACE_TRACK_DECIMALS), sample[1]])
    except (TypeError, ValueError, IndexError):
        return None
    return {**track, "samples": samples}


def _prefixed(value: str, prefix: str, limit: int) -> str:
    if len(prefix) + len(value) <= limit:
        return prefix + value
    tag = f"~{fnv1a32(value):08x}"
    return prefix + value[: max(0, limit - len(prefix) - len(tag))] + tag


_TIMING_ORDER: tuple[Timing, ...] = ("none", "approx", "word")


def concat_plans(parts: Sequence[tuple[FxPlan, float]]) -> FxPlan:
    """Plans of merged clips in play order; ``parts`` = ``(plan, E edit duration)``.

    Cue (and beat-reference) ids get ``"m{i}:"`` (shortened with a hash when over the limit). Style, template,
    seed, music, grade and the other globals come from the first part; ``sensitivity`` is serious if any part is;
    ``timing`` is the least precise of the parts. Cursors are E.
    """
    if not parts:
        raise ValueError("concat_plans needs at least one part")
    first = parts[0][0]
    cursor = 0.0
    cues: list[dict] = []
    sensitivity: Sensitivity = "normal"
    timing_rank = len(_TIMING_ORDER) - 1
    for index, (plan, duration) in enumerate(parts):
        prefix = f"m{index}:"
        for cue in plan.cues:
            data = cue.model_dump()
            data["id"] = _prefixed(cue.id, prefix, CUE_ID_MAX)
            data["beat"] = _prefixed(cue.beat, prefix, BEAT_ID_MAX) if cue.beat else ""
            data["start"] = cue.start + cursor
            data["end"] = cue.end + cursor
            cues.append(data)
        if plan.sensitivity == "serious":
            sensitivity = "serious"
        timing_rank = min(timing_rank, _TIMING_ORDER.index(plan.timing))
        cursor += max(0.0, float(duration))
    cues.sort(key=lambda cue: (cue["start"], cue["id"]))
    return FxPlan.model_validate({
        **first.model_dump(),
        "edit_duration": cursor,
        "sensitivity": sensitivity,
        "timing": _TIMING_ORDER[timing_rank],
        "cues": cues[:MAX_CUES],
    })


def _pieces_through(warp: Warp, duration: float) -> tuple[list[WarpPiece], float]:
    """``warp``'s pieces over ``[0, duration]`` of E (extended at rate 1 past its end) and their F length."""
    pieces = list(warp.pieces)
    if duration >= warp.edit_duration - EPS:
        if duration > warp.edit_duration + EPS:
            pieces.append(WarpPiece(warp.edit_duration, duration, warp.out_duration, 1.0))
        return pieces, warp.out_duration + max(0.0, duration - warp.edit_duration)
    clipped: list[WarpPiece] = []
    out = 0.0
    for piece in pieces:
        if piece.e0 >= duration - EPS:
            break
        if piece.rate != 0 and piece.e1 > duration:
            piece = WarpPiece(piece.e0, duration, piece.o0, piece.rate)
        clipped.append(piece)
        out = piece.o0 + piece.length
    return clipped, out


def concat_warps(parts: Sequence[tuple[Warp, float]]) -> Warp:
    """The applied warps of merged clips, in order. ``parts`` = ``(warp, E edit duration)``; cursors are E and F.

    Adjacent rate-1 pieces that touch in both clocks are joined, so concatenating identities gives one piece.
    """
    pieces: list[WarpPiece] = []
    e_cursor = o_cursor = 0.0
    for warp, duration in parts:
        duration = max(0.0, float(duration))
        part_pieces, part_out = _pieces_through(warp, duration)
        for piece in part_pieces:
            shifted = WarpPiece(piece.e0 + e_cursor, piece.e1 + e_cursor, piece.o0 + o_cursor, piece.rate, piece.hold)
            previous = pieces[-1] if pieces else None
            if (
                previous is not None
                and previous.rate == 1
                and shifted.rate == 1
                and abs(previous.e1 - shifted.e0) <= 1e-6
                and abs(previous.o0 + previous.length - shifted.o0) <= 1e-6
            ):
                pieces[-1] = WarpPiece(previous.e0, shifted.e1, previous.o0, 1.0)
            else:
                pieces.append(shifted)
        e_cursor += duration
        o_cursor += part_out
    return Warp(tuple(pieces), e_cursor, o_cursor)
