import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src.config import Config
from src.media import ffmpeg


def probe(*streams):
    result = SimpleNamespace(returncode=0, stdout=json.dumps({"streams": list(streams)}), stderr="")
    return patch.object(ffmpeg, "run_ffmpeg_command", return_value=result)


def stream(language="und", **disposition):
    return {"tags": {"language": language}, "disposition": disposition}


def test_prefers_default_dialogue_over_commentary():
    with probe(stream("eng", comment=1), stream("spa", default=1)):
        assert ffmpeg.select_audio_stream_index(Path("v.mp4"), "") == 1


def test_skips_dub_tracks_when_nothing_is_flagged_default():
    with probe(stream("eng", dub=1), stream("jpn")):
        assert ffmpeg.select_audio_stream_index(Path("v.mp4"), "") == 1


def test_configured_language_picks_matching_track():
    with probe(stream("eng", default=1), stream("ger")):
        assert ffmpeg.select_audio_stream_index(Path("v.mp4"), "de") == 1
    with probe(stream("eng", default=1), stream("por")):
        assert ffmpeg.select_audio_stream_index(Path("v.mp4"), "en_us") == 0


def test_silent_source_has_no_audio_stream():
    with probe():
        assert ffmpeg.select_audio_stream_index(Path("v.mp4"), "") is None


@pytest.mark.parametrize(("raw", "expected"), [(None, ""), ("Auto", ""), ("pt-BR", "pt_br"), ("es", "es")])
def test_transcription_language_normalization(raw, expected):
    assert Config._normalize_transcription_language(raw) == expected


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is not installed")
def test_multi_range_render_keeps_the_selected_track(tmp_path):
    # Track 0 is silent commentary; track 1 is the default dialogue tone.
    source = tmp_path / "source.mkv"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=64x64:d=4",
            "-f", "lavfi", "-i", "anullsrc=r=16000:cl=stereo:d=4",
            "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:d=4",
            "-map", "0:v", "-map", "1:a", "-map", "2:a", "-shortest",
            "-disposition:a:0", "comment", "-disposition:a:1", "default",
            "-c:v", "libx264", "-c:a", "aac", str(source),
        ],
        check=True,
    )
    output = tmp_path / "out.mp4"
    assert ffmpeg.render_source_ranges_ffmpeg(source, [(0.0, 1.0), (2.0, 3.0)], output)
    volume = subprocess.run(
        ["ffmpeg", "-i", str(output), "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True,
    ).stderr
    mean_volume = float(volume.split("mean_volume:")[1].split("dB")[0])
    assert mean_volume > -40
