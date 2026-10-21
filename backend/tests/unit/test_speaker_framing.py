import json
import math
import shutil
import subprocess

import numpy as np
import pytest

from src.media import reframing, speaker_framing, timeline
from src.media.common import VERTICAL_SCALE_CHAIN
from src.media.speaker_framing import (
    apply_tracked_shots,
    assign_speakers_to_clusters,
    build_crop_x_expression,
    build_speaker_activity,
    build_speaker_segments,
    build_speaker_turns,
    build_step_x_expression,
    cluster_face_positions,
    find_wide_shots,
)


def eval_x(expr, t):
    """Evaluate a crop-x expression the way ffmpeg would at time t."""
    namespace = {
        "gte": lambda a, b: 1.0 if a >= b else 0.0,
        "trunc": lambda v: v if math.isnan(v) else math.trunc(v),
        "isnan": lambda v: 1.0 if math.isnan(v) else 0.0,
        "iff": lambda c, a, b: a if c else b,
        "t": t,
    }
    return eval(expr.replace("\\,", ",").replace("if(", "iff("), namespace)


def _word(start, end, speaker):
    return {"text": "w", "start": start, "end": end, "speaker": speaker}


def test_speaker_turns_cut_slightly_before_the_next_speaker():
    words = [
        _word(0.2, 1.0, "A"), _word(1.0, 2.5, "A"),
        _word(3.0, 4.0, "B"), _word(4.0, 5.5, "B"),
    ]

    turns = build_speaker_turns(words, duration=6.0)

    assert [t["speaker"] for t in turns] == ["A", "B"]
    assert turns[0]["start"] == 0.0
    assert turns[0]["end"] == pytest.approx(2.85)
    assert turns[1]["start"] == pytest.approx(2.85)
    assert turns[1]["end"] == 6.0


def test_speaker_turns_never_cut_before_previous_speaker_finishes():
    words = [_word(0.0, 3.0, "A"), _word(3.05, 5.0, "B")]

    turns = build_speaker_turns(words)

    assert turns[0]["end"] == pytest.approx(3.0)
    assert turns[1]["start"] == pytest.approx(3.0)


def test_short_interjections_are_absorbed():
    words = [
        _word(0.0, 3.0, "A"),
        _word(3.1, 3.4, "B"),  # "yeah"
        _word(3.5, 6.0, "A"),
        _word(6.2, 9.0, "B"),
    ]

    turns = build_speaker_turns(words, duration=9.0)

    assert [t["speaker"] for t in turns] == ["A", "B"]
    assert turns[1]["start"] == pytest.approx(6.05)


def test_unlabelled_words_extend_the_current_speaker():
    words = [_word(0.0, 2.0, "A"), _word(2.0, 3.0, None), _word(3.5, 6.0, "B")]

    turns = build_speaker_turns(words)

    assert turns[0]["end"] == pytest.approx(3.35)


def test_single_speaker_has_no_turns():
    words = [_word(0.0, 2.0, "A"), _word(2.1, 5.0, "A")]

    assert build_speaker_turns(words, duration=5.0) == []


def test_words_for_keep_ranges_carry_speaker_labels():
    transcript = {
        "words": [
            {"text": "hi", "start": 1000, "end": 1500, "speaker": "A"},
            {"text": "there", "start": 2000, "end": 2500, "speaker": "B"},
        ]
    }

    words = timeline.get_words_for_keep_ranges(transcript, [(1.0, 3.0)])

    assert [w["speaker"] for w in words] == ["A", "B"]
    assert words[0]["start"] == pytest.approx(0.0)


def test_mapping_picks_faces_that_move_while_their_speaker_talks():
    activity = {"A": [1.3, 0.8], "B": [0.85, 1.25]}

    assert assign_speakers_to_clusters(activity) == {"A": 0, "B": 1}


def test_mapping_is_rejected_when_ambiguous():
    activity = {"A": [1.04, 1.0], "B": [1.0, 1.03]}

    assert assign_speakers_to_clusters(activity) is None


def test_mapping_rejects_more_speakers_than_faces():
    activity = {"A": [1.4, 0.7], "B": [0.7, 1.4], "C": [1.0, 1.0]}

    assert assign_speakers_to_clusters(activity) is None


def test_large_panels_fall_back_without_exhaustive_search():
    # Codex repro: 8 speakers x 10+ faces used to enumerate millions of mappings.
    activity = {
        chr(65 + i): [1.5 if j == i else 0.9 for j in range(12)] for i in range(8)
    }

    assert assign_speakers_to_clusters(activity) is None


