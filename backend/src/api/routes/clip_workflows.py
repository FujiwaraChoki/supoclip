"""Localization, dubbing, and editable B-roll workflows for generated clips."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...broll import download_broll_video, get_video_download_url, search_broll_videos
from ...config import get_config
from ...database import get_db
from ...services.variant_service import render_localized_variant
from ...utils.async_helpers import run_in_thread
from ...video_utils import apply_broll_to_clip


router = APIRouter(prefix="/clip-workflows", tags=["clip workflows"])


class VariantCreate(BaseModel):
    language: str = Field(min_length=2, max_length=32)
    kind: str = "translated"
    voice: str = Field(default="alloy", max_length=120)


class BRollItemCreate(BaseModel):
    prompt: str = Field(min_length=1, max_length=300)
    source_url: str
    provider: str = "pexels"
    start_seconds: float = Field(default=0, ge=0)
    duration: float = Field(default=3, ge=0.5, le=15)
    sort_order: int = 0


class BRollItemUpdate(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=300)
    start_seconds: float | None = Field(default=None, ge=0)
    duration: float | None = Field(default=None, ge=0.5, le=15)
    sort_order: int | None = None


async def _user_id(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


async def _owned_clip(
    db: AsyncSession, clip_id: str, user_id: str, *, require_write: bool = False
) -> dict:
    result = await db.execute(
        text(
            """
            SELECT gc.*, t.user_id, t.workspace_id,
                   CASE WHEN t.user_id = :user_id THEN 'owner' ELSE wm.role END AS workspace_role
            FROM generated_clips gc JOIN tasks t ON t.id = gc.task_id
            LEFT JOIN workspace_members wm
              ON wm.workspace_id = t.workspace_id AND wm.user_id = :user_id
             AND wm.status = 'active'
            WHERE gc.id = :clip_id AND (
                t.user_id = :user_id OR wm.user_id = :user_id
            )
            """
        ),
        {"clip_id": clip_id, "user_id": user_id},
    )
    row = result.mappings().first()
    if not row:
        raise HTTPException(status_code=404, detail="Clip not found")
    if require_write and row["workspace_role"] == "viewer":
        raise HTTPException(status_code=403, detail="Viewer access is read-only")
    return dict(row)


def _json(value: str | dict | None) -> dict:
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}


@router.get("/clips/{clip_id}/variants")
async def list_variants(
    clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    await _owned_clip(db, clip_id, user_id)
    result = await db.execute(
        text("SELECT * FROM clip_variants WHERE clip_id = :clip_id ORDER BY created_at DESC"),
        {"clip_id": clip_id},
    )
    items = []
    for row in result.mappings():
        item = dict(row)
        item["metadata"] = _json(item.pop("metadata_json", None))
        item["file_url"] = (
            f"/clip-workflows/clips/{clip_id}/variants/{item['id']}/file"
            if item.get("file_path")
            else None
        )
        items.append(item)
    return {"variants": items}


@router.post("/clips/{clip_id}/variants")
async def create_variant(
    clip_id: str,
    body: VariantCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    clip = await _owned_clip(db, clip_id, user_id, require_write=True)
    kind = body.kind.strip().lower()
    if kind not in {"translated", "dubbed"}:
        raise HTTPException(status_code=422, detail="kind must be translated or dubbed")
    source = Path(clip["file_path"])
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Clip media file is unavailable")

    variant_id = str(uuid4())
    output = Path(get_config().temp_dir) / "variants" / f"{variant_id}.mp4"
    await db.execute(
        text(
            """
            INSERT INTO clip_variants
                (id, clip_id, user_id, variant_type, language, voice, status)
            VALUES (:id, :clip_id, :user_id, :kind, :language, :voice, 'processing')
            """
        ),
        {"id": variant_id, "clip_id": clip_id, "user_id": user_id,
         "kind": kind, "language": body.language, "voice": body.voice},
    )
    await db.commit()
    try:
        transcript, metadata = await render_localized_variant(
            source_path=source,
            transcript=clip.get("text") or "",
            duration=float(clip.get("duration") or 1),
            output_path=output,
            target_language=body.language,
            voice=body.voice,
            dub=kind == "dubbed",
        )
        await db.execute(
            text(
                """
                UPDATE clip_variants SET status = 'completed', file_path = :file_path,
                    transcript_text = :transcript, metadata_json = :metadata, updated_at = NOW()
                WHERE id = :id
                """
            ),
            {"id": variant_id, "file_path": str(output), "transcript": transcript,
             "metadata": json.dumps(metadata)},
        )
        await db.commit()
        return {"id": variant_id, "status": "completed", "variant_type": kind,
                "language": body.language, "transcript_text": transcript,
                "metadata": metadata,
                "file_url": f"/clip-workflows/clips/{clip_id}/variants/{variant_id}/file"}
    except Exception as exc:
        await db.execute(
            text("UPDATE clip_variants SET status = 'failed', error = :error, updated_at = NOW() WHERE id = :id"),
            {"id": variant_id, "error": str(exc)[:2000]},
        )
        await db.commit()
        raise HTTPException(status_code=502, detail=f"Variant generation failed: {exc}") from exc


@router.get("/clips/{clip_id}/variants/{variant_id}/file")
async def variant_file(
    clip_id: str, variant_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    await _owned_clip(db, clip_id, user_id)
    result = await db.execute(
        text("SELECT file_path FROM clip_variants WHERE id = :id AND clip_id = :clip_id"),
        {"id": variant_id, "clip_id": clip_id},
    )
    file_path = result.scalar_one_or_none()
    if not file_path or not Path(file_path).is_file():
        raise HTTPException(status_code=404, detail="Variant file not found")
    return FileResponse(file_path, media_type="video/mp4", filename=Path(file_path).name)


@router.get("/clips/{clip_id}/broll/search")
async def search_broll(
    clip_id: str,
    request: Request,
    query: str = Query(min_length=1, max_length=200),
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    await _owned_clip(db, clip_id, user_id)
    videos = await search_broll_videos(query, per_page=8)
    return {
        "results": [
            {
                "id": str(video.get("id")),
                "thumbnail": video.get("image"),
                "duration": video.get("duration"),
                "source_url": get_video_download_url(video),
                "creator": (video.get("user") or {}).get("name"),
                "provider": "pexels",
            }
            for video in videos
            if get_video_download_url(video)
        ]
    }


@router.get("/clips/{clip_id}/broll")
async def list_broll(
    clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    await _owned_clip(db, clip_id, user_id)
    result = await db.execute(
        text("SELECT * FROM clip_broll_items WHERE clip_id = :clip_id ORDER BY sort_order, created_at"),
        {"clip_id": clip_id},
    )
    return {"items": [dict(row) for row in result.mappings()]}


@router.post("/clips/{clip_id}/broll")
async def add_broll(
    clip_id: str,
    body: BRollItemCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    clip = await _owned_clip(db, clip_id, user_id, require_write=True)
    host = (urlparse(body.source_url).hostname or "").lower()
    if body.provider != "pexels" or not (host == "videos.pexels.com" or host.endswith(".pexels.com")):
        raise HTTPException(status_code=422, detail="Only selected Pexels media URLs are accepted")
    clip_duration = float(clip.get("duration") or 0)
    if body.start_seconds >= clip_duration:
        raise HTTPException(status_code=422, detail="B-roll start must be inside the clip")
    item_id = str(uuid4())
    local_path = Path(get_config().temp_dir) / "broll" / f"{item_id}.mp4"
    if not await download_broll_video(body.source_url, local_path):
        raise HTTPException(status_code=502, detail="Unable to download selected B-roll")
    end_seconds = min(clip_duration, body.start_seconds + body.duration)
    await db.execute(
        text(
            """
            INSERT INTO clip_broll_items
                (id, clip_id, user_id, provider, prompt, source_url, file_path,
                 start_seconds, end_seconds, sort_order)
            VALUES (:id, :clip_id, :user_id, 'pexels', :prompt, :source_url, :file_path,
                    :start_seconds, :end_seconds, :sort_order)
            """
        ),
        {"id": item_id, "clip_id": clip_id, "user_id": user_id, "prompt": body.prompt,
         "source_url": body.source_url, "file_path": str(local_path),
         "start_seconds": body.start_seconds, "end_seconds": end_seconds,
         "sort_order": body.sort_order},
    )
    await db.commit()
    return {"id": item_id, **body.model_dump(), "file_path": str(local_path),
            "end_seconds": end_seconds}


@router.patch("/clips/{clip_id}/broll/{item_id}")
async def update_broll(
    clip_id: str,
    item_id: str,
    body: BRollItemUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    user_id = await _user_id(request, db)
    clip = await _owned_clip(db, clip_id, user_id, require_write=True)
    result = await db.execute(
        text("SELECT * FROM clip_broll_items WHERE id = :id AND clip_id = :clip_id"),
        {"id": item_id, "clip_id": clip_id},
    )
    current = result.mappings().first()
    if not current:
        raise HTTPException(status_code=404, detail="B-roll item not found")
    start_seconds = (
        body.start_seconds if body.start_seconds is not None else float(current["start_seconds"])
    )
    current_duration = max(0.5, float(current["end_seconds"]) - float(current["start_seconds"]))
    duration = body.duration if body.duration is not None else current_duration
    clip_duration = float(clip.get("duration") or 0)
    if start_seconds >= clip_duration:
        raise HTTPException(status_code=422, detail="B-roll start must be inside the clip")
    end_seconds = min(clip_duration, start_seconds + duration)
    prompt = body.prompt if body.prompt is not None else current["prompt"]
    sort_order = body.sort_order if body.sort_order is not None else current["sort_order"]
    await db.execute(
        text(
            """
            UPDATE clip_broll_items SET prompt = :prompt, start_seconds = :start_seconds,
                end_seconds = :end_seconds, sort_order = :sort_order, updated_at = NOW()
            WHERE id = :id AND clip_id = :clip_id
            """
        ),
        {"id": item_id, "clip_id": clip_id, "prompt": prompt,
         "start_seconds": start_seconds, "end_seconds": end_seconds,
         "sort_order": sort_order},
    )
    await db.commit()
    return {"id": item_id, "prompt": prompt, "start_seconds": start_seconds,
            "end_seconds": end_seconds, "duration": end_seconds - start_seconds,
            "sort_order": sort_order}


@router.delete("/clips/{clip_id}/broll/{item_id}")
async def delete_broll(
    clip_id: str, item_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    await _owned_clip(db, clip_id, user_id, require_write=True)
    result = await db.execute(
        text("DELETE FROM clip_broll_items WHERE id = :id AND clip_id = :clip_id RETURNING file_path"),
        {"id": item_id, "clip_id": clip_id},
    )
    file_path = result.scalar_one_or_none()
    if file_path is None:
        raise HTTPException(status_code=404, detail="B-roll item not found")
    await db.commit()
    path = Path(file_path)
    if path.is_file():
        path.unlink()
    return {"deleted": True}


@router.post("/clips/{clip_id}/broll/render")
async def render_broll(
    clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _user_id(request, db)
    clip = await _owned_clip(db, clip_id, user_id, require_write=True)
    result = await db.execute(
        text("SELECT * FROM clip_broll_items WHERE clip_id = :clip_id ORDER BY sort_order, created_at"),
        {"clip_id": clip_id},
    )
    items = [dict(row) for row in result.mappings()]
    if not items:
        raise HTTPException(status_code=422, detail="Add at least one B-roll item first")
    variant_id = str(uuid4())
    output = Path(get_config().temp_dir) / "variants" / f"{variant_id}.mp4"
    output.parent.mkdir(parents=True, exist_ok=True)
    suggestions = [
        {"local_path": item["file_path"], "timestamp": item["start_seconds"],
         "duration": max(0.5, item["end_seconds"] - item["start_seconds"])}
        for item in items
    ]
    success = await run_in_thread(apply_broll_to_clip, Path(clip["file_path"]), suggestions, output)
    if not success or not output.is_file():
        raise HTTPException(status_code=500, detail="B-roll render failed")
    await db.execute(
        text(
            """
            INSERT INTO clip_variants
                (id, clip_id, user_id, variant_type, status, file_path, transcript_text, metadata_json)
            VALUES (:id, :clip_id, :user_id, 'broll', 'completed', :file_path, :transcript, :metadata)
            """
        ),
        {"id": variant_id, "clip_id": clip_id, "user_id": user_id,
         "file_path": str(output), "transcript": clip.get("text"),
         "metadata": json.dumps({"broll_item_ids": [item["id"] for item in items]})},
    )
    await db.commit()
    return {"id": variant_id, "status": "completed",
            "file_url": f"/clip-workflows/clips/{clip_id}/variants/{variant_id}/file"}
