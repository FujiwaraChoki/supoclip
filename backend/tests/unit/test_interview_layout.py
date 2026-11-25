"""Per-shot interview composition: stacked two-shots, tight singles, fitted graphics.

Face boxes are reduced from a 1920x1080 sit-down interview (a host and a
guest on a couch, cut between a wide two-shot and single close-ups).
"""

from dataclasses import replace
from pathlib import Path
import re
import shutil
import subprocess
from unittest.mock import patch

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
from src.media.screen_text import ScreenWord
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


def composed(faces, context, cuts, duration, **kwargs):
    """The plan and the segments it was built from: a cover there has its own reason and crop centre
    (``cover_x``), where the report only says ``single`` / ``cover``."""
    build = interview_layout.build_interview_filter
    with patch.object(interview_layout, "build_interview_filter", wraps=build) as built:
        result = plan_interview_layout(W, H, duration, cuts, faces, context, **kwargs)
    return result, built.call_args.args[0]


def cover_crop(graph, index):
    """(w, h, x, y) of the still crop of piece ``index``."""
    return tuple(map(int, re.search(rf"\[s{index}\]trim=[^,]*,crop=(\d+):(\d+):(\d+):(\d+),", graph).groups()))


def line(text, left, right, y, height=0.04):
    """The words of one read line, spread evenly from ``left`` to ``right`` (0-1 of the frame) around ``y``."""
    parts = text.split()
    step = (right - left) / len(parts)
    return [ScreenWord(part, left + i * step, y - height / 2, left + (i + 1) * step - 0.01, y + height / 2, 90.0)
            for i, part in enumerate(parts)]


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


def test_b_roll_without_people_fills_the_frame_and_a_missed_face_flash_does_not_split_it():
    shots = [(0, 4, [HOST, GUEST]), (4, 7, []), (7, 7.33, []), (7.33, 12, [HOST, GUEST])]
    faces, context = samples(shots)
    result, segments = composed(faces, context, [4, 7, 7.33], 12)
    assert kinds(result) == [(0, 4, "pair"), (4, 7.33, "single"), (7.33, 12, "pair")]
    assert result["segments"][1]["reason"] == "cover"
    # One full-height crop on the middle of the frame (nothing was measured to aim it), not the whole
    # picture on a blurred background.
    assert (segments[1].kind, segments[1].reason, segments[1].cover_x) == ("cover", "no_subject", 0.5)
    assert cover_crop(result["video_filter"], 1) == (606, 1080, 656, 0)
    assert "gblur" not in result["video_filter"]


def test_a_small_lone_face_gets_a_full_height_crop_around_it_not_a_tight_one():
    # A distant figure or a game character is not worth a 2x crop, and the picture is not shown small for
    # it either: the frame fills with a full-height crop around the figure, moving footage or not.
    small = (900.0, 300.0, 80.0, 100.0)
    faces, context = samples([(0, 4, [HOST, GUEST]), (4, 8, [small])])
    result, segments = composed(faces, context, [4], 8)
    assert (result["segments"][1]["kind"], result["segments"][1]["reason"]) == ("single", "cover")
    assert (segments[1].kind, segments[1].reason) == ("cover", "small_subject")
    w, h, x, y = cover_crop(result["video_filter"], 1)
    assert (w, h, y) == (606, 1080, 0)
    assert abs(x + w / 2 - (small[0] + small[2] / 2)) <= 6
    assert "gblur" not in result["video_filter"]


def test_a_stretch_nobody_sampled_fills_the_frame_from_the_centre():
    # The analysis has no sample for the first two seconds.
    faces, context = samples([(2, 6, [HOST, GUEST]), (6, 10, [CLOSE])])
    result, segments = composed(faces, context, [2, 6], 10)
    assert kinds(result) == [(0, 2, "single"), (2, 6, "pair"), (6, 10, "single")]
    assert result["segments"][0]["reason"] == "cover"
    assert (segments[0].kind, segments[0].reason, segments[0].cover_x) == ("cover", "unsampled", 0.5)
    assert cover_crop(result["video_filter"], 0) == (606, 1080, 656, 0)
    assert "gblur" not in result["video_filter"]


def test_a_glance_at_a_person_between_two_stretches_of_a_graphic_keeps_the_whole_frame():
    # While a graphic is up, a second and a half of the guest would drop it and bring it back.
    def shots(seconds):
        return [PAIR_SHOT, (4, 8, [], "graphic"), (8, 8 + seconds, [CLOSE]), (8 + seconds, 14, [], "graphic"),
                (14, 18, [HOST, GUEST])]

    glance = plan(shots(1.5), [4, 8, 9.5, 14], 18)
    assert [(s["start"], s["end"], s["kind"], s["reason"]) for s in glance["segments"]] == [
        (0, 4, "pair", "two_people"), (4, 14, "fit", "graphic"), (14, 18, "pair", "two_people")]
    # Three seconds are a shot of their own: the guest gets the crop.
    assert kinds(plan(shots(3), [4, 8, 11, 14], 18)) == [
        (0, 4, "pair"), (4, 8, "fit"), (8, 11, "single"), (11, 14, "fit"), (14, 18, "pair")]


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
    monkeypatch.setattr("src.media.reaction_layout.choose_reaction_layout", lambda *_a, **_k: reaction)
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


# --- Vlog footage: groups, b-roll and stray detections (three hosts, handheld, food inserts) ---

