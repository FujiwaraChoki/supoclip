"""Transcription audio extraction: no partial cache hits, no undecodable uploads."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from src.media import transcription


class Result:
    def __init__(self, returncode: int):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def test_failed_extraction_raises_and_leaves_no_cache(tmp_path, monkeypatch):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")

    def fake_run(command, timeout=900):
        # ffmpeg wrote some frames, then gave up on the rest of the audio.
        Path(command[-1]).write_bytes(b"partial")
        return Result(183)

    monkeypatch.setattr(transcription, "run_ffmpeg_command", fake_run)
    monkeypatch.setattr(transcription, "select_audio_stream_index", lambda _path: 0)
    with pytest.raises(RuntimeError, match="Could not decode"):
        transcription._prepare_audio_for_transcription(video_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source.mp4"]


def test_interrupted_extraction_is_not_reused(tmp_path, monkeypatch):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")
    calls = []

    def fake_run(command, timeout=900):
        calls.append(command)
        Path(command[-1]).write_bytes(b"partial")
        if len(calls) == 1:
            raise subprocess.TimeoutExpired(command, timeout)
        Path(command[-1]).write_bytes(b"audio")
        return Result(0)

    monkeypatch.setattr(transcription, "run_ffmpeg_command", fake_run)
    monkeypatch.setattr(transcription, "select_audio_stream_index", lambda _path: 0)
    with pytest.raises(subprocess.TimeoutExpired):
        transcription._prepare_audio_for_transcription(video_path)
    audio_path = transcription._prepare_audio_for_transcription(video_path)
    assert len(calls) == 2
    assert audio_path.name == "source.transcription.mp3"
    assert audio_path.read_bytes() == b"audio"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["source.mp4", "source.transcription.mp3"]


def test_missing_ffmpeg_still_falls_back_to_the_source(tmp_path, monkeypatch):
    video_path = tmp_path / "source.mp4"
    video_path.write_bytes(b"video")

    def missing(command, timeout=900):
        raise FileNotFoundError("ffmpeg")

    monkeypatch.setattr(transcription, "run_ffmpeg_command", missing)
    monkeypatch.setattr(transcription, "select_audio_stream_index", lambda _path: None)
    assert transcription._prepare_audio_for_transcription(video_path) == video_path


def test_undecodable_audio_is_never_uploaded(tmp_path, monkeypatch):
    if not shutil.which("ffmpeg"):
        pytest.skip("ffmpeg is required")
    good = tmp_path / "good.aac"
    subprocess.run(
        ["ffmpeg", "-y", "-nostdin", "-f", "lavfi", "-i", "sine=frequency=440:duration=5", "-c:a", "aac", "-f", "adts", str(good)],
        check=True, capture_output=True,
    )
    data = bytearray(good.read_bytes())
    # Keep every ADTS header so the file still parses; scramble the frames.
    offset, state = 0, 1
    while offset + 7 <= len(data):
        length = ((data[offset + 3] & 0x03) << 11) | (data[offset + 4] << 3) | (data[offset + 5] >> 5)
        for index in range(offset + 7, min(offset + length, len(data))):
            state = (state * 1103515245 + 12345) & 0x7FFFFFFF
            data[index] = state & 0xFF
        offset += max(length, 7)
    source = tmp_path / "source.aac"
    source.write_bytes(bytes(data))

    class Transcriber:
        uploads: list[str] = []

        def upload_file(self, path):
            self.uploads.append(path)
            return "https://upload.test/audio"

    transcriber = Transcriber()
    with pytest.raises(RuntimeError, match="Could not decode"):
        media_path = transcription._prepare_audio_for_transcription(source)
        transcription._submit_and_wait_for_assemblyai_transcript(transcriber, media_path, None, 5)
    assert transcriber.uploads == []
    assert not list(tmp_path.glob("*.transcription.mp3*"))

    # A clean source still extracts to the cached sidecar.
    assert transcription._prepare_audio_for_transcription(good).name == "good.transcription.mp3"
