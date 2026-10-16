from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from src.api.routes import tasks


def _service(task):
    return SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task))
    )


@pytest.mark.asyncio
async def test_owner_can_read_any_status(monkeypatch):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="owner"))
    task = {"user_id": "owner", "status": "completed"}

    result = await tasks._require_task_reader(object(), _service(task), object(), "task-1")

    assert result is task


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "processing", "pending"])
async def test_admin_can_read_another_users_active_task(monkeypatch, status):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="admin"))
    monkeypatch.setattr(tasks, "user_is_admin", AsyncMock(return_value=True))
    task = {"user_id": "owner", "status": status}

    result = await tasks._require_task_reader(object(), _service(task), object(), "task-1")

    assert result is task


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "error", "cancelled"])
async def test_admin_cannot_read_another_users_finished_task(monkeypatch, status):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="admin"))
    is_admin = AsyncMock(return_value=True)
    monkeypatch.setattr(tasks, "user_is_admin", is_admin)

    with pytest.raises(HTTPException) as exc:
        await tasks._require_task_reader(
            object(), _service({"user_id": "owner", "status": status}), object(), "task-1"
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "Not authorized for this task"
    is_admin.assert_not_awaited()


@pytest.mark.asyncio
async def test_non_admin_cannot_read_another_users_active_task(monkeypatch):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="viewer"))
    monkeypatch.setattr(tasks, "user_is_admin", AsyncMock(return_value=False))

    with pytest.raises(HTTPException) as exc:
        await tasks._require_task_reader(
            object(),
            _service({"user_id": "owner", "status": "processing"}),
            object(),
            "task-1",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_cannot_mutate_another_users_active_task(monkeypatch):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="admin"))

    with pytest.raises(HTTPException) as exc:
        await tasks._require_task_owner(
            object(),
            _service({"user_id": "owner", "status": "processing"}),
            object(),
            "task-1",
        )

    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_missing_task_is_not_found(monkeypatch):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="admin"))

    with pytest.raises(HTTPException) as exc:
        await tasks._require_task_reader(object(), _service(None), object(), "missing")

    assert exc.value.status_code == 404