LEFT = (180.0, 230.0, 200.0, 250.0)       # three friends at a table, across the whole frame
MIDDLE = (940.0, 270.0, 150.0, 185.0)
RIGHT = (1400.0, 180.0, 200.0, 250.0)
PAIR_SHOT = (0, 4, [HOST, GUEST])


def live(shots, **evidence):
    """``samples`` for moving footage: every context sample measures motion (and ``evidence``)."""
    faces, context = samples(shots)
    values = {"motion": 0.1, **evidence}
    return faces, [replace(e, **values) for e in context]


def panel_crops(graph, index):
    found = re.search(
        rf"\[c{index}t\]crop=(\d+):(\d+):x=(\d+):y=(\d+).*?\[c{index}b\]crop=(\d+):(\d+):x=(\d+):y=(\d+)", graph)
    return tuple(map(int, found.groups()))


def test_three_people_in_a_row_are_divided_into_two_panels():
    # Was a letterboxed full frame (reason "group"): a third of the output height, the rest blur.
    result = plan([PAIR_SHOT, (4, 10, [LEFT, MIDDLE, RIGHT])], [4], 10)
    group = result["segments"][1]
    assert (group["kind"], group["reason"]) == ("pair", "group")
    assert "gblur" not in result["video_filter"]
    tw, th, tx, ty, bw, bh, bx, by = panel_crops(result["video_filter"], 1)
    # The widest gap is left of the middle person: the left one is alone on top, the other two share the bottom.
    assert tx <= LEFT[0] and LEFT[0] + LEFT[2] <= tx + tw < MIDDLE[0]
    assert bx <= MIDDLE[0] and RIGHT[0] + RIGHT[2] <= bx + bw
    assert bx > LEFT[0] + LEFT[2]
    for w, h, x, y in ((tw, th, tx, ty), (bw, bh, bx, by)):
        assert abs(w / h - 9 / 8) < 0.01 and w % 2 == h % 2 == 0
        assert 0 <= x and x + w <= W and 0 <= y and y + h <= H
    # Captions and the hook title treat it like any stacked two-shot.
    assert [s["kind"] for s in result["segments"]] == ["pair", "pair"]


def test_a_group_alone_does_not_make_the_clip_an_interview():
    # A three-host podcast or a call gallery keeps speaker framing and the gallery layouts.
    assert plan([(0, 10, [LEFT, MIDDLE, RIGHT])], [5], 10) is None


def test_a_call_grid_and_a_crowd_fill_the_frame_with_the_people_who_share_one_crop():
    grid = [(300.0, 150.0, 160.0, 200.0), (1300.0, 150.0, 160.0, 200.0),
            (300.0, 690.0, 160.0, 200.0), (1300.0, 690.0, 160.0, 200.0)]
    crowd = [(100.0 + 350 * i, 250.0, 150.0, 190.0) for i in range(5)]
    for people in (grid, crowd):
        faces, context = samples([PAIR_SHOT, (4, 10, people)])
        result, segments = composed(faces, context, [4], 10)
        # No two panels hold them, and the whole picture is not shown small for them either.
        assert (result["segments"][1]["kind"], result["segments"][1]["reason"]) == ("single", "cover")
        assert (segments[1].kind, segments[1].reason) == ("cover", "group")
        assert "gblur" not in result["video_filter"]
        w, h, x, y = cover_crop(result["video_filter"], 1)
        assert (w, h, y) == (606, 1080, 0)
        # One column of the grid, two neighbours of the crowd: the most faces one crop holds, centred.
        whole = [face for face in people if x <= face[0] - 4 and face[0] + face[2] + 4 <= x + w]
        assert len(whole) == 2
        centre = (min(face[0] for face in whole) + max(face[0] + face[2] for face in whole)) / 2
        assert segments[1].cover_x == pytest.approx(centre / W, abs=0.005)
        assert abs(x + w / 2 - centre) <= 6


def test_a_passer_by_does_not_turn_two_people_into_a_group():
    # The third face shows in half the samples and cannot share a panel: it is left out, not letterboxed.
    stranger = (1700.0, 620.0, 150.0, 190.0)
    shots = [PAIR_SHOT]
    t = 4.0
    while t < 10 - 1e-9:
        nxt = round(t + 1 / FPS, 6)
        shots.append((t, nxt, [HOST, GUEST] + ([stranger] if len(shots) % 2 else [])))
        t = nxt
    result = plan(shots, [4], 10)
    assert [(s["kind"], s["reason"]) for s in result["segments"]] == [("pair", "two_people")] * 2


def test_a_panel_at_the_frame_edge_stops_at_the_neighbours_face():
    # A booth: the right-hand person sits against the frame edge, so their panel is pushed back towards the
    # others. It must not show the middle person a second time.
    edge = (1650.0, 150.0, 250.0, 330.0)
    booth = [(250.0, 320.0, 180.0, 230.0), (820.0, 280.0, 160.0, 200.0), edge]
    result = plan([PAIR_SHOT, (4, 10, booth)], [4], 10)
    assert result["segments"][1]["reason"] == "group"
    *_, bw, bh, bx, by = panel_crops(result["video_filter"], 1)
    assert bx >= 820 + 160 and bx + bw <= W


