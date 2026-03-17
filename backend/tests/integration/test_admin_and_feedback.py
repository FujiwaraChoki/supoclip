import pytest

from tests.fixtures.factories import create_user


@pytest.mark.asyncio
async def test_admin_route_requires_admin_user(client, db_session):
    await create_user(
        db_session,
        user_id="user-1",
        email="owner@example.com",
        is_admin=False,
    )

    response = await client.get(
        "/admin/health",
        headers={"x-supoclip-user-id": "user-1"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_feedback_rejects_invalid_category(client):
    response = await client.post(
        "/feedback",
        headers={"user_id": "user-1"},
        json={"category": "unknown", "message": "hi"},
    )

    assert response.status_code == 400
