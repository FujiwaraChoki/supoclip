"""edit_fx.shift: plans, beats, face tracks and warps through trim, split and merge (§13.2)."""

import pytest
from pydantic import TypeAdapter

from src.edit_fx.contracts import Beat, EditBeats, EditStyle, FxCue, FxPlan
from src.edit_fx.shift import concat_plans, concat_warps, shift_beats, shift_face_track, shift_plan
from src.edit_fx.warp import Warp, WarpPiece, build_warp, identity_warp

CUE = TypeAdapter(FxCue)


def plan(cues, duration=10.0, **extra):
    return FxPlan(style=EditStyle(intensity="bold"), template="slam", seed=99, edit_duration=duration,
                  cues=cues, **extra)


def freeze(at, hold, cue_id="f"):
    return {"kind": "freeze", "id": cue_id, "start": at, "end": at, "hold": hold, "label": "WAIT"}


def ids(p):
    return [(c.id, round(c.start, 6), round(c.end, 6)) for c in p.cues]


def test_shift_plan_clips_spans_and_drops_instants_outside():
    p = plan([
        {"kind": "flash", "id": "a", "start": 1.0, "end": 1.2},
        {"kind": "accent", "id": "b", "start": 2.5, "end": 4.5, "variant": "circle"},
        {"kind": "sfx", "id": "c", "start": 2.0, "end": 2.0, "sound": "pop"},
        {"kind": "sfx", "id": "d", "start": 3.0, "end": 3.0, "sound": "pop"},
        {"kind": "shake", "id": "e", "start": 7.98, "end": 9.0, "amp": 10},
        {"kind": "transition", "id": "t", "start": 8.0, "end": 8.0, "style": "whip"},
    ], music={"mood": "hype", "bed": "hype", "offset": 1.0})
    out = shift_plan(p, 3.0, 8.0, applied_warp=identity_warp(10.0), f0=3.0, f1=8.0)
    assert ids(out) == [("b", 0.0, 1.5), ("d", 0.0, 0.0)]
    assert out.edit_duration == 5.0 and out.music.offset == 4.0
    assert out.seed == p.seed and out.template == p.template and out.style == p.style


def test_cut_through_a_hold_keeps_the_remaining_hold():
    p = plan([freeze(3.0, 1.0)], 6.0)
    warp = build_warp(p.cues, 6.0)                       # hold occupies F [3, 4)
    tail = shift_plan(p, 3.0, 6.0, applied_warp=warp, f0=3.4, f1=7.0)
    assert [(c.start, c.hold) for c in tail.cues] == [(0.0, pytest.approx(0.6))]
    head = shift_plan(p, 0.0, 3.0, applied_warp=warp, f0=0.0, f1=3.5)
    assert [(c.start, c.hold) for c in head.cues] == [(3.0, pytest.approx(0.5))]
    assert head.edit_duration == 3.0


def test_holds_under_200_ms_are_dropped():
    p = plan([freeze(3.0, 1.0)], 6.0)
    warp = build_warp(p.cues, 6.0)
    assert shift_plan(p, 3.0, 6.0, applied_warp=warp, f0=3.9, f1=7.0).cues == ()
    assert shift_plan(p, 0.0, 3.0, applied_warp=warp, f0=0.0, f1=3.1).cues == ()
    # cut exactly at the hold's end: nothing of it is left in the tail
    assert shift_plan(p, 3.0, 6.0, applied_warp=warp, f0=4.0, f1=7.0).cues == ()


def test_the_remaining_hold_is_quantised():
    p = plan([freeze(3.0, 1.0)], 6.0)
    out = shift_plan(p, 3.0, 6.0, applied_warp=build_warp(p.cues, 6.0), f0=3.41, f1=7.0)
    assert out.cues[0].hold == pytest.approx(18 / 30)


def test_unrendered_freeze_is_kept_as_intent():
    p = plan([freeze(3.0, 1.0)], 6.0)
    out = shift_plan(p, 2.0, 6.0, applied_warp=identity_warp(6.0), f0=2.0, f1=6.0)
    assert [(c.start, c.hold) for c in out.cues] == [(1.0, 1.0)]


