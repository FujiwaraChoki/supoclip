"""Full-quality renders of preview clips.

Clips from a task with ``preview_renders`` start as previews
(``render_quality: "preview"``). These routes start the full render and report
its progress, so a client can show "Preparing full quality…" and download once
``full_render.status`` is ``ready``:

- ``POST /tasks/{task_id}/clips/{clip_id}/full-render`` starts it (202)
- ``GET  /tasks/{task_id}/clips/{clip_id}/full-render`` polls it
- ``POST|GET /tasks/{task_id}/full-render`` does the same for every clip

The download, ZIP, export, edit and publish routes call ``require_full_clip``
first. Clients that never poll still get the full file from the same URLs: the
request waits for the render (``FULL_RENDER_DOWNLOAD_WAIT_SECONDS``) and answers
503 with ``Retry-After`` and ``code: FULL_QUALITY_PREPARING`` past that.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from ...analytics import request_client, request_user_id
from ...config import get_config
from ...database import get_db
from ...repositories.clip_repository import ClipRepository
from ...services.full_render_service import (
    FAILED_CODE,
    FAILED_MESSAGE,
    PREPARING_CODE,
    PREPARING_MESSAGE,
    TRIGGERS,
    FullRenderFailed,
    FullRenderPending,
    FullRenderUnavailable,
    ensure_full_quality,
    is_preview,
    request_full_render,
    serialize_state,
    summarize,
)
from ...services.task_service import TaskService
from ..pool_timeout import PoolTimeoutRoute
from .tasks import _require_task_reader

router = APIRouter(prefix="/tasks", tags=["tasks"], route_class=PoolTimeoutRoute)

RETRY_AFTER_SECONDS = 10


def render_queue(request: Request):
    queue = getattr(request.app.state, "queue_adapter", None)
    if queue is None:
        from ...workers.job_queue import JobQueue

        queue = JobQueue
    return queue


ACCEPT_PREVIEW_HEADER = "x-supoclip-accept-preview"


def needs_full_file(request: Request, download: bool) -> bool:
    """Whether ``GET .../file`` must serve the full-quality clip.

    The web app plays previews inline and asks for ``download=true`` when it
    saves one. Other clients (the iOS app, API keys, MCP) get the full clip
    unless they say they are only playing it (``x-supoclip-accept-preview: 1``):
    builds from before previews can't tell us whether they play or save.
    """
    if download:
        return True
    if (request.headers.get(ACCEPT_PREVIEW_HEADER) or "").strip() == "1":
        return False
    return request_client(request) != "web"


def _preparing(state: dict) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={"code": PREPARING_CODE, "message": PREPARING_MESSAGE, **state},
        headers={"Retry-After": str(RETRY_AFTER_SECONDS)},
    )


def _failed(state: dict) -> HTTPException:
    message = (state.get("full_render") or {}).get("error") or FAILED_MESSAGE
    return HTTPException(
        status_code=502, detail={"code": FAILED_CODE, "message": message, **state}
    )


async def require_full_clip(
    request: Request,
    db: AsyncSession,
    task: dict,
    clip: dict,
    *,
    trigger: str,
    timeout: Optional[float] = None,
) -> dict:
    """The clip once it is full quality, rendering it on demand.

    Returns the refreshed clip (its ``file_path`` is the full render). A
    preview ends the request's transaction while it waits.
    """
    if not is_preview(clip):
        return clip
    wait = get_config().full_render_download_wait_seconds if timeout is None else timeout
    try:
        return await ensure_full_quality(
            db,
            clip,
            queue=render_queue(request),
            trigger=trigger,
            user_id=task.get("user_id"),
            timeout=wait,
        )
    except FullRenderPending as exc:
        raise _preparing(exc.state) from None
    except FullRenderFailed as exc:
        raise _failed(exc.state) from None
    except FullRenderUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=str(exc), headers={"Retry-After": str(RETRY_AFTER_SECONDS)}
        ) from None


async def require_full_clips(
    request: Request, db: AsyncSession, task: dict, clips: list[dict], *, trigger: str
) -> list[dict]:
    """Every clip at full quality (the ZIP). Starts all renders before waiting on any."""
    # Task listings leave out the clip's task id.
    previews = [{**clip, "task_id": task.get("id")} for clip in clips if is_preview(clip)]
    if not previews:
        return clips
    queue = render_queue(request)
    try:
        for clip in previews:
            await request_full_render(
                db, clip, queue=queue, trigger=trigger, user_id=task.get("user_id")
            )
    except FullRenderUnavailable as exc:
        raise HTTPException(
            status_code=503, detail=str(exc), headers={"Retry-After": str(RETRY_AFTER_SECONDS)}
        ) from None
    import time

    deadline = time.monotonic() + get_config().full_render_download_wait_seconds
    ready: dict[str, dict] = {}
    for clip in previews:
        current = await require_full_clip(
            request, db, task, clip, trigger=trigger,
            timeout=max(0.0, deadline - time.monotonic()),
        )
        ready[clip["id"]] = current
    return [ready.get(clip["id"], clip) for clip in clips]


async def _task_and_clip(request: Request, db: AsyncSession, task_id: str, clip_id: str):
    task = await _require_task_reader(request, TaskService(db), db, task_id)
    clip = await ClipRepository.get_clip_by_id(db, clip_id)
    if not clip or clip.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="Clip not found")
    return task, clip


async def _trigger(request: Request) -> str:
    """Optional ``{"trigger": ...}`` body: what the user is about to do, for analytics."""
    if "application/json" not in (request.headers.get("content-type") or "").lower():
        return "api"
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        return "api"
    value = payload.get("trigger") if isinstance(payload, dict) else None
    return value if isinstance(value, str) and value in TRIGGERS else "api"


@router.post("/{task_id}/clips/{clip_id}/full-render", status_code=202)
async def start_full_render(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Start the clip's full-quality render. A full-quality clip answers ``ready``."""
    task, clip = await _task_and_clip(request, db, task_id, clip_id)
    trigger = await _trigger(request)
    try:
        return await request_full_render(
            db, clip, queue=render_queue(request), trigger=trigger,
            user_id=request_user_id(request) or task.get("user_id"),
        )
    except FullRenderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None


@router.get("/{task_id}/clips/{clip_id}/full-render")
async def get_full_render(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Progress of the clip's full-quality render."""
    _task, clip = await _task_and_clip(request, db, task_id, clip_id)
    return serialize_state(clip)


@router.post("/{task_id}/full-render", status_code=202)
async def start_task_full_render(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Start the full-quality render of every preview clip in the task ("Download all")."""
    task = await _require_task_reader(request, TaskService(db), db, task_id)
    trigger = await _trigger(request)
    clips = await ClipRepository.get_clips_by_task(db, task_id)
    queue = render_queue(request)
    try:
        for clip in clips:
            if is_preview(clip):
                await request_full_render(
                    db, {**clip, "task_id": task_id}, queue=queue, trigger=trigger,
                    user_id=request_user_id(request) or task.get("user_id"),
                )
    except FullRenderUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from None
    return summarize(await ClipRepository.get_clips_by_task(db, task_id))


@router.get("/{task_id}/full-render")
async def get_task_full_render(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Progress of every clip's full-quality render."""
    await _require_task_reader(request, TaskService(db), db, task_id)
    return summarize(await ClipRepository.get_clips_by_task(db, task_id))
