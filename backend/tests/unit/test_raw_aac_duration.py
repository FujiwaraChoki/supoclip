"""HLS audio arrives as raw ADTS AAC, whose length ffprobe can only guess.

A 26-minute video once read as 24:52, so the section download stopped there
and a series lost its last part.
"""

import shutil
import subprocess

import pytest

from src.media.source_limits import contain_raw_aac, probe_media_duration
from src.services.video_service import VideoService

requires_ffmpeg = pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")


def _raw_aac(path):
    # Loud noise then silence: a bitrate guess from the start reads it far too short.
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "anoisesrc=d=20:a=0.5",
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
            "-filter_complex", "[1]atrim=0:40[s];[0][s]concat=n=2:v=0:a=1",
            "-c:a", "aac", "-b:a", "128k", "-f", "adts", str(path),
        ],
        check=True,
    )
    return path


def _format(path):
    return subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=format_name,duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


@requires_ffmpeg
def test_raw_aac_duration_is_read_from_packets(tmp_path):
    # Named like the cache file: the extension says mp4, the bytes are ADTS.
    path = _raw_aac(tmp_path / "audio-original-v2.mp4")
    name, guessed = _format(path).rsplit(",", 1)
    assert name == "aac" and float(guessed) < 50  # the guess this guards against

    assert probe_media_duration(path) == pytest.approx(60, abs=0.2)
    assert VideoService._get_file_duration(path) == pytest.approx(60, abs=0.2)


@requires_ffmpeg
def test_raw_aac_is_contained_in_place(tmp_path):
    path = _raw_aac(tmp_path / "audio.mp4")
    assert contain_raw_aac(path) == path
    name, duration = _format(path).replace("\"", "").rsplit(",", 1)
    assert name.startswith("mov,mp4") and float(duration) == pytest.approx(60, abs=0.2)
    assert [p.name for p in tmp_path.iterdir()] == ["audio.mp4"]


@requires_ffmpeg
def test_other_media_is_left_alone(tmp_path):
    path = tmp_path / "audio.m4a"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=d=3", "-c:a", "aac", str(path)],
        check=True,
    )
    before = path.read_bytes()
    assert contain_raw_aac(path) == path
    assert path.read_bytes() == before
    assert probe_media_duration(path) == pytest.approx(3, abs=0.1)


def test_unreadable_media_has_no_duration(tmp_path):
    path = tmp_path / "broken.mp4"
    path.write_bytes(b"not media")
    assert probe_media_duration(path) is None
    assert contain_raw_aac(path) == path
