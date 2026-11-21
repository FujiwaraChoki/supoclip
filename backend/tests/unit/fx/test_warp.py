"""edit_fx.warp: the E → F time warp (§10, G4)."""

import random

import pytest
from pydantic import TypeAdapter

from src.edit_fx.contracts import FRAME, FxCue
from src.edit_fx.warp import Warp, WarpPiece, build_warp, identity_warp, quantize, slice_warp, warp_from_json

CUE = TypeAdapter(FxCue)


def speed(start, end, rate, cue_id="s"):
    return CUE.validate_python({"kind": "speed", "id": cue_id, "start": start, "end": end, "rate": rate})


def freeze(at, hold, cue_id="f"):
    return CUE.validate_python({"kind": "freeze", "id": cue_id, "start": at, "end": at, "hold": hold})


def frames(seconds):
    return seconds * 30


def assert_whole_frames(seconds):
    assert abs(frames(seconds) - round(frames(seconds))) < 1e-6, seconds


def test_quantize_rounds_half_up():
    assert quantize(0.05) == pytest.approx(2 / 30)        # 1.5 frames → 2 (banker's round would give 2 too)
    assert quantize(2.5 / 30) == pytest.approx(3 / 30)    # 2.5 frames → 3 (banker's would give 2)
    assert quantize(0.0) == 0.0


def test_no_warp_cues_is_identity():
    warp = build_warp([], 10.0)
    assert not warp.nontrivial and warp.to_json() == []
    assert warp.out_duration == 10.0 and warp.pieces == identity_warp(10.0).pieces
    for e in (-1.0, 0.0, 3.3, 10.0, 12.0):
        assert warp.to_output(e) == e and warp.to_edit(e) == e


def test_empty_identity_extrapolates():
    warp = Warp((), 0.0, 0.0)
    assert warp.to_output(4.2) == 4.2 and warp.to_edit(4.2) == 4.2


def test_freeze_adds_its_hold_and_maps_to_the_hold_start():
    warp = build_warp([freeze(2.0, 0.5)], 6.0)
    assert warp.out_duration == pytest.approx(6.5)
    assert warp.to_output(2.0) == pytest.approx(2.0)          # start of the hold
    assert warp.to_output(2.1) == pytest.approx(2.6)
    for o in (2.0, 2.2, 2.49):
        assert warp.to_edit(o) == pytest.approx(2.0)          # constant across the hold
    assert warp.to_edit(2.5) == pytest.approx(2.0)
    assert warp.to_edit(2.6) == pytest.approx(2.1)


def test_freeze_at_zero_and_at_the_end():
    start = build_warp([freeze(0.0, 0.4)], 3.0)
    assert start.pieces[0] == WarpPiece(0.0, 0.0, 0.0, 0.0, pytest.approx(0.4))
    assert start.to_output(0.0) == 0.0 and start.to_edit(0.3) == 0.0 and start.to_output(1.0) == pytest.approx(1.4)
    end = build_warp([freeze(3.0, 1.0)], 3.0)
    assert end.pieces[-1].rate == 0 and end.out_duration == pytest.approx(4.0)
    assert end.to_edit(3.5) == pytest.approx(3.0) and end.to_output(3.0) == pytest.approx(3.0)


def test_freeze_is_quantised():
    warp = build_warp([freeze(2.345, 0.71)], 6.0)
    hold = next(p for p in warp.pieces if p.rate == 0)
    assert hold.e0 == pytest.approx(70 / 30) and hold.hold == pytest.approx(21 / 30)
    assert_whole_frames(warp.out_duration)


def test_speed_piece_has_whole_frames_and_adjusted_rate():
    warp = build_warp([speed(1.0, 2.0, 1.3)], 4.0)
    piece = warp.pieces[1]
    assert_whole_frames(piece.length)
    assert piece.length == pytest.approx(23 / 30)              # 1/1.3 = 0.769 s → 23 frames
    assert piece.rate == pytest.approx(30 / 23)
    assert warp.out_duration == pytest.approx(3 + 23 / 30)


def test_slow_motion_lengthens():
    warp = build_warp([speed(3.0, 3.6, 0.5)], 6.0)
    assert warp.out_duration == pytest.approx(6.6)
    assert warp.to_output(3.6) == pytest.approx(4.2)


def test_overlapping_speed_cues_earlier_start_wins():
    warp = build_warp([speed(2.0, 4.0, 0.5, "b"), speed(1.0, 3.0, 2.0, "a")], 6.0)
    linear = [(p.e0, p.e1, round(p.rate, 6)) for p in warp.pieces]
    assert linear == [(0.0, 1.0, 1.0), (1.0, 3.0, 2.0), (3.0, 4.0, 0.5), (4.0, 6.0, 1.0)]


def test_freeze_inside_speed_is_dropped_but_edges_are_kept():
    inside = build_warp([speed(1.0, 3.0, 2.0), freeze(2.0, 0.5)], 5.0)
    assert not any(p.rate == 0 for p in inside.pieces)
    edges = build_warp([speed(1.0, 2.0, 1.5), freeze(1.0, 0.3, "f1"), freeze(2.0, 0.3, "f2")], 4.0)
    assert [p.e0 for p in edges.pieces if p.rate == 0] == [1.0, 2.0]


