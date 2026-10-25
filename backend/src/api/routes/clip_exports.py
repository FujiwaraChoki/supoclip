"""Non-blocking platform-preset exports.

``GET /tasks/{task_id}/clips/{clip_id}/export`` still encodes in the request
for older clients. These routes enqueue that same encode and let the client poll.
"""

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from ...clip_editor import EXPORT_PRESETS
from ...config import get_config
from ...database import get_db
from ...media_storage import MediaStorage, is_s3
from ...services.clip_export_service import (
    FILE_MISSING_MESSAGE,
    ExportWorkerUnavailable,
    present_export,
    start_export,
)
from ...services.task_service import TaskService
from ...utils.async_helpers import run_in_thread
from .tasks import _require_task_reader

router = APIRouter(prefix="/tasks", tags=["tasks"])


def _preset_name(value: str) -> str:
    preset_name = value.lower().strip()
    if preset_name not in EXPORT_PRESETS:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid preset. Use one of: {', '.join(EXPORT_PRESETS.keys())}",
        )
    return preset_name


async def _preset_from_request(request: Request, preset: str) -> str:
    selected = preset
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type.lower():
        raw = await request.body()
        if raw.strip():
            try:
                payload = await request.json()
            except (ValueError, UnicodeDecodeError):
                raise HTTPException(status_code=400, detail="Invalid JSON body")
            if not isinstance(payload, dict):
                raise HTTPException(status_code=400, detail="Request body must be an object")
            if "preset" in payload and payload["preset"] is not None:
                if not isinstance(payload["preset"], str):
                    raise HTTPException(status_code=400, detail="preset must be a string")
                selected = payload["preset"]
    return _preset_name(selected)


async def _owned_clip(request: Request, db: AsyncSession, task_id: str, clip_id: str):
    task_service = TaskService(db)
    task = await _require_task_reader(request, task_service, db, task_id)
    clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip.get("task_id") != task_id or not clip.get("file_path"):
        raise HTTPException(status_code=404, detail="Clip not found")
    return task, clip


@router.post("/{task_id}/clips/{clip_id}/exports", status_code=202)
async def start_clip_export(
    task_id: str,
    clip_id: str,
    request: Request,
    preset: str = "tiktok",
    db: AsyncSession = Depends(get_db),
):
    """Queue a preset export. The same clip and options reuse a live or finished job."""
    task, clip = await _owned_clip(request, db, task_id, clip_id)
    preset_name = await _preset_from_request(request, preset)
    queue = getattr(request.app.state, "queue_adapter", None)
    if queue is None:
        from ...workers.job_queue import JobQueue

        queue = JobQueue
    try:
        return await start_export(db, task=task, clip=clip, preset=preset_name, queue=queue)
    except ExportWorkerUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("/{task_id}/clips/{clip_id}/exports/{export_id}")
async def get_clip_export(
    task_id: str,
    clip_id: str,
    export_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Return queued, running, succeeded, or failed for one export job."""
    from ...repositories.clip_export_repository import ClipExportRepository

    await _owned_clip(request, db, task_id, clip_id)
    row = await ClipExportRepository.get_for_task_clip(db, export_id, task_id, clip_id)
    if not row:
        raise HTTPException(status_code=404, detail="Export not found")
    return await present_export(db, row)


@router.get("/{task_id}/clips/{clip_id}/exports/{export_id}/file")
async def download_clip_export(
    task_id: str,
    clip_id: str,
    export_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Stream a finished export, or redirect to a presigned object URL."""
    from ...repositories.clip_export_repository import ClipExportRepository

    await _owned_clip(request, db, task_id, clip_id)
    row = await ClipExportRepository.get_for_task_clip(db, export_id, task_id, clip_id)
    if not row:
        raise HTTPException(status_code=404, detail="Export not found")
    payload = await present_export(db, row)
    if payload["status"] != "succeeded":
        raise HTTPException(status_code=409, detail="Export is not ready")
    current = await ClipExportRepository.get_for_task_clip(db, export_id, task_id, clip_id)
    file_path = (current or {}).get("file_path")
    filename = (current or row)["download_filename"]
    await db.close()
    if not file_path:
        raise HTTPException(status_code=404, detail=FILE_MISSING_MESSAGE)
    if is_s3(file_path):
        download_url = payload.get("download_url") or ""
        if not download_url.startswith("http"):
            download_url = await run_in_thread(
                MediaStorage().signed_url, file_path, filename, attachment=True
            )
        return RedirectResponse(
            download_url,
            status_code=307,
            headers={"Cache-Control": "private, no-store"},
        )
    path = Path(file_path).resolve()
    root = Path(get_config().temp_dir).resolve()
    if not path.is_file() or not path.is_relative_to(root):
        raise HTTPException(status_code=404, detail=FILE_MISSING_MESSAGE)
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=filename,
        content_disposition_type="attachment",
        headers={"Cache-Control": "private, no-store"},
    )
