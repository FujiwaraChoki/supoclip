import pytest

from tests.fixtures.factories import create_source, create_task, create_user


@pytest.mark.asyncio
async def test_health_endpoints_report_healthy(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "healthy"}

    db_response = await client.get("/health/db")
    assert db_response.status_code == 200
    assert db_response.json()["status"] == "healthy"

    redis_response = await client.get("/health/redis")
    assert redis_response.status_code == 200
    assert redis_response.json()["status"] == "healthy"


@pytest.mark.asyncio
async def test_list_tasks_only_returns_owned_tasks(client, db_session):
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    other = await create_user(db_session, user_id="user-2", email="other@example.com")
    source_one = await create_source(db_session, title="Owner source")
    source_two = await create_source(db_session, title="Other source")
    await create_task(db_session, user_id=owner["id"], source_id=source_one["id"])
    await create_task(db_session, user_id=other["id"], source_id=source_two["id"])

    response = await client.get(
        "/tasks/",
        headers={"x-supoclip-user-id": owner["id"]},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["tasks"][0]["source_title"] == "Owner source"


@pytest.mark.asyncio
async def test_create_task_enqueues_a_job(client, db_session):
    await create_user(db_session, user_id="user-1", email="owner@example.com")

    response = await client.post(
        "/tasks/",
        headers={"x-supoclip-user-id": "user-1"},
        json={
            "source": {"url": "https://www.youtube.com/watch?v=demo"},
            "font_options": {"font_color": "#abcdef", "font_size": 18},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"]
    assert payload["job_id"] == "job-test-1"