def test_second_freeze_at_the_same_instant_is_dropped():
    warp = build_warp([freeze(2.0, 0.5, "a"), freeze(2.01, 1.0, "b")], 4.0)
    assert [p.hold for p in warp.pieces if p.rate == 0] == [pytest.approx(0.5)]


def test_speed_is_clipped_to_the_clip():
    warp = build_warp([speed(4.5, 9.0, 2.0)], 5.0)
    assert warp.pieces[-1].e1 == 5.0 and warp.edit_duration == 5.0
    assert warp.out_duration == pytest.approx(4.5 + 8 / 30)          # 0.25 s = 7.5 frames, rounded up to 8


def test_rate_one_and_other_kinds_are_ignored():
    sfx = CUE.validate_python({"kind": "sfx", "id": "x", "start": 1, "end": 1, "sound": "pop"})
    assert not build_warp([speed(1.0, 2.0, 1.0), sfx], 3.0).nontrivial


def random_warp(rng: random.Random) -> Warp:
    duration = rng.randint(30, 600) / 30
    cues = []
    for k in range(rng.randint(0, 4)):
        a = rng.uniform(0, duration)
        cues.append(speed(a, min(duration + 1, a + rng.uniform(0.1, 3)), rng.choice([0.5, 0.8, 1.2, 1.3, 2, 4]),
                          f"s{k}"))
    for k in range(rng.randint(0, 3)):
        cues.append(freeze(rng.uniform(0, duration), rng.uniform(0.2, 2.5), f"f{k}"))
    return build_warp(cues, duration)


def test_properties_over_random_plans():
    rng = random.Random(20261114)
    for _ in range(300):
        warp = random_warp(rng)
        assert_whole_frames(warp.out_duration)                              # G4
        assert warp.out_duration == pytest.approx(sum(p.length for p in warp.pieces))
        # contiguous pieces
        e = o = 0.0
        for piece in warp.pieces:
            assert piece.e0 == pytest.approx(e) and piece.o0 == pytest.approx(o)
            e, o = piece.e1, piece.o0 + piece.length
        assert e == pytest.approx(warp.edit_duration)
        holds = {p.e0 for p in warp.pieces if p.rate == 0}
        previous = -1.0
        for k in range(200):
            t = warp.edit_duration * k / 199
            if all(abs(t - h) > 1e-6 for h in holds):
                assert warp.to_edit(warp.to_output(t)) == pytest.approx(t, abs=1e-9)
            out = warp.to_output(t)
            assert out >= previous - 1e-9
            previous = out
        previous = -1.0
        for k in range(200):
            value = warp.to_edit(warp.out_duration * k / 199)
            assert value >= previous - 1e-9
            previous = value


def test_json_round_trip():
    warp = build_warp([speed(1.0, 2.0, 2.0), freeze(3.0, 0.5)], 5.0)
    restored = warp_from_json(warp.to_json())
    assert restored == warp
    assert warp_from_json([], 4.0) == identity_warp(4.0)
    assert warp_from_json(None).to_output(3.0) == 3.0


@pytest.mark.parametrize("bad", [
    [{"e0": 0, "e1": 1}],                                                      # missing keys
    [{"e0": 0, "e1": 1, "o0": 0, "rate": 1}, {"e0": 2, "e1": 3, "o0": 1, "rate": 1}],   # gap
    [{"e0": 0, "e1": 1, "o0": 0, "rate": -1}],
    [{"e0": 1, "e1": 1, "o0": 0, "rate": 0, "hold": 0}],
    [{"e0": "x", "e1": 1, "o0": 0, "rate": 1}],
    ["nope"],
])
def test_malformed_json_is_identity(bad):
    warp = warp_from_json(bad, 6.0)
    assert not warp.nontrivial and warp.to_output(2.0) == 2.0


def test_slice_warp_keeps_a_cut_hold_and_rebases():
    warp = build_warp([freeze(2.0, 1.0)], 6.0)                # hold occupies F [2, 3)
    head = slice_warp(warp, 0.0, 2.5)
    assert head.out_duration == pytest.approx(2.5) and head.edit_duration == pytest.approx(2.0)
    assert head.pieces[-1].rate == 0 and head.pieces[-1].hold == pytest.approx(0.5)
    tail = slice_warp(warp, 2.5, 7.0)
    assert tail.pieces[0] == WarpPiece(0.0, 0.0, 0.0, 0.0, pytest.approx(0.5))
    assert tail.edit_duration == pytest.approx(4.0) and tail.out_duration == pytest.approx(4.5)
    assert tail.to_edit(1.5) == pytest.approx(1.0)


def test_slice_warp_through_speed_piece():
    warp = build_warp([speed(2.0, 4.0, 2.0)], 6.0)            # F: [0,2) normal, [2,3) 2x, [3,5) normal
    middle = slice_warp(warp, 2.5, 4.0)
    assert middle.edit_duration == pytest.approx(2.0)          # E 3 → 5
    assert middle.pieces[0].rate == pytest.approx(2.0) and middle.pieces[0].e1 == pytest.approx(1.0)
    assert middle.to_output(1.0) == pytest.approx(0.5)


def test_slice_of_identity():
    sliced = slice_warp(identity_warp(6.0), 1.0, 4.0)
    assert sliced.edit_duration == pytest.approx(3.0) and not sliced.nontrivial


def test_frame_constant():
    assert FRAME == pytest.approx(1 / 30)
