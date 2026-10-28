from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from src.media.scene_context import (
    ContextAnalyzer, SceneEvidence, build_context_layout_plan, context_reason, context_scene_cuts,
)
from src.media import reframing


def subject(time=0.0, **kwargs):
    return replace(SceneEvidence(time, face_area=0.05, face_count=1, face_span=0.2), **kwargs)


@pytest.mark.parametrize("sample,expected", [
    (SceneEvidence(0), "no_reliable_close_subject"),
    (subject(face_area=0.003), "no_reliable_close_subject"),
    (subject(face_span=0.5), "subject_exceeds_crop"),
    (subject(face_area=0.02, face_center=0.9), "peripheral_presenter"),
    (subject(face_area=0.035, face_center=0.9), "peripheral_presenter"),
    (subject(face_count=4, face_span=0.8), "subjects_spread_across_frame"),
    (subject(face_count=2, face_span=0.7, face_area=0.02), "interaction_exceeds_crop"),
    (subject(face_count=2, face_span=0.7, motion=0.12), "interaction_exceeds_crop"),
    # Calm faces larger than 2.5% of the frame still do not fit a 9:16 crop.
    (subject(face_count=2, face_span=0.7, face_area=0.03), "interaction_exceeds_crop"),
    (subject(face_count=2, face_span=0.2, face_area=0.03), None),
    (subject(face_area=0.03, text_rows=8), "screen_content_outside_crop"),
    (subject(face_area=0.08, face_center=0.9, text_rows=6), "screen_content_outside_crop"),
    (subject(motion=0.12, outside_motion=0.8, motion_spread=1), "distributed_action"),
    (subject(), None),
    (subject(camera_motion=0.2), None),
    (subject(motion=0.2, outside_motion=0.1, motion_spread=0.2), None),
    (subject(text_rows=8, face_area=0.15), None),
])
def test_context_decisions(sample, expected):
    assert context_reason(sample, 0.316) == expected


def test_real_short_shots_are_not_swallowed_by_debounce():
    # Short crops around a real content shot widen with it; the content shot
    # itself is never cropped to keep a neighbouring face.
    samples = [subject(0), subject(0.33), SceneEvidence(0.66), subject(1), subject(1.33)]
    plan = build_context_layout_plan(samples, [0.66, 1], 1.6, 0.316)
    assert [(p["start"], p["end"], p["kind"]) for p in plan] == [(0, 1.6, "fit")]


def test_single_missed_detection_does_not_widen_interview():
    samples = [subject(i / 3) if i != 5 else SceneEvidence(i / 3) for i in range(30)]
    assert build_context_layout_plan(samples, [], 10, 0.316)[0]["kind"] == "face"


def test_repeated_risk_widens_entire_continuous_shot_without_flicker():
    samples = [subject(i / 3) if i % 2 else SceneEvidence(i / 3) for i in range(30)]
    plan = build_context_layout_plan(samples, [], 10, 0.316)
    assert len(plan) == 1 and plan[0]["kind"] == "fit"


def test_unsampled_shot_is_preserved_and_boundaries_cover_duration():
    plan = build_context_layout_plan([subject(0), subject(1)], [-1, 0.5, 0.5, 0.75, 9], 2, 0.316)
    assert plan[0]["start"] == 0 and plan[-1]["end"] == 2
    assert all(a["end"] == b["start"] for a, b in zip(plan, plan[1:]))
    # The unsampled shot stays full frame; the brief crops beside it widen
    # with it instead of flipping layouts twice in two seconds.
    assert all(p["kind"] == "fit" for p in plan)


@pytest.mark.parametrize("duration", [0, -1, 10])
def test_empty_analysis_fits(duration):
    assert build_context_layout_plan([], [], duration, 0.316)[0]["kind"] == "fit"
    assert reframing.build_layout_plan([], [], duration)[0]["kind"] == "fit"