def test_b_roll_fills_the_frame_moving_or_still_and_a_labelled_still_picture_does_not():
    shots = [PAIR_SHOT, (4, 8, [])]
    faces, context = live(shots, focus_center=0.42)
    result = plan_interview_layout(W, H, 8, [4], faces, context)
    # Reported as a frame-filling crop, so captions sit where they do on a close-up.
    assert (result["segments"][1]["kind"], result["segments"][1]["reason"]) == ("single", "cover")
    assert "gblur" not in result["video_filter"]
    w, h, x, y = map(int, re.search(r"\[s1\]trim=start=4.000,crop=(\d+):(\d+):(\d+):(\d+)", result["video_filter"]).groups())
    assert (w, h, y) == (606, 1080, 0)
    assert abs((x + w / 2) / W - 0.42) < 0.01
    # A photo or a product shot that does not move fills the frame as well, from the middle here (no focus
    # was measured).
    faces, context = samples(shots)
    still, segments = composed(faces, context, [4], 8)
    assert (still["segments"][1]["kind"], still["segments"][1]["reason"]) == ("single", "cover")
    assert (segments[1].kind, segments[1].reason, segments[1].cover_x) == ("cover", "no_subject", 0.5)
    assert cover_crop(still["video_filter"], 1) == (606, 1080, 656, 0)
    assert "gblur" not in still["video_filter"]
    # A chart or a screenshot: a still picture whose labels the crop would cut. The whole picture stays.
    labelled = [replace(e, cut_text_lines=2) if e.time >= 4 else e for e in context]
    chart = plan_interview_layout(W, H, 8, [4], faces, labelled)
    assert (chart["segments"][1]["kind"], chart["segments"][1]["reason"]) == ("fit", "labelled_still")
    assert "gblur" in chart["video_filter"]
    # The same labels over moving footage are signs and captions: the footage fills the frame.
    signs = plan_interview_layout(W, H, 8, [4], faces, [replace(e, motion=0.1) for e in labelled])
    assert (signs["segments"][1]["kind"], signs["segments"][1]["reason"]) == ("single", "cover")
    # A whip pan (no motion measured, every frame different) fills the frame like any other footage.
    whip = plan_interview_layout(W, H, 8, [4], faces, [replace(e, difference=40.0) for e in context])
    assert (whip["segments"][1]["kind"], whip["segments"][1]["reason"]) == ("single", "cover")


def test_b_roll_showing_a_paragraph_keeps_the_whole_frame_only_when_it_reads_as_one_or_is_still():
    faces, context = live([PAIR_SHOT, (4, 8, [])])
    context = [replace(e, text_block_lines=3) if e.time >= 4 and i % 2 else e for i, e in enumerate(context)]

    def b_roll(result):
        return result["segments"][1]["kind"], result["segments"][1]["reason"]

    # Moving footage that cannot be read: brick courses and shelves stack like a paragraph too. It fills.
    unread = plan_interview_layout(W, H, 8, [4], faces, context)
    assert b_roll(unread) == ("single", "cover") and "gblur" not in unread["video_filter"]
    # Read, and it is a page: thirty words on five lines, most of them outside the crop.
    page = [word for row in range(5) for word in line("the quick brown fox jumps over", 0.1, 0.9, 0.2 + 0.1 * row)]
    asked = []
    read = plan_interview_layout(W, H, 8, [4], faces, context, read_words=lambda t: asked.append(t) or page)
    assert b_roll(read) == ("fit", "graphic") and "gblur" in read["video_filter"]
    assert asked and all(4 <= moment < 8 for moment in asked)
    # Read, and nothing is written there: it fills the frame.
    blank = plan_interview_layout(W, H, 8, [4], faces, context, read_words=lambda _t: [])
    assert b_roll(blank) == ("single", "cover")
    # The reader failed: moving footage fills the frame, as without one.
    failed = plan_interview_layout(W, H, 8, [4], faces, context, read_words=lambda _t: None)
    assert b_roll(failed) == ("single", "cover")
    # A picture that does not move, unread: the paragraph is a graphic, shown whole as before.
    still = plan_interview_layout(W, H, 8, [4], faces, [replace(e, motion=0.0) for e in context])
    assert b_roll(still) == ("fit", "graphic") and "gblur" in still["video_filter"]


def test_a_small_lone_face_in_live_footage_is_covered_around_it():
    small = (1300.0, 300.0, 80.0, 100.0)
    faces, context = live([PAIR_SHOT, (4, 8, [small])])
    result = plan_interview_layout(W, H, 8, [4], faces, context)
    assert (result["segments"][1]["kind"], result["segments"][1]["reason"]) == ("single", "cover")
    w, x = map(int, re.search(r"\[s1\]trim=start=4.000,crop=(\d+):\d+:(\d+):", result["video_filter"]).groups())
    assert abs(x + w / 2 - 1340) <= 6


def test_one_stray_detection_does_not_make_a_two_shot():
    # Two samples, a face in a different place in each: one person the camera swings past, not a pair.
    faces, context = live([PAIR_SHOT, (4, 4.34, [(900.0, 270.0, 220.0, 280.0)]), (4.34, 4.67, [(400.0, 330.0, 220.0, 290.0)]),
                           (4.67, 9, [CLOSE])])
    result = plan_interview_layout(W, H, 9, [4, 4.67], faces, context)
    assert (result["segments"][1]["kind"], result["segments"][1]["reason"]) == ("single", "cover")


