from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.media import ffmpeg


def test_multi_range_render_seeks_to_the_first_kept_moment(tmp_path):
    commands = []

    def fake_run(command, timeout=900):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    # Short pieces skip the crossfade and take the hard-concat path.
    ranges = [(100.0, 100.3), (100.5, 100.8)]
    with (
        patch.object(ffmpeg, "run_ffmpeg_command", side_effect=fake_run),
        patch.object(ffmpeg, "select_audio_stream_index", return_value=None),
    ):
        assert ffmpeg.render_source_ranges_ffmpeg(Path("in.mp4"), ranges, tmp_path / "out.mp4")

    command = commands[0]
    ss = command.index("-ss")
    assert command[ss + 1] == "100.000"
    assert ss < command.index("-i")
    graph = command[command.index("-filter_complex") + 1]
    assert "trim=start=0.000:end=0.300" in graph
    assert "trim=start=0.500:end=0.800" in graph
    assert "trim=start=100" not in graph


def test_crossfade_render_seeks_before_reading(tmp_path):
    commands = []

    def fake_run(command, timeout=900):
        commands.append(command)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with patch.object(ffmpeg, "run_ffmpeg_command", side_effect=fake_run):
        assert ffmpeg.render_ranges_crossfade_ffmpeg(
            Path("in.mp4"),
            [(30.0, 32.0), (34.0, 36.0)],
            tmp_path / "out.mp4",
            audio_stream=None,
        )

    command = commands[0]
    assert command[command.index("-ss") + 1] == "30.000"
    graph = command[command.index("-filter_complex") + 1]
    assert "trim=start=0.000:end=2.000" in graph
    assert "trim=start=4.000:end=6.000" in graph
