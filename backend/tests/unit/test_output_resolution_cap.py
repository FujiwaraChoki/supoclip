import shutil
import subprocess

import pytest

from src.media.ffmpeg import cap_video_short_side, ffprobe_has_audio, ffprobe_video_size

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")


def _clip(path, width, height):
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={width}x{height}:rate=30:duration=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )
    return path


def test_vertical_full_hd_clip_is_downscaled_to_720p_with_audio(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 1080, 1920)

    assert cap_video_short_side(clip, 720) is True

    assert ffprobe_video_size(clip) == (720, 1280)
    assert ffprobe_has_audio(clip)
    assert not list(tmp_path.glob(".*capped*"))


@pytest.mark.parametrize("size", [(720, 1280), (640, 360)])
def test_clip_at_or_below_the_cap_is_left_alone(tmp_path, size):
    clip = _clip(tmp_path / "clip.mp4", *size)
    before = clip.read_bytes()

    assert cap_video_short_side(clip, 720) is False

    assert clip.read_bytes() == before


def test_landscape_original_format_caps_its_height(tmp_path):
    clip = _clip(tmp_path / "clip.mp4", 1920, 1080)

    cap_video_short_side(clip, 720)

    assert ffprobe_video_size(clip) == (1280, 720)
