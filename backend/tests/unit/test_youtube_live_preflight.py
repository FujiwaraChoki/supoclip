"""Live, upcoming and zero-duration YouTube sources at task creation.

An active broadcast reports duration P0D. It is refused with a clear,
non-retryable message instead of the generic "try again" 503. A finished
video that one provider reports as zero gets one try on the other provider;
if neither can verify a length, creation still fails closed.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src import youtube_utils
from src.api.routes import tasks
from src.config import Config, set_config_override
from src.services.video_service import VideoService
from src.youtube_utils import (
    YOUTUBE_METADATA_PROVIDER_DATA_API,
    YOUTUBE_METADATA_PROVIDER_YTDLP,
    _fetch_video_info_with_youtube_data_api,
    get_youtube_video_info,
)

URL = "https://www.youtube.com/watch?v=abcdefghijk"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


def data_api_item(duration="P0D", broadcast="live", live_details=None):
    item = {
        "id": "abcdefghijk",
        "snippet": {"title": "Stream", "liveBroadcastContent": broadcast, "thumbnails": {}},
        "contentDetails": {"duration": duration},
        "statistics": {},
    }
    if live_details is not None:
        item["liveStreamingDetails"] = live_details
    return {"items": [item]}


@pytest.fixture
def config():
    config = Config()
    config.youtube_data_api_key = "youtube-key"
    config.youtube_proxy_url = ""
    set_config_override(config)
    yield config
    set_config_override(None)


@pytest.fixture
def creation(monkeypatch):
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
        json=AsyncMock(return_value={"source": {"url": "https://youtube.com/live/abcdefghijk"}}),
        app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue)),
    )
    return request, service, queue


def assert_nothing_charged(service, queue):
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()
    # Only the up-front entitlement check ran; the duration-sized one never did.
    assert all(len(call.args) == 1 for call in tasks.BillingService.assert_can_create_task.await_args_list)


# Provider normalization


@pytest.mark.parametrize(
    "broadcast,live_details,expected",
    [
        ("live", None, "is_live"),
        ("upcoming", None, "is_upcoming"),
        ("none", {"actualEndTime": "2026-11-10T10:00:00Z"}, "was_live"),
        ("none", None, "not_live"),
        ("bogus", None, None),
    ],
)
def test_data_api_reports_live_status(config, monkeypatch, broadcast, live_details, expected):
    monkeypatch.setattr(
        youtube_utils.requests, "get",
        lambda *_a, **_k: FakeResponse(data_api_item("P0D", broadcast, live_details)),
    )
    result = _fetch_video_info_with_youtube_data_api(URL)
    assert result["duration"] == 0
    assert result["live_status"] == expected


@pytest.mark.parametrize(
    "info,expected",
    [
        ({"live_status": "is_live"}, "is_live"),
        ({"live_status": "is_upcoming"}, "is_upcoming"),
        ({"live_status": "post_live"}, "post_live"),
        ({"is_live": True}, "is_live"),
        ({"live_status": "<script>"}, None),
        ({}, None),
    ],
)
def test_ytdlp_live_status_is_validated(info, expected):
    assert youtube_utils._ytdlp_live_status(info) == expected


# Provider fallback


def test_active_live_from_primary_returns_without_fallback(config, monkeypatch):
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_DATA_API
    monkeypatch.setattr(
        youtube_utils, "_fetch_video_info_with_youtube_data_api",
        lambda _url, **_k: {"duration": 0, "live_status": "is_live"},
    )
    ytdlp = Mock()
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", ytdlp)
    assert get_youtube_video_info(URL)["live_status"] == "is_live"
    ytdlp.assert_not_called()


def test_zero_duration_falls_back_to_other_provider(config, monkeypatch):
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(
        youtube_utils, "_fetch_video_info_with_ytdlp",
        lambda _url, **_k: {"duration": 0, "live_status": None},
    )
    monkeypatch.setattr(
        youtube_utils, "_fetch_video_info_with_youtube_data_api",
        lambda _url, **_k: {"duration": 754, "live_status": "not_live"},
    )
    assert get_youtube_video_info(URL)["duration"] == 754


def test_zero_duration_fallback_finds_live_status(config, monkeypatch):
    # The incident: yt-dlp gave no length; the Data API said the stream is live.
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(
        youtube_utils, "_fetch_video_info_with_ytdlp",
        lambda _url, **_k: {"duration": None, "live_status": None},
    )
    monkeypatch.setattr(
        youtube_utils.requests, "get",
        lambda *_a, **_k: FakeResponse(data_api_item("P0D", "live")),
    )
    assert get_youtube_video_info(URL)["live_status"] == "is_live"


@pytest.mark.parametrize("secondary", ["zero", "error"])
def test_zero_duration_with_no_better_fallback_returns_unverified(config, monkeypatch, secondary):
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(
        youtube_utils, "_fetch_video_info_with_ytdlp",
        lambda _url, **_k: {"duration": 0, "title": "first", "live_status": None},
    )
    data_api = Mock(
        return_value={"duration": 0, "title": "second", "live_status": None}
        if secondary == "zero" else None,
        side_effect=None if secondary == "zero" else RuntimeError("quota"),
    )
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_youtube_data_api", data_api)
    result = get_youtube_video_info(URL)
    # Bounded: each provider was asked once, and nothing was downloaded.
    data_api.assert_called_once()
    assert result["title"] == "first"
    assert not youtube_utils.has_verified_duration(result)


# Task creation


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "metadata,phrase",
    [
        ({"duration": 0, "live_status": "is_live"}, "still broadcasting"),
        # A provider can attach a length to a live stream; it is still refused.
        ({"duration": 3600, "live_status": "is_live"}, "still broadcasting"),
        ({"duration": None, "live_status": "is_upcoming"}, "hasn't started yet"),
        ({"duration": 0, "live_status": "post_live"}, "still processing"),
    ],
)
async def test_unfinished_broadcast_is_a_clear_422(monkeypatch, creation, metadata, phrase):
    request, service, queue = creation
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value=metadata))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 422
    detail = error.value.detail
    assert detail["code"] == "VIDEO_IS_LIVE"
    assert detail["live_status"] == metadata["live_status"]
    assert phrase in detail["message"]
    assert "No minutes were used" in detail["message"]
    assert_nothing_charged(service, queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("live_status", [None, "not_live", "was_live", "unexpected"])
async def test_zero_duration_non_live_still_fails_closed(monkeypatch, creation, live_status):
    request, service, queue = creation
    monkeypatch.setattr(
        tasks, "async_get_youtube_video_info",
        AsyncMock(return_value={"duration": 0, "live_status": live_status}),
    )
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "VIDEO_METADATA_UNAVAILABLE"
    assert_nothing_charged(service, queue)


@pytest.mark.asyncio
@pytest.mark.parametrize("live_status", ["was_live", "post_live", "not_live"])
async def test_finished_broadcast_with_length_is_billed_normally(monkeypatch, creation, live_status):
    request, service, queue = creation
    monkeypatch.setattr(
        tasks, "async_get_youtube_video_info",
        AsyncMock(return_value={"duration": 1800, "title": "Replay", "live_status": live_status}),
    )
    result = await tasks.create_task(request, db=None)
    assert result["task_id"] == "task-1"
    assert service.create_task_with_source.await_args.kwargs["source_duration_seconds"] == 1800
    assert tasks.BillingService.assert_can_create_task.await_args.args[-1] == 1800
    queue.enqueue_processing_job.assert_awaited_once()


@pytest.mark.asyncio
async def test_finished_broadcast_over_plan_limit_is_still_rejected(monkeypatch, creation):
    request, service, queue = creation
    monkeypatch.setattr(
        tasks, "async_get_youtube_video_info",
        AsyncMock(return_value={"duration": 3 * 3600, "live_status": "was_live"}),
    )
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.detail["code"] == "VIDEO_TOO_LONG"
    assert_nothing_charged(service, queue)


@pytest.mark.asyncio
async def test_incident_path_end_to_end(config, monkeypatch, creation):
    """yt-dlp returns no length, the Data API says live: a 422, not a retry loop."""
    request, service, queue = creation
    config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(
        youtube_utils, "_fetch_video_info_with_ytdlp",
        lambda _url, **_k: {"duration": 0, "live_status": None},
    )
    monkeypatch.setattr(
        youtube_utils.requests, "get",
        lambda *_a, **_k: FakeResponse(data_api_item("P0D", "live")),
    )
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "VIDEO_IS_LIVE"
    assert_nothing_charged(service, queue)
