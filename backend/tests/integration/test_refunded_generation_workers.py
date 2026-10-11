from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from tests.fixtures.factories import create_user

@pytest.mark.asyncio
async def test_refunded_error_job_cannot_start_without_explicit_resume(
    db_session, initialized_database, tmp_path
):
    from tests.integration.test_clip_edit_atomicity import make_task
    from tests.integration.test_processing_cancellation import setup_worker
    from src.repositories.task_repository import TaskRepository
    from src.services.billing_service import BillingService
    from src.config import Config

    task_id, _, original = await make_task(db_session, tmp_path, status="error")
    await create_user(db_session, user_id="user-1", plan="pro", subscription_status="active")
    await db_session.execute(
        text("UPDATE tasks SET user_id='user-1' WHERE id=:id"), {"id": task_id}
    )
    await db_session.execute(
        text("DELETE FROM generated_clips WHERE task_id=:id"), {"id": task_id}
    )
    await db_session.commit()
    config = Config()
    config.monetization_enabled = True
    assert (await BillingService(db_session, config).get_usage_summary("user-1"))["usage_count"] == 0
    service, _, _ = setup_worker(db_session, tmp_path, original)
    result = await service.process_task(task_id, "upload://source.mp4", "video_url")
    assert result["skipped"] is True
    assert result["status"] == "error"
    service.video_service.process_video_complete.assert_not_awaited()
    assert (await TaskRepository.get_task_by_id(db_session, task_id))["status"] == "error"


@pytest.mark.asyncio
@pytest.mark.parametrize("job_exists", [True, False])
async def test_polling_old_queue_reservation_only_refunds_when_job_missing(
    db_session, initialized_database, tmp_path, monkeypatch, job_exists
):
    from src.services.task_service import TaskService
    from src.workers.job_queue import JobQueue
    from src.repositories.task_repository import TaskRepository
    from tests.integration.test_clip_edit_atomicity import make_task
    from tests.integration.test_processing_cancellation import setup_worker

    task_id, _, original = await make_task(db_session, tmp_path, status="queued")
    await db_session.execute(text(
        "UPDATE tasks SET created_at=NOW()-INTERVAL '1 hour', "
        "updated_at=NOW()-INTERVAL '1 hour' WHERE id=:id"
    ), {"id": task_id})
    await db_session.commit()
    await TaskRepository.save_source_metadata(db_session, task_id, {"processing_job_id": "tracked-job"})
    monkeypatch.setattr(JobQueue, "get_pool", AsyncMock(return_value=SimpleNamespace(
        exists=AsyncMock(return_value=job_exists), get=AsyncMock(return_value=b"healthy")
    )))
    service = TaskService(db_session)
    service._load_task_source_settings = AsyncMock(return_value={})
    polled = await service.get_task_with_clips(task_id)
    assert polled["status"] == ("queued" if job_exists else "error")
    worker, _, _ = setup_worker(db_session, tmp_path, original)
    result = await worker.process_task(task_id, "upload://input.mp4", "upload")
    if job_exists:
        worker.video_service.process_video_complete.assert_awaited_once()
        assert (await worker.task_repo.get_task_by_id(db_session, task_id))["status"] == "completed"
    else:
        assert result["skipped"] is True
        worker.video_service.process_video_complete.assert_not_awaited()
