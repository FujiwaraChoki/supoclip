"""Worker side of the clip edits that re-render dynamic-edits clips (FINAL §13.0, AP-C5).

The API stores what it stores today, queues ``fx_clip_job(ctx, job)`` on the video worker queue and answers 202
(``clip_service.enqueue_fx_clip_job``). This job runs the same ``ClipService`` method the synchronous route would
have run, under the same per-task edit lock (a PostgreSQL row lock, ``SELECT ... FOR UPDATE`` on the task, so a
legacy edit in an API process and this job on another machine never interleave), and records the outcome in Redis
``fx_clip_job:{job_id}``: ``running`` with an estimated ``progress``, then ``done`` with the synchronous route's body
as ``result``, or ``failed`` with ``{"code", "message"}``. Open task pages are told on the task's progress channel.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, Awaitable, Callable, Dict, Optional

from ..observability import set_trace_id, set_user_id
from ..repositories.edit_transaction import allow_long_edit
from .clip_service import (
    FX_CLIP_JOB_CLOSED,
    FX_CLIP_JOB_KINDS,
    FX_CLIP_JOB_STARTED,
    claim_fx_clip_job,
    _fx_clip_job_redis,
    read_fx_clip_job_state,
    release_fx_clip_job_marker,
    write_fx_clip_job_state,
)

logger = logging.getLogger(__name__)

# The registered ARQ timeout. The job enforces its own, smaller budget (max(1800, 120 x clips), capped below this)
# so it can still record the failure before ARQ cancels it.
FX_CLIP_JOB_TIMEOUT_SECONDS = 3 * 60 * 60
FX_CLIP_JOB_MIN_BUDGET_SECONDS = 1800
FX_CLIP_JOB_SECONDS_PER_CLIP = 120
FX_CLIP_JOB_BUDGET_MARGIN_SECONDS = 300
HEARTBEAT_SECONDS = 15.0
# A running record whose heartbeat is older than this belongs to a worker that died (OOM, host lost, SIGKILL):
# nothing will ever finish it, so the status route reports it as failed and clients stop polling.
HEARTBEAT_STALE_SECONDS = 120.0
WORKER_STOPPED_ERROR = {"code": "WORKER_STOPPED", "message": "The worker stopped before the edit finished. Try again."}
# Rough render time of one clip, for the progress estimate (renders report no progress of their own).
ESTIMATED_SECONDS_PER_CLIP = 45.0
PROGRESS_START = 0.05
PROGRESS_CEILING = 0.95
TASK_SOURCE_TTL_SECONDS = 60 * 60 * 24 * 7

GENERIC_FAILURE = "The edit could not be rendered. Your clip is unchanged; try again."


def job_budget_seconds(clip_count: int) -> float:
    """§13.0: ``max(1800, 120 x clip count)``, kept inside the registered ARQ timeout."""
    budget = max(FX_CLIP_JOB_MIN_BUDGET_SECONDS, FX_CLIP_JOB_SECONDS_PER_CLIP * max(1, int(clip_count)))
    return float(min(budget, FX_CLIP_JOB_TIMEOUT_SECONDS - FX_CLIP_JOB_BUDGET_MARGIN_SECONDS))


def estimated_progress(elapsed: float, clip_count: int) -> float:
    """A monotone estimate in ``[PROGRESS_START, PROGRESS_CEILING)``; ``done`` sets 1.0."""
    expected = ESTIMATED_SECONDS_PER_CLIP * max(1, int(clip_count))
    share = max(0.0, elapsed) / (max(0.0, elapsed) + expected)
    return round(PROGRESS_START + (PROGRESS_CEILING - PROGRESS_START) * share, 3)


def _number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if number == number and number > 0 else 0.0


def stale_job_error(state: Dict[str, Any], now: Optional[float] = None) -> Optional[Dict[str, str]]:
    """``WORKER_STOPPED`` for a ``running``/``queued`` record nothing can finish any more, else None.

    Running: the heartbeat (``updated_at``, rewritten every ``HEARTBEAT_SECONDS``) is older than
    ``HEARTBEAT_STALE_SECONDS``. Running or queued: the job has existed for longer than its budget plus the margin
    (``budget_seconds`` once the worker knows the clip count, else the registered ARQ timeout). The record itself is
    left alone: if the job does finish after all, a later poll sees ``done``.
    """
    status = state.get("status")
    if status not in ("running", "queued"):
        return None
    now = time.time() if now is None else now
    if status == "running":
        beat = _number(state.get("updated_at"))
        if beat and now - beat > HEARTBEAT_STALE_SECONDS:
            return dict(WORKER_STOPPED_ERROR)
    since = _number(state.get("started_at")) or _number(state.get("queued_at"))
    budget = _number(state.get("budget_seconds")) or float(FX_CLIP_JOB_TIMEOUT_SECONDS)
    if since and now - since > budget + FX_CLIP_JOB_BUDGET_MARGIN_SECONDS:
        return dict(WORKER_STOPPED_ERROR)
    return None


def _error_for(exc: BaseException) -> Dict[str, str]:
    """The failed record's error: the synchronous route's error, as a code."""
    from ..youtube_proxy import YouTubeGeoRestrictedError

    if isinstance(exc, asyncio.TimeoutError):
        return {"code": "CLIP_JOB_TIMEOUT", "message": "The edit took too long to render. Your clip is unchanged."}
    if isinstance(exc, YouTubeGeoRestrictedError):
        return {"code": "YOUTUBE_GEO_RESTRICTED", "message": str(exc)}
    if isinstance(exc, ValueError):
        # What the synchronous route answers with a 400 (clip not found, task busy, source gone, ...).
        return {"code": "INVALID_EDIT", "message": str(exc) or GENERIC_FAILURE}
    return {"code": "CLIP_JOB_FAILED", "message": GENERIC_FAILURE}


async def _notify_task_page(task_id: str, job_id: str, kind: str, status: str) -> None:
    """Tell open task pages (SSE ``clip_job`` event on the task's progress channel). Best effort.

    Not a ``progress`` event: it must never look like the task's own status, which would close the stream.
    """
    client = _fx_clip_job_redis()
    try:
        await client.publish(f"progress:{task_id}", json.dumps({
            "task_id": task_id,
            "event_type": "clip_job",
            "job_id": job_id,
            "kind": kind,
            "clip_job_status": status,
        }))
    except Exception as exc:  # noqa: BLE001 - the poll route is the source of truth
        logger.debug("Could not publish clip job %s event: %s", job_id, exc)
    finally:
        try:
            await client.aclose()
        except Exception:  # noqa: BLE001
            pass


async def _load_redis_source_metadata(task_id: str) -> Dict[str, Any]:
    """The Redis copy of the task's source metadata (older tasks keep it only there)."""
    client = _fx_clip_job_redis()
    try:
        raw = await client.get(f"task_source:{task_id}")
        parsed = json.loads(raw) if raw else {}
        return parsed if isinstance(parsed, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not read the source metadata of task %s from Redis: %s", task_id, exc)
        return {}
    finally:
        await client.aclose()


async def _save_redis_source_metadata(task_id: str, metadata: Dict[str, Any]) -> None:
    client = _fx_clip_job_redis()
    try:
        await client.set(f"task_source:{task_id}", json.dumps(metadata), ex=TASK_SOURCE_TTL_SECONDS)
    except Exception as exc:  # noqa: BLE001 - PostgreSQL holds the authoritative copy
        logger.warning("Unable to save task source metadata for %s: %s", task_id, exc)
    finally:
        await client.aclose()


async def _run_settings(service: Any, task_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    fallback = await _load_redis_source_metadata(task_id)
    metadata = await service.rerender_task_settings(
        task_id,
        args.get("font_family"),
        args.get("font_size"),
        args.get("font_color"),
        args.get("caption_template") or "default",
        args.get("cleanup_settings"),
        fallback_metadata=fallback,
    )
    await _save_redis_source_metadata(task_id, metadata)
    return {"task": await service.get_task_with_clips(task_id) or {}, "message": "Task settings updated"}


async def _run_captions(service: Any, task_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    clip = await service.update_clip_captions(
        task_id,
        args["clip_id"],
        str(args.get("caption_text") or ""),
        str(args.get("position") or "bottom"),
        [str(word) for word in args.get("highlight_words") or []],
        font_size=args.get("font_size"),
        position_y=args.get("position_y"),
    )
    return {"clip": clip}


async def _run_trim(service: Any, task_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    clip = await service.trim_clip(
        task_id, args["clip_id"], float(args.get("start_offset") or 0.0), float(args.get("end_offset") or 0.0)
    )
    return {"clip": clip}


async def _run_split(service: Any, task_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    return await service.split_clip(task_id, args["clip_id"], float(args["split_time"]))


async def _run_merge(service: Any, task_id: str, args: Dict[str, Any]) -> Dict[str, Any]:
    return await service.merge_clips(task_id, [str(clip_id) for clip_id in args.get("clip_ids") or []])


RUNNERS: Dict[str, Callable[[Any, str, Dict[str, Any]], Awaitable[Dict[str, Any]]]] = {
    "settings": _run_settings,
    "captions": _run_captions,
    "trim": _run_trim,
    "split": _run_split,
    "merge": _run_merge,
}
assert set(RUNNERS) == set(FX_CLIP_JOB_KINDS)


async def _clip_count(service: Any, task_id: str, kind: str, args: Dict[str, Any]) -> int:
    """Clips this job renders (the budget and the progress estimate)."""
    if kind == "settings":
        try:
            return len(await service.clip_repo.get_clips_by_task(service.db, task_id) or [])
        except Exception:  # noqa: BLE001 - the estimate only
            return 1
    if kind == "merge":
        return len(args.get("clip_ids") or []) or 1
    return 2 if kind == "split" else 1


def _service_factory(db: Any) -> Any:
    from .task_service import TaskService

    return TaskService(db)


def _session_factory():
    from ..database import AsyncSessionLocal

    return AsyncSessionLocal()


async def run_fx_clip_job(
    job: Dict[str, Any],
    *,
    session_factory: Callable[[], Any] = _session_factory,
    service_factory: Callable[[Any], Any] = _service_factory,
) -> Dict[str, Any]:
    """Run one queued clip edit and record its outcome. Returns the final Redis record (tests read it).

    However it ends (done, failed, skipped, cancelled with the worker), the task may take its next clip job."""
    try:
        return await _run_fx_clip_job(job, session_factory=session_factory, service_factory=service_factory)
    finally:
        try:
            await asyncio.shield(
                release_fx_clip_job_marker(str(job.get("task_id") or ""), str(job.get("job_id") or ""))
            )
        except BaseException:  # noqa: BLE001 - cancelled again; the marker points at a failed record or expires
            pass


async def _run_fx_clip_job(
    job: Dict[str, Any],
    *,
    session_factory: Callable[[], Any],
    service_factory: Callable[[Any], Any],
) -> Dict[str, Any]:
    job_id = str(job.get("job_id") or "")
    task_id = str(job.get("task_id") or "")
    kind = str(job.get("kind") or "")
    args = dict(job.get("args") or {})
    stored = await read_fx_clip_job_state(job_id) or {}
    base = {
        "job_id": job_id,
        "task_id": task_id,
        "user_id": job.get("user_id"),
        "kind": kind,
        "queued_at": stored.get("queued_at") or time.time(),
        **({"clip_ids": stored["clip_ids"]} if stored.get("clip_ids") else {}),
    }
    if stored.get("status") in ("done", "failed"):
        # A duplicate delivery of a finished job: never render twice.
        logger.info("FX clip job %s already %s; skipping", job_id, stored["status"])
        return stored
    if await claim_fx_clip_job(job_id, FX_CLIP_JOB_STARTED) == FX_CLIP_JOB_CLOSED:
        # It waited so long that the status route reported it as failed and the user was told to retry:
        # running it now would apply the edit a second time.
        logger.info("FX clip job %s was given up on while queued; skipping", job_id)
        return {**base, "status": "failed", "progress": 0.0, "error": stored.get("error") or {"code": "WORKER_UNAVAILABLE", "message": "No worker picked up this edit. Please try again."}}
    if kind not in RUNNERS or not task_id:
        state = {**base, "status": "failed", "progress": 0.0,
                 "error": {"code": "INVALID_EDIT", "message": f"Unknown clip edit {kind!r}"}}
        await write_fx_clip_job_state(job_id, state)
        return state

    started = time.time()
    running = {**base, "status": "running", "progress": PROGRESS_START, "started_at": started}
    await write_fx_clip_job_state(job_id, running)
    await _notify_task_page(task_id, job_id, kind, "running")
    logger.info("FX clip job %s (%s) started for task %s", job_id, kind, task_id)

    clip_count = 1
    heartbeat: Optional[asyncio.Task] = None

    async def beat() -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            try:
                await write_fx_clip_job_state(job_id, {
                    **running, "progress": estimated_progress(time.time() - started, clip_count),
                })
            except Exception as exc:  # noqa: BLE001 - a missed beat only delays the estimate
                logger.debug("Clip job %s heartbeat failed: %s", job_id, exc)

    final: Dict[str, Any]
    try:
        async with session_factory() as db:
            # The edit holds its row lock, idle, for the whole render; this job's budget bounds it instead.
            allow_long_edit(db)
            service = service_factory(db)
            clip_count = await _clip_count(service, task_id, kind, args)
            budget = job_budget_seconds(clip_count)
            running = {**running, "budget_seconds": budget}
            await write_fx_clip_job_state(job_id, running)
            heartbeat = asyncio.create_task(beat())
            result = await asyncio.wait_for(RUNNERS[kind](service, task_id, args), timeout=budget)
        final = {**base, "status": "done", "progress": 1.0, "started_at": started, "result": result}
    except asyncio.CancelledError:
        # The worker is shutting down (or ARQ gave up): the edit's transaction rolled back, the clip is unchanged.
        if heartbeat is not None:
            heartbeat.cancel()
        try:
            await asyncio.shield(write_fx_clip_job_state(job_id, {
                **base, "status": "failed", "progress": 0.0, "started_at": started, "error": dict(WORKER_STOPPED_ERROR),
            }))
        except BaseException:  # noqa: BLE001
            pass
        raise
    except BaseException as exc:  # noqa: BLE001 - every failure is recorded for the poller
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        error = _error_for(exc)
        log = logger.warning if error["code"] in ("INVALID_EDIT", "YOUTUBE_GEO_RESTRICTED") else logger.exception
        log("FX clip job %s (%s) for task %s failed: %s", job_id, kind, task_id, exc)
        final = {**base, "status": "failed", "progress": 0.0, "started_at": started, "error": error}
    finally:
        if heartbeat is not None:
            heartbeat.cancel()
            try:
                await heartbeat
            except BaseException:  # noqa: BLE001 - cancelled
                pass

    await write_fx_clip_job_state(job_id, final)
    await _notify_task_page(task_id, job_id, kind, final["status"])
    logger.info(
        "FX clip job %s (%s) for task %s %s in %.1fs", job_id, kind, task_id, final["status"], time.time() - started
    )
    return final


async def fx_clip_job(ctx: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
    """ARQ entry point (video worker queue, one try). ``job = {"job_id", "task_id", "user_id", "kind", "args"}``."""
    task_id = str((job or {}).get("task_id") or "")
    set_trace_id(f"task-{task_id}")
    if (job or {}).get("user_id"):
        set_user_id(str(job["user_id"]))
    final = await run_fx_clip_job(dict(job or {}))
    return {"job_id": final.get("job_id"), "status": final.get("status")}
