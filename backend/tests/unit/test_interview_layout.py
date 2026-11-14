"""Per-shot interview composition: stacked two-shots, tight singles, fitted graphics.

Face boxes are reduced from a 1920x1080 sit-down interview (a host and a
guest on a couch, cut between a wide two-shot and single close-ups).
"""

from dataclasses import replace
from pathlib import Path
import re
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from src.clip_source_map import load_clip_vertical_layout, save_clip_vertical_layout
from src.media import interview_layout, reframing
from src.media.interview_layout import (
    OUT_H,
    build_interview_filter,
    frame_changes,
    plan_interview_layout,
    precise_cuts,
)
from src.media.scene_context import ContextAnalyzer, SceneEvidence
from src.services.editor_service import PRESERVED_LAYOUT_KINDS

W, H = 1920, 1080
HOST = (500.0, 222.0, 133.0, 170.0)      # left on the couch
GUEST = (1228.0, 208.0, 148.0, 200.0)    # right on the couch
CLOSE = (760.0, 200.0, 245.0, 325.0)     # the guest's close-up
FPS = 3.0

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")


def jitter(box, i):
    x, y, w, h = box
    return (x + (i % 3) * 4 - 4, y + (i % 2) * 3, w, h)


def samples(shots, fps=FPS):
    """[(start, end, [boxes] | "graphic" flag set)] -> (face_samples, context)."""
    faces, context = [], []
    for start, end, boxes, *flags in shots:
        t = start
        i = 0
        while t < end - 1e-9:
            faces.append((t, [jitter(b, i) for b in boxes]))
            context.append(SceneEvidence(t, text_block_lines=3 if "graphic" in flags else 0))
            t = round(t + 1 / fps, 6)
            i += 1
    return faces, context


def plan(shots, cuts, duration, **kwargs):
    faces, context = samples(shots)
    return plan_interview_layout(W, H, duration, cuts, faces, context, **kwargs)


def kinds(result):
    return [(s["start"], s["end"], s["kind"]) for s in result["segments"]]


INTERVIEW = [
    (0, 5, [HOST, GUEST]),
    (5, 9, [CLOSE]),
    (9, 14, [HOST, GUEST]),
    (14, 18, [CLOSE]),
]


def test_two_shot_stacks_and_close_up_crops_at_each_cut():
    result = plan(INTERVIEW, [5, 9, 14], 18)
    assert result["kind"] == "interview"
    assert kinds(result) == [(0, 5, "pair"), (5, 9, "single"), (9, 14, "pair"), (14, 18, "single")]
    graph = result["video_filter"]
    assert graph.startswith("[0:v]split=4[s0][s1][s2][s3];")
    assert "[s0]trim=end=5.000," in graph and "[s3]trim=start=14.000," in graph
    assert "trim=start=5.000:end=9.000" in graph
    assert graph.count("vstack=inputs=2") == 2
    assert graph.endswith("concat=n=4:v=1:a=0[vout]")
    # The last piece has no end, so a stream longer than the probe still renders.
    assert "trim=start=14.000:end" not in graph


def test_left_person_is_on_top_and_each_return_to_the_two_shot_matches():
    result = plan(INTERVIEW, [5, 9, 14], 18)
    graph = result["video_filter"]
    crops = re.findall(r"\[c(\d)t\]crop=(\d+):(\d+):x=(\d+):y=(\d+).*?\[c\1b\]crop=(\d+):(\d+):x=(\d+):y=(\d+)", graph)
    assert len(crops) == 2
    first, second = crops
    # Same camera setup, same panels: no jump between returns to the two-shot.
    assert first[1:] == second[1:]
    tw, th, tx, ty, bw, bh, bx, by = map(int, first[1:])
    host_cx, guest_cx = HOST[0] + HOST[2] / 2, GUEST[0] + GUEST[2] / 2
    assert tx < host_cx < tx + tw and bx < guest_cx < bx + bw
    # Panels are 9:8, even, inside the frame, and keep each face whole.
    for w, h, x, y, face in ((tw, th, tx, ty, HOST), (bw, bh, bx, by, GUEST)):
        assert w % 2 == h % 2 == x % 2 == y % 2 == 0
        assert abs(w / h - 9 / 8) < 0.01
        assert 0 <= x and x + w <= W and 0 <= y and y + h <= H
        assert x <= face[0] and face[0] + face[2] <= x + w
        assert y < face[1] - 0.3 * face[3] and face[1] + face[3] < y + h
        # Tight: the face is about a third of the panel, never a waist-up wide.
        assert 0.25 <= face[3] / h <= 0.4
        # Enlargement stays under the cap.
        assert 960 / h <= interview_layout.MAX_UPSCALE + 0.01


