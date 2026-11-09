"""POST /tasks/{id}/settings answers 409 when a regeneration can't go ahead."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import tasks
from src.repositories.edit_transaction import RegenerationBusy
from src.services.clip_service import ClipsChangedDuringRegeneration


@pytest.mark.parametrize("error", [RegenerationBusy(), ClipsChangedDuringRegeneration()])
async def test_settings_conflicts_return_409_with_their_message(monkeypatch, error):
    task = {"id": "task", "user_id": "owner", "source_type": "youtube"}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        update_task_settings=AsyncMock(side_effect=error),
    )
    save_metadata = AsyncMock()
    monkeypatch.setattr(tasks, "TaskService", lambda _: service)
    monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(return_value=task))
    monkeypatch.setattr(tasks, "_load_task_source_metadata", AsyncMock(return_value={}))
    monkeypatch.setattr(tasks, "_save_task_source_metadata", save_metadata)

    async def database():
        yield None

    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[tasks.get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post("/tasks/task/settings", json={"apply_to_existing": True})

    assert response.status_code == 409
    assert response.json()["detail"] == str(error)
    # Settings the regeneration never applied are not recorded either.
    save_metadata.assert_not_awaited()
