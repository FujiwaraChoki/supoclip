import json
import shutil
import subprocess
from types import SimpleNamespace

import pytest

from src.media.source_limits import (
    MAX_OUTPUT_FPS,
    UnreadableAudioError,
    VideoGeometry,
    build_normalization_command,
    exceeds_output_limits,
    fit_within_1080p,
    normalize_source_media,
    output_rate_token,
    parse_frame_rate,
    pick_audio_stream,
    probe_video_geometry,
    source_within_output_limits,
)
from src.media.upload_limits import (
    MULTIPART_FRAMING_BYTES,
    format_upload_limit,
    reject_content_length,
    upload_too_large_detail,
)


@pytest.mark.parametrize(
    "width,height,expected",
    [
        (1920, 1080, (1920, 1080)),
        (1080, 1920, (1080, 1920)),
        (1280, 720, (1280, 720)),
        (3840, 2160, (1920, 1080)),
        (2160, 3840, (1080, 1920)),
        (2560, 1440, (1920, 1080)),
        (1920, 1440, (1440, 1080)),
        (3840, 1080, (1920, 540)),
        (1440, 1440, (1080, 1080)),
    ],
)
def test_fit_within_1080p_preserves_aspect_and_the_box(width, height, expected):
    fitted = fit_within_1080p(width, height)
    assert fitted == expected
    assert max(fitted) <= 1920
    assert min(fitted) <= 1080
    assert fitted[0] % 2 == 0 and fitted[1] % 2 == 0


def test_frame_rate_cap_keeps_60_and_ntsc():
    assert parse_frame_rate("60/1") == 60
    assert parse_frame_rate("60000/1001") == pytest.approx(59.94, abs=0.01)
    assert not exceeds_output_limits(1920, 1080, 60)
    assert not exceeds_output_limits(1920, 1080, 59.94)
    assert exceeds_output_limits(1920, 1080, 120)
    assert exceeds_output_limits(3840, 2160, 30)


def test_rotation_uses_display_dimensions():
    geometry = VideoGeometry(3840, 2160, 30, 90)
    assert (geometry.display_width, geometry.display_height) == (2160, 3840)
    assert fit_within_1080p(geometry.display_width, geometry.display_height) == (1080, 1920)


def test_ntsc_rates_and_phone_vfr_timebases():
    assert output_rate_token(60) == "60"
    assert output_rate_token(60000 / 1001) == "60000/1001"
    assert output_rate_token(24000 / 1001) == "24000/1001"
    assert output_rate_token(120) == "60"
    # A 90k timebase is not a frame rate. A phone file whose average is 30
    # and whose r_frame_rate is 120 still uses the average.
    assert parse_frame_rate("90000/1") is None
    assert parse_frame_rate("30/1") == 30
    assert not exceeds_output_limits(1920, 1080, parse_frame_rate("30/1"))


def test_anamorphic_and_hdr_geometry():
    wide = VideoGeometry(1440, 1080, 24, 0, sample_aspect=4 / 3)
    assert (wide.display_width, wide.display_height) == (1920, 1080)
    assert not wide.hdr
    hdr = VideoGeometry(3840, 2160, 24, 0, color_transfer="smpte2084", pix_fmt="yuv420p10le")
    assert hdr.hdr
    command = build_normalization_command(
        __import__("pathlib").Path("in.mov"),
        __import__("pathlib").Path("out.mp4"),
        scale=(1920, 1080),
        cap_frame_rate=False,
        audio_stream=0,
        copy_audio=True,
        square_pixels=True,
        tonemap=True,
    )
    filters = command[command.index("-vf") + 1]
    assert "iw*sar" in filters
    assert "tonemap=tonemap=hable" in filters
    assert "scale=1920:1080" in filters
    assert command[command.index("-f") + 1] == "mp4"
    assert "bt709" in command


