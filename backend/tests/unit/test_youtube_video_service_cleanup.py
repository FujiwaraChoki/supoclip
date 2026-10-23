"""YouTube source validation and cleanup at the processing-service boundary."""

import asyncio
from types import SimpleNamespace

import pytest

from src import youtube_utils
from src.services import video_service as module
from src.services.video_service import VideoService


VIDEO_ID = "abcdefghijk"
URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


@pytest.fixture
def apify_setup(monkeypatch, tmp_path):
    config = SimpleNamespace(
        temp_dir=str(tmp_path),
        max_video_duration=10800,
        youtube_download_provider="apify",
    )
    monkeypatch.setattr(module, "get_config", lambda: config)
    monkeypatch.setattr(youtube_utils, "get_config", lambda: config)
    return tmp_path


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError("analysis failed"), asyncio.CancelledError()])
async def test_later_failure_cleans_downloaded_source(apify_setup, monkeypatch, failure):
    source_dir = apify_setup / f"apify-{VIDEO_ID}-test"
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


@pytest.mark.asyncio
@pytest.mark.parametrize('unsupported', [True, False])
async def test_only_unsupported_sections_retry_full_proxy(tmp_path, monkeypatch, unsupported):
    from src.youtube_proxy import PartialDownloadUnavailable
    from src.config import Config
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr(module, 'get_config', lambda: config)
    failure = PartialDownloadUnavailable('unsupported playlist') if unsupported else RuntimeError('proxy blocked')
    monkeypatch.setattr(module, 'materialize_clip_source', lambda *a, **kw: (_ for _ in ()).throw(failure))
    captured = {}
    def full(*a, **kwargs):
        captured.update(kwargs)
        return None
    monkeypatch.setattr(module, 'resolve_full_youtube_download', full)
    assert await VideoService.download_video(URL, ranges=[(10, 20)]) is None
    assert captured['skip_proxy'] is not unsupported
