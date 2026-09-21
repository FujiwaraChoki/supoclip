"""
Ranking/Compilation tool API routes.

A ranking task holds N input videos (ranking_inputs) instead of clipping's
single source, and renders to one compilation stored as a single
generated_clips row (clip_order=0) rather than N clip rows — see
CLAUDE.md's Ranking tool section and ranking_service.py for the pipeline.
Uploads reuse the existing generic `POST /upload` endpoint in media.py
unchanged; this router only owns ranking-specific task/input/settings state.
"""

import hashlib
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import aiofiles
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from sqlalchemy.ext.asyncio import AsyncSession

from ...auth_headers import resolve_authenticated_user_id
from ...clip_editor import EXPORT_PRESETS
from ...config import get_config
from ...database import get_db
from ...ranking_templates import get_template, get_template_info, get_template_preview_path
from ...repositories.ranking_folder_repository import (
    MIN_CLIPS_PER_FOLDER,
    RankingFolderRepository,
    SELECTION_SIZE,
)
from ...repositories.ranking_repository import RankingRepository
from ...repositories.task_repository import TaskRepository
from ...runtime_settings import encrypt_setting_value, load_runtime_settings_cache
from ...video_utils import SFX_DIR, SFX_EXTENSIONS, ffprobe_duration
from ...workers.job_queue import JobQueue

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ranking", tags=["ranking"])

task_repo = TaskRepository()
ranking_repo = RankingRepository()
folder_repo = RankingFolderRepository()

_MAX_FOLDER_UPLOAD_BYTES = 500 * 1024 * 1024
_ACCEPTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}


async def _get_user_id(request: Request, db: AsyncSession) -> str:
    return await resolve_authenticated_user_id(request, db, get_config())


async def _get_owned_ranking_task(
    request: Request, db: AsyncSession, task_id: str
) -> Dict[str, Any]:
    user_id = await _get_user_id(request, db)
    task = await task_repo.get_task_by_id(db, task_id)
    if not task or task.get("task_type") != "ranking":
        raise HTTPException(status_code=404, detail="Ranking task not found")
    if task["user_id"] != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this task")
    return task


@router.get("/templates")
async def list_ranking_templates():
    """Data-driven template list for the picker — see ranking_templates.py."""
    return {"templates": get_template_info()}


@router.get("/templates/{template_id}/preview")
async def get_ranking_template_preview(template_id: str):
    """Serve a template's preview.svg/preview.png thumbnail (see
    ranking_templates.py) — no auth needed, these are static assets shipped
    with the repo, not user content."""
    path = get_template_preview_path(template_id)
    if not path:
        raise HTTPException(status_code=404, detail="Template preview not found")
    media_type = "image/svg+xml" if path.suffix == ".svg" else "image/png"
    return FileResponse(
        path=str(path),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000"},
    )


@router.get("/export-presets")
async def list_ranking_export_presets():
    """Same platform export presets as clipping — reused as-is."""
    return {
        "presets": [
            {
                "id": name,
                "name": preset.name,
                "width": preset.width,
                "height": preset.height,
                "max_duration_seconds": preset.max_duration_seconds,
                "target_lufs": preset.target_lufs,
            }
            for name, preset in EXPORT_PRESETS.items()
        ]
    }


# --- Folder library -------------------------------------------------------
# A "folder" is a user-named batch of clips picked together via a browser
# directory picker or multi-file drop (there is no server filesystem path to
# scan — uploads are the only way a clip reaches the backend). Clips persist
# here across ranking projects for random/prefer-unused selection and
# cross-ranking text memory (RankingFolderRepository) — distinct from
# ranking_inputs, which only holds the clips attached to one project.


async def _write_upload_and_hash(uploaded_file: UploadFile, target_path: Path, max_bytes: int) -> str:
    """Stream an upload to disk while hashing it in the same pass — the
    content hash is a library clip's identity (see ranking_folder_repository
    docstring), so it has to be computed from bytes actually written, not a
    second read-back pass over the file."""
    hasher = hashlib.sha256()
    written = 0
    chunk_size = 1024 * 1024
    try:
        async with aiofiles.open(target_path, "wb") as destination:
            while True:
                chunk = await uploaded_file.read(chunk_size)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large")
                hasher.update(chunk)
                await destination.write(chunk)
    except Exception:
        if target_path.exists():
            target_path.unlink(missing_ok=True)
        raise
    return hasher.hexdigest()


