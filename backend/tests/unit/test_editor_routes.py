from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import editor
from src.database import get_db
from src.editor_document import EditDocument, atomic_json, editor_dir
from src.editor_state import editor_key


@pytest.fixture
async def client(tmp_path, monkeypatch, editor_store):
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
    editor_store.put_ready("task", "clip", "clip.mp4", {"duration": 2}, doc)
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


async def test_export_snapshot_cancel_and_private_download(client, editor_store):
    http, service, doc, directory = client
    response = await http.post(
        "/tasks/task/clips/clip/editor/exports",
        json={"basis": "clip.mp4", "document": doc, "preset": "reels"},
    )
    assert response.status_code == 202
    job = response.json()["id"]
    # The job carries the document it will render; no file on this machine does.
    stored = editor_store.jobs_by_id[job]
    assert stored["request"] == {"document": doc, "preset": "reels"}
    assert not directory.exists() or not list(directory.glob("*.json"))
    queued = editor.JobQueue.enqueue_job.await_args
    assert queued.args == ("export_editor", "task", "clip", "clip.mp4", job)
    assert queued.kwargs == {"_job_id": f"editor-job:{job}"}
    assert (
        await http.get(f"/tasks/task/clips/clip/editor/exports/{job}/file")
    ).status_code == 409
    cancelled = await http.delete(f"/tasks/task/clips/clip/editor/exports/{job}")
    assert cancelled.status_code == 200 and cancelled.json() == {"status": "cancelled"}
    assert stored["status"] == "cancelled" and stored["cancel_requested"]
    # A worker that receives the queue job afterwards finds nothing to claim.
    assert await editor_store.start_job(job) is None
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


async def test_autosave_does_not_depend_on_object_storage_or_local_files(client, monkeypatch, editor_store):
    http, service, doc, directory = client

    def unavailable(*args, **kwargs):
        raise AssertionError("Saving a draft must not touch media storage")

    monkeypatch.setattr(editor.MediaStorage, "client", property(unavailable))
    payload = {"basis": "clip.mp4", "revision": 0, "document": doc}
    saved = await http.patch("/tasks/task/clips/clip/editor", json=payload)
    assert saved.status_code == 200
    assert saved.json()["revision"] == 1
    payload["revision"] = 1
    assert (await http.patch("/tasks/task/clips/clip/editor", json=payload)).json()["revision"] == 2
    assert not directory.exists() or not list(directory.glob("*.json"))


async def test_a_session_written_as_files_is_imported_once_with_its_jobs(client, editor_store):
    """Drafts saved by the file-based editor must open after the upgrade."""
    http, service, doc, directory = client
    editor_store.sessions.clear()
    edited = {**doc, "muted": True}
    atomic_json(directory / "draft.json", {"revision": 7, "document": edited})
    atomic_json(directory / "original.json", doc)
    atomic_json(directory / "asset.json", {"status": "ready", "duration": 2})
    job = "a" * 32
    atomic_json(directory / f"job-{job}.json", {"status": "completed", "progress": 100})
    atomic_json(directory / f"request-{job}.json", {"document": doc, "preset": "tiktok"})

    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    assert state["status"] == "ready"
    assert state["draft"]["revision"] == 7 and state["draft"]["document"]["muted"] is True
    assert state["original"]["muted"] is False
    assert state["jobs"] == [{"id": job, "status": "completed", "progress": 100}]

    # From here the stored session is the truth; the old files are ignored.
    payload = {"basis": "clip.mp4", "revision": 7, "document": doc}
    assert (await http.patch("/tasks/task/clips/clip/editor", json=payload)).json()["revision"] == 8
    atomic_json(directory / "draft.json", {"revision": 99, "document": edited})
    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    assert state["draft"]["revision"] == 8 and state["draft"]["document"]["muted"] is False


async def test_only_one_request_starts_a_preparation(client, editor_store):
    http, service, doc, directory = client
    editor_store.sessions.clear()
    first = await http.post("/tasks/task/clips/clip/editor/prepare")
    second = await http.post("/tasks/task/clips/clip/editor/prepare")
    assert first.status_code == second.status_code == 202
    assert first.json() == second.json() == {"status": "preparing"}
    assert editor.JobQueue.enqueue_job.await_count == 1
    assert (await http.get("/tasks/task/clips/clip/editor")).json()["status"] == "preparing"


