import pytest

from src.services import editor_service
from src.services.editor_service import AssetMetadata
from tests.fixtures.factories import create_source, create_task, create_user


async def _create_owned_task(db_session, *, user_id: str = "user-1") -> str:
    user = await create_user(
        db_session, user_id=user_id, email=f"{user_id}@editor.example.com"
    )
    source = await create_source(db_session, title="Editor source")
    task = await create_task(
        db_session,
        user_id=user["id"],
        source_id=source["id"],
        status="completed",
    )
    return task["id"]


def _project(task_id: str, duration: float) -> dict:
    return {
        "schemaVersion": 1,
        "id": "project-1",
        "name": "Editor project",
        "taskId": task_id,
        "version": 1,
        "canvas": {
            "width": 1080,
            "height": 1920,
            "background": "#000000",
            "fps": 30,
        },
        "duration": duration,
        "items": [],
    }


def _media_item(asset_id: str) -> dict:
    return {
        "id": "item-1",
        "assetId": asset_id,
        "type": "video",
        "name": "Uploaded video",
        "track": "main",
        "start": 0,
        "duration": 4.5,
        "trimStart": 0,
        "speed": 1,
        "volume": 100,
        "muted": False,
        "hidden": False,
        "locked": False,
        "opacity": 100,
        "blendMode": "normal",
        "transform": {"x": 50, "y": 50, "width": 100, "height": 100, "rotation": 0},
        "crop": {"top": 0, "right": 0, "bottom": 0, "left": 0},
        "effects": {"brightness": 100, "contrast": 100, "saturation": 100, "blur": 0, "hue": 0},
        "fadeIn": 0,
        "fadeOut": 0,
    }


@pytest.mark.asyncio
async def test_editor_project_is_versioned_and_rejects_stale_writes(
    client, db_session, auth_headers
):
    task_id = await _create_owned_task(db_session)

    initial = await client.get(f"/tasks/{task_id}/editor", headers=auth_headers)
    assert initial.status_code == 200
    assert initial.json() == {
        "project": None,
        "version": 0,
        "updated_at": None,
        "assets": [],
    }

    first = await client.put(
        f"/tasks/{task_id}/editor",
        headers=auth_headers,
        json={"project": _project(task_id, 30), "expected_version": 0},
    )
    assert first.status_code == 200
    assert first.json()["project"]["duration"] == 30
    assert first.json()["version"] == 1
    assert first.json()["updated_at"]

    stale = await client.put(
        f"/tasks/{task_id}/editor",
        headers=auth_headers,
        json={"project": _project(task_id, 10), "expected_version": 0},
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["current_version"] == 1

    second = await client.put(
        f"/tasks/{task_id}/editor",
        headers=auth_headers,
        json={"project": _project(task_id, 45), "expected_version": 1},
    )
    assert second.status_code == 200
    assert second.json()["version"] == 2

    malformed = await client.put(
        f"/tasks/{task_id}/editor",
        headers=auth_headers,
        json={"project": {"items": []}, "expected_version": 2},
    )
    assert malformed.status_code == 400


@pytest.mark.asyncio
async def test_editor_is_task_owner_scoped(client, db_session, auth_headers):
    task_id = await _create_owned_task(db_session, user_id="user-2")

    response = await client.get(f"/tasks/{task_id}/editor", headers=auth_headers)

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_editor_asset_upload_list_serve_and_delete(
    client, app, db_session, auth_headers, monkeypatch, tmp_path
):
    task_id = await _create_owned_task(db_session)
    app.state.config.temp_dir = str(tmp_path)
    monkeypatch.setattr(
        editor_service,
        "probe_editor_asset",
        lambda _path, _kind: AssetMetadata(
            duration=4.5,
            width=1920,
            height=1080,
            video_codec="h264",
            audio_codec="aac",
            pixel_format="yuv420p",
        ),
    )

    uploaded = await client.post(
        f"/tasks/{task_id}/editor/assets",
        headers=auth_headers,
        files={"file": ("../intro.mp4", b"bounded-video-bytes", "video/mp4")},
    )
    assert uploaded.status_code == 201
    asset = uploaded.json()["asset"]
    assert set(asset) == {
        "id",
        "name",
        "kind",
        "mime_type",
        "size_bytes",
        "duration",
        "width",
        "height",
        "url",
        "created_at",
    }
    assert asset["name"] == "intro.mp4"
    assert asset["kind"] == "video"
    assert asset["size_bytes"] == len(b"bounded-video-bytes")
    assert asset["url"].endswith(f"/{asset['id']}/file")

    listed = await client.get(
        f"/tasks/{task_id}/editor/assets", headers=auth_headers
    )
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["assets"]] == [asset["id"]]

    served = await client.get(asset["url"], headers=auth_headers)
    assert served.status_code == 200
    assert served.content == b"bounded-video-bytes"
    assert served.headers["cache-control"] == "private, no-store"

    project = _project(task_id, 4.5)
    project["items"] = [_media_item(asset["id"])]
    saved = await client.put(
        f"/tasks/{task_id}/editor",
        headers=auth_headers,
        json={"project": project, "expected_version": 0},
    )
    assert saved.status_code == 200

    blocked = await client.delete(
        f"/tasks/{task_id}/editor/assets/{asset['id']}", headers=auth_headers
    )
    assert blocked.status_code == 409

    deleted = await client.delete(
        f"/tasks/{task_id}/editor/assets/{asset['id']}?remove_references=true&expected_version=1",
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["id"] == asset["id"]
    assert deleted.json()["project"]["items"] == []
    assert deleted.json()["version"] == 2
    assert (await client.get(asset["url"], headers=auth_headers)).status_code == 404
    assert list(tmp_path.rglob("*.mp4")) == []


@pytest.mark.asyncio
async def test_editor_asset_rejects_mismatched_and_oversized_uploads(
    client, app, db_session, auth_headers, monkeypatch, tmp_path
):
    task_id = await _create_owned_task(db_session)
    app.state.config.temp_dir = str(tmp_path)

    mismatch = await client.post(
        f"/tasks/{task_id}/editor/assets",
        headers=auth_headers,
        files={"file": ("photo.png", b"not-an-image", "video/mp4")},
    )
    assert mismatch.status_code == 400

    monkeypatch.setitem(editor_service.EDITOR_ASSET_MAX_BYTES, "video", 3)
    oversized = await client.post(
        f"/tasks/{task_id}/editor/assets",
        headers=auth_headers,
        files={"file": ("large.mp4", b"four", "video/mp4")},
    )
    assert oversized.status_code == 413
    assert list(tmp_path.rglob("*.mp4")) == []
