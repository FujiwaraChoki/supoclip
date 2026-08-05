import pytest

from fastapi.routing import APIRoute
from sqlalchemy import text

from src.database import get_db
from tests.fixtures.factories import create_clip, create_source, create_task, create_user


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
async def test_database_health_returns_503_when_the_database_is_unavailable(
    app, client
):
    class UnavailableDatabaseSession:
        async def execute(self, _statement):
            raise RuntimeError("database unavailable")

    async def unavailable_database():
        yield UnavailableDatabaseSession()

    app.dependency_overrides[get_db] = unavailable_database
    try:
        response = await client.get("/health/db")
    finally:
        app.dependency_overrides.pop(get_db, None)

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "database": "disconnected",
    }


@pytest.mark.parametrize(
    "path",
    [
        "/tasks/shared/{share_token}/clips/{clip_id}/file",
        "/tasks/{task_id}/clips/{clip_id}/file",
        "/tasks/{task_id}/clips/{clip_id}/export",
        "/fonts/{font_name}",
    ],
)
def test_database_backed_file_routes_release_sessions_before_streaming(app, path):
    route = next(
        route
        for route in app.routes
        if isinstance(route, APIRoute) and route.path == path
    )
    database_dependency = next(
        dependency
        for dependency in route.dependant.dependencies
        if dependency.call is get_db
    )

    assert database_dependency.scope == "function"


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
async def test_create_task_returns_serializable_subscription_gate(
    client, app, db_session, auth_headers
):
    app.state.config.monetization_enabled = True
    await create_user(db_session, user_id="user-1", email="owner@example.com")

    response = await client.post(
        "/tasks/",
        headers=auth_headers,
        json={"source": {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}},
    )

    assert response.status_code == 402
    payload = response.json()["detail"]
    assert payload["code"] == "SUBSCRIPTION_REQUIRED"
    assert payload["billing"]["period_start"].startswith("20")
    assert payload["billing"]["period_end"].startswith("20")


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
async def test_completed_task_can_be_shared_without_exposing_private_fields(
    client, db_session, auth_headers
):
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Shareable source")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"], text_value="Public transcript")

    share_response = await client.post(
        f"/tasks/{task['id']}/share", headers=auth_headers
    )

    assert share_response.status_code == 200
    share_token = share_response.json()["share_token"]
    assert share_response.json()["share_path"] == f"/share/{share_token}"

    public_response = await client.get(f"/tasks/shared/{share_token}")
    assert public_response.status_code == 200
    payload = public_response.json()
    assert payload["source_title"] == "Shareable source"
    assert payload["clips"][0]["id"] == clip["id"]
    assert payload["clips"][0]["text"] == "Public transcript"
    assert payload["clips"][0]["video_url"].endswith(f"/{clip['id']}/file")
    assert "user_id" not in payload
    assert "source_url" not in payload
    assert "file_path" not in payload["clips"][0]

    repeat_response = await client.post(
        f"/tasks/{task['id']}/share", headers=auth_headers
    )
    assert repeat_response.json()["share_token"] == share_token

    unshare_response = await client.delete(
        f"/tasks/{task['id']}/share", headers=auth_headers
    )
    assert unshare_response.status_code == 200
    assert (await client.get(f"/tasks/shared/{share_token}")).status_code == 404

    reshare_response = await client.post(
        f"/tasks/{task['id']}/share", headers=auth_headers
    )
    rotated_token = reshare_response.json()["share_token"]
    assert rotated_token != share_token
    assert (await client.get(f"/tasks/shared/{share_token}")).status_code == 404
    assert (await client.get(f"/tasks/shared/{rotated_token}")).status_code == 200


@pytest.mark.asyncio
async def test_shared_clip_file_requires_an_enabled_share_token(
    client, db_session, auth_headers, tmp_path
):
    owner = await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Shared video")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"])
    clip_path = tmp_path / "shared.mp4"
    clip_path.write_bytes(b"shared-video-bytes")
    await db_session.execute(
        text("UPDATE generated_clips SET file_path = :path WHERE id = :clip_id"),
        {"path": str(clip_path), "clip_id": clip["id"]},
    )
    await db_session.commit()

    share_response = await client.post(
        f"/tasks/{task['id']}/share", headers=auth_headers
    )
    share_token = share_response.json()["share_token"]

    video_response = await client.get(
        f"/tasks/shared/{share_token}/clips/{clip['id']}/file"
    )
    assert video_response.status_code == 200
    assert video_response.content == b"shared-video-bytes"
    assert video_response.headers["cache-control"] == "private, no-store"

    invalid_response = await client.get(
        f"/tasks/shared/not-a-token/clips/{clip['id']}/file"
    )
    assert invalid_response.status_code == 404


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
