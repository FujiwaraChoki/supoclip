"""Content-policy word-list management and per-project settings/rescan."""

import logging
from typing import Any, Dict

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from ...database import get_db
from ...content_policy import CATEGORIES
from ...repositories.content_policy_repository import ContentPolicyRepository
from ...services.content_policy_service import ContentPolicyService
from ...services.task_service import TaskService
from .tasks import _get_user_id_from_headers, _require_task_owner

logger = logging.getLogger(__name__)
router = APIRouter(tags=["content-policy"])


def _normalize_word_list_payload(payload: Any) -> Dict[str, list]:
    if not isinstance(payload, dict):
        return {"severe": [], "borderline": []}
    return {
        "severe": [str(w).strip() for w in payload.get("severe", []) if str(w).strip()],
        "borderline": [str(w).strip() for w in payload.get("borderline", []) if str(w).strip()],
    }


@router.get("/content-policy/word-lists")
async def get_word_lists(request: Request, db: AsyncSession = Depends(get_db)):
    """All 4 categories' effective word lists, counts, and default/override status."""
    user_id = await _get_user_id_from_headers(request, db)
    return {"categories": await ContentPolicyRepository.get_word_list_status_for_user(db, user_id)}


@router.put("/content-policy/word-lists/{category}")
async def update_word_list(category: str, request: Request, db: AsyncSession = Depends(get_db)):
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category '{category}'")
    payload = await request.json()
    user_id = await _get_user_id_from_headers(request, db)
    words = _normalize_word_list_payload(payload.get("words"))
    await ContentPolicyRepository.upsert_word_list(db, user_id, category, words)
    return {"category": category, "words": words}


@router.post("/content-policy/word-lists/{category}/reset")
async def reset_word_list(category: str, request: Request, db: AsyncSession = Depends(get_db)):
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail=f"Unknown category '{category}'")
    user_id = await _get_user_id_from_headers(request, db)
    await ContentPolicyRepository.reset_word_list_to_default(db, user_id, category)
    lists = await ContentPolicyRepository.get_word_lists_for_user(db, user_id)
    return {"category": category, "words": lists[category]}


@router.get("/tasks/{task_id}/content-policy-settings")
async def get_task_content_policy_settings(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)
    return await ContentPolicyRepository.get_project_settings(db, task_id)


@router.put("/tasks/{task_id}/content-policy-settings")
async def update_task_content_policy_settings(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)
    payload = await request.json()

    sensitivity = str(payload.get("sensitivity") or "medium").strip().lower()
    if sensitivity not in ("off", "low", "medium", "high"):
        raise HTTPException(status_code=400, detail="sensitivity must be off/low/medium/high")

    return await ContentPolicyRepository.upsert_project_settings(
        db,
        task_id,
        enabled=bool(payload.get("enabled", True)),
        sensitivity=sensitivity,
        categories_enabled=payload.get("categories_enabled"),
        ollama_borderline_check_enabled=bool(payload.get("ollama_borderline_check_enabled", False)),
    )


@router.post("/tasks/{task_id}/clips/{clip_id}/content-policy/rescan")
async def rescan_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Manually re-run the content-policy scan for one clip (e.g. after a
    word-list edit) without waiting for the next full processing run."""
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)

    clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    user_id = await _get_user_id_from_headers(request, db)
    project_settings = await ContentPolicyRepository.get_project_settings(db, task_id)
    service = ContentPolicyService(db)
    flags = await service.scan_clip(
        user_id, clip_id, clip.get("text", ""), project_settings=project_settings
    )
    return {"clip_id": clip_id, "content_policy_flags": flags}