def test_mapping_still_works_at_the_panel_cap():
    activity = {
        chr(65 + i): [1.5 if j == i else 0.9 for j in range(6)] for i in range(4)
    }

    assert assign_speakers_to_clusters(activity) == {"A": 0, "B": 1, "C": 2, "D": 3}


def test_tied_mappings_are_ambiguous():
    assert assign_speakers_to_clusters({"A": [1.3, 1.3], "B": [1.3, 1.3]}) is None


def test_mapping_requires_each_face_to_be_more_active_than_average():
    # The swap is clearly worse, but face 1 never moves more while B talks.
    activity = {"A": [1.6, 0.6], "B": [0.6, 0.95]}

    assert assign_speakers_to_clusters(activity) is None


def _two_person_samples(duration=10.0, step=0.3):
    samples = []
    t = 0.0
    while t < duration:
        samples.append((t, [(300.0, 300.0, 80.0, 80.0), (1460.0, 320.0, 90.0, 90.0)]))
        t += step
    return samples


def test_wide_shot_and_face_clusters_are_found():
    samples = _two_person_samples()
    samples.append((5.0, [(900.0, 600.0, 40.0, 40.0)]))  # one-off false positive

    wide = find_wide_shots(samples, [(0.0, 10.0)], crop_w=606)
    clusters = cluster_face_positions(samples, wide, crop_w=606)

    assert wide == [(0.0, 10.0)]
    assert [round(c["cx"]) for c in clusters] == [340, 1505]


def test_single_face_shots_are_not_wide():
    samples = [(t / 3, [(800.0, 300.0, 120.0, 120.0)]) for t in range(30)]

    assert find_wide_shots(samples, [(0.0, 10.0)], crop_w=606) == []


def test_speaker_activity_follows_mouth_motion():
    fps = 10.0
    n = 100
    motion = np.full((n, 30, 60), 1.0, dtype=np.float32)
    # Left face mouth region active in the first half, right face in the second.
    motion[1:50, 5:10, 3:8] = 4.0
    motion[50:, 5:10, 45:50] = 4.0
    analysis = {"motion": motion, "motion_fps": fps, "motion_cell": 32.0}
    clusters = [
        {"cx": 175.0, "cy": 150.0, "w": 100.0, "h": 100.0},
        {"cx": 1500.0, "cy": 150.0, "w": 100.0, "h": 100.0},
    ]
    words = [_word(0.0, 4.9, "A"), _word(5.0, 9.9, "B")]

    activity = build_speaker_activity(analysis, clusters, words, [(0.0, 10.0)], [])

    assert assign_speakers_to_clusters(activity) == {"A": 0, "B": 1}


def test_segments_cut_on_turns_and_keep_single_face_shots():
    turns = [
        {"speaker": "A", "start": 0.0, "end": 4.0},
        {"speaker": "B", "start": 4.0, "end": 8.2},
        {"speaker": "A", "start": 8.2, "end": 12.0},
    ]
    shots = [(0.0, 8.0), (8.0, 12.0)]

    segments = build_speaker_segments(
        turns,
        mapping={"A": 0, "B": 1},
        cluster_x=[40, 1200],
        shots=shots,
        wide_shots=[(0.0, 8.0)],
        shot_x={(8.0, 12.0): 600},
        scene_cuts=[8.0],
        duration=12.0,
    )

    assert segments == [(0.0, 4.0, 40), (4.0, 8.0, 1200), (8.0, 12.0, 600)]


def test_turn_cuts_snap_to_nearby_scene_cuts():
    turns = [
        {"speaker": "A", "start": 0.0, "end": 4.8},
        {"speaker": "B", "start": 4.8, "end": 10.0},
    ]

    segments = build_speaker_segments(
        turns,
        mapping={"A": 0, "B": 1},
        cluster_x=[40, 1200],
        shots=[(0.0, 5.0), (5.0, 10.0)],
        wide_shots=[(0.0, 5.0), (5.0, 10.0)],
        shot_x={},
        scene_cuts=[5.0],
        duration=10.0,
    )

    assert segments == [(0.0, 5.0, 40), (5.0, 10.0, 1200)]