def test_a_face_cut_by_the_frame_edge_is_not_the_second_person_of_a_pair():
    # Someone half out of shot beside the subject: a panel on them would show the subject twice.
    partial = (0.0, 180.0, 290.0, 340.0)
    subject = (760.0, 200.0, 300.0, 390.0)
    result = plan([PAIR_SHOT, (4, 8, [partial, subject])], [4], 8)
    assert (result["segments"][1]["kind"], result["segments"][1]["reason"]) == ("single", "one_person")
    w, x = re.search(r"\[s1\]trim=start=4.000,crop=(\d+):\d+:x=(\d+)", result["video_filter"]).group(1, 2)
    assert int(x) <= 760 and 1060 <= int(x) + int(w)


def test_a_person_who_moves_along_is_still_one_person():
    # Lost for a sample and found a face width further on: one cluster, so the three stay a group
    # instead of dropping the mover (half support in each place).
    moved = (LEFT[0] + 230, LEFT[1], LEFT[2], LEFT[3])
    shots = [PAIR_SHOT, (4, 7, [LEFT, MIDDLE, RIGHT]), (7, 7.34, [MIDDLE, RIGHT]), (7.34, 10, [moved, MIDDLE, RIGHT])]
    result = plan(shots, [4], 10)
    assert [(s["start"], s["kind"], s["reason"]) for s in result["segments"]][1] == (4, "pair", "group")


def test_a_sign_behind_people_in_a_handheld_shot_is_not_a_graphic():
    shots = [PAIR_SHOT, (4, 8, [HOST, GUEST], "graphic")]
    faces, context = samples(shots)
    handheld = [replace(e, camera_motion=0.02) if e.time >= 4 else e for e in context]
    result = plan_interview_layout(W, H, 8, [4], faces, handheld)
    assert {s["kind"] for s in result["segments"]} == {"pair"}
    # On a tripod the paragraph is a graphic laid over the interview, as before.
    assert plan(shots, [4], 8)["segments"][-1]["reason"] == "graphic"


def test_focus_centre_finds_the_dish_beside_the_middle():
    frame = np.full((270, 480, 3), 120, np.uint8)
    rng = np.random.default_rng(3)
    dish = np.zeros((150, 120, 3), np.uint8)
    dish[..., 1] = rng.integers(120, 255, (150, 120))   # saturated, textured
    dish[..., 2] = 230
    frame[60:210, 90:210] = dish
    evidence = ContextAnalyzer().measure(frame, [], 0.0)
    assert 0.25 <= evidence.focus_center <= 0.4
    assert ContextAnalyzer().measure(np.full((270, 480, 3), 120, np.uint8), [], 0.0).focus_center == 0.5
    assert SceneEvidence(0.0).focus_center == 0.5
# A static podcast two-shot: two people at the far left and right of the room,
# too far apart for one 9:16 crop, and no camera cut in the whole clip.
LEFT = (300.0, 240.0, 150.0, 190.0)
RIGHT = (1450.0, 230.0, 150.0, 195.0)


def _podcast(dropouts):
    """30 s of LEFT and RIGHT; in each (first, last, keep) only ``keep`` is found."""
    faces, context = [], []
    for i in range(90):
        t = round(i / FPS, 6)
        boxes = [LEFT, RIGHT]
        for first, last, keep in dropouts:
            if first <= i < last:
                boxes = [keep]
        faces.append((t, [jitter(b, i) for b in boxes]))
        context.append(SceneEvidence(t))
    return faces, context


def _crops(graph):
    return [tuple(map(int, m)) for m in re.findall(r"crop=(\d+):(\d+):x=(\d+):y=(\d+)", graph)]


@pytest.mark.parametrize("dropouts", [
    # The right-hand guest turns to the host in profile, several times.
    [(12, 21, LEFT), (42, 51, LEFT), (70, 80, LEFT)],
    # Each of them looks away in turn.
    [(12, 21, LEFT), (30, 38, RIGHT), (70, 80, LEFT)],
    # The clip opens with one of them undetected.
    [(0, 9, RIGHT), (45, 54, LEFT)],
])
def test_a_two_shot_stays_stacked_while_one_person_is_not_detected(dropouts):
    faces, context = _podcast(dropouts)
    result = plan_interview_layout(W, H, 30, [], faces, context)
    # One stable composition: no switch and no tight crop onto one edge of the room.
    assert kinds(result) == [(0, 30, "pair")]
    graph = result["video_filter"]
    assert "concat" not in graph
    crops = _crops(graph)
    assert len(crops) == 2
    (tw, _, tx, _), (bw, _, bx, _) = crops
    assert tx < LEFT[0] + LEFT[2] / 2 < tx + tw
    assert bx < RIGHT[0] + RIGHT[2] / 2 < bx + bw


def test_a_lone_face_away_from_the_pair_is_still_its_own_shot():
    # Without a detected cut, a close-up of someone else (not where either
    # person of the two-shot sits) still gets its own tight crop.
    faces, context = _podcast([(45, 90, (860.0, 200.0, 240.0, 320.0))])
    result = plan_interview_layout(W, H, 30, [], faces, context)
    assert [k for *_, k in kinds(result)] == ["pair", "single"]
    assert result["segments"][1]["start"] == pytest.approx(15, abs=0.01)


