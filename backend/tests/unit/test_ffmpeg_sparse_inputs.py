"""Sparse planned clips decode range by range; explicit hard cuts skip the crossfade attempt."""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.media import ffmpeg
from src.media.common import OUTPUT_FPS
from src.media.ffmpeg import ffprobe_duration
from src.media.render_ranges import rendered_duration

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required"
)


def _capture(tmp_path, ranges, offsets=None, audio=1):
    commands = []

    def fake_run(command, timeout=900):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with (
        patch.object(ffmpeg, "run_ffmpeg_command", side_effect=fake_run),
        patch.object(ffmpeg, "select_audio_stream_index", return_value=audio),
    ):
        kwargs = {} if offsets is None else {"output_offsets": offsets}
        assert ffmpeg.render_source_ranges_ffmpeg(Path("in.mp4"), ranges, tmp_path / "out.mp4", **kwargs)
    return commands


def _inputs(command):
    return [index for index, arg in enumerate(command) if arg == "-i"]


def test_sparse_trailer_gets_one_padded_input_per_range(tmp_path):
    # Three 3s beats spread over 20 minutes, played out of order with 0.04s declicks.
    ranges = [(1200.0, 1203.0), (300.0, 303.0), (900.0, 903.0)]
    commands = _capture(tmp_path, ranges, offsets=[0.0, 2.96, 5.92])

    command = commands[0]
    # Inputs open in source order; each beat reads its own padded window.
    assert len(_inputs(command)) == 3
    assert [command[index + 1] for index, arg in enumerate(command) if arg == "-ss"] == [
        "299.500", "899.500", "1199.500",
    ]
    assert command[command.index("-t") + 1] == "4.000"
    graph = command[command.index("-filter_complex") + 1]
    # Each range is read on the output's frame grid, its window opened 0.5 s early.
    first = graph.split(";")[0]
    assert first.startswith("[2:v]fps=30:start_time=0,") and "trim=start_frame=15:end_frame=105" in first
    assert "[2:a:1]atrim=start=0.500:end=3.500" in graph
    assert "[0:a:1]atrim=start=0.500:end=3.500" in graph
    assert "[1:a:1]atrim=start=0.500:end=3.500" in graph
    # 0.04 s rounds to one output frame.
    assert f"xfade=transition=fade:duration={1 / OUTPUT_FPS:.6f}" in graph


def test_dense_ranges_keep_the_single_input_command(tmp_path):
    # A series part with one skipped sponsor read: not sparse.
    ranges = [(100.0, 140.0), (170.0, 200.0)]
    commands = _capture(tmp_path, ranges, offsets=[0.0, 39.9])

    command = commands[0]
    assert len(_inputs(command)) == 1
    assert command[command.index("-ss") + 1] == "100.000"
    graph = command[command.index("-filter_complex") + 1]
    assert "[0:v]fps=30:start_time=0,tpad=stop=-1:stop_mode=clone,trim=start_frame=0:end_frame=1200" in graph
    # 170 s is 70 s into the window: input frame 2100.
    assert "trim=start_frame=2100:end_frame=3000" in graph


def test_highlight_cleanup_cuts_are_never_sparse(tmp_path):
    ranges = [(600.0, 612.0), (612.4, 630.0), (630.5, 660.0)]
    assert not ffmpeg._sparse(ranges)
    command = _capture(tmp_path, ranges)[0]
    assert len(_inputs(command)) == 1


def test_all_hard_cut_offsets_go_straight_to_concat(tmp_path, caplog):
    ranges = [(10.0, 12.0), (20.0, 23.0)]
    commands = _capture(tmp_path, ranges, offsets=[0.0, 2.0])

    assert len(commands) == 1
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1[v][a]" in graph
    assert "xfade" not in graph
    assert "Crossfade stitch failed" not in caplog.text


def test_mismatched_offsets_are_rejected(tmp_path):
    with patch.object(ffmpeg, "select_audio_stream_index", return_value=None):
        assert not ffmpeg.render_source_ranges_ffmpeg(
            Path("in.mp4"), [(1.0, 2.0), (3.0, 4.0)], tmp_path / "out.mp4", output_offsets=[0.0]
        )


def test_many_sparse_ranges_are_cut_to_pieces_first(tmp_path):
    ranges = [(index * 100.0, index * 100.0 + 1.0) for index in range(26)]
    commands = _capture(tmp_path, ranges, offsets=[float(index) for index in range(26)])

    cuts, stitch = commands[:26], commands[26]
    assert all(command[command.index("-ss") + 1] == f"{max(0.0, i * 100.0 - 0.5):.3f}" for i, command in enumerate(cuts))
    assert all("-crf" in command and command[-1].endswith(f"r{i}.mp4") for i, command in enumerate(cuts))
    assert len(_inputs(stitch)) == 26
    assert "-ss" not in stitch
    graph = stitch[stitch.index("-filter_complex") + 1]
    # Pieces hold only the chosen audio track.
    assert "[25:a:0]atrim=start=0.500:end=1.500" in graph
    assert "[0:v]trim=start=0.000:end=1.000" in graph


