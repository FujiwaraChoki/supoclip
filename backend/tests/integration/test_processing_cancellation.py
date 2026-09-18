import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.repositories.clip_repository import ClipRepository
from src.repositories.task_repository import TaskRepository
from src.services.task_service import TaskService
from tests.integration.test_clip_edit_atomicity import make_task, config_for


def setup_worker(db, tmp_path, original):
    service = TaskService(db, config=config_for(tmp_path))
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.cache_repo.upsert_cache = AsyncMock()
    service.video_service.process_video_complete = AsyncMock(return_value={
        "video_path": str(original), "segments_to_render": [{}], "segments": [],
    })
    service._send_completion_notification_if_needed = AsyncMock()
    clip_path = tmp_path / "clips" / "new.mp4"
    clip_path.parent.mkdir(exist_ok=True)
    clip_path.touch()
    clip_path.with_suffix(".source_map.json").write_text('{}')
    clip_info = {"filename": clip_path.name, "path": str(clip_path),
                 "start_time": "00:00", "end_time": "00:10", "duration": 10}
    service.video_service.create_single_clip = AsyncMock(return_value=clip_info)
    return service, clip_path, clip_info


@pytest.mark.asyncio
@pytest.mark.parametrize("redis_flag", [True, False])
async def test_cancel_during_final_render_keeps_cancelled_and_removes_unpublished_clip(
    db_session, initialized_database, tmp_path, redis_flag
):
    task_id, _, original = await make_task(db_session, tmp_path, status="queued")
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()
    cancelled = False

    async def should_cancel():
        return cancelled and redis_flag

    async with sessions() as worker, sessions() as api:
        service, clip_path, clip_info = setup_worker(worker, tmp_path, original)

        async def render(*_args):
            entered.set()
            await release.wait()
            return clip_info

        service.video_service.create_single_clip = render
        progress = AsyncMock()
        ready = AsyncMock()
        job = asyncio.create_task(service.process_task(
            task_id, "upload://input.mp4", "upload", should_cancel=should_cancel,
            progress_callback=progress, clip_ready_callback=ready,
        ))
        await asyncio.wait_for(entered.wait(), 3)
        cancelled = True
        await TaskRepository.update_task_status(api, task_id, "cancelled")
        release.set()
        with pytest.raises(Exception, match="Task cancelled"):
            await asyncio.wait_for(job, 3)
        ready.assert_not_awaited()
        service._send_completion_notification_if_needed.assert_not_awaited()
        assert all(call.args[2] != "completed" for call in progress.await_args_list)

    current = await TaskRepository.get_task_by_id(db_session, task_id)
    assert current["status"] == "cancelled"
    assert list(current["generated_clips_ids"]) == []
    assert await ClipRepository.get_clips_by_task(db_session, task_id) == []
    assert not clip_path.exists()
    assert not clip_path.with_suffix(".source_map.json").exists()
    assert original.exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("target_status", ["processing", "completed", "error"])
async def test_cancel_wins_race_before_worker_status_write(
    db_session, initialized_database, tmp_path, target_status
):
    task_id, _, original = await make_task(db_session, tmp_path, status="queued")
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    async with sessions() as worker, sessions() as api:
        service, _, _ = setup_worker(worker, tmp_path, original)
        update_status = service.task_repo.update_task_status
        cancelled = False

        async def racing_update(db, tid, status, **kwargs):
            nonlocal cancelled
            if status == target_status and kwargs.get("progress_message") != "Starting...":
                cancelled = True
                await TaskRepository.update_task_status(api, task_id, "cancelled")
            return await update_status(db, tid, status, **kwargs)

        service.task_repo.update_task_status = racing_update
        if target_status == "error":
            service.video_service.create_single_clip = AsyncMock(side_effect=RuntimeError("render failed"))
        with pytest.raises(Exception):
            await service.process_task(task_id, "upload://input.mp4", "upload", should_cancel=AsyncMock(return_value=False))
        assert cancelled
        service._send_completion_notification_if_needed.assert_not_awaited()
    current = await TaskRepository.get_task_by_id(db_session, task_id)
    assert current["status"] == "cancelled"


@pytest.mark.asyncio
async def test_cancel_between_insert_and_commit_rolls_back_clip(db_session, tmp_path):
    task_id, _, original = await make_task(db_session, tmp_path, status="queued")
    service, clip_path, _ = setup_worker(db_session, tmp_path, original)
    cancelled = False
    create_clip = service.clip_repo.create_clip

    async def insert_then_cancel(*args, **kwargs):
        nonlocal cancelled
        clip_id = await create_clip(*args, **kwargs)
        cancelled = True
        return clip_id

    async def should_cancel():
        return cancelled

    service.clip_repo.create_clip = insert_then_cancel
    with pytest.raises(Exception, match="Task cancelled"):
        await service.process_task(task_id, "upload://input.mp4", "upload", should_cancel=should_cancel)
    current = await TaskRepository.get_task_by_id(db_session, task_id)
    assert current["status"] == "cancelled"
    assert list(current["generated_clips_ids"]) == []
    assert await ClipRepository.get_clips_by_task(db_session, task_id) == []
    assert not clip_path.exists()
