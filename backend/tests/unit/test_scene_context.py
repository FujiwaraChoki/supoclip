from dataclasses import replace
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from src.media.scene_context import (
    ContextAnalyzer, SceneEvidence, build_context_layout_plan, context_reason, context_scene_cuts,
    stabilize_layout_plan,
)
from src.media import reframing
from src.media.screen_text import ScreenWord


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
    # Players in a wide shot: action across the frame needs the full frame.
    (subject(face_area=0.02, motion=0.12, outside_motion=0.8, motion_spread=1), "distributed_action"),
    # One head-and-shoulders presenter: the motion is their own gestures (and
    # a chat overlay beside them), so the face crop stays.
    (subject(face_area=0.048, motion=0.1, outside_motion=0.6, motion_spread=0.67), None),
    (subject(face_area=0.03, motion=0.12, outside_motion=0.8, motion_spread=1), None),
    # A big face at the frame edge (a facecam over gameplay) keeps the frame.
    (subject(face_area=0.05, face_center=0.9, motion=0.12, outside_motion=0.8, motion_spread=1),
     "distributed_action"),
    # A second face is not a lone presenter.
    (subject(face_count=2, face_span=0.25, motion=0.12, outside_motion=0.8, motion_spread=1),
     "distributed_action"),
    (subject(), None),
    (subject(camera_motion=0.2), None),
    (subject(motion=0.2, outside_motion=0.1, motion_spread=0.2), None),
    (subject(text_rows=8, face_area=0.15), None),
])
def test_context_decisions(sample, expected):
    assert context_reason(sample, 0.316) == expected


def test_real_short_shots_are_not_swallowed_by_debounce():
    # A short shot without a subject between two close-ups keeps its own layout: it fills the frame from
    # its centre, and the close-ups beside it keep their face crops. Nothing is shown small.
    samples = [subject(0), subject(0.33), SceneEvidence(0.66), subject(1), subject(1.33)]
    plan = build_context_layout_plan(samples, [0.66, 1], 1.6, 0.316)
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [
        (0, 0.66, "face", "confident_subject"),
        (0.66, 1, "cover", "no_reliable_close_subject"),
        (1, 1.6, "face", "confident_subject"),
    ]
    assert plan[1]["x"] == 0.5


def test_single_missed_detection_does_not_widen_interview():
    samples = [subject(i / 3) if i != 5 else SceneEvidence(i / 3) for i in range(30)]
    assert build_context_layout_plan(samples, [], 10, 0.316)[0]["kind"] == "face"


def test_repeated_risk_fills_the_entire_continuous_shot_with_one_still_crop_without_flicker():
    # The face is lost in every other sample: one full-height crop on the face for the whole shot, instead
    # of a tracked crop that comes and goes (and instead of the whole picture shown small).
    samples = [subject(i / 3, face_center=0.6) if i % 2 else SceneEvidence(i / 3) for i in range(30)]
    plan = build_context_layout_plan(samples, [], 10, 0.316)
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [
        (0, 10, "cover", "no_reliable_close_subject")]
    assert plan[0]["x"] == pytest.approx(0.6)


def test_unsampled_shot_fills_from_the_centre_and_boundaries_cover_duration():
    plan = build_context_layout_plan([subject(0), subject(1)], [-1, 0.5, 0.5, 0.75, 9], 2, 0.316)
    assert plan[0]["start"] == 0 and plan[-1]["end"] == 2
    assert all(a["end"] == b["start"] for a, b in zip(plan, plan[1:]))
    # Nothing is known about the unsampled shot: it fills the frame from the centre. The close-ups beside
    # it keep their face crops.
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [
        (0, 0.5, "face", "confident_subject"),
        (0.5, 0.75, "cover", "analysis_unavailable"),
        (0.75, 2, "face", "confident_subject"),
    ]
    assert plan[1]["x"] == 0.5


