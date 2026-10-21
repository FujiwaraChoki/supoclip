from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text

from src.api.routes import tasks
from src.services.billing_service import BillingService
from tests.fixtures.factories import create_user


@pytest.mark.asyncio
async def test_failed_enqueue_releases_generation_allowance(
    client, app, db_session, auth_headers, monkeypatch
):
    app.state.config.monetization_enabled = True
    app.state.config.pro_plan_minute_limit = 1
    await create_user(
        db_session, user_id="user-1", plan="pro", subscription_status="active"
    )
    enqueue = AsyncMock(side_effect=RuntimeError("Redis unavailable"))
    app.state.queue_adapter = SimpleNamespace(enqueue_processing_job=enqueue)
    metadata = AsyncMock()
    monkeypatch.setattr(tasks, "_save_task_source_metadata", metadata)

    response = await client.post(
        "/tasks/", headers=auth_headers,
        json={"source": {"url": "upload://source.mp4"}, "output_format": "original", "add_subtitles": False},
    )

    assert response.status_code == 500
    result = await db_session.execute(
        text("SELECT id, status FROM tasks WHERE user_id = 'user-1'")
    )
    task = result.one()
    assert task.status == "error"
    summary = await BillingService(db_session, app.state.config).get_usage_summary("user-1")
    assert summary["usage_count"] == 0
    assert summary["remaining_minutes"] == 1
    metadata.assert_awaited_once()
    assert metadata.await_args.args[0] == task.id
    assert metadata.await_args.args[1]["output_format"] == "original"
    assert metadata.await_args.args[1]["add_subtitles"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("winning_status", ["processing", "cancelled"])
async def test_enqueue_error_does_not_overwrite_a_concurrent_status_change(
    client, app, db_session, auth_headers, monkeypatch, winning_status
):
    await create_user(db_session, user_id="user-1")
    monkeypatch.setattr(tasks, "_save_task_source_metadata", AsyncMock())

    async def uncertain_enqueue(function, mode, task_id, *args, **kwargs):
        await db_session.execute(
            text("UPDATE tasks SET status=:status WHERE id=:id"),
            {"status": winning_status, "id": task_id},
        )
        await db_session.commit()
        raise RuntimeError("Queue acknowledgement lost")

    app.state.queue_adapter = SimpleNamespace(enqueue_processing_job=uncertain_enqueue)
    response = await client.post(
        "/tasks/", headers=auth_headers, json={"source": {"url": "upload://source.mp4"}}
    )

    assert response.status_code == 500
    result = await db_session.execute(
        text("SELECT status FROM tasks WHERE user_id='user-1'")
    )
    assert result.scalar_one() == winning_status


@pytest.mark.asyncio
async def test_retry_preserves_source_options_when_redis_metadata_is_unavailable(
    client, app, db_session, auth_headers, monkeypatch
):
    from src.services.task_service import TaskService

    await create_user(db_session, user_id="user-1")
    failed_redis = SimpleNamespace(
        set=AsyncMock(side_effect=RuntimeError("Redis unavailable")),
        get=AsyncMock(side_effect=RuntimeError("Redis unavailable")),
        delete=AsyncMock(), aclose=AsyncMock(),
    )
    monkeypatch.setattr(tasks.redis, "Redis", lambda **_kwargs: failed_redis)
    app.state.queue_adapter = SimpleNamespace(
        enqueue_processing_job=AsyncMock(side_effect=RuntimeError("Redis unavailable"))
    )
    response = await client.post(
        "/tasks/", headers=auth_headers,
        json={
            "source": {"url": "upload://source.mp4"},
            "output_format": "original", "add_subtitles": False,
            "cut_long_pauses": True, "pause_threshold_ms": 1200,
            "remove_filler_words": True, "filtered_words": ["basically"],
        },
    )
    assert response.status_code == 500
    result = await db_session.execute(
        text("SELECT id, source_metadata FROM tasks WHERE user_id='user-1'")
    )
    task = result.one()
    assert task.source_metadata["output_format"] == "original"
    assert task.source_metadata["add_subtitles"] is False
    settings = await TaskService(db_session, app.state.config)._load_task_source_settings(task.id)
    assert settings["output_format"] == "original"
    assert settings["add_subtitles"] is False
    assert settings["cut_long_pauses"] is True
    assert settings["filtered_words"] == ["basically"]

    # A styling-only update must read PostgreSQL before merging omitted fields,
    # including after the cleanup-defaults change on main.
    updated = await client.post(
        f"/tasks/{task.id}/settings", headers=auth_headers, json={"font_size": 72}
    )
    assert updated.status_code == 200, updated.text
    saved = await tasks.TaskRepository.load_source_metadata(db_session, task.id)
    assert saved["output_format"] == "original"
    assert saved["add_subtitles"] is False
    assert saved["pause_threshold_ms"] == 1200
    assert saved["filtered_words"] == ["basically"]
    assert saved["url"] == "upload://source.mp4"

    enqueue = AsyncMock(return_value="retry-job")
    monkeypatch.setattr(tasks.JobQueue, "enqueue_processing_job", enqueue)
    resumed = await client.post(f"/tasks/{task.id}/resume", headers=auth_headers)
    assert resumed.status_code == 200
    assert enqueue.await_args.args[-3] == "original"
    assert enqueue.await_args.args[-2] is False
    assert enqueue.await_args.args[-1]["pause_threshold_ms"] == 1200
    assert enqueue.await_args.args[-1]["filtered_words"] == ["basically"]
    failed_redis.get.assert_not_awaited()
    retried = await tasks.TaskRepository.load_source_metadata(db_session, task.id)
    assert retried["processing_job_id"] == enqueue.await_args.kwargs["_job_id"]
    assert retried["processing_job_id"] != task.source_metadata["processing_job_id"]


@pytest.mark.asyncio
async def test_settings_edit_does_not_restore_job_id_from_before_concurrent_resume(
    client, db_session, auth_headers, monkeypatch
):
    from src.services.task_service import TaskService
    from src.repositories.task_repository import TaskRepository
    from tests.fixtures.factories import create_source, create_task

    await create_user(db_session, user_id="user-1")
    source = await create_source(db_session, source_type="video_url", url="upload://source.mp4")
    task = await create_task(db_session, user_id="user-1", source_id=source["id"], status="error")
    await TaskRepository.save_source_metadata(db_session, task["id"], {
        "processing_job_id": "old-job", "output_format": "original", "add_subtitles": False,
    })

    async def concurrent_resume_during_edit(self, *args):
        await TaskRepository.save_source_metadata(db_session, task["id"], {
            "processing_job_id": "new-job", "concurrent_key": "retained",
        })
        return task

    monkeypatch.setattr(TaskService, "update_task_settings", concurrent_resume_during_edit)
    redis = SimpleNamespace(set=AsyncMock(), aclose=AsyncMock())
    monkeypatch.setattr(tasks.redis, "Redis", lambda **_kwargs: redis)
    response = await client.post(
        f"/tasks/{task['id']}/settings", headers=auth_headers, json={"font_size": 72}
    )
    assert response.status_code == 200, response.text
    saved = await TaskRepository.load_source_metadata(db_session, task["id"])
    assert saved["processing_job_id"] == "new-job"
    assert saved["concurrent_key"] == "retained"
    assert saved["output_format"] == "original"
    assert saved["add_subtitles"] is False
