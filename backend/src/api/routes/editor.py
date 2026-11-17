"""Authenticated draft, preview and background export endpoints."""

import logging
import math
import json
import re
import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from ...media_storage import MediaStorage, STORAGE_ERRORS, media_work
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ...analytics import capture_clip_export
from ...database import get_db
from ...editor_document import EditDocument, RevisionConflict, document_fx_on, turns_fx_on
from ...feature_flags import DYNAMIC_EDITS, client_platform, is_enabled as feature_enabled
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
from ..pool_timeout import PoolTimeoutRoute
from .tasks import _require_task_owner, _read_json_object

# A request holds at most one pooled connection at a time. The editor store
# opens a short session per statement, so a route closes the request's
# session (``db``) before it calls the store or does slow I/O; otherwise
# overlapping requests each hold one connection while waiting for another and
# starve the pool.
router = APIRouter(prefix="/tasks", tags=["editor"], route_class=PoolTimeoutRoute)


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


def _clip_edit_fx(clip: dict) -> dict | None:
    """The dynamic edits the clip was generated with (sidecar), or None. Never raises."""
    from ...clip_source_map import load_clip_edit_fx

    try:
        return load_clip_edit_fx(MediaStorage().local_path(clip["file_path"], metadata_only=True))
    except Exception:
        logging.getLogger(__name__).warning("Could not read the clip's dynamic edits", exc_info=True)
        return None


def _needs_fx_seed(*documents: dict | None) -> bool:
    return any(isinstance(d, dict) and d.get("fx") is None for d in documents)


async def _fx_flag(db, request: Request, user_id: str) -> bool:
    """The ``dynamic_edits`` flag for the caller and platform. Fails closed: only turning edits on needs it."""
    try:
        return await feature_enabled(db, DYNAMIC_EDITS, platform=client_platform(request), user_id=user_id)
    except Exception:
        logging.getLogger(__name__).warning("Could not evaluate the dynamic_edits flag", exc_info=True)
        return False


def _dynamic_edits_not_available() -> HTTPException:
    return HTTPException(403, {
        "code": "DYNAMIC_EDITS_NOT_AVAILABLE",
        "message": "Dynamic edits aren't available on your account yet.",
    })


def _stored_document(state: dict, clip: dict | None = None, task: dict | None = None) -> EditDocument | None:
    """The saved draft as a model (seeded like ``GET`` when it predates dynamic edits), or None when unreadable."""
    draft = (state.get("draft") or {}).get("document")
    if not isinstance(draft, dict):
        return None
    if clip is not None and task is not None and draft.get("fx") is None:
        sidecar = _clip_edit_fx(clip)
        if sidecar:
            draft = upgrade_document(draft, clip, task, [], state.get("duration"), sidecar)
    try:
        return EditDocument.model_validate(draft)
    except (ValueError, ValidationError):
        return None


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
        # Drafts that predate dynamic edits are seeded from the clip's own plan.
        fx_sidecar = (
            await run_in_thread(_clip_edit_fx, clip) if _needs_fx_seed(draft["document"], state["original"]) else None
        )
        draft["document"] = upgrade_document(draft["document"], clip, task, placements, state["duration"], fx_sidecar)
        state["original"] = upgrade_document(state["original"], clip, task, placements, state["duration"], fx_sidecar)
        from ...edit_fx.contracts import is_fx_template

        kept_looks = tuple(sorted({
            template for template in (
                (draft["document"].get("captions") or {}).get("template"),
                (state["original"].get("captions") or {}).get("template"),
            ) if is_fx_template(template)
        }))
        state["captionLooks"] = caption_looks(
            include_fx=await _fx_flag(db, request, task["user_id"]), keep=kept_looks
        )
        from ...services.ai_edit_service import ai_editing_configured

        state["aiEditing"] = ai_editing_configured()
        if state["aiEditing"]:
            state["aiEditUsage"] = await AIEditAllowanceService(db).summary(task["user_id"])
            # The allowance read reopened the request's session.
            await db.close()
    if state.get("status") == "ready":
        incomplete = await run_in_thread(_incomplete_source, clip, state)
        if incomplete:
            state["sourceIncomplete"] = incomplete
    state["basis"] = clip["filename"]
    await reap_jobs(session.key)
    state["jobs"] = await get_store().jobs(session.key)
    return state


