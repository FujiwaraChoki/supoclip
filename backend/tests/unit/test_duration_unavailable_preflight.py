"""Task creation when a source's length can't be verified.

An in-progress livestream reports a length of 0 (YouTube P0D) or none at all
(Twitch/Kick VODs of a running stream). The preflight doesn't try to detect
live streams: after the bounded metadata fallback, a zero or missing length
gets a 422 saying the video *may* be a livestream in progress. Provider and
network failures keep their own errors. Either way no task is created and no
minutes are charged.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException

from src import stream_source, youtube_utils
from src.api.routes import tasks
from src.config import Config, set_config_override
from src.services.video_service import VideoService
from src.youtube_proxy import YouTubeGeoRestrictedError
from src.youtube_utils import (
    YOUTUBE_METADATA_PROVIDER_DATA_API,
    YOUTUBE_METADATA_PROVIDER_YTDLP,
    get_youtube_video_info,
)

YOUTUBE_URL = "https://www.youtube.com/watch?v=abcdefghijk"
TWITCH_VOD = "https://www.twitch.tv/videos/123456"
KICK_VOD = "https://kick.com/creator/videos/12345678-1234-1234-1234-123456789abc"


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


def assert_maybe_live_message(detail, platform):
    assert detail["code"] == "VIDEO_DURATION_UNAVAILABLE"
    message = detail["message"]
    assert f"couldn't verify this {platform} video's length" in message
    assert "may be a livestream that's still in progress" in message
    assert "isn't supported yet" in message
    assert "try again" in message and "completed recording" in message
    assert "No minutes were used" in message


@pytest.fixture
def youtube_config():
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
    body = {"source": {"url": YOUTUBE_URL}}
    request = SimpleNamespace(
        json=AsyncMock(side_effect=lambda: body),
        app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue)),
    )
    return SimpleNamespace(request=request, body=body, service=service, queue=queue)


def stream_info(*, returns=None, raises=None):
    """A named stand-in: run_in_thread logs the function's __name__."""
    def fetch_stream_info(*_args, **_kwargs):
        if raises is not None:
            raise raises
        return returns
    return fetch_stream_info


def assert_nothing_charged(creation):
    creation.service.create_task_with_source.assert_not_awaited()
    creation.queue.enqueue_processing_job.assert_not_awaited()
    # Only the up-front entitlement check ran, never the one sized to a duration.
    assert all(len(call.args) == 1 for call in tasks.BillingService.assert_can_create_task.await_args_list)


# ------------------------------------------------- YouTube provider fallback


def test_youtube_zero_duration_falls_back_to_other_provider(youtube_config, monkeypatch):
    youtube_config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", lambda _url, **_k: {"duration": 0})
    monkeypatch.setattr(
        youtube_utils, "_fetch_video_info_with_youtube_data_api",
        lambda _url, **_k: {"duration": 754, "title": "Finished"},
    )
    assert get_youtube_video_info(YOUTUBE_URL)["duration"] == 754


def test_youtube_valid_primary_does_not_call_fallback(youtube_config, monkeypatch):
    youtube_config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", lambda _url, **_k: {"duration": 90})
    data_api = Mock()
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_youtube_data_api", data_api)
    assert get_youtube_video_info(YOUTUBE_URL)["duration"] == 90
    data_api.assert_not_called()


@pytest.mark.parametrize("secondary", ["zero", "error"])
def test_youtube_zero_duration_without_better_fallback_is_returned_unverified(youtube_config, monkeypatch, secondary):
    youtube_config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(
        youtube_utils, "_fetch_video_info_with_ytdlp",
        lambda _url, **_k: {"duration": 0, "title": "first"},
    )
    data_api = (
        Mock(return_value={"duration": None, "title": "second"})
        if secondary == "zero"
        else Mock(side_effect=RuntimeError("quota"))
    )
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_youtube_data_api", data_api)
    result = get_youtube_video_info(YOUTUBE_URL)
    # Bounded: one try per provider, metadata only.
    data_api.assert_called_once()
    assert result["title"] == "first"
    assert not youtube_utils.has_verified_duration(result)


def test_youtube_all_providers_failing_still_returns_none(youtube_config, monkeypatch):
    youtube_config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", Mock(side_effect=RuntimeError("down")))
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_youtube_data_api", Mock(side_effect=RuntimeError("down")))
    assert get_youtube_video_info(YOUTUBE_URL) is None


# ------------------------------------------------------ YouTube task creation


@pytest.mark.asyncio
async def test_youtube_active_live_stream_gets_maybe_live_message(youtube_config, monkeypatch, creation):
    """The incident: yt-dlp gives no length, the Data API reports P0D."""
    youtube_config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_YTDLP
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", lambda _url, **_k: {"duration": None})
    monkeypatch.setattr(
        youtube_utils.requests, "get",
        lambda *_a, **_k: FakeResponse({"items": [{
            "id": "abcdefghijk",
            "snippet": {"title": "Live now", "liveBroadcastContent": "live", "thumbnails": {}},
            "contentDetails": {"duration": "P0D"},
            "statistics": {},
        }]}),
    )
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)
    assert error.value.status_code == 422
    assert_maybe_live_message(error.value.detail, "YouTube")
    assert_nothing_charged(creation)


