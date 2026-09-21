"""
Tests for the Ranking/Compilation tool's data-model + API layer:
  - creating a ranking task, attaching inputs, reordering, manual rank override
  - ranking_settings persistence
  - render enqueues the correct worker job (FakeQueueAdapter, no real ffmpeg)
  - GET /tasks/ surfaces task_type so clipping and ranking projects share one list
"""

import pytest

from tests.fixtures.factories import create_source, create_task


@pytest.mark.asyncio
async def test_ranking_task_lifecycle(client, db_session):
    create_response = await client.post("/ranking/tasks", json={})
    assert create_response.status_code == 200
    task_id = create_response.json()["task_id"]

    add_a = await client.post(
        f"/ranking/tasks/{task_id}/inputs",
        json={
            "file_path": "upload://a.mp4",
            "original_filename": "a.mp4",
            "duration_seconds": 5.0,
        },
    )
    assert add_a.status_code == 200
    input_a = add_a.json()["input_id"]

    add_b = await client.post(
        f"/ranking/tasks/{task_id}/inputs",
        json={
            "file_path": "upload://b.mp4",
            "original_filename": "b.mp4",
            "duration_seconds": 4.0,
        },
    )
    assert add_b.status_code == 200
    input_b = add_b.json()["input_id"]

    # Auto-numbered by insertion order (top = #1) until a manual rank is set.
    inputs_response = await client.get(f"/ranking/tasks/{task_id}/inputs")
    ordered_ids = [i["id"] for i in inputs_response.json()["inputs"]]
    assert ordered_ids == [input_a, input_b]

    # Manual rank override wins over drag order.
    rank_response = await client.patch(
        f"/ranking/tasks/{task_id}/inputs/{input_b}/rank", json={"rank_position": 1}
    )
    assert rank_response.status_code == 200

    inputs_response = await client.get(f"/ranking/tasks/{task_id}/inputs")
    ordered_ids = [i["id"] for i in inputs_response.json()["inputs"]]
    assert ordered_ids == [input_b, input_a]

    # Reordering via drag doesn't clear the manual rank override.
    reorder_response = await client.patch(
        f"/ranking/tasks/{task_id}/inputs/order",
        json={"ordered_input_ids": [input_a, input_b]},
    )
    assert reorder_response.status_code == 200
    inputs_response = await client.get(f"/ranking/tasks/{task_id}/inputs")
    ordered_ids = [i["id"] for i in inputs_response.json()["inputs"]]
    assert ordered_ids == [input_b, input_a]

    # Settings persist as JSON and merge on subsequent partial updates.
    settings_response = await client.patch(
        f"/ranking/tasks/{task_id}/settings",
        json={"template_id": "rapid_fire", "export_preset": "tiktok"},
    )
    assert settings_response.status_code == 200
    assert settings_response.json()["settings"] == {
        "template_id": "rapid_fire",
        "export_preset": "tiktok",
    }
    merge_response = await client.patch(
        f"/ranking/tasks/{task_id}/settings", json={"export_preset": "reels"}
    )
    assert merge_response.json()["settings"] == {
        "template_id": "rapid_fire",
        "export_preset": "reels",
    }

    # Rendering enqueues the ranking-specific worker function (captured by
    # FakeQueueAdapter, no real ffmpeg run in this test).
    render_response = await client.post(f"/ranking/tasks/{task_id}/render")
    assert render_response.status_code == 200

    task_response = await client.get(f"/tasks/{task_id}")
    assert task_response.json()["task_type"] == "ranking"
    assert task_response.json()["status"] == "queued"

    # Removing an input drops it from the ordered list.
    remove_response = await client.delete(f"/ranking/tasks/{task_id}/inputs/{input_a}")
    assert remove_response.status_code == 200
    inputs_response = await client.get(f"/ranking/tasks/{task_id}/inputs")
    assert [i["id"] for i in inputs_response.json()["inputs"]] == [input_b]


