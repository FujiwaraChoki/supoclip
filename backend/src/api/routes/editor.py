"""Authenticated draft, preview and background export endpoints."""

from pathlib import Path
import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...editor_document import (
    EditDocument,
    RevisionConflict,
    atomic_json,
    editor_dir,
    editor_lock,
    save_document,
)
from ...services.editor_service import read_state
from ...services.task_service import TaskService
from ...repositories.edit_transaction import task_edit_transaction
from ...workers.job_queue import JobQueue
from ...utils.async_helpers import run_in_thread
from .tasks import _require_task_owner, _read_json_object

router = APIRouter(prefix="/tasks", tags=["editor"])


async def context(request, db, task_id, clip_id):
    service = TaskService(db)
    await _require_task_owner(request, service, db, task_id)
    clip = await service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip["task_id"] != task_id:
        raise HTTPException(404, "Clip not found")
    directory = editor_dir(service.config.temp_dir, task_id, clip_id, clip["filename"])
    await db.close()
    return clip, directory


def job_path(directory: Path, job_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Export not found")
    path = directory / f"job-{job_id}.json"
    if not path.exists():
        raise HTTPException(404, "Export not found")
    return path


@router.get("/{task_id}/clips/{clip_id}/editor")
async def get_editor(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    clip, directory = await context(request, db, task_id, clip_id)
    state = await run_in_thread(read_state, directory)
    state["basis"] = clip["filename"]
    state["jobs"] = [
        {"id": p.stem[4:], **json.loads(p.read_text())}
        for p in sorted(
            directory.glob("job-*.json"), key=lambda p: p.stat().st_mtime, reverse=True
        )[:20]
    ]
    return state


@router.post("/{task_id}/clips/{clip_id}/editor/prepare", status_code=202)
async def prepare(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    clip, directory = await context(request, db, task_id, clip_id)
    with editor_lock(directory):
        state = read_state(directory)
        if state["status"] in {"ready", "preparing"}:
            return {"status": state["status"]}
        atomic_json(directory / "asset.json", {"status": "preparing"})
    try:
        await JobQueue.enqueue_job("prepare_editor", task_id, clip_id, clip["filename"])
    except Exception:
        atomic_json(
            directory / "asset.json",
            {
                "status": "failed",
                "error": "The rendering worker is unavailable. Try again shortly.",
            },
        )
        raise HTTPException(
            503, "The rendering worker is unavailable. Try again shortly."
        )
    return {"status": "preparing"}


@router.patch("/{task_id}/clips/{clip_id}/editor")
async def save(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    service = TaskService(db)
    await _require_task_owner(request, service, db, task_id)
    payload = await _read_json_object(request)
    try:
        async with task_edit_transaction(db, task_id):
            clip = await service.clip_repo.get_clip_by_id(db, clip_id)
            if not clip or clip["task_id"] != task_id:
                raise HTTPException(404, "Clip not found")
            if payload.get("basis") != clip["filename"]:
                raise HTTPException(
                    409, "The original clip changed. Reopen the editor."
                )
            directory = editor_dir(
                service.config.temp_dir, task_id, clip_id, clip["filename"]
            )
            state = read_state(directory)
            if state["status"] != "ready":
                raise HTTPException(
                    409, "Wait for the editor preview to finish preparing"
                )
            revision = payload.get("revision")
            if (
                isinstance(revision, bool)
                or not isinstance(revision, int)
                or revision < 0
            ):
                raise ValueError("Invalid draft revision")
            document = EditDocument.model_validate(payload.get("document"))
            return await run_in_thread(
                save_document, directory, document, revision, state["duration"]
            )
    except RevisionConflict as exc:
        raise HTTPException(409, str(exc))
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc))


@router.get("/{task_id}/clips/{clip_id}/editor/media/{asset}")
async def media(
    task_id: str,
    clip_id: str,
    asset: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    _, directory = await context(request, db, task_id, clip_id)
    if asset not in {"clean.mp4", "thumbnails.jpg"} or not (directory / asset).exists():
        raise HTTPException(404, "Preview not ready")
    return FileResponse(
        directory / asset,
        media_type="video/mp4" if asset.endswith("mp4") else "image/jpeg",
        headers={"Cache-Control": "private, no-cache"},
    )


@router.post("/{task_id}/clips/{clip_id}/editor/exports", status_code=202)
async def start_export(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    clip, directory = await context(request, db, task_id, clip_id)
    payload = await _read_json_object(request)
    state = read_state(directory)
    if state["status"] != "ready":
        raise HTTPException(409, "Prepare the editor preview before exporting")
    if payload.get("basis") != clip["filename"]:
        raise HTTPException(409, "The original clip changed. Reopen the editor.")
    preset = payload.get("preset", "tiktok")
    if preset not in {"tiktok", "reels", "shorts"}:
        raise HTTPException(400, "Unknown export preset")
    try:
        document = EditDocument.model_validate(
            payload.get("document")
        ).validate_duration(state["duration"])
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    with editor_lock(directory):
        active = [
            p
            for p in directory.glob("job-*.json")
            if json.loads(p.read_text()).get("status") in {"queued", "rendering"}
        ]
        if len(active) >= 3:
            raise HTTPException(429, "Three exports are already running for this clip")
        job_id = uuid.uuid4().hex
        atomic_json(
            directory / f"request-{job_id}.json",
            {"document": document.model_dump(), "preset": preset},
        )
        atomic_json(
            directory / f"job-{job_id}.json", {"status": "queued", "progress": 0}
        )
    try:
        await JobQueue.enqueue_job(
            "export_editor", task_id, clip_id, clip["filename"], job_id
        )
    except Exception:
        atomic_json(
            directory / f"job-{job_id}.json",
            {
                "status": "failed",
                "progress": 0,
                "error": "The rendering worker is unavailable.",
            },
        )
        raise HTTPException(
            503, "The rendering worker is unavailable. Your draft is safe."
        )
    return {"id": job_id, "status": "queued", "progress": 0}


@router.get("/{task_id}/clips/{clip_id}/editor/exports/{job_id}")
async def export_status(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, directory = await context(request, db, task_id, clip_id)
    return {"id": job_id, **json.loads(job_path(directory, job_id).read_text())}


@router.delete("/{task_id}/clips/{clip_id}/editor/exports/{job_id}")
async def cancel_export(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, directory = await context(request, db, task_id, clip_id)
    status = json.loads(job_path(directory, job_id).read_text())
    if status["status"] in {"queued", "rendering"}:
        (directory / f"cancel-{job_id}").touch()
        if status["status"] == "queued":
            atomic_json(
                directory / f"job-{job_id}.json", {"status": "cancelled", "progress": 0}
            )
            return {"status": "cancelled"}
    return {
        "status": "cancelling" if status["status"] == "rendering" else status["status"]
    }


@router.get("/{task_id}/clips/{clip_id}/editor/exports/{job_id}/file")
async def export_file(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    _, directory = await context(request, db, task_id, clip_id)
    status = json.loads(job_path(directory, job_id).read_text())
    output = directory / f"export-{job_id}.mp4"
    if status["status"] != "completed" or not output.exists():
        raise HTTPException(409, "Export is not ready")
    return FileResponse(
        output,
        media_type="video/mp4",
        filename=f"supoclip-{clip_id[:8]}-{job_id[:6]}.mp4",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/{task_id}/editor/combine", status_code=202)
async def combine(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    service = TaskService(db)
    await _require_task_owner(request, service, db, task_id)
    payload = await _read_json_object(request)
    ids = payload.get("clip_ids")
    if (
        not isinstance(ids, list)
        or not 2 <= len(ids) <= 20
        or not all(isinstance(i, str) for i in ids)
        or len(set(ids)) != len(ids)
    ):
        raise HTTPException(400, "Select between 2 and 20 distinct clips")
    entries = []
    for clip_id in ids:
        clip = await service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(404, "Clip not found")
        directory = editor_dir(
            service.config.temp_dir, task_id, clip_id, clip["filename"]
        )
        state = read_state(directory)
        if state["status"] != "ready":
            raise HTTPException(409, "Prepare each selected clip before combining")
        entries.append(
            {
                "id": clip_id,
                "filename": clip["filename"],
                "document": state["draft"]["document"],
                "state": {k: state[k] for k in ("width", "height", "hasAudio")},
                "ranges": service._get_clip_source_ranges(clip),
            }
        )
    await db.close()
    anchor = entries[0]
    directory = editor_dir(
        service.config.temp_dir, task_id, anchor["id"], anchor["filename"]
    )
    job_id = uuid.uuid4().hex
    with editor_lock(directory):
        active = [
            p
            for p in directory.glob("job-*.json")
            if json.loads(p.read_text()).get("status") in {"queued", "rendering"}
        ]
        if len(active) >= 3:
            raise HTTPException(
                429, "Wait for an existing export to finish before combining"
            )
        atomic_json(directory / f"request-{job_id}.json", {"inputs": entries})
        atomic_json(
            directory / f"job-{job_id}.json", {"status": "queued", "progress": 0}
        )
    try:
        await JobQueue.enqueue_job(
            "combine_editor", task_id, anchor["id"], anchor["filename"], job_id
        )
    except Exception:
        atomic_json(
            directory / f"job-{job_id}.json",
            {
                "status": "failed",
                "progress": 0,
                "error": "The rendering worker is unavailable.",
            },
        )
        raise HTTPException(503, "The rendering worker is unavailable")
    return {"id": job_id, "anchor_clip_id": anchor["id"]}
