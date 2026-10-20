from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import editor
from src.database import get_db
from src.editor_document import EditDocument, atomic_json, editor_dir


@pytest.fixture
async def client(tmp_path, monkeypatch):
    task = {"id": "task", "user_id": "owner", "status": "completed"}
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4")}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        config=SimpleNamespace(temp_dir=str(tmp_path)),
    )
    monkeypatch.setattr(editor, "TaskService", lambda db: service)
    monkeypatch.setattr(
        "src.api.routes.tasks._get_user_id_from_headers",
        AsyncMock(return_value="owner"),
    )
    monkeypatch.setattr(editor.JobQueue, "enqueue_job", AsyncMock(return_value="job"))

    db = SimpleNamespace(close=AsyncMock())
    app = FastAPI()
    app.include_router(editor.router)
    app.dependency_overrides[get_db] = lambda: db
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    doc = EditDocument.model_validate(
        {"segments": [{"id": "one", "start": 0, "end": 2}]}
    ).model_dump()
    atomic_json(directory / "draft.json", {"revision": 0, "document": doc})
    atomic_json(directory / "original.json", doc)
    atomic_json(directory / "asset.json", {"status": "ready", "duration": 2})
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http, service, doc, directory


async def test_drafts_require_task_and_clip_ownership(client, monkeypatch):
    http, service, doc, directory = client
    monkeypatch.setattr(
        "src.api.routes.tasks._get_user_id_from_headers",
        AsyncMock(return_value="outsider"),
    )
    assert (await http.get("/tasks/task/clips/clip/editor")).status_code == 403
    assert (
        await http.patch(
            "/tasks/task/clips/clip/editor",
            json={"basis": "clip.mp4", "revision": 0, "document": doc},
        )
    ).status_code == 403
    monkeypatch.setattr(
        "src.api.routes.tasks._get_user_id_from_headers",
        AsyncMock(return_value="owner"),
    )
    service.clip_repo.get_clip_by_id.return_value = {
        "id": "clip",
        "task_id": "other",
        "filename": "clip.mp4",
    }
    assert (await http.get("/tasks/task/clips/clip/editor")).status_code == 404


async def test_revision_and_basis_prevent_stale_writes(client):
    http, service, doc, directory = client
    payload = {"basis": "clip.mp4", "revision": 0, "document": doc}
    assert (await http.patch("/tasks/task/clips/clip/editor", json=payload)).json()[
        "revision"
    ] == 1
    assert (
        await http.patch("/tasks/task/clips/clip/editor", json=payload)
    ).status_code == 409
    payload["basis"] = "previous.mp4"
    assert (
        await http.patch("/tasks/task/clips/clip/editor", json=payload)
    ).status_code == 409
    payload.update(basis="clip.mp4", revision=1, document={**doc, "volume": -1})
    assert (
        await http.patch("/tasks/task/clips/clip/editor", json=payload)
    ).status_code == 400


async def test_export_snapshot_cancel_and_private_download(client):
    http, service, doc, directory = client
    response = await http.post(
        "/tasks/task/clips/clip/editor/exports",
        json={"basis": "clip.mp4", "document": doc, "preset": "reels"},
    )
    assert response.status_code == 202
    job = response.json()["id"]
    assert (directory / f"request-{job}.json").exists()
    assert (
        await http.get(f"/tasks/task/clips/clip/editor/exports/{job}/file")
    ).status_code == 409
    assert (
        await http.delete(f"/tasks/task/clips/clip/editor/exports/{job}")
    ).status_code == 200
    assert (directory / f"cancel-{job}").exists()
    assert (
        await http.get("/tasks/task/clips/clip/editor/media/draft.json")
    ).status_code == 404
    assert (
        await http.get("/tasks/task/clips/clip/editor/exports/not-a-job")
    ).status_code == 404


async def test_failed_enqueue_is_retryable(client, monkeypatch):
    http, service, doc, directory = client
    monkeypatch.setattr(
        editor.JobQueue, "enqueue_job", AsyncMock(side_effect=ConnectionError())
    )
    response = await http.post(
        "/tasks/task/clips/clip/editor/exports",
        json={"basis": "clip.mp4", "document": doc},
    )
    assert response.status_code == 503
    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    assert state["jobs"][0]["status"] == "failed"


@pytest.mark.parametrize("wrapped", [False, True])
async def test_autosave_returns_new_revision_when_snapshot_sync_is_unavailable(client, monkeypatch, wrapped):
    from botocore.exceptions import EndpointConnectionError
    from boto3.exceptions import S3UploadFailedError
    http, service, doc, directory = client
    failure = S3UploadFailedError("SlowDown") if wrapped else EndpointConnectionError(endpoint_url="https://storage.test")
    monkeypatch.setattr(editor.MediaStorage, "save_editor", lambda *a, **k: (_ for _ in ()).throw(failure))
    payload = {"basis": "clip.mp4", "revision": 0, "document": doc}
    saved = await http.patch("/tasks/task/clips/clip/editor", json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    payload["revision"] = 1
    assert (await http.patch("/tasks/task/clips/clip/editor", json=payload)).json()["revision"] == 2


async def test_local_preview_and_export_survive_storage_outage(client, monkeypatch):
    from botocore.exceptions import EndpointConnectionError
    http, service, doc, directory = client
    (directory / ".s3-manifest").write_text('{}')
    (directory / "clean.mp4").write_bytes(b"local preview")
    job = "f" * 32
    atomic_json(directory / f"job-{job}.json", {"status": "completed"})
    (directory / f"export-{job}.mp4").write_bytes(b"local export")
    monkeypatch.setattr(editor.MediaStorage, "exists", lambda *a: (_ for _ in ()).throw(EndpointConnectionError(endpoint_url="https://storage.test")))
    preview = await http.get("/tasks/task/clips/clip/editor/media/clean.mp4")
    export = await http.get(f"/tasks/task/clips/clip/editor/exports/{job}/file")
    assert preview.status_code == export.status_code == 200
    assert preview.content == b"local preview"
    assert export.content == b"local export"


async def test_old_drafts_regain_template_animation_and_hook_title(client):
    http, service, doc, directory = client
    service.task_repo.get_task_by_id.return_value = {
        "id": "task",
        "user_id": "owner",
        "status": "completed",
        "caption_template": "hormozi",
    }
    service.clip_repo.get_clip_by_id.return_value = {
        **service.clip_repo.get_clip_by_id.return_value,
        "hook_title": "Nobody tells you this",
    }
    # A draft saved before the editor kept the template and hook title.
    old = {**doc, "captions": {k: v for k, v in doc["captions"].items() if k not in {"template", "karaoke", "uppercase"}}}
    old.pop("hookTitle")
    atomic_json(directory / "draft.json", {"revision": 3, "document": old})

    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    captions = state["draft"]["document"]["captions"]
    assert captions["template"] == "hormozi"
    assert captions["karaoke"] is True and captions["uppercase"] is True
    assert state["draft"]["document"]["hookTitle"] == "Nobody tells you this"
    assert state["draft"]["revision"] == 3
    assert state["captionLooks"]["hormozi"]["wordBox"] is True

    # Drafts that already chose these values keep them.
    current = {**doc, "hookTitle": "", "captions": {**doc["captions"], "karaoke": False}}
    atomic_json(directory / "draft.json", {"revision": 4, "document": current})
    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    assert state["draft"]["document"]["hookTitle"] == ""
    assert state["draft"]["document"]["captions"]["karaoke"] is False
