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
    for eye in (-9, 9):                                                     # with eyes: a face has features
        cv2.circle(frame, (x + w // 2 + sway + eye, y + 40), 4, (30, 30, 30), -1)
    cv2.line(frame, (x + w // 2 + sway - 8, y + 56), (x + w // 2 + sway + 8, y + 56), (40, 40, 90), 2)
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
    assert profile.facecam is None and not profile.virtual_camera


def test_a_face_that_never_moves_is_a_picture_not_a_keyed_out_streamer():
    # A channel logo or a drawn avatar with a face in it, over gameplay with a HUD and no camera.
    frames, times = clip(cam=False)
    face = (60.0, 250.0, 34.0, 42.0)
    for frame in frames:
        cv2.circle(frame, (77, 271), 26, (150, 170, 210), -1)
        cv2.circle(frame, (70, 264), 4, (20, 20, 20), -1)
        cv2.circle(frame, (84, 264), 4, (20, 20, 20), -1)
    profile = classify(frames, times, [[face] for _ in frames])
    assert profile.facecam is None and not profile.virtual_camera
    assert profile.is_gaming and profile.reason == "hud"            # still gameplay: the game-only layout
    # The picture is lifted out as a sticker: its box holds the drawn head and not much more.
    mark = profile.sticker
    assert mark is not None and mark.x <= 53 * 3 and mark.x2 >= 101 * 3 and mark.w <= 80 * 3 and mark.h <= 80 * 3
    cols, rows, cells = profile.sticker_mask
    covered = sum(w * h for _, _, w, h in cells) / (cols * rows)
    assert 0.55 <= covered <= 0.95                                  # a disc in its box, not the box
    assert all(0 <= x and x + w <= cols and 0 <= y and y + h <= rows for x, y, w, h in cells)
    assert not any(r.x < mark.x2 and mark.x < r.x2 and r.y < mark.y2 and mark.y < r.y2 for r in profile.hud)


def sticker_profile(mark=Rect(1700, 700, 200, 260)):
    return GamingProfile(
        width=SRC_W, height=SRC_H, is_gaming=True, reason="hud",
        hud=[Rect(30, 30, 300, 300)], sticker=mark, sticker_mask=(4, 5, [(1, 0, 2, 5), (0, 2, 4, 1)]),
    )


def test_a_sticker_goes_to_the_top_once_the_hook_title_has_gone():
    layout = gl.build_gaming_layout(sticker_profile(), 20.0)
    graph = layout["video_filter"]
    assert "alphamerge" in graph and graph.count("color=white") == 2
    x, y = (int(v) for v in re.search(r"\[gm_sa\]overlay=(\d+):(\d+)", graph).groups())
    assert x > 1080 / 2 and y < 1920 * 0.1                          # top, on the side it came from
    assert f"enable='gte(t,{gl.STICKER_FROM_SECONDS:g})'" in graph
    # A picture on the left goes to the left, unless a HUD cut-out already sits there.
    left = sticker_profile(Rect(20, 700, 200, 260))
    left.hud = []
    assert int(re.search(r"\[gm_sa\]overlay=(\d+):", gl.build_gaming_layout(left, 20.0)["video_filter"]).group(1)) < 200


def test_a_picture_the_crop_still_shows_gets_no_sticker():
    inside = sticker_profile(Rect(900, 700, 120, 160))              # in the middle of the frame: the crop shows it
    assert "alphamerge" not in gl.build_gaming_layout(inside, 20.0)["video_filter"]


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_sticker_layout_renders(tmp_path):
    graph = gl.build_gaming_layout(sticker_profile(), 6.0)["video_filter"]
    script = tmp_path / "graph.txt"
    script.write_text(graph)
    out = tmp_path / "out.mp4"
    run = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={SRC_W}x{SRC_H}:r=10:d=6",
         "-/filter_complex", str(script), "-map", "[vout]", "-c:v", "libx264", "-preset", "ultrafast", str(out)],
        capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height,nb_frames",
         "-of", "csv=p=0", str(out)], capture_output=True, text=True,
    )
    assert probe.stdout.strip() == "1080,1920,60"


def test_someone_filmed_in_the_middle_of_the_frame_is_not_a_keyed_out_streamer():
    frames, times = clip(cam=False)
    middle = (290.0, 90.0, 56.0, 70.0)                              # 1.7 % of the frame, nowhere near its edge
    profile = classify(frames, times, [[middle] for _ in frames])
    assert not profile.is_gaming and not profile.virtual_camera
    assert profile.reason == "people_on_screen"
    # The same head at the edge of the picture is a streamer beside the game.
    edge = (20.0, 240.0, 56.0, 70.0)
    profile = classify(frames, times, [[edge] for _ in frames])
    assert profile.is_gaming and profile.virtual_camera


def camera_without_hud(count=48, seed=7):
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(count):
        frame = world(rng)
        draw_cam(frame, i)
        frames.append(frame)
    return np.stack(frames), [i / 3 for i in range(count)]


def test_a_bigger_still_face_beside_the_inset_is_a_reaction_video():
    frames, times = camera_without_hud()
    host = (120.0, 150.0, 60.0, 75.0)
    profile = classify(frames, times, [[CAM_FACE, host] for _ in frames])
    assert not profile.is_gaming and profile.reason == "people_on_screen"


def test_a_character_who_comes_and_goes_in_a_cutscene_is_not_a_host():
    # No HUD during a cutscene; the character is on screen for 60 % of the clip, larger than the streamer.
    frames, times = camera_without_hud()
    character = (120.0, 150.0, 60.0, 75.0)
    faces = [[CAM_FACE, character] if i % 5 < 3 else [CAM_FACE] for i in range(len(frames))]
    profile = classify(frames, times, faces)
    assert profile.is_gaming and profile.reason == "facecam_hud"


def calm_head_clip(count=48, seed=7, noise=True):
    """Gameplay with a HUD and a head in the bottom-left corner that barely moves: a streamer listening."""
    rng = np.random.default_rng(seed)
    texture = rng.integers(120, 200, (42, 34, 3), dtype=np.uint8)
    frames = []
    for i in range(count):
        frame = world(rng)
        draw_hud(frame)
        skin = texture.copy()
        if noise:                                                   # a live camera: grain, a blink, a nod
            skin = np.clip(skin.astype(np.int16) + rng.integers(-14, 15, skin.shape), 0, 255).astype(np.uint8)
        frame[250:292, 60:94] = skin
        frames.append(frame)
    return np.stack(frames), [i / 3 for i in range(count)]


def test_a_streamer_who_sits_still_is_not_a_picture():
    face = (60.0, 250.0, 34.0, 42.0)
    frames, times = calm_head_clip()
    profile = classify(frames, times, [[face] for _ in frames])
    assert profile.is_gaming and profile.virtual_camera
    # The very same head without a single change is a picture.
    frames, times = calm_head_clip(noise=False)
    profile = classify(frames, times, [[face] for _ in frames])
    assert profile.facecam is None and not profile.virtual_camera


def test_a_short_look_cannot_call_a_face_a_picture():
    # A per-camera segment of a compilation can be a few seconds long: too little to tell a calm moment from
    # a picture, so the face counts as a person, as it did before.
    face = (60.0, 250.0, 34.0, 42.0)
    frames, times = calm_head_clip(count=gl.FACE_MOTION_MIN_SAMPLES - 6, noise=False)
    profile = classify(frames, times, [[face] for _ in frames])
    assert profile.virtual_camera


def test_a_host_only_slightly_larger_than_the_inset_is_not_a_host():
    frames, times = camera_without_hud()
    other = (120.0, 150.0, 44.0, 58.0)                              # 1.16 times the camera's face
    profile = classify(frames, times, [[CAM_FACE, other] for _ in frames])
    assert profile.is_gaming and profile.reason == "facecam_hud"


def test_one_watermark_does_not_switch_the_host_rule_off():
    # A reaction video with a channel logo in the corner: one persistent graphic is not a game's HUD.
    frames, times = camera_without_hud()
    for frame in frames:
        cv2.rectangle(frame, (12, 322), (170, 348), (15, 15, 15), -1)
        cv2.putText(frame, "CHANNEL TV", (16, 342), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
    host = (120.0, 150.0, 60.0, 75.0)
    profile = classify(frames, times, [[CAM_FACE, host] for _ in frames])
    assert len(profile.hud) == 1
    assert not profile.is_gaming and profile.reason == "people_on_screen"


def test_a_face_that_stays_put_over_a_hud_is_the_games_own_character():
    # The player's character seen from behind, a conversation: the game draws its HUD around them.
    frames, times = clip(cam=True)
    character = (120.0, 150.0, 60.0, 75.0)
    profile = classify(frames, times, [[CAM_FACE, character] for _ in frames])
    assert profile.is_gaming and profile.reason == "facecam_hud"


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


def test_a_game_that_stands_still_beside_a_camera_is_still_a_stream():
    # A menu or the map: only part of the picture moves. Without a camera that is a screen recording.
    rng = np.random.default_rng(5)
    still = world(rng)
    frames = []
    for i in range(48):
        frame = still.copy()
        frame[120:300, 40:300] = world(rng)[120:300, 40:300]      # a quarter of the screen keeps moving
        draw_hud(frame)
        draw_cam(frame, i)
        frames.append(frame)
    frames = np.stack(frames)
    times = [i / 3 for i in range(48)]
    with_cam = classify(frames, times, [[CAM_FACE] for _ in frames])
    assert gl.MIN_MOVING_FRACTION_WITH_FACECAM <= with_cam.moving_fraction < gl.MIN_MOVING_FRACTION
    assert with_cam.is_gaming and with_cam.facecam is not None
    without = classify(frames, times)
    assert not without.is_gaming and without.reason == "static_picture"


def still_clip(count=48, seed=5):
    rng = np.random.default_rng(seed)
    still = world(rng)
    frames = []
    for i in range(count):
        frame = still.copy()
        draw_hud(frame)
        draw_cam(frame, i)
        frames.append(frame)
    return np.stack(frames), [i / 3 for i in range(count)]


def test_a_picture_that_stands_still_beside_a_camera_is_shown_whole_under_it():
    frames, times = still_clip()
    profile = classify(frames, times, [[CAM_FACE] for _ in frames])
    assert profile.is_gaming and profile.reason == "facecam_still"
    assert profile.focus is not None and profile.hud == []
    layout = gl.build_gaming_layout(profile, 16.0)
    graph = layout["video_filter"]
    assert "boxblur" in graph and "[gm_fill]" in graph and graph.endswith("[vout]")
    assert layout["gaming"]["facecam_panel"] is not None


def test_a_speaker_in_front_of_a_frame_is_not_a_stream():
    # The same picture with a head several times larger: someone filmed in front of a window, not an inset.
    frames, times = still_clip()
    big = (CAM_FACE[0] - 20, CAM_FACE[1] - 10, 90.0, 80.0)
    assert big[2] * big[3] > gl.CALM_PICTURE_FACE_MAX_AREA * W * H
    profile = classify(frames, times, [[big] for _ in frames])
    assert not profile.is_gaming and profile.reason == "static_picture"


def test_a_still_picture_is_enlarged_around_the_part_that_changes():
    # A phone held up in the middle of a still scene, its text scrolling: the sides of the picture give way.
    def frames_with(columns):
        frames, times = still_clip()
        for i, frame in enumerate(frames):
            for x in columns:
                cv2.rectangle(frame, (x, 150), (x + 60, 250), (245, 245, 245), -1)
                for line in range(150 + (i * 5) % 12, 250, 12):         # lines of text, scrolling
                    cv2.line(frame, (x + 6, line), (x + 54, line), (30, 30, 30), 3)
        return frames, times

    frames, times = frames_with([250])
    profile = classify(frames, times, [[CAM_FACE] for _ in frames])
    assert profile.reason == "facecam_still"
    focus = profile.focus
    assert profile.focus_fill and focus.w <= 0.85 * SRC_H
    assert focus.x <= 250 * 3 and focus.x2 >= 310 * 3                # the phone is inside it
    # It fills the panel: no fitted strip, no blurred bars, and the camera on top is not in the picture again.
    layout = gl.build_gaming_layout(profile, 16.0)
    assert "boxblur" not in layout["video_filter"]
    crop, cam = Rect(*layout["gaming"]["gameplay"]), profile.facecam
    assert crop.x2 <= cam.x or crop.x >= cam.x2 or crop.y2 <= cam.y or crop.y >= cam.y2
    assert crop.x <= 250 * 3 and crop.x2 >= 310 * 3
    # Changes at both sides of the picture (chat on the left, a counter on the right): shown whole, as before.
    frames, times = frames_with([20, 380])
    whole = classify(frames, times, [[CAM_FACE] for _ in frames])
    assert whole.reason == "facecam_still" and not whole.focus_fill and whole.focus.w > 0.95 * SRC_W * (440 / W)
    assert "boxblur" in gl.build_gaming_layout(whole, 16.0)["video_filter"]


def test_a_filling_crop_steps_off_the_camera_only_while_the_subject_stays_in_it():
    cam = Rect(40, 400, 300, 300)
    subject = Rect(400, 0, 500, 1080)
    crop, fitted = gl.still_crop(subject, SRC_W, SRC_H, 1312, fill=True, avoid=cam)
    assert fitted is None and crop.h >= SRC_H - 2 and crop.w / crop.h == pytest.approx(1080 / 1312, rel=0.01)
    assert crop.x >= cam.x2 and crop.x <= subject.x and crop.x2 >= subject.x2
    # The subject sits right beside the camera: stepping off the camera would push it out, so the crop stays.
    near = Rect(200, 0, 700, 1080)
    crop, _ = gl.still_crop(near, SRC_W, SRC_H, 1312, fill=True, avoid=cam)
    assert crop.x <= near.x and crop.x2 >= near.x2
    # A camera in the middle of a narrow frame leaves no room either.
    crop, _ = gl.still_crop(Rect(0, 0, 900, 1080), 900, SRC_H, 1312, fill=True, avoid=Rect(300, 400, 300, 300))
    assert crop.x <= 12


def test_a_clock_in_the_corner_does_not_narrow_a_still_picture():
    frames, times = still_clip()
    for i, frame in enumerate(frames):
        cv2.rectangle(frame, (400, 340), (414, 350), (i * 53 % 255, 255 - i * 31 % 255, 40), -1)
    profile = classify(frames, times, [[CAM_FACE] for _ in frames])
    assert profile.reason == "facecam_still" and profile.focus.x <= 12


def test_still_detail_fills_the_panel_or_is_fitted_across_it():
    phone = Rect(820, 200, 280, 700)                   # narrower than the panel: the crop widens around it
    crop, fitted = gl.still_crop(phone, SRC_W, SRC_H, 1312)
    assert fitted is None and crop.w / crop.h == pytest.approx(1080 / 1312, rel=0.01)
    assert crop.x <= phone.x and crop.x2 >= phone.x2 and crop.y <= phone.y and crop.y2 >= phone.y2
    slide = Rect(0, 0, SRC_W, SRC_H)                    # the whole frame: nothing is cut
    crop, fitted = gl.still_crop(slide, SRC_W, SRC_H, 1312)
    assert crop.as_list() == [0, 0, SRC_W, SRC_H] and fitted == (1080, 608)


def test_a_wide_camera_without_a_drawn_border_is_found_on_the_second_look():
    rng = np.random.default_rng(13)
    cam = (370, 0, 270, 125)                            # nine face widths across, flush with the top right corner
    face = (490.0, 40.0, 30.0, 38.0)
    frames = []
    for i in range(48):
        frame = world(rng)
        draw_hud(frame)
        x, y, w, h = cam
        frame[y:y + h, x:x + w] = (70, 40, 90)          # a dark room, no frame drawn around it
        sway = (i * 7) % 23 - 11
        cv2.circle(frame, (505 + sway, 60), 20, (150, 170, 210), -1)
        for eye in (-8, 8):
            cv2.circle(frame, (505 + sway + eye, 56), 3, (30, 30, 30), -1)
        cv2.rectangle(frame, (475 + sway, 85), (535 + sway, 123), (40, 40, 160), -1)
        frames.append(frame)
    frames = np.stack(frames)
    evidence = gl.measure_frames(frames, [i / 3 for i in range(48)])
    assert gl.find_facecam_rect(evidence, face) is None
    found = gl.find_facecam_rect(evidence, face, patient=True)
    assert found is not None and abs(found.x - 370) <= 4 and abs(found.y2 - 125) <= 4
    profile = gl.classify_gaming(evidence, [[face] if i % 4 == 0 else [] for i in range(48)], SRC_W, SRC_H, 16.0)
    assert profile.is_gaming and profile.facecam is not None and not profile.virtual_camera


def split_clip(count=48, split=lambda i: 12 <= i < 36, seed=9):
    rng = np.random.default_rng(seed)
    frames = []
    for i in range(count):
        frame = world(rng)
        if split(i):
            frame[:, W // 2 - 2:W // 2 + 2] = 255                 # the divider between two pictures
        frames.append(frame)
    return np.stack(frames), [i / 3 for i in range(count)]


def test_a_side_by_side_comparison_stacks_its_halves_while_the_screen_is_split():
    frames, times = split_clip()
    profile = classify(frames, times)
    assert profile.is_gaming and profile.reason == "split_screen"
    assert profile.split_ranges == [(pytest.approx(4.0 - 1 / 6), pytest.approx(12.0 - 1 / 6))]
    layout = gl.build_split_layout(profile, 16.0)
    report = layout["gaming"]
    left, right = report["split"]["left"], report["split"]["right"]
    # Each half keeps clear of the divider and fills half of the canvas.
    assert left[0] + left[2] < SRC_W // 2 < right[0]
    assert left[2] / left[3] == pytest.approx(1080 / 960, rel=0.01) == pytest.approx(right[2] / right[3], rel=0.01)
    graph = layout["video_filter"]
    assert "vstack=inputs=2[sp_out]" in graph and graph.endswith("[vout]") and graph.count("[vout]") == 1
    assert "enable='between(t\\,3.833\\,11.833)'" in graph


def test_a_shaded_pole_down_the_middle_is_not_a_divider():
    rng = np.random.default_rng(21)
    frames = []
    for i in range(48):
        frame = world(rng)
        column = np.linspace(40, 230, H).astype(np.uint8)          # lit from above: not one flat colour
        frame[:, W // 2 - 2:W // 2 + 2] = column[:, None, None]
        frames.append(frame)
    profile = classify(np.stack(frames), [i / 3 for i in range(48)])
    assert profile.split_ranges == [] and not profile.is_gaming


def test_a_guest_thumbnail_beside_a_larger_speaker_is_not_a_stream():
    # A calm picture, an inset with a small face, and a speaker whose face is too close or too seldom seen to track.
    frames, times = still_clip()
    close_up = (150.0, 60.0, 190.0, 230.0)                          # 19 % of the frame: never a fixed track
    often = classify(frames, times, [[CAM_FACE, close_up] for _ in frames])
    assert not often.is_gaming and often.reason == "people_on_screen"
    medium = (200.0, 120.0, 80.0, 100.0)
    seldom = classify(frames, times, [[CAM_FACE, medium] if i % 5 < 2 else [CAM_FACE] for i in range(len(frames))])
    assert not seldom.is_gaming and seldom.reason == "people_on_screen"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_fitted_still_picture_with_a_fallback_span_renders(tmp_path):
    profile = profile_with(Rect(1320, 60, 540, 304), None, [], [(0.3, 0.6)])
    profile.focus = Rect(60, 100, 1352, 700)                        # wider than the panel: fitted, over a blur
    layout = gl.build_gaming_layout(profile, 1.0)
    graph = layout["video_filter"]
    labels = re.findall(r"\[(\w+)\]", graph)
    assert all(labels.count(name) == 2 for name in set(labels) - {"0", "vout"}), graph
    out = tmp_path / "frame.mp4"
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={SRC_W}x{SRC_H}:rate=10:duration=1",
         "-filter_complex", graph, "-map", "[vout]", "-c:v", "libx264", "-preset", "ultrafast", str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_a_layout_switch_is_placed_half_a_frame_before_the_new_picture(tmp_path):
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    # 2 s at 30 fps: black, then white from frame 41 (a frame whose time rounds up at three decimals).
    frames = np.zeros((60, 90, 160, 3), np.uint8)
    frames[41:] = 255
    source = tmp_path / "cut.mp4"
    encode = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", "160x90", "-r", "30", "-i", "-",
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10", "-pix_fmt", "yuv420p", str(source)],
        input=frames.tobytes(), capture_output=True,
    )
    assert encode.returncode == 0, encode.stderr
    cut = gl.find_cut(source, 1.0, 1.667)
    assert 40 / 30 < cut < 41 / 30                                   # frame 40 keeps the old layout, frame 41 has the new


def test_gameplay_without_a_divider_and_people_side_by_side_are_not_comparisons():
    frames, times = split_clip(split=lambda i: False)
    assert classify(frames, times).split_ranges == []
    frames, times = split_clip(split=lambda i: i == 20)            # one sample: a lamp post, not a split
    assert classify(frames, times).split_ranges == []
    frames, times = split_clip()
    debate = classify(frames, times, [[(150.0, 120.0, 90.0, 110.0)] for _ in frames])
    assert debate.reason == "people_on_screen" and not debate.is_gaming


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_split_layout_renders_a_1080x1920_frame(tmp_path):
    profile = profile_with()
    profile.split_ranges = [(0.2, 0.7)]
    layout = gl.build_split_layout(profile, 1.0)
    out = tmp_path / "frame.mp4"
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=size={SRC_W}x{SRC_H}:rate=10:duration=1",
         "-filter_complex", layout["video_filter"], "-map", "[vout]", "-c:v", "libx264", "-preset", "ultrafast",
         str(out)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


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


# ---------------------------------------------------------------------------
# Second production review: maps, keyed-out streamers who leave, stickers beside HUD
# ---------------------------------------------------------------------------

def test_labels_in_the_middle_of_the_picture_are_not_a_hud():
    # A drawn map panned across the screen: persistent labels, none of them at the frame's edge.
    rng = np.random.default_rng(3)
    frames = []
    for _ in range(48):
        frame = world(rng)
        for x, y, text in ((120, 90, "TWILIGHT"), (410, 90, "STRONGHOLD"), (120, 250, "SUNNYGLADE"), (410, 250, "RIVERGLADE")):
            cv2.rectangle(frame, (x, y), (x + 120, y + 40), (15, 15, 15), -1)
            cv2.putText(frame, text, (x + 6, y + 27), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        frames.append(frame)
    profile = classify(np.stack(frames), [i / 3 for i in range(48)])
    assert len(profile.hud) >= 3 and not profile.is_gaming and profile.reason == "no_hud"


def test_a_keyed_out_streamer_who_leaves_the_screen_gives_the_frame_back():
    frames, times = clip(cam=False)
    face = (60.0, 250.0, 34.0, 42.0)
    faces = [[face] if i < 28 else [] for i in range(len(frames))]      # gone for the last 6.7 s
    profile = classify(frames, times, faces)
    assert profile.is_gaming and profile.virtual_camera
    assert len(profile.fallback_ranges) == 1
    start, end = profile.fallback_ranges[0]
    assert 8.0 <= start <= 10.5 and end == pytest.approx(16.0, abs=0.4)
    # A missed detection or two is not leaving.
    blinks = [[face] if i % 9 else [] for i in range(len(frames))]
    assert classify(frames, times, blinks).fallback_ranges == []


def test_the_crop_slides_off_a_keyed_out_streamer_and_a_logo_at_its_edge():
    keyed = GamingProfile(width=SRC_W, height=SRC_H, is_gaming=True, reason="facecam_hud",
                          facecam=Rect(16, 596, 686, 484), face=(250.0, 700.0, 160.0, 190.0), virtual_camera=True)
    crop = Rect(*gl.build_gaming_layout(keyed, 20.0)["gaming"]["gameplay"])
    centred = (SRC_W - crop.w) // 2
    assert crop.x >= min(702, centred + int(SRC_W * gl.CROP_SHIFT_MAX)) - 2   # as far as the shift goes
    # A logo the centre crop would cut in half: the crop steps off it and the logo goes up as a sticker.
    logo = sticker_profile(Rect(1180, 40, 200, 120))
    layout = gl.build_gaming_layout(logo, 20.0)
    crop = Rect(*layout["gaming"]["gameplay"])
    assert crop.x2 <= 1180 and "alphamerge" in layout["video_filter"]


def test_a_picture_joined_to_a_hud_panel_is_not_a_sticker():
    # An avatar right beside the minimap: one unchanging patch. Lifting it out would take the minimap with it.
    frames, times = clip(cam=False)
    face = (118.0, 30.0, 34.0, 42.0)
    for frame in frames:
        cv2.circle(frame, (135, 51), 26, (150, 170, 210), -1)
        cv2.circle(frame, (128, 44), 4, (20, 20, 20), -1)
        cv2.circle(frame, (142, 44), 4, (20, 20, 20), -1)
    profile = classify(frames, times, [[face] for _ in frames])
    mark = profile.sticker
    assert mark is None or mark.w * mark.h <= gl.STICKER_MAX_FACE_AREAS * (34 * 3) * (42 * 3)
    assert any(r.x < 330 and r.y < 330 for r in profile.hud)             # the minimap is still a HUD element


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_a_sticker_mask_is_fully_opaque_on_a_full_range_source(tmp_path):
    graph = gl.build_gaming_layout(sticker_profile(), 6.0)["video_filter"]
    mask = graph.split("[gm_sk]")[0].split(";")[-1]                     # the chain that draws the mask
    assert "out_range=tv" in mask
    script = tmp_path / "graph.txt"
    script.write_text(graph)
    out = tmp_path / "out.mp4"
    run = subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s={SRC_W}x{SRC_H}:r=10:d=6,format=yuvj420p",
         "-/filter_complex", str(script), "-map", "[vout]", "-c:v", "libx264", "-preset", "ultrafast", str(out)],
        capture_output=True, text=True,
    )
    assert run.returncode == 0, run.stderr