def test_step_expression_hard_cuts_between_segments():
    segments = [(0.0, 4.0, 40), (4.0, 8.0, 1200), (8.0, 12.0, 40)]

    expr = build_step_x_expression(segments)

    assert [eval_x(expr, t) for t in (0.0, 3.99, 4.0, 7.99, 8.0, 30.0)] == [
        40, 40, 1200, 1200, 40, 40,
    ]
    assert eval_x(expr, float("nan")) == 40


def test_crop_expression_interpolates_tracked_pieces_and_stays_even():
    pieces = [(0.0, 2.0, 100.0, 100.0), (2.0, 4.0, 100.0, 501.0), (4.0, 6.0, 900.0, 900.0)]

    expr = build_crop_x_expression(pieces)

    assert eval_x(expr, 1.0) == 100
    assert eval_x(expr, 3.0) == 300  # 100 + 401 * 0.5 = 300.5 -> even 300
    assert eval_x(expr, 5.0) == 900
    assert eval_x(expr, float("nan")) == 100


def test_tracked_shot_pieces_replace_static_crop_inside_the_shot():
    segments = [(0.0, 4.0, 40), (4.0, 10.0, 600)]
    tracked = {(4.0, 10.0): [(4.0, 10.0, 340.0, 1340.0)]}

    pieces = apply_tracked_shots(segments, tracked)

    assert pieces == [(0.0, 4.0, 40.0, 40.0), (4.0, 10.0, 340.0, 1340.0)]


needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@needs_ffmpeg
def test_step_expression_parses_in_ffmpeg_with_many_turns():
    # A flat chain of ~100 terms exceeds ffmpeg's parser depth; this must not.
    segments = [(i * 0.05, (i + 1) * 0.05, 0 if i % 2 == 0 else 600) for i in range(400)]
    chain = (
        f"crop=606:1080:x='{build_step_x_expression(segments)}':y=0,"
        "scale=1080:1920:flags=lanczos,setsar=1"
    )

    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi",
            "-i", "testsrc2=size=1920x1080:rate=20:duration=2",
            "-vf", chain, "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr


@needs_ffmpeg
def test_step_expression_hard_cuts_at_turn_boundary():
    # Left half black, right half white: the crop shows black, then white.
    expr = build_step_x_expression([(0.0, 1.0, 0), (1.0, 2.0, 1200)])
    result = subprocess.run(
        [
            "ffmpeg", "-v", "error",
            "-f", "lavfi", "-i", "color=black:size=960x1080:rate=10:duration=2",
            "-f", "lavfi", "-i", "color=white:size=960x1080:rate=10:duration=2",
            "-filter_complex",
            f"[0:v][1:v]hstack,crop=606:1080:x='{expr}':y=0,scale=4:4,format=gray[v]",
            "-map", "[v]", "-f", "rawvideo", "-",
        ],
        capture_output=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    frames = np.frombuffer(result.stdout, dtype=np.uint8).reshape(-1, 16).mean(axis=1)
    assert len(frames) == 20
    assert frames[:10].max() < 30
    assert frames[10:].min() > 225


def _face_track(duration=10.0, fps=3.0, cx=960.0):
    return [(i / fps, cx, 0.02) for i in range(int(duration * fps))]


def test_vertical_plan_uses_speaker_chain_for_all_face_clips(monkeypatch, tmp_path):
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _: 10.0)
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_: (_face_track(), []))
    monkeypatch.setattr(
        speaker_framing, "build_speaker_crop_chain",
        lambda *args, **kwargs: "crop=606:1080:x='0':y=0,scale=1080:1920",
    )
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]

    chain, mode, _caption = reframing.build_vertical_filter_plan(
        tmp_path / "clip.mp4", 1920, 1080, turns, [_word(0, 1, "A")]
    )

    assert mode == "vf"
    assert chain.startswith("crop=606:1080:x='0'")


def test_vertical_plan_falls_back_when_speaker_mapping_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _: 10.0)
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_: (_face_track(), []))
    monkeypatch.setattr(speaker_framing, "build_speaker_crop_chain", lambda *a, **k: None)
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]

    chain, mode, _caption = reframing.build_vertical_filter_plan(
        tmp_path / "clip.mp4", 1920, 1080, turns, [_word(0, 1, "A")]
    )

    assert mode == "vf"
    assert "crop=606:1080:656:0" in chain  # dominant face centred at x=960