@pytest.mark.parametrize("duration", [0, -1, 10])
def test_empty_analysis_fills_the_frame_from_the_centre(duration):
    # Nothing is known about the picture: the centre of the frame, full height. Never the letterboxed strip.
    for plan in (build_context_layout_plan([], [], duration, 0.316), reframing.build_layout_plan([], [], duration)):
        assert [(p["start"], p["end"], p["kind"], p["reason"], p["x"]) for p in plan] == [
            (0, max(0, duration), "cover", "analysis_unavailable", 0.5)]


def test_empty_samples_are_one_centred_cover():
    assert build_context_layout_plan([], [5], 10, 0.316) == [
        {"start": 0.0, "end": 10, "kind": "cover", "reason": "analysis_unavailable", "x": 0.5, "fill_x": 0.5}]


def test_unmeasured_clip_crops_faces_and_fills_the_rest_from_the_centre():
    # No scene context, only the face track: ten seconds of a face, then ten without one. The layouts
    # switch at the scene cut beside the change.
    track = [(i / 3, 960.0, 0.05) for i in range(30)] + [(i / 3, None, 0.0) for i in range(30, 60)]
    plan = reframing.build_layout_plan(track, [10.5], 20)
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [
        (0, 10.5, "face", "confident_subject"), (10.5, 20, "cover", "no_face")]
    assert plan[1]["x"] == 0.5


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
def test_failed_analysis_fills_the_frame_from_the_centre_without_second_face_scan(analysis):
    kwargs = {"side_effect": analysis} if isinstance(analysis, Exception) else {"return_value": analysis}
    report = {}
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", **kwargs), \
         patch("src.media.screen_text.ocr_available", return_value=False), \
         patch.object(reframing, "detect_optimal_crop_region", side_effect=AssertionError("unsafe fallback")):
        graph, mode, caption_y = reframing.build_vertical_filter_plan(
            Path("missing.mp4"), 1920, 1080, report=report)
    assert report["kind"] == "crop" and mode == "complex" and "[crsrc]" not in graph
    # A full-height crop on the middle of the frame (960 - 303 = 657 → even 656), not the whole picture small.
    assert "crop=606:ih:x='656':y=0,scale=1080:-2" in graph
    assert "force_original_aspect_ratio=decrease" not in graph and caption_y is None


def test_context_overrides_faces_and_speaker_turns():
    evidence = [
        subject(i / 3, face_area=0.02, motion=0.2, outside_motion=0.8, motion_spread=1)
        for i in range(30)
    ]
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


# A calm two-shot whose people are too far apart for one 9:16 crop used to
# letterbox the whole clip (or, with diarized speakers, cut between them).
# It is now an interview layout: both people stacked, each in a face crop
# (see test_interview_layout.py for the composition itself).

def test_calm_wide_two_shot_stacks_both_people_instead_of_letterboxing():
    evidence = _wide_pair()
    assert context_reason(evidence[0], 0.316) == "interaction_exceeds_crop"
    track = [(s.time, 400.0, s.face_area) for s in evidence]
    faces = _high_pair_boxes(evidence)
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [], faces, evidence)):
        described = reframing.describe_vertical_layout(Path("two-shot.mp4"), 1920, 1080)
    assert described["kind"] == "interview" and described["mode"] == "complex"
    assert "vstack=inputs=2" in described["video_filter"]
    assert "force_original_aspect_ratio=decrease" not in described["video_filter"]


def test_wide_two_shot_stays_stacked_with_diarized_speakers():
    # Both people stay on screen while the camera shows both; speaker cuts
    # remain for multi-person shots the interview layout does not take.
    evidence = _wide_pair()
    track = [(s.time, 400.0, s.face_area) for s in evidence]
    faces = _high_pair_boxes(evidence)
    words = [{"text": "hello", "start": 0.2, "end": 0.6, "speaker": "A"}]
    turns = [{"speaker": "A", "start": 0, "end": 5}, {"speaker": "B", "start": 5, "end": 10}]
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [], faces, evidence)), \
         patch("src.media.speaker_framing.build_speaker_filter_plan", side_effect=AssertionError("speaker cut")):
        graph, mode, _ = reframing.build_vertical_filter_plan(
            Path("two-shot.mp4"), 1920, 1080, turns, words)
    assert mode == "complex" and "vstack=inputs=2" in graph


