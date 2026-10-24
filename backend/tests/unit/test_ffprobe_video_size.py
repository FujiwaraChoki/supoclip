"""Displayed video size from ffprobe JSON.

csv ``stream=width,height`` prints a trailing separator when the stream has
side data, so a 1920×1080 phone file with a display matrix comes back as
``1920x1080x``. ``split("x", 1)`` then leaves the height as ``1080x``.
"""

import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from src.clip_editor import _ffprobe_size
from src.media.ffmpeg import (
    ffprobe_video_size,
    parse_ffprobe_video_size,
    stream_rotation_degrees,
)
from src.media.source_limits import VideoGeometry
from src.youtube_utils import _get_local_video_dimensions


def test_normal_landscape_size():
    payload = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}
    assert parse_ffprobe_video_size(payload) == (1920, 1080)


def test_side_data_and_extra_streams_do_not_corrupt_the_size():
    # The same display-matrix section makes csv output ``1920x1080x``.
    # JSON keeps the dimensions on the stream and the extra entries beside them.
    payload = {
        "streams": [
            {"codec_type": "data", "width": 1, "height": 1},
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": 0},
                    {"side_data_type": "H.26[45] User Data Unregistered SEI message"},
                ],
            },
            {"codec_type": "video", "width": 640, "height": 360},
            {"codec_type": "audio", "sample_rate": "48000"},
        ]
    }
    assert parse_ffprobe_video_size(payload) == (1920, 1080)


def test_phone_display_matrix_rotation_uses_displayed_dimensions():
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "width": 1920,
                "height": 1080,
                "side_data_list": [
                    {"side_data_type": "Display Matrix", "rotation": -90},
                ],
            }
        ]
    }
    assert parse_ffprobe_video_size(payload) == (1080, 1920)
    geometry = VideoGeometry(1920, 1080, 30, stream_rotation_degrees(payload["streams"][0]))
    assert (geometry.display_width, geometry.display_height) == (1080, 1920)


@pytest.mark.parametrize("rotation", [90, -90, 270, -270])
def test_quarter_turns_swap_axes(rotation):
    payload = {
        "streams": [
            {
                "width": 1920,
                "height": 1080,
                "tags": {"rotate": str(rotation)},
                # A disagreeing matrix must not override a parsed rotate tag.
                "side_data_list": [{"rotation": 0}],
            }
        ]
    }
    assert parse_ffprobe_video_size(payload) == (1080, 1920)


def test_half_turn_keeps_coded_dimensions():
    payload = {
        "streams": [
            {"width": 1920, "height": 1080, "side_data_list": [{"rotation": 180}]}
        ]
    }
    assert parse_ffprobe_video_size(payload) == (1920, 1080)


def test_square_pixels_ignore_sample_aspect_ratio():
    # Normalization squares anamorphic pixels for the 1080p cap. Crop and scale
    # filters still address the stored frame, so size probing must not.
    payload = {
        "streams": [
            {
                "width": 720,
                "height": 480,
                "sample_aspect_ratio": "32:27",
            }
        ]
    }
    assert parse_ffprobe_video_size(payload) == (720, 480)
    squared = VideoGeometry(720, 480, 30, 0, sample_aspect=32 / 27)
    assert (squared.display_width, squared.display_height) != (720, 480)


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"streams": []},
        {"streams": [{"codec_type": "audio"}]},
        {"streams": [{"codec_type": "video"}]},
        {"streams": [{"codec_type": "video", "width": "1080x", "height": 1920}]},
        {"streams": [{"codec_type": "video", "width": 0, "height": 1080}]},
        "1920x1080x",
    ],
)
def test_missing_or_invalid_stream_is_rejected(payload):
    with pytest.raises(ValueError):
        parse_ffprobe_video_size(payload)


def test_probe_uses_json_and_wraps_a_missing_stream(monkeypatch, tmp_path):
    path = tmp_path / "clip.mp4"
    seen = {}

    def fake_run(command, timeout=60):
        seen["command"] = command
        seen["timeout"] = timeout
        return SimpleNamespace(returncode=0, stdout=json.dumps({"streams": []}), stderr="")

    monkeypatch.setattr("src.media.ffmpeg.run_ffmpeg_command", fake_run)
    with pytest.raises(RuntimeError, match="no video stream"):
        ffprobe_video_size(path)
    command = seen["command"]
    assert command[0] == "ffprobe"
    assert "v:0" in command
    assert command[command.index("-of") + 1] == "json"
    assert "stream_side_data=rotation" in command[command.index("-show_entries") + 1]
    assert "csv" not in command
    assert seen["timeout"] == 60


def test_probe_failure_and_non_json_output_raise_a_clear_error(monkeypatch, tmp_path):
    path = tmp_path / "clip.mp4"

    def failed(command, timeout=60):
        return SimpleNamespace(returncode=1, stdout="1920x1080x", stderr="boom")

    monkeypatch.setattr("src.media.ffmpeg.run_ffmpeg_command", failed)
    with pytest.raises(RuntimeError, match="Unable to read video size"):
        ffprobe_video_size(path)

    def not_json(command, timeout=60):
        return SimpleNamespace(returncode=0, stdout="1920x1080x", stderr="")

    monkeypatch.setattr("src.media.ffmpeg.run_ffmpeg_command", not_json)
    with pytest.raises(RuntimeError, match="not JSON"):
        ffprobe_video_size(path)


def test_clip_editor_and_download_ranking_share_the_probe(monkeypatch, tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"")
    monkeypatch.setattr("src.clip_editor.ffprobe_video_size", lambda _path: (1080, 1920))
    monkeypatch.setattr("src.youtube_utils.ffprobe_video_size", lambda _path: (1080, 1920))
    assert _ffprobe_size(path) == (1080, 1920)
    assert _get_local_video_dimensions(path) == (1080, 1920)

    def broken(_path):
        raise RuntimeError("Unable to read video size")

    monkeypatch.setattr("src.youtube_utils.ffprobe_video_size", broken)
    assert _get_local_video_dimensions(path) == (0, 0)


def _landscape(path):
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=0.1",
            "-frames:v", "2",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg is required")
def test_real_rotated_phone_file_reports_displayed_size(tmp_path):
    source = tmp_path / "landscape.mp4"
    rotated = tmp_path / "phone.mp4"
    _landscape(source)
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-display_rotation:v:0", "-90",
            "-i", str(source),
            "-c", "copy",
            str(rotated),
        ],
        check=True,
    )

    assert ffprobe_video_size(source) == (1920, 1080)
    assert ffprobe_video_size(rotated) == (1080, 1920)

    # Filters see the autorotated frame. The size we return has to match it.
    showinfo = subprocess.run(
        ["ffmpeg", "-v", "info", "-i", str(rotated), "-vf", "showinfo", "-frames:v", "1", "-f", "null", "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "s:1080x1920" in showinfo.stderr


@pytest.mark.skipif(shutil.which("ffprobe") is None, reason="ffprobe is required")
def test_real_probe_of_a_missing_stream_raises(tmp_path):
    bogus = tmp_path / "not-a-video.mp4"
    bogus.write_bytes(b"not a video")
    with pytest.raises(RuntimeError, match="Unable to read video size"):
        ffprobe_video_size(bogus)
