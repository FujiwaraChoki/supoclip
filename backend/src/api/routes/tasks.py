"""
Task API routes using refactored architecture.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from pathlib import Path
import json
import logging
from typing import Dict, Any, Optional
import inspect
import re
import secrets

from ...database import get_db
from ...database import AsyncSessionLocal
from ...services.task_service import TaskService
from ...services.billing_service import BillingService, BillingLimitExceeded
from ...auth_headers import resolve_authenticated_user_id
from ...workers.job_queue import JobQueue
from ...workers.progress import ProgressTracker
from ...config import get_config
from ...font_registry import is_font_accessible
from ...clip_cleanup import normalize_clip_cleanup_settings
from ...video_utils import VALID_OUTPUT_FORMATS
from ...caption_templates import HOOK_POSITIONS, HOOK_ANIMATIONS, HOOK_TYPES, get_hook_options
from ...admin_auth import require_admin_user
import redis.asyncio as redis
from ...clip_editor import export_with_preset, EXPORT_PRESETS

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["tasks"])


def _normalize_font_size(value: Any, default: int = 24) -> Optional[int]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(12, min(72, parsed))


def _normalize_font_color(value: Any, default: str = "#FFFFFF") -> Optional[str]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, str) and re.match(r"^#[0-9A-Fa-f]{6}$", value):
        return value.upper()
    return default


def _normalize_font_family(value: Any) -> Optional[str]:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


_HOOK_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def _normalize_hook_hex_color(value: Any) -> Optional[str]:
    if isinstance(value, str) and _HOOK_HEX_RE.match(value.strip()):
        return value.strip().upper()
    return None


def _normalize_hook_style(value: Any) -> Optional[Dict[str, Any]]:
    """Whitelist and clamp a per-task hook-title styling payload.

    Unknown keys are dropped and invalid values fall back to None (meaning
    "inherit from the caption template"), so a malformed request can never
    produce a broken or unexpected style rather than erroring out.
    """
    if not isinstance(value, dict):
        return None

    style: Dict[str, Any] = {}

    hook_font_family = _normalize_font_family(value.get("hook_font_family"))
    if hook_font_family:
        style["hook_font_family"] = hook_font_family

    font_size_scale = value.get("hook_font_size_scale")
    if isinstance(font_size_scale, (int, float)):
        style["hook_font_size_scale"] = max(0.4, min(1.5, float(font_size_scale)))

    for key in ("hook_font_color", "hook_background_color", "hook_stroke_color", "hook_highlight_color"):
        color = _normalize_hook_hex_color(value.get(key))
        if color:
            style[key] = color

    stroke_width = value.get("hook_stroke_width")
    if isinstance(stroke_width, (int, float)):
        style["hook_stroke_width"] = max(0, min(10, int(stroke_width)))

    position = value.get("hook_position")
    if isinstance(position, str) and position.strip().lower() in HOOK_POSITIONS:
        style["hook_position"] = position.strip().lower()

    duration = value.get("hook_duration_seconds")
    if isinstance(duration, (int, float)):
        style["hook_duration_seconds"] = max(1.5, min(8.0, float(duration)))

    animation = value.get("hook_animation")
    if isinstance(animation, str) and animation.strip().lower() in HOOK_ANIMATIONS:
        style["hook_animation"] = animation.strip().lower()

    shadow = value.get("hook_shadow")
    if isinstance(shadow, bool):
        style["hook_shadow"] = shadow

    sfx_name = value.get("hook_sfx")
    if isinstance(sfx_name, str) and sfx_name.strip():
        from ...video_utils import find_sfx_path

        if find_sfx_path(sfx_name.strip()):
            style["hook_sfx"] = Path(sfx_name.strip()).name

    return style or None


_SOCIAL_OVERLAY_TEXT_FIELDS = ("username", "likes", "comments", "followers")


def _normalize_social_overlay(value: Any) -> Optional[Dict[str, Any]]:
    """Whitelist and clamp a per-task fake-social-overlay payload.

    Purely cosmetic, user-typed placeholder text — never validated against any
    real account. Text fields are length-capped defensively.
    """
    if not isinstance(value, dict):
        return None

    overlay: Dict[str, Any] = {}

    enabled = value.get("enabled")
    if isinstance(enabled, bool):
        overlay["enabled"] = enabled

    for key in _SOCIAL_OVERLAY_TEXT_FIELDS:
        text_value = value.get(key)
        if isinstance(text_value, str) and text_value.strip():
            overlay[key] = text_value.strip()[:40]

    verified = value.get("verified")
    if isinstance(verified, bool):
        overlay["verified"] = verified

    return overlay or None


_BROLL_TIMING_KEYS = ("max_insertions", "min_gap_seconds")


def _normalize_broll_settings(value: Any) -> Optional[Dict[str, Any]]:
    """Whitelist and clamp per-task B-roll timing controls."""
    if not isinstance(value, dict):
        return None

    settings: Dict[str, Any] = {}

    enabled = value.get("enabled")
    if isinstance(enabled, bool):
        settings["enabled"] = enabled

    max_insertions = value.get("max_insertions")
    if isinstance(max_insertions, (int, float)):
        settings["max_insertions"] = max(1, min(6, int(max_insertions)))

    min_gap_seconds = value.get("min_gap_seconds")
    if isinstance(min_gap_seconds, (int, float)):
        settings["min_gap_seconds"] = max(2.0, min(30.0, float(min_gap_seconds)))

    return settings or None


def _normalize_target_duration(value: Any) -> Optional[int]:
    """Whitelist an auto-trim duration preset (15/30/60s, or None for AI-selected)."""
    if isinstance(value, (int, float)) and int(value) in (15, 30, 60):
        return int(value)
    return None


def _normalize_max_clips(value: Any) -> Optional[int]:
    """Clamp a user-requested clip count, or None to use the global default."""
    if isinstance(value, (int, float)):
        return max(1, min(20, int(value)))
    return None


async def _get_user_id_from_headers(request: Request, db: AsyncSession) -> str:
    """Resolve the authenticated user ID from an API key or signed frontend headers."""
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


TASK_SOURCE_METADATA_TTL_SECONDS = 60 * 60 * 24 * 180  # 180 days, refreshed on read


async def _load_task_source_metadata(task_id: str) -> Dict[str, Any]:
    runtime_config = get_config()
    redis_client = redis.Redis(
        host=runtime_config.redis_host,
        port=runtime_config.redis_port,
        password=runtime_config.redis_password,
        decode_responses=True,
    )
    try:
        key = f"task_source:{task_id}"
        payload = await redis_client.get(key)
        if payload:
            # Sliding expiration: a project the user keeps coming back to
            # never expires just from the calendar, only from real inactivity.
            await redis_client.expire(key, TASK_SOURCE_METADATA_TTL_SECONDS)
    except Exception as exc:
        logger.warning("Unable to load task source metadata for %s: %s", task_id, exc)
        return {}
    finally:
        await redis_client.aclose()

    if not payload:
        return {}

    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        return {}


async def _save_task_source_metadata(task_id: str, payload: Dict[str, Any]) -> None:
    runtime_config = get_config()
    redis_client = redis.Redis(
        host=runtime_config.redis_host,
        port=runtime_config.redis_port,
        password=runtime_config.redis_password,
        decode_responses=True,
    )
    try:
        # Long, sliding TTL (refreshed on every read too — see
        # _load_task_source_metadata) so a project a user comes back to
        # occasionally doesn't silently lose its hook/cleanup/social/duration
        # settings just from sitting idle between visits.
        await redis_client.set(
            f"task_source:{task_id}",
            json.dumps(payload),
            ex=TASK_SOURCE_METADATA_TTL_SECONDS,
        )
    except Exception as exc:
        logger.warning("Unable to save task source metadata for %s: %s", task_id, exc)
    finally:
        await redis_client.aclose()


def _merge_task_source_metadata(
    existing: Dict[str, Any] | None,
    *,
    source_url: Any = None,
    source_type: Any = None,
    output_format: Any = None,
    add_subtitles: Any = None,
    cleanup_settings: Dict[str, Any] | None = None,
    hook_style: Dict[str, Any] | None = None,
    social_overlay: Dict[str, Any] | None = None,
    broll_settings: Dict[str, Any] | None = None,
    target_duration_seconds: Any = None,
    max_clips: Any = None,
) -> Dict[str, Any]:
    merged = dict(existing or {})

    if isinstance(source_url, str) and source_url:
        merged["url"] = source_url
    if isinstance(source_type, str) and source_type:
        merged["source_type"] = source_type
    if output_format in VALID_OUTPUT_FORMATS:
        merged["output_format"] = output_format
    if isinstance(add_subtitles, bool):
        merged["add_subtitles"] = add_subtitles
    if cleanup_settings:
        merged.update(cleanup_settings)
    if hook_style is not None:
        merged["hook_style"] = hook_style or None
    if social_overlay is not None:
        merged["social_overlay"] = social_overlay or None
    if broll_settings is not None:
        merged["broll_settings"] = broll_settings or None
    if target_duration_seconds is not None:
        merged["target_duration_seconds"] = target_duration_seconds or None
    if max_clips is not None:
        merged["max_clips"] = max_clips or None

    return merged


async def _require_task_owner(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    """Ensure authenticated user owns the task."""
    user_id = await _get_user_id_from_headers(request, db)

    task = await task_service.task_repo.get_task_by_id(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this task")

    return task


PUBLIC_TASK_FIELDS = {
    "source_title",
    "source_type",
    "status",
    "clips_count",
    "created_at",
    "updated_at",
}
PUBLIC_CLIP_FIELDS = {
    "id",
    "filename",
    "start_time",
    "end_time",
    "duration",
    "text",
    "relevance_score",
    "reasoning",
    "clip_order",
    "created_at",
    "virality_score",
    "hook_score",
    "engagement_score",
    "value_score",
    "shareability_score",
    "hook_type",
    "hook_title",
}


def _build_public_task(task: Dict[str, Any], share_token: str) -> Dict[str, Any]:
    """Return only fields intended for anyone holding the share URL."""
    public_task = {key: task.get(key) for key in PUBLIC_TASK_FIELDS}
    public_task["clips"] = []
    for clip in task.get("clips", []):
        public_clip = {key: clip.get(key) for key in PUBLIC_CLIP_FIELDS}
        public_clip["video_url"] = (
            f"/tasks/shared/{share_token}/clips/{clip['id']}/file"
        )
        public_task["clips"].append(public_clip)
    return public_task


@router.get("/")
async def list_tasks(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
):
    """
    Get all tasks for the authenticated user.

    Default limit stays 50 for callers that don't ask for more, but the
    /list page's "select all" needs to actually see everything selectable —
    it explicitly requests the max (500) so select-all-and-delete can't
    silently miss tasks past whatever the default page size happens to be.
    """
    user_id = await _get_user_id_from_headers(request, db)

    try:
        task_service = TaskService(db)
        tasks = await task_service.get_user_tasks(user_id, limit)

        return {"tasks": tasks, "total": len(tasks)}

    except Exception as e:
        logger.error(f"Error retrieving user tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving tasks: {str(e)}")


@router.get("/system-status")
async def get_system_status(request: Request, db: AsyncSession = Depends(get_db)):
    """Compact operator-panel status for the home screen's status strip:
    queue depth / active jobs (from this user's own tasks — there's no
    separate multi-tenant queue to inspect in the local-first model), GPU
    encoder availability, and disk space on TEMP_DIR (where clips render
    to before they're served)."""
    import shutil

    from ...video_utils import detect_gpu_encoder

    user_id = await _get_user_id_from_headers(request, db)
    config = get_config()

    task_service = TaskService(db)
    counts = await task_service.get_task_status_counts(user_id)
    queue_depth = counts.get("queued", 0)
    processing_count = counts.get("processing", 0)
    active_jobs = queue_depth + processing_count

    try:
        disk_total, _, disk_free = shutil.disk_usage(config.temp_dir)
    except OSError:
        disk_total, disk_free = 0, 0

    return {
        "queue_depth": queue_depth,
        "processing_count": processing_count,
        "active_jobs": active_jobs,
        "gpu_enabled": config.gpu_acceleration_enabled,
        "gpu_available": detect_gpu_encoder() is not None,
        "disk_free_bytes": disk_free,
        "disk_total_bytes": disk_total,
    }


@router.post("/")
async def create_task(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Create a new task and enqueue it for processing.
    Returns task_id immediately.
    """
    data = await request.json()

    raw_source = data.get("source")
    user_id = await _get_user_id_from_headers(request, db)

    # Get font options
    font_options = data.get("font_options", {})
    font_family = _normalize_font_family(font_options.get("font_family"))
    font_size = _normalize_font_size(font_options.get("font_size"))
    font_color = _normalize_font_color(font_options.get("font_color"))
    caption_template = data.get("caption_template", "default")
    include_broll = data.get("include_broll", False)
    runtime_config = get_config()
    processing_mode = data.get(
        "processing_mode", runtime_config.default_processing_mode
    )
    if processing_mode not in {"fast", "balanced", "quality"}:
        processing_mode = runtime_config.default_processing_mode
    output_format = data.get("output_format", "vertical")
    if output_format not in VALID_OUTPUT_FORMATS:
        output_format = "vertical"
    add_subtitles = data.get("add_subtitles", True)
    if not isinstance(add_subtitles, bool):
        add_subtitles = True
    cleanup_settings = normalize_clip_cleanup_settings(
        data.get("cut_long_pauses"),
        data.get("pause_threshold_ms"),
        data.get("remove_filler_words"),
        data.get("filtered_words"),
        data.get("sensitivity"),
    )
    hook_style = _normalize_hook_style(data.get("hook_style"))
    social_overlay = _normalize_social_overlay(data.get("social_overlay"))
    broll_settings = _normalize_broll_settings(data.get("broll_settings"))
    target_duration_seconds = _normalize_target_duration(data.get("target_duration_seconds"))
    max_clips = _normalize_max_clips(data.get("max_clips"))
    if not raw_source or not raw_source.get("url"):
        raise HTTPException(status_code=400, detail="Source URL is required")

    try:
        billing_service = BillingService(db)
        await billing_service.assert_can_create_task(user_id)

        task_service = TaskService(db)

        # Create task
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
        )

        # Get source type for worker
        source_type = task_service.video_service.determine_source_type(
            raw_source["url"]
        )

        # Enqueue job for worker
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
            cleanup_settings,
            hook_style,
            social_overlay,
            target_duration_seconds,
            max_clips,
        )

        # Save source metadata for resume/retries in environments without sources.url column
        await _save_task_source_metadata(
            task_id,
            _merge_task_source_metadata(
                None,
                source_url=raw_source["url"],
                source_type=source_type,
                output_format=output_format,
                add_subtitles=add_subtitles,
                social_overlay=social_overlay,
                broll_settings=broll_settings,
                target_duration_seconds=target_duration_seconds,
                cleanup_settings=cleanup_settings,
                hook_style=hook_style,
                max_clips=max_clips,
            ),
        )

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
                "message": "Choose a paid plan to process videos.",
                "billing": e.summary,
            },
        )
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating task: {str(e)}")


@router.get("/billing/summary")
async def get_billing_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Get monetization status and current usage for authenticated user."""
    user_id = await _get_user_id_from_headers(request, db)

    try:
        billing_service = BillingService(db)
        summary = await billing_service.get_usage_summary(user_id)
        return summary
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error retrieving billing summary: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving billing summary: {str(e)}",
        )


@router.get("/hook-options")
async def get_hook_options_route():
    """List selectable hook types and animation styles for the hook editor UI."""
    return get_hook_options()


@router.get("/shared/{share_token}")
async def get_shared_task(share_token: str, db: AsyncSession = Depends(get_db)):
    """Get the read-only generation result associated with an opaque share token."""
    task_service = TaskService(db)
    task_id = await task_service.task_repo.get_shared_task_id(db, share_token)
    if not task_id:
        raise HTTPException(status_code=404, detail="Shared result not found")

    task = await task_service.get_task_with_clips(task_id)
    if not task or task.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Shared result not found")

    return _build_public_task(task, share_token)


@router.get("/shared/{share_token}/clips/{clip_id}/file")
async def get_shared_clip_file(
    share_token: str,
    clip_id: str,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve one shared clip only when the bearer share token is enabled."""
    task_service = TaskService(db)
    task_id = await task_service.task_repo.get_shared_task_id(db, share_token)
    if not task_id:
        raise HTTPException(status_code=404, detail="Shared result not found")

    clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
    if not clip or clip.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="Clip not found")

    clip_path = Path(clip["file_path"])
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    return FileResponse(
        path=str(clip_path),
        media_type="video/mp4",
        filename=clip["filename"],
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/trash")
async def list_trash(
    request: Request,
    db: AsyncSession = Depends(get_db),
    limit: int = Query(50, ge=1, le=500),
):
    """List the authenticated user's soft-deleted (trashed) tasks.

    Registered before `GET /{task_id}` so the literal "trash" path segment
    isn't swallowed by that param route.
    """
    user_id = await _get_user_id_from_headers(request, db)
    try:
        task_service = TaskService(db)
        tasks = await task_service.list_trash(user_id, limit)
        return {"tasks": tasks, "total": len(tasks)}
    except Exception as e:
        logger.error(f"Error retrieving trashed tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving trashed tasks: {str(e)}")


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


@router.post("/{task_id}/share")
async def share_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Create or re-enable a stable, read-only share URL for a completed task."""
    task_service = TaskService(db)
    task = await _require_task_owner(request, task_service, db, task_id)
    if task.get("status") != "completed":
        raise HTTPException(
            status_code=409, detail="Only completed generations can be shared"
        )

    share_token = await task_service.task_repo.enable_sharing(
        db, task_id, secrets.token_urlsafe(32)
    )
    if not share_token:
        raise HTTPException(status_code=404, detail="Task not found")

    return {
        "share_token": share_token,
        "share_path": f"/share/{share_token}",
    }


@router.delete("/{task_id}/share")
async def unshare_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Disable the public URL while preserving the private generation result."""
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)
    await task_service.task_repo.disable_sharing(db, task_id)
    return {"message": "Share link disabled"}


@router.get("/{task_id}/progress")
async def get_task_progress_sse(task_id: str, request: Request):
    """
    SSE endpoint for real-time progress updates.
    Streams progress updates as Server-Sent Events.
    """

    async with AsyncSessionLocal() as local_db:
        user_id = await _get_user_id_from_headers(request, local_db)
        task_service = TaskService(local_db)
        task = await task_service.task_repo.get_task_by_id(local_db, task_id)

    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.get("user_id") != user_id:
        raise HTTPException(status_code=403, detail="Not authorized for this task")

    async def event_generator():
        """Generate SSE events for task progress."""
        # Connect to Redis for real-time updates
        runtime_config = get_config()
        redis_client = redis.Redis(
            host=runtime_config.redis_host,
            port=runtime_config.redis_port,
            password=runtime_config.redis_password,
            decode_responses=True,
        )

        # Prefer the last Redis progress snapshot (carries `stage`) over the
        # DB row, which doesn't persist stage — falls back to the DB values
        # if Redis has nothing cached yet (e.g. right after enqueue).
        cached_progress = await ProgressTracker(redis_client, task_id).get()
        yield {
            "event": "status",
            "data": json.dumps(
                {
                    "task_id": task_id,
                    "status": task.get("status"),
                    "progress": (cached_progress or {}).get("progress", task.get("progress", 0)),
                    "message": (cached_progress or {}).get("message", task.get("progress_message", "")),
                    "stage": (cached_progress or {}).get("stage"),
                }
            ),
        }

        # If task is already completed, error, or cancelled, close connection
        if task.get("status") in ["completed", "error", "cancelled"]:
            await redis_client.close()
            yield {"event": "close", "data": json.dumps({"status": task.get("status")})}
            return

        try:
            # Subscribe to progress updates
            async for progress_data in ProgressTracker.subscribe_to_progress(
                redis_client, task_id
            ):
                event_type = progress_data.get("event_type", "progress")
                yield {"event": event_type, "data": json.dumps(progress_data)}

                # Close connection if task is done
                if progress_data.get("status") in ["completed", "error", "cancelled"]:
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
    """Update task details (title)."""
    try:
        data = await request.json()
        title = data.get("title")

        if not title:
            raise HTTPException(status_code=400, detail="Title is required")

        task_service = TaskService(db)

        task = await _require_task_owner(request, task_service, db, task_id)

        if not task.get("source_id"):
            raise HTTPException(
                status_code=400,
                detail="This task has no source to rename (e.g. a ranking project)",
            )

        # Update source title
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
        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)

        # Get task to verify ownership
        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this task"
            )

        # Soft-delete: moves the task to trash, clips stay attached
        await task_service.delete_task(task_id)

        return {"message": "Task moved to trash"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting task: {str(e)}")


@router.post("/{task_id}/restore")
async def restore_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Restore a task out of trash."""
    try:
        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)

        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to restore this task"
            )

        restored = await task_service.restore_task(task_id)
        if not restored:
            raise HTTPException(status_code=400, detail="Task is not in trash")

        return {"message": "Task restored successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error restoring task: {e}")
        raise HTTPException(status_code=500, detail=f"Error restoring task: {str(e)}")