def test_wide_two_shot_beside_a_closeup_switches_at_the_cut():
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
    # The close-up is a face crop until the cut, then both people are stacked.
    assert "[s0]trim=end=5.000,crop=" in graph
    assert "[s1]trim=start=5.000,split=2" in graph and "vstack=inputs=2" in graph
    assert "force_original_aspect_ratio=decrease" not in graph


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


def footage(time, **kwargs):
    """A sample of moving live footage without a close face (b-roll, a POV walk)."""
    return replace(SceneEvidence(time, motion=0.12, outside_motion=0.4, motion_spread=0.6,
                                 detail_left=0.1, detail_right=0.8), **kwargs)


def test_moving_footage_without_a_face_fills_the_frame_at_its_detail():
    plan = build_context_layout_plan([footage(i / 3) for i in range(30)], [], 10, 0.316)
    assert [(p["kind"], p["reason"]) for p in plan] == [("cover", "no_reliable_close_subject")]
    assert plan[0]["x"] == pytest.approx(0.45)


@pytest.mark.parametrize("kwargs,x", [
    ({"motion": 0.0}, 0.45),                           # a still picture without text: a photo, a product shot
    ({"text_block_lines": 3}, 0.45),                   # moving footage in which shapes stack like a paragraph
    ({"face_count": 3, "face_area": 0.004, "face_span": 0.6}, 0.5),   # a crowd: on its largest face
], ids=["still", "paragraph-shapes", "crowd"])
def test_footage_that_is_not_content_fills_the_frame(kwargs, x):
    plan = build_context_layout_plan([footage(i / 3, **kwargs) for i in range(30)], [], 10, 0.316)
    assert [(p["kind"], p["reason"]) for p in plan] == [("cover", "no_reliable_close_subject")]
    assert plan[0]["x"] == pytest.approx(x)


@pytest.mark.parametrize("sample,reason", [
    (footage(0, motion=0.0, text_block_lines=3), "text_block"),          # a document, a tweet, a slide
    (footage(0, motion=0.0, cut_text_lines=2), "labelled_still"),        # a chart, a side-by-side comparison
    (subject(face_area=0.03, text_rows=8), "screen_content_outside_crop"),   # a desktop with a webcam on it
], ids=["document", "chart", "desktop"])
def test_still_content_keeps_the_full_frame(sample, reason):
    plan = build_context_layout_plan([replace(sample, time=i / 3) for i in range(30)], [], 10, 0.316)
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [(0, 10, "fit", reason)]
    assert "x" not in plan[0]


def test_a_slow_push_in_over_a_photo_fills_the_frame():
    samples = [footage(i / 3, motion=0.0, camera_motion=0.005, text_rows=6) for i in range(30)]
    plan = build_context_layout_plan(samples, [], 10, 0.316)
    assert plan[0]["kind"] == "cover"


def test_a_lone_small_face_in_moving_footage_is_the_crop_centre():
    samples = [footage(i / 3, face_count=1, face_area=0.004, face_span=0.05, face_center=0.7) for i in range(30)]
    plan = build_context_layout_plan(samples, [], 10, 0.316)
    assert plan[0]["kind"] == "cover" and plan[0]["x"] == pytest.approx(0.7)


def test_a_montage_with_one_document_keeps_its_crops():
    # 0-4 s b-roll, 4-7 s a document on screen, 7-12 s the speaker, 12-16 s b-roll.
    samples = (
        [footage(i / 3) for i in range(12)]
        + [SceneEvidence(4 + i / 3, text_block_lines=4, text_rows=8) for i in range(9)]
        + [subject(7 + i / 3) for i in range(15)]
        + [footage(12 + i / 3, detail_left=0.4, detail_right=1.0) for i in range(12)]
    )
    plan = build_context_layout_plan(samples, [4, 7, 12], 16, 0.316)
    assert [(p["kind"], p["start"], p["end"]) for p in plan] == [
        ("cover", 0, 4), ("fit", 4, 7), ("face", 7, 12), ("cover", 12, 16)]
    assert plan[3]["x"] == pytest.approx(0.7)