@router.get("/folders")
async def list_ranking_folders(request: Request, db: AsyncSession = Depends(get_db)):
    user_id = await _get_user_id(request, db)
    folders = await folder_repo.list_folders(db, user_id)
    return {"folders": folders, "min_clips_per_folder": MIN_CLIPS_PER_FOLDER}


@router.post("/folders/scan")
async def scan_ranking_folder(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Add a batch of clips (a browser directory-picker selection or a
    multi-file drop) to a named folder's persistent library. Re-adding a
    file already in the library (same content hash) is a no-op that returns
    the existing row — it doesn't reset use_count/saved_text."""
    user_id = await _get_user_id(request, db)
    form = await request.form()
    folder_name = str(form.get("folder_name") or "").strip()
    if not folder_name:
        raise HTTPException(status_code=400, detail="folder_name is required")

    files = [v for v in form.getlist("files") if hasattr(v, "filename")]
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    config = get_config()
    uploads_dir = Path(config.temp_dir) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)
    folder_id = await folder_repo.get_or_create_folder(db, user_id, folder_name)

    added = []
    skipped = []
    for uploaded in files:
        filename = uploaded.filename or "clip.mp4"
        ext = Path(filename).suffix.lower()
        if ext not in _ACCEPTED_VIDEO_EXTENSIONS:
            skipped.append(filename)
            continue
        unique_filename = f"{uuid.uuid4()}{ext}"
        target_path = uploads_dir / unique_filename
        content_hash = await _write_upload_and_hash(uploaded, target_path, _MAX_FOLDER_UPLOAD_BYTES)
        duration = ffprobe_duration(target_path)
        clip_id = await folder_repo.upsert_clip(
            db,
            folder_id=folder_id,
            file_path=f"upload://{unique_filename}",
            original_filename=filename,
            duration_seconds=duration,
            content_hash=content_hash,
        )
        added.append(clip_id)

    clips = await folder_repo.list_clips(db, folder_id)
    return {
        "folder_id": folder_id,
        "added": len(added),
        "skipped": skipped,
        "clip_count": len(clips),
        "clips": clips,
        "meets_minimum": len(clips) >= MIN_CLIPS_PER_FOLDER,
    }


@router.get("/folders/clips/{clip_id}/file")
async def get_ranking_folder_clip_file(
    clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Stream one library clip's video bytes for preview — a real endpoint
    (not a raw path query param) so ownership is checked and the on-disk
    uploads layout never leaks to the client."""
    user_id = await _get_user_id(request, db)
    clip = await folder_repo.get_clip(db, clip_id)
    if not clip:
        raise HTTPException(status_code=404, detail="Clip not found")
    folder = await folder_repo.get_folder(db, user_id, clip["folder_id"])
    if not folder:
        raise HTTPException(status_code=404, detail="Clip not found")

    from ...services.video_service import VideoService

    local_path = VideoService.resolve_local_video_path(clip["file_path"])
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(path=str(local_path), media_type="video/mp4")


@router.get("/folders/{folder_id}/clips")
async def list_ranking_folder_clips(
    folder_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    user_id = await _get_user_id(request, db)
    folder = await folder_repo.get_folder(db, user_id, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    clips = await folder_repo.list_clips(db, folder_id)
    return {"folder": folder, "clips": clips, "meets_minimum": len(clips) >= MIN_CLIPS_PER_FOLDER}


@router.post("/folders/{folder_id}/select")
async def select_ranking_folder_clips(
    folder_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Auto-select SELECTION_SIZE (5) clips at random, preferring clips with
    fewer prior uses in this folder — the "random + prefer-unused" pick a new
    ranking project starts from; the frontend lets the user swap any slot
    afterward via GET .../clips (which shows every clip's use_count)."""
    user_id = await _get_user_id(request, db)
    folder = await folder_repo.get_folder(db, user_id, folder_id)
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    clips = await folder_repo.list_clips(db, folder_id)
    if len(clips) < MIN_CLIPS_PER_FOLDER:
        raise HTTPException(
            status_code=400,
            detail=f"This folder has {len(clips)} clips — at least {MIN_CLIPS_PER_FOLDER} are needed.",
        )
    selected = await folder_repo.select_clips(db, folder_id, SELECTION_SIZE)
    return {"clips": selected}


# --- Ranking Settings (Settings -> Ranking) --------------------------------


@router.post("/settings/sfx")
async def upload_ranking_sfx(
    request: Request,
    db: AsyncSession = Depends(get_db),
    file: UploadFile = File(...),
):
    """Upload the single global transition SFX used by the Ranking tool's
    default template (Settings -> Ranking). Saved into the same SFX_DIR
    clipping's curated sfx ship from (video_utils.find_sfx_path resolves
    both the same way), under a reserved filename so re-uploading replaces
    it in place rather than accumulating files."""
    user_id = await _get_user_id(request, db)
    ext = Path(file.filename or "").suffix.lower()
    if ext not in SFX_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported SFX format — use one of: {', '.join(SFX_EXTENSIONS)}",
        )

    SFX_DIR.mkdir(parents=True, exist_ok=True)
    for existing in SFX_DIR.glob("ranking_default.*"):
        existing.unlink(missing_ok=True)
    filename = f"ranking_default{ext}"
    target_path = SFX_DIR / filename
    await _write_upload_and_hash(file, target_path, 25 * 1024 * 1024)

    encrypted_value = encrypt_setting_value(filename)
    await db.execute(
        sql_text("""
            INSERT INTO app_settings (setting_key, encrypted_value, updated_by)
            VALUES ('RANKING_SFX_FILENAME', :encrypted_value, :updated_by)
            ON CONFLICT (setting_key) DO UPDATE
            SET encrypted_value = EXCLUDED.encrypted_value,
                updated_by = EXCLUDED.updated_by,
                updated_at = CURRENT_TIMESTAMP
        """),
        {"encrypted_value": encrypted_value, "updated_by": user_id},
    )
    await db.commit()
    await load_runtime_settings_cache(db)

    return {"filename": filename}


