"""Black bars baked into a source are measured once and the layout is planned on the picture between them."""

import shutil
import subprocess

import numpy as np
import pytest

from src.media import letterbox, reframing
from src.media.letterbox import bars_from_frames, bars_that_keep_faces_whole, detect_letterbox, plan_on_picture

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")


def _frames(count, height=180, width=320, top=0, bottom=0, level=120):
    frames = np.full((count, height, width), level, dtype=np.uint8)
    if top:
        frames[:, :top] = 0
    if bottom:
        frames[:, height - bottom:] = 0
    return frames


def test_bars_are_measured_as_a_share_of_the_height():
    top, bottom = bars_from_frames(_frames(12, top=23, bottom=23))
    assert top == pytest.approx(23 / 180) and bottom == pytest.approx(23 / 180)
    # One bar only (a shot placed at the top of the frame).
    assert bars_from_frames(_frames(12, top=40)) == (pytest.approx(40 / 180), 0.0)


def test_a_frame_without_bars_and_thin_strips_are_left_alone():
    assert bars_from_frames(_frames(12)) is None
    assert bars_from_frames(_frames(12, top=4, bottom=4)) is None       # encoder padding
    assert bars_from_frames(_frames(12, top=60, bottom=60)) is None     # less than half the frame is picture


def test_a_dark_background_is_picture_not_a_bar():
    """A code editor or a dark-mode page: dark margins around the content, but not black."""
    frames = np.full((12, 180, 320), 22, dtype=np.uint8)
    frames[:, 40:140, 96:224] = 60
    assert bars_from_frames(frames) is None


def test_dark_frames_do_not_decide_and_a_subtitle_in_a_bar_goes_with_it():
    frames = _frames(12, top=23, bottom=23)
    frames[:3] = 0                                   # a fade from black: says nothing about bars
    assert bars_from_frames(frames)[0] == pytest.approx(23 / 180)
    # The source writes its own subtitles into the bottom bar for a sixth of the clip: the bar goes,
    # subtitles included (the clip gets its own captions).
    frames = _frames(12, top=23, bottom=23)
    frames[4:6, 165:172, 90:230] = 235
    top, bottom = bars_from_frames(frames)
    assert top == pytest.approx(23 / 180) and bottom == pytest.approx(23 / 180)
    # A compilation where a sixth of the frames have no bars: those rows are picture there, so they stay.
    mixed = _frames(12, top=23, bottom=23)
    mixed[4:6] = 120
    assert bars_from_frames(mixed) is None
    # Half the clip without bars: nothing to remove either.
    mixed = _frames(12, top=23, bottom=23)
    mixed[6:] = 120
    assert bars_from_frames(mixed) is None


def test_a_bar_with_a_channel_logo_in_it_is_still_a_bar():
    frames = _frames(12, top=23, bottom=23)
    frames[:, 4:18, 8:30] = 200                      # a small badge in the top bar, in every frame
    top, bottom = bars_from_frames(frames)
    assert top == pytest.approx(23 / 180) and bottom == pytest.approx(23 / 180)
    # A wide strip of picture there (a third of the width, always) is not a logo.
    frames = _frames(12, top=23, bottom=23)
    frames[:, 4:18, 100:220] = 200
    assert bars_from_frames(frames) == (0.0, pytest.approx(23 / 180))


def test_close_ups_keep_as_much_bar_as_keeps_their_faces_whole():
    # 1080 rows with 150-row bars: the picture is 780 rows.
    # Wide shots (faces a tenth of the frame): all of both bars go.
    assert bars_that_keep_faces_whole(150, 150, 1080, 0.10) == (150, 150)
    assert bars_that_keep_faces_whole(150, 150, 1080, 0.0) == (150, 150)
    # A face of 39% of the frame needs 936 rows to stay at 45%: 156 rows of bar stay, 72 go on each side.
    assert bars_that_keep_faces_whole(150, 150, 1080, 0.39) == (72, 72)
    # A face of 48% of the frame needs the whole frame: nothing is removed.
    assert bars_that_keep_faces_whole(150, 150, 1080, 0.48) is None
    # Uneven bars are trimmed in proportion.
    assert bars_that_keep_faces_whole(200, 100, 1080, 0.39) == (96, 48)


