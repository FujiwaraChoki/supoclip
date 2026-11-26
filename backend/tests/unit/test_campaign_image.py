import shutil
import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from src.media.campaign_image import (
    BAND_BOTTOM_FRAC,
    MAX_CAMPAIGN_IMAGE_SIDE,
    CampaignImageError,
    apply_campaign_image,
    campaign_image_box,
    normalize_campaign_image,
)
from src.media.ffmpeg import ffprobe_duration, ffprobe_video_size

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


def _encode(image: np.ndarray, ext: str) -> bytes:
    ok, data = cv2.imencode(ext, image)
    assert ok
    return data.tobytes()


def test_png_keeps_alpha_and_size():
    image = np.zeros((200, 600, 4), np.uint8)
    image[50:150, 100:500] = (0, 0, 255, 255)
    result = normalize_campaign_image(_encode(image, ".png"))
    decoded = cv2.imdecode(np.frombuffer(result.png, np.uint8), cv2.IMREAD_UNCHANGED)
    assert (result.width, result.height) == (600, 200)
    assert decoded.shape == (200, 600, 4)
    assert decoded[0, 0, 3] == 0 and decoded[100, 300, 3] == 255


def test_jpeg_becomes_png():
    result = normalize_campaign_image(_encode(np.full((300, 400, 3), 128, np.uint8), ".jpg"))
    assert result.png.startswith(b"\x89PNG")
    assert (result.width, result.height) == (400, 300)


def test_large_image_is_capped_on_its_long_side():
    result = normalize_campaign_image(_encode(np.zeros((1000, 4000, 3), np.uint8), ".png"))
    assert result.width == MAX_CAMPAIGN_IMAGE_SIDE
    assert result.height == 400


@pytest.mark.parametrize("data", [b"", b"not an image at all", _encode(np.zeros((8, 8, 3), np.uint8), ".png")])
def test_rejects_unusable_uploads(data):
    with pytest.raises(CampaignImageError):
        normalize_campaign_image(data)


@pytest.mark.parametrize("size", [(1080, 1920), (720, 1280)])
def test_box_sits_above_a_centred_16_9_video(size):
    width, height = size
    x, y, box_width, box_height = campaign_image_box(width, height)
    video_top = (height - width * 9 / 16) / 2
    assert x >= 0 and x + box_width <= width
    assert y > 0
    assert y + box_height <= video_top
    assert y + box_height == int(height * BAND_BOTTOM_FRAC)


def _clip(path: Path, size: str) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c=black:s={size}:d=1:r=30",
            "-f", "lavfi", "-i", "sine=f=440:d=1",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )


@needs_ffmpeg
def test_overlay_lands_in_the_band_and_keeps_length_and_audio(tmp_path):
    clip = tmp_path / "clip.mp4"
    _clip(clip, "1080x1920")
    image = tmp_path / "campaign.png"
    image.write_bytes(normalize_campaign_image(_encode(np.full((400, 1200, 3), 255, np.uint8), ".png")).png)
    duration = ffprobe_duration(clip)

    assert apply_campaign_image(clip, image) is True

    assert ffprobe_video_size(clip) == (1080, 1920)
    assert abs(ffprobe_duration(clip) - duration) < 0.1
    streams = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(clip)],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert streams == ["video", "audio"]
    frame_path = tmp_path / "frame.png"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", str(clip), "-frames:v", "1", str(frame_path)], check=True)
    frame = cv2.imread(str(frame_path), cv2.IMREAD_GRAYSCALE)
    x, y, box_width, box_height = campaign_image_box(1080, 1920)
    assert frame[y + box_height // 2, 540] > 200  # the white image, mid-band
    assert frame[1500, 540] < 40  # below the band the clip is untouched
    assert not any(tmp_path.glob(".clip.campaign*"))


@needs_ffmpeg
def test_landscape_output_is_left_alone(tmp_path):
    clip = tmp_path / "wide.mp4"
    _clip(clip, "1920x1080")
    before = clip.read_bytes()
    image = tmp_path / "campaign.png"
    image.write_bytes(normalize_campaign_image(_encode(np.full((100, 300, 3), 255, np.uint8), ".png")).png)

    assert apply_campaign_image(clip, image) is False
    assert clip.read_bytes() == before