@pytest.mark.asyncio
async def test_duplicate_copies_inputs_and_settings_as_a_new_draft_task(client, db_session):
    create_response = await client.post("/ranking/tasks", json={})
    task_id = create_response.json()["task_id"]

    await client.post(
        f"/ranking/tasks/{task_id}/inputs",
        json={"file_path": "upload://a.mp4", "original_filename": "a.mp4", "duration_seconds": 5.0},
    )
    add_b = await client.post(
        f"/ranking/tasks/{task_id}/inputs",
        json={"file_path": "upload://b.mp4", "original_filename": "b.mp4", "duration_seconds": 4.0},
    )
    input_b = add_b.json()["input_id"]
    await client.patch(f"/ranking/tasks/{task_id}/inputs/{input_b}/rank", json={"rank_position": 1})
    await client.patch(
        f"/ranking/tasks/{task_id}/settings",
        json={"template_id": "countdown", "export_preset": "reels"},
    )

    duplicate_response = await client.post(f"/ranking/tasks/{task_id}/duplicate")
    assert duplicate_response.status_code == 200
    new_task_id = duplicate_response.json()["task_id"]
    assert new_task_id != task_id

    new_task_response = await client.get(f"/tasks/{new_task_id}")
    assert new_task_response.json()["task_type"] == "ranking"
    assert new_task_response.json()["status"] == "draft"

    new_inputs_response = await client.get(f"/ranking/tasks/{new_task_id}/inputs")
    new_inputs = new_inputs_response.json()["inputs"]
    assert len(new_inputs) == 2
    assert {i["original_filename"] for i in new_inputs} == {"a.mp4", "b.mp4"}
    # Manual rank override carried over — b is still ranked #1.
    assert new_inputs[0]["original_filename"] == "b.mp4"
    assert new_inputs[0]["rank_position"] == 1

    # New rows, not shared with the source task.
    original_inputs = (await client.get(f"/ranking/tasks/{task_id}/inputs")).json()["inputs"]
    assert {i["id"] for i in new_inputs}.isdisjoint({i["id"] for i in original_inputs})

    # Settings aren't part of GET /tasks/{id}'s response shape, so confirm
    # via the ranking settings PATCH merge behavior instead: patching an
    # unrelated field should leave template_id as the duplicated value.
    merge_response = await client.patch(
        f"/ranking/tasks/{new_task_id}/settings", json={"export_preset": "tiktok"}
    )
    assert merge_response.json()["settings"]["template_id"] == "countdown"


@pytest.mark.asyncio
async def test_duplicate_rejects_a_project_with_no_inputs(client, db_session):
    create_response = await client.post("/ranking/tasks", json={})
    task_id = create_response.json()["task_id"]

    duplicate_response = await client.post(f"/ranking/tasks/{task_id}/duplicate")
    assert duplicate_response.status_code == 400


@pytest.mark.asyncio
async def test_render_requires_at_least_two_inputs(client, db_session):
    create_response = await client.post("/ranking/tasks", json={})
    task_id = create_response.json()["task_id"]

    render_response = await client.post(f"/ranking/tasks/{task_id}/render")
    assert render_response.status_code == 400

    await client.post(
        f"/ranking/tasks/{task_id}/inputs",
        json={"file_path": "upload://a.mp4", "original_filename": "a.mp4"},
    )
    render_response = await client.post(f"/ranking/tasks/{task_id}/render")
    assert render_response.status_code == 400


@pytest.mark.asyncio
async def test_tasks_list_surfaces_task_type_for_both_project_types(client, db_session):
    source = await create_source(db_session, title="Clipping test video")
    clipping_task = await create_task(
        db_session, user_id="local", source_id=source["id"], status="completed"
    )

    ranking_response = await client.post("/ranking/tasks", json={})
    ranking_task_id = ranking_response.json()["task_id"]

    list_response = await client.get("/tasks/")
    tasks_by_id = {t["id"]: t for t in list_response.json()["tasks"]}
    assert tasks_by_id[clipping_task["id"]]["task_type"] == "clipping"
    assert tasks_by_id[ranking_task_id]["task_type"] == "ranking"


@pytest.mark.asyncio
async def test_ranking_templates_and_export_presets_endpoints(client):
    templates_response = await client.get("/ranking/templates")
    assert templates_response.status_code == 200
    templates = templates_response.json()["templates"]
    template_ids = [t["id"] for t in templates]
    assert {"rapid_fire", "countdown", "ranking_list"} <= set(template_ids)
    for template in templates:
        assert template["preview_url"] == f"/ranking/templates/{template['id']}/preview"

    presets_response = await client.get("/ranking/export-presets")
    assert presets_response.status_code == 200
    preset_ids = [p["id"] for p in presets_response.json()["presets"]]
    assert "tiktok" in preset_ids


@pytest.mark.asyncio
async def test_ranking_template_preview_serves_svg_and_404s_for_unknown_id(client):
    ok_response = await client.get("/ranking/templates/rapid_fire/preview")
    assert ok_response.status_code == 200
    assert ok_response.headers["content-type"].startswith("image/svg+xml")

    missing_response = await client.get("/ranking/templates/does-not-exist/preview")
    assert missing_response.status_code == 404
