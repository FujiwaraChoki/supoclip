import hashlib
import shutil
import subprocess

import pytest

from src.media.corner_reaction import (
    build_corner_reaction_layout,
    choose_corner_reaction_layout,
    find_corner_webcam,
    reactor_crop,
)

W, H = 1920, 1080
FACE = (1700.0, 800.0, 90.0, 100.0)  # lower-right corner, like a cut-out player


def _samples(face=FACE, count=12):
    return [(i * 0.5, [face]) for i in range(count)]


def test_finds_a_lower_right_webcam_without_any_other_face():
    found = find_corner_webcam(W, H, _samples())
    assert found is not None and abs(found[0] - FACE[0]) < 1


@pytest.mark.parametrize("face", [
    (60.0, 40.0, 90.0, 100.0), (60.0, 800.0, 90.0, 100.0), (1700.0, 40.0, 90.0, 100.0),
])
def test_finds_every_corner(face):
    assert find_corner_webcam(W, H, _samples(face)) is not None


def test_a_large_centred_face_is_not_a_webcam():
    assert find_corner_webcam(W, H, _samples((800.0, 200.0, 400.0, 450.0))) is None


def test_a_roaming_face_is_not_a_webcam():
    roaming = [(i * 0.5, [(1500.0 + (i % 2) * 250, 800.0, 90.0, 100.0)]) for i in range(12)]
    assert find_corner_webcam(W, H, roaming) is None


def test_too_few_samples_is_not_enough():
    assert find_corner_webcam(W, H, _samples(count=4)) is None


def test_crop_stays_inside_the_frame_and_in_the_panel_aspect():
    cw, ch, x, y = reactor_crop(W, H, FACE)
    assert x >= 0 and y >= 0 and x + cw <= W and y + ch <= H
    assert abs(cw / ch - 1080 / 640) < 0.02


def test_bottom_puts_the_webcam_last_and_moves_the_caption():
    top = build_corner_reaction_layout(W, H, FACE, "top")
    bottom = build_corner_reaction_layout(W, H, FACE, "bottom")
    assert top["video_filter"].endswith("[reactor][game]vstack=inputs=2[vout]")
    assert bottom["video_filter"].endswith("[game][reactor]vstack=inputs=2[vout]")
    assert bottom["caption_y"] > top["caption_y"]


def test_no_webcam_returns_none():
    assert choose_corner_reaction_layout(W, H, 6.0, [(i * 0.5, []) for i in range(12)]) is None


def _words(*spans):
    return [{"text": "w", "start": a, "end": b} for a, b in spans]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
@pytest.mark.parametrize("position,words", [
    ("top", None), ("bottom", None),
    ("top", _words(*[(1.0 + i * 0.5, 1.5 + i * 0.5) for i in range(8)], (7.0, 7.4))),
])
def test_filtergraph_renders_a_1080x1920_clip(tmp_path, position, words):
    plan = choose_corner_reaction_layout(
        W, H, 8.0, _samples(), reactor_position=position, speech_words=words
    )
    out = tmp_path / "out.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc2=size=1920x1080:rate=10:duration=8",
        "-filter_complex", plan["video_filter"], "-map", "[vout]",
        "-pix_fmt", "yuv420p", str(out),
    ], check=True)
    size = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
        "stream=width,height", "-of", "csv=p=0", str(out),
    ], check=True, capture_output=True, text=True).stdout.strip()
    assert size == "1080,1920"
    if words:
        def frame(t):
            return hashlib.md5(subprocess.run([
                "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1",
                "-vf", "scale=108:192", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
            ], check=True, capture_output=True).stdout).hexdigest()
        assert len({frame(0.5), frame(2.0), frame(6.0)}) == 3
