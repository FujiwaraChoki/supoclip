from unittest.mock import AsyncMock

import pytest

from src.services.task_service import TaskService


@pytest.mark.asyncio
async def test_create_task_with_source_creates_queued_task(monkeypatch):
    service = TaskService(db=AsyncMock())
    service.task_repo.user_exists = AsyncMock(return_value=True)
    service.source_repo.create_source = AsyncMock(return_value="source-1")
    service.task_repo.create_task = AsyncMock(return_value="task-1")
    monkeypatch.setattr(
        service.video_service,
        "determine_source_type",
        lambda _url: "youtube",
    )
    service.video_service.get_video_title = AsyncMock(return_value="Seeded title")

    task_id = await service.create_task_with_source(
        user_id="user-1",
        url="https://www.youtube.com/watch?v=demo",
    )

    assert task_id == "task-1"
    service.task_repo.create_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_task_with_source_requires_existing_user():
    service = TaskService(db=AsyncMock())
    service.task_repo.user_exists = AsyncMock(return_value=False)

    with pytest.raises(ValueError):
        await service.create_task_with_source(
            user_id="missing-user",
            url="https://example.com/video.mp4",
        )
