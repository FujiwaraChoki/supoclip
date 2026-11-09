import hashlib
import shutil
import subprocess

import pytest

from src.media.reaction_dynamic import build_dynamic_reaction_layout, plan_dynamic_windows


def _words(*spans):
    return [{"text": "w", "start": a, "end": b} for a, b in spans]


def test_opening_on_webcam_then_game_in_the_gap():
    words = _words(*[(1.0 + i * 0.5, 1.5 + i * 0.5) for i in range(8)], (7.0, 7.4))
    assert plan_dynamic_windows(words, 8.0) == [(1.0, 3.5, "webcam"), (5.0, 7.0, "game")]


def test_short_runs_and_short_gaps_keep_the_split():
    # One 1.8s run (gaps under 1.2s join words): too short to open on the webcam.
    words = _words((0.2, 0.8), (1.5, 2.0))
    assert plan_dynamic_windows(words, 3.0) == []


def test_no_speech_gives_no_windows():
    assert plan_dynamic_windows([], 10.0) == []
    assert plan_dynamic_windows(_words((0, 1)), 0) == []


def test_windows_never_overlap_and_stay_in_range():
    words = _words(*[(i * 0.4, i * 0.4 + 0.3) for i in range(30)], (20.0, 24.0))
    windows = plan_dynamic_windows(words, 25.0)
    assert windows == sorted(windows)
    for (a, b, _), (c, _, _) in zip(windows, windows[1:]):
        assert b <= c
    assert all(0 <= a < b <= 25.0 for a, b, _ in windows)


def _layout(words, duration=8.0):
    face = (1600.0, 40.0, 120.0, 120.0)
    inset = (1500.0, 0.0, 420.0, 300.0)
    return build_dynamic_reaction_layout(
        1920, 1080, duration, face, inset, [(0.0, [face])], (), words
    )


def test_no_windows_returns_none_so_the_plain_split_is_used():
    assert _layout([]) is None


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
def test_filtergraph_is_valid_and_changes_with_the_windows(tmp_path):
    words = _words(*[(1.0 + i * 0.5, 1.5 + i * 0.5) for i in range(8)], (7.0, 7.4))
    plan = _layout(words)
    assert plan and [w[2] for w in plan["windows"]] == ["webcam", "game"]
    out = tmp_path / "out.mp4"
    subprocess.run([
        "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
        "-i", "testsrc2=size=1920x1080:rate=10:duration=8",
        "-filter_complex", plan["video_filter"], "-map", "[vout]",
        "-pix_fmt", "yuv420p", str(out),
    ], check=True)

    def frame(t):
        return hashlib.md5(subprocess.run([
            "ffmpeg", "-v", "error", "-ss", str(t), "-i", str(out), "-frames:v", "1",
            "-vf", "scale=108:192", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
        ], check=True, capture_output=True).stdout).hexdigest()

    size = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v", "-show_entries",
        "stream=width,height", "-of", "csv=p=0", str(out),
    ], check=True, capture_output=True, text=True).stdout.strip()
    assert size == "1080,1920"
    # split (0.5), webcam alone (2.0), split (4.0), game alone (6.0) all look different.
    assert len({frame(0.5), frame(2.0), frame(4.0), frame(6.0)}) == 4