def textured_frame():
    rng = np.random.default_rng(10)
    frame = np.full((270, 480, 3), 100, np.uint8)
    for _ in range(140):
        x, y = rng.integers([10, 10], [465, 255])
        cv2.circle(frame, (int(x), int(y)), 3, tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    return frame


def test_static_frame_has_no_motion():
    analyzer = ContextAnalyzer()
    frame = textured_frame()
    analyzer.measure(frame, [], 0)
    sample = analyzer.measure(frame, [], 1 / 3)
    assert sample.motion == 0 and sample.camera_motion < 0.001 and not sample.cut


def test_camera_translation_is_compensated():
    analyzer = ContextAnalyzer()
    frame = textured_frame()
    analyzer.measure(frame, [(200, 60, 70, 70)], 0)
    moved = cv2.warpAffine(frame, np.float32([[1, 0, 12], [0, 1, 3]]), (480, 270))
    sample = analyzer.measure(moved, [(212, 63, 70, 70)], 1 / 3)
    assert 0.015 < sample.camera_motion < 0.04
    assert sample.motion < 0.01
    assert context_reason(sample, 0.316) is None


def test_distributed_objects_are_not_explained_away_as_a_camera_pan():
    analyzer = ContextAnalyzer()
    background = textured_frame()
    first, second = background.copy(), background.copy()
    for x in [30, 130, 300, 400]:
        cv2.rectangle(first, (x, 70), (x + 30, 160), (255, 255, 255), -1)
        cv2.rectangle(second, (x + 15, 110), (x + 45, 200), (255, 255, 255), -1)
    analyzer.measure(first, [(200, 40, 60, 60)], 0)
    sample = analyzer.measure(second, [(200, 40, 60, 60)], 1 / 3)
    assert sample.outside_motion > 0.45
    assert sample.motion_spread >= 0.5
    assert context_reason(sample, 0.316) == "distributed_action"


def test_hard_cut_does_not_count_as_distributed_motion():
    analyzer = ContextAnalyzer()
    analyzer.measure(np.full((270, 480, 3), (10, 30, 180), np.uint8), [], 0)
    sample = analyzer.measure(np.full((270, 480, 3), (180, 200, 20), np.uint8), [], 1 / 3)
    assert sample.cut and sample.motion == 0


def test_reverse_angle_cut_with_similar_colors_is_detected():
    rng = np.random.default_rng(7)
    frames = []
    for _ in range(2):
        frame = np.zeros((270, 480, 3), np.uint8)
        for _ in range(45):
            x, y = rng.integers([0, 0], [440, 230])
            color = (30, 100, 210) if rng.random() > 0.5 else (180, 160, 60)
            cv2.rectangle(frame, (int(x), int(y)), (int(x + 40), int(y + 40)), color, -1)
        frames.append(frame)
    analyzer = ContextAnalyzer()
    analyzer.measure(frames[0], [], 0)
    sample = analyzer.measure(frames[1], [], 1 / 3)
    assert sample.cut and sample.motion == 0


def test_sustained_fast_pan_does_not_become_many_scene_cuts():
    samples = [subject(i / 3, cut=True, difference=45 + i % 3) for i in range(12)]
    assert context_scene_cuts(samples) == []


def test_isolated_cut_survives_motion_debounce():
    samples = [subject(i / 3, cut=i == 5, difference=50 if i == 5 else 8) for i in range(12)]
    assert context_scene_cuts(samples) == [5 / 3]


def test_text_outside_webcam_is_detected():
    frame = np.full((270, 480, 3), 245, np.uint8)
    for y in range(25, 230, 24):
        cv2.putText(frame, "This slide contains essential context", (10, y),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.45, (20, 20, 20), 1)
    sample = ContextAnalyzer().measure(frame, [(405, 190, 50, 50)], 0)
    assert sample.text_rows >= 4
    assert context_reason(sample, 0.316) == "screen_content_outside_crop"


def test_tiny_audience_faces_do_not_override_dominant_presenter():
    sample = ContextAnalyzer().measure(textured_frame(), [(180, 40, 100, 100), (30, 90, 20, 20)], 0)
    assert sample.face_count == 1
    assert context_reason(sample, 0.316) is None


@pytest.mark.parametrize("analysis", [([], [], [], []), RuntimeError("decoder failed")])
def test_failed_analysis_uses_full_frame_without_second_face_scan(analysis):
    kwargs = {"side_effect": analysis} if isinstance(analysis, Exception) else {"return_value": analysis}
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", **kwargs), \
         patch.object(reframing, "detect_optimal_crop_region", side_effect=AssertionError("unsafe fallback")):
        graph, mode, caption_y = reframing.build_vertical_filter_plan(Path("missing.mp4"), 1920, 1080)
    assert mode == "complex" and "[crsrc]" not in graph
    assert "force_original_aspect_ratio=decrease" in graph and caption_y == 0.8


def test_context_overrides_faces_and_speaker_turns():
    evidence = [subject(i / 3, motion=0.2, outside_motion=0.8, motion_spread=1) for i in range(30)]
    track = [(s.time, 960, s.face_area) for s in evidence]
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [], [], evidence)), \
         patch("src.media.speaker_framing.build_speaker_filter_plan", side_effect=AssertionError("unsafe crop")):
        graph, mode, _ = reframing.build_vertical_filter_plan(
            Path("sports.mp4"), 1920, 1080, [{"speaker": "A"}], [{"text": "shoots"}])
    assert mode == "complex" and "[crsrc]" not in graph


