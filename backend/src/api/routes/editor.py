"""Authenticated draft, preview and background export endpoints."""

import logging
import math
import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from ...media_storage import MediaStorage, STORAGE_ERRORS, media_work
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...database import get_db
from ...editor_document import EditDocument, RevisionConflict
from ...editor_state import (
    get_store,
    new_job_id,
    open_session,
    reap_jobs,
    render_job_id,
)
from ...services.editor_service import caption_looks, upgrade_document
from ...services.task_service import TaskService
from ...services.ai_edit_allowance_service import (
    AIEditAllowanceService, AIEditLimitExceeded, refund_ai_edit_usage,
)
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
    await db.close()
    session = await open_session(service.config.temp_dir, task_id, clip)
    return task, clip, session


async def context(request, db, task_id, clip_id):
    _, clip, session = await owned_context(request, db, task_id, clip_id)
    return clip, session


def _clip_broll(clip: dict) -> list:
    from ...clip_source_map import load_clip_broll

    try:
        return load_clip_broll(MediaStorage().local_path(clip["file_path"], metadata_only=True))
    except Exception:
        logging.getLogger(__name__).warning("Could not read the clip's B-roll", exc_info=True)
        return []


async def _job(session, job_id: str) -> dict:
    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Export not found")
    job = await get_store().job(session.key, job_id)
    if job is None:
        raise HTTPException(404, "Export not found")
    return job


async def _stored_media(storage: MediaStorage, session, name: str, download_name: str, *, attachment=False):
    """Redirect to the object storage copy, or None when the file is only on this disk."""
    local = session.directory / name
    # A bucket that is configured but no longer the default still holds
    # media that an earlier configuration stored there.
    if not (storage.enabled or (storage.bucket and not local.exists())):
        return None
    reference = storage.editor_reference(session.directory, name)
    try:
        if await run_in_thread(storage.exists, reference):
            url = await run_in_thread(storage.signed_url, reference, download_name, attachment=attachment)
            return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
    except STORAGE_ERRORS:
        if not local.exists():
            raise HTTPException(503, "Media storage is temporarily unavailable") from None
    return None


