"""YouTube source validation and cleanup at the processing-service boundary."""

import asyncio
import logging
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


@pytest.mark.asyncio
@pytest.mark.parametrize("transient", [False, True])
async def test_section_failure_uses_the_existing_backup_order_once(tmp_path, monkeypatch, caplog, transient):
    """SSL retries happen inside the section download. The full-file fallback
    stays the existing provider list, once, and still skips a second proxy
    download. A bot check takes that same list and does not add an Apify call."""
    from yt_dlp.utils import DownloadError

    from src.config import Config
    from src.observability import JsonLogFormatter

    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = "http://user-session-{session}:super-secret-password@gw.example:823"
    monkeypatch.setattr(module, "get_config", lambda: config)
    if transient:
        failure = DownloadError(
            "ERROR: [SSL] record layer failure (_ssl.c:2590). Giving up after 1 retries "
            "proxy=http://user:super-secret-password@gw.example:823 "
            "url=https://rr.googlevideo.com/videoplayback?sig=secret"
        )
    else:
        failure = RuntimeError("Sign in to confirm you're not a bot")
    monkeypatch.setattr(module, "materialize_clip_source", lambda *a, **kw: (_ for _ in ()).throw(failure))
    captured = {}

    def full(*_args, **kwargs):
        captured.update(kwargs)
        return None

    monkeypatch.setattr(module, "resolve_full_youtube_download", full)
    caplog.set_level(logging.WARNING)
    assert await VideoService.download_video(URL, ranges=[(10, 20)], task_id="task-1") is None
    assert captured["skip_proxy"] is True
    matched = [record for record in caplog.records if getattr(record, "youtube_provider_fallback", "")]
    assert matched
    assert matched[-1].youtube_provider_fallback == "rapidapi,apify,yt_dlp"
    formatted = JsonLogFormatter().format(matched[-1])
    assert "super-secret-password" not in formatted
    assert "sig=secret" not in formatted
    assert "yt_dlp_proxy" not in matched[-1].youtube_provider_fallback


@pytest.mark.asyncio
async def test_age_restricted_source_fails_before_any_download(apify_setup, monkeypatch):
    from src.youtube_proxy import YouTubeAgeRestrictedError

    async def info(*_args, **_kwargs):
        return {"duration": 1609, "age_restricted": True}

    async def download(*_args, **_kwargs):
        raise AssertionError("no provider should be tried for an age-restricted video")

    monkeypatch.setattr(module, "async_get_youtube_video_info", info)
    monkeypatch.setattr(VideoService, "download_video", download)

    with pytest.raises(YouTubeAgeRestrictedError):
        await VideoService.process_video_complete(URL, "youtube")