@pytest.mark.parametrize("raw,returncode", [(b"partial", 0), (bytes(480 * 270 * 3), 1)],
                         ids=["incomplete-frame", "failed-decoder"])
def test_partial_decoder_output_is_discarded_and_pipe_closed(raw, returncode):
    class Decoder:
        stdout = BytesIO(raw)

        def wait(self):
            self.returncode = returncode

    decoder = Decoder()
    with patch.object(reframing, "ffprobe_video_size", return_value=(480, 270)), \
         patch.object(reframing.subprocess, "Popen", return_value=decoder), \
         patch.object(reframing, "_open_face_detectors", return_value=(None, None)):
        with pytest.raises(RuntimeError, match="decoder"):
            reframing.analyze_vertical_clip(Path("broken.mp4"))
    assert decoder.stdout.closed


def test_crop_safe_context_keeps_reaction_layout_and_detail_pass():
    evidence = [subject(i / 3) for i in range(30)]
    track = [(s.time, 960, s.face_area) for s in evidence]
    faces = [(s.time, [(800, 200, 300, 300)]) for s in evidence]
    reaction = {"video_filter": "reaction-filter", "caption_y": 1 / 3,
                "inset": (0, 0, 100, 100), "source_runs": [1]}
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [], faces, evidence)) as analyze, \
         patch("src.media.reaction_layout.find_reactor_face", return_value=(0, 0, 100, 100)), \
         patch("src.media.reaction_layout.choose_reaction_layout", return_value=reaction):
        result = reframing.build_vertical_filter_plan(Path("reaction.mp4"), 1920, 1080)
    assert result == ("reaction-filter", "complex", 1 / 3)
    assert analyze.call_count == 2
    assert analyze.call_args.kwargs == {"proc_width": 960, "analyze_context": False}


def _wide_pair(count=30, start=0.0):
    """Calm two-shot whose faces are wider than a 9:16 crop (face_area=0.03, span=0.7)."""
    return [
        subject(start + i / 3, face_count=2, face_span=0.7, face_area=0.03)
        for i in range(count)
    ]


def _high_pair_boxes(evidence):
    # High podcast faces: not a webcam stack, and too large to be a reaction inset.
    return [(s.time, [(200, 60, 300, 280), (1100, 70, 300, 280)]) for s in evidence]


def test_all_fit_context_with_corner_reactor_uses_reaction_layout():
    evidence = [SceneEvidence(i / 3) for i in range(30)]
    track = [(s.time, None, 0.0) for s in evidence]
    faces = [(s.time, []) for s in evidence]
    reaction = {"video_filter": "reaction-filter", "caption_y": 1 / 3,
                "inset": (0, 0, 100, 100), "source_runs": [1]}
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [], faces, evidence)) as analyze, \
         patch("src.media.reaction_layout.find_reactor_face", return_value=(1500, 20, 40, 50)), \
         patch("src.media.reaction_layout.choose_reaction_layout", return_value=reaction):
        graph, mode, caption_y = reframing.build_vertical_filter_plan(Path("reaction.mp4"), 1920, 1080)
    assert (graph, mode, caption_y) == ("reaction-filter", "complex", 1 / 3)
    assert graph != reframing.build_full_frame_filter()
    assert analyze.call_args.kwargs == {"proc_width": 960, "analyze_context": False}