@pytest.mark.parametrize("box", [
    (1450.0, 230.0, 150.0, 195.0),   # one speaker, off-centre
    (885.0, 240.0, 150.0, 195.0),    # one speaker, centred
])
def test_a_single_speaker_keeps_the_generic_tracked_crop(box):
    faces, context = samples([(0, 30, [box])])
    assert plan_interview_layout(W, H, 30, [], faces, context) is None
    # The generic crop is centred on that one face, as before.
    track = [(t, f[0][0] + f[0][2] / 2, 0.02) for t, f in faces]
    crop_w, _ = reframing.compute_vertical_crop_dims(W, H)
    keys = reframing.build_crop_trajectory(track, W, crop_w)
    x = int(np.median([x for _, x in keys]))
    assert x < box[0] + box[2] / 2 < x + crop_w
    assert abs(x + crop_w / 2 - (box[0] + box[2] / 2)) < crop_w * 0.1


def test_a_single_speaker_after_a_two_shot_is_still_cropped_tight():
    # An off-centre close-up cut in after the two-shot keeps its own crop.
    off_centre = (1300.0, 180.0, 250.0, 320.0)
    result = plan([(0, 6, [LEFT, RIGHT]), (6, 12, [off_centre])], [6], 12)
    assert [k for *_, k in kinds(result)] == ["pair", "single"]
    w, x = map(int, re.search(r"\[s1\]trim=start=6.000,crop=(\d+):\d+:x=(\d+)", result["video_filter"]).groups())
    assert x < off_centre[0] + off_centre[2] / 2 < x + w


# ---------------------------------------------------------------- stacked panels inside one handheld shot

def handheld(parts, moving=True, fps=FPS):
    """[(start, end, [boxes])] of ONE camera shot -> (face_samples, context); ``moving`` marks a camera on the move."""
    faces, context = [], []
    for start, end, boxes in parts:
        t, i = start, 0
        while t < end - 1e-9:
            faces.append((t, [jitter(b, i) for b in boxes]))
            context.append(SceneEvidence(t, difference=50.0 if moving else 0.0))
            t = round(t + 1 / fps, 6)
            i += 1
    return faces, context


def settled(parts, duration, **kwargs):
    faces, context = handheld(parts, **kwargs)
    result = plan_interview_layout(W, H, duration, [], faces, context)
    return result, faces


def test_a_two_shot_that_opens_on_one_person_starts_as_a_single_crop():
    # The camera is on the guest's face, pulls back to both, then swings to the host alone. No cut anywhere.
    result, faces = settled([(0, 1, [CLOSE]), (1, 6, [HOST, GUEST]), (6, 7.4, [HOST])], 7.4)
    assert [s["kind"] for s in result["segments"]] == ["single", "pair", "single"]
    pair = result["segments"][1]
    both = [t for t, boxes in faces if len(boxes) == 2]
    # The panels start on the first sample that shows both and end a frame after the last one.
    assert pair["start"] == pytest.approx(both[0], abs=1e-3)
    assert both[-1] <= pair["end"] <= both[-1] + interview_layout.PAIR_HOLD_SECONDS + 1e-3
    assert result["video_filter"].count("vstack") == 1


def test_no_stretch_of_stacked_panels_shows_only_one_side():
    result, faces = settled([(0, 4, [HOST, GUEST]), (4, 6, [GUEST]), (6, 10, [HOST, GUEST])], 10)
    for segment in result["segments"]:
        inside = [boxes for t, boxes in faces if segment["start"] <= t < segment["end"]]
        if segment["kind"] == "pair":
            assert all(len(boxes) == 2 for boxes in inside)
    assert [s["kind"] for s in result["segments"]] == ["pair", "single", "pair"]


def test_a_missed_detection_in_a_moving_two_shot_keeps_the_panels():
    # Two samples (0.67 s) without the guest, then they are back where they were.
    result, _ = settled([(0, 4, [HOST, GUEST]), (4, 4.6, [HOST]), (4.6, 9, [HOST, GUEST])], 9)
    assert [s["kind"] for s in result["segments"]] == ["pair"]


def test_a_still_two_shot_holds_its_panels_while_one_person_is_undetected():
    # A tripod shot: the guest turns to the host in profile for 1.4 s. Nobody left the picture.
    parts = [(0, 4, [HOST, GUEST]), (4, 5.4, [HOST]), (5.4, 9, [HOST, GUEST])]
    still, _ = settled(parts, 9, moving=False)
    assert [s["kind"] for s in still["segments"]] == ["pair"]
    # The same gap in a moving shot is the camera leaving them.
    moving, _ = settled(parts, 9, moving=True)
    assert [s["kind"] for s in moving["segments"]] == ["pair", "single", "pair"]


def test_one_face_in_both_panels_is_never_held():
    # The detector loses the host while the camera drifts until the guest sits where the host was: the guest's
    # panel follows them into the host's panel. Even a shot measured as still must not show the guest twice.
    drift = [(4 + i / FPS, [(GUEST[0] - (i + 1) * 150.0, GUEST[1], GUEST[2], GUEST[3])]) for i in range(5)]
    faces, context = handheld([(0, 4, [HOST, GUEST])], moving=False)
    faces += drift
    context += [SceneEvidence(t, difference=0.0) for t, _ in drift]
    result = plan_interview_layout(W, H, 4 + 5 / FPS, [], faces, context)
    assert [s["kind"] for s in result["segments"]] == ["pair", "single"]
    # The panels end before the guest's face reaches the host's window (their third drifting sample).
    assert result["segments"][0]["end"] < 4 + 2 / FPS


