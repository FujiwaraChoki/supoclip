"""Authenticated draft, preview and background export endpoints."""

from datetime import datetime, timezone
from pathlib import Path
import logging
import math
import json
import os
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from ...media_storage import MediaStorage, STORAGE_ERRORS, is_s3, media_work
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
from ...services.editor_service import caption_looks, read_state, upgrade_document
from ...services.task_service import TaskService
from ...workers.job_queue import JobQueue
from ...utils.async_helpers import run_in_thread
from .tasks import _require_task_owner, _read_json_object

router = APIRouter(prefix="/tasks", tags=["editor"])


async def owned_context(request, db, task_id, clip_id):
    service = TaskService(db)
    task = await _require_task_owner(request, service, db, task_id)
    clip = await service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip["task_id"] != task_id:
        raise HTTPException(404, "Clip not found")
    directory = editor_dir(service.config.temp_dir, task_id, clip_id, clip["filename"])
    await db.close()
    await run_in_thread(MediaStorage().restore_editor, directory, s3_backed=is_s3(clip["file_path"]))
    return task, clip, directory


async def context(request, db, task_id, clip_id):
    _, clip, directory = await owned_context(request, db, task_id, clip_id)
    return clip, directory


def _clip_broll(clip: dict) -> list:
    from ...clip_source_map import load_clip_broll

    try:
        return load_clip_broll(MediaStorage().local_path(clip["file_path"], metadata_only=True))
    except Exception:
        logging.getLogger(__name__).warning("Could not read the clip's B-roll", exc_info=True)
        return []


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
    task, clip, directory = await owned_context(request, db, task_id, clip_id)
    state = await run_in_thread(read_state, directory)
    if state.get("status") == "ready":
        draft = state["draft"]
        placements = []
        if "broll" not in draft["document"] or "broll" not in state["original"]:
            placements = await run_in_thread(_clip_broll, clip)
        draft["document"] = upgrade_document(draft["document"], clip, task, placements, state["duration"])
        state["original"] = upgrade_document(state["original"], clip, task, placements, state["duration"])
        state["captionLooks"] = caption_looks()
        from ...services.ai_edit_service import ai_editing_configured

        state["aiEditing"] = ai_editing_configured()
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
    # Restore missing files before checking the local draft revision.
    clip, directory = await context(request, db, task_id, clip_id)
    try:
        # Drafts have their own file lock and revision check. They do not
        # mutate task/clip rows, so renders must not block autosave.
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
        result = await run_in_thread(
            save_document, directory, document, revision, state["duration"]
        )
        try:
            await run_in_thread(MediaStorage().save_editor, directory, metadata_only=True, blocking=False)
        except STORAGE_ERRORS:
            logging.getLogger(__name__).warning("Draft saved locally; S3 snapshot will retry")
        return result
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
    if asset not in {"clean.mp4", "layout.mp4", "thumbnails.jpg"}:
        raise HTTPException(404, "Preview not ready")
    storage = MediaStorage()
    try:
        if (storage.enabled or (directory / ".s3-manifest").exists()) and await run_in_thread(storage.exists, storage.editor_reference(directory, asset)):
            url = await run_in_thread(storage.signed_url, storage.editor_reference(directory, asset), asset)
            return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
    except STORAGE_ERRORS:
        if not (directory / asset).exists():
            raise HTTPException(503, "Media storage is temporarily unavailable") from None
    if not (directory / asset).exists():
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
    if status["status"] != "completed":
        raise HTTPException(409, "Export is not ready")
    storage = MediaStorage()
    reference = storage.editor_reference(directory, output.name)
    try:
        if (storage.enabled or (directory / ".s3-manifest").exists()) and await run_in_thread(storage.exists, reference):
            url = await run_in_thread(storage.signed_url, reference, f"supoclip-{clip_id[:8]}-{job_id[:6]}.mp4", attachment=True)
            return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
    except STORAGE_ERRORS:
        if not output.exists():
            raise HTTPException(503, "Media storage is temporarily unavailable") from None
    if not output.exists():
        raise HTTPException(409, "Export is not ready")
    return FileResponse(
        output,
        media_type="video/mp4",
        filename=f"supoclip-{clip_id[:8]}-{job_id[:6]}.mp4",
        headers={"Cache-Control": "private, no-store"},
    )