def test_footage_with_captions_the_crop_would_cut_fills_the_frame():
    # An edited video without faces: a two-line caption comes up in a quarter of the shot, wider than the crop.
    # A line or two over footage is not content; the caption code deals with the source's own text.
    samples = [footage(i / 3, text_block_lines=2, cut_text_lines=2) if i % 4 == 0 else footage(i / 3)
               for i in range(60)]
    # One line across the frame in every sample, over moving footage.
    title = [footage(i / 3, cut_text_lines=1) for i in range(9)]
    # Labels everywhere, two or more cut in most samples, over moving footage.
    labels = [footage(i / 3, cut_text_lines=3) for i in range(30)]
    for evidence, duration in ((samples, 20), (title, 3), (labels, 10)):
        plan = build_context_layout_plan(evidence, [], duration, 0.316)
        assert [(p["kind"], p["reason"]) for p in plan] == [("cover", "no_reliable_close_subject")]
        assert plan[0]["x"] == pytest.approx(0.45)


def test_a_still_picture_whose_labels_the_crop_would_cut_keeps_the_whole_picture():
    # A title card: one line across the frame, in every sample, and nothing moves.
    card = [footage(i / 3, motion=0.0, cut_text_lines=1) for i in range(9)]
    assert [(p["kind"], p["reason"]) for p in build_context_layout_plan(card, [], 3, 0.316)] == [
        ("fit", "labelled_still")]
    # A drawn map: labels everywhere, two or more cut in most samples.
    drawn = [footage(i / 3, motion=0.0, cut_text_lines=3) for i in range(30)]
    assert [(p["kind"], p["reason"]) for p in build_context_layout_plan(drawn, [], 10, 0.316)] == [
        ("fit", "labelled_still")]
    # A line cut in half of the samples is a texture, not a label: the picture fills the frame.
    stray = [footage(i / 3, motion=0.0, cut_text_lines=i % 2) for i in range(30)]
    assert [(p["kind"], p["reason"]) for p in build_context_layout_plan(stray, [], 10, 0.316)] == [
        ("cover", "no_reliable_close_subject")]


def test_stray_lines_in_plain_footage_do_not_stop_the_crop():
    # Signs and textures: a line cut in half the samples, two in a fifth, no caption block. As measured on gameplay.
    samples = [footage(i / 3, cut_text_lines=2 if i % 5 == 0 else (1 if i % 2 == 0 else 0)) for i in range(60)]
    assert build_context_layout_plan(samples, [], 20, 0.316)[0]["kind"] == "cover"


def test_a_clip_that_is_mostly_content_fits_only_its_content_shots():
    # Two seconds of b-roll, then ten of a document: the document keeps its full width, the b-roll still
    # fills the frame. A clip is never letterboxed as a whole because most of it is.
    samples = [footage(i / 3) for i in range(6)] + [SceneEvidence(2 + i / 3, text_block_lines=4) for i in range(30)]
    plan = build_context_layout_plan(samples, [2], 12, 0.316)
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [
        (0, 2, "cover", "no_reliable_close_subject"), (2, 12, "fit", "text_block")]
    assert plan[0]["x"] == pytest.approx(0.45)


def test_cover_shots_render_as_full_height_crops():
    evidence = [footage(i / 3) for i in range(30)]
    report = {}
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=([], [], [], evidence)):
        graph, mode, caption_y = reframing.build_vertical_filter_plan(Path("broll.mp4"), 1920, 1080, report=report)
    # 0.45 of 1920 is the centre: x = 864 - 303 = 561 → even 560.
    assert report["kind"] == "crop" and mode == "complex" and caption_y is None
    assert "crop=606:ih:x='560':y=0,scale=1080:-2" in graph


