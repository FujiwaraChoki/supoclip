"""The end-of-clip audio fade, and a re-render never adding the next sentence.

A clip ending just before a sentence that follows straight on used to grow
by that whole sentence when its stored ranges were extended again (a settings
re-render). Every rendered clip also fades its audio over the last moment so a
cut never clicks.
"""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from src.clip_editor import split_clip_file, trim_clip_file
from src.media import timeline
from src.media.ffmpeg import render_source_ranges_ffmpeg

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required"
)


def _source(tmp_path: Path, bursts, duration: float = 4.0, name: str = "source.mp4") -> Path:
    """A video whose audio is loud 220 Hz bursts at ``bursts`` over a faint hum."""
    on = "+".join(f"between(t,{start},{end})" for start, end in bursts) or "0"
    path = tmp_path / name
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"color=c=black:s=64x64:d={duration}:r=30",
            "-f", "lavfi", "-i",
            f"aevalsrc='0.3*sin(2*PI*220*t)*({on})+0.002*sin(2*PI*50*t)':s=16000:d={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )
    return path


def _transcript(video: Path, *words) -> None:
    payload = {
        "version": 2,
        "words": [
            {"text": text, "start": int(start * 1000), "end": int(end * 1000), "confidence": 0.99}
            for text, start, end in words
        ],
    }
    video.with_suffix(".transcript_cache.json").write_text(json.dumps(payload))


def _tail_levels_db(path: Path, seconds: float = 0.1, frame: float = 0.005) -> np.ndarray:
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "48000", "-f", "f32le", "-"],
        capture_output=True,
        check=True,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
    size = int(48000 * frame)
    tail = samples[-int(48000 * seconds) // size * size :].reshape(-1, size)
    return 20 * np.log10(np.sqrt((tail**2).mean(axis=1)) + 1e-9)


@needs_ffmpeg
@pytest.mark.parametrize(
    "ranges",
    [
        [(0.5, 2.0)],
        [(0.2, 1.0), (1.5, 2.5)],  # crossfaded junction
        [(0.2, 0.4), (1.5, 2.5)],  # too short to crossfade: hard concat
    ],
)
def test_every_rendered_clip_fades_out(tmp_path, ranges):
    # A tone that never stops: without the fade the clip ends at full level.
    video = _source(tmp_path, [(0.0, 4.0)])
    output = tmp_path / "clip.mp4"

    assert render_source_ranges_ffmpeg(video, ranges, output)

    levels = _tail_levels_db(output)
    assert levels[0] > -20  # 100 ms before the end the tone still plays
    assert levels[-1] < levels[0] - 15  # and the last 5 ms are faded out


@needs_ffmpeg
def test_trim_and_split_fade_their_new_ends(tmp_path):
    video = _source(tmp_path, [(0.0, 4.0)])
    clips = tmp_path / "clips"

    trimmed = trim_clip_file(video, clips, 0.5, 1.0)
    first, second = split_clip_file(video, clips, 2.0)

    for path in (trimmed, first, second):
        levels = _tail_levels_db(path)
        assert levels[-1] < levels[0] - 15


def test_an_end_held_back_before_the_next_sentence_is_not_extended_again(tmp_path):
    # No audio here: the transcript alone. The first pass ends 30 ms before
    # "Kendall"; extending that stored end again (a settings re-render) used
    # to treat "Kendall" as part of the clip and add its whole sentence.
    video = tmp_path / "source.mp4"
    _transcript(
        video,
        ("She", 0.4, 1.0),
        ("sued.", 1.0, 1.9),
        ("Kendall", 1.93, 2.4),
        ("Llewellyn,", 2.4, 3.0),
        ("one", 3.0, 3.2),
        ("plaintiff.", 3.2, 3.8),
    )

    once = timeline.extend_keep_ranges_to_sentence_boundary(video, [(0.3, 1.5)])
    assert once[-1][1] == pytest.approx(1.9)
    assert timeline.extend_keep_ranges_to_sentence_boundary(video, once) == once
