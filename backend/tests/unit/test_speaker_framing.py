import shutil
import subprocess

import numpy as np
import pytest

from src.media import reframing, speaker_framing, timeline
from src.media.speaker_framing import (
    assign_speakers_to_clusters,
    build_speaker_activity,
    build_speaker_segments,
    build_speaker_turns,
    build_step_x_expression,
    cluster_face_positions,
    find_wide_shots,
)


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


def test_step_expression_is_flat_and_even():
    segments = [(0.0, 4.0, 40), (4.0, 8.0, 1200), (8.0, 12.0, 40)]

    expr = build_step_x_expression(segments)

    assert expr == "40+1160*gte(t\\,4.000)+-1160*gte(t\\,8.000)"
    assert "if(" not in expr


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


def test_vertical_plan_uses_speaker_chain(monkeypatch, tmp_path):
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _: 10.0)
    monkeypatch.setattr(
        speaker_framing, "build_speaker_crop_chain",
        lambda *args, **kwargs: "crop=606:1080:x='0':y=0,scale=1080:1920",
    )
    monkeypatch.setattr(
        reframing, "analyze_vertical_clip",
        lambda *_: pytest.fail("dominant-face analysis should be skipped"),
    )
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]

    chain, mode = reframing.build_vertical_filter_plan(
        tmp_path / "clip.mp4", 1920, 1080, turns, [_word(0, 1, "A")]
    )

    assert mode == "vf"
    assert chain.startswith("crop=606:1080:x='0'")


def test_vertical_plan_falls_back_when_speaker_mapping_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _: 10.0)
    monkeypatch.setattr(speaker_framing, "build_speaker_crop_chain", lambda *a, **k: None)
    calls = []

    def fake_analyze(path):
        calls.append(path)
        return [], []

    monkeypatch.setattr(reframing, "analyze_vertical_clip", fake_analyze)
    monkeypatch.setattr(reframing, "detect_optimal_crop_region", lambda *a: (650, 0, 606, 1080))
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]

    chain, mode = reframing.build_vertical_filter_plan(
        tmp_path / "clip.mp4", 1920, 1080, turns, [_word(0, 1, "A")]
    )

    assert calls, "fell back to dominant-face tracking"
    assert "crop=606:1080:650:0" in chain


def test_speaker_chain_skips_clips_with_content_shots(monkeypatch, tmp_path):
    samples = [(t / 3, []) for t in range(30)]  # no faces at all -> content shot
    monkeypatch.setattr(
        speaker_framing, "analyze_speaker_clip",
        lambda *a: {"face_samples": samples, "scene_cuts": [], "motion": np.zeros((1, 1, 1))},
    )
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]

    chain = speaker_framing.build_speaker_crop_chain(
        tmp_path / "clip.mp4", 1920, 1080, 606, 1080, 10.0, turns, []
    )

    assert chain is None