def test_cover_and_face_shots_switch_at_the_cut():
    evidence = [footage(i / 3) for i in range(15)] + [subject(5 + i / 3, face_center=0.3) for i in range(15)]
    track = [(s.time, 576, s.face_area) for s in evidence[15:]]
    report = {}
    with patch.object(reframing, "ffprobe_duration", return_value=10), \
         patch.object(reframing, "analyze_vertical_clip", return_value=(track, [5.0], [], evidence)):
        graph, _, _ = reframing.build_vertical_filter_plan(Path("doc.mp4"), 1920, 1080, report=report)
    assert report["kind"] == "compositor"
    assert "crop=606:ih:x='560'" in graph and "between(t\\,0.000\\,5.000)" in graph


# -- Fill first: a shot is shown small only when reading it finds content ---------------------------

def line(text, left, right, y, height=0.04):
    """The words of one read line, spread evenly from ``left`` to ``right`` (0-1 of the frame) around ``y``."""
    parts = text.split()
    step = (right - left) / len(parts)
    return [ScreenWord(part, left + i * step, y - height / 2, left + (i + 1) * step - 0.01, y + height / 2, 90.0)
            for i, part in enumerate(parts)]


def reader(read):
    """A ``read_words`` stand-in that answers ``read(time)`` and records the moments it was asked for."""
    asked = []

    def read_words(moment):
        asked.append(moment)
        return read(moment)

    read_words.asked = asked
    return read_words


# A quiz card over a wide shot: the question in the middle, two rows of answers across the frame under it.
QUESTION = line("Which country has the", 0.25, 0.75, 0.40) + line("most time zones", 0.30, 0.70, 0.46)
ANSWERS = line("United States France", 0.04, 0.96, 0.54) + line("South Africa Russia", 0.04, 0.96, 0.62)


def test_a_talking_head_in_front_of_shapes_that_look_like_text_is_never_letterboxed():
    # Brickwork, shelves, a barred window behind the speaker: every shape test sees text. Nothing is written
    # there, so reading the frames finds no words and the shot fills the frame on the speaker.
    samples = [subject(i / 3, text_block_lines=3, cut_text_lines=3, text_rows=5) for i in range(36)]
    assert context_reason(samples[0], 0.316) == "text_cut_by_crop"
    read_words = reader(lambda _moment: [])
    plan = build_context_layout_plan(samples, [4, 8], 12, 0.316, read_words)
    assert read_words.asked
    # The only doubt was text, and there is none: a talking head, followed by the tracked crop like any other.
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [(0, 12, "face", "confident_subject")]
    # Without OCR a person talking to the camera is the shot, whatever the shapes behind them look like.
    assert [(p["kind"], p["reason"]) for p in build_context_layout_plan(samples, [4, 8], 12, 0.316)] == [
        ("face", "confident_subject")]
    # Two people in front of the same wall are not one face to follow: a still crop on the larger one.
    pair = [replace(s, face_count=2, face_span=0.3) for s in samples]
    assert [(p["kind"], p["reason"]) for p in build_context_layout_plan(pair, [], 12, 0.316, read_words)] == [
        ("cover", "text_cut_by_crop")]


def test_a_quiz_card_over_a_wide_shot_keeps_the_full_width_and_so_does_its_question_beside_it():
    # 0-4 s a close-up with the question already up, 4-8 s the wide shot with the whole card, 8-12 s a close-up.
    question_shot = [subject(i / 3, cut_text_lines=1) for i in range(12)]
    card_shot = [footage(4 + i / 3, cut_text_lines=2) for i in range(12)]
    plain = [subject(8 + i / 3) for i in range(12)]
    read_words = reader(lambda moment: QUESTION if moment < 4 else QUESTION + ANSWERS)
    # The card alone: eleven words on four lines, the answers (and more) outside the centre crop.
    alone = build_context_layout_plan(
        [replace(s, time=s.time - 4) for s in card_shot], [], 4, 0.316, lambda _moment: QUESTION + ANSWERS)
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in alone] == [(0, 4, "fit", "card")]
    # Two lines by themselves are a caption: the close-up keeps its face crop and is not even read.
    assert [(p["kind"], p["reason"]) for p in build_context_layout_plan(
        question_shot, [], 4, 0.316, lambda _moment: QUESTION)] == [("face", "confident_subject")]
    # Next to the card, the same two lines are the card: the clip does not drop the full width around it.
    plan = build_context_layout_plan(question_shot + card_shot + plain, [4, 8], 12, 0.316, read_words)
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [
        (0, 8, "fit", "card"), (8, 12, "face", "confident_subject")]
    assert any(moment < 4 for moment in read_words.asked) and not any(moment >= 8 for moment in read_words.asked)