def test_a_flash_of_two_people_between_single_stretches_stays_single():
    result, _ = settled(
        [(0, 5, [HOST, GUEST]), (5, 7, [HOST]), (7, 7.67, [HOST, GUEST]), (7.67, 9.7, [HOST])], 9.7)
    assert [s["kind"] for s in result["segments"]] == ["pair", "single"]


def test_a_crop_that_would_lose_its_face_follows_it_directly():
    # A whip pan: the face crosses 700 px in a third of a second. The eased crop cannot keep up.
    times = [round(i / 10, 3) for i in range(12)]
    track = {t: (300.0 + (700.0 if t >= 0.5 else 0.0) + i * 2, 300.0, 200.0, 260.0) for i, t in enumerate(times)}
    segment = interview_layout.Segment(0.0, 1.2, "single", "one_person", [interview_layout._median_box(list(track.values()))],
                                       [track], times)
    size = 606
    desired = {t: box[0] + box[2] / 2 - size / 2 for t, box in track.items()}
    expression = interview_layout._axis(segment, desired, size, W - size, still=0.0)
    assert expression.startswith("'")
    keys = interview_layout._follow_keys(segment, sorted((t, min(max(v, 0.0), W - size)) for t, v in desired.items()))
    for t, x in keys[1:-1]:
        if abs(t - 0.5) <= 0.1:
            continue  # the jump itself is spread over the neighbouring frames
        assert abs(x - min(max(desired[t], 0.0), W - size)) <= interview_layout.FOLLOW_MAX_ERROR * size
    # A calm face keeps the still crop.
    calm = {t: 400.0 + (i % 2) * 6 for i, t in enumerate(times)}
    assert not interview_layout._axis(segment, calm, size, W - size, still=400.0).startswith("'")


# ---------------------------------------------------------------------------
# A single crop that cannot stay on its person
# ---------------------------------------------------------------------------

LEFT_FACE = (300.0, 300.0, 180.0, 230.0)
RIGHT_FACE = (575.0, 310.0, 180.0, 230.0)       # the next seat: 275 px away, more than half a crop
MID_FACE = (820.0, 280.0, 180.0, 230.0)


def test_a_stretch_whose_face_keeps_jumping_across_the_frame_holds_a_still_crop_where_the_face_mostly_is():
    # A two-shot, a cut, then a handheld scene whose inner cuts were missed: the one face found is on the left,
    # for a moment on the right, left again, right again. A crop that follows "the person" trails behind and
    # shows the wall between them. The frame stays filled: a full-height crop that does not move, on the seat
    # the face is in for most of the stretch.
    whip = [(5, 6.7, [LEFT_FACE]), (6.7, 7.4, [RIGHT_FACE]), (7.4, 9, [LEFT_FACE]), (9, 9.7, [RIGHT_FACE]),
            (9.7, 11, [LEFT_FACE])]
    faces, context = samples([(0, 5, [HOST, GUEST]), *whip])
    result, segments = composed(faces, context, [5.0], 11.0)
    assert [(s["kind"], s.get("reason")) for s in result["segments"]] == [("pair", "two_people"), ("single", "cover")]
    assert (segments[1].kind, segments[1].reason) == ("cover", "person_moves_across_frame")
    w, h, x, y = cover_crop(result["video_filter"], 1)
    assert (w, h, y) == (606, 1080, 0)
    assert abs(x + w / 2 - (LEFT_FACE[0] + LEFT_FACE[2] / 2)) <= 6
    assert "gblur" not in result["video_filter"]
    # One move across the frame is a person changing seat or the camera reframing: the crop follows.
    once = plan([(0, 5, [HOST, GUEST]), (5, 8, [LEFT_FACE]), (8, 11, [RIGHT_FACE])], [5.0], 11.0)
    assert [(s["kind"], s["reason"]) for s in once["segments"]] == [("pair", "two_people"), ("single", "one_person")]
    # A person who stays put, or drifts, keeps the crop.
    drift = [(5 + i * 0.5, 5.5 + i * 0.5, [(300.0 + 30 * i, 300.0, 180.0, 230.0)]) for i in range(12)]
    steady = plan([(0, 5, [HOST, GUEST]), *drift], [5.0], 11.0)
    assert [(s["kind"], s["reason"]) for s in steady["segments"]] == [("pair", "two_people"), ("single", "one_person")]


def test_face_jumps_counts_moves_of_half_a_crop_or_more():
    segment = interview_layout.Segment(0.0, 4.0, kind="single", reason="one_person")
    xs = [100, 110, 400, 405, 120, 125, 130]
    segment.tracks = [{i * 0.5: (float(x), 200.0, 100.0, 120.0) for i, x in enumerate(xs)}]
    assert interview_layout._face_jumps(segment, 400.0) == 2        # 110 -> 400 and 405 -> 120
    assert interview_layout._face_jumps(segment, 800.0) == 0        # in a wider crop those are small moves