def test_shift_beats():
    beats = EditBeats(words_hash="h", timeline_hash="t", hook_end=2.0, beats=[
        Beat(id="b0", kind="hook", start=0.0, end=2.0, src_start=100.0, src_end=102.0, words=("listen",)),
        Beat(id="b1", kind="number", start=3.0, end=3.5, value=40, suffix="%"),
        Beat(id="b2", kind="payoff", start=8.0, end=9.0),
    ], reactions=[{"at": 3.2, "query": "wow"}, {"at": 9.5, "query": "lol"}])
    out = shift_beats(beats, 1.0, 8.0)
    assert [(b.id, b.start, b.end) for b in out.beats] == [("b0", 0.0, 1.0), ("b1", 2.0, 2.5)]
    assert out.beats[0].src_start == 100.0 and out.beats[0].words == ("listen",)
    assert out.hook_end == 1.0 and out.timeline_hash == "" and out.words_hash == "h"
    assert [(r.at, r.query) for r in out.reactions] == [(pytest.approx(2.2), "wow")]
    assert shift_beats(beats, 2.5, 8.0).hook_end is None


def test_shift_face_track():
    track = {"version": 1, "space": "picture", "fps": 6,
             "samples": [[i / 6, [[0.4, 0.3, 0.2, 0.2, 0.9]]] for i in range(0, 37)]}
    out = shift_face_track(track, 2.0, 3.0)
    times = [s[0] for s in out["samples"]]
    assert times[0] == pytest.approx(-1 / 6, abs=1e-3) and times[-1] == pytest.approx(1 + 1 / 6, abs=1e-3)
    assert out["fps"] == 6 and out is not track
    assert shift_face_track(None, 0, 1) is None
    assert shift_face_track({"samples": [["x"]]}, 0, 1) is None


def test_concat_warps_matches_a_hand_built_case():
    first = build_warp([CUE.validate_python(freeze(1.0, 0.5))], 2.0)          # F 2.5
    second = build_warp([CUE.validate_python({"kind": "speed", "id": "s", "start": 1.0, "end": 2.0, "rate": 2.0})],
                        3.0)                                                   # F 2.5
    merged = concat_warps([(first, 2.0), (identity_warp(1.0), 1.0), (second, 3.0)])
    expected = (
        WarpPiece(0.0, 1.0, 0.0, 1.0),
        WarpPiece(1.0, 1.0, 1.0, 0.0, 0.5),
        WarpPiece(1.0, 4.0, 1.5, 1.0),            # first's tail + the identity part + second's head, joined
        WarpPiece(4.0, 5.0, 4.5, 2.0),
        WarpPiece(5.0, 6.0, 5.0, 1.0),
    )
    assert merged == Warp(expected, 6.0, 6.0)
    assert merged.to_output(4.5) == pytest.approx(4.75)


def test_concat_warps_extends_identity_json_parts():
    merged = concat_warps([(Warp((), 0.0, 0.0), 2.0), (identity_warp(3.0), 3.0)])
    assert merged == identity_warp(5.0)


def test_concat_plans_prefixes_ids_and_offsets_cues():
    a = plan([{"kind": "flash", "id": "b0.flash", "start": 1, "end": 1.1, "beat": "b0"}], 4.0,
             music={"mood": "hype", "bed": "hype"}, grade="noir")
    b = plan([{"kind": "sfx", "id": "b0.sfx", "start": 0.5, "end": 0.5, "sound": "pop", "beat": "b0"}], 3.0,
             sensitivity="serious", timing="approx")
    merged = concat_plans([(a, 4.0), (b, 3.0)])
    assert ids(merged) == [("m0:b0.flash", 1.0, 1.1), ("m1:b0.sfx", 4.5, 4.5)]
    assert [c.beat for c in merged.cues] == ["m0:b0", "m1:b0"]
    assert merged.edit_duration == 7.0 and merged.music == a.music and merged.grade == "noir"
    assert merged.sensitivity == "serious" and merged.timing == "approx" and merged.seed == a.seed


def test_concat_plans_keeps_long_ids_valid_and_distinct():
    long_a, long_b = "x" * 31 + "a", "x" * 31 + "b"
    p = plan([{"kind": "flash", "id": long_a, "start": 0, "end": 0.1},
              {"kind": "flash", "id": long_b, "start": 0.2, "end": 0.3}], 1.0)
    merged = concat_plans([(p, 1.0)])
    assert all(len(c.id) <= 32 for c in merged.cues)
    assert len({c.id for c in merged.cues}) == 2


def test_concat_plans_needs_parts():
    with pytest.raises(ValueError):
        concat_plans([])
