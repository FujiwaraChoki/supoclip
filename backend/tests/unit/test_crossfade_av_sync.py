"""Crossfaded multi-range renders keep video and audio the same length on any source.

A range's video ends on a whole frame, usually before its nominal end. xfade
offsets worked out in seconds could then land past the last frame of the first
input; xfade forwards the second input with restarted timestamps, the encoder
drops those frames, and the picture freezes while the audio plays on. The real
case was a 23.976 fps source: video stopped at 15.27 s of a 30 s trailer.
"""

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.media import ffmpeg
from src.media.common import OUTPUT_FPS
from src.media.ffmpeg import _crossfade_video_frames, render_source_ranges_ffmpeg
from src.media.render_ranges import output_offsets_for_junctions, rendered_duration

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required"
)

# Range lengths of the fixture trailer that froze. On a 23.976 fps source with a
# 0.04 s fade these freeze the same way: the picture stops at 14.57 s of 21.56 s.
FROZE_RANGES = [
    (0.75, 6.1),
    (8.371, 12.581),
    (14.432, 19.542),
    (20.583, 27.593),
]


def _source(tmp_path, video_filter):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", video_filter,
            "-f", "lavfi", "-i", "sine=f=220:d=45",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-fps_mode", "vfr",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
    )
    return source


def _stream_durations(path):
    streams = json.loads(
        subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
            check=True, capture_output=True, text=True,
        ).stdout
    )["streams"]
    return {stream["codec_type"]: float(stream["duration"]) for stream in streams}


def _planned(ranges, fade):
    groups = list(range(len(ranges)))
    return output_offsets_for_junctions(ranges, groups, [fade] * (len(ranges) - 1))


@needs_ffmpeg
@pytest.mark.parametrize(
    ("video_filter", "fade"),
    [
        # The fixture's frame rate, with the short declick that froze it.
        ("testsrc=s=64x36:r=24000/1001:d=45", 0.04),
        # A screen recording's rate, with the recap's fade. One source frame is
        # longer than the fade, so every junction is exposed.
        ("testsrc=s=64x36:r=4:d=45", 0.12),
        # Variable rate: one frame every five seconds, so some ranges hold none.
        ("testsrc=s=64x36:r=30:d=45,select='not(mod(n\\,150))'", 0.12),
    ],
    ids=["23.976fps", "4fps", "variable-rate"],
)
def test_video_and_audio_last_as_long_as_planned(tmp_path, video_filter, fade):
    source = _source(tmp_path, video_filter)
    offsets = _planned(FROZE_RANGES, fade)
    output = tmp_path / "clip.mp4"

    assert render_source_ranges_ffmpeg(source, FROZE_RANGES, output, output_offsets=offsets)

    planned = rendered_duration(FROZE_RANGES, offsets)
    durations = _stream_durations(output)
    assert durations["video"] == pytest.approx(planned, abs=1.5 / OUTPUT_FPS)
    assert durations["audio"] == pytest.approx(planned, abs=0.05)


@needs_ffmpeg
def test_highlight_default_crossfades_keep_video_and_audio_together(tmp_path):
    # No explicit offsets: the cleanup crossfade every highlight clip uses.
    source = _source(tmp_path, "testsrc=s=64x36:r=4:d=45")
    ranges = [(2.0, 8.3), (9.1, 14.7), (15.2, 21.9), (22.6, 28.4), (29.0, 36.1)]
    output = tmp_path / "clip.mp4"

    assert ffmpeg.crossfade_fade_for_ranges(ranges) > 0
    assert render_source_ranges_ffmpeg(source, ranges, output)

    planned = ffmpeg.rendered_ranges_duration(ranges)
    durations = _stream_durations(output)
    assert durations["video"] == pytest.approx(planned, abs=1.5 / OUTPUT_FPS)
    assert durations["audio"] == pytest.approx(planned, abs=0.05)


