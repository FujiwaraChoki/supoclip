"""Feature flags on PostgreSQL: the seeded row, the workflows gate per platform, and the API."""

import hashlib
import hmac
import json
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from src import feature_flags
from tests.fixtures.factories import create_user

MIGRATION = Path(__file__).parents[2] / "src/migrations/sql/20261105_0004_feature_flags.sql"
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
IOS = {"x-supoclip-client": "ios"}


def headers_for(user_id: str, **extra: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return {"x-supoclip-user-id": user_id, "x-supoclip-ts": timestamp, "x-supoclip-signature": signature, **extra}


@pytest.fixture
def youtube_duration(monkeypatch):
    async def info(_url, **_kwargs):
        return {"duration": 600, "title": "Workflow source"}

    monkeypatch.setattr("src.api.routes.tasks.async_get_youtube_video_info", info)


@pytest.fixture
def workflows_for_all(app):
    app.state.config.workflows_access = "all"
    return app.state.config


async def create_series(client, headers):
    return await client.post(
        "/tasks/",
        headers={**headers, "content-type": "application/json"},
        content=json.dumps({"source": {"url": YOUTUBE_URL}, "workflow": "series"}),
    )


async def counts(db_session, user_id):
    tasks = (
        await db_session.execute(text("SELECT COUNT(*) FROM tasks WHERE user_id = :u"), {"u": user_id})
    ).scalar()
    minutes = (
        await db_session.execute(text("SELECT COUNT(*) FROM minute_usage_events WHERE user_id = :u"), {"u": user_id})
    ).scalar()
    return tasks, minutes


@pytest.mark.asyncio
async def test_migration_seeds_workflows_off_with_the_admin_bypass(db_session):
    row = (
        await db_session.execute(
            text("SELECT web_enabled, ios_enabled, admins_bypass, description FROM feature_flags WHERE key = 'workflows'")
        )
    ).one()
    assert (row.web_enabled, row.ios_enabled, row.admins_bypass) == (False, False, True)
    assert row.description
    # Applying it again changes nothing, and never resets a flag an admin switched.
    await db_session.execute(text("UPDATE feature_flags SET web_enabled = true WHERE key = 'workflows'"))
    for statement in MIGRATION.read_text().split(";"):
        if statement.strip():
            await db_session.execute(text(statement))
    assert (
        await db_session.execute(text("SELECT web_enabled FROM feature_flags WHERE key = 'workflows'"))
    ).scalar() is True
    await db_session.rollback()


@pytest.mark.asyncio
async def test_flag_off_hides_workflows_on_that_platform_only(
    client, db_session, feature_flag, workflows_for_all
):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await feature_flag("workflows", web_enabled=False, ios_enabled=True)

    web = (await client.get("/tasks/workflows", headers=headers_for(user_id))).json()
    ios = (await client.get("/tasks/workflows", headers=headers_for(user_id, **IOS))).json()

    assert web["enabled"] is False
    assert {item["id"] for item in web["workflows"] if item["available"]} == {"highlights"}
    assert ios["enabled"] is True

    await feature_flag("workflows", web_enabled=True, ios_enabled=False)
    assert (await client.get("/tasks/workflows", headers=headers_for(user_id))).json()["enabled"] is True
    assert (await client.get("/tasks/workflows", headers=headers_for(user_id, **IOS))).json()["enabled"] is False


@pytest.mark.asyncio
async def test_flag_off_rejects_create_with_403_before_charging(
    client, app, db_session, feature_flag, workflows_for_all, youtube_duration
):
    user_id = str(uuid4())
    app.state.config.monetization_enabled = True
    await create_user(db_session, user_id=user_id, plan="pro", subscription_status="active")
    await feature_flag("workflows", web_enabled=False, ios_enabled=True)

    blocked = await create_series(client, headers_for(user_id))

    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["detail"]["code"] == "WORKFLOW_NOT_AVAILABLE"
    assert await counts(db_session, user_id) == (0, 0)

    allowed = await create_series(client, headers_for(user_id, **IOS))
    assert allowed.status_code == 200, allowed.text


@pytest.mark.asyncio
async def test_admins_bypass_the_flag_unless_switched_off(
    client, db_session, feature_flag, workflows_for_all, youtube_duration
):
    admin = str(uuid4())
    await create_user(db_session, user_id=admin, is_admin=True)
    await feature_flag("workflows", web_enabled=False, ios_enabled=False, admins_bypass=True)

    assert (await client.get("/tasks/workflows", headers=headers_for(admin))).json()["enabled"] is True
    assert (await create_series(client, headers_for(admin, **IOS))).status_code == 200

    await feature_flag("workflows", admins_bypass=False)
    assert (await client.get("/tasks/workflows", headers=headers_for(admin))).json()["enabled"] is False
    assert (await create_series(client, headers_for(admin))).status_code == 403


@pytest.mark.asyncio
async def test_flag_on_still_needs_the_env_gate(client, app, db_session, feature_flag):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await feature_flag("workflows", web_enabled=True, ios_enabled=True)
    app.state.config.workflows_access = "off"
    assert (await client.get("/tasks/workflows", headers=headers_for(user_id))).json()["enabled"] is False
    app.state.config.workflows_access = "all"
    assert (await client.get("/tasks/workflows", headers=headers_for(user_id))).json()["enabled"] is True


@pytest.mark.asyncio
async def test_resume_checks_the_flag_for_the_resuming_platform(
    client, db_session, feature_flag, workflows_for_all, youtube_duration, monkeypatch
):
    from unittest.mock import AsyncMock

    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await feature_flag("workflows", web_enabled=True, ios_enabled=True)
    task_id = (await create_series(client, headers_for(user_id))).json()["task_id"]
    await db_session.execute(text("UPDATE tasks SET status = 'error' WHERE id = :id"), {"id": task_id})
    await db_session.commit()
    monkeypatch.setattr(
        "src.api.routes.tasks.JobQueue.enqueue_processing_job", AsyncMock(return_value="resumed-job")
    )
    await feature_flag("workflows", web_enabled=False)

    blocked = await client.post(f"/tasks/{task_id}/resume", headers=headers_for(user_id))
    assert blocked.status_code == 403, blocked.text
    resumed = await client.post(f"/tasks/{task_id}/resume", headers=headers_for(user_id, **IOS))
    assert resumed.status_code == 200, resumed.text


@pytest.mark.asyncio
async def test_feature_flags_route_evaluates_for_the_caller(client, db_session, feature_flag):
    user_id, admin = str(uuid4()), str(uuid4())
    await create_user(db_session, user_id=user_id)
    await create_user(db_session, user_id=admin, is_admin=True)
    await feature_flag("workflows", web_enabled=False, ios_enabled=True)

    web = await client.get("/feature-flags", headers=headers_for(user_id))
    assert web.status_code == 200, web.text
    assert web.json() == {"platform": "web", "flags": {"tiktok_publishing": False, "workflows": False}}
    ios = (await client.get("/feature-flags", headers=headers_for(user_id, **IOS))).json()
    assert ios == {"platform": "ios", "flags": {"tiktok_publishing": False, "workflows": True}}
    assert (await client.get("/feature-flags", headers=headers_for(admin))).json()["flags"]["workflows"] is True
    assert (await client.get("/feature-flags")).status_code == 401


@pytest.mark.asyncio
async def test_flag_changes_reach_the_api_after_the_cache_expires(
    client, db_session, feature_flag, workflows_for_all, monkeypatch
):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await feature_flag("workflows", web_enabled=False)
    assert (await client.get("/tasks/workflows", headers=headers_for(user_id))).json()["enabled"] is False

    # An admin switches it in /admin: written straight to the table, no cache clear.
    await db_session.execute(text("UPDATE feature_flags SET web_enabled = true WHERE key = 'workflows'"))
    await db_session.commit()
    assert (await client.get("/tasks/workflows", headers=headers_for(user_id))).json()["enabled"] is False

    loaded_at, flags = feature_flags._cache
    monkeypatch.setattr(feature_flags, "_cache", (loaded_at - feature_flags.CACHE_TTL_SECONDS - 1, flags))
    assert (await client.get("/tasks/workflows", headers=headers_for(user_id))).json()["enabled"] is True


@pytest.mark.asyncio
async def test_unreadable_table_falls_back_to_closed_without_breaking_the_request(db_session):
    feature_flags.clear_cache()
    await db_session.execute(text("ALTER TABLE feature_flags RENAME TO feature_flags_hidden"))
    try:
        assert await feature_flags.is_enabled(db_session, "workflows", platform="web") is False
        # The failed read was rolled back to its savepoint: the session still works.
        assert (await db_session.execute(text("SELECT 1"))).scalar() == 1
    finally:
        await db_session.rollback()
        feature_flags.clear_cache()