def test_calm_wide_two_shot_is_full_frame_when_speaker_framing_declines():
    evidence = _wide_pair()
    assert context_reason(evidence[0], 0.316) == "interaction_exceeds_crop"
    track = [(s.time, 400.0, s.face_area) for s in evidence]
    faces = _high_pair_boxes(evidence)
    words = [{"text": "hello", "start": 0.2, "end": 0.6, "speaker": "A"}]
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [], faces, evidence)), \
         patch("src.media.speaker_framing.build_speaker_filter_plan", return_value=None):
        graph, mode, caption_y = reframing.build_vertical_filter_plan(
            Path("two-shot.mp4"), 1920, 1080, turns, words)
    assert (graph, mode, caption_y) == (reframing.build_full_frame_filter(), "complex", 0.80)
    assert "crop=606:" not in graph


def test_calm_wide_two_shot_keeps_a_speaker_plan():
    evidence = _wide_pair()
    track = [(s.time, 400.0, s.face_area) for s in evidence]
    faces = _high_pair_boxes(evidence)
    words = [{"text": "hello", "start": 0.2, "end": 0.6, "speaker": "A"}]
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]
    speaker = ("crop=606:1080:x='100':y=0,scale=1080:1920", "vf")
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [], faces, evidence)), \
         patch("src.media.speaker_framing.build_speaker_filter_plan", return_value=speaker):
        graph, mode, _ = reframing.build_vertical_filter_plan(
            Path("two-shot.mp4"), 1920, 1080, turns, words)
    assert (graph, mode) == speaker


def test_wide_two_shot_interval_stays_full_frame_beside_a_closeup():
    close = [subject(i / 3) for i in range(15)]
    wide = _wide_pair(count=15, start=5)
    evidence = close + wide
    track = [(s.time, 960.0, 0.05) for s in evidence]
    faces = [(s.time, [(700, 80, 400, 360)]) for s in close] + _high_pair_boxes(wide)
    words = [{"text": "hello", "start": 0.2, "end": 0.6, "speaker": "A"}]
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [5.0], faces, evidence)), \
         patch("src.media.speaker_framing.build_speaker_filter_plan", return_value=None):
        graph, mode, _ = reframing.build_vertical_filter_plan(
            Path("mixed.mp4"), 1920, 1080, [{"speaker": "A"}], words)
    assert mode == "complex"
    assert "[crsrc]" in graph
    assert "between(t\\,0.000\\,5.000)" in graph
    assert "between(t\\,5.000\\,10.000)" in graph
    assert "force_original_aspect_ratio=decrease" in graph


def test_analyze_context_false_skips_flow_text_and_histograms():
    width, height = 32, 18
    frame = bytes(width * height * 3)

    class Decoder:
        def __init__(self):
            self.stdout = BytesIO(frame)
            self.returncode = None

        def wait(self):
            self.returncode = 0

    decoder = Decoder()
    with patch.object(reframing, "ffprobe_video_size", return_value=(width, height)), \
         patch.object(reframing.subprocess, "Popen", return_value=decoder), \
         patch.object(reframing, "_open_face_detectors", return_value=(None, None)), \
         patch.object(reframing, "ContextAnalyzer", side_effect=AssertionError("context analysis")):
        track, cuts, faces, context = reframing.analyze_vertical_clip(
            Path("clip.mp4"), proc_width=width, analyze_context=False)
    assert context == [] and cuts == []
    assert len(track) == 1 and len(faces) == 1
    assert decoder.stdout.closed


def test_analyze_context_true_still_records_scene_evidence():
    width, height = 32, 18
    frame = bytes(width * height * 3)

    class Decoder:
        def __init__(self):
            self.stdout = BytesIO(frame)
            self.returncode = None

        def wait(self):
            self.returncode = 0

    decoder = Decoder()
    evidence = SceneEvidence(0.0)
    with patch.object(reframing, "ffprobe_video_size", return_value=(width, height)), \
         patch.object(reframing.subprocess, "Popen", return_value=decoder), \
         patch.object(reframing, "_open_face_detectors", return_value=(None, None)), \
         patch.object(reframing, "ContextAnalyzer") as analyzer:
        analyzer.return_value.measure.return_value = evidence
        track, cuts, faces, context = reframing.analyze_vertical_clip(
            Path("clip.mp4"), proc_width=width, analyze_context=True)
    assert context == [evidence]
    analyzer.return_value.measure.assert_called_once()
    assert len(track) == 1 and len(faces) == 1 and cuts == []
    assert decoder.stdout.closed
