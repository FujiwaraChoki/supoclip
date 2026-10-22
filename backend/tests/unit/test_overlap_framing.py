"""Crosstalk timing, conservative fallback, and a real final-render regression."""

import json
import shutil
import subprocess

import numpy as np
import pytest

from src.media import reframing, speaker_framing


def word(start, end, speaker):
    return {"text": "hello", "start": start, "end": end, "speaker": speaker}


FACES = [
    {"cx": 100.0, "cy": 50.0, "w": 40.0, "h": 40.0},
    {"cx": 540.0, "cy": 50.0, "w": 40.0, "h": 40.0},
]


def overlaps(words, shots=((0.0, 6.0),), analysis=None):
    return speaker_framing.find_speaker_overlaps(
        words, ["A", "B"], FACES, analysis or {}, shots
    )


def test_word_overlap_is_order_independent_and_holds_briefly():
    words = [word(0, 3, "A"), word(2, 5, "B")]
    assert overlaps(words) == overlaps(list(reversed(words))) == [(2.0, 3.35)]


def test_word_gaps_do_not_flicker_the_split():
    words = [word(0, 4, "A"), word(1, 1.4, "B"), word(1.55, 2.1, "B")]
    assert overlaps(words) == [(1.0, 2.45)]


def test_turn_taking_is_not_bridged_into_overlap():
    # 200 ms words alternate, so each speaker's 200 ms pause is the other
    # person's turn. Bridging those pauses (they are under 250 ms) used to
    # report 0.8 s of crosstalk and switch to the stacked layout.
    words = [
        word(0.0, 0.2, "A"), word(0.2, 0.4, "B"),
        word(0.4, 0.6, "A"), word(0.6, 0.8, "B"),
        word(0.8, 1.0, "A"), word(1.0, 1.2, "B"),
    ]
    assert overlaps(words) == overlaps(list(reversed(words))) == []


def test_a_pause_filled_by_the_other_speaker_is_not_bridged():
    words = [
        word(0.0, 1.0, "A"), word(1.2, 2.5, "A"),
        word(1.0, 1.2, "B"), word(1.3, 2.5, "B"),
    ]
    assert overlaps(words) == [(1.3, 2.85)]


def test_a_shared_pause_still_holds_the_stacked_layout():
    # Neither speaker talks from 1.0-1.2, so that breath is not a turn.
    words = [
        word(0.0, 1.0, "A"), word(1.2, 2.4, "A"),
        word(0.0, 1.0, "B"), word(1.2, 2.4, "B"),
    ]
    assert overlaps(words) == [(0.0, 2.75)]


@pytest.mark.parametrize("words", [
    [word(0, 3, "A"), word(3, 5, "B")],
    [word(0, 4, "A"), word(2, 2.2, "B")],
    [word(0, 3, "A"), word(1, 2, None)],
    [word(0, 2, "A"), word(1, 1, "B")],
])
def test_solo_speech_and_brief_acknowledgements_keep_the_single_shot(words):
    assert overlaps(words) == []


def test_split_stops_at_camera_cut_and_never_enters_single_face_shot():
    words = [word(0, 6, "A"), word(1, 6, "B")]
    assert overlaps(words, shots=[(0, 2), (4, 6)]) == [(1.0, 2), (4, 6)]


def test_visual_crosstalk_works_with_exclusive_diarization():
    # Each face moves in its own solo turn, then both mouths move at 2-3s.
    motion = np.ones((60, 36, 64), dtype=np.float32)
    motion[:30, 5:8, 8:12] = 5
    motion[20:50, 5:8, 52:56] = 5
    analysis = {"motion": motion, "motion_fps": 10.0, "motion_cell": 10.0}
    words = [word(0, 3, "A"), word(3, 6, "B")]

    assert overlaps(words, analysis=analysis) == [(2.0, 3.35)]


def test_uniform_camera_motion_cannot_trigger_visual_crosstalk():
    motion = np.ones((60, 36, 64), dtype=np.float32)
    motion[10:30] = 5  # a sustained camera move affects the whole frame
    analysis = {"motion": motion, "motion_fps": 10.0, "motion_cell": 10.0}
    assert overlaps([word(0, 3, "A"), word(3, 6, "B")], analysis=analysis) == []


def test_mapping_learns_only_from_solo_speech():
    motion = np.ones((60, 36, 64), dtype=np.float32)
    motion[:20, 5:8, 8:12] = 5
    motion[20:40, 5:8, 8:12] = 50  # must not contaminate B's mapping
    motion[20:, 5:8, 52:56] = 5
    analysis = {"motion": motion, "motion_fps": 10.0, "motion_cell": 10.0}
    activity = speaker_framing.build_speaker_activity(
        analysis, FACES, [word(0, 3.9, "A"), word(2, 6, "B")], [(0, 6)], []
    )
    assert speaker_framing.assign_speakers_to_clusters(activity) == {"A": 0, "B": 1}


