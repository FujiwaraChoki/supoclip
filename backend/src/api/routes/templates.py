"""Reusable settings templates: save a project's current settings as a named,
versioned bundle and apply it (replace or merge) onto any project.

The settings shape mirrors the existing per-project settings surface (task
columns + the task_source:{id} Redis metadata blob used by
POST /tasks/{id}/settings) rather than inventing a parallel one, so applying a
template is just "write these same fields" through the same code paths.
"""

import logging
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...clip_cleanup import normalize_clip_cleanup_settings
from ...repositories.template_repository import TemplateRepository
from ...services.task_service import TaskService
from .tasks import (
    _normalize_font_size,
    _normalize_font_color,
    _normalize_font_family,
    _normalize_hook_style,
    _normalize_social_overlay,
    _normalize_broll_settings,
    _normalize_target_duration,
    _normalize_max_clips,
    _merge_task_source_metadata,
    _load_task_source_metadata,
    _save_task_source_metadata,
    _get_user_id_from_headers,
    _require_task_owner,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/templates", tags=["templates"])

# Bump when the settings shape changes in a way old rows can't just be read
# as-is; extend migrate_template_settings() to upgrade older rows to match.
TEMPLATE_SCHEMA_VERSION = 1

_SETTINGS_SECTIONS = {
    "captions": ("font_family", "font_size", "font_color", "caption_template"),
    "hooks": ("hook_style",),
    "filler_pauses": (
        "cut_long_pauses",
        "remove_filler_words",
        "pause_threshold_ms",
        "filtered_words",
        "sensitivity",
    ),
    "effects": ("include_broll", "broll_settings"),
    "export": ("output_format", "add_subtitles", "target_duration_seconds", "max_clips"),
    "ui": ("social_overlay",),
}


def normalize_template_settings(value: Any) -> Dict[str, Any]:
    """Whitelist and clamp a template settings payload using the same
    per-field normalizers the /tasks/{id}/settings endpoint uses, so a
    template can never carry a value that endpoint itself would reject."""
    if not isinstance(value, dict):
        value = {}

    cleanup = normalize_clip_cleanup_settings(
        value.get("cut_long_pauses"),
        value.get("pause_threshold_ms"),
        value.get("remove_filler_words"),
        value.get("filtered_words"),
        value.get("sensitivity"),
    )

    output_format = value.get("output_format")
    from ...video_utils import VALID_OUTPUT_FORMATS

    return {
        "font_family": _normalize_font_family(value.get("font_family")),
        "font_size": _normalize_font_size(value.get("font_size")),
        "font_color": _normalize_font_color(value.get("font_color")),
        "caption_template": (
            value.get("caption_template")
            if isinstance(value.get("caption_template"), str)
            else "default"
        ),
        "include_broll": bool(value.get("include_broll", False)),
        "output_format": output_format if output_format in VALID_OUTPUT_FORMATS else None,
        "add_subtitles": (
            value.get("add_subtitles") if isinstance(value.get("add_subtitles"), bool) else None
        ),
        **cleanup,
        "hook_style": _normalize_hook_style(value.get("hook_style")),
        "social_overlay": _normalize_social_overlay(value.get("social_overlay")),
        "broll_settings": _normalize_broll_settings(value.get("broll_settings")),
        "target_duration_seconds": _normalize_target_duration(value.get("target_duration_seconds")),
        "max_clips": _normalize_max_clips(value.get("max_clips")),
    }


def migrate_template_settings(settings: Dict[str, Any], schema_version: int) -> Dict[str, Any]:
    """Upgrade a stored template's settings dict to TEMPLATE_SCHEMA_VERSION.

    Version 1 is the only version that has ever existed, so this is
    currently a passthrough (re-normalized so a hand-edited/imported JSON
    file still gets whitelisted/clamped) — the extensibility point for
    future schema bumps.
    """
    if schema_version < 1:
        settings = normalize_template_settings(settings)
    return normalize_template_settings(settings)


def _section_count(settings: Dict[str, Any]) -> int:
    count = 0
    for fields in _SETTINGS_SECTIONS.values():
        if any(settings.get(field) not in (None, False, "", []) for field in fields):
            count += 1
    return count


def _template_summary(template: Dict[str, Any]) -> Dict[str, Any]:
    settings = migrate_template_settings(template["settings"], template["schema_version"])
    return {
        "id": template["id"],
        "name": template["name"],
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "updated_at": template["updated_at"],
        "created_at": template["created_at"],
        "section_count": _section_count(settings),
    }


async def _capture_task_settings(task_service: TaskService, task_id: str) -> Dict[str, Any]:
    """Snapshot a task's current settings (Postgres columns + Redis metadata)
    into the flat template settings shape."""
    task = await task_service.task_repo.get_task_by_id(task_service.db, task_id)
    if not task:
        raise ValueError("Task not found")
    metadata = await _load_task_source_metadata(task_id)
    return normalize_template_settings(
        {
            "font_family": task.get("font_family"),
            "font_size": task.get("font_size"),
            "font_color": task.get("font_color"),
            "caption_template": task.get("caption_template") or "default",
            "include_broll": task.get("include_broll", False),
            "output_format": metadata.get("output_format"),
            "add_subtitles": metadata.get("add_subtitles"),
            "cut_long_pauses": metadata.get("cut_long_pauses"),
            "pause_threshold_ms": metadata.get("pause_threshold_ms"),
            "remove_filler_words": metadata.get("remove_filler_words"),
            "filtered_words": metadata.get("filtered_words"),
            "sensitivity": metadata.get("sensitivity"),
            "hook_style": metadata.get("hook_style"),
            "social_overlay": metadata.get("social_overlay"),
            "broll_settings": metadata.get("broll_settings"),
            "target_duration_seconds": metadata.get("target_duration_seconds"),
            "max_clips": metadata.get("max_clips"),
        }
    )


@router.get("/")
async def list_templates(request: Request, db: AsyncSession = Depends(get_db)):
    """List the user's saved templates (metadata only, not full settings)."""
    user_id = await _get_user_id_from_headers(request, db)
    templates = await TemplateRepository.list_for_user(db, user_id)
    return {"templates": [_template_summary(t) for t in templates]}


@router.get("/{template_id}")
async def get_template(
    template_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Get a template's full settings (e.g. for JSON export)."""
    user_id = await _get_user_id_from_headers(request, db)
    template = await TemplateRepository.get_by_id(db, user_id, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    settings = migrate_template_settings(template["settings"], template["schema_version"])
    return {
        "id": template["id"],
        "name": template["name"],
        "schema_version": TEMPLATE_SCHEMA_VERSION,
        "settings": settings,
        "created_at": template["created_at"],
        "updated_at": template["updated_at"],
    }


@router.post("/")
async def create_template(request: Request, db: AsyncSession = Depends(get_db)):
    """Save a new template, either from a task's current settings (`task_id`)
    or from an explicit `settings` object (used for JSON import)."""
    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Template name is required")

    user_id = await _get_user_id_from_headers(request, db)
    task_service = TaskService(db)

    task_id = payload.get("task_id")
    if task_id:
        await _require_task_owner(request, task_service, db, str(task_id))
        try:
            settings = await _capture_task_settings(task_service, str(task_id))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
    else:
        schema_version = int(payload.get("schema_version") or TEMPLATE_SCHEMA_VERSION)
        settings = migrate_template_settings(payload.get("settings") or {}, schema_version)

    template = await TemplateRepository.create(
        db, str(uuid.uuid4()), user_id, name[:200], settings, TEMPLATE_SCHEMA_VERSION
    )
    return {"template": template}


@router.patch("/{template_id}")
async def rename_template(
    template_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Rename a template."""
    payload = await request.json()
    name = str(payload.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Template name is required")

    user_id = await _get_user_id_from_headers(request, db)
    template = await TemplateRepository.rename(db, user_id, template_id, name[:200])
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"template": template}


@router.post("/{template_id}/duplicate")
async def duplicate_template(
    template_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Duplicate an existing template under a new name."""
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    user_id = await _get_user_id_from_headers(request, db)
    source = await TemplateRepository.get_by_id(db, user_id, template_id)
    if not source:
        raise HTTPException(status_code=404, detail="Template not found")

    name = str(payload.get("name") or f"{source['name']} (copy)").strip()[:200]
    settings = migrate_template_settings(source["settings"], source["schema_version"])
    template = await TemplateRepository.create(
        db, str(uuid.uuid4()), user_id, name, settings, TEMPLATE_SCHEMA_VERSION
    )
    return {"template": template}


@router.delete("/{template_id}")
async def delete_template(
    template_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Permanently delete a template."""
    user_id = await _get_user_id_from_headers(request, db)
    deleted = await TemplateRepository.delete(db, user_id, template_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"message": "Template deleted"}


@router.post("/{template_id}/apply/{task_id}")
async def apply_template(
    template_id: str, task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Apply a template onto a project, either REPLACE-ing all settings or
    MERGE-ing (template values win, unset template fields keep the
    project's current value). Persists settings only — same "cheap
    auto-save" semantics as POST /tasks/{id}/settings; the user still uses
    the existing "Apply to All Clips" action to re-render with the new
    settings.
    """
    payload: Dict[str, Any] = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    mode = str(payload.get("mode") or "replace").strip().lower()
    if mode not in ("replace", "merge"):
        raise HTTPException(status_code=400, detail="mode must be 'replace' or 'merge'")

    user_id = await _get_user_id_from_headers(request, db)
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)

    template = await TemplateRepository.get_by_id(db, user_id, template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    template_settings = migrate_template_settings(template["settings"], template["schema_version"])

    if mode == "merge":
        current = await _capture_task_settings(task_service, task_id)
        effective = {
            key: (value if value not in (None, "") else current.get(key))
            for key, value in template_settings.items()
        }
    else:
        effective = template_settings

    cleanup_settings = normalize_clip_cleanup_settings(
        effective.get("cut_long_pauses"),
        effective.get("pause_threshold_ms"),
        effective.get("remove_filler_words"),
        effective.get("filtered_words"),
        effective.get("sensitivity"),
    )

    metadata = await _load_task_source_metadata(task_id)
    merged_metadata = _merge_task_source_metadata(
        metadata,
        source_url=metadata.get("url"),
        source_type=metadata.get("source_type"),
        output_format=effective.get("output_format"),
        add_subtitles=effective.get("add_subtitles"),
        cleanup_settings=cleanup_settings,
        hook_style=effective.get("hook_style"),
        social_overlay=effective.get("social_overlay"),
        broll_settings=effective.get("broll_settings"),
        target_duration_seconds=effective.get("target_duration_seconds"),
        max_clips=effective.get("max_clips"),
    )
    await _save_task_source_metadata(task_id, merged_metadata)

    task = await task_service.update_task_settings(
        task_id,
        effective.get("font_family"),
        effective.get("font_size"),
        effective.get("font_color"),
        effective.get("caption_template") or "default",
        bool(effective.get("include_broll", False)),
        apply_to_existing=False,
        cleanup_settings=cleanup_settings,
    )
    return {"task": task, "message": f"Template applied ({mode})"}
