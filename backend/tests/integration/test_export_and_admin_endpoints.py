"""Integration tests for export + admin task endpoints.

Only guard-rails (auth, validation, not-found) are exercised — the actual
video export calls ffmpeg/MoviePy which would make the test slow and flaky.
"""

import pytest
from sqlalchemy import text

from tests.fixtures.factories import (
    create_clip,
    create_source,
    create_task,
    create_user,
)


@pytest.mark.asyncio
async def test_export_rejects_unknown_preset(client, db_session):
    user = await create_user(db_session, user_id="exp-user", email="exp@example.com")
    source = await create_source(db_session, title="Export")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"])

    response = await client.get(
        f"/tasks/{task['id']}/clips/{clip['id']}/export?preset=not-real",
        headers={"x-supoclip-user-id": user["id"]},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_export_forbidden_for_other_user(client, db_session):
    owner = await create_user(db_session, user_id="exp-owner", email="eo@example.com")
    other = await create_user(db_session, user_id="exp-other", email="eoo@example.com")
    source = await create_source(db_session, title="Owned")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"])

    response = await client.get(
        f"/tasks/{task['id']}/clips/{clip['id']}/export?preset=tiktok",
        headers={"x-supoclip-user-id": other["id"]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_export_requires_user_header(client, db_session):
    response = await client.get("/tasks/any/clips/any/export?preset=tiktok")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_export_returns_404_when_task_missing(client, db_session):
    await create_user(db_session, user_id="lonely", email="lonely@example.com")

    response = await client.get(
        "/tasks/no-task/clips/no-clip/export?preset=tiktok",
        headers={"x-supoclip-user-id": "lonely"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_admin_metrics_reachable_in_self_host(client, db_session):
    """Self-host deployments skip the admin check, so both roles can see metrics."""
    await create_user(
        db_session,
        user_id="metrics-user",
        email="mu@example.com",
        is_admin=False,
    )

    response = await client.get(
        "/tasks/metrics/performance",
        headers={"x-supoclip-user-id": "metrics-user"},
    )
    assert response.status_code == 200
    assert isinstance(response.json(), dict)


@pytest.mark.asyncio
async def test_admin_metrics_works_for_admin(client, db_session):
    await create_user(
        db_session,
        user_id="admin-user",
        email="admin@example.com",
        is_admin=True,
    )

    response = await client.get(
        "/tasks/metrics/performance",
        headers={"x-supoclip-user-id": "admin-user"},
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_admin_dead_letter_list_shape(client, db_session):
    await create_user(
        db_session, user_id="dl-user", email="dl@example.com", is_admin=True
    )

    response = await client.get(
        "/tasks/dead-letter/list",
        headers={"x-supoclip-user-id": "dl-user"},
    )
    assert response.status_code == 200
    # Endpoint returns a dict — shape is enforced by the handler, just verify type.
    assert isinstance(response.json(), dict)