@router.post("/{task_id}/editor/combine", status_code=202)
@media_work
async def combine(task_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    service = TaskService(db)
    task = await _require_task_owner(request, service, db, task_id)
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
        await run_in_thread(MediaStorage().restore_editor, directory)
        state = read_state(directory)
        if state["status"] != "ready":
            raise HTTPException(409, "Prepare each selected clip before combining")
        placements = (
            await run_in_thread(_clip_broll, clip)
            if "broll" not in state["draft"]["document"] else []
        )
        from ...clip_source_map import load_clip_output_offsets
        source_ranges = await run_in_thread(service._get_clip_source_ranges, clip)
        source_offsets = load_clip_output_offsets(MediaStorage().local_path(clip["file_path"], metadata_only=True), source_ranges)
        entries.append(
            {
                "id": clip_id,
                "filename": clip["filename"],
                "document": upgrade_document(
                    state["draft"]["document"], clip, task, placements, state["duration"]
                ),
                "state": {k: state[k] for k in ("width", "height", "hasAudio")},
                "ranges": source_ranges,
                "source_offsets": source_offsets,
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


def _broll_target(aspect: str, state: dict) -> tuple[str, int]:
    """Pexels orientation and minimum short side for the draft's output frame."""
    if aspect == "square":
        return "square", 1080
    if aspect == "original":
        width, height = int(state.get("width") or 0), int(state.get("height") or 0)
        short_side = min(1080, min(width, height) or 1080)
        return ("landscape" if width > height else "portrait"), short_side
    return "portrait", 1080


def _require_broll():
    from ...broll import broll_configured

    if not broll_configured():
        raise HTTPException(503, "Stock footage isn't configured on this server.")


@router.get("/{task_id}/clips/{clip_id}/editor/broll/search")
async def broll_search(
    task_id: str,
    clip_id: str,
    request: Request,
    q: str = "",
    page: int = 1,
    aspect: str = "vertical",
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Pexels footage for the B-roll panel, sized for the draft's frame."""
    from ...broll import search_footage

    _require_broll()
    query = " ".join(q.split())[:80]
    if not query:
        raise HTTPException(400, "Enter something to search for")
    if not 1 <= page <= 20 or aspect not in {"vertical", "square", "original"}:
        raise HTTPException(400, "Invalid search")
    _, directory = await context(request, db, task_id, clip_id)
    orientation, short_side = _broll_target(aspect, read_state(directory))
    results = await search_footage(query, orientation, short_side, page)
    return {"results": results, "provider": "pexels"}


@router.post("/{task_id}/clips/{clip_id}/editor/broll/suggest")
async def broll_suggest(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """AI-picked cutaways for the draft's spoken words, avoiding existing B-roll."""
    from ...broll import BRollSession, placement_to_editor_clip, plan_clip_broll

    _require_broll()
    payload = await _read_json_object(request)
    _, directory = await context(request, db, task_id, clip_id)
    state = read_state(directory)
    if state["status"] != "ready":
        raise HTTPException(409, "Wait for the editor preview to finish preparing")
    try:
        document = EditDocument.model_validate(payload.get("document")).validate_duration(state["duration"])
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc))
    kept = [s for s in document.segments]
    words = [
        {"text": w.text, "start": w.start, "end": w.end}
        for w in sorted(document.words, key=lambda w: w.start)
        if any(s.start <= w.start < s.end for s in kept)
    ]
    if not words:
        return {"broll": []}
    session = BRollSession()
    session.used_ids.update(b.footage.id for b in document.broll)
    orientation, short_side = _broll_target(document.framing.aspect, state)
    placements = await plan_clip_broll(words, state["duration"], orientation, short_side, session)
    suggestions = []
    for index, placement in enumerate(placements):
        clip = placement_to_editor_clip(placement, index, state["duration"])
        if not clip:
            continue
        clip["id"] = uuid.uuid4().hex
        overlaps = any(
            clip["start"] < b.end and b.start < clip["end"] for b in document.broll
        )
        if not overlaps:
            suggestions.append(clip)
    return {"broll": suggestions}


@router.get("/{task_id}/clips/{clip_id}/editor/broll/footage/{video_id}/{size}")
async def broll_footage(
    task_id: str,
    clip_id: str,
    video_id: int,
    size: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Stream a cached Pexels rendition so the preview can draw it on its canvas."""
    from ...broll import ensure_footage_file

    match = re.fullmatch(r"(\d{2,5})x(\d{2,5})\.mp4", size)
    if not match or video_id <= 0:
        raise HTTPException(404, "Footage not found")
    service = TaskService(db)
    await _require_task_owner(request, service, db, task_id)
    clip = await service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip["task_id"] != task_id:
        raise HTTPException(404, "Clip not found")
    await db.close()
    path = await ensure_footage_file(
        {"id": video_id, "width": int(match[1]), "height": int(match[2]), "fileUrl": None}
    )
    if path is None:
        raise HTTPException(404, "Footage not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        # A rendition never changes; the browser can keep it.
        headers={"Cache-Control": "private, max-age=86400, immutable"},
    )


AI_EDIT_DAILY_LIMIT = int(os.getenv("AI_EDIT_DAILY_LIMIT", "150"))


@router.post("/{task_id}/clips/{clip_id}/editor/ai", status_code=202)
async def start_ai_edit(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Queue a plain-language edit for the AI worker."""
    from ...services.ai_edit_service import (
        AI_EDIT_QUEUE,
        AI_EDIT_RESULT_TTL_SECONDS,
        MAX_INSTRUCTION_CHARS,
        ai_editing_configured,
        request_key,
    )

    if not ai_editing_configured():
        raise HTTPException(503, "AI editing isn't set up on this server.")
    payload = await _read_json_object(request)
    task, clip, directory = await owned_context(request, db, task_id, clip_id)
    state = read_state(directory)
    if state["status"] != "ready":
        raise HTTPException(409, "Wait for the editor preview to finish preparing")
    if payload.get("basis") != clip["filename"]:
        raise HTTPException(409, "The original clip changed. Reopen the editor.")
    if not isinstance(payload.get("instruction"), str):
        raise HTTPException(400, "Describe the change as text")
    instruction = " ".join(payload["instruction"].split())
    if not instruction:
        raise HTTPException(400, "Describe the change you want")
    if len(instruction) > MAX_INSTRUCTION_CHARS:
        raise HTTPException(400, f"Keep it under {MAX_INSTRUCTION_CHARS} characters")
    try:
        document = EditDocument.model_validate(payload.get("document")).validate_duration(state["duration"])
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc))
    playhead = payload.get("playhead")
    if isinstance(playhead, bool) or not isinstance(playhead, (int, float)):
        playhead = None
    elif isinstance(playhead, float) and not math.isfinite(playhead):
        playhead = None
    else:
        # Clamp integers before converting: an arbitrarily large JSON integer
        # must not overflow float() and turn a harmless cursor hint into a 500.
        playhead = float(min(state["duration"], max(0, playhead)))

    pool = await JobQueue.get_pool()
    day = datetime.now(timezone.utc).strftime("%Y%m%d")
    counter = f"ai_edit:count:{task['user_id']}:{day}"
    used = await pool.incr(counter)
    if used == 1:
        await pool.expire(counter, 2 * 86400)
    if used > AI_EDIT_DAILY_LIMIT:
        raise HTTPException(429, "You've reached today's limit for AI edits. It resets tomorrow.")

    job_id = uuid.uuid4().hex
    try:
        await pool.set(
            request_key(job_id),
            json.dumps(
                {
                    "document": document.model_dump(),
                    "instruction": instruction,
                    "playhead": playhead,
                    "duration": state["duration"],
                    "width": state.get("width"),
                    "height": state.get("height"),
                }
            ),
            ex=600,
        )
        await pool.set(f"ai_edit:owner:{job_id}", f"{task_id}:{clip_id}", ex=AI_EDIT_RESULT_TTL_SECONDS)
        await JobQueue.enqueue_job("ai_edit_job", job_id, _queue_name=AI_EDIT_QUEUE)
    except Exception:
        try:
            await pool.decr(counter)
            await pool.delete(request_key(job_id))
            await pool.delete(f"ai_edit:owner:{job_id}")
        except Exception:
            logging.getLogger(__name__).warning("Could not clean up failed AI request %s", job_id, exc_info=True)
        raise HTTPException(503, "The AI service is unavailable. Your draft is safe.")
    return {"id": job_id, "status": "queued"}


@router.get("/{task_id}/clips/{clip_id}/editor/ai/{job_id}")
async def ai_edit_status(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    from ...services.ai_edit_service import result_key

    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "AI edit not found")
    service = TaskService(db)
    await _require_task_owner(request, service, db, task_id)
    await db.close()
    pool = await JobQueue.get_pool()
    owner = await pool.get(f"ai_edit:owner:{job_id}")
    if (owner.decode() if isinstance(owner, bytes) else owner) != f"{task_id}:{clip_id}":
        raise HTTPException(404, "AI edit not found")
    result = await pool.get(result_key(job_id))
    if not result:
        return {"id": job_id, "status": "queued"}
    return {"id": job_id, **json.loads(result)}