def test_single_is_a_tight_crop_with_the_eyes_near_the_upper_third():
    wide_single = [(0, 6, [HOST, GUEST]), (6, 12, [(900.0, 300.0, 110.0, 140.0)])]
    result = plan(wide_single, [6], 12)
    graph = result["video_filter"]
    w, h, x, y = map(int, re.search(r"\[s1\]trim=start=6.000,crop=(\d+):(\d+):x=(\d+):y=(\d+)", graph).groups())
    assert abs(w / h - 9 / 16) < 0.01 and w % 2 == h % 2 == 0
    # Zoomed in (not the full 1080 height) but within the upscale cap.
    assert OUT_H / interview_layout.MAX_UPSCALE - 2 <= h < H
    face_y = (300 + 70 - y) / h
    assert 0.3 <= face_y <= 0.42
    assert x <= 900 and 1010 <= x + w


def test_on_screen_graphic_is_shown_in_full_from_its_onset():
    shots = [(0, 3, [HOST, GUEST]), (3, 6, [HOST, GUEST], "graphic"), (6, 10, [CLOSE])]
    result = plan(shots, [6], 10)
    # The graphic layout starts a sample early: the text fades in before the
    # detector reads whole lines.
    assert kinds(result) == [(0, 2.667, "pair"), (2.667, 6, "fit"), (6, 10, "single")]
    assert result["segments"][1]["reason"] == "graphic"
    assert "gblur" in result["video_filter"]


def test_a_stray_paragraph_sample_does_not_flip_the_layout():
    faces, context = samples([(0, 10, [HOST, GUEST])])
    context[12] = replace(context[12], text_block_lines=4)
    context[13] = replace(context[13], text_block_lines=4)
    result = plan_interview_layout(W, H, 10, [5], faces, context)
    assert {s["kind"] for s in result["segments"]} == {"pair"}


def test_b_roll_without_people_fits_and_a_missed_face_flash_does_not():
    shots = [(0, 4, [HOST, GUEST]), (4, 7, []), (7, 7.33, []), (7.33, 12, [HOST, GUEST])]
    result = plan(shots, [4, 7, 7.33], 12)
    assert kinds(result)[1] == (4, 7.33, "fit")
    assert [k for *_, k in kinds(result)] == ["pair", "fit", "pair"]


def test_a_small_lone_face_keeps_the_whole_frame():
    # A distant figure or a game character is not worth a 2x crop.
    result = plan([(0, 4, [HOST, GUEST]), (4, 8, [(900.0, 300.0, 80.0, 100.0)])], [4], 8)
    assert result["segments"][1]["kind"] == "fit"
    assert result["segments"][1]["reason"] == "small_subject"


def test_over_the_shoulder_shot_frames_the_face_not_the_near_shoulder():
    shoulder = (1540.0, 150.0, 320.0, 600.0)   # blurred back of the interviewer's head
    face = (630.0, 215.0, 185.0, 235.0)
    result = plan([(0, 4, [HOST, GUEST]), (4, 8, [shoulder, face])], [4], 8)
    assert result["segments"][1]["kind"] == "single"
    x, w = re.search(r"\[s1\]trim=start=4.000,crop=(\d+):\d+:x=(\d+)", result["video_filter"]).group(2, 1)
    assert int(x) <= 630 and 815 <= int(x) + int(w) < 1540


