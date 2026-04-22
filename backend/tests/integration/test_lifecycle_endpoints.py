"""Integration tests for task lifecycle endpoints.

Covers happy-path + 404 + 403 branches for GET /tasks/{id}, DELETE /tasks/{id},
POST /tasks/{id}/cancel, and validation errors on POST /tasks/.
"""

import pytest

from tests.fixtures.factories import (
    create_clip,
    create_source,
    create_task,
    create_user,
)


@pytest.mark.asyncio
async def test_get_task_returns_details_with_clips(client, db_session):
    user = await create_user(db_session, user_id="owner-1", email="owner1@example.com")
    source = await create_source(db_session, title="My source")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="completed"
    )
    await create_clip(db_session, task_id=task["id"], text_value="first clip")

    response = await client.get(
        f"/tasks/{task['id']}",
        headers={"x-supoclip-user-id": user["id"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == task["id"]
    assert payload["clips_count"] == 1
    assert payload["clips"][0]["text"] == "first clip"


@pytest.mark.asyncio
async def test_get_task_returns_404_when_missing(client, db_session):
    await create_user(db_session, user_id="nobody", email="nobody@example.com")

    response = await client.get(
        "/tasks/does-not-exist",
        headers={"x-supoclip-user-id": "nobody"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_task_forbidden_for_other_user(client, db_session):
    owner = await create_user(db_session, user_id="owner-x", email="ox@example.com")
    other = await create_user(db_session, user_id="other-x", email="otx@example.com")
    source = await create_source(db_session, title="Guarded")
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"])

    response = await client.get(
        f"/tasks/{task['id']}",
        headers={"x-supoclip-user-id": other["id"]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_task_success(client, db_session):
    user = await create_user(db_session, user_id="deleter", email="del@example.com")
    source = await create_source(db_session, title="To delete")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"])

    response = await client.delete(
        f"/tasks/{task['id']}",
        headers={"x-supoclip-user-id": user["id"]},
    )
    assert response.status_code == 200

    # Second GET confirms gone (404)
    followup = await client.get(
        f"/tasks/{task['id']}",
        headers={"x-supoclip-user-id": user["id"]},
    )
    assert followup.status_code == 404


@pytest.mark.asyncio
async def test_delete_task_returns_403_for_other_user(client, db_session):
    owner = await create_user(db_session, user_id="d-owner", email="do@example.com")
    other = await create_user(db_session, user_id="d-other", email="doo@example.com")
    source = await create_source(db_session, title="Protected")
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"])

    response = await client.delete(
        f"/tasks/{task['id']}",
        headers={"x-supoclip-user-id": other["id"]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_task_returns_404_when_missing(client, db_session):
    await create_user(db_session, user_id="ghost", email="ghost@example.com")

    response = await client.delete(
        "/tasks/ghost-id",
        headers={"x-supoclip-user-id": "ghost"},
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_task_clips_endpoint(client, db_session):
    user = await create_user(db_session, user_id="clipper", email="c@example.com")
    source = await create_source(db_session, title="Clipped")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="completed"
    )
    await create_clip(db_session, task_id=task["id"], text_value="a")
    await create_clip(db_session, task_id=task["id"], text_value="b")

    response = await client.get(
        f"/tasks/{task['id']}/clips",
        headers={"x-supoclip-user-id": user["id"]},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task["id"]
    assert payload["total_clips"] == 2
    assert sorted(c["text"] for c in payload["clips"]) == ["a", "b"]


@pytest.mark.asyncio
async def test_cancel_task_acknowledged_when_already_terminal(client, db_session):
    user = await create_user(db_session, user_id="canceller", email="cx@example.com")
    source = await create_source(db_session, title="Done already")
    task = await create_task(
        db_session, user_id=user["id"], source_id=source["id"], status="completed"
    )

    response = await client.post(
        f"/tasks/{task['id']}/cancel",
        headers={"x-supoclip-user-id": user["id"]},
    )
    assert response.status_code == 200
    assert "already" in response.json()["message"].lower()


@pytest.mark.asyncio
async def test_cancel_task_forbidden_for_other_user(client, db_session):
    owner = await create_user(db_session, user_id="c-owner", email="co@example.com")
    other = await create_user(db_session, user_id="c-other", email="coo@example.com")
    source = await create_source(db_session, title="Running")
    task = await create_task(
        db_session, user_id=owner["id"], source_id=source["id"], status="processing"
    )

    response = await client.post(
        f"/tasks/{task['id']}/cancel",
        headers={"x-supoclip-user-id": other["id"]},
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_create_task_requires_user_header(client, db_session):
    response = await client.post(
        "/tasks/",
        json={"source": {"url": "https://www.youtube.com/watch?v=demo"}},
    )
    # No x-supoclip-user-id header → 401/403 depending on auth impl
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_task_rejects_missing_source(client, db_session):
    await create_user(db_session, user_id="u-missing", email="um@example.com")

    response = await client.post(
        "/tasks/",
        headers={"x-supoclip-user-id": "u-missing"},
        json={},
    )
    # missing source should be a 4xx
    assert 400 <= response.status_code < 500


@pytest.mark.asyncio
async def test_list_tasks_empty_for_new_user(client, db_session):
    await create_user(db_session, user_id="fresh-user", email="fresh@example.com")

    response = await client.get(
        "/tasks/",
        headers={"x-supoclip-user-id": "fresh-user"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["tasks"] == []


@pytest.mark.asyncio
async def test_create_task_rejects_invalid_font_color(client, db_session):
    await create_user(db_session, user_id="styler", email="st@example.com")

    response = await client.post(
        "/tasks/",
        headers={"x-supoclip-user-id": "styler"},
        json={
            "source": {"url": "https://www.youtube.com/watch?v=demo"},
            "font_options": {"font_color": "not-a-hex"},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_task_rejects_oversized_font(client, db_session):
    await create_user(db_session, user_id="big-font", email="bf@example.com")

    response = await client.post(
        "/tasks/",
        headers={"x-supoclip-user-id": "big-font"},
        json={
            "source": {"url": "https://www.youtube.com/watch?v=demo"},
            "font_options": {"font_size": 999},
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_task_rejects_unknown_processing_mode(client, db_session):
    await create_user(db_session, user_id="mode-user", email="mm@example.com")

    response = await client.post(
        "/tasks/",
        headers={"x-supoclip-user-id": "mode-user"},
        json={
            "source": {"url": "https://www.youtube.com/watch?v=demo"},
            "processing_mode": "ultra-extra-hd",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_task_rejects_unknown_output_format(client, db_session):
    await create_user(db_session, user_id="fmt-user", email="fm@example.com")

    response = await client.post(
        "/tasks/",
        headers={"x-supoclip-user-id": "fmt-user"},
        json={
            "source": {"url": "https://www.youtube.com/watch?v=demo"},
            "output_format": "cinematic-4k",
        },
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_task_settings_rejects_bad_color(client, db_session):
    user = await create_user(db_session, user_id="set-user", email="sx@example.com")
    source = await create_source(db_session, title="Settings")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"])

    response = await client.post(
        f"/tasks/{task['id']}/settings",
        headers={"x-supoclip-user-id": user["id"]},
        json={"font_color": "rgb(1,2,3)"},
    )
    assert response.status_code == 422
