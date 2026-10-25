import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks
from src.config import set_config_override
from src.services.video_service import VideoService


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
        json=AsyncMock(return_value={"source": {"url": "https://youtu.be/abcdefghijk"}}),
        app=SimpleNamespace(state=SimpleNamespace(queue_adapter=queue)),
    )
    return request, service, queue, billing


@pytest.mark.asyncio
async def test_long_video_rejected_before_task_or_queue_creation(monkeypatch, creation):
    request, service, queue, _billing = creation
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 94 * 60}))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "VIDEO_TOO_LONG"
    assert "90 minutes" in error.value.detail["message"]
    assert "No minutes were used" in error.value.detail["message"]
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("limit,duration", [(5400, 5400), (10800, 94 * 60)])
async def test_plan_boundary_and_longer_entitlement_are_accepted(monkeypatch, creation, limit, duration):
    request, service, queue, billing = creation
    billing["max_youtube_duration_seconds"] = limit
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": duration, "title": "Verified title"}))
    result = await tasks.create_task(request, db=None)
    assert result["task_id"] == "task-1"
    assert service.create_task_with_source.await_args.kwargs["title"] == "Verified title"
    assert service.create_task_with_source.await_args.kwargs["source_duration_seconds"] == duration
    # The allowance check inside the guard is sized to the whole video.
    assert tasks.BillingService.assert_can_create_task.await_args.args[-1] == duration
    queue.enqueue_processing_job.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [None, {}, {"duration": None}, {"duration": 0}, {"duration": -1}, {"duration": float("nan")}, {"duration": True}])
async def test_unverifiable_duration_does_not_consume_generation(monkeypatch, creation, metadata):
    request, service, queue, _billing = creation
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value=metadata))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "VIDEO_METADATA_UNAVAILABLE"
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_metadata_error_does_not_leak_provider_details(monkeypatch, creation):
    request, service, queue, _billing = creation
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(side_effect=RuntimeError("secret provider details")))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "VIDEO_METADATA_UNAVAILABLE"
    assert "timed out" not in error.value.detail["message"]
    assert "secret provider details" not in str(error.value.detail)
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("configured", [None, "60"])
async def test_youtube_preflight_timeout_default_and_override(monkeypatch, configured):
    if configured is None:
        monkeypatch.delenv("YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS", raising=False)
        expected = 30.0
    else:
        monkeypatch.setenv("YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS", configured)
        expected = float(configured)
    set_config_override(None)
    captured = {}

    async def fake_wait_for(awaitable, timeout):
        captured["timeout"] = timeout
        return await awaitable

    monkeypatch.setattr(tasks.asyncio, "wait_for", fake_wait_for)
    monkeypatch.setattr(
        tasks,
        "async_get_youtube_video_info",
        AsyncMock(return_value={"duration": 20, "title": "Ok"}),
    )
    result = await tasks._preflight_youtube_source("https://youtu.be/abcdefghijk", 5400)
    assert captured["timeout"] == expected
    assert result["duration"] == 20


@pytest.mark.asyncio
async def test_youtube_preflight_timeout_returns_a_clear_error(monkeypatch):
    monkeypatch.setenv("YOUTUBE_PREFLIGHT_TIMEOUT_SECONDS", "0.05")
    set_config_override(None)

    async def slow(_url):
        await asyncio.sleep(2)
        return {"duration": 10, "secret": "secret provider details"}

    monkeypatch.setattr(tasks, "async_get_youtube_video_info", slow)
    with pytest.raises(HTTPException) as error:
        await tasks._preflight_youtube_source("https://youtu.be/abcdefghijk", 5400)
    assert error.value.status_code == 503
    assert error.value.detail["code"] == "VIDEO_METADATA_TIMEOUT"
    message = error.value.detail["message"]
    assert "timed out" in message
    assert "No minutes were used" in message
    assert "secret provider details" not in message


@pytest.mark.asyncio
async def test_upload_is_probed_instead_of_requesting_youtube_metadata(monkeypatch, creation):
    request, service, _queue, _billing = creation
    request.json.return_value = {"source": {"url": "upload://video.mp4"}}
    metadata = AsyncMock()
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", metadata)
    probe = MagicMock(return_value=600.4)
    monkeypatch.setattr(tasks.VideoService, "probe_upload_duration", probe)
    result = await tasks.create_task(request, db=None)
    assert result["task_id"] == "task-1"
    metadata.assert_not_awaited()
    probe.assert_called_once_with("upload://video.mp4", "user-1")
    assert service.create_task_with_source.await_args.kwargs["source_duration_seconds"] == 601


@pytest.mark.asyncio
@pytest.mark.parametrize("probe,code", [
    (MagicMock(side_effect=RuntimeError("unreadable")), "VIDEO_METADATA_UNAVAILABLE"),
    (MagicMock(return_value=0.0), "VIDEO_METADATA_UNAVAILABLE"),
    (MagicMock(return_value=5401.0), "VIDEO_TOO_LONG"),
])
async def test_unreadable_or_long_upload_uses_no_minutes(monkeypatch, creation, probe, code):
    request, service, queue, _billing = creation
    request.json.return_value = {"source": {"url": "upload://video.mp4"}}
    monkeypatch.setattr(tasks.VideoService, "probe_upload_duration", probe)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == code
    assert "No minutes were used" in error.value.detail["message"]
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", ["auto", "short", "medium", "long"])
async def test_clip_length_is_persisted_and_queued(monkeypatch, creation, preset):
    request, _, queue, _ = creation
    request.json.return_value["clip_length"] = preset
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 300}))
    await tasks.create_task(request, db=None)
    assert tasks._save_task_source_metadata.await_args.args[1]["clip_length"] == preset
    assert queue.enqueue_processing_job.await_args.args[-1] == preset


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [None, True, 30, "custom", {}])
async def test_invalid_clip_length_rejected_before_billing_or_download(monkeypatch, creation, value):
    request, service, queue, _ = creation
    request.json.return_value["clip_length"] = value
    preflight = AsyncMock()
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", preflight)
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 422
    tasks.BillingService.assert_can_create_task.assert_not_awaited()
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()
    preflight.assert_not_awaited()
