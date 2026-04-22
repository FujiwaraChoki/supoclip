"""Task lifecycle endpoints: create, read, update, delete, progress, cancel, resume."""

from __future__ import annotations

import json
import logging

import redis.asyncio as redis
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from ....auth_headers import USER_ID_HEADER, get_signed_user_id
from ....config import get_config
from ....database import AsyncSessionLocal, get_db
from ....font_registry import is_font_accessible
from ....services.billing_service import BillingLimitExceeded, BillingService
from ....services.task_service import TaskService
from ....workers.job_queue import JobQueue
from ....workers.progress import ProgressTracker
from ._helpers import (
    _get_user_id_from_headers,
    _require_task_owner,
)
from .schemas import CreateTaskRequest, TaskSettingsRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
async def list_tasks(
    request: Request, db: AsyncSession = Depends(get_db), limit: int = 50
):
    """Get all tasks for the authenticated user."""
    user_id = _get_user_id_from_headers(request)

    try:
        task_service = TaskService(db)
        tasks = await task_service.get_user_tasks(user_id, limit)
        return {"tasks": tasks, "total": len(tasks)}
    except Exception as e:
        logger.error(f"Error retrieving user tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving tasks: {str(e)}")


@router.post("/")
async def create_task(
    body: CreateTaskRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Create a new task and enqueue it for processing; returns task_id immediately."""
    config = get_config()
    if config.monetization_enabled:
        user_id = get_signed_user_id(request, config)
    else:
        user_id = request.headers.get("user_id") or request.headers.get(USER_ID_HEADER)
    if not user_id:
        raise HTTPException(status_code=401, detail="User authentication required")

    raw_source = {"url": body.source.url, "title": body.source.title}
    font_family = body.font_options.font_family
    font_size = body.font_options.font_size
    font_color = body.font_options.font_color
    stroke_color = body.font_options.stroke_color
    stroke_width = body.font_options.stroke_width
    subtitle_bold = body.font_options.bold
    subtitle_italic = body.font_options.italic
    subtitle_underline = body.font_options.underline
    caption_template = body.caption_template
    include_broll = body.include_broll
    processing_mode = body.processing_mode or config.default_processing_mode
    output_format = body.output_format
    add_subtitles = body.add_subtitles
    # CapCut edit mode ships the clip into CapCut with an editable text track;
    # burning subtitles or B-roll into the mp4 would defeat the purpose.
    if output_format == "capcut":
        add_subtitles = False
        include_broll = False
    avoid_original_subtitle = body.avoid_original_subtitle

    try:
        billing_service = BillingService(db)
        await billing_service.assert_can_create_task(user_id)

        task_service = TaskService(db)

        task_id = await task_service.create_task_with_source(
            user_id=user_id,
            url=raw_source["url"],
            title=raw_source.get("title"),
            font_family=font_family,
            font_size=font_size,
            font_color=font_color,
            caption_template=caption_template,
            include_broll=include_broll,
            processing_mode=processing_mode,
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            subtitle_bold=subtitle_bold,
            subtitle_italic=subtitle_italic,
            subtitle_underline=subtitle_underline,
        )

        source_type = task_service.video_service.determine_source_type(
            raw_source["url"]
        )

        queue_adapter = getattr(request.app.state, "queue_adapter", JobQueue)
        job_id = await queue_adapter.enqueue_processing_job(
            "process_video_task",
            processing_mode,
            task_id,
            raw_source["url"],
            source_type,
            user_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            processing_mode,
            output_format,
            add_subtitles,
            stroke_color=stroke_color,
            stroke_width=stroke_width,
            bold=subtitle_bold,
            italic=subtitle_italic,
            underline=subtitle_underline,
            avoid_original_subtitle=avoid_original_subtitle,
        )

        redis_client = redis.Redis(
            host=config.redis_host,
            port=config.redis_port,
            password=config.redis_password,
            decode_responses=True,
        )
        try:
            await redis_client.set(
                f"task_source:{task_id}",
                json.dumps(
                    {
                        "url": raw_source["url"],
                        "source_type": source_type,
                        "output_format": output_format,
                        "add_subtitles": add_subtitles,
                        "avoid_original_subtitle": avoid_original_subtitle,
                    }
                ),
                ex=60 * 60 * 24 * 7,
            )
        finally:
            await redis_client.close()

        logger.info(f"Task {task_id} created and job {job_id} enqueued")

        return {
            "task_id": task_id,
            "job_id": job_id,
            "message": "Task created and queued for processing",
        }

    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BillingLimitExceeded as e:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "SUBSCRIPTION_REQUIRED",
                "message": "Active subscription required to create tasks.",
                "billing": e.summary,
            },
        )
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating task: {str(e)}")


@router.get("/{task_id}")
async def get_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Get task details."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task = await task_service.get_task_with_clips(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return task
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving task: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving task: {str(e)}")


@router.get("/{task_id}/clips")
async def get_task_clips(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Get all clips for a task."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task = await task_service.get_task_with_clips(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        return {
            "task_id": task_id,
            "clips": task.get("clips", []),
            "total_clips": len(task.get("clips", [])),
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrieving clips: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving clips: {str(e)}")


@router.get("/{task_id}/progress")
async def get_task_progress_sse(task_id: str, request: Request):
    """SSE stream of real-time progress updates."""
    user_id = _get_user_id_from_headers(request)

    async with AsyncSessionLocal() as local_db:
        task_service = TaskService(local_db)
        task = await task_service.task_repo.get_task_by_id(local_db, task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this task")

    async def event_generator():
        yield {
            "event": "status",
            "data": json.dumps(
                {
                    "task_id": task_id,
                    "status": task.get("status"),
                    "progress": task.get("progress", 0),
                    "message": task.get("progress_message", ""),
                }
            ),
        }

        if task.get("status") in ["completed", "error"]:
            yield {"event": "close", "data": json.dumps({"status": task.get("status")})}
            return

        runtime_config = get_config()
        redis_client = redis.Redis(
            host=runtime_config.redis_host,
            port=runtime_config.redis_port,
            password=runtime_config.redis_password,
            decode_responses=True,
        )

        try:
            async for progress_data in ProgressTracker.subscribe_to_progress(
                redis_client, task_id
            ):
                event_type = progress_data.get("event_type", "progress")
                yield {"event": event_type, "data": json.dumps(progress_data)}

                if progress_data.get("status") in ["completed", "error"]:
                    yield {
                        "event": "close",
                        "data": json.dumps({"status": progress_data.get("status")}),
                    }
                    break
        finally:
            await redis_client.close()

    return EventSourceResponse(event_generator())


@router.patch("/{task_id}")
async def update_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update task title."""
    try:
        data = await request.json()
        title = data.get("title")
        if not title:
            raise HTTPException(status_code=400, detail="Title is required")

        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        await task_service.source_repo.update_source_title(db, task["source_id"], title)
        return {"message": "Task updated successfully", "task_id": task_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task: {e}")
        raise HTTPException(status_code=500, detail=f"Error updating task: {str(e)}")


@router.delete("/{task_id}")
async def delete_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a task and all its associated clips."""
    try:
        user_id = _get_user_id_from_headers(request)
        task_service = TaskService(db)
        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this task"
            )

        await task_service.delete_task(task_id)
        return {"message": "Task deleted successfully"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting task: {str(e)}")


@router.post("/{task_id}/settings")
async def apply_task_settings(
    task_id: str,
    body: TaskSettingsRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    """Update task-level styling settings and optionally apply to all existing clips."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task_record = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task_record:
            raise HTTPException(status_code=404, detail="Task not found")
        if not is_font_accessible(body.font_family, task_record["user_id"]):
            raise HTTPException(
                status_code=400, detail="Selected font is not available"
            )
        task = await task_service.update_task_settings(
            task_id,
            body.font_family,
            body.font_size,
            body.font_color,
            body.caption_template,
            body.include_broll,
            body.apply_to_existing,
        )
        return {"task": task, "message": "Task settings updated"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task settings: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating task settings: {str(e)}"
        )


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Cancel an active queued or processing task."""
    try:
        config = get_config()
        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)

        if task.get("status") in ["completed", "error", "cancelled"]:
            return {"message": f"Task already in terminal state: {task.get('status')}"}

        redis_client = redis.Redis(
            host=config.redis_host,
            port=config.redis_port,
            password=config.redis_password,
            decode_responses=True,
        )
        try:
            await redis_client.setex(f"task_cancel:{task_id}", 3600, "1")
        finally:
            await redis_client.close()

        await task_service.task_repo.update_task_status(
            db,
            task_id,
            "cancelled",
            progress=0,
            progress_message="Cancelled by user",
        )

        return {"message": "Task cancellation requested"}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error cancelling task: {e}")
        raise HTTPException(status_code=500, detail=f"Error cancelling task: {str(e)}")


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Resume a cancelled or errored task by enqueueing a new worker job."""
    try:
        config = get_config()
        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)

        if task.get("status") not in ["cancelled", "error", "queued"]:
            raise HTTPException(
                status_code=400,
                detail="Only cancelled/error/queued tasks can be resumed",
            )

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        output_format = "vertical"
        avoid_original_subtitle = "none"
        add_subtitles = True

        redis_client = redis.Redis(
            host=config.redis_host,
            port=config.redis_port,
            password=config.redis_password,
            decode_responses=True,
        )
        try:
            source_payload = await redis_client.get(f"task_source:{task_id}")
            if source_payload:
                parsed = json.loads(source_payload)
                if not source_url:
                    source_url = parsed.get("url")
                if not source_type:
                    source_type = parsed.get("source_type")
                of = parsed.get("output_format", output_format)
                if of in ("vertical", "fit", "original", "capcut"):
                    output_format = of
                asub = parsed.get("add_subtitles", add_subtitles)
                if isinstance(asub, bool):
                    add_subtitles = asub
                aos = parsed.get("avoid_original_subtitle", avoid_original_subtitle)
                if aos in ("none", "bottom", "top"):
                    avoid_original_subtitle = aos
        finally:
            await redis_client.close()

        # Mirror the create-path enforcement: capcut mode must never burn in
        # subtitles/B-roll, those land as editable tracks instead.
        if output_format == "capcut":
            add_subtitles = False

        if not source_url or not source_type:
            raise HTTPException(status_code=400, detail="Task source URL is missing")

        redis_client = redis.Redis(
            host=config.redis_host,
            port=config.redis_port,
            password=config.redis_password,
            decode_responses=True,
        )
        try:
            await redis_client.delete(f"task_cancel:{task_id}")
        finally:
            await redis_client.close()

        await task_service.task_repo.update_task_status(
            db,
            task_id,
            "queued",
            progress=0,
            progress_message="Re-queued by user",
        )

        processing_mode = task.get("processing_mode") or config.default_processing_mode

        job_id = await JobQueue.enqueue_processing_job(
            "process_video_task",
            processing_mode,
            task_id,
            source_url,
            source_type,
            task["user_id"],
            task.get("font_family") or "TikTokSans-Regular",
            task.get("font_size") or 24,
            task.get("font_color") or "#FFFFFF",
            task.get("caption_template") or "default",
            processing_mode,
            output_format,
            add_subtitles,
            avoid_original_subtitle=avoid_original_subtitle,
        )

        return {"message": "Task resumed", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming task: {e}")
        raise HTTPException(status_code=500, detail=f"Error resuming task: {str(e)}")