def test_cleanup_pieces_of_one_point_share_an_input(tmp_path):
    # A recap of six points over an hour, each cut by cleanup into five pieces:
    # thirty ranges, six windows, no pre-cut re-encode.
    ranges = [
        (point * 600.0 + piece * 4.0, point * 600.0 + piece * 4.0 + 3.0)
        for point in range(6)
        for piece in range(5)
    ]
    offsets = [index * 3.0 for index in range(len(ranges))]
    commands = _capture(tmp_path, ranges, offsets=offsets)

    assert len(commands) == 1
    command = commands[0]
    assert len(_inputs(command)) == 6
    graph = command[command.index("-filter_complex") + 1]
    # Point 2's third piece (1208-1211) is read from the window opened at 1199.5.
    assert "[2:v]trim=start=8.500:end=11.500" in graph


def test_neighbours_share_a_window_only_in_play_order():
    # 1200-1204 plays after 1210-1212: one input would decode it first and hold
    # its frames until the cold open drained, so it gets its own window.
    ranges = [(1210.0, 1212.0), (300.0, 303.0), (1200.0, 1204.0)]
    windows, owners = ffmpeg._sparse_windows(ranges)

    assert windows == [(299.5, 4.0), (1199.5, 5.0), (1209.5, 3.0)]
    assert owners == [2, 0, 1]

    in_order = [(300.0, 303.0), (1200.0, 1204.0), (1210.0, 1212.0)]
    assert ffmpeg._sparse_windows(in_order) == ([(299.5, 4.0), (1199.5, 13.0)], [0, 1, 1])


def test_dense_ranges_that_jump_back_read_each_window_separately(tmp_path):
    # A trailer whose cold open comes from late in a short source: one seeked
    # input would buffer every earlier beat as raw frames behind the cold open.
    ranges = [(140.0, 146.0), (10.0, 25.0), (30.0, 45.0), (60.0, 70.0)]
    assert not ffmpeg._sparse(ranges)
    command = _capture(tmp_path, ranges, offsets=[0.0, 5.96, 20.92, 35.88])[0]

    assert len(_inputs(command)) == 2
    assert [command[index + 1] for index, arg in enumerate(command) if arg == "-ss"] == ["9.500", "139.500"]
    graph = command[command.index("-filter_complex") + 1]
    first = graph.split(";")[0]
    assert first.startswith("[1:v]fps=") and "trim=start_frame=15:end_frame=195" in first
    assert "[0:a:1]atrim=start=50.500:end=60.500" in graph


def test_hook_teaser_reads_its_own_window(tmp_path):
    # A legacy teaser replays a moment from inside the clip body before it.
    ranges = [(42.0, 45.0), (30.0, 60.0)]
    command = _capture(tmp_path, ranges)[0]

    assert len(_inputs(command)) == 2
    graph = command[command.index("-filter_complex") + 1]
    assert "[1:v]trim=start=0.500:end=3.500" in graph
    assert "[0:v]trim=start=0.500:end=30.500" in graph


def _source(tmp_path, seconds):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=s=64x36:r=10:d={seconds}",
            "-f", "lavfi", "-i", f"sine=f=220:d={seconds}",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "50", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
    )
    return source


@needs_ffmpeg
def test_sparse_out_of_order_render_has_the_planned_length(tmp_path):
    source = _source(tmp_path, 240)
    ranges = [(200.0, 203.0), (20.0, 23.5), (120.0, 122.0)]
    offsets = [0.0, 2.96, 6.42]
    output = tmp_path / "trailer.mp4"

    assert ffmpeg._sparse(ranges)
    assert ffmpeg.render_source_ranges_ffmpeg(source, ranges, output, output_offsets=offsets)

    assert ffprobe_duration(output) == pytest.approx(rendered_duration(ranges, offsets), abs=0.12)


@needs_ffmpeg
def test_shared_window_out_of_order_render_has_the_planned_length(tmp_path):
    source = _source(tmp_path, 240)
    # The first and last beats come from one window, played around a far one.
    ranges = [(200.0, 203.0), (20.0, 23.5), (210.0, 212.0)]
    offsets = [0.0, 2.96, 6.42]
    output = tmp_path / "trailer.mp4"

    assert ffmpeg._sparse_windows(ranges)[1] == [1, 0, 1]
    assert ffmpeg.render_source_ranges_ffmpeg(source, ranges, output, output_offsets=offsets)

    assert ffprobe_duration(output) == pytest.approx(rendered_duration(ranges, offsets), abs=0.12)


