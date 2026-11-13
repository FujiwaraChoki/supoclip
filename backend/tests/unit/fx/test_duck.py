"""edit_fx.duck: speech spans and the music duck envelope (§9)."""

import pytest

from src.edit_fx.duck import duck_envelope, envelope_db_at, speech_spans

PARAMS = dict(gain_db=-20.0, duck_db=-12.0, attack=0.08, release=0.35, fade_in=0.5, fade_out=0.8, out_duration=10.0)


def test_speech_spans_merge_close_words():
    words = [(1.0, 1.3), (1.5, 1.9), (2.4, 2.6), (0.2, 0.4)]
    assert speech_spans(words) == [(0.2, 0.4), (1.0, 1.9), (2.4, 2.6)]
    assert speech_spans(words, merge_gap=1.0) == [(0.2, 2.6)]
    assert speech_spans([]) == []
    assert speech_spans([(2.0, 1.0)]) == []


def test_envelope_shape_for_one_phrase():
    points = duck_envelope([(2.0, 3.6)], **PARAMS)
    assert points == [
        (0.0, -60.0), (0.5, -20.0), (pytest.approx(1.92), -20.0), (2.0, -32.0), (3.6, -32.0),
        (pytest.approx(3.95), -20.0), (pytest.approx(9.2), -20.0), (10.0, -60.0),
    ]


def test_times_strictly_increase_and_levels_are_bounded():
    for spans in ([], [(0.0, 10.0)], [(0.0, 0.5), (0.6, 0.7), (4, 5)], [(9.9, 12.0)], [(5, 5)]):
        points = duck_envelope(spans, **PARAMS)
        times = [t for t, _ in points]
        assert times == sorted(set(times)) and times[0] == 0.0 and times[-1] == 10.0
        assert all(-60.0 <= v <= -20.0 for _, v in points)


def test_close_phrases_stay_ducked():
    points = duck_envelope([(1.0, 2.0), (2.3, 3.0)], **PARAMS)
    assert envelope_db_at(points, 2.15) == pytest.approx(-32.0)


def test_ramps_follow_attack_and_release():
    points = duck_envelope([(4.0, 5.0)], **PARAMS)
    assert envelope_db_at(points, 4.0 - 0.04) == pytest.approx(-26.0)       # halfway down the attack
    assert envelope_db_at(points, 5.0 + 0.175) == pytest.approx(-26.0)      # halfway up the release
    assert envelope_db_at(points, 4.5) == pytest.approx(-32.0)
    assert envelope_db_at(points, 7.0) == pytest.approx(-20.0)


def test_fades_are_scaled_to_fit():
    points = duck_envelope([], **{**PARAMS, "out_duration": 1.0, "fade_in": 1.0, "fade_out": 1.0})
    assert points == [(0.0, -60.0), (0.5, -20.0), (1.0, -60.0)]


def test_no_fades_starts_at_base_level():
    points = duck_envelope([], **{**PARAMS, "fade_in": 0.0, "fade_out": 0.0})
    assert points == [(0.0, -20.0), (10.0, -20.0)]


def test_deep_duck_is_clamped_to_the_floor():
    points = duck_envelope([(4.0, 5.0)], **{**PARAMS, "gain_db": -40.0, "duck_db": -30.0})
    assert min(v for _, v in points) == -60.0


def test_zero_duration():
    assert duck_envelope([(0, 1)], **{**PARAMS, "out_duration": 0.0}) == [(0.0, -60.0)]


def test_envelope_db_at_holds_outside_and_handles_empty():
    points = [(0.0, -60.0), (1.0, -20.0)]
    assert envelope_db_at(points, -1) == -60.0 and envelope_db_at(points, 5) == -20.0
    assert envelope_db_at(points, 0.25) == pytest.approx(-50.0)
    assert envelope_db_at([], 3.0) == 0.0
