"""Video-only uploads fail before charge; video-only downloads try the next provider."""

import shutil
import subprocess
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.errors import MissingAudioError
from src.media.ffmpeg import audio_stream_present, parse_ffprobe_has_audio
from src.services.task_service import classify_task_error


def test_parse_ffprobe_json_reports_audio_streams_only():
    silent = {"streams": [{"codec_type": "video", "width": 1920, "height": 1080}]}
    audible = {
        "streams": [
            {"codec_type": "video", "width": 320, "height": 240},
            {"codec_type": "audio", "sample_rate": "48000"},
        ]
    }
    assert parse_ffprobe_has_audio(silent) is False
    assert parse_ffprobe_has_audio(audible) is True
    assert parse_ffprobe_has_audio({"streams": []}) is False
    # Side data and extra streams stay beside codec_type, which is why this is
    # JSON rather than the csv writer used for a single column.
    with_side_data = {
        "streams": [
            {
                "codec_type": "video",
                "side_data_list": [{"side_data_type": "Display Matrix", "rotation": -90}],
            }
        ]
    }
    assert parse_ffprobe_has_audio(with_side_data) is False


def test_unreadable_probe_is_not_a_silent_video(tmp_path):
    garbage = tmp_path / "not-a-video.mp4"
    garbage.write_bytes(b"\x00\x00not a media file")
    assert audio_stream_present(garbage) is None
    assert audio_stream_present(tmp_path / "missing.mp4") is None


@pytest.mark.skipif(shutil.which("ffmpeg") is None or shutil.which("ffprobe") is None, reason="ffmpeg is not installed")
def test_ffprobe_json_detects_a_video_only_lavf_file(tmp_path):
    silent = tmp_path / "silent.mp4"
    audible = tmp_path / "talking.mp4"
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
            "-c:v", "libx264", "-an", str(silent),
        ],
        check=True,
        timeout=60,
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error",
            "-f", "lavfi", "-i", "color=c=black:s=320x240:d=1",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
            "-c:v", "libx264", "-c:a", "aac", "-shortest", str(audible),
        ],
        check=True,
        timeout=60,
    )
    assert audio_stream_present(silent) is False
    assert audio_stream_present(audible) is True
    # Lavf remux of a video-only encode, the shape of a YouTube DASH download
    # that was copied into a new MP4 without an audio stream.
    remuxed = tmp_path / "remuxed.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(silent), "-c", "copy", str(remuxed)],
        check=True,
        timeout=60,
    )
    assert b"Lavf" in remuxed.read_bytes()
    assert audio_stream_present(remuxed) is False


def test_missing_audio_is_not_a_download_or_transcription_error():
    error = MissingAudioError()
    assert "youtube" in str(error).lower()
    assert classify_task_error(error) == "no_audio"
    assert classify_task_error(Exception("Transcription failed: No audio stream")) == "transcription_error"
    assert classify_task_error(Exception("Failed to download video: file has no audio stream")) == "download_error"


def test_video_only_youtube_download_tries_the_next_provider(tmp_path, monkeypatch):
    from src.config import Config, set_config_override
    from src.youtube_utils import YOUTUBE_DOWNLOAD_PROVIDER_YTDLP, download_youtube_video

    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = "apify-token"
    config.youtube_download_provider = YOUTUBE_DOWNLOAD_PROVIDER_YTDLP
    silent = tmp_path / "abcdefghijk.webm"
    audible = tmp_path / "abcdefghijk.mp4"
    probed = []

    def ytdlp(*_args, **_kwargs):
        silent.write_bytes(b"video-only")
        return silent

    def apify(*_args, **_kwargs):
        audible.write_bytes(b"with-audio")
        return audible

    def present(path):
        probed.append(Path(path).name)
        return path == audible

    set_config_override(config)
    try:
        monkeypatch.setattr("src.youtube_utils._download_youtube_video_with_ytdlp", ytdlp)
        monkeypatch.setattr("src.youtube_utils.download_youtube_video_with_apify", apify)
        monkeypatch.setattr("src.youtube_utils.audio_stream_present", present)
        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")
    finally:
        set_config_override(None)

    assert result == audible
    assert not silent.exists()
    assert probed[0] == silent.name


def test_unreadable_youtube_download_is_not_discarded(tmp_path, monkeypatch):
    from src.config import Config, set_config_override
    from src.youtube_utils import download_youtube_video

    config = Config()
    config.temp_dir = str(tmp_path)
    config.apify_api_token = None
    kept = tmp_path / "abcdefghijk.mp4"

    def ytdlp(*_args, **_kwargs):
        kept.write_bytes(b"unreadable")
        return kept

    set_config_override(config)
    try:
        monkeypatch.setattr("src.youtube_utils._download_youtube_video_with_ytdlp", ytdlp)
        monkeypatch.setattr("src.youtube_utils.audio_stream_present", lambda _path: None)
        result = download_youtube_video("https://www.youtube.com/watch?v=abcdefghijk")
    finally:
        set_config_override(None)

    assert result == kept
    assert kept.is_file()


def test_section_download_without_audio_uses_the_full_file_fallback(tmp_path, monkeypatch):
    from src.youtube_proxy import AudioDownloadForbidden
    from src.youtube_source import download_analysis_source

    section = tmp_path / "section.mp4"
    section.write_bytes(b"video-only")
    full = tmp_path / "full.mp4"
    full.write_bytes(b"with-audio")

    def refuse(*_args, **_kwargs):
        raise AudioDownloadForbidden("audio forbidden")

    monkeypatch.setattr("src.youtube_source.download_audio_for_job", refuse)
    monkeypatch.setattr("src.youtube_source._full_timeline_via_sections", lambda *_args, **_kwargs: section)
    monkeypatch.setattr(
        "src.youtube_source.audio_stream_present",
        lambda path: False if Path(path) == section else True,
    )
    seen = []

    def download_full():
        seen.append("full")
        return full

    path, audio_first = download_analysis_source(
        "https://www.youtube.com/watch?v=abcdefghijk",
        "abcdefghijk",
        duration=30,
        download_full=download_full,
    )
    assert path == full
    assert audio_first is False
    assert seen == ["full"]


@pytest.mark.asyncio
async def test_worker_does_not_retry_a_missing_audio_upload(monkeypatch):
    from src.workers import tasks as worker_tasks

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *_args):
            return False

    class Service:
        def __init__(self, _db):
            pass

        async def process_task(self, **_kwargs):
            raise MissingAudioError()

    class Progress:
        def __init__(self, _redis, _task_id):
            self.errors = []

        async def error(self, message):
            self.errors.append(message)

    redis = type("Redis", (), {})()
    redis.set = AsyncMock()
    redis.sadd = AsyncMock()
    monkeypatch.setattr("src.database.AsyncSessionLocal", lambda: Session())
    monkeypatch.setattr("src.runtime_settings.load_runtime_settings_cache", AsyncMock())
    monkeypatch.setattr("src.services.task_service.TaskService", Service)
    monkeypatch.setattr("src.workers.progress.ProgressTracker", Progress)

    result = await worker_tasks.process_video_task(
        {"redis": redis, "job_try": 1},
        "task-1",
        "upload://silent.mp4",
        "video_url",
        "user-1",
    )

    assert result == {"task_id": "task-1", "status": "error", "error_code": "no_audio"}
    redis.set.assert_not_awaited()
    redis.sadd.assert_not_awaited()