@pytest.mark.parametrize("subtitle", [
    line("and that is when I knew", 0.2, 0.8, 0.85),
    line("and that is when I", 0.2, 0.8, 0.82) + line("knew it was over", 0.25, 0.75, 0.88),
], ids=["one-line", "two-lines"])
def test_a_subtitle_over_moving_footage_fills_the_frame(subtitle):
    samples = [footage(i / 3, cut_text_lines=1) for i in range(30)]
    read_words = reader(lambda _moment: subtitle)
    plan = build_context_layout_plan(samples, [], 10, 0.316, read_words)
    assert read_words.asked
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in plan] == [
        (0, 10, "cover", "no_reliable_close_subject")]
    assert plan[0]["x"] == pytest.approx(0.45)


def test_a_failed_read_fills_moving_footage_and_keeps_a_still_document():
    # OCR broke (no binary, a timeout): the planner keeps its no-OCR behaviour. Moving footage fills the
    # frame whatever the shapes suggest; only a picture that does not move can still be a document.
    read_words = reader(lambda _moment: None)
    moving = [footage(i / 3, text_block_lines=3, cut_text_lines=3) for i in range(30)]
    plan = build_context_layout_plan(moving, [], 10, 0.316, read_words)
    assert read_words.asked
    assert [(p["kind"], p["reason"]) for p in plan] == [("cover", "no_reliable_close_subject")]
    assert plan[0]["x"] == pytest.approx(0.45)
    still = [replace(sample, motion=0.0) for sample in moving]
    assert [(p["kind"], p["reason"]) for p in build_context_layout_plan(still, [], 10, 0.316, read_words)] == [
        ("fit", "text_block")]


def entry(start, end, kind, reason, fill_x=0.5):
    return {"start": start, "end": end, "kind": kind, "reason": reason, "fill_x": fill_x}


def test_a_fit_too_short_to_read_between_two_crops_fills_the_frame():
    plan = [
        entry(0, 5, "face", "confident_subject"),
        entry(5, 6.5, "fit", "text_block", fill_x=0.3),
        entry(6.5, 12, "face", "confident_subject"),
    ]
    stable = stabilize_layout_plan(plan)
    assert [(p["start"], p["end"], p["kind"], p["reason"]) for p in stable] == [
        (0, 5, "face", "confident_subject"), (5, 6.5, "cover", "brief_content"), (6.5, 12, "face", "confident_subject")]
    # Its own full-height crop, where the shot would have been cropped had it not been content.
    assert stable[1]["x"] == 0.3


def test_a_fit_long_enough_to_read_between_two_crops_stays():
    plan = [
        entry(0, 5, "face", "confident_subject"),
        entry(5, 11, "fit", "text_block"),
        entry(11, 16, "face", "confident_subject"),
    ]
    assert stabilize_layout_plan(plan) == plan


def test_alternating_crops_and_content_are_never_collapsed_into_one_fit():
    # Forty seconds, five shots of eight: speaker, slide, speaker, slide, speaker.
    plan = [
        entry(start, start + 8, *(("fit", "page") if index % 2 else ("face", "confident_subject")))
        for index, start in enumerate(range(0, 40, 8))
    ]
    stable = stabilize_layout_plan(plan)
    assert stable == plan
    assert [p["kind"] for p in stable] == ["face", "fit", "face", "fit", "face"]