@router.get("/{task_id}/clips/{clip_id}/editor")
async def get_editor(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    task, clip, session = await owned_context(request, db, task_id, clip_id)
    state = await session.state()
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
        if state["aiEditing"]:
            state["aiEditUsage"] = await AIEditAllowanceService(db).summary(task["user_id"])
    state["basis"] = clip["filename"]
    await reap_jobs(session.key)
    state["jobs"] = await get_store().jobs(session.key)
    return state


@router.post("/{task_id}/clips/{clip_id}/editor/prepare", status_code=202)
async def prepare(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    clip, session = await context(request, db, task_id, clip_id)
    state = await session.state()
    if state["status"] in {"ready", "preparing"}:
        return {"status": state["status"]}
    store = get_store()
    job = f"editor-prepare:{uuid.uuid4().hex}"
    # One request wins the claim; the others report the preparation it started.
    claimed = await store.claim_prepare(session.key, task_id, clip_id, clip["filename"], job)
    if claimed != "claimed":
        return {"status": claimed}
    try:
        await JobQueue.enqueue_job("prepare_editor", task_id, clip_id, clip["filename"], _job_id=job)
    except Exception:
        await store.fail_prepare(
            session.key,
            task_id,
            clip_id,
            clip["filename"],
            "The rendering worker is unavailable. Try again shortly.",
            only_stale=True,
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
    clip, session = await context(request, db, task_id, clip_id)
    try:
        # Drafts have their own revision check. They do not mutate task/clip
        # rows, so renders must not block autosave.
        if payload.get("basis") != clip["filename"]:
            raise HTTPException(
                409, "The original clip changed. Reopen the editor."
            )
        state = await session.state()
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
        document.validate_duration(state["duration"])
        saved = await get_store().save_draft(session.key, document.model_dump(), revision)
        if saved is None:
            raise RevisionConflict(
                "This clip was edited in another tab. Reload the saved draft before continuing."
            )
        return {"revision": saved, "document": document.model_dump()}
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
    _, session = await context(request, db, task_id, clip_id)
    if asset not in {"clean.mp4", "layout.mp4", "thumbnails.jpg"}:
        raise HTTPException(404, "Preview not ready")
    redirect = await _stored_media(MediaStorage(), session, asset, asset)
    if redirect is not None:
        return redirect
    if not (session.directory / asset).exists():
        raise HTTPException(404, "Preview not ready")
    return FileResponse(
        session.directory / asset,
        media_type="video/mp4" if asset.endswith("mp4") else "image/jpeg",
        headers={"Cache-Control": "private, no-cache"},
    )


@router.post("/{task_id}/clips/{clip_id}/editor/exports", status_code=202)
async def start_export(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    clip, session = await context(request, db, task_id, clip_id)
    payload = await _read_json_object(request)
    state = await session.state()
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
    store = get_store()
    # A render whose worker died must not count against the limit.
    await reap_jobs(session.key)
    job_id = new_job_id()
    # The job keeps the exact document it renders; later saves do not change it.
    if not await store.create_job(
        session.key, job_id, "export", {"document": document.model_dump(), "preset": preset}
    ):
        raise HTTPException(429, "Three exports are already running for this clip")
    try:
        await JobQueue.enqueue_job(
            "export_editor", task_id, clip_id, clip["filename"], job_id, _job_id=render_job_id(job_id)
        )
    except Exception:
        await store.fail_job(job_id, "The rendering worker is unavailable.", expected="queued")
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
    _, session = await context(request, db, task_id, clip_id)
    await reap_jobs(session.key)
    return await _job(session, job_id)


@router.delete("/{task_id}/clips/{clip_id}/editor/exports/{job_id}")
async def cancel_export(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    _, session = await context(request, db, task_id, clip_id)
    await _job(session, job_id)
    # The worker rendering this job sees the request on its next heartbeat.
    return {"status": await get_store().cancel_job(session.key, job_id)}


@router.get("/{task_id}/clips/{clip_id}/editor/exports/{job_id}/file")
async def export_file(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    _, session = await context(request, db, task_id, clip_id)
    job = await _job(session, job_id)
    if job["status"] != "completed":
        raise HTTPException(409, "Export is not ready")
    output = session.directory / f"export-{job_id}.mp4"
    filename = f"supoclip-{clip_id[:8]}-{job_id[:6]}.mp4"
    redirect = await _stored_media(MediaStorage(), session, output.name, filename, attachment=True)
    if redirect is not None:
        return redirect
    if not output.exists():
        raise HTTPException(409, "Export is not ready")
    return FileResponse(
        output,
        media_type="video/mp4",
        filename=filename,
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
    anchor_session = None
    for clip_id in ids:
        clip = await service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(404, "Clip not found")
        session = await open_session(service.config.temp_dir, task_id, {**clip, "id": clip_id})
        anchor_session = anchor_session or session
        state = await session.state()
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
    store = get_store()
    await reap_jobs(anchor_session.key)
    job_id = new_job_id()
    # The job carries each draft as it was when the user asked to combine.
    if not await store.create_job(anchor_session.key, job_id, "combine", {"inputs": entries}):
        raise HTTPException(
            429, "Wait for an existing export to finish before combining"
        )
    try:
        await JobQueue.enqueue_job(
            "combine_editor", task_id, anchor["id"], anchor["filename"], job_id, _job_id=render_job_id(job_id)
        )
    except Exception:
        await store.fail_job(job_id, "The rendering worker is unavailable.", expected="queued")
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
    _, session = await context(request, db, task_id, clip_id)
    orientation, short_side = _broll_target(aspect, await session.state())
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
    _, session = await context(request, db, task_id, clip_id)
    state = await session.state()
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


@router.get("/{task_id}/clips/{clip_id}/editor/ai")
async def ai_edit_usage(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    service = TaskService(db)
    task = await _require_task_owner(request, service, db, task_id)
    clip = await service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip["task_id"] != task_id:
        raise HTTPException(404, "Clip not found")
    return await AIEditAllowanceService(db).summary(task["user_id"])


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
    task, clip, session = await owned_context(request, db, task_id, clip_id)
    state = await session.state()
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
    job_id = uuid.uuid4().hex
    try:
        usage = await AIEditAllowanceService(db).reserve(task["user_id"], job_id)
    except AIEditLimitExceeded as exc:
        raise HTTPException(429 if exc.usage["limit"] else 402, {
            "code": "AI_EDIT_LIMIT_REACHED",
            "message": "Your monthly AI edit allowance is used up." if exc.usage["limit"] else "AI editing requires an active Pro or Creator subscription.",
            "usage": exc.usage,
        }) from None
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
            await refund_ai_edit_usage(job_id)
        except Exception:
            logging.getLogger(__name__).exception("Could not refund failed AI request %s", job_id)
        try:
            await pool.delete(request_key(job_id))
            await pool.delete(f"ai_edit:owner:{job_id}")
        except Exception:
            logging.getLogger(__name__).warning("Could not clean up failed AI request %s", job_id, exc_info=True)
        raise HTTPException(503, "The AI service is unavailable. Your draft is safe.")
    return {"id": job_id, "status": "queued", "usage": usage}


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
    task = await _require_task_owner(request, service, db, task_id)
    await db.close()
    pool = await JobQueue.get_pool()
    owner = await pool.get(f"ai_edit:owner:{job_id}")
    if (owner.decode() if isinstance(owner, bytes) else owner) != f"{task_id}:{clip_id}":
        raise HTTPException(404, "AI edit not found")
    result = await pool.get(result_key(job_id))
    if not result:
        return {"id": job_id, "status": "queued"}
    payload = json.loads(result)
    if payload.get("status") in {"completed", "failed"}:
        payload["usage"] = await AIEditAllowanceService(db).summary(task["user_id"])
    return {"id": job_id, **payload}