def test_content_length_rules():
    assert reject_content_length([], [], 1000) is None
    assert reject_content_length(["1000"], [], 1000) is None
    assert reject_content_length([str(1000 + MULTIPART_FRAMING_BYTES)], [], 1000) is None
    assert reject_content_length([str(1000 + MULTIPART_FRAMING_BYTES + 1)], [], 1000) == "too_large"
    assert reject_content_length(["10"], ["chunked"], 1000) == "conflict"
    assert reject_content_length(["nope"], [], 1000) == "malformed"
    assert reject_content_length(["1", "2"], [], 1000) == "malformed"


def test_upload_limit_copy_states_the_cap():
    assert format_upload_limit(4_000_000_000) == "4 GB"
    assert "4 GB" in upload_too_large_detail(4_000_000_000)
    assert "too large" in upload_too_large_detail(4_000_000_000)


def _completed(stdout="", code=0):
    return SimpleNamespace(returncode=code, stdout=stdout, stderr="")


AAC_AUDIO = json.dumps({"streams": [{"index": 1, "codec_name": "aac"}]})


def _is_audio_probe(command):
    return command[0] == "ffprobe" and command[command.index("-select_streams") + 1] == "a"


def test_normalize_leaves_1080p30_untouched(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"original")
    calls = []

    def fake_run(command, **_kwargs):
        calls.append(command)
        payload = {"streams": [{"width": 1920, "height": 1080, "avg_frame_rate": "30/1"}]}
        return _completed(json.dumps(payload))

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    assert normalize_source_media(source) == source
    assert source.read_bytes() == b"original"
    assert all(command[0] != "ffmpeg" for command in calls)


def test_normalize_scales_4k_120fps_in_place(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"original")

    def fake_run(command, **_kwargs):
        if _is_audio_probe(command):
            return _completed(AAC_AUDIO)
        if command[0] == "ffprobe":
            payload = {
                "streams": [{
                    "width": 3840,
                    "height": 2160,
                    "avg_frame_rate": "120/1",
                    "r_frame_rate": "120/1",
                }]
            }
            return _completed(json.dumps(payload))
        assert f"scale=1920:1080" in command[command.index("-vf") + 1]
        assert f"fps={MAX_OUTPUT_FPS}" in command[command.index("-vf") + 1]
        assert "copy" in command
        destination = command[-1]
        from pathlib import Path
        Path(destination).write_bytes(b"capped")
        return _completed()

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    assert normalize_source_media(source) == source
    assert source.read_bytes() == b"capped"
    assert not list(tmp_path.glob(".*capped*"))
    locks = list(tmp_path.glob(".*.normalize.lock"))
    assert len(locks) == 1 and locks[0].stat().st_size == 0


def test_failed_normalization_keeps_the_original(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"original")

    def fake_run(command, **_kwargs):
        if command[0] == "ffprobe" and "v:0" in command:
            payload = {"streams": [{"width": 3840, "height": 2160, "avg_frame_rate": "30/1"}]}
            return _completed(json.dumps(payload))
        if command[0] == "ffprobe":
            return _completed("")
        return _completed(code=1)

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    with pytest.raises(RuntimeError, match="1080p"):
        normalize_source_media(source)
    assert source.read_bytes() == b"original"


def test_unreadable_probe_counts_as_within_limits(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"original")

    def fake_run(_command, **_kwargs):
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    assert probe_video_geometry(source) is None
    assert source_within_output_limits(source) is True


def _sample_video(path, size: str, rate: int) -> None:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration=0.2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.2",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True,
    )


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_real_source_above_1080p_and_60fps_is_capped_in_place(tmp_path):
    source = tmp_path / "video.mp4"
    _sample_video(source, "2048x1152", 120)

    assert normalize_source_media(source) == source
    geometry = probe_video_geometry(source)
    assert geometry is not None
    assert (geometry.width, geometry.height) == (1920, 1080)
    assert geometry.fps == pytest.approx(60, abs=0.2)
    audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0", "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True, check=True,
    )
    assert "audio" in audio.stdout
    assert not list(tmp_path.glob(".*capped*"))


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_real_source_within_limits_is_not_rewritten(tmp_path):
    source = tmp_path / "video.mp4"
    _sample_video(source, "1280x720", 30)
    before = source.read_bytes()

    assert normalize_source_media(source) == source
    assert source.read_bytes() == before