async def test_a_preparation_whose_worker_died_can_be_retried(client, editor_store):
    from src.editor_state import STALE_PREPARE_SECONDS

    http, service, doc, directory = client
    editor_store.sessions.clear()
    await http.post("/tasks/task/clips/clip/editor/prepare")
    key = editor_key("task", "clip", "clip.mp4")
    await editor_store.begin_prepare(key, "task", "clip", "clip.mp4")
    assert (await http.get("/tasks/task/clips/clip/editor")).json()["status"] == "preparing"
    # The worker stopped heartbeating a while ago.
    editor_store.sessions[key]["updated"] -= STALE_PREPARE_SECONDS + 1
    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    assert state["status"] == "failed" and "interrupted" in state["error"]
    assert (await http.post("/tasks/task/clips/clip/editor/prepare")).json() == {"status": "preparing"}
    assert editor.JobQueue.enqueue_job.await_count == 2


async def test_a_render_whose_worker_died_frees_its_slot(client, editor_store):
    from src.editor_state import STALE_RENDER_SECONDS

    http, service, doc, directory = client
    body = {"basis": "clip.mp4", "document": doc}
    jobs = [(await http.post("/tasks/task/clips/clip/editor/exports", json=body)).json()["id"] for _ in range(3)]
    assert (await http.post("/tasks/task/clips/clip/editor/exports", json=body)).status_code == 429
    assert await editor_store.start_job(jobs[0]) is not None
    editor_store.jobs_by_id[jobs[0]]["updated"] -= STALE_RENDER_SECONDS + 1
    status = (await http.get(f"/tasks/task/clips/clip/editor/exports/{jobs[0]}")).json()
    assert status["status"] == "failed" and "stopped" in status["error"]
    assert (await http.post("/tasks/task/clips/clip/editor/exports", json=body)).status_code == 202


async def test_local_preview_and_export_survive_storage_outage(client, monkeypatch, editor_store):
    from botocore.exceptions import EndpointConnectionError
    http, service, doc, directory = client
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "clean.mp4").write_bytes(b"local preview")
    job = "f" * 32
    key = editor_key("task", "clip", "clip.mp4")
    await editor_store.create_job(key, job, "export", {"document": doc, "preset": "tiktok"})
    claim = (await editor_store.start_job(job))["claim"]
    await editor_store.finish_job(job, claim, "completed")
    (directory / f"export-{job}.mp4").write_bytes(b"local export")
    monkeypatch.setattr(editor.MediaStorage, "enabled", True, raising=False)
    monkeypatch.setattr(editor.MediaStorage, "editor_reference", lambda self, directory, name="": f"s3://bucket/media/editor/{name}")
    monkeypatch.setattr(editor.MediaStorage, "exists", lambda *a: (_ for _ in ()).throw(EndpointConnectionError(endpoint_url="https://storage.test")))
    preview = await http.get("/tasks/task/clips/clip/editor/media/clean.mp4")
    export = await http.get(f"/tasks/task/clips/clip/editor/exports/{job}/file")
    assert preview.status_code == export.status_code == 200
    assert preview.content == b"local preview"
    assert export.content == b"local export"


async def test_old_drafts_regain_template_animation_and_hook_title(client, editor_store):
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
    editor_store.put_ready("task", "clip", "clip.mp4", {"duration": 2}, old, revision=3)

    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    captions = state["draft"]["document"]["captions"]
    assert captions["template"] == "hormozi"
    assert captions["karaoke"] is True and captions["uppercase"] is True
    assert state["draft"]["document"]["hookTitle"] == "Nobody tells you this"
    assert state["draft"]["revision"] == 3
    assert state["captionLooks"]["hormozi"]["wordBox"] is True

    # Drafts that already chose these values keep them.
    current = {**doc, "hookTitle": "", "captions": {**doc["captions"], "karaoke": False}}
    editor_store.put_ready("task", "clip", "clip.mp4", {"duration": 2}, current, revision=4)
    state = (await http.get("/tasks/task/clips/clip/editor")).json()
    assert state["draft"]["document"]["hookTitle"] == ""
    assert state["draft"]["document"]["captions"]["karaoke"] is False