def _incomplete_source(clip: dict, state: dict) -> dict | None:
    """``{duration, expected}`` when the prepared source lost part of the clip.

    Sessions prepared before the source was checked can hold a truncated
    copy (two preparations of one task once shared a work file). The studio
    offers to rebuild them.
    """
    from ...clip_source_map import load_clip_output_offsets, load_clip_source_ranges
    from ...services.editor_service import expected_prepared_duration, prepared_too_short

    duration = state.get("duration")
    if not isinstance(duration, (int, float)):
        return None
    expected = state.get("expectedDuration")
    if not isinstance(expected, (int, float)):
        try:
            path = MediaStorage().local_path(clip["file_path"], metadata_only=True)
            ranges = load_clip_source_ranges(path)
            if not ranges:
                return None
            expected = expected_prepared_duration(ranges, load_clip_output_offsets(path, ranges))
        except Exception:
            logging.getLogger(__name__).warning("Could not read the clip's source ranges", exc_info=True)
            return None
    if not prepared_too_short(float(duration), float(expected)):
        return None
    return {"duration": round(float(duration), 2), "expected": round(float(expected), 2)}


async def _enqueue_prepare(store, session, task_id: str, clip: dict, job: str) -> None:
    try:
        await JobQueue.enqueue_job("prepare_editor", task_id, clip["id"], clip["filename"], _job_id=job)
    except Exception:
        await store.fail_prepare(
            session.key,
            task_id,
            clip["id"],
            clip["filename"],
            "The rendering worker is unavailable. Try again shortly.",
            only_stale=True,
        )
        raise HTTPException(
            503, "The rendering worker is unavailable. Try again shortly."
        )


