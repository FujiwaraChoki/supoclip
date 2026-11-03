"""Source restrictions remain actionable at authenticated editing boundaries."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import tasks
from src.youtube_proxy import YouTubeGeoRestrictedError


@pytest.mark.parametrize("operation", ["captions", "settings"])
@pytest.mark.parametrize("restricted", [True, False])
async def test_edit_routes_distinguish_source_restrictions_from_server_failures(
    monkeypatch, operation, restricted,
):
    error = YouTubeGeoRestrictedError() if restricted else RuntimeError("renderer failed")
    task = {"id": "task", "user_id": "owner", "source_type": "youtube"}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        update_clip_captions=AsyncMock(side_effect=error),
        update_task_settings=AsyncMock(side_effect=error),
    )
    owner_check = AsyncMock(return_value=task)
    monkeypatch.setattr(tasks, "TaskService", lambda _: service)
    monkeypatch.setattr(tasks, "_require_task_owner", owner_check)
    monkeypatch.setattr(tasks, "_load_task_source_metadata", AsyncMock(return_value={}))

    async def database():
        yield None

    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[tasks.get_db] = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        if operation == "captions":
            response = await client.patch("/tasks/task/clips/clip/captions", json={"caption_text": "Hello"})
        else:
            response = await client.post("/tasks/task/settings", json={"apply_to_existing": True})

    owner_check.assert_awaited_once()
    assert response.status_code == (422 if restricted else 500)
    if restricted:
        assert response.json()["detail"] == {
            "code": "YOUTUBE_GEO_RESTRICTED", "message": str(error),
        }
        # Edits must not claim that a previous generation used no minutes.
        assert "No minutes" not in response.text
