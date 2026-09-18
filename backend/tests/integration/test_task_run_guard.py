import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.repositories.task_repository import TaskRepository
from src.repositories.task_run_guard import task_run_guard, TaskRunBusy
from tests.fixtures.factories import create_user
from tests.integration.test_clip_edit_atomicity import make_task
from tests.integration.test_processing_cancellation import setup_worker


@pytest.mark.asyncio
async def test_resume_waits_for_cancelled_worker_and_then_enqueues_only_once(
    client, auth_headers, db_session, initialized_database, tmp_path, monkeypatch
):
    task_id, _, original = await make_task(db_session, tmp_path, status="queued")
    await create_user(db_session, user_id="user-1")
    await db_session.execute(text("UPDATE tasks SET user_id='user-1' WHERE id=:id"), {"id": task_id})
    await db_session.commit()
    enqueue = AsyncMock(return_value="new-job")
    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", enqueue)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()
    async with sessions() as worker:
        service, _, clip_info = setup_worker(worker, tmp_path, original)

        async def render(*_args):
            entered.set()
            await release.wait()
            return clip_info

        service.video_service.create_single_clip = render
        job = asyncio.create_task(service.process_task(
            task_id, "upload://input.mp4", "upload", should_cancel=AsyncMock(return_value=False),
        ))
        await asyncio.wait_for(entered.wait(), 3)
        cancelled = await client.post(f"/tasks/{task_id}/cancel", headers=auth_headers)
        assert cancelled.status_code == 200
        try:
            premature = await client.post(f"/tasks/{task_id}/resume", headers=auth_headers)
            assert premature.status_code == 409
            assert "still stopping" in premature.json()["detail"]
            enqueue.assert_not_awaited()
            assert (await TaskRepository.get_task_by_id(db_session, task_id))["status"] == "cancelled"
        finally:
            release.set()
        with pytest.raises(Exception, match="Task cancelled"):
            await asyncio.wait_for(job, 3)

    resumed = await client.post(f"/tasks/{task_id}/resume", headers=auth_headers)
    assert resumed.status_code == 200
    assert resumed.json()["job_id"] == "new-job"
    again = await client.post(f"/tasks/{task_id}/resume", headers=auth_headers)
    assert again.status_code == 200
    assert again.json()["message"] == "Task already queued"
    enqueue.assert_awaited_once()
    assert (await TaskRepository.get_task_by_id(db_session, task_id))["status"] == "queued"


@pytest.mark.asyncio
async def test_duplicate_worker_cannot_overlap_or_repeat_completed_run(
    db_session, initialized_database, tmp_path
):
    task_id, _, original = await make_task(db_session, tmp_path, status="queued")
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()
    async with sessions() as one, sessions() as two:
        first, _, info = setup_worker(one, tmp_path, original)
        duplicate, _, _ = setup_worker(two, tmp_path, original)

        async def render(*_args):
            entered.set()
            await release.wait()
            return info

        first.video_service.create_single_clip = render
        job = asyncio.create_task(first.process_task(task_id, "upload://input.mp4", "upload"))
        await asyncio.wait_for(entered.wait(), 3)
        try:
            with pytest.raises(TaskRunBusy):
                await duplicate.process_task(task_id, "upload://input.mp4", "upload")
            duplicate.video_service.process_video_complete.assert_not_awaited()
        finally:
            release.set()
        await asyncio.wait_for(job, 3)
        result = await duplicate.process_task(task_id, "upload://input.mp4", "upload")
        assert result["skipped"] is True
        assert result["status"] == "completed"
        duplicate.video_service.process_video_complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_run_guard_survives_commits_and_releases_on_cancellation(db_session, initialized_database):
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    entered = asyncio.Event()
    async with sessions() as other:
        async def hold():
            async with task_run_guard(db_session, "guard-cancellation-test"):
                await db_session.commit()
                entered.set()
                await asyncio.Event().wait()

        job = asyncio.create_task(hold())
        await entered.wait()
        with pytest.raises(TaskRunBusy):
            async with task_run_guard(other, "guard-cancellation-test"):
                pass
        job.cancel()
        with pytest.raises(asyncio.CancelledError):
            await job
        async with task_run_guard(other, "guard-cancellation-test"):
            pass


@pytest.mark.asyncio
async def test_failed_resume_enqueue_remains_retryable(client, auth_headers, db_session, tmp_path, monkeypatch):
    task_id, _, _ = await make_task(db_session, tmp_path, status="cancelled")
    await create_user(db_session, user_id="user-1")
    await db_session.execute(text("UPDATE tasks SET user_id='user-1' WHERE id=:id"), {"id": task_id})
    await db_session.commit()
    enqueue = AsyncMock(side_effect=[RuntimeError("queue unavailable"), "retry-job"])
    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", enqueue)
    failed = await client.post(f"/tasks/{task_id}/resume", headers=auth_headers)
    assert failed.status_code == 500
    assert (await TaskRepository.get_task_by_id(db_session, task_id))["status"] == "cancelled"
    retried = await client.post(f"/tasks/{task_id}/resume", headers=auth_headers)
    assert retried.status_code == 200
    assert retried.json()["job_id"] == "retry-job"