@router.post("/{task_id}/clips/{clip_id}/editor/rebuild", status_code=202)
async def rebuild(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Prepare the clip's editing copy again and start a fresh draft.

    The client sends the draft revision it shows, so a rebuild never discards
    edits it has not seen. The replaced draft stays downloadable from
    ``GET .../editor/backup``.
    """
    payload = await _read_json_object(request)
    revision = payload.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise HTTPException(400, "Invalid draft revision")
    clip, session = await context(request, db, task_id, clip_id)
    store = get_store()
    job = f"editor-prepare:{uuid.uuid4().hex}"
    outcome = await store.rebuild(session.key, revision, job)
    if outcome == "missing":
        raise HTTPException(404, "This clip has not been opened in the editor yet")
    if outcome == "busy":
        raise HTTPException(409, "Wait for the current preparation or export to finish, then rebuild.")
    if outcome == "conflict":
        raise HTTPException(409, "This draft changed in another tab. Reload it before rebuilding.")
    await _enqueue_prepare(store, session, task_id, clip, job)
    return {"status": "preparing"}


@router.get("/{task_id}/clips/{clip_id}/editor/backup")
async def draft_backup(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """The draft a rebuild replaced, as a downloadable JSON file."""
    from fastapi.responses import JSONResponse

    clip, session = await context(request, db, task_id, clip_id)
    backup = await get_store().draft_backup(session.key)
    if not isinstance(backup, dict) or not isinstance(backup.get("document"), dict):
        raise HTTPException(404, "No earlier draft is kept for this clip")
    stem = re.sub(r"[^A-Za-z0-9_-]", "_", clip["filename"].rsplit(".", 1)[0])[:80] or "clip"
    return JSONResponse(
        {"clip": clip_id, "basis": clip["filename"], **backup},
        headers={
            "Content-Disposition": f'attachment; filename="{stem}-previous-draft.json"',
            "Cache-Control": "private, no-store",
        },
    )


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
    await _enqueue_prepare(store, session, task_id, clip, job)
    return {"status": "preparing"}


@router.patch("/{task_id}/clips/{clip_id}/editor")
async def save(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    service = TaskService(db)
    task = await _require_task_owner(request, service, db, task_id)
    # Don't hold a connection while the body uploads; context() checks again.
    await db.close()
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
        raw_document = payload.get("document")
        stored = (state.get("draft") or {}).get("document") or {}
        if isinstance(raw_document, dict) and raw_document.get("fx") is None and stored.get("fx") is not None:
            # A client that does not know dynamic edits never drops them (tested on the raw JSON, AI-H1).
            raw_document = {**raw_document, "fx": stored["fx"]}
        document = EditDocument.model_validate(raw_document)
        document.validate_duration(state["duration"])
        if document_fx_on(document):
            await _check_fx_write(db, request, task, clip, state, document)
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


async def _check_fx_write(db, request: Request, task: dict, clip: dict, state: dict, document: EditDocument) -> None:
    """A saved draft with dynamic edits: 403 when it turns them on without the flag (§0.4.8), 422 when its freezes
    hold longer than the budget and longer than the stored draft's (§5.1; a combined clip may already exceed it)."""
    from ...editor_document import edit_duration, fx_hold_budget, mapped_hold_total

    # Seeding an old draft reads the clip's sidecar, which may come from the bucket: keep it off the event loop.
    before = await run_in_thread(_stored_document, state, clip, task)
    if turns_fx_on(before, document) and not await _fx_flag(db, request, task["user_id"]):
        raise _dynamic_edits_not_available()
    held = mapped_hold_total(document)
    if held > fx_hold_budget(edit_duration(document)) + 1e-6 and held > (
        mapped_hold_total(before) if before is not None else 0.0
    ) + 1e-6:
        raise HTTPException(422, {
            "code": "FX_HOLD_BUDGET",
            "message": "Freeze frames can add at most 4% of the clip's length (1.6 s at most).",
        })


@router.get("/{task_id}/clips/{clip_id}/editor/media/{asset}")
async def media(
    task_id: str,
    clip_id: str,
    asset: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    _, session = await context(request, db, task_id, clip_id)
    if asset not in {"clean.mp4", "layout.mp4", "thumbnails.jpg", "matte.mp4"}:
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
    task, _, session = await owned_context(request, db, task_id, clip_id)
    job = await _job(session, job_id)
    if job["status"] != "completed":
        raise HTTPException(409, "Export is not ready")
    output = session.directory / f"export-{job_id}.mp4"
    filename = f"supoclip-{clip_id[:8]}-{job_id[:6]}.mp4"
    redirect = await _stored_media(MediaStorage(), session, output.name, filename, attachment=True)
    if redirect is not None:
        capture_clip_export(request, task, surface="editor", format="editor")
        return redirect
    if not output.exists():
        raise HTTPException(409, "Export is not ready")
    capture_clip_export(request, task, surface="editor", format="editor")
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
    clips = []
    for clip_id in ids:
        clip = await service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(404, "Clip not found")
        clips.append(clip)
    await db.close()
    entries = []
    anchor_session = None
    for clip_id, clip in zip(ids, clips):
        session = await open_session(service.config.temp_dir, task_id, {**clip, "id": clip_id})
        anchor_session = anchor_session or session
        state = await session.state()
        if state["status"] != "ready":
            raise HTTPException(409, "Prepare each selected clip before combining")
        placements = (
            await run_in_thread(_clip_broll, clip)
            if "broll" not in state["draft"]["document"] else []
        )
        fx_sidecar = (
            await run_in_thread(_clip_edit_fx, clip) if _needs_fx_seed(state["draft"]["document"]) else None
        )
        from ...clip_source_map import load_clip_output_offsets
        source_ranges = await run_in_thread(service._get_clip_source_ranges, clip)
        source_offsets = load_clip_output_offsets(MediaStorage().local_path(clip["file_path"], metadata_only=True), source_ranges)
        entries.append(
            {
                "id": clip_id,
                "filename": clip["filename"],
                "document": upgrade_document(
                    state["draft"]["document"], clip, task, placements, state["duration"], fx_sidecar
                ),
                "state": {k: state[k] for k in ("width", "height", "hasAudio", "mediaVersion") if k in state},
                "ranges": source_ranges,
                "source_offsets": source_offsets,
            }
        )
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

    # Dynamic-edit operations are offered only to flagged callers, or on a draft that already has edits (§0.4.8);
    # everyone else's request, prompt and grammar stay exactly as before.
    fx_flag = await _fx_flag(db, request, task["user_id"])
    fx_request = {}
    if fx_flag or document_fx_on(document):
        fx_request = {
            "fx_allowed": True,
            "fx_flag": fx_flag,
            "fx_seed": _draft_fx_seed(document, task_id, clip_id),
            "layout_has_zoompan": _layout_has_zoompan(state, document),
        }

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
                    **fx_request,
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


# --------------------------------------------------------------------------
# Dynamic edits (§5.5)
# --------------------------------------------------------------------------

FX_CUE_ID = re.compile(r"[A-Za-z0-9_.:@~-]{1,32}")


def _layout_has_zoompan(state: dict, document: EditDocument) -> bool:
    """The picture this draft renders from already zooms (Ken Burns baked into the layout, AR-M9).

    Sessions prepared before the studio recorded it are assumed zoomed when they have a layout, so turning edits
    on never stacks a second zoom on one.
    """
    has_layout = bool(state.get("hasLayout"))
    if not state.get("layoutHasZoompan", has_layout):
        return False
    if not has_layout:
        return True     # the composed frame replaced clean.mp4 itself
    return document.framing.fit == "auto" and document.framing.aspect == "vertical"


def _draft_fx_seed(document: EditDocument, task_id: str, clip_id: str) -> int:
    """The draft's own seed, or this clip's studio seed when it has none yet. A container the browser created with
    the default seed 0 also gets the clip's seed, so two clips never resolve to the same random choices."""
    from ...editor_document import studio_fx_seed

    if document.fx is not None and document.fx.seed:
        return document.fx.seed
    return studio_fx_seed(task_id, clip_id)


def _current_base(state: dict, document: EditDocument | None) -> str:
    uses_layout = (
        bool(state.get("hasLayout")) and document is not None
        and document.framing.fit == "auto" and document.framing.aspect == "vertical"
    )
    return "layout" if uses_layout else "clean"


def _ready(state: dict) -> None:
    if state.get("status") != "ready":
        raise HTTPException(409, "Wait for the editor preview to finish preparing")


def _payload_document(payload: dict, state: dict) -> EditDocument:
    try:
        return EditDocument.model_validate(payload.get("document")).validate_duration(state["duration"])
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc))