def install_analysis(monkeypatch, duration=2.4, single_shot=False, noisy=False):
    samples = [
        (i / 10, [(80.0, 30.0, 40.0, 40.0), (520.0, 30.0, 40.0, 40.0)])
        for i in range(round(duration * 10))
    ]
    if single_shot:
        samples = [(t, faces[:1]) for t, faces in samples]
    if noisy:
        samples = [(t, faces + ([(300.0, 280.0, 20.0, 20.0)] if i % 3 == 0 else []))
                   for i, (t, faces) in enumerate(samples)]
    monkeypatch.setattr(speaker_framing, "analyze_speaker_clip", lambda *a: {
        "face_samples": samples, "scene_cuts": [],
        "motion": np.zeros((len(samples), 36, 64)), "motion_fps": 10.0, "motion_cell": 10.0,
    })
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *a: (
        [(t, 100.0, 0.02) for t, _ in samples], [], samples,
    ))


def test_entirely_overlapping_clip_splits_without_an_identity_mapping(monkeypatch, tmp_path):
    install_analysis(monkeypatch)
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _: 2.4)
    graph, mode, caption_y = reframing.build_vertical_filter_plan(
        tmp_path / "clip.mp4", 640, 360, [], [word(0, 2.4, "A"), word(0, 2.4, "B")]
    )
    assert mode == "complex"
    assert "vstack=inputs=2" in graph
    assert caption_y == 0.5


def test_offscreen_speaker_cannot_trigger_a_split(monkeypatch, tmp_path):
    install_analysis(monkeypatch, single_shot=True)
    plan = speaker_framing.build_speaker_filter_plan(
        tmp_path / "clip.mp4", 640, 360, 202, 360, 2.4, [],
        [word(0, 2.4, "A"), word(0, 2.4, "B")],
    )
    assert plan is None


def test_sporadic_false_face_does_not_disable_the_stable_pair(monkeypatch, tmp_path):
    install_analysis(monkeypatch, noisy=True)
    plan = speaker_framing.build_speaker_filter_plan(
        tmp_path / "clip.mp4", 640, 360, 202, 360, 2.4, [],
        [word(0, 2.4, "A"), word(0, 2.4, "B")],
    )
    assert plan is not None and plan[1] == "complex"


def test_overlap_panels_override_a_call_layout_with_empty_space(monkeypatch, tmp_path):
    install_analysis(monkeypatch)
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _: 2.4)
    monkeypatch.setattr("src.media.talking_head_layout.choose_talking_head_layout", lambda *a: {
        "kind": "stack", "video_filter": "old_call_layout", "upscale": 1.1, "caption_y": 0.54,
    })
    graph, mode, caption_y = reframing.build_vertical_filter_plan(
        tmp_path / "clip.mp4", 640, 360, [], [word(0, 2.4, "A"), word(0, 2.4, "B")]
    )
    assert mode == "complex" and caption_y == 0.5
    assert "vstack=inputs=2" in graph


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_final_render_switches_solo_stack_solo_and_preserves_audio_and_captions(monkeypatch, tmp_path):
    source, output = tmp_path / "source.mp4", tmp_path / "output.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=red:size=640x360:rate=10:duration=2.4,drawbox=x=320:y=0:w=320:h=360:color=blue:t=fill",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2.4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(source),
    ], check=True, capture_output=True, timeout=60)
    install_analysis(monkeypatch)
    monkeypatch.setattr(speaker_framing, "build_speaker_activity", lambda *a, **k: {
        "A": [1.4, 0.7], "B": [0.7, 1.4],
    })
    # 0-.6s solo A, .6-1.2 overlap, 1.55s onward solo B after the release hold.
    words = [word(0, 1.2, "A"), word(0.6, 2.4, "B")]
    turns = speaker_framing.build_speaker_turns(words, duration=2.4, min_turn=0)
    subtitles = tmp_path / "captions.ass"
    subtitles.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, Alignment\n"
        "Style: Default,DejaVu Sans,60,&H00FFFFFF,5\n"
        "[Events]\nFormat: Layer, Start, End, Style, Text\n"
        "Dialogue: 0,0:00:00.00,0:00:02.40,Default,OVERLAP TEST\n"
    )
    ok, width, height = reframing.render_reframed_clip_ffmpeg(
        source, output, "vertical", subtitle_ass_path=subtitles,
        speaker_turns=turns, speaker_words=words,
    )
    assert ok and (width, height) == (1080, 1920)
    probe = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-of", "json", str(output),
    ]))
    assert {s["codec_type"] for s in probe["streams"]} == {"video", "audio"}
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    assert (video["width"], video["height"], video["r_frame_rate"]) == (1080, 1920, "30/1")
    assert float(video["duration"]) == pytest.approx(2.4, abs=1 / 30)
    frames = subprocess.check_output([
        "ffmpeg", "-v", "error", "-i", str(output), "-vf", "fps=10,format=rgb24",
        "-f", "rawvideo", "-",
    ])
    frames = np.frombuffer(frames, dtype=np.uint8).reshape(-1, 1920, 1080, 3)
    red = lambda p: p[0] > 200 and p[2] < 30
    blue = lambda p: p[2] > 200 and p[0] < 30
    assert red(frames[2, 200, 540]) and red(frames[2, 1700, 540])
    assert red(frames[9, 200, 540]) and blue(frames[9, 1700, 540])
    assert blue(frames[20, 200, 540]) and blue(frames[20, 1700, 540])
    # Full frame is covered, including all four corners, and subtitles survive
    # the complex graph at the seam instead of being applied to only one tile.
    for y in (0, 1919):
        for x in (0, 1079):
            assert frames[9, y, x].max() > 200
    seam = frames[9, 900:1020, 150:930]
    assert np.count_nonzero((seam > 220).all(axis=2)) > 100
