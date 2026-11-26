"""Two webcams side by side stack cleanly for the whole clip, each pane trimmed to its picture (call_split).

All frames are synthetic: smooth random textures stand in for two webcam pictures, a moving bright blob for a
person, a black box for an empty part of a pane.
"""

import cv2
import numpy as np
import pytest

from src.media import call_split, reframing
from src.media.call_split import (
    active_rect,
    analyze_call_split,
    build_call_stack,
    find_seam,
    fit_tiles_to_panes,
    pane_crop,
    split_regions_from_call,
)

PW, PH = 320, 180          # probe frames
W, H = 1920, 1080          # source


def _texture(seed, w, h, low=50, high=200):
    rng = np.random.default_rng(seed)
    noise = rng.random((h, w)).astype(np.float32)
    smooth = cv2.GaussianBlur(noise, (0, 0), 6)
    smooth = (smooth - smooth.min()) / max(float(smooth.max() - smooth.min()), 1e-6)
    return low + smooth * (high - low)


def _person(frame, cx, cy, size, level=235):
    x0, y0 = max(0, int(cx - size / 2)), max(0, int(cy - size / 2))
    frame[y0:y0 + size, x0:x0 + size] = level


def call_frames(count=16, right_black_below=None, right_moves=True, seam_line=False):
    """A two-pane call: left webcam over the left half, right webcam over the right half (optionally only its
    upper part, black below)."""
    left_bg = _texture(1, PW // 2, PH)
    right_bg = _texture(2, PW // 2, PH, low=30, high=170)
    frames = []
    for i in range(count):
        left = left_bg.copy()
        right = right_bg.copy()
        _person(left, 80 + 3 * np.sin(i), 70, 30)
        if right_moves:
            _person(right, 80 + 24 * np.sin(i / 2), 50 + 8 * np.cos(i), 40, level=10 + 12 * (i % 3))
        frame = np.concatenate([left, right], axis=1)
        if right_black_below is not None:
            frame[right_black_below:, PW // 2:] = 0
        if seam_line:
            frame[:, PW // 2 - 1:PW // 2 + 1] = 0
        frames.append(frame)
    return np.clip(np.stack(frames), 0, 255).astype(np.uint8)


def one_camera_frames(count=16):
    """One camera: a single continuous picture with a person moving across the middle."""
    bg = _texture(3, PW, PH)
    frames = []
    for i in range(count):
        frame = bg.copy()
        _person(frame, 120 + 6 * i, 80, 34)
        frames.append(frame)
    return np.clip(np.stack(frames), 0, 255).astype(np.uint8)


def faces(left=True, right=True, count=16, right_share=1.0):
    """Face samples in source pixels: a webcam face in each half (the right one optional or intermittent)."""
    out = []
    for i in range(count):
        boxes = []
        if left:
            boxes.append((420.0, 330.0, 170.0, 200.0))
        if right and i < right_share * count:
            boxes.append((1380.0, 200.0, 150.0, 180.0))
        out.append((i / 2, boxes))
    return out


# --- the seam --------------------------------------------------------------------------------------------

def test_the_seam_between_two_webcams_is_found_in_the_middle():
    seam = find_seam(call_frames())
    assert seam is not None and abs(seam - PW // 2) <= 1
    assert abs(find_seam(call_frames(seam_line=True)) - PW // 2) <= 2


def test_one_camera_has_no_seam():
    assert find_seam(one_camera_frames()) is None


def test_a_seam_that_comes_and_goes_is_not_a_call():
    # The clip cuts away from the call to one camera for half of it.
    frames = np.concatenate([call_frames(count=8), one_camera_frames(count=8)])
    assert find_seam(frames) is None


# --- dead borders in a pane -----------------------------------------------------------------------------------

def test_a_black_box_under_an_upper_right_webcam_is_trimmed_off_the_pane():
    frames = call_frames(right_black_below=PH // 2)
    x, y, w, h = active_rect(frames[:, :, PW // 2:])
    assert y == 0 and h <= PH // 2 + 1 and w >= PW // 2 - 2
    # A pane that is all picture keeps all of it.
    assert active_rect(frames[:, :, :PW // 2]) == (0, 0, PW // 2, PH)


def test_a_flat_panel_beside_a_webcam_is_border_too():
    frames = call_frames().copy()
    frames[:, :, PW // 2 + 100:] = 128      # a flat grey chat panel on the right of the right pane
    x, _y, w, _h = active_rect(frames[:, :, PW // 2:])
    assert x == 0 and w <= 101


# --- which clips are calls --------------------------------------------------------------------------------

def test_a_masked_person_the_detector_never_finds_still_makes_a_call():
    call = analyze_call_split(call_frames(), faces(right=False), W, H)
    assert call is not None
    assert abs(call["seam"] - W // 2) <= W // PW * 2
    assert call["faces"][0] is not None and call["faces"][1] is None


def test_a_still_picture_beside_a_speaker_is_not_a_call():
    # Slides on the right half: a seam and one face, but nothing on the other side moves.
    assert analyze_call_split(call_frames(right_moves=False), faces(right=False), W, H) is None


def test_a_face_across_the_seam_means_one_camera():
    straddling = [(t, [(900.0, 300.0, 200.0, 220.0)]) for t, _ in faces()]
    assert analyze_call_split(call_frames(), straddling, W, H) is None


def test_two_faces_in_a_pane_are_a_gallery_not_a_call():
    crowded = [(t, [(100.0, 300.0, 150.0, 170.0), (600.0, 300.0, 150.0, 170.0), (1380.0, 200.0, 150.0, 180.0)])
               for t, _ in faces()]
    assert analyze_call_split(call_frames(), crowded, W, H) is None


def test_one_camera_with_two_people_is_not_a_call():
    assert analyze_call_split(one_camera_frames(), faces(), W, H) is None


# --- the composition -----------------------------------------------------------------------------------

def _inside(crop, rect):
    x, y, w, h = crop
    rx, ry, rw, rh = rect
    return x >= rx and y >= ry and x + w <= rx + rw and y + h <= ry + rh


def test_the_stack_shows_each_pane_and_none_of_the_black_box():
    frames = call_frames(right_black_below=PH // 2)
    call = analyze_call_split(frames, faces(right_share=0.3), W, H)
    assert call is not None
    left_pane, right_pane = call["panes"]
    # The right pane ends where its webcam does: the black lower half is not part of it.
    assert right_pane[1] + right_pane[3] <= H // 2 + H // PH * 2
    layout = build_call_stack(call)
    assert layout["kind"] == "stack" and layout["mode"] == "complex"
    top, bottom = layout["crops"]
    assert _inside(top, left_pane) and _inside(bottom, right_pane)
    assert top[0] + top[2] <= call["seam"] + 2 and bottom[0] >= call["seam"] - 2
    # The panel's shape: no squashing on the way to 1080x960.
    for crop in (top, bottom):
        assert crop[2] / crop[3] == pytest.approx(1080 / 960, rel=0.03)
        assert all(value % 2 == 0 for value in crop)
    graph = layout["video_filter"]
    assert graph.startswith("[0:v]split=2[cs_l][cs_r];") and graph.endswith("vstack=inputs=2[vout]")
    assert graph.count("scale=1080:960") == 2
    assert layout["caption_y"] == 0.5


def test_a_pane_crop_frames_the_face_and_stays_in_the_pane():
    pane = (960, 0, 960, 540)
    face = (1380.0, 200.0, 150.0, 180.0)
    x, y, w, h = pane_crop(pane, face)
    assert _inside((x, y, w, h), pane)
    assert x <= face[0] and face[0] + face[2] <= x + w and y <= face[1] and face[1] + face[3] <= y + h
    # Without a face, the middle of the pane, as large as the panel's shape allows.
    x, y, w, h = pane_crop(pane, None)
    assert _inside((x, y, w, h), pane) and h == 540 and w == pytest.approx(540 * 1080 / 960, abs=2)


# --- in the vertical plan ----------------------------------------------------------------------------------

def test_a_call_stacks_for_the_whole_clip_before_the_interview_planner_can_flip_it(monkeypatch, tmp_path):
    # One person masked for most of the clip, then not: the interview planner switched the stack to a single
    # crop and back. The call split decides first, from the seam, for the whole clip.
    samples = faces(right_share=0.4, count=30)
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: 15.0)
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_a, **_k: ([], [], samples, []))
    monkeypatch.setattr(call_split, "read_probe_frames", lambda *_a: call_frames(right_black_below=PH // 2))

    def interview(*_a, **_k):
        raise AssertionError("the interview planner must not run for a side-by-side call")

    monkeypatch.setattr("src.media.interview_layout.plan_interview_layout", interview)
    monkeypatch.setattr("src.media.letterbox.detect_letterbox", lambda *_a: None)
    described = reframing.describe_vertical_layout(tmp_path / "clip.mp4", W, H)
    assert described["kind"] == "stack" and described["mode"] == "complex"
    assert described["video_filter"].count("crop=") == 2 and "concat" not in described["video_filter"]
    assert "gblur" not in described["video_filter"]           # no blurred letterbox anywhere
    assert described["caption_y"] == 0.5


def test_vertical_split_tiles_leave_the_black_box_out(monkeypatch):
    frames = call_frames(right_black_below=PH // 2)
    # Face tiles as cluster_two_face_regions makes them: the right one reaches into the black box.
    regions = {
        "left": {"center_x": 500, "center_y": 430, "tile_x": 200, "tile_y": 110, "tile_w": 600, "tile_h": 520},
        "right": {"center_x": 1450, "center_y": 290, "tile_x": 1150, "tile_y": 70, "tile_w": 600, "tile_h": 700},
    }
    fitted = fit_tiles_to_panes(regions, frames, W, H)
    assert fitted["left"] == regions["left"]                 # already inside its pane: untouched
    right = fitted["right"]
    assert right["tile_y"] + right["tile_h"] <= H // 2 + H // PH * 2
    assert right["tile_x"] >= W // 2 - 12
    assert right["tile_x"] <= 1450 <= right["tile_x"] + right["tile_w"]
    assert right["tile_w"] / right["tile_h"] == pytest.approx(1080 / 960, rel=0.03)


def test_vertical_split_with_one_face_found_uses_the_call_panes(monkeypatch, tmp_path):
    monkeypatch.setattr(reframing, "ffprobe_video_size", lambda _p: (W, H))
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _p: 8.0)
    monkeypatch.setattr(reframing, "count_scene_cuts", lambda _p: 0)
    # Only the left person is ever found: two clusters of one face are too close to be two people.
    monkeypatch.setattr(reframing, "detect_faces_in_clip", lambda *_a: [(505, 430, 34000, 0.9)] * 8)
    monkeypatch.setattr(call_split, "read_probe_frames", lambda *_a: call_frames(right_black_below=PH // 2))
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_a, **_k: ([], [], faces(right=False), []))
    plan = reframing.detect_speaker_reframe_plan(tmp_path / "clip.mp4", "vertical_split")
    assert plan is not None and plan["mode"] == "split"
    left, right = plan["regions"]["left"], plan["regions"]["right"]
    assert left["tile_x"] + left["tile_w"] <= W // 2 + 12 and right["tile_x"] >= W // 2 - 12
    assert right["tile_y"] + right["tile_h"] <= H // 2 + H // PH * 2


def test_regions_from_a_call_are_shaped_like_face_tiles():
    call = {"seam": 960, "panes": [(0, 0, 960, 1080), (960, 0, 960, 540)],
            "faces": [(420.0, 330.0, 170.0, 200.0), None]}
    regions = split_regions_from_call(call)
    for key in ("left", "right"):
        assert {"center_x", "center_y", "tile_x", "tile_y", "tile_w", "tile_h", "roi_x", "roi_w"} <= set(regions[key])
    assert regions["right"]["tile_y"] + regions["right"]["tile_h"] <= 540