def _with_style(document: EditDocument, style) -> EditDocument:
    from ...editor_document import DocFx

    return document.model_copy(update={"fx": (document.fx or DocFx()).model_copy(update={"style": style})})


@router.post("/{task_id}/clips/{clip_id}/editor/fx/resolve")
async def fx_resolve(task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Re-resolve the draft's automatic edits for an edit style, in edit time (§5.5). Nothing is saved: the studio
    applies the returned draft as one undoable change and saves it like any edit."""
    from ...edit_fx.settings import EditStyleError, parse_edit_style
    from ...editor_document import NeedsPlan, resolve_document_fx

    payload = await _read_json_object(request)
    task, clip, session = await owned_context(request, db, task_id, clip_id)
    state = await session.state()
    _ready(state)
    document = _payload_document(payload, state)
    try:
        style = parse_edit_style(payload.get("style"))
    except EditStyleError as exc:
        raise HTTPException(422, {"code": "INVALID_EDIT_STYLE", "message": str(exc)}) from None
    stored = await run_in_thread(_stored_document, state, clip, task)
    if turns_fx_on(stored, _with_style(document, style)) and not await _fx_flag(db, request, task["user_id"]):
        raise _dynamic_edits_not_available()
    seed = _draft_fx_seed(document, task_id, clip_id)
    try:
        resolved = await run_in_thread(
            resolve_document_fx, document, style, seed=seed,
            layout_has_zoompan=_layout_has_zoompan(state, document),
        )
        resolved.validate_duration(state["duration"])
    except NeedsPlan:
        raise HTTPException(409, {
            "code": "NEEDS_PLAN",
            "message": "Plan edits for this clip first: they decide where the effects go.",
        }) from None
    except (ValueError, ValidationError) as exc:
        raise HTTPException(400, str(exc))
    return {"document": resolved.model_dump()}


def _plan_words(document: EditDocument) -> list[dict]:
    """The words the edit plays, in source (S) time and order: beats are stored in S."""
    kept = [(s.start, s.end) for s in document.segments]
    return [
        {"id": w.id, "start": w.start, "end": w.end, "text": w.text}
        for w in sorted(document.words, key=lambda w: (w.start, w.id))
        if w.text.strip() and any(a < w.end and w.start < b for a, b in kept)
    ]


@router.post("/{task_id}/clips/{clip_id}/editor/fx/plan", status_code=202)
async def fx_plan(task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Queue beat tagging for the draft on the AI worker (``edit_fx_plan_job``); counts against the daily limit."""
    from ...edit_fx.env import edit_plan_daily_limit
    from ...services.ai_edit_service import (
        AI_EDIT_QUEUE,
        FX_PLAN_TTL_SECONDS,
        fx_plan_meta_key,
        fx_plan_request_key,
        refund_edit_plan,
        reserve_edit_plan,
    )

    payload = await _read_json_object(request)
    task, clip, session = await owned_context(request, db, task_id, clip_id)
    state = await session.state()
    _ready(state)
    document = _payload_document(payload, state)
    stored = await run_in_thread(_stored_document, state, clip, task)
    if not document_fx_on(stored) and not await _fx_flag(db, request, task["user_id"]):
        raise _dynamic_edits_not_available()
    pool = await JobQueue.get_pool()
    job_id = uuid.uuid4().hex
    daily_key = await reserve_edit_plan(pool, task["user_id"])
    if daily_key is None:
        raise HTTPException(429, {
            "code": "EDIT_PLAN_LIMIT_REACHED",
            "message": "You've planned edits as often as you can today. Try again tomorrow.",
            "limit": edit_plan_daily_limit(),
        })
    meta = {"owner": f"{task_id}:{clip_id}", "queued_at": time.time(), "daily_key": daily_key}
    try:
        await pool.set(fx_plan_meta_key(job_id), json.dumps(meta), ex=FX_PLAN_TTL_SECONDS)
        await pool.set(
            fx_plan_request_key(job_id),
            json.dumps({
                "words": _plan_words(document),
                "clip_seconds": round(sum(s.end - s.start for s in document.segments), 3),
                "user_id": task["user_id"],
                "task_id": task_id,
            }),
            ex=FX_PLAN_TTL_SECONDS,
        )
        await JobQueue.enqueue_job(
            "edit_fx_plan_job", job_id, _queue_name=AI_EDIT_QUEUE, _job_id=f"edit_fx_plan_job:{job_id}"
        )
    except Exception:
        try:
            await refund_edit_plan(pool, job_id, daily_key)
            await pool.delete(fx_plan_request_key(job_id))
            await pool.delete(fx_plan_meta_key(job_id))
        except Exception:
            logging.getLogger(__name__).warning("Could not clean up failed plan request %s", job_id, exc_info=True)
        raise HTTPException(503, "The AI service is unavailable. Your draft is safe.")
    return {"job_id": job_id, "status": "queued"}


@router.get("/{task_id}/clips/{clip_id}/editor/fx/plan/{job_id}")
async def fx_plan_status(
    task_id: str,
    clip_id: str,
    job_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """``queued | running | done | failed``; ``beats`` (S) when done. A request no worker answered within
    3 × ``EDIT_PLAN_TIMEOUT_SECONDS`` fails with ``WORKER_UNAVAILABLE`` and its daily unit is refunded once (AI-M7)."""
    from ...edit_fx.env import edit_plan_timeout
    from ...services.ai_edit_service import (
        FX_PLAN_TTL_SECONDS,
        fx_plan_meta_key,
        fx_plan_result_key,
        refund_edit_plan,
    )

    if not re.fullmatch(r"[a-f0-9]{32}", job_id):
        raise HTTPException(404, "Plan not found")
    service = TaskService(db)
    await _require_task_owner(request, service, db, task_id)
    await db.close()
    pool = await JobQueue.get_pool()
    raw_meta = await pool.get(fx_plan_meta_key(job_id))
    try:
        meta = json.loads(raw_meta) if raw_meta else None
    except ValueError:
        meta = None
    if not isinstance(meta, dict) or meta.get("owner") != f"{task_id}:{clip_id}":
        raise HTTPException(404, "Plan not found")
    raw = await pool.get(fx_plan_result_key(job_id))
    try:
        result = json.loads(raw) if raw else {"status": "queued"}
    except ValueError:
        result = {"status": "failed", "error": {"code": "PLAN_FAILED", "message": "Planning failed. Try again."}}
    if result.get("status") in {"queued", "running"} and (
        time.time() - float(meta.get("queued_at") or 0) > 3 * edit_plan_timeout()
    ):
        await refund_edit_plan(pool, job_id, meta.get("daily_key"))
        result = {"status": "failed", "error": {
            "code": "WORKER_UNAVAILABLE",
            "message": "The AI service didn't answer. Try planning again.",
        }}
        await pool.set(fx_plan_result_key(job_id), json.dumps(result), ex=FX_PLAN_TTL_SECONDS)
    return {"job_id": job_id, **result}


@router.post("/{task_id}/clips/{clip_id}/editor/fx/analysis", status_code=202)
async def fx_analysis(task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)):
    """Queue the face track (and the matte for behind-the-person text) for the base video the draft renders from, on
    the video worker (``editor_fx_analysis``). Idempotent per prepared media and base; ``ready`` when the session
    already has what the draft needs."""
    from ...services.editor_fx_analysis import (
        ANALYSIS_JOB_TIMEOUT_SECONDS,
        analysis_job_id,
        analysis_needs,
        session_base,
    )

    task, clip, session = await owned_context(request, db, task_id, clip_id)
    state = await session.state()
    _ready(state)
    draft = await run_in_thread(_stored_document, state, clip, task)
    raw = draft.model_dump(mode="json") if draft is not None else (state.get("draft") or {}).get("document")
    _, needs_matte = analysis_needs(raw)
    base = session_base(raw, state)
    face = state.get("faceTrack")
    has_face = isinstance(face, dict) and face.get("base") == base and isinstance(face.get("track"), dict)
    has_matte = bool(state.get("matteVersion")) and (state.get("matteBase") or "clean") == base
    if has_face and (not needs_matte or has_matte):
        return {"status": "ready"}
    job = f"{analysis_job_id(session.key, state.get('mediaVersion'), matte=needs_matte)}:{base}"
    # ARQ keeps a finished job's result (keep_result) and ignores a new enqueue under the same id meanwhile. A run that
    # failed, or whose worker died while "running" (tries exhausted), would then answer "queued" without running:
    # retry those under a fresh id, at most once a minute.
    run = state.get("fxAnalysis") if isinstance(state.get("fxAnalysis"), dict) else {}
    started = run.get("startedAt")
    stale = (run.get("status") == "running" and isinstance(started, (int, float))
             and time.time() - started > 2 * ANALYSIS_JOB_TIMEOUT_SECONDS)
    if run.get("base") == base and (run.get("status") == "failed" or stale):
        job = f"{job}:r{int(time.time()) // 60}"
    try:
        await JobQueue.enqueue_job("editor_fx_analysis", task_id, clip_id, clip["filename"], _job_id=job)
    except Exception:
        raise HTTPException(503, "The rendering worker is unavailable. Try again shortly.")
    return {"status": "queued"}


@router.get("/{task_id}/clips/{clip_id}/editor/fx/reaction/{cue_id}")
async def fx_reaction(
    task_id: str,
    clip_id: str,
    cue_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Stream a saved reaction cue's GIF (mp4) for the preview. 404 when reactions are off or it is unavailable."""
    import shutil
    import tempfile

    from starlette.background import BackgroundTask

    from ...edit_fx.reactions import ensure_reaction_file

    if not FX_CUE_ID.fullmatch(cue_id):
        raise HTTPException(404, "Reaction not found")
    _, session = await context(request, db, task_id, clip_id)
    state = await session.state()
    _ready(state)
    draft = _stored_document(state)
    cue = next(
        (c for c in (draft.fx.cues if draft is not None and draft.fx else []) if c.id == cue_id and c.kind == "reaction"),
        None,
    )
    if cue is None:
        raise HTTPException(404, "Reaction not found")
    scratch = Path(tempfile.mkdtemp(prefix="fx-reaction-"))
    path = await ensure_reaction_file(cue, scratch)
    if path is None or not path.exists():
        shutil.rmtree(scratch, ignore_errors=True)
        raise HTTPException(404, "Reaction not found")
    return FileResponse(
        path,
        media_type="video/mp4",
        headers={"Cache-Control": "private, max-age=86400"},
        background=BackgroundTask(shutil.rmtree, scratch, ignore_errors=True),
    )