def test_two_people_close_together_share_one_crop():
    near = [(0, 6, [(800.0, 250.0, 120.0, 150.0), (1010.0, 260.0, 120.0, 150.0)])]
    faces, context = samples(near + [(6, 10, [HOST, GUEST])])
    result = plan_interview_layout(W, H, 10, [6], faces, context)
    assert result["segments"][0]["kind"] == "single"
    assert result["segments"][0]["reason"] == "shared_crop"
    w = int(re.search(r"\[s0\]trim=end=6.000,crop=(\d+):", result["video_filter"]).group(1))
    assert w >= 330 * 1.25 - 2


@pytest.mark.parametrize("shots,cuts", [
    ([(0, 10, [CLOSE])], []),                                         # one talking head
    ([(0, 10, [])], []),                                              # slides / screen recording
    ([(0, 10, [HOST, GUEST, (900.0, 230.0, 140.0, 180.0)])], []),     # three people
    ([(0, 10, [(500.0, 300.0, 40.0, 50.0), (1300.0, 300.0, 40.0, 50.0)])], [5]),  # tiny wide shot
    ([(0, 10, [HOST, (1300.0, 230.0, 60.0, 70.0)])], [5]),            # mismatched sizes
    ([(0, 1, [HOST, GUEST]), (1, 10, [CLOSE])], [1]),                 # a second of two-shot
])
def test_other_content_keeps_the_existing_layouts(shots, cuts):
    assert plan(shots, cuts, 10) is None


def test_webcam_tiles_keep_the_call_stack_even_across_a_jump_cut():
    call = [(0, 8, [(160.0, 480.0, 220.0, 200.0), (1100.0, 500.0, 220.0, 200.0)])]
    assert plan(call, [], 8) is None
    assert plan(call, [4], 8) is None
    # The same pair cut with a close-up is an edited interview.
    assert plan(call + [(8, 12, [CLOSE])], [4, 8], 12) is not None


def test_switches_move_onto_the_exact_frame_of_each_cut(monkeypatch):
    monkeypatch.setattr(interview_layout, "frame_changes", lambda _p: ([], None))
    monkeypatch.setattr(interview_layout, "precise_cuts", lambda *_a: [4.95, 9.24, 14])
    result = plan(INTERVIEW, [5, 9, 14], 18, input_path=Path("clip.mp4"))
    assert [s["start"] for s in result["segments"]] == [0, 4.95, 9.24, 14]
    assert "trim=start=4.950:end=9.240" in result["video_filter"]


def test_cut_search_failure_keeps_the_sampled_times(monkeypatch):
    def boom(*_args):
        raise RuntimeError("decoder")

    monkeypatch.setattr(interview_layout, "frame_changes", boom)
    result = plan(INTERVIEW, [5, 9, 14], 18, input_path=Path("clip.mp4"))
    assert [s["start"] for s in result["segments"]] == [0, 5, 9, 14]


def _thumbnails(levels):
    return np.repeat(np.asarray(levels, np.uint8)[:, None], 64 * 36, axis=1)


def test_precise_cuts_find_hard_cuts_and_keep_soft_ones():
    times = [i / 30 for i in range(300)]
    levels = np.full(300, 100.0)
    levels[151:] = 160                       # straight cut between frames 150 and 151
    levels[60:66] += np.arange(1, 7) * 20    # a fast pan: even steps, no spike
    levels[66:151] = levels[65]
    levels[200] = 255                        # a camera flash: same picture after it
    levels[230], levels[231] = 200, 240      # a two-frame whip into a new shot
    levels[232:] = 40
    cuts = precise_cuts(times, _thumbnails(levels), [5.1, 9.0])
    # The sampled cut at 5.1 is the straight cut; nothing sharper is near 9.0
    # (a dissolve), so it keeps its time.
    assert cuts == [pytest.approx((150 + 151) / 60), pytest.approx(231 / 30, abs=0.04), 9.0]


def test_a_missed_cut_is_found_from_the_people_on_screen():
    # The cut detector found no edit, but a close-up follows the two-shot.
    shots = [(0, 5, [HOST, GUEST]), (5, 10, [CLOSE])]
    result = plan(shots, [], 10)
    assert [k for *_, k in kinds(result)] == ["pair", "single"]
    assert result["segments"][1]["start"] == pytest.approx(5, abs=0.01)


