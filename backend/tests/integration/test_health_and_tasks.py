from unittest.mock import AsyncMock, patch

import pytest

from tests.fixtures.factories import (
    create_clip,
    create_source,
    create_task,
    create_user,
)


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
    assert (
        response.json()["detail"]
        == "Only YouTube URLs, upload:// references, or http(s):// URLs are supported"
    )


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


@pytest.mark.asyncio
async def test_merge_async_enqueues_and_returns_job_id(
    client, db_session, auth_headers
):
    await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Owner source")
    task = await create_task(db_session, user_id="user-1", source_id=source["id"])
    clip_a = await create_clip(db_session, task_id=task["id"])
    clip_b = await create_clip(db_session, task_id=task["id"])

    with patch(
        "src.api.routes.tasks.JobQueue.enqueue_job",
        new=AsyncMock(return_value="merge-job-xyz"),
    ) as enqueue:
        response = await client.post(
            f"/tasks/{task['id']}/clips/merge_async",
            headers=auth_headers,
            json={"clip_ids": [clip_a["id"], clip_b["id"]]},
        )

    assert response.status_code == 202
    payload = response.json()
    assert payload == {"merge_job_id": "merge-job-xyz", "status": "queued"}
    enqueue.assert_awaited_once_with(
        "merge_clips_job", task["id"], [clip_a["id"], clip_b["id"]]
    )


@pytest.mark.asyncio
async def test_merge_async_rejects_unknown_clip(client, db_session, auth_headers):
    await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Owner source")
    task = await create_task(db_session, user_id="user-1", source_id=source["id"])
    clip = await create_clip(db_session, task_id=task["id"])

    # Don't even hit the queue if validation fails — guards against
    # burning a worker slot to discover a typo.
    with patch(
        "src.api.routes.tasks.JobQueue.enqueue_job",
        new=AsyncMock(return_value="should-not-be-called"),
    ) as enqueue:
        response = await client.post(
            f"/tasks/{task['id']}/clips/merge_async",
            headers=auth_headers,
            json={"clip_ids": [clip["id"], "ghost-clip-id"]},
        )

    assert response.status_code == 404
    assert "ghost-clip-id" in response.json()["detail"]
    enqueue.assert_not_awaited()


@pytest.mark.asyncio
async def test_merge_async_rejects_single_clip(client, db_session, auth_headers):
    await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Owner source")
    task = await create_task(db_session, user_id="user-1", source_id=source["id"])
    clip = await create_clip(db_session, task_id=task["id"])

    response = await client.post(
        f"/tasks/{task['id']}/clips/merge_async",
        headers=auth_headers,
        json={"clip_ids": [clip["id"]]},
    )

    assert response.status_code == 400
    assert "two" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_get_merge_job_returns_completion_result(
    client, db_session, auth_headers
):
    await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Owner source")
    task = await create_task(db_session, user_id="user-1", source_id=source["id"])

    with patch(
        "src.api.routes.tasks.JobQueue.get_job_status",
        new=AsyncMock(return_value="JobStatus.complete"),
    ), patch(
        "src.api.routes.tasks.JobQueue.get_job_result",
        new=AsyncMock(return_value={"clip_id": "merged-1", "message": "ok"}),
    ):
        response = await client.get(
            f"/tasks/{task['id']}/clips/merge_jobs/job-abc",
            headers=auth_headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload == {
        "merge_job_id": "job-abc",
        "status": "complete",
        "clip_id": "merged-1",
        "message": "ok",
    }


@pytest.mark.asyncio
async def test_get_merge_job_surfaces_worker_error(client, db_session, auth_headers):
    await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Owner source")
    task = await create_task(db_session, user_id="user-1", source_id=source["id"])

    with patch(
        "src.api.routes.tasks.JobQueue.get_job_status",
        new=AsyncMock(return_value="complete"),
    ), patch(
        "src.api.routes.tasks.JobQueue.get_job_result",
        new=AsyncMock(side_effect=RuntimeError("ffmpeg exit 254")),
    ):
        response = await client.get(
            f"/tasks/{task['id']}/clips/merge_jobs/job-bad",
            headers=auth_headers,
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "complete"
    assert "ffmpeg exit 254" in payload["error"]


@pytest.mark.asyncio
async def test_get_merge_job_returns_404_when_unknown(
    client, db_session, auth_headers
):
    await create_user(db_session, user_id="user-1", email="owner@example.com")
    source = await create_source(db_session, title="Owner source")
    task = await create_task(db_session, user_id="user-1", source_id=source["id"])

    with patch(
        "src.api.routes.tasks.JobQueue.get_job_status",
        new=AsyncMock(return_value=None),
    ):
        response = await client.get(
            f"/tasks/{task['id']}/clips/merge_jobs/ghost",
            headers=auth_headers,
        )

    assert response.status_code == 404
