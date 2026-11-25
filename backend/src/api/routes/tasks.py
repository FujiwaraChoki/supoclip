"""
Task API routes using refactored architecture.
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, StreamingResponse
from starlette.background import BackgroundTask
from ...media_storage import MediaStorage, STORAGE_ERRORS, is_s3, media_work
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse
from pathlib import Path
from contextlib import aclosing
import json
import logging
from typing import Dict, Any, Optional
import inspect
import re
import secrets
import subprocess
import math
import asyncio
from datetime import datetime, timezone
import hashlib
import time
from ...utils.async_helpers import run_in_thread
from ... import clip_archive

from ...repositories.generation_allowance_guard import generation_allowance_guard, GenerationAllowanceBusy
from ...repositories.task_run_guard import task_run_guard, TaskRunBusy
from ...repositories.task_repository import TaskRepository
from ...repositories.task_idempotency_repository import (
    TaskIdempotencyConflict,
    TaskIdempotencyReplay,
    find_task_for_key,
    claim_key,
)
from ...database import get_db
from ...database import AsyncSessionLocal
from ...services.task_service import TaskService
from ...services.fx_clip_jobs import stale_job_error
from ...services.video_service import VideoService
from ...media.upload_ingest import foreign_upload
from ...repositories.upload_repository import get_stored_upload
from ...services.billing_service import BillingService, BillingLimitExceeded, grants_paid_access, reduced_media_limits
from ...auth_headers import AuthContext, resolve_auth_context, resolve_authenticated_user_id
from ...repositories.team_repository import TeamRepository
from ...analytics import capture_clip_export, capture_event, request_client, source_kind
from ...feature_flags import DYNAMIC_EDITS, client_platform, is_enabled as feature_enabled
from ...edit_fx.contracts import EditStyle, GRADES, INTENSITIES, LISTED_FX_CAPTION_TEMPLATES, MOODS, is_fx_template
from ...edit_fx.jobs import fx_active
from ...edit_fx.settings import EditStyleError, edit_style_schema, parse_edit_style, read_edit_style
from ...services.clip_service import (
    FX_CLIP_JOB_STATUSES,
    FxClipJobInProgress,
    FxClipJobQueueError,
    FxClipJobRequest,
    QueuedClipJob,
    clip_has_fx,
    FX_CLIP_JOB_CLOSED,
    FX_CLIP_JOB_QUEUE_TIMEOUT_SECONDS,
    claim_fx_clip_job,
    enqueue_fx_clip_job,
    release_fx_clip_job_marker,
    write_fx_clip_job_state,
    read_fx_clip_job_state,
)
from ...source_identity import (
    duplicate_source_response,
    source_admission_lock,
    source_key_for_url,
)
from ...workers.job_queue import JobQueue
from ..pool_timeout import pool_busy
from ...workers.progress import ProgressTracker
from ...config import get_config
from ...youtube_utils import async_get_youtube_video_info, canonical_youtube_url, has_verified_duration
from ...youtube_proxy import YouTubeAgeRestrictedError, YouTubeGeoRestrictedError
from ...stream_source import (
    PREFLIGHT_DEADLINE_SECONDS as STREAM_PREFLIGHT_DEADLINE_SECONDS,
    StreamDurationUnavailableError,
    StreamSourceError,
    fetch_stream_info,
)
from ...font_registry import is_font_accessible
from ...clip_duration import normalize_clip_length, validate_clip_length
from ...clip_cleanup import (
    new_task_cleanup_settings,
    normalize_clip_cleanup_settings,
    updated_cleanup_settings,
)
from ...video_utils import VALID_OUTPUT_FORMATS
from ...admin_auth import require_admin_user, user_is_admin
from ...onboarding.preview import PREVIEW_REFUSALS, is_locked_preview, is_preview, preview_eligibility, preview_state
from ...source_submission import SourceAttempt
from ...workflows.contracts import workflow_spec_from_task
from ...workflows.errors import WorkflowRequestError
from ...workflows.options import (
    check_source_duration,
    parse_workflow_request,
    workflow_not_available,
    allowed_workflows,
    workflow_allowed_for,
    workflows_info,
)
import redis.asyncio as redis
from ...clip_editor import export_with_preset, EXPORT_PRESETS

from ...source_range import (
    METADATA_KEY as SOURCE_RANGE_KEY,
    SourceRangeError,
    parse_requested_range,
    resolve_source_range,
    video_too_long_detail,
    workflow_supports_range,
)
logger = logging.getLogger(__name__)
router = APIRouter(prefix="/tasks", tags=["tasks"])


DYNAMIC_EDITS_NOT_AVAILABLE_MESSAGE = "Dynamic edits aren't available on your account yet."


def _invalid_edit_style(exc: EditStyleError) -> HTTPException:
    """422 ``INVALID_EDIT_STYLE``, raised before any charge (same envelope as ``WORKFLOW_NOT_AVAILABLE``)."""
    return HTTPException(status_code=422, detail={"code": "INVALID_EDIT_STYLE", "message": str(exc)})


def _dynamic_edits_not_available() -> HTTPException:
    return HTTPException(
        status_code=403,
        detail={"code": "DYNAMIC_EDITS_NOT_AVAILABLE", "message": DYNAMIC_EDITS_NOT_AVAILABLE_MESSAGE},
    )


async def _dynamic_edits_enabled(db, request: Request, user_id: str) -> bool:
    return await feature_enabled(db, DYNAMIC_EDITS, platform=client_platform(request), user_id=user_id)


def _reactions_available() -> bool:
    """GIF/meme inserts can be offered: a provider and its key are configured."""
    from ...edit_fx.reactions import reactions_available

    return reactions_available()


class _DurationUnavailable(Exception):
    """Metadata came back, but with no positive length."""


def _duration_unavailable_error(platform: str) -> HTTPException:
    # Zero/missing length is what an in-progress livestream reports, but a
    # provider glitch can too, so this says "may be", never "is".
    return HTTPException(
        status_code=422,
        detail={
            "code": "VIDEO_DURATION_UNAVAILABLE",
            "message": (
                f"We couldn't verify this {platform} video's length. It may be a "
                "livestream that's still in progress, which isn't supported yet. "
                "If it's a finished video, try again shortly; otherwise wait until "
                "the stream ends and use the completed recording. No minutes were used."
            ),
        },
    )


async def _preflight_youtube_source(url: str, duration_limit: Optional[int]) -> Dict[str, Any]:
    """Check duration before creating a task or consuming a generation.

    ``duration_limit`` None skips the whole-video check (either self-hosted with
    no cap, or a part was picked and its length is checked once known).
    """
    # wait_for cancels the awaiting task when this elapses. A yt-dlp call already
    # running inside asyncio.to_thread can finish in the background.
    timeout_seconds = get_config().youtube_preflight_timeout_seconds
    try:
        metadata = await asyncio.wait_for(
            async_get_youtube_video_info(url, deadline=time.monotonic() + timeout_seconds),
            timeout=timeout_seconds,
        )
        if metadata is None:
            raise ValueError("No metadata provider answered")
        if metadata.get("age_restricted"):
            raise YouTubeAgeRestrictedError()
        if not has_verified_duration(metadata):
            raise _DurationUnavailable()
        duration = metadata["duration"]
    except _DurationUnavailable:
        raise _duration_unavailable_error("YouTube") from None
    except YouTubeGeoRestrictedError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "YOUTUBE_GEO_RESTRICTED",
                "message": f"{exc} No minutes were used.",
            },
        ) from None
    except YouTubeAgeRestrictedError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "YOUTUBE_AGE_RESTRICTED",
                "message": f"{exc} No minutes were used.",
            },
        ) from None
    except TimeoutError:
        logger.warning(
            "YouTube metadata preflight timed out after %s seconds",
            timeout_seconds,
        )
        raise HTTPException(
            status_code=503,
            detail={
                "code": "VIDEO_METADATA_TIMEOUT",
                "message": (
                    "Checking this YouTube video's length timed out. Please try again "
                    "shortly or upload the video file. No minutes were used."
                ),
            },
        ) from None
    except Exception:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "VIDEO_METADATA_UNAVAILABLE",
                "message": (
                    "We couldn't verify this video's length. Please try again shortly "
                    "or upload the video file. No minutes were used."
                ),
            },
        ) from None

    if duration_limit is not None and duration_limit > 0 and duration > duration_limit:
        raise HTTPException(status_code=422, detail=video_too_long_detail(duration, duration_limit))
    return metadata

async def _preflight_stream_source(url: str, source: str, duration_limit: Optional[int]) -> Dict[str, Any]:
    try:
        # One overall deadline: yt-dlp (proxy, direct) metadata, then the cheap
        # metadata actor only when both fail. The full-video actor never runs
        # here; the worker owns the rest of the fallback chain.
        metadata = await asyncio.wait_for(
            run_in_thread(fetch_stream_info, url, deadline_seconds=STREAM_PREFLIGHT_DEADLINE_SECONDS),
            timeout=STREAM_PREFLIGHT_DEADLINE_SECONDS + 5,
        )
        if not has_verified_duration(metadata):
            raise _DurationUnavailable()
        duration = metadata["duration"]
    except (StreamDurationUnavailableError, _DurationUnavailable):
        raise _duration_unavailable_error(source.title()) from None
    except StreamSourceError as exc:
        raise HTTPException(status_code=422, detail={"code": "VIDEO_UNAVAILABLE", "message": str(exc)}) from None
    except Exception:
        raise HTTPException(status_code=503, detail={"code": "VIDEO_METADATA_UNAVAILABLE", "message": "We couldn't fetch this video, try again later. No minutes were used."}) from None
    if duration_limit is not None and duration_limit > 0 and duration > duration_limit:
        raise HTTPException(status_code=422, detail=video_too_long_detail(duration, duration_limit, platform=source.title()))
    return metadata


def _stream_resolution_metadata(metadata: Dict[str, Any] | None) -> Dict[str, Any]:
    """Persist a preflight actor result so the worker does not pay for it again."""
    if not metadata or not metadata.get("media_url") or not str(metadata.get("provider", "")).startswith("apify_"):
        return {}
    return {
        "stream_resolution": {
            key: metadata.get(key)
            for key in ("provider", "id", "title", "duration", "media_url")
        }
        | {"resolved_at": time.time()}
    }


async def _preflight_upload_source(url: str, user_id: str, duration_limit: Optional[int], db=None) -> float:
    """Read an uploaded video's length so its minutes are charged up front."""
    reference = None
    if db is not None and isinstance(url, str) and url.startswith("upload://") and not url.startswith("upload://s3/"):
        row = await get_stored_upload(db, url)
        reference = row.storage_reference if row is not None else None
    try:
        if reference:
            duration = await run_in_thread(VideoService.probe_upload_duration, url, user_id, reference)
        else:
            duration = await run_in_thread(VideoService.probe_upload_duration, url, user_id)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("Invalid video duration")
    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VIDEO_PROBE_TIMEOUT",
                "message": (
                    "This video took too long to read. Re-export it with fast start "
                    "(\"optimize for web\") turned on, or upload a shorter or smaller "
                    "file. No minutes were used."
                ),
            },
        ) from None
    except Exception:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "VIDEO_METADATA_UNAVAILABLE",
                "message": (
                    "We couldn't read this video's length. Check that the file is a "
                    "playable video and upload it again. No minutes were used."
                ),
            },
        ) from None

    if duration_limit is not None and duration_limit > 0 and duration > duration_limit:
        raise HTTPException(status_code=422, detail=video_too_long_detail(duration, duration_limit, upload=True))
    return duration


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