def test_vertical_plan_keeps_compositor_for_content_shots(monkeypatch, tmp_path):
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _: 10.0)
    track = _face_track(5.0) + [(5.0 + i / 3.0, None, 0.0) for i in range(15)]
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_: (track, []))
    monkeypatch.setattr(
        speaker_framing, "build_speaker_crop_chain",
        lambda *a, **k: pytest.fail("content clips must not use speaker framing"),
    )
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]

    _, mode, _caption = reframing.build_vertical_filter_plan(
        tmp_path / "clip.mp4", 1920, 1080, turns, [_word(0, 1, "A")]
    )

    assert mode == "complex"


def test_short_real_shot_keeps_its_own_crop():
    # Codex repro: a 0.3s centred close-up between wide shots.
    segments = build_speaker_segments(
        [{"speaker": "A", "start": 0, "end": 4}, {"speaker": "B", "start": 4, "end": 12}],
        mapping={"A": 0, "B": 1},
        cluster_x=[40, 1200],
        shots=[(0.0, 8.0), (8.0, 8.3), (8.3, 12.0)],
        wide_shots=[(0.0, 8.0), (8.3, 12.0)],
        shot_x={(8.0, 8.3): 600},
        scene_cuts=[8.0, 8.3],
        duration=12.0,
    )

    assert (8.0, 8.3, 600) in segments


def test_turn_sliver_after_a_scene_cut_folds_into_the_same_shot():
    segments = build_speaker_segments(
        [{"speaker": "A", "start": 0, "end": 8.35}, {"speaker": "B", "start": 8.35, "end": 12}],
        mapping={"A": 0, "B": 1},
        cluster_x=[40, 1200],
        shots=[(0.0, 8.0), (8.0, 12.0)],
        wide_shots=[(0.0, 8.0), (8.0, 12.0)],
        shot_x={},
        scene_cuts=[8.0],
        duration=12.0,
    )

    assert segments == [(0.0, 8.0, 40), (8.0, 12.0, 1200)]


def test_wide_shot_from_another_angle_gets_its_own_face_crop(monkeypatch, tmp_path):
    # Codex repro: 16s wide shot, then a 4s wide shot with the guests shifted.
    samples = [
        (i / 3, [(300.0, 300.0, 80.0, 80.0), (1460.0, 300.0, 80.0, 80.0)])
        for i in range(48)
    ]
    samples += [
        (16 + i / 3, [(900.0, 300.0, 80.0, 80.0), (1700.0, 300.0, 110.0, 110.0)])
        for i in range(12)
    ]
    monkeypatch.setattr(
        speaker_framing, "analyze_speaker_clip",
        lambda *a: {"face_samples": samples, "scene_cuts": [16.0], "motion": np.zeros((1, 1, 1))},
    )
    seen_wide = []

    def fake_activity(analysis, clusters, words, wide_shots, cuts, baseline_shots=None):
        if baseline_shots is None:
            seen_wide.extend(wide_shots)
        return {"A": [1.3, 0.8], "B": [0.8, 1.3]}

    monkeypatch.setattr(speaker_framing, "build_speaker_activity", fake_activity)
    turns = [
        {"speaker": "A", "start": 0.0, "end": 8.0},
        {"speaker": "B", "start": 8.0, "end": 20.0},
    ]

    chain = speaker_framing.build_speaker_crop_chain(
        tmp_path / "clip.mp4", 1920, 1080, 606, 1080, 20.0, turns, []
    )

    assert seen_wide == [(0.0, 16.0)]
    # A on the left (x=36), B on the right (x=1196), then the shifted shot
    # centres its largest face (x=1755 -> clamped to 1314) instead of reusing
    # the stale right-hand cluster.
    expr = chain.split("x='", 1)[1].split("'", 1)[0]
    assert [eval_x(expr, t) for t in (1.0, 9.0, 17.0)] == [36, 1196, 1314]
    # Speaker crops are upscaled like every other vertical crop.
    assert chain.endswith(VERTICAL_SCALE_CHAIN)


def test_speaker_turns_follow_hook_teaser_play_order(tmp_path):
    # Teaser (B's punchline at 20-23s) plays first, then the body from 10s.
    video_path = tmp_path / "source.mp4"
    words = [
        {"text": "setup", "start": 10_000, "end": 14_000, "speaker": "A"},
        {"text": "question", "start": 14_000, "end": 17_500, "speaker": "A"},
        {"text": "punchline", "start": 20_000, "end": 23_000, "speaker": "B"},
    ]
    video_path.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "words": words, "utterances": [], "text": ""})
    )

    turns, clip_words = speaker_framing.build_clip_speaker_turns(
        video_path, [(20.0, 23.0), (10.0, 23.0)]
    )

    # Hard cut out of the teaser: no crossfade offset, ranges in play order.
    assert [(w["speaker"], w["start"]) for w in clip_words] == [
        ("B", 0.0), ("A", 3.0), ("A", 7.0), ("B", 13.0),
    ]
    assert [(t["speaker"], round(t["start"], 2)) for t in turns] == [
        ("B", 0.0), ("A", 3.0), ("B", 12.85),
    ]  # never cut before the teaser's last word ends