def test_plan_on_picture_puts_the_crop_in_front_of_every_read():
    assert plan_on_picture("crop=452:804:x=700:y=0,scale=1080:1920", "vf", 1920, 804, 138) == (
        "crop=1920:804:0:138,crop=452:804:x=700:y=0,scale=1080:1920")
    graph = "[0:v]split=2[bgsrc][ftsrc];[bgsrc]gblur[bg];[ftsrc]scale=1080:-2[fit];[bg][fit]overlay[vout]"
    assert plan_on_picture(graph, "complex", 1920, 804, 138) == (
        "[0:v]crop=1920:804:0:138[lbx0];"
        "[lbx0]split=2[bgsrc][ftsrc];[bgsrc]gblur[bg];[ftsrc]scale=1080:-2[fit];[bg][fit]overlay[vout]")
    twice = "[0:v]scale=540:960[a];[0:v]crop=10:10[b];[a][b]overlay[vout]"
    assert plan_on_picture(twice, "complex", 1920, 804, 138) == (
        "[0:v]crop=1920:804:0:138,split=2[lbx0][lbx1];"
        "[lbx0]scale=540:960[a];[lbx1]crop=10:10[b];[a][b]overlay[vout]")


def _letterboxed(path, bar=140, seconds=4):
    """A 1280x720 clip whose picture sits between two black bars of ``bar`` rows."""
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc2=s=1280x{720 - 2 * bar}:r=30:d={seconds}",
         "-vf", f"pad=1280:720:0:{bar}:black", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )


@needs_ffmpeg
def test_bars_are_found_in_a_clip_and_not_in_a_plain_one(tmp_path):
    boxed = tmp_path / "boxed.mp4"
    _letterboxed(boxed)
    top, bottom = detect_letterbox(boxed, 1280, 720)
    assert 140 <= top <= 146 and 140 <= bottom <= 146 and top % 2 == 0 and bottom % 2 == 0
    plain = tmp_path / "plain.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)], check=True)
    assert detect_letterbox(plain, 1280, 720) is None


@needs_ffmpeg
def test_the_plan_of_a_letterboxed_clip_renders_without_black_bands(tmp_path, monkeypatch):
    """The layout is planned on the picture; applied to the whole frame it leaves no bar in the output."""
    boxed = tmp_path / "boxed.mp4"
    _letterboxed(boxed)
    seen = {}

    def inner(path, width, height, *_args, **_kwargs):
        seen["size"] = (width, height)
        seen["bars_gone"] = detect_letterbox(path, width, height) is None
        # What the planner returns for a clip without a close subject: a centred full-height crop.
        crop_w = int(height * 9 / 16) // 2 * 2
        return {"kind": "crop", "mode": "vf", "caption_y": None,
                "video_filter": f"crop={crop_w}:{height}:{(width - crop_w) // 2}:0,scale=1080:1920"}

    monkeypatch.setattr(reframing, "_describe_vertical_layout", inner)
    described = reframing.describe_vertical_layout(boxed, 1280, 720)
    top, bottom = described["letterbox"]
    assert seen == {"size": (1280, 720 - top - bottom), "bars_gone": True}
    assert described["video_filter"].startswith(f"crop=1280:{720 - top - bottom}:0:{top},")
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(boxed), "-vf", described["video_filter"] + ",scale=108:192",
         "-frames:v", "1", "-pix_fmt", "gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    frame = np.frombuffer(raw, dtype=np.uint8).reshape(192, 108)
    assert (frame.max(axis=1) > 24).all()            # no black row left anywhere in the 9:16 frame


@needs_ffmpeg
def test_a_clip_without_bars_is_planned_as_before(tmp_path, monkeypatch):
    plain = tmp_path / "plain.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=s=1280x720:r=30:d=4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(plain)], check=True)
    calls = []
    monkeypatch.setattr(reframing, "_describe_vertical_layout", lambda path, w, h, *a, **k: calls.append((path, w, h)) or {
        "kind": "crop", "mode": "vf", "caption_y": None, "video_filter": "crop=404:720:438:0,scale=1080:1920"})
    described = reframing.describe_vertical_layout(plain, 1280, 720)
    assert calls == [(plain, 1280, 720)]
    assert "letterbox" not in described and described["video_filter"] == "crop=404:720:438:0,scale=1080:1920"
    monkeypatch.setattr(letterbox, "detect_letterbox", lambda *_a: (_ for _ in ()).throw(RuntimeError("probe failed")))
    assert reframing.describe_vertical_layout(plain, 1280, 720)["video_filter"].startswith("crop=404")
