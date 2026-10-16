import pytest

from tests.fixtures.factories import create_source, create_task, create_user


@pytest.mark.asyncio
async def test_admin_route_requires_admin_user(client, db_session, auth_headers):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=False,
    )

    response = await client.get(
        "/admin/health",
        headers=auth_headers,
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_feedback_rejects_invalid_category(client, auth_headers):
    response = await client.post(
        "/feedback",
        headers=auth_headers,
        json={"category": "unknown", "message": "hi"},
    )

    assert response.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "processing", "pending"])
async def test_admin_can_read_another_users_active_task(
    client, db_session, auth_headers, status
):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )
    owner = await create_user(db_session, user_id="user-2", email="owner@example.com")
    source = await create_source(db_session, title="Live source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status=status
    )

    response = await client.get(f"/tasks/{task['id']}", headers=auth_headers)
    clips = await client.get(f"/tasks/{task['id']}/clips", headers=auth_headers)
    cancel = await client.post(f"/tasks/{task['id']}/cancel", headers=auth_headers)

    assert response.status_code == 200
    assert response.json()["source_title"] == "Live source"
    assert clips.status_code == 200
    assert cancel.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["completed", "error", "cancelled"])
async def test_admin_cannot_read_another_users_finished_task(
    client, db_session, auth_headers, status
):
    await create_user(
        db_session, user_id="user-1", email="admin@example.com", is_admin=True
    )
    owner = await create_user(db_session, user_id="user-2", email="owner@example.com")
    source = await create_source(db_session, title="Finished source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status=status
    )

    response = await client.get(f"/tasks/{task['id']}", headers=auth_headers)

    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized for this task"


@pytest.mark.asyncio
async def test_non_admin_cannot_read_another_users_active_task(
    client, db_session, auth_headers
):
    await create_user(
        db_session, user_id="user-1", email="viewer@example.com", is_admin=False
    )
    owner = await create_user(db_session, user_id="user-2", email="owner@example.com")
    source = await create_source(db_session, title="Live source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="processing"
    )

    response = await client.get(f"/tasks/{task['id']}", headers=auth_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_performance_metrics_require_admin(client, db_session, auth_headers):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=False,
    )

    response = await client.get("/tasks/metrics/performance", headers=auth_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_dead_letter_details_require_admin(client, db_session, auth_headers):
    assert (await client.get("/tasks/dead-letter/list")).status_code == 401
    await create_user(db_session, user_id="user-1", is_admin=False)
    assert (await client.get("/tasks/dead-letter/list", headers=auth_headers)).status_code == 403
    await create_user(db_session, user_id="user-1", is_admin=True)
    assert (await client.get("/tasks/dead-letter/list", headers=auth_headers)).status_code == 200
