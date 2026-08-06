"""Authenticated, task-scoped APIs for the non-destructive editor."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...config import get_config
from ...database import get_db
from ...repositories.editor_repository import EditorProjectVersionConflict
from ...repositories.task_repository import TaskRepository
from ...services.editor_service import (
    EditorAssetError,
    EditorAssetInUse,
    EditorAssetNotFound,
    EditorAssetTooLarge,
    EditorService,
)


router = APIRouter(tags=["editor"])


class PutEditorProjectRequest(BaseModel):
    project: dict[str, Any]
    expected_version: int | None = Field(default=None, ge=0)


async def _require_task_owner(
    request: Request, db: AsyncSession, task_id: str
) -> dict[str, Any]:
    user_id = await resolve_authenticated_user_id(request, db, get_config())
    task = await TaskRepository.get_task_by_id(db, task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this task")
    return task


@router.get("/tasks/{task_id}/editor")
async def get_editor(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_task_owner(request, db, task_id)
    return await EditorService(db).get_editor(task_id)


@router.put("/tasks/{task_id}/editor")
async def put_editor_project(
    task_id: str,
    payload: PutEditorProjectRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_task_owner(request, db, task_id)
    try:
        return await EditorService(db).save_project(
            task_id, payload.project, payload.expected_version
        )
    except EditorProjectVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Editor project version conflict",
                "current_version": exc.current_version,
            },
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/editor/assets")
async def list_editor_assets(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_task_owner(request, db, task_id)
    return {"assets": await EditorService(db).list_assets(task_id)}


@router.post("/tasks/{task_id}/editor/assets", status_code=201)
async def upload_editor_asset(
    task_id: str,
    request: Request,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    await _require_task_owner(request, db, task_id)
    try:
        asset = await EditorService(db).upload_asset(task_id, file)
        return {"asset": asset}
    except EditorAssetTooLarge as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except EditorAssetError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/tasks/{task_id}/editor/assets/{asset_id}/file")
async def get_editor_asset_file(
    task_id: str,
    asset_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await _require_task_owner(request, db, task_id)
    try:
        asset, asset_path = await EditorService(db).get_asset(task_id, asset_id)
    except EditorAssetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    return FileResponse(
        path=str(asset_path),
        media_type=asset.mime_type,
        filename=asset.name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store"},
    )


@router.delete("/tasks/{task_id}/editor/assets/{asset_id}")
async def delete_editor_asset(
    task_id: str,
    asset_id: str,
    request: Request,
    remove_references: bool = Query(default=False),
    expected_version: int | None = Query(default=None, ge=0),
    db: AsyncSession = Depends(get_db),
):
    await _require_task_owner(request, db, task_id)
    try:
        if remove_references:
            if expected_version is None:
                raise HTTPException(
                    status_code=400,
                    detail="expected_version is required when removing timeline references",
                )
            result = await EditorService(db).delete_asset_and_references(
                task_id,
                asset_id,
                expected_version,
            )
            return {"deleted": True, "id": asset_id, **result}
        await EditorService(db).delete_asset(task_id, asset_id)
    except EditorProjectVersionConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Editor project version conflict",
                "current_version": exc.current_version,
            },
        ) from exc
    except EditorAssetInUse as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except EditorAssetNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"deleted": True, "id": asset_id}