@router.post("/tasks")
async def create_ranking_task(request: Request, db: AsyncSession = Depends(get_db)):
    """Create a new (empty) ranking project. Inputs are attached afterward via
    POST /ranking/tasks/{id}/inputs, one per already-uploaded video."""
    user_id = await _get_user_id(request, db)
    try:
        task_id = await task_repo.create_task(
            db,
            user_id=user_id,
            source_id=None,
            status="draft",
            task_type="ranking",
        )
        return {"task_id": task_id}
    except Exception as e:
        logger.error(f"Error creating ranking task: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating ranking task: {str(e)}")


class AddInputPayload(BaseModel):
    file_path: str
    original_filename: str
    duration_seconds: Optional[float] = None
    folder_clip_id: Optional[str] = None
    rank_text: Optional[str] = None
    framing: str = "blur_fill"


@router.post("/tasks/{task_id}/inputs")
async def add_ranking_input(
    task_id: str,
    payload: AddInputPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Attach one already-uploaded video (via POST /upload, or already in a
    folder's library via POST /ranking/folders/scan) to a ranking project,
    appended at the end of the current order."""
    await _get_owned_ranking_task(request, db, task_id)
    existing = await ranking_repo.list_inputs(db, task_id)
    input_id = await ranking_repo.add_input(
        db,
        task_id=task_id,
        file_path=payload.file_path,
        original_filename=payload.original_filename,
        duration_seconds=payload.duration_seconds,
        order_index=len(existing),
        folder_clip_id=payload.folder_clip_id,
        rank_text=payload.rank_text,
        framing=payload.framing,
    )
    return {"input_id": input_id}


class RankTextPayload(BaseModel):
    rank_text: str
    folder_clip_id: Optional[str] = None


@router.patch("/tasks/{task_id}/inputs/{input_id}/text")
async def set_ranking_input_text(
    task_id: str,
    input_id: str,
    payload: RankTextPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Save this ranking's text for one input, and — if it came from a
    folder's library — immediately mirror it into that clip's saved_text so
    the text-memory feature doesn't depend on the project actually
    rendering (the render pipeline re-syncs this too, as a safety net for
    inputs attached before this endpoint existed)."""
    await _get_owned_ranking_task(request, db, task_id)
    text_value = payload.rank_text.strip()
    await ranking_repo.update_rank_text(db, task_id, input_id, text_value or None)
    if payload.folder_clip_id and text_value:
        await folder_repo.save_text(db, payload.folder_clip_id, text_value)
    return {"ok": True}


class FramingPayload(BaseModel):
    framing: str


@router.patch("/tasks/{task_id}/inputs/{input_id}/framing")
async def set_ranking_input_framing(
    task_id: str,
    input_id: str,
    payload: FramingPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    if payload.framing not in {"blur_fill", "crop_fill", "letterbox"}:
        raise HTTPException(status_code=400, detail="Invalid framing value")
    await _get_owned_ranking_task(request, db, task_id)
    await ranking_repo.update_framing(db, task_id, input_id, payload.framing)
    return {"ok": True}


@router.get("/tasks/{task_id}/inputs")
async def list_ranking_inputs(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    await _get_owned_ranking_task(request, db, task_id)
    inputs = await ranking_repo.list_inputs(db, task_id)
    return {"inputs": inputs}


class ReorderPayload(BaseModel):
    ordered_input_ids: List[str]


@router.patch("/tasks/{task_id}/inputs/order")
async def reorder_ranking_inputs(
    task_id: str,
    payload: ReorderPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Persist a new drag-and-drop order (auto-number mode: top = #1)."""
    await _get_owned_ranking_task(request, db, task_id)
    await ranking_repo.update_order(db, task_id, payload.ordered_input_ids)
    return {"ok": True}


class RankPayload(BaseModel):
    rank_position: Optional[int] = None


@router.patch("/tasks/{task_id}/inputs/{input_id}/rank")
async def set_ranking_input_rank(
    task_id: str,
    input_id: str,
    payload: RankPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Manually set (or, with null, clear) this input's rank number — the
    "this is #1 directly" mode, independent of drag order."""
    await _get_owned_ranking_task(request, db, task_id)
    await ranking_repo.update_rank_position(db, task_id, input_id, payload.rank_position)
    return {"ok": True}


@router.delete("/tasks/{task_id}/inputs/{input_id}")
async def remove_ranking_input(
    task_id: str, input_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    await _get_owned_ranking_task(request, db, task_id)
    await ranking_repo.remove_input(db, task_id, input_id)
    return {"ok": True}


class RankingSettingsPayload(BaseModel):
    template_id: Optional[str] = None
    number_overlay: Optional[Dict[str, Any]] = None
    export_preset: Optional[str] = None
    target_lufs: Optional[float] = None
    hook_white: Optional[str] = None
    hook_red: Optional[str] = None


@router.patch("/tasks/{task_id}/settings")
async def update_ranking_settings(
    task_id: str,
    payload: RankingSettingsPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    task = await _get_owned_ranking_task(request, db, task_id)
    current = json.loads(task.get("ranking_settings") or "{}")
    updates = {k: v for k, v in payload.model_dump().items() if v is not None}
    current.update(updates)
    await task_repo.update_ranking_settings(db, task_id, current)
    return {"settings": current}


@router.post("/tasks/{task_id}/duplicate")
async def duplicate_ranking_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Clone a ranking project — same inputs (cheap row copy, `file_path` is
    an already-uploaded reference so nothing re-uploads) and the same
    settings, as a new draft task. Doesn't render; the caller (frontend)
    follows up with POST .../render the same way project creation does."""
    source_task = await _get_owned_ranking_task(request, db, task_id)
    user_id = await _get_user_id(request, db)

    new_task_id = await task_repo.create_task(
        db, user_id=user_id, source_id=None, status="draft", task_type="ranking"
    )
    copied = await ranking_repo.duplicate_inputs(db, task_id, new_task_id)
    if copied == 0:
        raise HTTPException(status_code=400, detail="Source project has no inputs to duplicate")

    settings = json.loads(source_task.get("ranking_settings") or "{}")
    if settings:
        await task_repo.update_ranking_settings(db, new_task_id, settings)

    return {"task_id": new_task_id}


@router.post("/tasks/{task_id}/render")
async def render_ranking_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Enqueue the compilation render. Uses the same generic job-queue path
    as clipping (JobQueue.enqueue_processing_job), just a different worker
    function — no job-queue changes were needed for a second job type."""
    task = await _get_owned_ranking_task(request, db, task_id)
    inputs = await ranking_repo.list_inputs(db, task_id)
    if len(inputs) < 2:
        raise HTTPException(
            status_code=400, detail="A ranking compilation needs at least 2 input videos"
        )

    ranking_settings = json.loads(task.get("ranking_settings") or "{}")
    template = get_template(ranking_settings.get("template_id", "rapid_fire"))
    number_overlay = {**template["number_overlay"], **(ranking_settings.get("number_overlay") or {})}
    if number_overlay.get("style") == "stacked":
        missing = [i["original_filename"] for i in inputs if not (i.get("rank_text") or "").strip()]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Every rank needs text before rendering: {', '.join(missing)}",
            )

    await task_repo.update_task_status(db, task_id, "queued", progress=0)
    queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
    job_id = await queue_adapter.enqueue_processing_job(
        "process_ranking_task", "fast", task_id
    )
    return {"task_id": task_id, "job_id": job_id}