def test_a_guest_looking_away_briefly_does_not_change_the_layout():
    faces, context = samples([(0, 10, [HOST, GUEST])])
    for i in (9, 10, 11, 12):
        faces[i] = (faces[i][0], [faces[i][1][0]])
    result = plan_interview_layout(W, H, 10, [], faces, context)
    assert kinds(result) == [(0, 10, "pair")]


def test_a_handheld_two_shot_is_followed_and_a_tripod_one_is_still():
    # A handheld camera drifts 120px sideways and 60px down over the shot.
    def drift(box, i):
        return (box[0] + 120 * np.sin(i / 10), box[1] + 60 * np.sin(i / 10), box[2], box[3])

    moving = [(i / 3, [drift(HOST, i), drift(GUEST, i)]) for i in range(30)]
    context = [SceneEvidence(t) for t, _ in moving]
    graph = plan_interview_layout(W, H, 10, [], moving, context)["video_filter"]
    assert "crop=" in graph and ":x='trunc(" in graph and ":y='trunc(" in graph
    still = plan(INTERVIEW, [5, 9, 14], 18)["video_filter"]
    assert "trunc(" not in still


def test_single_shot_clip_needs_no_concat():
    graph, _ = build_interview_filter(
        [interview_layout.Segment(0, 10, "pair", "two_people", [HOST, GUEST])], W, H)
    assert graph.startswith("[0:v]split=2[c0t][c0b];") and graph.endswith("[vout]")
    assert "concat" not in graph and "trim" not in graph


def test_describe_uses_the_interview_layout_and_its_plan_is_replayable(monkeypatch, tmp_path):
    faces, context = samples(INTERVIEW)
    track = [(t, None, 0.0) for t, _ in faces]
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: 18.0)
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_a, **_k: (track, [5, 9, 14], faces, context))
    monkeypatch.setattr(interview_layout, "frame_changes", lambda _p: ([], np.zeros(0)))
    described = reframing.describe_vertical_layout(tmp_path / "clip.mp4", W, H)
    assert described["kind"] == "interview" and described["mode"] == "complex"
    # Captions sit below the lower panel's face, inside the platform-safe band.
    assert 0.78 <= described["caption_y"] <= 0.84
    assert "interview" in PRESERVED_LAYOUT_KINDS
    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"")
    save_clip_vertical_layout(clip, {**described, "width": W, "height": H, "duration": 18.0})
    assert load_clip_vertical_layout(clip)["video_filter"] == described["video_filter"]


def test_reaction_inset_still_wins(monkeypatch, tmp_path):
    faces, context = samples(INTERVIEW)
    reaction = {"video_filter": "reaction-filter", "caption_y": 1 / 3, "inset": (0, 0, 10, 10), "source_runs": [1]}
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: 18.0)
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_a, **_k: ([], [5, 9, 14], faces, context))
    monkeypatch.setattr("src.media.reaction_layout.find_reactor_face", lambda *_a: None)
    monkeypatch.setattr("src.media.reaction_layout.choose_reaction_layout", lambda *_a: reaction)
    assert reframing.describe_vertical_layout(tmp_path / "clip.mp4", W, H)["kind"] == "reaction"


def test_interview_planning_failure_falls_back_to_the_generic_layout(monkeypatch, tmp_path):
    faces, context = samples(INTERVIEW)
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: 18.0)
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_a, **_k: ([], [5, 9, 14], faces, context))

    def boom(*_args, **_kwargs):
        raise RuntimeError("bad plan")

    monkeypatch.setattr(interview_layout, "plan_interview_layout", boom)
    assert reframing.describe_vertical_layout(tmp_path / "clip.mp4", W, H)["kind"] != "interview"


# -- paragraph (on-screen graphic) detection ------------------------------------

