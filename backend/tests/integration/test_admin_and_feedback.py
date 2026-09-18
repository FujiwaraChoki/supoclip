import pytest

from tests.fixtures.factories import create_user


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