@pytest.mark.asyncio
async def test_youtube_data_api_item_without_duration_gets_maybe_live_message(youtube_config, monkeypatch, creation):
    youtube_config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_DATA_API
    item = {"id": "abcdefghijk", "snippet": {"title": "Video"}, "contentDetails": {}}
    monkeypatch.setattr(youtube_utils.requests, "get", Mock(return_value=FakeResponse({"items": [item]})))
    fallback = Mock(side_effect=RuntimeError("network unavailable"))
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", fallback)

    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)

    fallback.assert_called_once()
    assert error.value.status_code == 422
    assert_maybe_live_message(error.value.detail, "YouTube")
    assert_nothing_charged(creation)


@pytest.mark.asyncio
@pytest.mark.parametrize("primary", [YOUTUBE_METADATA_PROVIDER_DATA_API, YOUTUBE_METADATA_PROVIDER_YTDLP])
async def test_youtube_geo_restriction_takes_precedence_over_zero_duration(youtube_config, monkeypatch, creation, primary):
    youtube_config.youtube_metadata_provider = primary
    item = {"id": "abcdefghijk", "contentDetails": {"duration": "P0D"}}
    data_api = Mock(return_value=FakeResponse({"items": [item]}))
    monkeypatch.setattr(youtube_utils.requests, "get", data_api)
    ytdlp = Mock(side_effect=YouTubeGeoRestrictedError())
    monkeypatch.setattr(youtube_utils, "_fetch_video_info_with_ytdlp", ytdlp)

    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)

    data_api.assert_called_once()
    ytdlp.assert_called_once()
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "YOUTUBE_GEO_RESTRICTED"
    assert "Upload the video file" in error.value.detail["message"]
    assert_nothing_charged(creation)


@pytest.mark.asyncio
async def test_youtube_fetch_failure_keeps_retry_error(monkeypatch, creation):
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(side_effect=RuntimeError("network")))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "VIDEO_METADATA_UNAVAILABLE"
    assert "livestream" not in error.value.detail["message"]
    assert_nothing_charged(creation)


@pytest.mark.asyncio
async def test_youtube_positive_duration_is_billed_normally(monkeypatch, creation):
    monkeypatch.setattr(
        tasks, "async_get_youtube_video_info",
        AsyncMock(return_value={"duration": 1800, "title": "Replay"}),
    )
    result = await tasks.create_task(creation.request, db=None)
    assert result["task_id"] == "task-1"
    assert creation.service.create_task_with_source.await_args.kwargs["source_duration_seconds"] == 1800
    assert tasks.BillingService.assert_can_create_task.await_args.args[-1] == 1800


@pytest.mark.asyncio
async def test_youtube_plan_cap_still_applies(monkeypatch, creation):
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 3 * 3600}))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)
    assert error.value.detail["code"] == "VIDEO_TOO_LONG"
    assert_nothing_charged(creation)


# ------------------------------------------------- Twitch/Kick provider chain


@pytest.fixture
def stream_env(monkeypatch):
    config = SimpleNamespace(
        twitch_download_providers="proxy,direct,apify_twitch",
        kick_download_providers="proxy,direct_retry,apify_kick_hls,apify_kick_mp4",
        apify_stream_enabled=True,
        apify_api_token="placeholder-token",
        apify_twitch_actor="twitch-actor",
        apify_kick_hls_actor="kick-hls-actor",
        apify_kick_mp4_actor="kick-mp4-actor",
        youtube_proxy_url="http://user-{session}:pw@proxy.test:8000",
    )
    monkeypatch.setattr(stream_source, "get_config", lambda: config)
    monkeypatch.setattr(stream_source, "_DIRECT_RETRY_BACKOFF_SECONDS", 0)
    env = SimpleNamespace(extract={}, actors={}, actor_calls=[])

    def extract(url, provider, *, session, deadline=None):
        result = env.extract.get(provider, RuntimeError("down"))
        if isinstance(result, Exception):
            raise result
        return result

    def actor_items(actor, run_input, charge, timeout_secs=None):
        env.actor_calls.append(actor)
        result = env.actors.get(actor, [])
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(stream_source, "_extract", extract)
    monkeypatch.setattr(stream_source, "_actor_items", actor_items)
    return env


@pytest.mark.parametrize("url", [TWITCH_VOD, KICK_VOD])
@pytest.mark.parametrize("duration", [0, None])
def test_stream_zero_duration_everywhere_is_duration_unavailable(stream_env, url, duration):
    stream_env.extract = {"proxy": {"duration": duration}, "direct": {"duration": duration}, "direct_retry": {"duration": duration}}
    with pytest.raises(stream_source.StreamDurationUnavailableError):
        stream_source.fetch_stream_info(url)
    # Bounded: the cheap metadata actor ran at most once; parsebird never.
    assert "kick-mp4-actor" not in stream_env.actor_calls
    assert len(stream_env.actor_calls) <= 1