async def _read_json_object(request: Request) -> Dict[str, Any]:
    try:
        payload = await request.json()
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="Invalid JSON body")
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Request body must be an object")
    return payload


def _finite_number(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{name} must be a finite number")
    if isinstance(value, bool) or not math.isfinite(number):
        raise ValueError(f"{name} must be a finite number")
    return number


async def _default_get_user_id_from_headers(request: Request, db: AsyncSession) -> str:
    """Resolve the authenticated user ID from an API key or signed frontend headers."""
    config = get_config()
    return await resolve_authenticated_user_id(request, db, config)


_get_user_id_from_headers = _default_get_user_id_from_headers


async def _get_auth_context(request: Request, db: AsyncSession) -> AuthContext:
    """Resolve authenticated user + verified team membership (if team header/key present)."""
    # Preserve test monkeypatching of `_get_user_id_from_headers`.
    if _get_user_id_from_headers is not _default_get_user_id_from_headers:
        user_id = await _get_user_id_from_headers(request, db)
        return AuthContext(user_id=user_id, team_id=None)
    config = get_config()
    return await resolve_auth_context(request, db, config)


async def _load_task_source_metadata(task_id: str, db: AsyncSession | None = None) -> Dict[str, Any]:
    if db is not None:
        metadata = await TaskRepository.load_source_metadata(db, task_id)
        if metadata:
            return metadata
    runtime_config = get_config()
    redis_client = redis.Redis(
        host=runtime_config.redis_host,
        port=runtime_config.redis_port,
        password=runtime_config.redis_password,
        decode_responses=True,
    )
    try:
        payload = await redis_client.get(f"task_source:{task_id}")
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


async def _save_task_source_metadata(
    task_id: str, payload: Dict[str, Any], db: AsyncSession | None = None
) -> None:
    if db is not None:
        await TaskRepository.save_source_metadata(db, task_id, payload)
    runtime_config = get_config()
    redis_client = redis.Redis(
        host=runtime_config.redis_host,
        port=runtime_config.redis_port,
        password=runtime_config.redis_password,
        decode_responses=True,
    )
    try:
        await redis_client.set(
            f"task_source:{task_id}",
            json.dumps(payload),
            ex=60 * 60 * 24 * 7,
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

    return merged


# Matches the admin dashboard's live queue: queued, processing, and the
# pre-queue pending default. Finished tasks stay visible only to their owner.
ADMIN_READABLE_TASK_STATUSES = {"queued", "processing", "pending"}


async def _load_task_for_user(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    auth_ctx = await _get_auth_context(request, db)
    task = await task_service.task_repo.get_task_by_id(db, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    return auth_ctx, task


async def _can_access_task(
    auth_ctx: AuthContext, db: AsyncSession, task: Dict[str, Any]
) -> bool:
    """Strict tenant isolation for personal and team-scoped tasks."""
    task_team_id = task.get("team_id")
    if task_team_id:
        # A team-scoped API key may only access tasks in its own team.
        if auth_ctx.via_api_key and auth_ctx.team_id != task_team_id:
            return False
        if auth_ctx.team_id == task_team_id:
            return True
        return await TeamRepository.is_member(db, task_team_id, auth_ctx.user_id)

    # Personal task: never accessible via a team-scoped API key, and only by owner.
    if auth_ctx.via_api_key and auth_ctx.team_id is not None:
        return False
    return task.get("user_id") == auth_ctx.user_id


async def _require_task_access(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
) -> tuple[AuthContext, Dict[str, Any]]:
    """The caller and a task they own or reach through its team. Returns (caller, task).

    In a team the caller is not always the task's creator; whatever is metered
    per member is charged to the caller.
    """
    auth_ctx, task = await _load_task_for_user(request, task_service, db, task_id)

    if not await _can_access_task(auth_ctx, db, task):
        raise HTTPException(status_code=403, detail="Not authorized for this task")
    await _refuse_locked_preview(request, db, auth_ctx.user_id, task)

    return auth_ctx, task


async def _require_task_owner(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    """Ensure authenticated user owns the task or actively belongs to its team."""
    _auth_ctx, task = await _require_task_access(request, task_service, db, task_id)
    return task


# What a locked free preview still allows without a plan: delete it, cancel it, or unlock it.
_PREVIEW_ALLOWED = re.compile(r"^/tasks/[^/]+/?(cancel|unlock)?/?$")


async def _refuse_locked_preview(request: Request, db: AsyncSession, user_id: str, task: dict) -> None:
    """A free preview's clip can be watched, downloaded and posted; editing and re-rendering
    it need a plan (onboarding/preview.py). Unlocked previews are ordinary projects."""
    if not is_locked_preview(task):
        return
    path = request.url.path
    if request.method == "DELETE" and _PREVIEW_ALLOWED.match(path):
        return
    if request.method == "POST" and path.rstrip("/").endswith(("/cancel", "/unlock")):
        return
    summary = await BillingService(db).get_usage_summary(user_id)
    if grants_paid_access(summary.get("plan"), summary.get("subscription_status"), summary.get("subscription_provider")):
        return
    raise HTTPException(
        status_code=402,
        detail={
            "code": "PREVIEW_LOCKED",
            "message": "Editing this clip comes with a plan. Pick one to unlock every clip in this video.",
            "billing": jsonable_encoder(summary),
        },
    )


async def _authorize_task_reader(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    """Owner/teammate, or an admin reading a task that is still active. Returns (user_id, task)."""
    auth_ctx, task = await _load_task_for_user(request, task_service, db, task_id)

    if await _can_access_task(auth_ctx, db, task):
        return auth_ctx.user_id, task
    if task.get("status") in ADMIN_READABLE_TASK_STATUSES and await user_is_admin(
        db, auth_ctx.user_id
    ):
        return auth_ctx.user_id, task

    raise HTTPException(status_code=403, detail="Not authorized for this task")


async def _require_task_reader(
    request: Request, task_service: TaskService, db: AsyncSession, task_id: str
):
    """Owner, or an admin reading a task that is still active."""
    _user_id, task = await _authorize_task_reader(request, task_service, db, task_id)
    return task


PUBLIC_TASK_FIELDS = {
    "source_title",
    "source_type",
    "status",
    "clips_count",
    "workflow",
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
    "part_number",
    "workflow_role",
}


def _pick(value: Any, keys: tuple[str, ...]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in keys if key in value}


def _picked_list(value: Any, keys: tuple[str, ...]) -> list[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [_pick(item, keys) for item in value if isinstance(item, dict)]


def _public_workflow_plan(plan: Any) -> Optional[Dict[str, Any]]:
    """The share-safe subset of ``tasks.workflow_plan``: titles and labels, no
    unit ids, ranges, summaries, captions or errors."""
    if not isinstance(plan, dict):
        return None
    public = _pick(plan, ("version", "workflow", "title"))
    if "notes" in plan:
        public["notes"] = _picked_list(plan["notes"], ("code", "params"))
    if "parts" in plan:
        public["parts"] = _picked_list(plan["parts"], ("part_number", "title"))
    if "points" in plan:
        public["points"] = _picked_list(plan["points"], ("label",))
    if "beats" in plan:
        public["beats"] = _picked_list(plan["beats"], ("role",))
    if "topic" in plan:
        public["topic"] = _pick(plan["topic"], ("query", "canonical", "coverage", "found"))
    return public


def _public_workflow_meta(meta: Any) -> Dict[str, Any]:
    """The share-safe subset of ``generated_clips.workflow_meta``."""
    public = _pick(meta, ("title", "ends_on", "part_count"))
    if isinstance(meta, dict) and "points" in meta:
        public["points"] = _picked_list(meta["points"], ("label", "kind", "range_index", "output_start"))
    if isinstance(meta, dict) and "beats" in meta:
        public["beats"] = _picked_list(meta["beats"], ("role", "range_index", "output_start"))
    return public


def _build_public_task(task: Dict[str, Any], share_token: str) -> Dict[str, Any]:
    """Return only fields intended for anyone holding the share URL."""
    public_task = {key: task.get(key) for key in PUBLIC_TASK_FIELDS}
    public_task["workflow_plan"] = _public_workflow_plan(task.get("workflow_plan"))
    public_task["clips"] = []
    for clip in task.get("clips", []):
        public_clip = {key: clip.get(key) for key in PUBLIC_CLIP_FIELDS}
        public_clip["workflow_meta"] = _public_workflow_meta(clip.get("workflow_meta"))
        public_clip["video_url"] = (
            f"/tasks/shared/{share_token}/clips/{clip['id']}/file"
        )
        public_task["clips"].append(public_clip)
    return public_task


@router.get("/")
async def list_tasks(
    request: Request, db: AsyncSession = Depends(get_db), limit: int = 50
):
    """
    Get all tasks for the authenticated user's active workspace (personal or team).
    """
    auth_ctx = await _get_auth_context(request, db)

    try:
        task_service = TaskService(db)
        tasks = await task_service.get_user_tasks(
            auth_ctx.user_id, limit, team_id=auth_ctx.team_id
        )

        return {"tasks": tasks, "total": len(tasks), "team_id": auth_ctx.team_id}

    except Exception as e:
        logger.error(f"Error retrieving user tasks: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrieving tasks: {str(e)}")


def _request_idempotency_key(request: Request) -> str:
    headers = getattr(request, "headers", None)
    getter = getattr(headers, "get", None)
    if getter is None:
        return ""
    raw = getter("idempotency-key")
    if raw is None or not str(raw).strip():
        return ""
    key = str(raw).strip()
    if not re.fullmatch(r"[A-Za-z0-9._:-]{8,200}", key):
        raise HTTPException(status_code=400, detail="Invalid idempotency key")
    return key


def _upload_request_key(request: Request, url: str, options: Dict[str, Any]) -> str | None:
    """Stable id for one upload plus the options that change the job.

    A missing header still matches an identical retry. YouTube and other
    links are not deduped here.
    """
    header = _request_idempotency_key(request)
    if not isinstance(url, str) or not url.startswith("upload://"):
        return None
    payload = json.dumps(options, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{header}\n{payload}".encode()).hexdigest()


def _queued_upload_task(task_id: str, job_id: str | None) -> Dict[str, Any]:
    return {
        "task_id": task_id,
        "job_id": job_id or task_id,
        "message": "Task already queued",
    }


def _task_idempotency(request: Request, options: Dict[str, Any], title: Any) -> tuple[str, str] | None:
    """``(key, request_hash)`` when the client sent an Idempotency-Key.

    The hash covers the normalized options and title, so a retry that only
    spells an option differently is still the same request.
    """
    key = _request_idempotency_key(request)
    if not key:
        return None
    payload = json.dumps({"options": options, "title": title}, sort_keys=True, separators=(",", ":"), default=str)
    return key, hashlib.sha256(payload.encode()).hexdigest()


async def _replay_task(db, user_id: str, idempotency: tuple[str, str]) -> Dict[str, Any] | None:
    """The response for a task this key already created, if any."""
    try:
        row = await find_task_for_key(db, user_id, *idempotency)
    except TaskIdempotencyConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={"code": "IDEMPOTENCY_KEY_REUSED", "message": str(exc)},
        ) from None
    return None if row is None else _queued_upload_task(row.id, row.job_id)


async def _bind_existing_task(db, user_id, idempotency, response):
    """Remember a successful deduplication even after the active task finishes."""
    if not idempotency:
        return response
    if await claim_key(db, user_id, *idempotency, response["task_id"]):
        await db.commit()
        return response
    # Another request already bound this key. Its hash must match.
    return await _replay_task(db, user_id, idempotency) or response


@router.post("/")
async def create_task(request: Request, db: AsyncSession = Depends(get_db)):
    """
    Create a new task and enqueue it for processing.
    Returns task_id immediately.
    """
    attempt = SourceAttempt(request, db)
    try:
        response = await _create_task(request, db, attempt)
    except HTTPException as exc:
        await attempt.rejected(exc.status_code, exc.detail)
        raise
    await attempt.accepted()
    return response


async def _create_task(request: Request, db: AsyncSession, attempt: SourceAttempt):
    data = await _read_json_object(request)

    raw_source = data.get("source")
    if isinstance(raw_source, dict):
        attempt.note_url(raw_source.get("url"))
    if not isinstance(raw_source, dict) or not isinstance(raw_source.get("url"), str) or not raw_source["url"].strip():
        raise HTTPException(status_code=400, detail="Source URL is required")
    try:
        requested_range = parse_requested_range(raw_source)
    except SourceRangeError as exc:
        raise HTTPException(status_code=422, detail=exc.to_detail()) from None
    auth_ctx = await _get_auth_context(request, db)
    user_id = auth_ctx.user_id
    team_id = auth_ctx.team_id
    attempt.note_user(user_id)
    attempt.note_workflow(data.get("workflow"))
    # A free preview (onboarding/preview.py): plain highlights of one whole video, never charged.
    preview = data.get("preview") is True
    preview_access: Dict[str, Any] | None = None
    if preview:
        preview_access = await preview_eligibility(db, user_id, team_id, client_platform(request))
        if not preview_access["eligible"]:
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "PREVIEW_UNAVAILABLE",
                    "reason": preview_access["reason"],
                    "message": PREVIEW_REFUSALS.get(preview_access["reason"], "A free preview isn't available."),
                },
            )
        if requested_range is not None or data.get("workflow") not in (None, "highlights") or data.get("instructions"):
            raise HTTPException(status_code=422, detail="A free preview makes highlights from a whole video.")

    # Get font options
    font_options = data.get("font_options", {})
    if not isinstance(font_options, dict):
        raise HTTPException(status_code=400, detail="font_options must be an object")
    font_family = _normalize_font_family(font_options.get("font_family"))
    font_size = _normalize_font_size(font_options.get("font_size"))
    font_color = _normalize_font_color(font_options.get("font_color"))
    caption_template = data.get("caption_template", "default")
    include_broll = data.get("include_broll") is True
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
    try:
        clip_length = validate_clip_length(data.get("clip_length", "auto"))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from None
    try:
        workflow_spec = parse_workflow_request(data, clip_length)
    except WorkflowRequestError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.to_detail()) from None
    if requested_range is not None and not workflow_supports_range(workflow_spec.workflow):
        raise HTTPException(status_code=422, detail=SourceRangeError(
            "Picking a part of a video works with highlights. Choose highlights, or a shorter video."
        ).to_detail())
    if not workflow_spec.legacy and not await workflow_allowed_for(
        db, user_id, workflow_spec.workflow, platform=client_platform(request)
    ):
        error = workflow_not_available()
        raise HTTPException(status_code=error.status, detail=error.to_detail())
    # Dynamic edits are render-side, so any workflow takes them. A bad value is a
    # 422 and a locked feature a 403, both before any charge. The stored form is
    # to_json(): {} (stored as NULL) when the intensity is off.
    try:
        edit_style = parse_edit_style(data.get("edit_style"))
    except EditStyleError as exc:
        raise _invalid_edit_style(exc) from None
    edit_style_json = edit_style.to_json()
    fx_requested = fx_active(edit_style, caption_template)
    if fx_requested and not await _dynamic_edits_enabled(db, request, user_id):
        raise _dynamic_edits_not_available()
    if fx_requested and preview:
        raise HTTPException(status_code=422, detail="A free preview doesn't include dynamic edits.")
    # With dynamic edits, an in-flight task for the same video is the same job only
    # if it was made with the same style and caption template. Without them, an
    # in-flight task that renders dynamic edits is never the same job (the lookup skips it).
    edit_identity = {"edit_identity": (edit_style_json, caption_template or "default")} if fx_requested else {}
    workflow_options = workflow_spec.options.to_json()
    cleanup_settings = new_task_cleanup_settings(data)
    if not raw_source or not raw_source.get("url"):
        raise HTTPException(status_code=400, detail="Source URL is required")

    upload_options = {
        "url": raw_source["url"],
        "font_family": font_family,
        "font_size": font_size,
        "font_color": font_color,
        "caption_template": caption_template,
        "include_broll": include_broll,
        "processing_mode": processing_mode,
        "output_format": output_format,
        "add_subtitles": add_subtitles,
        "clip_length": clip_length,
        "cleanup_settings": cleanup_settings,
    }
    if not workflow_spec.legacy:
        # Plain highlights keep their existing key. The key is a hash, so topic
        # and instructions never leave the process.
        upload_options["workflow"] = workflow_spec.workflow
        upload_options["workflow_options"] = workflow_options
    if edit_style_json:
        upload_options["edit_style"] = edit_style_json
    if requested_range is not None:
        # Two parts of one upload are two jobs. Whole-video keys are unchanged.
        upload_options["source_range"] = requested_range.key()
    upload_request_key = _upload_request_key(request, raw_source["url"], upload_options)
    # A keyed request answers with the task the key made, however long ago
    # within retention. Unkeyed upload retries keep the short-window dedupe.
    idempotency = _task_idempotency(request, upload_options, raw_source.get("title"))
    if idempotency and db is not None:
        replay = await _replay_task(db, user_id, idempotency)
        if replay is not None:
            return replay
    if upload_request_key and db is not None:
        existing = await TaskRepository.find_recent_upload_task(
            db, user_id, upload_request_key, team_id=team_id
        )
        if existing is not None:
            return await _bind_existing_task(
                db, user_id, idempotency,
                _queued_upload_task(existing.id, existing.job_id),
            )

    # Same video already queued or rendering: hand back that task. This is
    # before preflight and before any minute charge. Callers that unit-test
    # the route without a database skip the lookup.
    source_key = None
    if db is not None:
        source_key = await source_key_for_url(db, user_id, raw_source["url"].strip())
        if source_key and requested_range is not None:
            source_key = f"{source_key}|range:{requested_range.key()}"
        if source_key:
            existing = await TaskRepository.find_active_source_task(
                db, user_id, source_key, team_id=team_id, **edit_identity
            )
            if existing is not None:
                return await _bind_existing_task(
                    db, user_id, idempotency,
                    duplicate_source_response(str(existing.id), existing.job_id),
                )

    try:
        billing_service = BillingService(db)
        if preview:
            preview_seconds = preview_access["max_minutes"] * 60
            billing = {
                "plan": "preview",
                "max_youtube_duration_seconds": preview_seconds,
                "max_upload_duration_seconds": preview_seconds,
            }
        else:
            billing = (
                await billing_service.assert_can_create_task(user_id, team_id=team_id)
                if team_id
                else await billing_service.assert_can_create_task(user_id)
            )

        task_service = TaskService(db)

        try:
            source_type = task_service.video_service.determine_source_type(raw_source["url"])
        except ValueError as exc:
            # Only rejected http(s) stream URLs are a 422 validation error.
            # Local paths and other non-URL sources keep the historic 404.
            if not str(raw_source["url"]).strip().lower().startswith(("http://", "https://")):
                raise
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if source_type == "youtube":
            # Shorts, live, share and embed links all become watch?v=ID, so the
            # stored source, downloads and caches see one form per video.
            raw_source["url"] = canonical_youtube_url(raw_source["url"]) or raw_source["url"]
        source_title = raw_source.get("title")
        stream_metadata: Dict[str, Any] | None = None
        duration_limit = (
            billing["max_youtube_duration_seconds"]
            if source_type in {"youtube", "twitch", "kick"}
            else billing["max_upload_duration_seconds"]
        )
        # With a picked part the whole video may be longer than the plan allows.
        whole_video_limit = None if requested_range is not None else duration_limit
        if source_type == "youtube":
            metadata = await _preflight_youtube_source(raw_source["url"], whole_video_limit)
            source_title = source_title or metadata.get("title") or "YouTube Video"
            duration_seconds = float(metadata["duration"])
        elif source_type in {"twitch", "kick"}:
            stream_metadata = await _preflight_stream_source(raw_source["url"], source_type, whole_video_limit)
            source_title = source_title or stream_metadata.get("title") or f"{source_type.title()} Video"
            duration_seconds = float(stream_metadata["duration"])
        else:
            if await foreign_upload(db, raw_source["url"], user_id):
                raise HTTPException(status_code=404, detail="Video file not found")
            duration_seconds = await _preflight_upload_source(
                raw_source["url"], user_id, whole_video_limit, db
            )

        try:
            source_range = resolve_source_range(requested_range, duration_seconds, duration_limit)
        except SourceRangeError as exc:
            raise HTTPException(status_code=422, detail=exc.to_detail()) from None
        # Minutes are charged for the part that is processed, and refunded the same.
        if source_range is not None:
            duration_seconds = source_range.length

        try:
            check_source_duration(workflow_spec, duration_seconds)
        except WorkflowRequestError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.to_detail()) from None

        async with generation_allowance_guard(
            db, user_id, enabled=runtime_config.monetization_enabled, team_id=team_id
        ):
            if idempotency:
                # Admissions are serialized here. A retry that waited on the
                # original must get its task, not a limit error after its charge.
                replay = await _replay_task(db, user_id, idempotency)
                if replay is not None:
                    return replay
            if preview:
                # Free: no minutes are checked or charged. Checked again under the account's lock: two
                # requests at once would otherwise both pass the check above and make two free previews.
                preview_access = await preview_eligibility(db, user_id, team_id, client_platform(request))
                if not preview_access["eligible"]:
                    raise HTTPException(
                        status_code=403,
                        detail={
                            "code": "PREVIEW_UNAVAILABLE",
                            "reason": preview_access["reason"],
                            "message": PREVIEW_REFUSALS.get(preview_access["reason"], "A free preview isn't available."),
                        },
                    )
            elif team_id:
                await billing_service.assert_can_create_task(
                    user_id, duration_seconds, team_id=team_id
                )
            else:
                await billing_service.assert_can_create_task(user_id, duration_seconds)
            async with source_admission_lock(db, f"team:{team_id}" if team_id else user_id, source_key):
                if source_key:
                    existing = await TaskRepository.find_active_source_task(
                        db, user_id, source_key, team_id=team_id, **edit_identity
                    )
                    if existing is not None:
                        return await _bind_existing_task(
                            db, user_id, idempotency,
                            duplicate_source_response(str(existing.id), existing.job_id),
                        )
                try:
                    task_id = await task_service.create_task_with_source(
                        user_id=user_id,
                        url=raw_source["url"],
                        title=source_title,
                        font_family=font_family,
                        font_size=font_size,
                        font_color=font_color,
                        caption_template=caption_template,
                        include_broll=include_broll,
                        processing_mode=processing_mode,
                        source_duration_seconds=math.ceil(duration_seconds),
                        upload_request_key=upload_request_key,
                        workflow=workflow_spec.workflow,
                        workflow_options=workflow_options,
                        **({"edit_style": edit_style_json} if edit_style_json else {}),
                        **({"idempotency": idempotency} if idempotency else {}),
                        **({"team_id": team_id} if team_id else {}),
                        **({"preview": True} if preview else {}),
                    )
                except TaskIdempotencyReplay:
                    # A concurrent request with this key committed its task first
                    # (self-hosted creates aren't serialized). Ours rolled back.
                    replay = await _replay_task(db, user_id, idempotency)
                    if replay is None:
                        raise
                    return replay
            if getattr(task_service, "reused_upload_task", False):
                return await _bind_existing_task(db, user_id, idempotency, _queued_upload_task(
                    task_id, getattr(task_service, "reused_upload_job_id", None)
                ))

            try:
                # Persist retry options before submitting the job; Redis remains a legacy cache.
                processing_job_id = f"{task_id}:{secrets.token_hex(16)}"
                await _save_task_source_metadata(
                    task_id,
                    _merge_task_source_metadata(
                        {
                            "processing_job_id": processing_job_id,
                            "clip_length": clip_length,
                            "enqueued_at": datetime.now(timezone.utc).isoformat(),
                            **({"source_key": source_key} if source_key else {}),
                            **_stream_resolution_metadata(stream_metadata),
                            **({SOURCE_RANGE_KEY: source_range.to_metadata()} if source_range else {}),
                        },
                        source_url=raw_source["url"],
                        source_type=source_type,
                        output_format=output_format,
                        add_subtitles=add_subtitles,
                        cleanup_settings=cleanup_settings,
                    ),
                    db=db,
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
                    clip_length,
                    _job_id=processing_job_id,
                )

            except Exception:
                await task_service.task_repo.update_task_status(
                    db, task_id, "error", progress=0,
                    progress_message="Unable to queue processing. Please retry this generation.",
                    expected_statuses=["queued"],
                )
                raise

            logger.info(f"Task {task_id} created and job {job_id} enqueued")
            capture_event(
                "task_created",
                user_id,
                {
                    "task_id": task_id,
                    "workflow": workflow_spec.workflow,
                    "source_kind": source_kind(source_type, raw_source["url"]),
                    "source_duration_seconds": math.ceil(duration_seconds),
                    **({"source_range": True} if source_range else {}),
                    "has_instructions": bool(workflow_spec.options.instructions),
                    "plan": billing.get("plan") if isinstance(billing, dict) else None,
                    "client": request_client(request),
                    "processing_mode": processing_mode,
                    "output_format": output_format,
                    "clip_length": clip_length,
                    **({"edit_intensity": edit_style.intensity} if edit_style.active else {}),
                    **({"preview": True} if preview else {}),
                },
            )

            return {
                "task_id": task_id,
                "job_id": job_id,
                "message": "Task created and queued for processing",
            }

    except GenerationAllowanceBusy as e:
        raise HTTPException(status_code=409, detail=str(e), headers={"Retry-After": "1"})
    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except BillingLimitExceeded as e:
        if idempotency and db is not None:
            replay = await _replay_task(db, user_id, idempotency)
            if replay is not None:
                return replay
        raise HTTPException(
            status_code=402,
            detail={
                "code": "SUBSCRIPTION_REQUIRED",
                "message": e.summary.get("reason") or "Choose a paid plan to process videos.",
                "billing": jsonable_encoder(e.summary),
            },
        )
    except Exception as e:
        logger.error(f"Error creating task: {e}")
        raise HTTPException(status_code=500, detail=f"Error creating task: {str(e)}")


@router.get("/billing/summary")
async def get_billing_summary(request: Request, db: AsyncSession = Depends(get_db)):
    """Get monetization status and current usage for authenticated workspace."""
    auth_ctx = await _get_auth_context(request, db)

    try:
        billing_service = BillingService(db)
        summary = await billing_service.get_usage_summary(
            auth_ctx.user_id, team_id=auth_ctx.team_id
        )
        return summary
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        logger.error(f"Error retrieving billing summary: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error retrieving billing summary: {str(e)}",
        )


@router.get("/workflows")
async def get_workflows(request: Request, db: AsyncSession = Depends(get_db)):
    """What the authenticated user can make on this platform, with each workflow's limits."""
    user_id = await _get_user_id_from_headers(request, db)
    return workflows_info(await allowed_workflows(db, user_id, platform=client_platform(request)))


# Declared before "/{task_id}", which would otherwise capture "edit-styles" as a task id.
@router.get("/edit-styles")
async def get_edit_styles(request: Request, db: AsyncSession = Depends(get_db)):
    """Dynamic edits for the authenticated user on this platform: whether they are on, and the choices.

    Clients show intensity, the edit toggles and the animated caption styles only when ``enabled`` is true, and
    ask ``/caption-templates?include=fx`` only then.
    """
    user_id = await _get_user_id_from_headers(request, db)
    defaults = EditStyle().model_dump()
    defaults.pop("intensity")
    return {
        "enabled": await _dynamic_edits_enabled(db, request, user_id),
        "intensities": list(INTENSITIES),
        "defaults": defaults,
        "settings": edit_style_schema(),
        "music_moods": list(MOODS),
        "grades": list(GRADES),
        # Only the listed animated styles are offered; unlisted ids stay valid for stored tasks and API callers.
        "templates": list(LISTED_FX_CAPTION_TEMPLATES),
        "reactions_available": _reactions_available(),
    }


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
    download: bool = False,
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

    if is_s3(clip["file_path"]):
        await db.close()
        url = await run_in_thread(MediaStorage().signed_url, clip["file_path"], clip["filename"], attachment=download)
        return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
    clip_path = Path(clip["file_path"])
    if not clip_path.exists():
        raise HTTPException(status_code=404, detail="Clip file not found")

    return FileResponse(
        path=str(clip_path),
        media_type="video/mp4",
        filename=clip["filename"],
        content_disposition_type="attachment" if download else "inline",
        headers={"Cache-Control": "private, no-store"},
    )


@router.get("/{task_id}")
async def get_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Get task details."""
    try:
        task_service = TaskService(db)
        await _require_task_reader(request, task_service, db, task_id)
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
        await _require_task_reader(request, task_service, db, task_id)
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
        task_service = TaskService(local_db)
        task = await _require_task_reader(request, task_service, local_db, task_id)

    async def event_generator():
        """Generate SSE events for task progress."""
        # Send initial task status
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

        # If task is already completed or error, close connection
        if task.get("status") in ["completed", "error", "cancelled"]:
            yield {"event": "close", "data": json.dumps({"status": task.get("status")})}
            return

        # Connect to Redis for real-time updates
        runtime_config = get_config()
        redis_client = redis.Redis(
            host=runtime_config.redis_host,
            port=runtime_config.redis_port,
            password=runtime_config.redis_password,
            decode_responses=True,
        )

        try:
            # Subscribe to progress updates
            async with aclosing(ProgressTracker.subscribe_to_progress(redis_client, task_id)) as updates:
                async for progress_data in updates:
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
            await redis_client.aclose()

    return EventSourceResponse(event_generator())


@router.patch("/{task_id}")
async def update_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Update task details (title)."""
    try:
        data = await _read_json_object(request)
        title = data.get("title")

        if not isinstance(title, str) or not title.strip():
            raise HTTPException(status_code=400, detail="Title is required")

        task_service = TaskService(db)

        task = await _require_task_owner(request, task_service, db, task_id)

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
        task_service = TaskService(db)

        # The owner, or a current member of the task's team. A member removed
        # from the team, or an API key bound to another workspace, is refused.
        await _require_task_owner(request, task_service, db, task_id)

        # Delete clips and task
        await task_service.delete_task(task_id)

        return {"message": "Task deleted successfully"}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting task: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting task: {str(e)}")


@router.delete("/{task_id}/clips/{clip_id}")
async def delete_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Delete a specific clip."""
    try:
        task_service = TaskService(db)

        await _require_task_owner(request, task_service, db, task_id)

        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        await task_service.delete_clip(task_id, clip_id)

        return {"message": "Clip deleted successfully"}

    except HTTPException:
        raise
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Error deleting clip: {e}")
        raise HTTPException(status_code=500, detail=f"Error deleting clip: {str(e)}")


@router.get("/{task_id}/clips/{clip_id}/file")
async def get_clip_file(
    task_id: str,
    clip_id: str,
    request: Request,
    download: bool = False,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Serve a clip file after verifying task ownership."""
    try:
        task_service = TaskService(db)
        task = await _require_task_reader(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        if is_s3(clip["file_path"]):
            await db.close()
            url = await run_in_thread(MediaStorage().signed_url, clip["file_path"], clip["filename"], attachment=download)
            if download:
                capture_clip_export(request, task, surface="original", format="original")
            return RedirectResponse(url, status_code=307, headers={"Cache-Control": "private, no-store"})
        clip_path = Path(clip["file_path"])
        if not clip_path.exists():
            raise HTTPException(status_code=404, detail="Clip file not found")
        if download:
            capture_clip_export(request, task, surface="original", format="original")

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
            content_disposition_type="attachment" if download else "inline",
            headers={"Cache-Control": "private, no-store"},
        )
    except HTTPException:
        raise
    except PoolTimeout:
        raise pool_busy(request) from None
    except Exception as e:
        logger.error(f"Error serving clip file: {e}")
        raise HTTPException(status_code=500, detail=f"Error serving clip file: {str(e)}")


async def _prepare_clip_archive(request: Request, db: AsyncSession, task_id: str):
    """Authorize like a single-clip download and list the clip files that exist."""
    task_service = TaskService(db)
    user_id, task = await _authorize_task_reader(request, task_service, db, task_id)
    clips = await task_service.clip_repo.get_clips_by_task(db, task_id)
    # Release the DB connection before storage lookups and the long stream;
    # see get_clip_file.
    await db.close()
    try:
        entries = await run_in_thread(
            clip_archive.resolve_entries, clips, MediaStorage(), task.get("workflow")
        )
    except STORAGE_ERRORS:
        logger.exception("Could not list clip files for task %s", task_id)
        raise HTTPException(
            status_code=503,
            detail="Clip storage is unavailable right now. Please try again shortly.",
        ) from None
    if not entries:
        raise HTTPException(
            status_code=404,
            detail="This generation has no clip files to download.",
        )
    return user_id, task, entries


@router.get("/{task_id}/clips/zip/preflight")
async def preflight_clip_archive(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Check a ZIP download would start, so a browser can surface errors first."""
    user_id, task, entries = await _prepare_clip_archive(request, db, task_id)
    if await clip_archive.archive_in_progress(user_id):
        raise HTTPException(
            status_code=429,
            detail="A ZIP download of your clips is already in progress. Wait for it to finish, then try again.",
        )
    return {
        "filename": clip_archive.archive_filename(task.get("source_title")),
        "clip_count": len(entries),
        "total_bytes": sum(entry.size for entry in entries),
    }


@router.get("/{task_id}/clips/zip")
async def download_clip_archive(
    task_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db, scope="function"),
):
    """Stream every existing clip of the task as one ZIP, built on the fly."""
    user_id, task, entries = await _prepare_clip_archive(request, db, task_id)
    try:
        slot = await clip_archive.ArchiveSlot(user_id).acquire()
    except clip_archive.ClipArchiveBusy as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from None
    filename = clip_archive.archive_filename(task.get("source_title"))
    capture_clip_export(request, task, surface="zip", format="zip", clip_count=len(entries))
    return StreamingResponse(
        clip_archive.stream_with_slot(entries, MediaStorage(), slot),
        media_type="application/zip",
        headers={
            "Content-Disposition": clip_archive.content_disposition(filename),
            "Cache-Control": "private, no-store",
            # Let nginx pass chunks through instead of buffering the archive.
            "X-Accel-Buffering": "no",
        },
    )


async def _fx_clip_edit(
    request: Request,
    task_service: TaskService,
    db: AsyncSession,
    task: Dict[str, Any],
    task_id: str,
    clip_ids: list[str],
) -> Optional[FxClipJobRequest]:
    """The go-ahead to queue an edit on the video worker when any of ``clip_ids`` has dynamic edits (§13.0).

    Legacy clips get None and keep the synchronous path byte for byte. Route unit tests without a database skip the
    lookup.
    """
    if db is None:
        return None
    for clip_id in clip_ids:
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if clip and clip.get("task_id") == task_id and clip_has_fx(clip):
            return FxClipJobRequest(
                user_id=task["user_id"],
                queue=getattr(request.app.state, "queue_adapter", None),
            )
    return None


async def _queue_fx_clip_edit(
    fx_job: FxClipJobRequest,
    task: Dict[str, Any],
    task_id: str,
    kind: str,
    args: Dict[str, Any],
    clip_ids: list[str],
) -> JSONResponse:
    """Queue ``fx_clip_job`` and answer 202 ``{"status": "queued", "job_id", "kind", "clip_ids"}``.

    Poll ``GET /tasks/{task_id}/clip-jobs/{job_id}``; ``result`` there is this route's synchronous body.
    While another clip job of the task is queued or running: 409 ``CLIP_JOB_IN_PROGRESS`` with its ``job_id``.
    """
    if task.get("status") in {"queued", "processing"}:
        raise ValueError("Wait for processing to finish before editing this task")
    try:
        queued = await enqueue_fx_clip_job(
            task_id=task_id,
            user_id=fx_job.user_id,
            kind=kind,
            args=args,
            queue=fx_job.queue,
            clip_ids=clip_ids,
        )
    except FxClipJobInProgress as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from None
    except Exception as exc:
        logger.warning("Could not queue the %s edit for task %s: %s", kind, task_id, exc)
        raise FxClipJobQueueError("The edit could not be queued") from exc
    return JSONResponse(status_code=202, content=queued.body())


def _clip_edit_queue_unavailable() -> HTTPException:
    return HTTPException(
        status_code=503,
        detail={
            "code": "QUEUE_UNAVAILABLE",
            "message": "The edit couldn't be queued for rendering. Nothing was changed; try again.",
        },
    )


@router.patch("/{task_id}/clips/{clip_id}")
async def trim_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Trim clip boundaries and regenerate clip file."""
    try:
        payload = await _read_json_object(request)
        start_offset = _finite_number(payload.get("start_offset", 0), "start_offset")
        end_offset = _finite_number(payload.get("end_offset", 0), "end_offset")

        if start_offset < 0 or end_offset < 0:
            raise HTTPException(status_code=400, detail="Offsets must be non-negative")

        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        fx_job = await _fx_clip_edit(request, task_service, db, task, task_id, [clip_id])
        if fx_job is not None:
            return await _queue_fx_clip_edit(
                fx_job, task, task_id, "trim",
                {"clip_id": clip_id, "start_offset": start_offset, "end_offset": end_offset}, [clip_id],
            )
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, start_offset, end_offset
        )
        return {"clip": updated_clip}
    except FxClipJobQueueError:
        raise _clip_edit_queue_unavailable() from None
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
        payload = await _read_json_object(request)
        split_time = _finite_number(payload.get("split_time", 0), "split_time")
        if split_time <= 0:
            raise HTTPException(
                status_code=400, detail="split_time must be greater than zero"
            )

        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        fx_job = await _fx_clip_edit(request, task_service, db, task, task_id, [clip_id])
        if fx_job is not None:
            return await _queue_fx_clip_edit(
                fx_job, task, task_id, "split", {"clip_id": clip_id, "split_time": split_time}, [clip_id],
            )
        result = await task_service.split_clip(task_id, clip_id, split_time)
        return result
    except FxClipJobQueueError:
        raise _clip_edit_queue_unavailable() from None
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
        payload = await _read_json_object(request)
        clip_ids = payload.get("clip_ids") or []
        if not isinstance(clip_ids, list) or any(not isinstance(cid, str) or not cid for cid in clip_ids):
            raise HTTPException(status_code=400, detail="clip_ids must be an array")

        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        fx_job = await _fx_clip_edit(request, task_service, db, task, task_id, clip_ids)
        if fx_job is not None:
            if len(clip_ids) < 2 or len(set(clip_ids)) != len(clip_ids):
                raise ValueError("At least two distinct clips are required to merge")
            return await _queue_fx_clip_edit(
                fx_job, task, task_id, "merge", {"clip_ids": list(clip_ids)}, list(clip_ids),
            )
        result = await task_service.merge_clips(task_id, clip_ids)
        return result
    except FxClipJobQueueError:
        raise _clip_edit_queue_unavailable() from None
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
        payload = await _read_json_object(request)
        caption_text = str(payload.get("caption_text", "")).strip()
        position = str(payload.get("position", "bottom"))
        highlight_words = payload.get("highlight_words") or []
        if not isinstance(highlight_words, list):
            raise HTTPException(
                status_code=400, detail="highlight_words must be an array"
            )

        font_size = payload.get("font_size")
        if font_size is not None:
            font_size = _finite_number(font_size, "font_size")
            if not math.isfinite(font_size) or not 12 <= font_size <= 72:
                raise ValueError("Font size must be between 12 and 72")
            font_size = int(font_size)
        position_y = payload.get("position_y")
        if position_y is not None:
            position_y = _finite_number(position_y, "position_y")
            if not math.isfinite(position_y) or not 0.1 <= position_y <= 0.85:
                raise ValueError("Caption position must be between 0.1 and 0.85")
        if position not in {"top", "middle", "bottom"}:
            raise ValueError("Invalid caption position")

        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        fx_job = await _fx_clip_edit(request, task_service, db, task, task_id, [clip_id])
        if fx_job is not None:
            return await _queue_fx_clip_edit(
                fx_job, task, task_id, "captions",
                {
                    "clip_id": clip_id,
                    "caption_text": caption_text,
                    "position": position,
                    "highlight_words": [str(word) for word in highlight_words],
                    "font_size": font_size,
                    "position_y": position_y,
                },
                [clip_id],
            )
        updated_clip = await task_service.update_clip_captions(
            task_id,
            clip_id,
            caption_text,
            position,
            [str(word) for word in highlight_words],
            font_size=font_size,
            position_y=position_y,
        )
        return {"clip": updated_clip}
    except FxClipJobQueueError:
        raise _clip_edit_queue_unavailable() from None
    except YouTubeGeoRestrictedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "YOUTUBE_GEO_RESTRICTED", "message": str(exc)},
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating captions: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating captions: {str(e)}"
        )


@router.post("/{task_id}/clips/{clip_id}/regenerate")
async def regenerate_clip(
    task_id: str, clip_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Regenerate a single clip after editing timing values."""
    try:
        payload = await _read_json_object(request)
        start_offset = _finite_number(payload.get("start_offset", 0), "start_offset")
        end_offset = _finite_number(payload.get("end_offset", 0), "end_offset")

        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        fx_job = await _fx_clip_edit(request, task_service, db, task, task_id, [clip_id])
        if fx_job is not None:
            return await _queue_fx_clip_edit(
                fx_job, task, task_id, "trim",
                {"clip_id": clip_id, "start_offset": start_offset, "end_offset": end_offset}, [clip_id],
            )
        updated_clip = await task_service.trim_clip(
            task_id, clip_id, start_offset, end_offset
        )
        return {"clip": updated_clip, "message": "Clip regenerated successfully"}
    except FxClipJobQueueError:
        raise _clip_edit_queue_unavailable() from None
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
    """Update task-level styling settings and optionally apply to all existing clips.

    ``edit_style`` omitted (or null) keeps the stored style. An omitted ``caption_template`` keeps a stored
    animated template (legacy tasks still fall back to "default"). Only turning dynamic edits on is flag-gated.
    Re-rendering clips with dynamic edits, or into them, runs on the video worker: 202 with a pollable job.
    """
    try:
        payload = await _read_json_object(request)
        font_family = _normalize_font_family(payload.get("font_family"))
        font_size = _normalize_font_size(payload.get("font_size"))
        font_color = _normalize_font_color(payload.get("font_color"))
        caption_template = payload.get("caption_template", "default")
        apply_to_existing = bool(payload.get("apply_to_existing", False))
        try:
            requested_style = (
                parse_edit_style(payload["edit_style"]) if payload.get("edit_style") is not None else None
            )
        except EditStyleError as exc:
            raise _invalid_edit_style(exc) from None

        task_service = TaskService(db)
        await _require_task_owner(request, task_service, db, task_id)
        task_record = await task_service.task_repo.get_task_by_id(db, task_id)
        if not task_record:
            raise HTTPException(status_code=404, detail="Task not found")

        stored_template = task_record.get("caption_template")
        if "caption_template" not in payload and is_fx_template(stored_template):
            caption_template = stored_template
        stored_style = read_edit_style(task_record.get("edit_style"))
        target_style = requested_style if requested_style is not None else stored_style
        # Gate only a transition to FX (style off -> on, template legacy -> FX), so
        # the kill switch never blocks turning edits off or a font change.
        turns_fx_on = (target_style.active and not stored_style.active) or (
            is_fx_template(caption_template) and not is_fx_template(stored_template)
        )
        if turns_fx_on and not await _dynamic_edits_enabled(db, request, task_record["user_id"]):
            raise _dynamic_edits_not_available()
        # Clips that already carry dynamic edits re-render on the worker too, even
        # into a legacy look. Route unit tests without a database skip the lookup.
        fx_job = None
        if apply_to_existing and (
            fx_active(target_style, caption_template)
            or (db is not None and await task_service.task_repo.has_fx_clips(db, task_id))
        ):
            fx_job = FxClipJobRequest(
                user_id=task_record["user_id"],
                queue=getattr(request.app.state, "queue_adapter", None),
            )
        # Like cleanup below: an omitted flag keeps the task's B-roll setting,
        # so a styling-only update doesn't strip B-roll from every clip.
        include_broll = (
            payload["include_broll"] is True
            if "include_broll" in payload
            else bool(task_record.get("include_broll"))
        )

        # Cleanup fields the request omits keep the task's stored values, so a
        # styling-only update can't silently switch pause/filler cleanup off.
        metadata = await _load_task_source_metadata(task_id, db=db)
        cleanup_settings = updated_cleanup_settings(payload, metadata)
        if font_family is not None and not is_font_accessible(
            font_family, task_record["user_id"]
        ):
            raise HTTPException(
                status_code=400, detail="Selected font is not available"
            )
        task = await task_service.update_task_settings(
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
            apply_to_existing,
            cleanup_settings,
            **({"edit_style": target_style.to_json()} if requested_style is not None else {}),
            **({"fx_job": fx_job} if fx_job is not None else {}),
        )
        if isinstance(task, QueuedClipJob):
            # The job re-renders and then stores the cleanup settings, like the
            # synchronous path does after its regeneration (§13.0).
            return JSONResponse(status_code=202, content=task.body())
        # Settings edits must not write the job ID captured before a lengthy
        # regeneration: a concurrent resume may have reserved a newer attempt.
        metadata.pop("processing_job_id", None)
        await _save_task_source_metadata(
            task_id,
            _merge_task_source_metadata(
                metadata,
                source_url=metadata.get("url") or task_record.get("source_url"),
                source_type=metadata.get("source_type") or task_record.get("source_type"),
                output_format=metadata.get("output_format") or task.get("output_format"),
                add_subtitles=(
                    metadata["add_subtitles"]
                    if isinstance(metadata.get("add_subtitles"), bool)
                    else task.get("add_subtitles")
                ),
                cleanup_settings=cleanup_settings,
            ),
            db=db,
        )
        return {"task": task, "message": "Task settings updated"}
    except YouTubeGeoRestrictedError as exc:
        raise HTTPException(
            status_code=422,
            detail={"code": "YOUTUBE_GEO_RESTRICTED", "message": str(exc)},
        ) from None
    except FxClipJobInProgress as exc:
        raise HTTPException(status_code=409, detail=exc.detail()) from None
    except FxClipJobQueueError:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "QUEUE_UNAVAILABLE",
                "message": "Your settings were saved, but the clips couldn't be queued for re-rendering. Try again.",
            },
        ) from None
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating task settings: {e}")
        raise HTTPException(
            status_code=500, detail=f"Error updating task settings: {str(e)}"
        )


async def _close_queued_clip_job(job_id: str, state: Dict[str, Any], error: Dict[str, str]) -> bool:
    """Give up on a clip job no worker has started, so that none starts it later. False if a worker has it.

    The user is told to try again, and a retry would otherwise run next to the first job once the queue
    clears (two trims or splits of one clip). Unreachable Redis is False too: the job may still run.
    """
    try:
        if await claim_fx_clip_job(job_id, FX_CLIP_JOB_CLOSED) != FX_CLIP_JOB_CLOSED:
            return False
        await write_fx_clip_job_state(job_id, {**state, "status": "failed", "progress": 0.0, "error": error})
        await release_fx_clip_job_marker(str(state.get("task_id") or ""), job_id)
        return True
    except Exception as exc:  # noqa: BLE001 - not closed, so not reported as failed
        logger.warning("Could not close queued clip job %s: %s", job_id, exc)
        return False


@router.get("/{task_id}/clip-jobs/{job_id}")
async def get_clip_job(
    task_id: str, job_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Status of a clip edit queued on the video worker (202 from settings, captions, trim, split or merge).

    ``{"status": "queued"|"running"|"done"|"failed", "kind", "progress": 0-1, "error"?: {code, message},
    "result"?: the body the synchronous route would have returned}``. Clients poll every 2 s.
    A job queued for 15 minutes is ``WORKER_UNAVAILABLE``; a running job without a heartbeat for 2 minutes, or one
    older than its budget, is ``WORKER_STOPPED`` (``fx_clip_jobs.stale_job_error``).
    """
    task_service = TaskService(db)
    await _require_task_owner(request, task_service, db, task_id)
    if not re.fullmatch(r"[0-9a-f]{32}", job_id or ""):
        raise HTTPException(status_code=404, detail="Clip job not found")
    try:
        state = await read_fx_clip_job_state(job_id)
    except Exception as exc:  # noqa: BLE001 - Redis down is a retryable 503, not a failed job
        logger.warning("Could not read clip job %s: %s", job_id, exc)
        raise HTTPException(status_code=503, detail="Clip job status is unavailable. Try again.") from None
    if not state or state.get("task_id") != task_id:
        raise HTTPException(status_code=404, detail="Clip job not found")

    status = state.get("status") if state.get("status") in FX_CLIP_JOB_STATUSES else "queued"
    try:
        progress = min(1.0, max(0.0, float(state.get("progress") or 0.0)))
    except (TypeError, ValueError):
        progress = 0.0
    body: Dict[str, Any] = {"status": status, "kind": state.get("kind"), "progress": progress}
    try:
        queued_at = float(state.get("queued_at") or 0.0)
    except (TypeError, ValueError):
        queued_at = 0.0
    if status == "queued" and queued_at and time.time() - queued_at > FX_CLIP_JOB_QUEUE_TIMEOUT_SECONDS:
        error = {
            "code": "WORKER_UNAVAILABLE",
            "message": "No worker picked up this edit. Please try again.",
        }
        # Failed only once no worker can run it any more; a job a worker has just taken stays queued here.
        if await _close_queued_clip_job(job_id, state, error):
            body["status"] = "failed"
            body["error"] = error
        return body
    stale = stale_job_error({**state, "status": status})
    if stale and status == "queued" and not await _close_queued_clip_job(job_id, state, stale):
        return body
    if stale:
        # The worker died mid-job (no heartbeat) or the job outlived its budget: nothing will finish it.
        body["status"] = "failed"
        body["error"] = stale
        return body
    if status == "done":
        body["progress"] = 1.0
        if "result" in state:
            body["result"] = state["result"]
    if status == "failed":
        error = state.get("error") if isinstance(state.get("error"), dict) else {}
        body["error"] = {
            "code": str(error.get("code") or "CLIP_JOB_FAILED"),
            "message": str(error.get("message") or "The edit could not be rendered."),
        }
    return body


@router.get("/{task_id}/clips/{clip_id}/export")
@media_work
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
        task = await _require_task_reader(request, task_service, db, task_id)
        clip = await task_service.clip_repo.get_clip_by_id(db, clip_id)
        if not clip or clip.get("task_id") != task_id:
            raise HTTPException(status_code=404, detail="Clip not found")

        from pathlib import Path

        # Release the DB connection before the (potentially multi-minute) ffmpeg
        # re-encode and file streaming below — see get_clip_file for why holding
        # it here turns into "idle in transaction" connection-pool exhaustion.
        await db.close()

        runtime_config = get_config()
        output_path = await run_in_thread(
            export_with_preset,
            await run_in_thread(MediaStorage().local_path, clip["file_path"]),
            Path(runtime_config.temp_dir) / "exports",
            preset_name,
        )

        download_name = f"{Path(clip['filename']).stem}_{preset_name}.mp4"
        capture_clip_export(request, task, surface="preset", format=preset_name)
        return FileResponse(
            path=str(output_path), media_type="video/mp4", filename=download_name,
            background=BackgroundTask(output_path.unlink, missing_ok=True),
        )
    except HTTPException:
        raise
    except PoolTimeout:
        raise pool_busy(request) from None
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
            await redis_client.aclose()

        await task_service.task_repo.update_task_status(
            db,
            task_id,
            "cancelled",
            progress=0,
            progress_message="Cancelled by user",
            expected_statuses=["queued", "processing"],
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
    """Retry a cancelled or errored task while preserving completed clips."""
    return await _run_task_again(task_id, request, db)


@router.post("/{task_id}/unlock")
async def unlock_preview_task(
    task_id: str, request: Request, db: AsyncSession = Depends(get_db)
):
    """Render the rest of a free preview's clips in place, now that the account has a plan.

    Charges the video's minutes like a new project. The transcript and analysis are reused, so
    the clips are the moments the preview listed. On a 1080p plan the preview's own clip is made
    again at full quality.
    """
    try:
        body = await request.json()
    except Exception:
        body = {}
    if isinstance(body, dict) and body.get("when_paid") is True:
        # Asked before checkout: remembered, and the worker unlocks it as soon as the plan is
        # active, even if this tab is closed by then (onboarding/preview.py).
        task_service = TaskService(db)
        task = await _require_task_owner(request, task_service, db, task_id)
        summary = await BillingService(db).get_usage_summary(task["user_id"])
        if not grants_paid_access(summary.get("plan"), summary.get("subscription_status"), summary.get("subscription_provider")):
            if not is_locked_preview(task):
                return {"message": "Already unlocked", "job_id": None}
            await task_service.task_repo.save_preview(
                db, task_id, {**(preview_state(task) or {}), "unlock_requested_at": datetime.now(timezone.utc).isoformat()}
            )
            return {"message": "Unlocks when the plan is active", "job_id": None, "pending": True}
    return await _run_task_again(task_id, request, db, unlock=True)


async def unlock_requested_preview(db: AsyncSession, task_id: str) -> Optional[str]:
    """The worker's half of "unlock when paid": unlock now if the owner has a plan. Returns the job
    id, or None when it isn't time yet (no plan) or there's nothing to do."""
    try:
        result = await _run_task_again(task_id, None, db, unlock=True)
    except HTTPException as exc:
        if exc.status_code == 402:
            return None
        raise
    return (result or {}).get("job_id")


async def _unlock_preview_state(db: AsyncSession, task_service: TaskService, task: dict, task_id: str) -> None:
    summary = await BillingService(db).get_usage_summary(task["user_id"])
    _quality, short_side = reduced_media_limits(summary)
    if short_side is None:
        # Full HD: the 720p preview clip is rendered again with the rest.
        await task_service.clip_repo.delete_clips_by_task(db, task_id)
        await task_service.checkpoint_repo.forget_completed_positions(db, task_id)
    await task_service.task_repo.save_preview(
        db, task_id, {**(preview_state(task) or {}), "unlocked_at": datetime.now(timezone.utc).isoformat()}
    )


async def _run_task_again(task_id: str, request: Optional[Request], db: AsyncSession, *, unlock: bool = False):
    """Resume a task, or unlock a free preview. ``request`` is None when the worker unlocks a
    preview whose owner asked for it before checkout (the owner was checked then)."""
    try:
        task_service = TaskService(db)
        if request is not None:
            task = await _require_task_owner(request, task_service, db, task_id)

        async with task_run_guard(db, task_id):
            task = await task_service.task_repo.get_task_by_id(db, task_id)
            if not unlock and is_locked_preview(task):
                # Retrying a locked preview after subscribing is an unlock: charged once, and every
                # clip is made, not the preview's one again.
                unlock = True
            if unlock and not is_locked_preview(task):
                if is_preview(task):
                    return {"message": "Already unlocked", "job_id": None}
                raise HTTPException(status_code=400, detail="Only a free preview can be unlocked")
            if task.get("status") == "queued":
                return {"message": "Task already queued"}
            if unlock and task.get("status") == "processing":
                raise HTTPException(
                    status_code=409,
                    detail={"code": "PREVIEW_IN_PROGRESS", "message": "The preview is still being made. Unlock it when it's ready."},
                )
            if task.get("status") not in (["completed", "cancelled", "error"] if unlock else ["cancelled", "error"]):
                raise HTTPException(
                    status_code=400,
                    detail="Only cancelled/error/queued tasks can be retried",
                )
            # The rollout gate holds for resumes too: switching it off (or
            # leaving the allowlist) stops a workflow task from running again.
            spec = workflow_spec_from_task(task, "auto")  # the length never decides the gate
            if not spec.legacy and not await workflow_allowed_for(
                db, task["user_id"], spec.workflow, platform=client_platform(request) if request is not None else "web"
            ):
                error = workflow_not_available()
                raise HTTPException(status_code=error.status, detail=error.to_detail())

            source_url = task.get("source_url")
            source_type = task.get("source_type")
            output_format = "vertical"
            add_subtitles = True

            metadata = await _load_task_source_metadata(task_id, db=db)
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
            )

            if not source_url or not source_type:
                raise HTTPException(status_code=400, detail="Task source URL is missing")

            task_team_id = task.get("team_id")
            async with generation_allowance_guard(
                db,
                task["user_id"],
                enabled=get_config().monetization_enabled,
                **({"team_id": task_team_id} if task_team_id else {}),
            ):
                if task_team_id:
                    await BillingService(db).assert_can_resume_task(
                        task["user_id"], task_id, team_id=task_team_id
                    )
                else:
                    await BillingService(db).assert_can_resume_task(
                        task["user_id"], task_id
                    )
                if unlock:
                    await _unlock_preview_state(db, task_service, task, task_id)

                try:
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
                        await redis_client.aclose()

                    await task_service.task_repo.update_task_status(
                        db,
                        task_id,
                        "queued",
                        progress=0,
                        progress_message="Unlocking every clip" if unlock else "Re-queued by user",
                    )

                    processing_mode = (
                        task.get("processing_mode") or runtime_config.default_processing_mode
                    )

                    processing_job_id = f"{task_id}:{secrets.token_hex(16)}"
                    metadata["processing_job_id"] = processing_job_id
                    await _save_task_source_metadata(task_id, metadata, db=db)
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
                        normalize_clip_length(metadata.get("clip_length")),
                        _job_id=processing_job_id,
                    )
                except Exception:
                    if unlock:
                        # Still locked, so Unlock (or the cron) can try again. The minutes stay charged
                        # to this task and are reused then, never charged twice.
                        await task_service.task_repo.save_preview(db, task_id, preview_state(task) or {})
                    await task_service.task_repo.update_task_status(
                        db, task_id, task["status"],
                        progress_message="Could not queue generation. Please try again.",
                        expected_statuses=["queued", task["status"]],
                    )
                    raise

                return {"message": "Unlocking every clip" if unlock else "Generation retry queued", "job_id": job_id}
    except GenerationAllowanceBusy as e:
        raise HTTPException(status_code=409, detail=str(e), headers={"Retry-After": "1"})
    except BillingLimitExceeded as e:
        raise HTTPException(
            status_code=402,
            detail={
                "code": "SUBSCRIPTION_REQUIRED",
                "message": e.summary.get("reason") or "Plan usage limit reached",
                "billing": jsonable_encoder(e.summary),
            },
        )
    except TaskRunBusy as e:
        raise HTTPException(status_code=409, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error retrying generation: {e}")
        raise HTTPException(status_code=500, detail=f"Error retrying generation: {str(e)}")


@router.get("/dead-letter/list")
async def list_dead_letter_tasks(request: Request, db: AsyncSession = Depends(get_db)):
    """List tasks that exhausted retries and landed in dead-letter store."""
    runtime_config = get_config()
    await require_admin_user(request, db, runtime_config)
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
        await redis_client.aclose()
