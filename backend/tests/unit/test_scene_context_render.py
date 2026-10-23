"""Real FFmpeg tests: retain edge content, timing and audio through the graph."""

from pathlib import Path
import shutil
import subprocess

import numpy as np
import pytest

from src.media import reframing
from src.media.ffmpeg import ffprobe_duration, ffprobe_has_audio, ffprobe_video_size


pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg required")


@pytest.fixture
def landmarks(tmp_path):
    path = tmp_path / "landmarks.mp4"
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
        "color=green:s=320x180:r=10:d=2,drawbox=x=0:y=0:w=70:h=180:color=red:t=fill,"
        "drawbox=x=250:y=0:w=70:h=180:color=blue:t=fill",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
    ], capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return path


def decoded_previews(source: Path, graph: str):
    result = subprocess.run([
        "ffmpeg", "-v", "error", "-i", str(source), "-filter_complex",
        graph + ";[vout]fps=2,scale=108:192[preview]", "-map", "[preview]",
        "-pix_fmt", "rgb24", "-f", "rawvideo", "-",
    ], capture_output=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return np.frombuffer(result.stdout, np.uint8).reshape(-1, 192, 108, 3)


def test_full_frame_graph_retains_both_edges(landmarks):
    frames = decoded_previews(landmarks, reframing.build_full_frame_filter())
    assert len(frames) == 4
    # The unblurred foreground must contain both red and blue source edges.
    assert np.all(frames[:, 96, 5, 0] > 200)
    assert np.all(frames[:, 96, 102, 2] > 200)


def test_mixed_layout_switches_without_losing_frames(landmarks):
    graph = reframing.build_vertical_compositor_filter(
        "crop=100:180:110:0,scale=1080:1920,setsar=1", [(0, 1)], [(1, 2)])
    frames = decoded_previews(landmarks, graph)
    assert len(frames) == 4
    assert frames[0, 96, 5, 1] > frames[0, 96, 5, 0] + 60
    assert frames[-1, 96, 5, 0] > 200
    assert frames[-1, 96, 102, 2] > 200


def test_crop_position_resets_at_camera_cut(landmarks):
    track = [(i / 3, 35 if i < 3 else 285, 0.05) for i in range(6)]
    expression = reframing.build_scene_crop_expression(track, [1], 2, 320, 100)
    graph = f"[0:v]crop=100:180:x='{expression}':y=0,scale=1080:1920[vout]"
    frames = decoded_previews(landmarks, graph)
    assert frames[1, 96, 54, 0] > 200  # still left immediately before the cut
    assert frames[2, 96, 54, 2] > 200  # right immediately at the cut


def test_explicit_full_frame_overrides_precomputed_crop_and_keeps_audio(landmarks, tmp_path, monkeypatch):
    monkeypatch.setattr(reframing, "analyze_vertical_clip", lambda *_: pytest.fail("override needs no analysis"))
    output = tmp_path / "vertical.mp4"
    ok, width, height = reframing.render_reframed_clip_ffmpeg(
        landmarks, output, "vertical_fit", vertical_plan=("crop=100:180:110:0", "vf"))
    assert ok and (width, height) == (1080, 1920)
    assert ffprobe_video_size(output) == (1080, 1920)
    assert ffprobe_has_audio(output)
    assert abs(ffprobe_duration(output) - 2) < 0.15
    frames = decoded_previews(output, "[0:v]null[vout]")
    assert frames[0, 96, 5, 0] > 200 and frames[0, 96, 102, 2] > 200
