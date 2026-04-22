"""Clip editing endpoints: trim, split, merge, captions, regenerate, delete."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ....database import get_db
from ....services.task_service import TaskService
from ._helpers import _get_user_id_from_headers, _require_task_owner
from .schemas import (
    ClipCaptionsRequest,
    ClipMergeRequest,
    ClipRegenerateRequest,
    ClipSplitRequest,
    ClipTrimRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter()


@router.delete("/{task_id}/clips/{clip_id}")
async def delete_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a specific clip."""
    try:
        user_id = _get_user_id_from_headers(request)
        task_service = TaskService(db)

        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this clip"
            )

        await task_service.clip_repo.delete_clip(db, clip_id)
        return {"message": "Clip deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting clip: {str(e)}")


@router.patch("/{task_id}/clips/{clip_id}")
async def trim_clip(
    task_id: str,
    clip_id: str,
    body: ClipTrimRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Trim clip boundaries and regenerate clip file."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, body.start_offset, body.end_offset
        )
        return {"clip": updated_clip}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error trimming clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error trimming clip: {str(e)}")


@router.post("/{task_id}/clips/{clip_id}/split")
async def split_clip(
    task_id: str,
    clip_id: str,
    body: ClipSplitRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Split a clip into two clips."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.split_clip(task_id, clip_id, body.split_time)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error splitting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error splitting clip: {str(e)}")


@router.post("/{task_id}/clips/merge")
async def merge_clips(
    task_id: str,
    body: ClipMergeRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Merge multiple clips into one clip."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.merge_clips(task_id, body.clip_ids)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error merging clips: {e}")
        raise HTTPException(status_code=500, detail=f"Error merging clips: {str(e)}")


@router.patch("/{task_id}/clips/{clip_id}/captions")
async def update_clip_captions(
    task_id: str,
    clip_id: str,
    body: ClipCaptionsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update clip caption text, timing style and highlighted words."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.update_clip_captions(
            task_id,
            clip_id,
            body.caption_text.strip(),
            body.position,
            body.highlight_words,
        )
        return {"clip": updated_clip}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating captions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating captions: {str(e)}"
        )


@router.post("/{task_id}/clips/{clip_id}/regenerate")
async def regenerate_clip(
    task_id: str,
    clip_id: str,
    body: ClipRegenerateRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Regenerate a single clip after editing timing values."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, body.start_offset, body.end_offset
        )
        return {"clip": updated_clip, "message": "Clip regenerated successfully"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error regenerating clip: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error regenerating clip: {str(e)}"
        )
