from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks
from src.services.video_service import VideoService


@pytest.fixture
def creation(monkeypatch):
    billing = {"max_youtube_duration_seconds": 5400}
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
    assert "No generation was used" in error.value.detail["message"]
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
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(side_effect=TimeoutError("secret provider details")))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 503
    assert "secret provider details" not in str(error.value.detail)
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()


@pytest.mark.asyncio
async def test_upload_does_not_request_youtube_metadata(monkeypatch, creation):
    request, _service, _queue, _billing = creation
    request.json.return_value = {"source": {"url": "upload://video.mp4"}}
    metadata = AsyncMock()
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", metadata)
    result = await tasks.create_task(request, db=None)
    assert result["task_id"] == "task-1"
    metadata.assert_not_awaited()


@pytest.mark.asyncio
async def test_requested_range_uses_effective_duration(monkeypatch, creation):
    request, service, queue, _billing = creation
    request.json.return_value = {"source": {"url": "https://youtube.com/live/abcdefghijk?start=7200&end=7260"}}
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 14400}))
    result = await tasks.create_task(request, db=None)
    assert result["task_id"] == "task-1"
    service.create_task_with_source.assert_awaited_once()
    queue.enqueue_processing_job.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("suffix", ["?t=abc", "?start=120&end=60", "?start=180"])
async def test_invalid_range_does_not_consume_generation(monkeypatch, creation, suffix):
    request, service, queue, _billing = creation
    request.json.return_value = {"source": {"url": "https://youtu.be/abcdefghijk" + suffix}}
    monkeypatch.setattr(tasks, "async_get_youtube_video_info", AsyncMock(return_value={"duration": 180}))
    with pytest.raises(HTTPException) as error:
        await tasks.create_task(request, db=None)
    assert error.value.status_code == 422
    assert error.value.detail["code"] == "INVALID_VIDEO_RANGE"
    service.create_task_with_source.assert_not_awaited()
    queue.enqueue_processing_job.assert_not_awaited()