@router.delete("/{task_id}/purge")
async def purge_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Permanently delete a trashed task and its clip files. Cannot be undone."""
    try:
        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)

        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to purge this task"
            )

        await task_service.purge_task(task_id)

        return {"message": "Task permanently deleted"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error purging task: {e}")
        raise HTTPException(status_code=500, detail=f"Error purging task: {str(e)}")


@router.delete("/{task_id}/clips/{clip_id}")
async def delete_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a specific clip."""
    try:
        user_id = await _get_user_id_from_headers(request, db)
        task_service = TaskService(db)

        # Verify task ownership
        task = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")

        if task["user_id"] != user_id:
            raise HTTPException(
                status_code=403, detail="Not authorized to delete this clip"
            )

        # Delete the clip
        await task_service.clip_repo.delete_clip(db, clip_id)

        return {"message": "Clip deleted successfully"}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error deleting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting clip: {str(e)}")


@router.get("/{task_id}/clips/{clip_id}/file")
async def get_clip_file(
    task_id: str,
    clip_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve a clip file after verifying task ownership."""
    try:
        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        clip_path = Path(clip["file_path"])
        if not clip_path.exists():
            raise HTTPException(status_code=404, detail="Clip file not found")

        # Release the DB connection before streaming: FastAPI keeps a
        # `Depends(get_db)` session open for the full response lifetime, and a
        # FileResponse can take many seconds (large file, slow client, range
        # scrubbing). Holding it leaves the read-only transaction above as
        # "idle in transaction" in Postgres for the whole transfer, which
        # exhausts the connection pool under concurrent playback.
        await db.close()

        return FileResponse(
            path=str(clip_path),
            media_type="video/mp4",
            filename=clip["filename"],
            content_disposition_type="inline",
            headers={"Cache-Control": "private, no-store"},
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error serving clip file: {e}")
        raise HTTPException(status_code=500, detail=f"Error serving clip file: {str(e)}")


@router.patch("/{task_id}/clips/{clip_id}")
async def trim_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Trim clip boundaries and regenerate clip file."""
    try:
        payload = await request.json()
        start_offset = float(payload.get("start_offset", 0))
        end_offset = float(payload.get("end_offset", 0))

        if start_offset < 0 or end_offset < 0:
            raise HTTPException(status_code=400, detail="Offsets must be non-negative")

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, start_offset, end_offset
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
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Split a clip into two clips."""
    try:
        payload = await request.json()
        split_time = float(payload.get("split_time", 0))
        if split_time <= 0:
            raise HTTPException(
                status_code=400, detail="split_time must be greater than zero"
            )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.split_clip(task_id, clip_id, split_time)
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
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Merge multiple clips into one clip."""
    try:
        payload = await request.json()
        clip_ids = payload.get("clip_ids") or []
        if not isinstance(clip_ids, list):
            raise HTTPException(status_code=400, detail="clip_ids must be an array")

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.merge_clips(task_id, clip_ids)
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
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update clip caption text, timing style and highlighted words."""
    try:
        payload = await request.json()
        caption_text = str(payload.get("caption_text", "")).strip()
        position = str(payload.get("position", "bottom"))
        highlight_words = payload.get("highlight_words") or []
        if not isinstance(highlight_words, list):
            raise HTTPException(
                status_code=400, detail="highlight_words must be an array"
            )
        font_size = (
            _normalize_font_size(payload.get("font_size"))
            if "font_size" in payload
            else None
        )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.update_clip_captions(
            task_id,
            clip_id,
            caption_text,
            position,
            [str(word) for word in highlight_words],
            font_size,
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


@router.post("/{task_id}/clips/{clip_id}/hook-variants")
async def generate_clip_hook_variants(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Generate alternative hook title candidates for a clip, for A/B comparison."""
    try:
        payload: Dict[str, Any] = {}
        try:
            payload = await request.json()
        except Exception:
            payload = {}
        count = payload.get("count", 3)
        try:
            count = max(1, min(6, int(count)))
        except (TypeError, ValueError):
            count = 3

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        result = await task_service.generate_hook_variants_for_clip(task_id, clip_id, count)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error generating hook variants: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error generating hook variants: {str(e)}"
        )