# --- text on footage that is not content (screening real gameplay and explainer clips) ---------------


def test_labels_scattered_over_a_game_or_a_map_are_not_content():
    from src.media.scene_context import content_from_words, text_blocks
    from src.media.screen_text import text_lines

    # A weapon wheel: a tab row at the top, a counter, a caption in the middle. Nothing is stacked into a block.
    wheel = (line("WEAPONS ITEMS", 0.42, 0.58, 0.08) + line("6 93", 0.47, 0.53, 0.27)
             + line("UNARMED", 0.44, 0.56, 0.47) + line("IN HAND", 0.45, 0.55, 0.52)
             + line("2 15", 0.05, 0.95, 0.36))
    assert content_from_words([wheel] * 3, 0.342, 0.658) is None
    # A map with a few place names, still, nobody in it: it fills the frame like any other picture.
    town = line("VICE CITY", 0.70, 0.90, 0.60) + line("MIAMI BEACH", 0.10, 0.30, 0.20) + line("LOST TOWN", 0.4, 0.6, 0.35)
    assert content_from_words([town] * 3, 0.342, 0.658) is None
    # Numbers are not words: a scoreboard of digits never becomes a page.
    digits = [word for row in range(8) for word in line("12 34 56 78 90", 0.05, 0.95, 0.1 + row * 0.05)]
    assert content_from_words([digits] * 3, 0.342, 0.658) is None
    # The same number of words stacked as a list is a card.
    stacked = [word for row in range(5) for word in line("first second third", 0.05, 0.95, 0.30 + row * 0.06)]
    assert len(text_blocks(text_lines(stacked))) == 1
    assert content_from_words([stacked] * 3, 0.342, 0.658) == "card"


def test_a_chapter_card_and_a_menu_are_not_text_content_but_a_list_of_sentences_is():
    from src.media.scene_context import content_from_words

    # Big heading, a slogan, a counter: three "lines", but only one is a line of text.
    chapter = (line("THE COPS", 0.05, 0.4, 0.40) + line("test the cops today", 0.05, 0.6, 0.47)
               + line("Lose them", 0.05, 0.3, 0.54) + line("10 stars", 0.7, 0.95, 0.47))
    assert content_from_words([chapter] * 3, 0.342, 0.658) is None
    # A pause menu: a column of one- and two-word entries.
    menu = [word for row, entry in enumerate(["Resume game", "Load game", "Settings", "Online mode", "Quit now"])
            for word in line(entry, 0.05, 0.3, 0.3 + row * 0.06)]
    assert content_from_words([menu] * 3, 0.342, 0.658) is None
    # Bullet points on a slide: lines of text, stacked.
    slide = [word for row in range(4) for word in line("this point has five words", 0.05, 0.95, 0.3 + row * 0.07)]
    assert content_from_words([slide] * 3, 0.342, 0.658) == "page"


def test_the_columns_each_full_height_crop_shows():
    from src.media.reframing import FACE_CROP_DRIFT, crop_windows

    plan = [
        {"start": 0.0, "end": 4.0, "kind": "cover", "x": 0.5, "fill_x": 0.5},
        {"start": 4.0, "end": 9.0, "kind": "face", "fill_x": 0.8},
        {"start": 9.0, "end": 12.0, "kind": "fit"},
        {"start": 12.0, "end": 15.0, "kind": "cover", "x": 0.05, "fill_x": 0.05},
    ]
    share = 608 / 1920
    windows = crop_windows(plan, share)
    assert [(a, b) for a, b, _, _ in windows] == [(0.0, 4.0), (4.0, 9.0), (12.0, 15.0)]     # a fitted shot crops nothing
    assert windows[0][2:] == pytest.approx((0.5 - share / 2, 0.5 + share / 2))
    # A tracked crop moves: it counts as narrower than it is.
    assert windows[1][2:] == pytest.approx((0.8 - share / 2 + FACE_CROP_DRIFT, 0.8 + share / 2 - FACE_CROP_DRIFT))
    assert windows[2][2:] == pytest.approx((0.0, share))                                     # kept inside the frame
    assert crop_windows(plan, 1.0) == []                                                     # a portrait source