def _luma_per_frame(path, width=64, height=36):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo", "-pix_fmt", "yuv420p", "-"],
        check=True, capture_output=True,
    ).stdout
    frame_size = width * height * 3 // 2
    return [
        sum(raw[index:index + width * height]) / (width * height)
        for index in range(0, len(raw), frame_size)
    ]


@needs_ffmpeg
def test_every_frame_shows_the_source_moment_its_range_plans(tmp_path):
    # Brightness encodes source time, so a frame that plays the wrong moment shows.
    ramp = 18.0
    source = _source(
        tmp_path,
        f"color=black:s=64x36:r=24000/1001:d=12,format=yuv444p,"
        f"geq=lum='16+T*{ramp}':cb=128:cr=128",
    )
    ranges = [(0.75, 3.1), (4.2, 6.41), (7.0, 9.11), (9.5, 11.6)]
    offsets = _planned(ranges, 0.12)
    output = tmp_path / "clip.mp4"

    assert render_source_ranges_ffmpeg(source, ranges, output, output_offsets=offsets)

    luma = _luma_per_frame(output)
    checked = 0
    for frame, value in enumerate(luma):
        moment = frame / OUTPUT_FPS
        # The last range that has started, skipping frames inside a dissolve.
        index = max(i for i, offset in enumerate(offsets) if offset <= moment + 1e-6)
        played = moment - offsets[index]
        if index > 0 and played < 0.16:
            continue
        if played >= ranges[index][1] - ranges[index][0]:
            continue
        assert value == pytest.approx(16 + ramp * (ranges[index][0] + played), abs=2.2), f"frame {frame}"
        checked += 1
    assert checked > 0.9 * len(luma)


def test_frame_layout_stays_on_the_rounded_clip_timeline():
    durations = [end - start for start, end in FROZE_RANGES]
    fades = [0.04] * (len(durations) - 1)

    leads, counts, overlaps = _crossfade_video_frames(durations, fades)

    # Rounding never adds up: the frames played match the planned length.
    planned = sum(durations) - sum(fades)
    assert sum(counts) - sum(overlaps) == round(planned * OUTPUT_FPS)
    assert all(abs(lead) <= 0.5 / OUTPUT_FPS + 1e-9 for lead in leads)
    # 0.04 s is 1.2 frames.
    assert all(overlap in (1, 2) for overlap in overlaps)


def test_a_fade_under_half_a_frame_is_a_hard_cut_in_video_only():
    _, counts, overlaps = _crossfade_video_frames([3.0, 3.0], [0.01])
    assert overlaps == [0]
    assert sum(counts) == 180


def test_hard_cuts_never_overlap_even_after_rounding():
    _, counts, overlaps = _crossfade_video_frames([2.0, 0.3, 6.8, 10.0], [0.0, 0.12, 0.0])
    assert overlaps[0] == 0 and overlaps[2] == 0
    assert overlaps[1] > 0
    assert sum(counts) - sum(overlaps) == round((2.0 + 0.3 + 6.8 + 10.0 - 0.12) * OUTPUT_FPS)


def test_every_range_declares_a_frame_rate_after_rebasing(tmp_path):
    # setpts clears the link's frame rate. ffmpeg 7.1 (production) then refuses
    # the graph at xfade, though newer builds accept it.
    commands = []

    def fake_run(command, timeout=900):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(ffmpeg, "run_ffmpeg_command", side_effect=fake_run):
        assert ffmpeg.render_ranges_crossfade_ffmpeg(
            Path("in.mp4"), [(30.0, 32.0), (34.0, 36.0)], tmp_path / "out.mp4", audio_stream=None
        )

    graph = commands[0][commands[0].index("-filter_complex") + 1]
    video_chains = [chain for chain in graph.split(";") if ":v]" in chain]
    assert len(video_chains) == 2
    assert all(f"setpts=PTS-STARTPTS,fps={OUTPUT_FPS}," in chain for chain in video_chains)
