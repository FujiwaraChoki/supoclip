"""A private YouTube video is a permanent 422, not a "try again shortly" 503."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from yt_dlp.utils import DownloadError

from src import youtube_utils
from src.api.routes import tasks
from src.config import Config, set_config_override
from src.services.video_service import VideoService
from src.youtube_proxy import YouTubeVideoPrivateError, is_private_video_error

URL = "https://www.youtube.com/watch?v=abcdefghijk"
PRIVATE = (
    "ERROR: [youtube] abcdefghijk: Private video. "
    "Sign in if you've been granted access to this video"
)
BOT_CHECK = (
    "ERROR: [youtube] abcdefghijk: Sign in to confirm you’re not a bot. "
    "Use --cookies-from-browser or --cookies for the authentication."
)


@pytest.fixture
def config():
    config = Config()
    config.youtube_metadata_provider = "yt_dlp"
    set_config_override(config)
    yield config
    set_config_override(None)


@pytest.fixture
def creation(monkeypatch, config):
    billing = {"max_youtube_duration_seconds": 5400, "max_upload_duration_seconds": 5400}
    monkeypatch.setattr(tasks.BillingService, "assert_can_create_task", AsyncMock(return_value=billing))
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    monkeypatch.setattr(tasks, "_save_task_source_metadata", AsyncMock())
    service = SimpleNamespace(
        video_service=VideoService(),
        create_task_with_source=AsyncMock(return_value="task-1"),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
    queue = SimpleNamespace(enqueue_processing_job=AsyncMock(return_value="job-1"))
    request = SimpleNamespace(
        json=AsyncMock(return_value={"source": {"url": URL}}),
        app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue)),
    )
    return request, service, queue


def _metadata_fails(monkeypatch, ytdlp_error: Exception):
    # The Data API never lists a private video, so it answers with no results.
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", Mock(side_effect=ytdlp_error))
    monkeypatch.setattr(
        youtube_utils,
        "_fetch_video_info_with_youtube_data_api",
        Mock(side_effect=ValueError("No YouTube Data API results for video abcdefghijk")),
    )


@pytest.mark.parametrize(
    "message",
    [PRIVATE, "ERROR: [youtube] abcdefghijk: This video is private"],
)
def test_private_video_messages_are_recognised(message):
    assert is_private_video_error(DownloadError(message))


@pytest.mark.parametrize(
    "message",
    [
        BOT_CHECK,
        "ERROR: [youtube] abcdefghijk: Video unavailable. This content isn't available, try again later.",
        "Read timed out. (read timeout=30)",
        "Unable to connect to proxy: Tunnel connection failed: 407 Proxy Authentication Required",
        "HTTP Error 429: Too Many Requests",
    ],
)
def test_transient_failures_are_not_private(message):
    assert not is_private_video_error(DownloadError(message))


def test_metadata_reports_a_private_video_after_both_providers_fail(config, monkeypatch):
    _metadata_fails(monkeypatch, DownloadError(PRIVATE))
    with pytest.raises(YouTubeVideoPrivateError):
        youtube_utils.get_youtube_video_info(URL)


@pytest.mark.asyncio
async def test_private_video_is_rejected_with_422_and_charges_nothing(monkeypatch, creation):
    request, service, queue = creation
    _metadata_fails(monkeypatch, DownloadError(PRIVATE))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "YOUTUBE_VIDEO_PRIVATE"
    assert "private" in error.value.detail["message"]
    assert "try again" not in error.value.detail["message"].lower()
    assert "No minutes were used" in error.value.detail["message"]
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_bot_check_stays_a_retryable_503(monkeypatch, creation):
    request, service, queue = creation
    _metadata_fails(monkeypatch, DownloadError(BOT_CHECK))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "VIDEO_METADATA_UNAVAILABLE"
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()