def test_the_hook_title_is_on_the_seam_only_when_the_two_shot_fills_most_of_its_time():
    """The title keeps one row for its four seconds. In a frame-filling crop the seam is the face or
    chest of the person in it."""
    # A close-up for three seconds, then the two-shot: the title used to lie across the face.
    late_two_shot = [(0, 3, [CLOSE]), (3, 9, [HOST, GUEST]), (9, 12, [CLOSE])]
    assert "hook_center_y" not in plan(late_two_shot, [3, 9], 12)
    # A two-shot for a second and a half, then a close-up.
    short_two_shot = [(0, 1.5, [HOST, GUEST]), (1.5, 8, [CLOSE]), (8, 12, [HOST, GUEST])]
    assert "hook_center_y" not in plan(short_two_shot, [1.5, 8], 12)
    # The two-shot fills three of the four seconds: the seam, as before.
    long_two_shot = [(0, 3, [HOST, GUEST]), (3, 8, [CLOSE]), (8, 12, [HOST, GUEST])]
    assert plan(long_two_shot, [3, 8], 12)["hook_center_y"] == interview_layout.PAIR_SEAM_Y
    # Two different two-shots across a cut still count together.
    other = ((430.0, 240.0, 140.0, 180.0), (1300.0, 230.0, 150.0, 195.0))
    two_two_shots = [(0, 2, [HOST, GUEST]), (2, 7, list(other)), (7, 12, [CLOSE])]
    assert plan(two_two_shots, [2, 7], 12)["hook_center_y"] == interview_layout.PAIR_SEAM_Y


# --- Over the shoulder at equal size: the back of a head gets no panel ---

OVER_SHOULDER = [
    (0, 5, [HOST, GUEST]),     # a real two-shot: both look towards the camera
    (5, 9, [CLOSE]),
    (9, 14, [HOST, GUEST]),    # the same two boxes, but one of them is seen from behind
]


def _facing_probe(monkeypatch, answers):
    """``probe_eye_ratios`` stand-in: ``answers(start)`` gives the two eye spacings of the stretch."""
    monkeypatch.setattr(interview_layout, "frame_changes", lambda _p: ([], None))
    monkeypatch.setattr(interview_layout, "precise_cuts", lambda _t, _f, coarse: list(coarse))
    monkeypatch.setattr(
        interview_layout, "probe_eye_ratios",
        lambda _path, start, _end, _boxes, _w, _h: answers(start),
    )


def test_a_person_seen_from_behind_gets_no_panel(monkeypatch):
    # From 9 s the left person has turned their back (eyes drawn together), the right one faces the lens.
    _facing_probe(monkeypatch, lambda start: [(0.42, 0.4), (0.44, 0.41)] if start < 9 else [(0.05, 0.03), (0.43, 0.4)])
    result = plan(OVER_SHOULDER, [5, 9], 14, input_path=Path("clip.mp4"))
    assert kinds(result) == [(0, 5, "pair"), (5, 9, "single"), (9, 14, "single")]
    assert result["segments"][2]["reason"] == "over_shoulder"
    # The crop of that stretch is on the person facing the lens (the right-hand box).
    x, w = re.search(r"trim=start=9.000,crop=(\d+):\d+:x=(\d+)", result["video_filter"]).group(2, 1)
    assert int(x) <= GUEST[0] and GUEST[0] + GUEST[2] <= int(x) + int(w)
    assert int(x) > HOST[0] + HOST[2]


def test_two_people_in_profile_keep_their_panels(monkeypatch):
    # A side-on two-shot: neither face shows both eyes apart, and neither is singled out.
    _facing_probe(monkeypatch, lambda _start: [(0.12, 0.2), (0.16, 0.22)])
    result = plan(OVER_SHOULDER, [5, 9], 14, input_path=Path("clip.mp4"))
    assert kinds(result) == [(0, 5, "pair"), (5, 9, "single"), (9, 14, "pair")]


def test_an_unreadable_probe_keeps_the_two_shot(monkeypatch):
    _facing_probe(monkeypatch, lambda _start: None)
    result = plan(OVER_SHOULDER, [5, 9], 14, input_path=Path("clip.mp4"))
    assert kinds(result) == [(0, 5, "pair"), (5, 9, "single"), (9, 14, "pair")]
    _facing_probe(monkeypatch, lambda _start: [(0.4, 0.4), None])
    assert kinds(plan(OVER_SHOULDER, [5, 9], 14, input_path=Path("clip.mp4")))[2][2] == "pair"


def test_a_speaker_in_profile_keeps_their_panel(monkeypatch):
    """Seen from the side the eyes are close together too, but the mouth is there: not the back of a head.
    Dropping that person can take the one who is speaking off screen."""
    _facing_probe(monkeypatch, lambda _start: [(0.46, 0.38), (0.07, 0.12)])
    result = plan(OVER_SHOULDER, [5, 9], 14, input_path=Path("clip.mp4"))
    assert kinds(result) == [(0, 5, "pair"), (5, 9, "single"), (9, 14, "pair")]


def test_turned_away_needs_a_clear_difference():
    from src.media.interview_layout import turned_away
    front, behind, profile, three_quarter = (0.42, 0.4), (0.05, 0.03), (0.07, 0.12), (0.2, 0.18)
    assert turned_away([front, behind]) == 1
    assert turned_away([behind, front]) == 0
    assert turned_away([front, profile]) is None        # a profile keeps its mouth: it may be the speaker
    assert turned_away([front, three_quarter]) is None
    assert turned_away([profile, behind]) is None       # nobody faces the lens
    assert turned_away([front, None]) is None and turned_away(None) is None


