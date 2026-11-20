"""Gaming mode: detection maths on synthetic frames, and the layout / filter builder."""

import re
import shutil
import subprocess
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from src.media import gaming_layout as gl
from src.media.gaming_layout import GamingProfile, Rect

W, H = 640, 360
SRC_W, SRC_H = 1920, 1080
CAM = (460, 20, 180, 102)          # x, y, w, h on the 640x360 frame, flush with its right edge
CAM_FACE = (525.0, 40.0, 40.0, 50.0)


def world(rng: np.random.Generator) -> np.ndarray:
    """A frame of moving footage: shapes in new places every sample, like a camera flying through a level."""
    frame = np.full((H, W, 3), rng.integers(0, 255, 3), dtype=np.uint8)
    for _ in range(60):
        x, y = int(rng.integers(-60, W)), int(rng.integers(-60, H))
        w, h = int(rng.integers(30, 220)), int(rng.integers(30, 160))
        cv2.rectangle(frame, (x, y), (x + w, y + h), [int(v) for v in rng.integers(0, 255, 3)], -1)
    return frame


def draw_hud(frame: np.ndarray) -> None:
    cv2.rectangle(frame, (10, 10), (110, 110), (20, 20, 20), -1)            # minimap, top left
    for step in range(20, 110, 15):
        cv2.line(frame, (10, step), (110, step), (230, 230, 230), 1)
        cv2.line(frame, (step, 10), (step, 110), (230, 230, 230), 1)
    cv2.rectangle(frame, (12, 322), (170, 348), (15, 15, 15), -1)           # health, bottom left
    cv2.putText(frame, "HP 100 / 100", (16, 342), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    cv2.rectangle(frame, (500, 310), (628, 350), (15, 15, 15), -1)          # ammo, bottom right
    cv2.putText(frame, "30 / 120", (506, 340), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.rectangle(frame, (250, 6), (390, 30), (15, 15, 15), -1)             # score, top centre
    cv2.putText(frame, "12 : 09", (275, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    cv2.line(frame, (314, 180), (326, 180), (255, 255, 255), 1)             # crosshair
    cv2.line(frame, (320, 174), (320, 186), (255, 255, 255), 1)


def draw_cam(frame: np.ndarray, i: int = 0, cam=CAM, still: bool = False) -> None:
    x, y, w, h = cam
    frame[y:y + h, x:x + w] = (90, 70, 60)
    sway = 0 if still else (i * 7) % 23 - 11                                # a person moves; a logo does not
    cv2.circle(frame, (x + w // 2 + sway, y + 45), 24, (150, 170, 210), -1)  # a head; the face boxes are supplied
    cv2.rectangle(frame, (x + w // 2 - 30 + sway, y + 70), (x + w // 2 + 30 + sway, y + h - 2), (40, 40, 160), -1)
    cv2.rectangle(frame, (x, y), (x + w, y + h), (255, 255, 255), 2)


def clip(count=48, hud=True, cam=False, moving=True, hud_until=None, seed=7, still_cam=False):
    rng = np.random.default_rng(seed)
    still = world(rng)
    frames = []
    for i in range(count):
        frame = world(rng) if moving else still.copy()
        if hud and (hud_until is None or i < hud_until):
            draw_hud(frame)
            if cam:
                draw_cam(frame, i, still=still_cam)
        frames.append(frame)
    return np.stack(frames), [i / 3 for i in range(count)]


def classify(frames, times, faces=None):
    faces = faces if faces is not None else [[] for _ in frames]
    return gl.classify_gaming(gl.measure_frames(frames, times), faces, SRC_W, SRC_H, len(frames) / 3)


# ---------------------------------------------------------------------------
# Switch
# ---------------------------------------------------------------------------

def test_switch_is_off_by_default_and_ignores_unknown_values(monkeypatch):
    monkeypatch.delenv("GAMING_LAYOUT", raising=False)
    assert gl.gaming_layout_mode() == "off"
    monkeypatch.setenv("GAMING_LAYOUT", "AUTO ")
    assert gl.gaming_layout_mode() == "auto"
    monkeypatch.setenv("GAMING_LAYOUT", "yes")
    assert gl.gaming_layout_mode() == "off"


def test_off_never_analyses_the_clip(monkeypatch, tmp_path):
    monkeypatch.delenv("GAMING_LAYOUT", raising=False)
    with patch.object(gl, "analyze_gaming_clip", side_effect=AssertionError("analysed while off")):
        assert gl.choose_gaming_layout(tmp_path / "clip.mp4", SRC_W, SRC_H, 20.0) is None


def test_a_failed_analysis_keeps_the_regular_layout(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMING_LAYOUT", "auto")
    with patch.object(gl, "analyze_gaming_clip", side_effect=RuntimeError("decode failed")):
        assert gl.choose_gaming_layout(tmp_path / "clip.mp4", SRC_W, SRC_H, 20.0) is None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def test_gameplay_with_a_hud_is_recognised_without_a_facecam():
    profile = classify(*clip())
    assert profile.is_gaming and profile.reason == "hud"
    assert profile.facecam is None and profile.crosshair
    assert profile.moving_fraction > 0.9 and profile.valid_fraction == 1.0
    assert len(profile.hud) == 4
    minimap = profile.hud[0]
    # 10..110 px on the 640 frame is 30..330 px in the 1080p source, plus a little padding.
    assert abs(minimap.x - 30) <= 24 and abs(minimap.y - 30) <= 24
    assert abs(minimap.x2 - 330) <= 24 and abs(minimap.y2 - 330) <= 24


def test_facecam_rectangle_is_found_around_a_fixed_face():
    frames, times = clip(cam=True)
    faces = [[(CAM_FACE[0] + (i % 3), CAM_FACE[1], CAM_FACE[2], CAM_FACE[3])] for i in range(len(frames))]
    profile = classify(frames, times, faces)
    assert profile.is_gaming and profile.reason == "facecam_hud"
    cam = profile.facecam
    assert cam is not None
    x, y, w, h = (v * 3 for v in CAM)
    assert abs(cam.x - x) <= 15 and abs(cam.y - y) <= 15
    assert abs(cam.x2 - (x + w)) <= 18 and abs(cam.y2 - (y + h)) <= 15
    # The camera is not also reported as a HUD element.
    assert all(r.x2 <= cam.x or r.x >= cam.x2 or r.y2 <= cam.y or r.y >= cam.y2 for r in profile.hud)


def test_a_face_without_a_camera_rectangle_is_not_a_facecam():
    frames, times = clip(cam=False, hud=False)
    faces = [[CAM_FACE] for _ in frames]
    profile = classify(frames, times, faces)
    assert profile.facecam is None and not profile.is_gaming


def test_a_small_still_head_over_a_hud_is_a_keyed_out_streamer():
    frames, times = clip(cam=False)
    face = (60.0, 250.0, 34.0, 42.0)
    profile = classify(frames, times, [[face] for _ in frames])
    assert profile.is_gaming and profile.virtual_camera
    cam = profile.facecam
    # Head and shoulders around the face, in source pixels.
    assert cam.x <= face[0] * 3 and cam.x2 >= (face[0] + face[2]) * 3
    assert cam.y < face[1] * 3 and cam.y2 > (face[1] + face[3]) * 3
    layout = gl.build_gaming_layout(profile, 16.0)
    assert layout["gaming"]["facecam_panel"] is not None


def test_a_logo_in_a_frame_is_not_a_camera():
    frames, times = clip(cam=True, still_cam=True)
    profile = classify(frames, times, [[CAM_FACE] for _ in frames])
    assert profile.facecam is None or profile.virtual_camera


def test_a_bigger_still_face_beside_the_inset_is_a_reaction_video():
    frames, times = clip(cam=True)
    host = (120.0, 150.0, 60.0, 75.0)
    profile = classify(frames, times, [[CAM_FACE, host] for _ in frames])
    assert not profile.is_gaming and profile.reason == "people_on_screen"


def test_a_seldom_detected_face_still_marks_a_camera_that_is_always_there():
    frames, times = clip(cam=True)
    faces = [[CAM_FACE] if i % 5 == 0 else [] for i in range(len(frames))]
    profile = classify(frames, times, faces)
    assert profile.is_gaming and profile.facecam is not None and not profile.virtual_camera


def test_a_wandering_face_is_not_a_fixed_camera():
    faces = [[(100.0 + i * 9, 60.0 + (i % 5) * 20, 40.0, 50.0)] for i in range(40)]
    assert gl.find_fixed_face(faces, W, H) is None
    fixed = gl.find_fixed_face([[CAM_FACE]] * 30 + [[]] * 10, W, H)
    assert fixed is not None and fixed[0] == CAM_FACE and fixed[1] == 0.75


def test_static_screen_recording_with_overlays_is_not_gameplay():
    profile = classify(*clip(moving=False))
    assert not profile.is_gaming and profile.reason == "static_picture"


def test_moving_footage_without_overlays_is_not_gameplay():
    profile = classify(*clip(hud=False))
    assert not profile.is_gaming and profile.reason == "no_hud"
    assert profile.hud == []


def test_two_cameras_that_take_turns_are_two_tracks():
    first, second = (505.0, 40.0, 40.0, 50.0), (60.0, 250.0, 36.0, 44.0)
    faces = [[first] for _ in range(20)] + [[second] for _ in range(14)] + [[first] for _ in range(6)]
    faces[25] = [second, first]                       # a stray detection where the first camera used to be
    tracks = gl.fixed_face_tracks(faces, W, H)
    assert [t.box for t in tracks] == [first, second]
    assert gl.camera_timeline(tracks, len(faces)) == [(0, 0, 19), (1, 20, 33), (0, 34, 39)]
    # Samples without a face go to the camera around them, or are shared between two.
    gaps = [[first]] * 6 + [[]] * 4 + [[first]] * 6 + [[]] * 4 + [[second]] * 6
    assert gl.camera_timeline(gl.fixed_face_tracks(gaps, W, H), len(gaps)) == [(0, 0, 17), (1, 18, 25)]


def test_cameras_close_together_in_different_segments_stay_apart():
    near, far = (130.0, 241.0, 42.0, 50.0), (77.0, 270.0, 34.0, 44.0)
    faces = [[near] for _ in range(12)] + [[far] for _ in range(12)]
    assert sorted(len(t.samples) for t in gl.fixed_face_tracks(faces, W, H)) == [12, 12]


def test_fallback_ranges_stay_inside_a_cameras_own_spans():
    times = [i / 2 for i in range(40)]
    valid = np.ones(40, bool)
    valid[4:12] = False                               # 2.0 s to 6.0 s: outside the spans from 5.0 s on
    valid[30:40] = False
    ranges = gl.fallback_ranges_within(valid, times, [(0.0, 5.0), (14.0, 20.0)])
    assert ranges == [(1.75, 5.0), (14.75, 20.0)]


def test_people_on_screen_rule_out_gameplay():
    frames, times = clip()
    faces = [[(250.0, 120.0, 90.0, 110.0)] for _ in frames]  # a vlog with challenge graphics in the corners
    profile = classify(frames, times, faces)
    assert not profile.is_gaming and profile.reason == "people_on_screen"


def test_two_corner_graphics_are_not_enough_without_a_facecam():
    rng = np.random.default_rng(3)
    frames = []
    for _ in range(48):
        frame = world(rng)
        cv2.putText(frame, "$", (20, 40), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
        cv2.putText(frame, "BALANCE 2400", (430, 345), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        frames.append(frame)
    profile = classify(np.stack(frames), [i / 3 for i in range(48)])
    assert not profile.is_gaming and profile.reason == "no_hud"


def test_stream_scene_change_becomes_a_fallback_range():
    frames, times = clip(count=60, hud_until=48)   # the HUD is gone for the last 4 of 20 seconds
    profile = classify(frames, times)
    assert profile.is_gaming
    assert len(profile.fallback_ranges) == 1
    start, end = profile.fallback_ranges[0]
    assert abs(start - 16.0) <= 0.5 and end == pytest.approx(20.0)


def test_clip_that_mostly_shows_something_else_is_left_alone():
    # The overlays are on screen for 65% of the clip: enough to persist, too little to build the clip around.
    frames, times = clip(count=60, hud_until=39)
    profile = classify(frames, times)
    assert not profile.is_gaming and profile.reason == "layout_changes"


def test_short_clips_are_not_classified():
    profile = classify(*clip(count=8))
    assert not profile.is_gaming and profile.reason == "too_short"


def test_fallback_ranges_ignore_blips_and_reach_the_clip_ends():
    times = [i * 0.5 for i in range(20)]
    valid = np.ones(20, bool)
    valid[0:5] = False      # 0 .. 2.25 s
    valid[9] = False        # a single sample: under the minimum
    valid[16:] = False      # 7.75 s .. end
    assert gl.fallback_ranges(valid, times, 10.0) == [(0.0, 2.25), (7.75, 10.0)]
    assert gl.fallback_ranges(np.ones(20, bool), times, 10.0) == []


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def profile_with(facecam=None, face=None, hud=(), fallback=()):
    return GamingProfile(
        width=SRC_W, height=SRC_H, is_gaming=True, reason="test", facecam=facecam, face=face,
        hud=list(hud), fallback_ranges=list(fallback),
    )


MINIMAP = Rect(20, 20, 320, 320)
KILLFEED = Rect(1500, 30, 400, 120)
HEALTH = Rect(30, 980, 420, 80)
AMMO = Rect(1560, 960, 330, 100)
SCORE = Rect(760, 10, 400, 70)
CHAT = Rect(20, 480, 400, 160)


def test_facecam_panel_shows_the_whole_camera_at_a_third_of_the_frame():
    crop, panel_h = gl.facecam_crop(Rect(1320, 60, 540, 304), (1540.0, 120.0, 110.0, 140.0))
    assert panel_h == 608 and crop.as_list() == [1320, 60, 540, 304]
    # A 4:3 camera is cropped around the face instead of taking half the frame.
    crop, panel_h = gl.facecam_crop(Rect(0, 0, 480, 360), (200.0, 90.0, 90.0, 110.0))
    assert panel_h == gl.FACECAM_PANEL_MAX
    assert crop.w == 480 and crop.h < 360 and crop.y2 <= 360
    assert crop.w / crop.h == pytest.approx(1080 / panel_h, rel=0.02)
    # A banner-shaped camera is cropped sideways, centred on the face.
    crop, panel_h = gl.facecam_crop(Rect(100, 0, 900, 300), (700.0, 80.0, 90.0, 110.0))
    assert panel_h == gl.FACECAM_PANEL_MIN and crop.h == 300 and crop.w < 900
    assert crop.x <= 745 - crop.w / 2 + 2 and crop.x >= 100


def test_gameplay_crop_is_the_screen_centre_and_avoids_the_camera():
    crop = gl.gameplay_crop(SRC_W, SRC_H, 1312, None)
    assert crop.h == 1080 and crop.w % 2 == 0 and crop.w == pytest.approx(1080 * 1080 / 1312, abs=2)
    assert crop.x == pytest.approx((SRC_W - crop.w) / 2, abs=2)
    # A camera that reaches into the centre crop pushes it sideways, by a tenth of the width at most.
    left_cam = Rect(0, 300, 620, 350)
    shifted = gl.gameplay_crop(SRC_W, SRC_H, 1312, left_cam)
    assert crop.x < shifted.x <= crop.x + SRC_W * gl.CROP_SHIFT_MAX and shifted.x >= left_cam.x2
    right_cam = Rect(1350, 300, 570, 350)
    shifted = gl.gameplay_crop(SRC_W, SRC_H, 1312, right_cam)
    assert crop.x - SRC_W * gl.CROP_SHIFT_MAX <= shifted.x < crop.x
    # A corner camera outside the crop changes nothing.
    assert gl.gameplay_crop(SRC_W, SRC_H, 1312, Rect(0, 0, 400, 225)) == crop


def test_hud_cutouts_slide_inwards_at_their_own_height():
    crop = gl.gameplay_crop(SRC_W, SRC_H, 1312, None)
    cutouts, occupied = gl.place_hud([MINIMAP, KILLFEED, HEALTH, AMMO, SCORE, CHAT], crop, SRC_W, 608, 1312)
    by_source = {tuple(item["source"].as_list()): item for item in cutouts}
    # The centred score is inside the crop already; the mid-height chat is left out.
    assert tuple(SCORE.as_list()) not in by_source and tuple(CHAT.as_list()) not in by_source
    assert set(by_source) == {tuple(r.as_list()) for r in (MINIMAP, KILLFEED, HEALTH, AMMO)}
    minimap, feed = by_source[tuple(MINIMAP.as_list())], by_source[tuple(KILLFEED.as_list())]
    health, ammo = by_source[tuple(HEALTH.as_list())], by_source[tuple(AMMO.as_list())]
    assert minimap["x"] < 60 and feed["x"] + feed["w"] > 1020          # left stays left, right stays right
    assert minimap["y"] < 608 + 1312 * 0.2 and health["y"] > 608 + 1312 * 0.8 and ammo["y"] > 608 + 1312 * 0.8
    for item in cutouts:
        assert 0 <= item["x"] and item["x"] + item["w"] <= 1080
        assert 608 <= item["y"] and item["y"] + item["h"] <= 1920
        assert item["w"] % 2 == 0 and item["h"] % 2 == 0
    for i, a in enumerate(cutouts):
        for b in cutouts[i + 1:]:
            assert gl._overlap((a["x"], a["y"], a["w"], a["h"]), (b["x"], b["y"], b["w"], b["h"])) == 0
    # Captions avoid cut-outs and the score that stayed in the picture.
    assert len(occupied) == 5


def test_cutouts_that_cannot_share_a_row_shrink_then_give_way():
    crop = gl.gameplay_crop(SRC_W, SRC_H, 1312, None)
    wide_left, wide_right = Rect(0, 950, 900, 110), Rect(1100, 950, 820, 110)
    cutouts, _ = gl.place_hud([wide_left, wide_right], crop, SRC_W, 608, 1312)
    assert len(cutouts) == 1 and cutouts[0]["source"] == wide_left


def test_nothing_is_placed_on_the_middle_of_the_gameplay_panel():
    crop = gl.gameplay_crop(SRC_W, SRC_H, 1920, None)
    # A wide strip at 70% height, mostly outside a 9:16 crop: its cut-out would cross the character.
    strip = Rect(200, 560, 1500, 60)
    cutouts, _ = gl.place_hud([strip], crop, SRC_W, 0, 1920)
    assert cutouts == []


def test_captions_take_the_first_free_row_below_the_action():
    assert gl.choose_caption_y([], 608, 1312) == gl.CAPTION_CANDIDATES[0]
    blocked = [(0, 0.80 * 1920, 1080, 0.10 * 1920)]          # a HUD bar across 0.80 .. 0.90
    y = gl.choose_caption_y(blocked, 608, 1312)
    assert y + gl.CAPTION_BAND / 2 <= 0.80
    assert (y - gl.CAPTION_BAND / 2) * 1920 >= 608


def test_layout_with_facecam_reports_panels_seam_and_caption_row():
    cam = Rect(1320, 60, 540, 304)
    layout = gl.build_gaming_layout(
        profile_with(cam, (1540.0, 120.0, 110.0, 140.0), [MINIMAP, HEALTH, AMMO, SCORE]), 20.0)
    assert layout["kind"] == "gaming" and layout["mode"] == "complex"
    report = layout["gaming"]
    assert report["facecam_panel"] == [0, 0, 1080, 608]
    assert report["gameplay_panel"] == [0, 608, 1080, 1312]
    assert layout["hook_center_y"] == pytest.approx(608 / 1920)
    assert layout["caption_y"] in gl.CAPTION_CANDIDATES
    assert (layout["caption_y"] - gl.CAPTION_BAND / 2) * 1920 > 608 + 1312 * 0.5   # below the crosshair
    assert len(report["hud"]) == 3 and report["fallback_ranges"] == []
    graph = layout["video_filter"]
    assert graph.startswith("[0:v]split=5[gm_game][gm_cam][gm_h0][gm_h1][gm_h2];")
    assert graph.endswith("[gm_top][gm_p3]vstack=inputs=2[vout]")
    assert "crop=540:304:1320:60,scale=1080:608" in graph
    for w, h, x, y in re.findall(r"crop=(\d+):(\d+):(\d+):(\d+)", graph):
        w, h, x, y = int(w), int(h), int(x), int(y)
        assert w % 2 == 0 and h % 2 == 0 and x + w <= SRC_W and y + h <= SRC_H


def test_layout_without_facecam_fills_the_frame_and_leaves_the_hook_alone():
    layout = gl.build_gaming_layout(profile_with(hud=[MINIMAP, HEALTH, AMMO]), 20.0)
    assert layout["hook_center_y"] is None
    assert layout["gaming"]["facecam"] is None
    assert layout["gaming"]["gameplay_panel"] == [0, 0, 1080, 1920]
    assert layout["gaming"]["gameplay"][2] == 608
    assert "vstack" not in layout["video_filter"] and layout["video_filter"].endswith("[vout]")


def test_fallback_spans_show_the_whole_frame_and_reach_the_last_frame():
    layout = gl.build_gaming_layout(profile_with(hud=[MINIMAP, HEALTH, AMMO], fallback=[(4.0, 7.5), (16.0, 20.0)]), 20.0)
    graph = layout["video_filter"]
    assert "enable='between(t\\,4.000\\,7.500)+between(t\\,16.000\\,1000000.000)'" in graph
    assert graph.count("[vout]") == 1 and graph.endswith("[vout]")
    assert layout["gaming"]["fallback_ranges"] == [[4.0, 7.5], [16.0, 20.0]]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
@pytest.mark.parametrize("with_cam,fallback", [(True, []), (False, [(0.4, 0.8)])])
def test_filter_graph_renders_a_1080x1920_frame(tmp_path, with_cam, fallback):
    cam = Rect(1320, 60, 540, 304) if with_cam else None
    layout = gl.build_gaming_layout(profile_with(cam, None, [MINIMAP, HEALTH, AMMO, KILLFEED], fallback), 1.0)
    out = tmp_path / "frame.mp4"
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={SRC_W}x{SRC_H}:rate=10:duration=1",
         "-filter_complex", layout["video_filter"], "-map", "[vout]", "-c:v", "libx264", "-preset", "ultrafast",
         str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_frames",
         "-of", "csv=p=0", str(out)], capture_output=True, text=True,
    ).stdout.strip()
    assert probe == "1080,1920,10"


def camera_profile(cam, active, fallback=(), hud=()):
    profile = profile_with(cam, None, hud, fallback)
    profile.active = list(active)
    return profile


def test_cameras_that_take_turns_each_get_their_layout_over_the_full_frame():
    left = camera_profile(Rect(8, 470, 370, 224), [(0.0, 6.0), (14.0, 20.0)], fallback=[(2.0, 4.0)], hud=[MINIMAP])
    right = camera_profile(Rect(1320, 60, 540, 304), [(6.0, 14.0)], hud=[HEALTH, AMMO])
    layout = gl.build_camera_layouts([left, right], 20.0)
    graph = layout["video_filter"]
    assert graph.count("[vout]") == 1 and graph.endswith("[vout]")
    assert "[gm_alt][g0_out]overlay=0:0:enable='between(t\\,0.000\\,2.000)+between(t\\,4.000\\,6.000)+between(t\\,14.000\\,1000000.000)'[gm_m0]" in graph
    assert "[gm_m0][g1_out]overlay=0:0:enable='between(t\\,6.000\\,14.000)'[vout]" in graph
    report = layout["gaming"]
    assert [s["between"] for s in report["segments"]] == [[[0.0, 2.0], [4.0, 6.0], [14.0, 20.0]], [[6.0, 14.0]]]
    assert report["fallback_ranges"] == [[2.0, 4.0]]
    # The longest camera sets the seam for the hook title.
    assert layout["hook_center_y"] == pytest.approx(report["segments"][0]["facecam_panel"][3] / 1920)
    assert layout["caption_y"] in gl.CAPTION_CANDIDATES


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_camera_layouts_render_a_1080x1920_frame(tmp_path):
    left = camera_profile(Rect(8, 470, 370, 224), [(0.0, 0.5)], hud=[MINIMAP])
    right = camera_profile(Rect(1320, 60, 540, 304), [(0.5, 1.0)], fallback=[(0.8, 1.0)], hud=[HEALTH, AMMO, KILLFEED])
    layout = gl.build_camera_layouts([left, right], 1.0)
    out = tmp_path / "frame.mp4"
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={SRC_W}x{SRC_H}:rate=10:duration=1",
         "-filter_complex", layout["video_filter"], "-map", "[vout]", "-c:v", "libx264", "-preset", "ultrafast",
         str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_frames",
         "-of", "csv=p=0", str(out)], capture_output=True, text=True,
    ).stdout.strip()
    assert probe == "1080,1920,10"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_compilation_of_two_streamers_is_analysed_per_camera(monkeypatch, tmp_path):
    monkeypatch.setenv("GAMING_LAYOUT", "auto")
    second_cam, second_face = (20, 230, 170, 110), (85.0, 250.0, 40.0, 50.0)
    rng = np.random.default_rng(11)
    frames = []
    for i in range(96):                                # 12 s at 8 fps: one streamer, a hard cut, another
        frame = world(rng)
        draw_hud(frame)
        draw_cam(frame, i) if i < 48 else draw_cam(frame, i, cam=second_cam)
        frames.append(cv2.resize(frame, (SRC_W, SRC_H), interpolation=cv2.INTER_NEAREST))
    source = tmp_path / "compilation.mp4"
    encode = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{SRC_W}x{SRC_H}", "-r", "8",
         "-i", "-", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "12", "-pix_fmt", "yuv420p", str(source)],
        input=np.stack(frames).tobytes(), capture_output=True,
    )
    assert encode.returncode == 0, encode.stderr

    def faces_of(sampled):
        scale = sampled.shape[2] / W
        found = []
        for frame in sampled:
            x, y, w, h = (int(v * scale) for v in CAM)
            first = abs(int(frame[y + 4, x + 8, 0]) - 90) < 25 and abs(int(frame[y + 4, x + 8, 2]) - 60) < 25
            face = CAM_FACE if first else second_face
            found.append([tuple(v * scale for v in face)])
        return found

    monkeypatch.setattr(gl, "detect_faces", faces_of)
    profiles = gl.analyze_gaming_clip(source, SRC_W, SRC_H, 12.0)
    assert len(profiles) == 2 and all(p.is_gaming and p.facecam is not None for p in profiles)
    first, second = profiles
    assert first.active[0][0] == 0.0 and second.active[-1][1] == 12.0
    assert first.active[-1][1] == pytest.approx(6.0, abs=0.15) == pytest.approx(second.active[0][0], abs=0.15)
    assert first.facecam.x > SRC_W / 2 and second.facecam.x < SRC_W / 2
    layout = gl.choose_gaming_layout(source, SRC_W, SRC_H, 12.0)
    assert layout is not None and len(layout["gaming"]["segments"]) == 2


# ---------------------------------------------------------------------------
# Integration point
# ---------------------------------------------------------------------------

def test_describe_vertical_layout_returns_the_gaming_plan(monkeypatch, tmp_path):
    from src.media import reframing

    plan = gl.build_gaming_layout(profile_with(Rect(1320, 60, 540, 304), None, [MINIMAP, HEALTH, AMMO]), 20.0)
    monkeypatch.setattr(reframing, "ffprobe_duration", lambda _path: 20.0)
    monkeypatch.setattr(gl, "choose_gaming_layout", lambda *_args: plan)
    with patch.object(reframing, "analyze_vertical_clip", side_effect=AssertionError("regular analysis ran")):
        report = {}
        video_filter, mode, caption_y = reframing.build_vertical_filter_plan(tmp_path / "c.mp4", SRC_W, SRC_H, report=report)
    assert (video_filter, mode, caption_y) == (plan["video_filter"], "complex", plan["caption_y"])
    assert report["kind"] == "gaming" and report["hook_center_y"] == plan["hook_center_y"]
    assert report["gaming"] == plan["gaming"]