@needs_ffmpeg
def test_pre_cut_pieces_render_the_planned_length(tmp_path, monkeypatch):
    source = _source(tmp_path, 160)
    monkeypatch.setattr(ffmpeg, "SPARSE_MAX_INPUTS", 2)
    ranges = [(150.0, 152.0), (10.0, 11.5), (80.0, 82.0)]
    offsets = [0.0, 2.0, 3.5]
    output = tmp_path / "recap.mp4"

    assert ffmpeg.render_source_ranges_ffmpeg(source, ranges, output, output_offsets=offsets)

    assert ffprobe_duration(output) == pytest.approx(rendered_duration(ranges, offsets), abs=0.12)
    assert not list(tmp_path.glob("ranges_*"))


@needs_ffmpeg
def test_a_hard_cut_before_a_crossfade_keeps_the_planned_timeline(tmp_path):
    # Cleanup leaves a sliver inside a planned range (intra-range junctions
    # hard-cut), then the planner's dissolve joins the next range. concat's
    # microsecond timebase used to make the later xfade fail, and the hard
    # concat fallback played every later range late against its offsets.
    source = _source(tmp_path, 60)
    ranges = [(10.0, 12.0), (12.5, 12.8), (13.2, 20.0), (30.0, 40.0), (41.0, 50.0)]
    offsets = [0.0, 2.0, 2.3, 8.98, 18.83]
    output = tmp_path / "part.mp4"

    assert ffmpeg.junction_fades_for_offsets(ranges, offsets) == pytest.approx([0.0, 0.0, 0.12, 0.15])
    assert ffmpeg.render_ranges_crossfade_ffmpeg(
        source, ranges, output, ffmpeg.select_audio_stream_index(source), output_offsets=offsets
    )
    assert ffprobe_duration(output) == pytest.approx(rendered_duration(ranges, offsets), abs=0.06)


def test_a_failed_stitch_with_explicit_offsets_fails_the_render(tmp_path):
    with (
        patch.object(ffmpeg, "render_ranges_crossfade_ffmpeg", return_value=False),
        patch.object(ffmpeg, "select_audio_stream_index", return_value=0),
        patch.object(ffmpeg, "run_ffmpeg_command") as run,
    ):
        assert not ffmpeg.render_source_ranges_ffmpeg(
            Path("in.mp4"), [(1.0, 3.0), (5.0, 8.0)], tmp_path / "out.mp4", output_offsets=[0.0, 1.9]
        )
    run.assert_not_called()


def test_a_failed_legacy_crossfade_still_hard_concats(tmp_path):
    with patch.object(ffmpeg, "render_ranges_crossfade_ffmpeg", return_value=False):
        commands = _capture(tmp_path, [(1.0, 3.0), (5.0, 8.0)])
    graph = commands[0][commands[0].index("-filter_complex") + 1]
    assert "concat=n=2:v=1:a=1[v][a]" in graph


def _peak_rss_mb(monkeypatch):
    """Record the peak resident memory of every ffmpeg the render runs, in MB."""
    import re
    import sys

    peaks = []
    launcher = (
        "import resource, subprocess, sys; r = subprocess.run(sys.argv[1:], capture_output=True); "
        "sys.stderr.write('RSS %d' % resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss); "
        "sys.exit(r.returncode)"
    )

    def measured(command, timeout=900):
        result = subprocess.run([sys.executable, "-I", "-c", launcher, *command], capture_output=True, text=True)
        peaks.append(int(re.findall(r"RSS (\d+)", result.stderr)[-1]) / 1024)
        return SimpleNamespace(returncode=result.returncode, stdout="", stderr=result.stderr)

    monkeypatch.setattr(ffmpeg, "run_ffmpeg_command", measured)
    return peaks


@needs_ffmpeg
def test_jumping_back_in_a_dense_clip_does_not_buffer_raw_frames(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=s=640x360:r=30:d=60",
            "-f", "lavfi", "-i", "sine=f=220:d=60",
            "-c:v", "libx264", "-preset", "ultrafast", "-g", "60", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
    )
    peaks = _peak_rss_mb(monkeypatch)

    assert ffmpeg.render_source_ranges_ffmpeg(source, [(5.0, 35.0), (50.0, 54.0)], tmp_path / "a.mp4")
    in_order = max(peaks)
    peaks.clear()
    # The cold open first: thirty seconds of 360p frames (~300 MB) used to wait
    # in the filtergraph behind it.
    assert ffmpeg.render_source_ranges_ffmpeg(
        source, [(50.0, 54.0), (5.0, 35.0)], tmp_path / "b.mp4", output_offsets=[0.0, 3.96]
    )
    assert max(peaks) < in_order + 100