def test_eye_ratios_match_detections_to_the_people():
    from src.media.interview_layout import eye_ratios

    def row(box, eyes, mouth):
        x, y, w, h = box
        return [x, y, w, h, 0.93, x + w * 0.3, y + h * 0.4, x + w * (0.3 + eyes), y + h * 0.4,
                x + w * 0.5, y + h * 0.6, x + w * 0.35, y + h * 0.8, x + w * (0.35 + mouth), y + h * 0.8]

    # Probe frames at half the clip's size; the left face is frontal, the right one is the back of a head.
    half = lambda box: tuple(v / 2 for v in box)
    frames = [[row(half(HOST), 0.4, 0.36), row(half(GUEST), 0.08, 0.04)] for _ in range(4)] + [[]]
    ratios = eye_ratios(frames, [HOST, GUEST], scale=2.0)
    assert ratios[0] == pytest.approx((0.4, 0.36)) and ratios[1] == pytest.approx((0.08, 0.04))
    # Seen in one frame only: unknown.
    assert eye_ratios([[row(half(HOST), 0.4, 0.36)]], [HOST, GUEST], scale=2.0) == [None, None]


@needs_ffmpeg
def test_the_facing_probe_reads_frames_of_a_clip_and_knows_nothing_without_faces(tmp_path):
    from src.media.interview_layout import probe_eye_ratios
    clip = tmp_path / "clip.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=3",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(clip)], check=True)
    # No faces in a test pattern: both people unknown, so no layout changes because of it.
    assert probe_eye_ratios(clip, 0.5, 2.5, [HOST, GUEST], 1280, 720) == [None, None]
    assert probe_eye_ratios(clip, 2.0, 2.0, [HOST, GUEST], 1280, 720) is None


# --- A layout does not flash by inside one continuous shot ---

def _seg(start, end, kind="single"):
    from src.media.interview_layout import Segment
    return Segment(start, end, kind=kind, reason="one_person" if kind == "single" else "two_people", times=[start])


def test_a_flash_of_another_layout_with_no_camera_cut_joins_its_neighbour():
    from src.media.interview_layout import _steady
    # One continuous shot from 0 to 10 s: the two-shot drops to a single crop for 0.5 s and comes back.
    # Neither side is a cut: it goes with the longer neighbour.
    steady = _steady([_seg(0, 6, "pair"), _seg(6, 6.5), _seg(6.5, 10, "pair")], cuts=[])
    assert [(s.start, s.end, s.kind) for s in steady] == [(0, 6.5, "pair"), (6.5, 10, "pair")]
    steady = _steady([_seg(0, 2, "pair"), _seg(2, 2.6), _seg(2.6, 10, "pair")], cuts=[])
    assert [(s.start, s.end, s.kind) for s in steady] == [(0, 2, "pair"), (2, 10, "pair")]
    # How the clip opens and closes is not a flash: a short first or last stretch stays.
    edges = [_seg(0, 0.8), _seg(0.8, 9, "pair"), _seg(9, 9.6)]
    assert len(_steady(edges, cuts=[])) == 3


def test_a_flash_joins_the_neighbour_framed_the_same_way():
    from src.media.interview_layout import _steady
    # One person for a moment between a long two-shot and a single crop: it stays a single crop,
    # instead of two panels held over a place where nobody is.
    steady = _steady([_seg(0, 6, "pair"), _seg(6, 6.8), _seg(6.8, 9), _seg(9, 12, "pair")], cuts=[])
    assert [(s.start, s.end, s.kind) for s in steady] == [(0, 6, "pair"), (6, 9, "single"), (9, 12, "pair")]


def test_a_short_shot_between_two_camera_cuts_keeps_its_framing():
    from src.media.interview_layout import _steady
    # A 0.7 s reaction shot is the source's own editing.
    shots = [_seg(0, 4, "pair"), _seg(4, 4.7), _seg(4.7, 10, "pair")]
    assert [(s.start, s.end) for s in _steady(shots, cuts=[4.0, 4.7])] == [(0, 4), (4, 4.7), (4.7, 10)]
    # Cut on one side only: it joins the side it was not cut from.
    steady = _steady([_seg(0, 4, "pair"), _seg(4, 4.7), _seg(4.7, 10, "pair")], cuts=[4.05])
    assert [(s.start, s.end, s.kind) for s in steady] == [(0, 4, "pair"), (4, 10, "pair")]


def test_a_change_without_a_cut_that_lasts_is_kept():
    from src.media.interview_layout import _steady
    # Someone walks out of a two-shot and stays out: the single crop holds 3 s, so the change stands.
    shots = [_seg(0, 4, "pair"), _seg(4, 7), _seg(7, 10, "pair")]
    assert len(_steady(shots, cuts=[])) == 3


def test_a_card_too_short_to_read_between_people_fills_the_frame():
    from src.media.interview_layout import Segment, _hold_content

    def segment(start, end, kind, reason="x"):
        return Segment(start, end, kind=kind, reason=reason)

    # 2.4 s of "content" between two stretches of people: the drop to a third of the screen is what a viewer
    # would notice, not the card.
    held = _hold_content([segment(0, 10, "pair"), segment(10, 12.4, "fit", "graphic"), segment(12.4, 20, "single")])
    assert [(s.kind, s.reason) for s in held][1] == ("cover", "brief_content")
    # Long enough to read: it stays.
    kept = _hold_content([segment(0, 10, "pair"), segment(10, 16, "fit", "graphic"), segment(16, 20, "single")])
    assert [s.kind for s in kept] == ["pair", "fit", "single"]
    # A clip that is one card throughout is content, however short.
    assert [s.kind for s in _hold_content([segment(0, 2, "fit", "graphic")])] == ["fit"]