def _portrait_in_wide(frames=12, left=104, right=215, seed=3):
    """Grey frames of a moving portrait picture between twinkling decorated bars."""
    rng = np.random.default_rng(seed)
    clip = np.empty((frames, 180, 320), dtype=np.uint8)
    for index in range(frames):
        clip[index] = rng.integers(16, 24, size=(180, 320))                              # dark, twinkling bars
        # The picture: a different brightness in every frame, smooth across (a face, a wall, a stage).
        clip[index, :, left:right] = 90 + 10 * index + rng.integers(0, 5, size=(180, right - left))
    return clip


def test_a_portrait_picture_set_in_a_wide_frame_is_found_by_its_two_lasting_edges():
    left, right = reframing.inner_picture(_portrait_in_wide())
    assert left == pytest.approx(104 / 320, abs=0.01) and right == pytest.approx(215 / 320, abs=0.01)
    # Ordinary footage has no such edges.
    rng = np.random.default_rng(5)
    assert reframing.inner_picture(rng.integers(0, 255, size=(12, 180, 320), dtype=np.uint8)) is None
    # A still camera on a scene full of uprights (people standing, trees, poles) has many, not two.
    busy = np.tile(np.repeat(np.array([30, 200] * 8, dtype=np.uint8), 20)[None, None, :], (12, 180, 1))
    assert reframing.inner_picture(busy) is None
    # An off-centre panel (a split screen, a picture-in-picture) is not a portrait picture in the middle.
    assert reframing.inner_picture(_portrait_in_wide(left=20, right=140)) is None
    assert reframing.inner_picture(_portrait_in_wide(frames=2)) is None                  # too little to judge


def test_cover_crops_stay_inside_the_portrait_picture():
    # The crop sat a little to the right of the inner picture: a strip of the side bar showed and the first
    # letter of the picture's own captions was cut off.
    share = 608 / 1920
    plan = [
        {"start": 0.0, "end": 6.0, "kind": "cover", "x": 0.535, "fill_x": 0.535, "reason": "subjects_spread_across_frame"},
        {"start": 6.0, "end": 12.0, "kind": "face", "fill_x": 0.52},
    ]
    kept = reframing.keep_crops_inside_picture(plan, _portrait_in_wide(), share)
    left, right = 104 / 320, 215 / 320
    assert left - 0.01 <= kept[0]["x"] - share / 2 and kept[0]["x"] + share / 2 <= right + 0.01
    assert kept[0]["fill_x"] == kept[0]["x"] and kept[0]["reason"] == "subjects_spread_across_frame"
    # A crop that follows a face near the picture's edge showed the bar beside it. In a picture about as
    # wide as the crop there is nobody to follow: the shot becomes a still crop inside the picture.
    assert kept[1]["kind"] == "cover" and (kept[1]["start"], kept[1]["end"]) == (6.0, 12.0)
    assert left - 0.01 <= kept[1]["x"] - share / 2 and kept[1]["x"] + share / 2 <= right + 0.01
    # A wider inner picture (a 4:3 picture in a 16:9 frame) leaves room to follow a face: tracking stays.
    wide = reframing.keep_crops_inside_picture(plan, _portrait_in_wide(left=88, right=232), share)
    assert wide[1] == plan[1]
    # No inner picture, no frames, or a crop already inside: the plan is untouched.
    rng = np.random.default_rng(7)
    assert reframing.keep_crops_inside_picture(plan, rng.integers(0, 255, size=(12, 180, 320), dtype=np.uint8), share) == plan
    assert reframing.keep_crops_inside_picture(plan, None, share) == plan
    centred = [{"start": 0.0, "end": 12.0, "kind": "cover", "x": 0.5, "fill_x": 0.5}]
    assert reframing.keep_crops_inside_picture(centred, _portrait_in_wide(), share) == centred
