"""Feature flags on PostgreSQL: the seeded row, evaluation per platform, and the API."""

import hashlib
import hmac
import time
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text

from src import feature_flags
from tests.fixtures.factories import create_user

MIGRATION = Path(__file__).parents[2] / "src/migrations/sql/20261105_0004_feature_flags.sql"
IOS = {"x-supoclip-client": "ios"}


def headers_for(user_id: str, **extra: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return {"x-supoclip-user-id": user_id, "x-supoclip-ts": timestamp, "x-supoclip-signature": signature, **extra}


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
async def test_feature_flags_route_evaluates_for_the_caller(client, db_session, feature_flag):
    user_id, admin = str(uuid4()), str(uuid4())
    await create_user(db_session, user_id=user_id)
    await create_user(db_session, user_id=admin, is_admin=True)
    await feature_flag("workflows", web_enabled=False, ios_enabled=True)

    web = await client.get("/feature-flags", headers=headers_for(user_id))
    assert web.status_code == 200, web.text
    assert web.json() == {"platform": "web", "flags": {"workflows": False}}
    ios = (await client.get("/feature-flags", headers=headers_for(user_id, **IOS))).json()
    assert ios == {"platform": "ios", "flags": {"workflows": True}}
    assert (await client.get("/feature-flags", headers=headers_for(admin))).json()["flags"]["workflows"] is True
    assert (await client.get("/feature-flags")).status_code == 401


@pytest.mark.asyncio
async def test_flag_changes_reach_the_api_after_the_cache_expires(client, db_session, feature_flag, monkeypatch):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await feature_flag("workflows", web_enabled=False)

    async def flag():
        return (await client.get("/feature-flags", headers=headers_for(user_id))).json()["flags"]["workflows"]

    assert await flag() is False

    # An admin switches it in /admin: written straight to the table, no cache clear.
    await db_session.execute(text("UPDATE feature_flags SET web_enabled = true WHERE key = 'workflows'"))
    await db_session.commit()
    assert await flag() is False

    loaded_at, flags = feature_flags._cache
    monkeypatch.setattr(feature_flags, "_cache", (loaded_at - feature_flags.CACHE_TTL_SECONDS - 1, flags))
    assert await flag() is True


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