def test_reverse_angle_shot_is_not_speaker_framed():
    # Normal 0-16s, then a reverse angle 16-20s with the guests' sides swapped
    # but faces at the same coordinates (Codex repro).
    fps = 10.0
    n = 200
    motion = np.full((n, 30, 60), 1.0, dtype=np.float32)
    left, right = (slice(5, 10), slice(3, 8)), (slice(5, 10), slice(45, 50))
    for i in range(1, n):
        t = i / fps
        a_talking = (t % 4) < 2
        if t < 16:
            region = left if a_talking else right
        else:
            region = right if a_talking else left  # swapped
        motion[i][region] = 4.0
    analysis = {"motion": motion, "motion_fps": fps, "motion_cell": 32.0}
    clusters = [
        {"cx": 175.0, "cy": 150.0, "w": 100.0, "h": 100.0},
        {"cx": 1500.0, "cy": 150.0, "w": 100.0, "h": 100.0},
    ]
    words = [
        _word(start, start + 1.9, "A" if (start % 4) < 2 else "B")
        for start in [x * 2.0 for x in range(10)]
    ]
    wide = [(0.0, 16.0), (16.0, 20.0)]
    mapping = assign_speakers_to_clusters(
        build_speaker_activity(analysis, clusters, words, wide, [16.0])
    )

    assert mapping == {"A": 0, "B": 1}
    assert not speaker_framing.shot_contradicts_mapping(
        analysis, clusters, words, (0.0, 16.0), wide, [16.0], mapping
    )
    assert speaker_framing.shot_contradicts_mapping(
        analysis, clusters, words, (16.0, 20.0), wide, [16.0], mapping
    )


def test_moving_single_face_shot_keeps_tracking(monkeypatch, tmp_path):
    # Mapped wide shot 0-10s, then a single-person shot whose subject walks
    # from x~340 to x~1540 (Codex repro).
    samples = [
        (i / 3, [(300.0, 300.0, 80.0, 80.0), (1460.0, 300.0, 80.0, 80.0)])
        for i in range(30)
    ]
    samples += [
        (10 + i / 3, [(300.0 + i * 40.0, 300.0, 80.0, 80.0)]) for i in range(30)
    ]
    monkeypatch.setattr(
        speaker_framing, "analyze_speaker_clip",
        lambda *a: {"face_samples": samples, "scene_cuts": [10.0], "motion": np.zeros((1, 1, 1))},
    )
    monkeypatch.setattr(
        speaker_framing, "build_speaker_activity",
        lambda *a, **k: {"A": [1.3, 0.8], "B": [0.8, 1.3]},
    )
    monkeypatch.setattr(speaker_framing, "shot_contradicts_mapping", lambda *a, **k: False)
    turns = [
        {"speaker": "A", "start": 0.0, "end": 5.0},
        {"speaker": "B", "start": 5.0, "end": 20.0},
    ]

    chain = speaker_framing.build_speaker_crop_chain(
        tmp_path / "clip.mp4", 1920, 1080, 606, 1080, 20.0, turns, []
    )

    expr = chain.split("x='", 1)[1].split("'", 1)[0]
    early, late = eval_x(expr, 11.0), eval_x(expr, 19.5)
    assert late - early > 600  # the crop follows the subject across the frame
    face_x = lambda t: 340.0 + (t - 10.0) * 120.0
    for t in (12.0, 15.0, 18.0):
        x = eval_x(expr, t)
        assert x <= face_x(t) <= x + 606, (t, x)


@needs_ffmpeg
def test_tracked_pieces_render_in_ffmpeg():
    pieces = [(0.0, 0.5, 0.0, 0.0)] + [
        (0.5 + i * 0.1, 0.6 + i * 0.1, i * 10.0, (i + 1) * 10.0) for i in range(120)
    ]
    chain = f"crop=606:1080:x='{build_crop_x_expression(pieces)}':y=0"

    result = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-f", "lavfi",
            "-i", "testsrc2=size=1920x1080:rate=10:duration=2",
            "-vf", chain, "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
