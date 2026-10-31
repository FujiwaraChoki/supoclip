"""Sparse planned clips decode range by range; explicit hard cuts skip the crossfade attempt."""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.media import ffmpeg
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
    assert "[2:v]trim=start=0.500:end=3.500,setpts=PTS-STARTPTS,fps=" in graph.split(";")[0]
    assert "[0:v]trim=start=0.500:end=3.500" in graph
    assert "[1:a:1]atrim=start=0.500:end=3.500" in graph
    assert "xfade=transition=fade:duration=0.040000" in graph


def test_dense_ranges_keep_the_single_input_command(tmp_path):
    # A series part with one skipped sponsor read: not sparse.
    ranges = [(100.0, 140.0), (170.0, 200.0)]
    commands = _capture(tmp_path, ranges, offsets=[0.0, 39.9])

    command = commands[0]
    assert len(_inputs(command)) == 1
    assert command[command.index("-ss") + 1] == "100.000"
    graph = command[command.index("-filter_complex") + 1]
    assert "[0:v]trim=start=0.000:end=40.000" in graph
    assert "[0:v]trim=start=70.000:end=100.000" in graph


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


def test_out_of_order_neighbours_share_a_window():
    ranges = [(1210.0, 1212.0), (300.0, 303.0), (1200.0, 1204.0)]
    windows, owners = ffmpeg._sparse_windows(ranges)

    assert windows == [(299.5, 4.0), (1199.5, 13.0)]
    assert owners == [1, 0, 1]


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
    ranges = [(210.0, 212.0), (20.0, 23.5), (200.0, 203.0)]
    offsets = [0.0, 1.96, 5.42]
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