def test_aligned_sentences_are_a_paragraph():
    frame = np.full((270, 480, 3), 30, np.uint8)
    for i, text in enumerate(["Sent a letter to the City of San", "Marcos today condemning its plan",
                              "for a new development"]):
        cv2.putText(frame, text, (100, 150 + i * 16), cv2.FONT_HERSHEY_SIMPLEX, 0.42, (240, 240, 240), 1)
    sample = ContextAnalyzer().measure(frame, [(60, 30, 50, 60)], 0)
    assert sample.text_block_lines >= 2


def test_scattered_texture_is_not_a_paragraph():
    rng = np.random.default_rng(3)
    frame = np.full((270, 480, 3), 90, np.uint8)
    for _ in range(400):
        x, y = rng.integers([0, 0], [470, 262])
        w, h = rng.integers([2, 4], [6, 10])
        colour = tuple(int(v) for v in rng.integers(0, 255, 3))
        cv2.rectangle(frame, (int(x), int(y)), (int(x + w), int(y + h)), colour, -1)
    sample = ContextAnalyzer().measure(frame, [(200, 40, 70, 90)], 0)
    assert sample.text_block_lines == 0


def test_a_single_name_caption_is_not_a_paragraph():
    frame = np.full((270, 480, 3), 30, np.uint8)
    cv2.putText(frame, "Greg Abbott", (100, 220), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (240, 240, 240), 1)
    assert ContextAnalyzer().measure(frame, [(200, 40, 70, 90)], 0).text_block_lines == 0


# -- real FFmpeg ----------------------------------------------------------------

@pytest.fixture
def cut_clip(tmp_path):
    """2s at 30 fps: a red shot that cuts to a blue one at frame 31 (1.033s)."""
    path = tmp_path / "cut.mp4"
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", "color=red:s=640x360:r=30:d=1.0333",
        "-f", "lavfi", "-i", "color=blue:s=640x360:r=30:d=0.9667",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
        "-filter_complex", "[0:v][1:v]concat=n=2:v=1:a=0,format=yuv420p[v]",
        "-map", "[v]", "-map", "2:a", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path),
    ], capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return path


@needs_ffmpeg
def test_frame_changes_place_the_cut_on_the_exact_frame(cut_clip):
    times, thumbnails = frame_changes(cut_clip)
    assert len(times) == len(thumbnails) == 60
    cuts = precise_cuts(times, thumbnails, [1.333333])
    assert cuts == [pytest.approx((30 / 30 + 31 / 30) / 2, abs=0.02)]


@needs_ffmpeg
def test_rendered_interview_keeps_every_frame_at_1080x1920(cut_clip):
    # 640x360 source: two faces in the "two-shot" (red) and one in the close-up (blue).
    segments = [
        interview_layout.Segment(0, 1.0167, "pair", "two_people",
                                 [(150.0, 80.0, 50.0, 64.0), (440.0, 80.0, 50.0, 64.0)]),
        interview_layout.Segment(1.0167, 2.0, "single", "one_person", [(280.0, 60.0, 80.0, 100.0)]),
    ]
    graph, _ = build_interview_filter(segments, 640, 360)
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(cut_clip), "-filter_complex", graph + ";[vout]scale=54:96[p]",
        "-map", "[p]", "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    frames = np.frombuffer(result.stdout, np.uint8).reshape(-1, 96, 54, 3)
    assert len(frames) == 60
    # Exactly the red frames are stacked panels; the switch lands on the cut.
    red = [bool(f[48, 27, 0] > 180 and f[48, 27, 2] < 80) for f in frames]
    assert red == [True] * 31 + [False] * 29
    probe = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(cut_clip), "-filter_complex", graph,
        "-map", "[vout]", "-frames:v", "1", "-f", "null", "-",
    ], capture_output=True, timeout=60)
    assert probe.returncode == 0, probe.stderr


def test_opening_on_a_two_shot_moves_the_hook_title_to_the_panel_seam():
    assert plan(INTERVIEW, [5, 9, 14], 18)["hook_center_y"] == interview_layout.PAIR_SEAM_Y
    close_first = [(0, 5, [CLOSE]), (5, 9, [HOST, GUEST]), (9, 14, [CLOSE])]
    assert "hook_center_y" not in plan(close_first, [5, 9], 14)
