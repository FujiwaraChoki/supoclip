"""VideoScale source validation and cleanup at the processing-service boundary."""

import asyncio
from types import SimpleNamespace

import pytest

from src import youtube_utils
from src.services import video_service as module
from src.services.video_service import VideoService


VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.fixture
def videoscale_setup(monkeypatch, tmp_path):
    config = SimpleNamespace(
        temp_dir=str(tmp_path),
        max_video_duration=10800,
        youtube_download_provider="videoscale",
    )
    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(youtube_utils, "get_config", lambda: config)
    return tmp_path


@pytest.mark.asyncio
async def test_missing_metadata_refuses_paid_download(videoscale_setup, monkeypatch):
    async def no_info(*_args, **_kwargs):
        return None

    async def unexpected_download(*_args, **_kwargs):
        pytest.fail("download must not start without verified source duration")

    monkeypatch.setattr(module, "async_get_youtube_video_info", no_info)
    monkeypatch.setattr(VideoService, "download_video", unexpected_download)

    with pytest.raises(ValueError, match="duration could not be verified"):
        await VideoService.process_video_complete(URL, "youtube")


@pytest.mark.asyncio
async def test_short_file_fails_before_transcription_and_is_cleaned(videoscale_setup, monkeypatch):
    source_dir = videoscale_setup / f"videoscale-{VIDEO_ID}-test"
    source_dir.mkdir()
    source = source_dir / f"{VIDEO_ID}.mp4"
    source.write_bytes(b"short")

    async def info(*_args, **_kwargs):
        return {"duration": 600}

    async def download(*_args, **_kwargs):
        return source

    async def unexpected_transcript(*_args, **_kwargs):
        pytest.fail("truncated source must fail before transcription")

    monkeypatch.setattr(module, "async_get_youtube_video_info", info)
    monkeypatch.setattr(VideoService, "download_video", download)
    monkeypatch.setattr(VideoService, "_get_file_duration", lambda _path: 2)
    monkeypatch.setattr(VideoService, "generate_transcript", unexpected_transcript)

    with pytest.raises(ValueError, match="incomplete"):
        await VideoService.process_video_complete(URL, "youtube")
    assert not source.exists()
    assert not source_dir.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("analysis failed"), asyncio.CancelledError()])
async def test_later_failure_cleans_downloaded_source(videoscale_setup, monkeypatch, failure):
    source_dir = videoscale_setup / f"videoscale-{VIDEO_ID}-test"
    source_dir.mkdir()
    source = source_dir / f"{VIDEO_ID}.mp4"
    source.write_bytes(b"complete")

    async def info(*_args, **_kwargs):
        return {"duration": 600}

    async def download(*_args, **_kwargs):
        return source

    async def failed_transcript(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(module, "async_get_youtube_video_info", info)
    monkeypatch.setattr(VideoService, "download_video", download)
    monkeypatch.setattr(VideoService, "_get_file_duration", lambda _path: 600)
    monkeypatch.setattr(VideoService, "generate_transcript", failed_transcript)

    with pytest.raises(type(failure)):
        await VideoService.process_video_complete(URL, "youtube")
    assert not source.exists()
    assert not source_dir.exists()
