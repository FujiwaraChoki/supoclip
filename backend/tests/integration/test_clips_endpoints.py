"""Integration tests for clip editing endpoints.

Covers the 404/403/validation branches of ``/tasks/{id}/clips/*``. The full
trim/split/merge paths require MoviePy ffmpeg invocations so are not asserted
here — only the guard/validation layers are exercised.
"""

import pytest

from tests.fixtures.factories import (
    create_clip,
    create_source,
    create_task,
    create_user,
)


@pytest.mark.asyncio
async def test_delete_clip_returns_403_for_other_user(client, db_session):
    owner = await create_user(db_session, user_id="clip-owner", email="co@example.com")
    other = await create_user(db_session, user_id="clip-other", email="cto@example.com")
    source = await create_source(db_session, title="Guarded")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"])

    response = await client.delete(
        f"/tasks/{task['id']}/clips/{clip['id']}",
        headers={"x-supoclip-user-id": other["id"]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_clip_returns_404_when_task_missing(client, db_session):
    await create_user(db_session, user_id="ghost-clip", email="gc@example.com")

    response = await client.delete(
        "/tasks/no-task/clips/no-clip",
        headers={"x-supoclip-user-id": "ghost-clip"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_trim_clip_rejects_negative_offsets(client, db_session):
    user = await create_user(db_session, user_id="trimmer", email="tr@example.com")
    source = await create_source(db_session, title="Trim source")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"])

    response = await client.patch(
        f"/tasks/{task['id']}/clips/{clip['id']}",
        headers={"x-supoclip-user-id": user["id"]},
        json={"start_offset": -1, "end_offset": 0},
    )
    # Pydantic schema rejects with 422 (FastAPI validation error)
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_split_clip_rejects_non_positive_time(client, db_session):
    user = await create_user(db_session, user_id="splitter", email="sp@example.com")
    source = await create_source(db_session, title="Split source")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"])

    response = await client.post(
        f"/tasks/{task['id']}/clips/{clip['id']}/split",
        headers={"x-supoclip-user-id": user["id"]},
        json={"split_time": 0},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_merge_clips_rejects_too_few_ids(client, db_session):
    user = await create_user(db_session, user_id="merger", email="mg@example.com")
    source = await create_source(db_session, title="Merge")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="completed"
    )

    response = await client.post(
        f"/tasks/{task['id']}/clips/merge",
        headers={"x-supoclip-user-id": user["id"]},
        json={"clip_ids": ["one"]},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_captions_rejects_invalid_position(client, db_session):
    user = await create_user(db_session, user_id="cap-user", email="cp@example.com")
    source = await create_source(db_session, title="Captions")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"])

    response = await client.patch(
        f"/tasks/{task['id']}/clips/{clip['id']}/captions",
        headers={"x-supoclip-user-id": user["id"]},
        json={"caption_text": "hi", "position": "nowhere"},
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_clip_edit_endpoints_require_auth(client, db_session):
    # Missing x-supoclip-user-id header should not reach the service layer
    response = await client.patch(
        "/tasks/any/clips/any",
        json={"start_offset": 0, "end_offset": 0},
    )
    assert response.status_code in (401, 403)

    response = await client.delete("/tasks/any/clips/any")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_trim_forbidden_for_other_user(client, db_session):
    owner = await create_user(db_session, user_id="t-owner", email="to@example.com")
    other = await create_user(db_session, user_id="t-other", email="tot@example.com")
    source = await create_source(db_session, title="Guarded trim")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="completed"
    )
    clip = await create_clip(db_session, task_id=task["id"])

    response = await client.patch(
        f"/tasks/{task['id']}/clips/{clip['id']}",
        headers={"x-supoclip-user-id": other["id"]},
        json={"start_offset": 0, "end_offset": 0},
    )
    assert response.status_code == 403
