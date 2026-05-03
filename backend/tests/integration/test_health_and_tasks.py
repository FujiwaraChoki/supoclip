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
async def test_list_tasks_only_returns_owned_tasks(client, db_session, auth_headers):
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    other = await create_user(db_session, user_id="user-2", email="other@example.com")
    source_one = await create_source(db_session, title="Owner source")
    source_two = await create_source(db_session, title="Other source")
    await create_task(db_session, user_id=owner["id"], source_id=source_one["id"])
    await create_task(db_session, user_id=other["id"], source_id=source_two["id"])

    response = await client.get(
        "/tasks/",
        headers=auth_headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["tasks"][0]["source_title"] == "Owner source"


@pytest.mark.asyncio
async def test_create_task_enqueues_a_job(client, db_session, auth_headers):
    await create_user(db_session, user_id="user-1", email="owner@example.com")

    response = await client.post(
        "/tasks/",
        headers=auth_headers,
        json={
            "source": {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
            "font_options": {"font_color": "#abcdef", "font_size": 18},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"]
    assert payload["job_id"] == "job-test-1"


@pytest.mark.asyncio
async def test_create_task_rejects_non_upload_local_paths(client, db_session, auth_headers):
    await create_user(db_session, user_id="user-1", email="owner@example.com")

    response = await client.post(
        "/tasks/",
        headers=auth_headers,
        json={
            "source": {"url": "/etc/passwd"},
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Only YouTube URLs or upload:// references are supported"


@pytest.mark.asyncio
async def test_legacy_public_clips_mount_is_not_available(client):
    response = await client.get("/clips/seeded.mp4")

    assert response.status_code == 404


@pytest.mark.asyncio
async def test_upload_video_uses_runtime_config_temp_dir(
    client, app, auth_headers, tmp_path
):
    app.state.config.temp_dir = str(tmp_path)

    response = await client.post(
        "/upload",
        headers=auth_headers,
        files={"video": ("demo.mp4", b"video-bytes", "video/mp4")},
    )

    assert response.status_code == 200
    payload = response.json()
    saved_name = payload["video_path"].removeprefix("upload://")
    assert (tmp_path / "uploads" / saved_name).exists()