def test_stream_zero_from_ytdlp_then_actor_success_is_normal(stream_env):
    stream_env.extract = {"proxy": {"duration": 0}, "direct_retry": {"duration": 0}}
    stream_env.actors["kick-hls-actor"] = [{
        "type": "vod", "id": "12345678-1234-1234-1234-123456789abc", "durationSec": 3600,
        "hlsUrl": "https://media.test/kick.m3u8", "status": "ready",
    }]
    info = stream_source.fetch_stream_info(KICK_VOD)
    assert info["duration"] == 3600 and info["provider"] == "apify_kick_hls"


@pytest.mark.parametrize("url", [TWITCH_VOD, KICK_VOD])
def test_stream_provider_failures_stay_fetch_failed(stream_env, url):
    # Nothing answered at all: not a duration problem.
    with pytest.raises(stream_source.StreamSourceError) as error:
        stream_source.fetch_stream_info(url)
    assert not isinstance(error.value, stream_source.StreamDurationUnavailableError)


def test_stream_positive_duration_from_first_provider(stream_env):
    stream_env.extract = {"proxy": {"duration": 125.0, "title": "VOD"}}
    info = stream_source.fetch_stream_info(TWITCH_VOD)
    assert info["duration"] == 125.0 and info["provider"] == "proxy"
    assert stream_env.actor_calls == []


# ---------------------------------------------------- Twitch/Kick task creation


@pytest.mark.asyncio
@pytest.mark.parametrize("url,platform", [(TWITCH_VOD, "Twitch"), (KICK_VOD, "Kick")])
async def test_stream_duration_unavailable_is_maybe_live_422(monkeypatch, creation, url, platform):
    creation.body["source"]["url"] = url
    monkeypatch.setattr(
        tasks, "fetch_stream_info",
        stream_info(raises=stream_source.StreamDurationUnavailableError("x")),
    )
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)
    assert error.value.status_code == 422
    assert_maybe_live_message(error.value.detail, platform)
    assert_nothing_charged(creation)


@pytest.mark.asyncio
async def test_stream_fetch_failure_keeps_video_unavailable(monkeypatch, creation):
    creation.body["source"]["url"] = TWITCH_VOD
    monkeypatch.setattr(
        tasks, "fetch_stream_info",
        stream_info(raises=stream_source.StreamSourceError(stream_source.FETCH_FAILED_MESSAGE)),
    )
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "VIDEO_UNAVAILABLE"
    assert_nothing_charged(creation)


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [TWITCH_VOD, KICK_VOD])
async def test_stream_positive_duration_is_billed_normally(monkeypatch, creation, url):
    creation.body["source"]["url"] = url
    monkeypatch.setattr(
        tasks, "fetch_stream_info",
        stream_info(returns={"duration": 1200.0, "title": "VOD", "provider": "proxy"}),
    )
    result = await tasks.create_task(creation.request, db=None)
    assert result["task_id"] == "task-1"
    assert creation.service.create_task_with_source.await_args.kwargs["source_duration_seconds"] == 1200
    assert tasks.BillingService.assert_can_create_task.await_args.args[-1] == 1200.0


@pytest.mark.asyncio
async def test_stream_plan_cap_still_applies(monkeypatch, creation):
    creation.body["source"]["url"] = KICK_VOD
    monkeypatch.setattr(tasks, "fetch_stream_info", stream_info(returns={"duration": 3 * 3600.0, "provider": "proxy"}))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)
    assert error.value.detail["code"] == "VIDEO_TOO_LONG"
    assert_nothing_charged(creation)


@pytest.mark.asyncio
async def test_twitch_channel_link_is_rejected_before_any_metadata_call(monkeypatch, creation):
    creation.body["source"]["url"] = "https://www.twitch.tv/somechannel"
    fetch = Mock()
    monkeypatch.setattr(tasks, "fetch_stream_info", fetch)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)
    assert error.value.status_code == 422
    fetch.assert_not_called()
    assert_nothing_charged(creation)


@pytest.mark.asyncio
async def test_age_restricted_youtube_video_is_refused_before_charging(youtube_config, monkeypatch, creation):
    youtube_config.youtube_metadata_provider = YOUTUBE_METADATA_PROVIDER_DATA_API
    item = {
        "id": "abcdefghijk",
        "contentDetails": {"duration": "PT26M49S", "contentRating": {"ytRating": "ytAgeRestricted"}},
    }
    monkeypatch.setattr(youtube_utils.requests, "get", Mock(return_value=FakeResponse({"items": [item]})))

    with pytest.raises(HTTPException) as error:
        await tasks.create_task(creation.request, db=None)

    assert error.value.status_code == 422
    assert error.value.detail["code"] == "YOUTUBE_AGE_RESTRICTED"
    assert "upload the file" in error.value.detail["message"]
    assert "No minutes were used" in error.value.detail["message"]
    assert_nothing_charged(creation)