@router.patch("/{task_id}/clips/{clip_id}/hook-variants/select")
async def select_clip_hook_variant(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Apply a generated hook variant (or custom text) as the clip's hook title, and re-render it."""
    try:
        payload = await request.json()
        variant_id = payload.get("variant_id")
        custom_text = payload.get("hook_title")
        hook_type = payload.get("hook_type")
        if not variant_id and not custom_text:
            raise HTTPException(
                status_code=400, detail="variant_id or hook_title is required"
            )
        if hook_type is not None and (
            not isinstance(hook_type, str) or hook_type.strip().lower() not in HOOK_TYPES
        ):
            raise HTTPException(status_code=400, detail="Invalid hook_type")

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.select_hook_variant(
            task_id,
            clip_id,
            variant_id=str(variant_id) if variant_id else None,
            custom_text=str(custom_text) if custom_text else None,
            hook_type=hook_type.strip().lower() if hook_type else None,
        )
        return {"clip": updated_clip}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error selecting hook variant: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error selecting hook variant: {str(e)}"
        )


@router.patch("/{task_id}/clips/{clip_id}/reactions")
async def update_clip_reactions(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Replace a clip's emoji reactions and re-render it with them burned in."""
    try:
        payload = await request.json()
        reactions = payload.get("reactions")
        if not isinstance(reactions, list):
            raise HTTPException(status_code=400, detail="reactions must be an array")

        from ...emoji_reactions import REACTION_ANIMATIONS

        normalized: list[Dict[str, Any]] = []
        for index, item in enumerate(reactions):
            if not isinstance(item, dict):
                raise HTTPException(
                    status_code=400, detail=f"reactions[{index}] must be an object"
                )
            emoji = str(item.get("emoji") or "").strip()
            if not emoji:
                raise HTTPException(
                    status_code=400, detail=f"reactions[{index}].emoji is required"
                )
            animation_style = str(item.get("animation_style") or "fade_pop").strip().lower()
            if animation_style not in REACTION_ANIMATIONS:
                raise HTTPException(
                    status_code=400,
                    detail=f"reactions[{index}].animation_style must be one of: {', '.join(REACTION_ANIMATIONS)}",
                )
            try:
                timestamp_seconds = max(0.0, float(item.get("timestamp_seconds", 0)))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail=f"reactions[{index}].timestamp_seconds must be a number"
                )
            try:
                duration_seconds = float(item.get("duration_seconds", 1.6))
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail=f"reactions[{index}].duration_seconds must be a number"
                )
            duration_seconds = max(0.2, duration_seconds)

            raw_position = item.get("position") or {}
            try:
                x_pct = min(max(float(raw_position.get("x_pct", 0.5)), 0.0), 1.0)
                y_pct = min(max(float(raw_position.get("y_pct", 0.3)), 0.0), 1.0)
            except (TypeError, ValueError):
                raise HTTPException(
                    status_code=400, detail=f"reactions[{index}].position must have numeric x_pct/y_pct"
                )

            normalized.append(
                {
                    "id": str(item.get("id") or secrets.token_hex(6)),
                    "emoji": emoji,
                    "timestamp_seconds": timestamp_seconds,
                    "animation_style": animation_style,
                    "duration_seconds": duration_seconds,
                    "position": {"x_pct": x_pct, "y_pct": y_pct},
                }
            )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.update_clip_reactions(task_id, clip_id, normalized)
        return {"clip": updated_clip}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating clip reactions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating clip reactions: {str(e)}"
        )