def test_audio_copy_failure_falls_back_without_touching_the_sidecar(tmp_path, monkeypatch):
    source = tmp_path / "video.webm"
    source.write_bytes(b"original")
    sidecar = source.with_suffix(".transcript_cache.json")
    sidecar.write_text('{"words":[{"text":"hi"}]}')

    def fake_run(command, **_kwargs):
        if _is_audio_probe(command):
            return _completed(AAC_AUDIO)
        if command[0] == "ffprobe":
            payload = {"streams": [{"width": 3840, "height": 2160, "avg_frame_rate": "60000/1001"}]}
            return _completed(json.dumps(payload))
        if "copy" in command:
            return _completed(code=1)
        assert command[command.index("-c:a") + 1] == "aac"
        assert "fps=60" not in command[command.index("-vf") + 1]
        from pathlib import Path
        Path(command[-1]).write_bytes(b"capped")
        return _completed()

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    assert normalize_source_media(source) == source
    assert source.read_bytes() == b"capped"
    assert sidecar.read_text() == '{"words":[{"text":"hi"}]}'
    assert not list(tmp_path.glob(".*capped*"))


def test_normalize_stops_before_ffmpeg_when_disk_is_full(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"x" * 64)

    def fake_run(command, **_kwargs):
        if command[0] == "ffprobe" and "v:0" in command:
            payload = {"streams": [{"width": 3840, "height": 2160, "avg_frame_rate": "24/1"}]}
            return _completed(json.dumps(payload))
        if command[0] == "ffmpeg":
            raise AssertionError("ffmpeg ran without room for the output")
        return _completed("")

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    monkeypatch.setattr(
        "src.media.source_limits.shutil.disk_usage",
        lambda _path: SimpleNamespace(free=1024),
    )
    with pytest.raises(RuntimeError, match="disk"):
        normalize_source_media(source)
    assert source.read_bytes() == b"x" * 64
    assert not list(tmp_path.glob(".*capped*"))


def test_normalization_command_can_reencode_audio():
    command = build_normalization_command(
        __import__("pathlib").Path("in.mp4"),
        __import__("pathlib").Path("out.mp4"),
        scale=(1920, 1080),
        cap_frame_rate=False,
        audio_stream=0,
        copy_audio=False,
    )
    assert command[command.index("-c:a") + 1] == "aac"
    assert "fps=60" not in command


# An iPhone .mov: H.264 video, a spatial-audio track ffprobe can't name,
# the regular AAC track, and a `mebx` metadata track.
IPHONE_AUDIO_STREAMS = [{"index": 1, "codec_name": "none"}, {"index": 2, "codec_name": "aac"}]


def test_pick_audio_stream_skips_undecodable_tracks():
    assert pick_audio_stream(IPHONE_AUDIO_STREAMS) == 1
    assert pick_audio_stream([{"index": 1}, {"index": 2, "codec_name": "aac"}]) == 1
    assert pick_audio_stream([{"index": 1, "codec_name": "aac"}]) == 0
    assert pick_audio_stream([{"index": 1}, {"index": 2, "codec_name": "none"}]) is None
    assert pick_audio_stream([]) is None


def _maps(command):
    return [command[i + 1] for i, part in enumerate(command) if part == "-map"]


