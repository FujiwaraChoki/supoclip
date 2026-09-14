"""Per-clip metadata (title/description/tags) generation and manual edits."""

import logging

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import get_config
from ...database import get_db
from ...services.metadata_service import MetadataService
from ...services.task_service import TaskService
from .tasks import _get_user_id_from_headers, _require_task_owner

logger = logging.getLogger(__name__)
router = APIRouter(tags=["metadata"])


def _allow_gemini() -> bool:
    return get_config().llm_provider_mode in ("gemini", "hybrid")


@router.post("/tasks/{task_id}/metadata/regenerate")
async def regenerate_task_metadata(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Video-level "Regenerate all metadata" — one LLM call for every clip."""
    task_service = TaskService(db)
    task = await _require_task_owner(request, task_service, db, task_id)

    clips = await task_service.clip_repo.get_clips_by_task(db, task_id)
    if not clips:
        raise HTTPException(status_code=400, detail="This project has no clips yet")

    result = await MetadataService(db).regenerate_project_metadata(
        task_id,
        clips,
        video_title=task.get("source_title") or task.get("source_url"),
        allow_gemini=_allow_gemini(),
    )
    return result


_VALID_QUALITIES = {"fast", "balanced", "high", "gemini"}


@router.post("/tasks/{task_id}/clips/{clip_id}/metadata/regenerate")
async def regenerate_clip_metadata(
    task_id: str,
    clip_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    quality: Optional[str] = Query(default=None),
):
    """Per-clip "Regenerate" button — a single, lighter LLM call.

    `quality` (fast/balanced/high/gemini) optionally overrides the model
    for just this call — see MetadataService.regenerate_clip_metadata."""
    if quality is not None and quality not in _VALID_QUALITIES:
        raise HTTPException(status_code=400, detail=f"quality must be one of {sorted(_VALID_QUALITIES)}")

    task_service = TaskService(db)
    task = await _require_task_owner(request, task_service, db, task_id)

    clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    result = await MetadataService(db).regenerate_clip_metadata(
        clip,
        video_title=task.get("source_title") or task.get("source_url"),
        allow_gemini=_allow_gemini(),
        quality=quality,
    )
    if result is None:
        raise HTTPException(
            status_code=503,
            detail="Metadata generation is unavailable (Ollama unreachable and no Gemini fallback configured)",
        )
    return result


@router.patch("/tasks/{task_id}/clips/{clip_id}/metadata")
async def update_clip_metadata(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Manual edit of title/description/tags — marks the edited field(s) so a
    later passive regeneration never overwrites them."""
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)

    clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    payload = await request.json()
    title = payload.get("title")
    description = payload.get("description")
    tags = payload.get("tags")
    if title is None and description is None and tags is None:
        raise HTTPException(status_code=400, detail="No metadata fields provided")

    await MetadataService(db).update_clip_metadata_fields(
        clip_id,
        title=title if isinstance(title, str) else None,
        description=description if isinstance(description, str) else None,
        tags=tags if isinstance(tags, list) else None,
    )
    updated = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    return {"clip": updated}