@router.post("/{task_id}/clips/{clip_id}/regenerate")
async def regenerate_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Regenerate a single clip after editing timing values."""
    try:
        payload = await request.json()
        start_offset = float(payload.get("start_offset", 0))
        end_offset = float(payload.get("end_offset", 0))

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, start_offset, end_offset
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


@router.post("/{task_id}/settings")
async def apply_task_settings(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update task-level styling settings and optionally apply to all existing clips."""
    try:
        payload = await request.json()
        font_family = _normalize_font_family(payload.get("font_family"))
        font_size = _normalize_font_size(payload.get("font_size"))
        font_color = _normalize_font_color(payload.get("font_color"))
        caption_template = payload.get("caption_template", "default")
        include_broll = bool(payload.get("include_broll", False))
        apply_to_existing = bool(payload.get("apply_to_existing", False))
        cleanup_settings = normalize_clip_cleanup_settings(
            payload.get("cut_long_pauses"),
            payload.get("pause_threshold_ms"),
            payload.get("remove_filler_words"),
            payload.get("filtered_words"),
            payload.get("sensitivity"),
        )
        hook_style = _normalize_hook_style(payload.get("hook_style"))
        social_overlay = _normalize_social_overlay(payload.get("social_overlay"))
        broll_settings = _normalize_broll_settings(payload.get("broll_settings"))

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task_record = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task_record:
            raise HTTPException(status_code=404, detail="Task not found")
        if font_family is not None and not is_font_accessible(
            font_family, task_record["user_id"]
        ):
            raise HTTPException(
                status_code=400, detail="Selected font is not available"
            )

        # Persist hook_style (and other metadata) before regenerating clips so
        # regenerate_all_clips_for_task picks up the new value when it re-reads
        # task_source:{task_id}.
        metadata = await _load_task_source_metadata(task_id)
        merged_metadata = _merge_task_source_metadata(
            metadata,
            source_url=metadata.get("url") or task_record.get("source_url"),
            source_type=metadata.get("source_type") or task_record.get("source_type"),
            output_format=metadata.get("output_format"),
            add_subtitles=(
                metadata["add_subtitles"]
                if isinstance(metadata.get("add_subtitles"), bool)
                else None
            ),
            cleanup_settings=cleanup_settings,
            hook_style=hook_style if "hook_style" in payload else metadata.get("hook_style"),
            social_overlay=(
                social_overlay if "social_overlay" in payload else metadata.get("social_overlay")
            ),
            broll_settings=(
                broll_settings if "broll_settings" in payload else metadata.get("broll_settings")
            ),
        )
        await _save_task_source_metadata(task_id, merged_metadata)

        task = await task_service.update_task_settings(
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
            apply_to_existing,
            cleanup_settings,
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


@router.get("/{task_id}/clips/{clip_id}/export")
async def export_clip(
    task_id: str,
    clip_id: str,
    request: Request,
    preset: str = "tiktok",
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Export clip with a social platform preset."""
    try:
        preset_name = preset.lower().strip()
        if preset_name not in EXPORT_PRESETS:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid preset. Use one of: {', '.join(EXPORT_PRESETS.keys())}",
            )

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        from pathlib import Path

        # Release the DB connection before the (potentially multi-minute) ffmpeg
        # re-encode and file streaming below — see get_clip_file for why holding
        # it here turns into "idle in transaction" connection-pool exhaustion.
        await db.close()

        runtime_config = get_config()
        output_path = export_with_preset(
            Path(clip["file_path"]),
            Path(runtime_config.temp_dir) / "exports",
            preset_name,
        )

        download_name = f"{Path(clip['filename']).stem}_{preset_name}.mp4"
        return FileResponse(
            path=str(output_path), media_type="video/mp4", filename=download_name
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error exporting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error exporting clip: {str(e)}")


@router.post("/{task_id}/cancel")
async def cancel_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Cancel an active queued or processing task."""
    try:
        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)

        if task.get("status") in ["completed", "error", "cancelled"]:
            return {"message": f"Task already in terminal state: {task.get('status')}"}

        runtime_config = get_config()
        redis_client = redis.Redis(
            host=runtime_config.redis_host,
            port=runtime_config.redis_port,
            password=runtime_config.redis_password,
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


@router.get("/metrics/performance")
async def get_performance_metrics(
    request: Request, db: AsyncSession = Depends(get_db)
):
    """Get aggregate processing performance metrics by mode."""
    try:
        await require_admin_user(request, db, get_config())
        task_service = TaskService(db)
        return await task_service.get_performance_metrics()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error loading performance metrics: {e}")
        raise HTTPException(status_code=500, detail=f"Error loading metrics: {str(e)}")


@router.post("/{task_id}/resume")
async def resume_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Resume a cancelled or errored task by enqueueing a new worker job."""
    try:
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
        add_subtitles = True

        metadata = await _load_task_source_metadata(task_id)
        if not source_url:
            source_url = metadata.get("url")
        if not source_type:
            source_type = metadata.get("source_type")
        of = metadata.get("output_format", output_format)
        if of in VALID_OUTPUT_FORMATS:
            output_format = of
        asub = metadata.get("add_subtitles", add_subtitles)
        if isinstance(asub, bool):
            add_subtitles = asub
        cleanup_settings = normalize_clip_cleanup_settings(
            metadata.get("cut_long_pauses"),
            metadata.get("pause_threshold_ms"),
            metadata.get("remove_filler_words"),
            metadata.get("filtered_words"),
            metadata.get("sensitivity"),
        )
        # These were previously loaded into `metadata` but never forwarded to
        # the re-enqueued job, so resuming a task silently dropped hook
        # styling, social overlay, target duration, and clip-count settings.
        hook_style = _normalize_hook_style(metadata.get("hook_style"))
        social_overlay = _normalize_social_overlay(metadata.get("social_overlay"))
        target_duration_seconds = _normalize_target_duration(
            metadata.get("target_duration_seconds")
        )
        max_clips = _normalize_max_clips(metadata.get("max_clips"))

        if not source_url or not source_type:
            raise HTTPException(status_code=400, detail="Task source URL is missing")

        runtime_config = get_config()
        redis_client = redis.Redis(
            host=runtime_config.redis_host,
            port=runtime_config.redis_port,
            password=runtime_config.redis_password,
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

        processing_mode = (
            task.get("processing_mode") or runtime_config.default_processing_mode
        )

        job_id = await JobQueue.enqueue_processing_job(
            "process_video_task",
            processing_mode,
            task_id,
            source_url,
            source_type,
            task["user_id"],
            task.get("font_family"),
            task.get("font_size"),
            task.get("font_color"),
            task.get("caption_template") or "default",
            processing_mode,
            output_format,
            add_subtitles,
            cleanup_settings,
            hook_style,
            social_overlay,
            target_duration_seconds,
            max_clips,
        )

        return {"message": "Task resumed", "job_id": job_id}
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error resuming task: {e}")
        raise HTTPException(status_code=500, detail=f"Error resuming task: {str(e)}")


@router.get("/dead-letter/list")
async def list_dead_letter_tasks():
    """List tasks that exhausted retries and landed in dead-letter store."""
    runtime_config = get_config()
    redis_client = redis.Redis(
        host=runtime_config.redis_host,
        port=runtime_config.redis_port,
        password=runtime_config.redis_password,
        decode_responses=True,
    )
    try:
        ids_result = redis_client.smembers("tasks:dead_letter")
        ids = await ids_result if inspect.isawaitable(ids_result) else ids_result
        items = []
        safe_ids = list(ids or [])
        for task_id in sorted(safe_ids):
            payload = await redis_client.get(f"dead_letter:{task_id}")
            if payload:
                try:
                    items.append(json.loads(payload))
                except json.JSONDecodeError:
                    items.append({"task_id": task_id, "raw": payload})

        return {"total": len(items), "tasks": items}
    finally:
        await redis_client.close()