@pytest.mark.parametrize("copy_audio", [True, False])
def test_iphone_mov_maps_only_the_first_video_and_the_decodable_audio(tmp_path, monkeypatch, copy_audio):
    source = tmp_path / "video.mov"
    source.write_bytes(b"original")
    commands = []

    def fake_run(command, **_kwargs):
        if _is_audio_probe(command):
            return _completed(json.dumps({"streams": IPHONE_AUDIO_STREAMS}))
        if command[0] == "ffprobe":
            payload = {"streams": [{"width": 3840, "height": 2160, "avg_frame_rate": "30/1"}]}
            return _completed(json.dumps(payload))
        commands.append(command)
        if copy_audio is False and "copy" in command:
            return _completed(code=1)
        from pathlib import Path
        Path(command[-1]).write_bytes(b"capped")
        return _completed()

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    assert normalize_source_media(source) == source
    assert source.read_bytes() == b"capped"
    assert commands
    for command in commands:
        assert _maps(command) == ["0:v:0", "0:a:1"]


def test_source_with_only_undecodable_audio_fails_with_a_clear_message(tmp_path, monkeypatch):
    from src.services.task_service import user_facing_error

    source = tmp_path / "video.mov"
    source.write_bytes(b"original")

    def fake_run(command, **_kwargs):
        if _is_audio_probe(command):
            return _completed(json.dumps({"streams": [{"index": 1, "codec_name": "none"}, {"index": 2}]}))
        if command[0] == "ffprobe":
            payload = {"streams": [{"width": 3840, "height": 2160, "avg_frame_rate": "30/1"}]}
            return _completed(json.dumps(payload))
        raise AssertionError("ffmpeg ran on a source with no readable audio")

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    with pytest.raises(UnreadableAudioError) as raised:
        normalize_source_media(source)
    assert raised.value.code == "audio_unreadable"
    shown = user_facing_error(raised.value)
    assert "audio track" in shown and "re-export" in shown
    assert "Retry" not in shown
    assert source.read_bytes() == b"original"


def test_source_without_audio_is_still_normalized_silently(tmp_path, monkeypatch):
    source = tmp_path / "video.mp4"
    source.write_bytes(b"original")

    def fake_run(command, **_kwargs):
        if _is_audio_probe(command):
            return _completed(json.dumps({"streams": []}))
        if command[0] == "ffprobe":
            payload = {"streams": [{"width": 3840, "height": 2160, "avg_frame_rate": "30/1"}]}
            return _completed(json.dumps(payload))
        assert _maps(command) == ["0:v:0"] and "-an" in command
        from pathlib import Path
        Path(command[-1]).write_bytes(b"capped")
        return _completed()

    monkeypatch.setattr("src.media.source_limits.subprocess.run", fake_run)
    assert normalize_source_media(source) == source
    assert source.read_bytes() == b"capped"


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg is required")
def test_real_source_with_an_undecodable_extra_audio_track_is_capped(tmp_path):
    # PCM under an unknown fourcc: ffprobe reports the track with no codec,
    # like the spatial-audio track on iPhone recordings. Mapping it fails
    # both the audio copy and the audio transcode.
    source = tmp_path / "video.mov"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "testsrc2=size=2048x1152:rate=30:duration=0.3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
            "-f", "lavfi", "-i", "sine=frequency=880:duration=0.3",
            "-map", "0:v", "-map", "1:a", "-map", "2:a",
            "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
            "-c:a:0", "pcm_s16le", "-tag:a:0", "zzzz",
            "-c:a:1", "aac",
            "-strict", "-2", "-f", "mov", str(source),
        ],
        check=True,
    )
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "json", str(source)],
        capture_output=True, text=True, check=True,
    )
    streams = json.loads(probe.stdout)["streams"]
    if len(streams) != 2 or streams[0].get("codec_name"):
        pytest.skip("this ffmpeg build decodes the fixture's unknown audio track")

    assert normalize_source_media(source) == source
    geometry = probe_video_geometry(source)
    assert geometry is not None and (geometry.width, geometry.height) == (1920, 1080)
    audio = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True, check=True,
    )
    assert audio.stdout.split() == ["aac"]
