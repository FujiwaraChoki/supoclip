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
    app.state.config.pro_plan_task_limit = 1
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
    assert summary["remaining"] == 1
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

    async def uncertain_enqueue(function, mode, task_id, *args):
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
