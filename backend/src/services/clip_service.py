"""Clip editing and regeneration operations shared by TaskService.

The mixin uses TaskService's repositories, configuration, and cache key contract.
"""

from ..repositories.edit_transaction import serialized_task_edit
from typing import Dict, Any, Optional
from dataclasses import dataclass
from uuid import uuid4
import logging
from pathlib import Path
import copy
import json
import tempfile
import time

import redis.asyncio as redis

from ..clip_editor import (
    edited_caption_words,
    trim_clip_file,
    split_clip_file,
    merge_clip_files,
    overlay_custom_captions,
)
from ..video_utils import (
    VALID_OUTPUT_FORMATS,
    create_optimized_clip,
    load_cached_transcript_data,
    parse_timestamp_to_seconds,
    save_fx_render,
)
from ..stream_transfer import run_stream_download
from ..utils.async_helpers import run_in_thread
from ..clip_duration import clip_duration, normalize_clip_length
from ..media.common import MAX_RENDERED_CLIP_SECONDS
from ..clip_cleanup import normalize_clip_cleanup_settings
from ..broll import broll_overlays_for, shift_broll_placements
from ..clip_source_map import (
    normalize_source_ranges,
    load_clip_broll,
    load_clip_source_ranges,
    save_clip_broll,
    save_clip_source_ranges,
    load_clip_output_offsets,
    slice_source_timeline,
    save_clip_caption_settings,
    load_clip_caption_settings,
    source_range_bounds,
    split_source_ranges,
    trim_source_ranges,
)

from ..media_storage import MediaStorage
from ..youtube_source import (
    load_time_map,
    local_ranges_for,
    localize_segment,
    ranges_from_segments,
    restore_clip_timeline,
)
from ..media.ffmpeg import cap_video_short_side, ffprobe_duration, rendered_clip_duration
from ..media.render_ranges import burned_hook_title, point_labels_for
from ..source_range import (
    METADATA_KEY as SOURCE_RANGE_KEY,
    SourceRange,
    link_with_part_words,
    media_duration,
    range_transcript,
    release_part_link,
)
from ..stream_source import download_stream_sections
from .video_service import fx_lines_for_ranges, fx_words_for_ranges, resolve_upload_for_edit
from ..config import get_config
from ..repositories.task_repository import KEEP_EDIT_STYLE
from ..clip_source_map import (
    FX_LEVELS,
    MIN_RANGE_SECONDS,
    load_clip_edit_fx,
    save_clip_edit_fx,
)
from ..edit_fx.beats import words_with_timing
from ..edit_fx.contracts import EditBeats, FxPlan
from ..edit_fx.jobs import FxJob, fx_active
from ..edit_fx.mapping import BEAT_ID_MAX
from ..edit_fx.reactions import ensure_reaction_file
from ..edit_fx.settings import read_edit_style
from ..edit_fx.shift import (
    _prefixed as _prefixed_id,
    concat_plans,
    concat_warps,
    shift_beats,
    shift_face_track,
    shift_plan,
)
from ..edit_fx.warp import Warp, identity_warp, slice_warp, warp_from_json
from ..emoji_captions import normalize_token
from ..media.fx_chain import FxRenderInput

logger = logging.getLogger(__name__)


def fill_output_starts(items: Any, group_output_starts: list) -> None:
    """Set each recap point / trailer beat's start on the rendered clip timeline."""
    for item in items if isinstance(items, list) else []:
        index = item.get("range_index") if isinstance(item, dict) else None
        if isinstance(index, int) and 0 <= index < len(group_output_starts):
            start = group_output_starts[index]
            item["output_start"] = round(float(start), 3) if start is not None else None


# Keys that describe a clip's planned cuts; a clip holding other footage drops them.
PLANNED_META_KEYS = ("planned", "ranges", "junction_fades", "max_seconds", "points", "beats")
# Rounding slack when checking that a played piece lies inside a planned range.
PLAN_EDGE_TOLERANCE_SECONDS = 0.05


def _overlap(piece: tuple, planned_range: tuple) -> float:
    return min(float(piece[1]), float(planned_range[1])) - max(float(piece[0]), float(planned_range[0]))


def _follows_plan(pieces: list, planned: list) -> bool:
    """Every played piece comes from one of the planned ranges (trims and splits only).

    A merge adds footage the plan never chose; such a clip is re-cut like any
    edited clip instead of being pulled back onto its first clip's plan.
    """
    return bool(planned) and all(
        any(
            float(piece[0]) >= float(start) - PLAN_EDGE_TOLERANCE_SECONDS
            and float(piece[1]) <= float(end) + PLAN_EDGE_TOLERANCE_SECONDS
            for start, end in planned
        )
        for piece in pieces
    )


def _unplanned_meta(meta: Dict[str, Any]) -> Dict[str, Any]:
    """Workflow metadata of a clip that no longer plays its planned cuts (series title, part title, ...)."""
    return {key: value for key, value in meta.items() if key not in PLANNED_META_KEYS}


def _range_groups(pieces: list, planned: list) -> list[int]:
    """Index of the planned range each played piece came from.

    Cleanup only cuts inside a planned range, so a piece overlaps the range
    it was cut from most; trailer ranges may play out of source order.
    """
    return [
        max(range(len(planned)), key=lambda index: _overlap(piece, planned[index]))
        for piece in pieces
    ]


def _narrow_planned_meta(meta: Dict[str, Any], pieces: list, offsets: list) -> Optional[Dict[str, Any]]:
    """Workflow metadata for a clip that now plays only ``pieces`` (after a trim or split).

    ``ranges`` keeps the planned ranges these pieces still cover, trimmed to
    them, with the fades between them. Recap points and trailer beats keep
    their place on the new timeline (``output_start``) or go with their range.
    None when the pieces include footage outside the plan (a merged clip).
    """
    planned = normalize_source_ranges(meta.get("ranges"))
    if not planned or not pieces:
        return dict(meta)
    if not _follows_plan(pieces, planned):
        return None
    fades = [float(value) for value in meta.get("junction_fades") or []]
    groups = _range_groups(pieces, planned)
    kept: list[int] = []
    narrowed: list[tuple[float, float]] = []
    starts: Dict[int, float] = {}
    for group, (start, end) in enumerate(planned):
        members = [index for index, owner in enumerate(groups) if owner == group]
        if not members:
            continue
        kept.append(group)
        narrowed.append((
            max(start, min(pieces[index][0] for index in members)),
            min(end, max(pieces[index][1] for index in members)),
        ))
        starts[group] = max(0.0, float(offsets[members[0]])) if members[0] < len(offsets) else 0.0
    result = dict(meta)
    result["ranges"] = [[round(start, 6), round(end, 6)] for start, end in narrowed]
    result["junction_fades"] = [
        fades[kept[index] - 1] if kept[index] - 1 == kept[index - 1] and kept[index] <= len(fades) else 0.0
        for index in range(1, len(kept))
    ]
    for key in ("points", "beats"):
        if isinstance(meta.get(key), list):
            result[key] = [
                {
                    **item,
                    "range_index": kept.index(item["range_index"]),
                    "output_start": round(starts[item["range_index"]], 3),
                }
                for item in meta[key]
                if isinstance(item, dict) and item.get("range_index") in kept
            ]
    return result


# ----------------------------------------------------------------------------- FX clip jobs (§13.0)
# Edits that would re-render a dynamic-edits clip (deno chain, face track, maybe a beat LLM call) never run inside an
# API request. The route stores what it stores today, queues ``fx_clip_job`` on the video worker and answers 202.
# State lives in Redis ``fx_clip_job:{job_id}`` (24 h) and is read by ``GET /tasks/{id}/clip-jobs/{job_id}``. The
# worker side (services/fx_clip_jobs.py) updates status, progress, error and result in the same record.

FX_CLIP_JOB_FUNCTION = "fx_clip_job"
FX_CLIP_JOB_KINDS: tuple[str, ...] = ("settings", "captions", "trim", "split", "merge")
FX_CLIP_JOB_TTL_SECONDS = 24 * 60 * 60
FX_CLIP_JOB_STATUSES: tuple[str, ...] = ("queued", "running", "done", "failed")
# A clip job nobody picked up in this long has no worker that knows it (§5.2).
FX_CLIP_JOB_QUEUE_TIMEOUT_SECONDS = 15 * 60
# One job per task at a time: ``fx_clip_job:task:{task_id}`` names the job in flight. It is cleared when the job
# ends in any way; this TTL (longer than a job can wait and run) only collects one whose worker died.
FX_CLIP_JOB_MARKER_TTL_SECONDS = 4 * 60 * 60
_MARKER_RELEASE = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) end return 0"
_MARKER_TAKE_OVER = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "redis.call('set', KEYS[1], ARGV[2], 'EX', ARGV[3]) return 1 end return 0"
)


def fx_clip_job_key(job_id: str) -> str:
    return f"fx_clip_job:{job_id}"


def fx_clip_job_marker_key(task_id: str) -> str:
    return f"fx_clip_job:task:{task_id}"


class FxClipJobQueueError(RuntimeError):
    """The edit was stored, but its FX clip job could not be queued (Redis or the queue is down)."""


class FxClipJobInProgress(RuntimeError):
    """Another clip job is queued or running for this task; nothing was queued (the route answers 409)."""

    def __init__(self, job_id: str, kind: Optional[str] = None):
        super().__init__("Another edit of this project's clips is still rendering")
        self.job_id = job_id
        self.kind = kind

    def detail(self) -> Dict[str, Any]:
        return {
            "code": "CLIP_JOB_IN_PROGRESS",
            "message": "Another edit of this project's clips is still rendering. Wait for it to finish, then try again.",
            "job_id": self.job_id,
            **({"kind": self.kind} if self.kind else {}),
        }


@dataclass(frozen=True)
class FxClipJobRequest:
    """The route's go-ahead to queue an FX clip job: who asked, and the queue (tests pass a fake)."""

    user_id: str
    queue: Any = None


@dataclass(frozen=True)
class QueuedClipJob:
    """An edit accepted for the video worker. ``body()`` is the route's 202 response."""

    job_id: str
    kind: str
    clip_ids: tuple[str, ...] = ()

    def body(self) -> Dict[str, Any]:
        """``{"status": "queued", "job_id", "kind"}``, plus ``clip_ids`` (the clips being re-rendered) for clip edits."""
        body: Dict[str, Any] = {"status": "queued", "job_id": self.job_id, "kind": self.kind}
        if self.clip_ids:
            body["clip_ids"] = list(self.clip_ids)
        return body


def _fx_clip_job_redis() -> redis.Redis:
    config = get_config()
    return redis.Redis(
        host=config.redis_host,
        port=config.redis_port,
        password=config.redis_password,
        decode_responses=True,
    )


async def write_fx_clip_job_state(job_id: str, state: Dict[str, Any]) -> None:
    """Replace the job's Redis record (TTL 24 h). Shared with the worker side."""
    client = _fx_clip_job_redis()
    try:
        await client.set(
            fx_clip_job_key(job_id),
            json.dumps({**state, "updated_at": time.time()}, sort_keys=True, default=str),
            ex=FX_CLIP_JOB_TTL_SECONDS,
        )
    finally:
        await client.aclose()


FX_CLIP_JOB_STARTED = "started"
FX_CLIP_JOB_CLOSED = "closed"
FX_CLIP_JOB_STARTED_CLAIM_SECONDS = 120


async def claim_fx_clip_job(job_id: str, holder: str) -> str:
    """Who decides a queued job's fate: the worker that runs it (``started``) or the status route that gave up
    on it (``closed``). The first caller wins and every caller gets the winner back, so a job reported as failed
    for waiting too long is never run afterwards, and a job a worker has taken is never reported as failed."""
    client = _fx_clip_job_redis()
    try:
        key = f"{fx_clip_job_key(job_id)}:claim"
        # A worker's claim only has to last until it has written "running", a moment later. Should the worker
        # die in between, the claim lapses and the job can be given up on like any other that never started.
        ttl = FX_CLIP_JOB_STARTED_CLAIM_SECONDS if holder == FX_CLIP_JOB_STARTED else FX_CLIP_JOB_TTL_SECONDS
        await client.set(key, holder, nx=True, ex=ttl)
        return str(await client.get(key) or holder)
    finally:
        await client.aclose()


async def read_fx_clip_job_state(job_id: str) -> Optional[Dict[str, Any]]:
    """The job's Redis record, or None when it is unknown or expired."""
    client = _fx_clip_job_redis()
    try:
        raw = await client.get(fx_clip_job_key(job_id))
    finally:
        await client.aclose()
    if not raw:
        return None
    try:
        state = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return state if isinstance(state, dict) else None


def _fx_clip_job_waiting(state: Optional[Dict[str, Any]], now: float) -> Optional[bool]:
    """Whether the job behind a task's marker still blocks new edits, by the status route's own rules.

    True: queued or running and not stale. False: finished, unknown, or a running job ``stale_job_error`` gives up
    on. None: a queued job that waited past its limit; it stops blocking only once it is closed, so that no worker
    starts it afterwards.
    """
    from .fx_clip_jobs import stale_job_error

    if not state or state.get("status") not in ("queued", "running"):
        return False
    stale = stale_job_error(state, now)
    if state["status"] == "running":
        return stale is None
    try:
        queued_at = float(state.get("queued_at") or 0.0)
    except (TypeError, ValueError):
        queued_at = 0.0
    if stale or (queued_at and now - queued_at > FX_CLIP_JOB_QUEUE_TIMEOUT_SECONDS):
        return None
    return True


async def fx_clip_job_in_flight(task_id: str) -> Optional[Dict[str, Any]]:
    """The record of the task's clip job that is queued or running, or None. Reads only; ``enqueue_fx_clip_job``
    decides atomically. For a route that stores something before it queues."""
    client = _fx_clip_job_redis()
    try:
        holder = await client.get(fx_clip_job_marker_key(task_id))
    finally:
        await client.aclose()
    if not holder:
        return None
    state = await read_fx_clip_job_state(str(holder))
    return state if _fx_clip_job_waiting(state, time.time()) else None


async def release_fx_clip_job_marker(task_id: str, job_id: str) -> None:
    """Let the task take its next clip job, if ``job_id`` still holds the marker. Best effort: a marker left
    behind points at a finished or stale record, which the next enqueue replaces."""
    if not task_id or not job_id:
        return
    try:
        client = _fx_clip_job_redis()
        try:
            await client.eval(_MARKER_RELEASE, 1, fx_clip_job_marker_key(task_id), job_id)
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not clear the clip job marker of task %s: %s", task_id, exc)


async def _claim_task_for_fx_clip_job(task_id: str, job_id: str) -> None:
    """Make ``job_id`` the task's one clip job in flight, or raise ``FxClipJobInProgress`` naming the other."""
    key = fx_clip_job_marker_key(task_id)
    holder = ""
    for _attempt in range(3):
        client = _fx_clip_job_redis()
        try:
            if await client.set(key, job_id, nx=True, ex=FX_CLIP_JOB_MARKER_TTL_SECONDS):
                return
            holder = str(await client.get(key) or "")
        finally:
            await client.aclose()
        if not holder:
            continue  # cleared between the two calls
        state = await read_fx_clip_job_state(holder)
        waiting = _fx_clip_job_waiting(state, time.time())
        if waiting is None:
            # Waited too long in the queue: close it like the status route does. A worker that took it meanwhile
            # keeps it, and it blocks after all.
            if await claim_fx_clip_job(holder, FX_CLIP_JOB_CLOSED) == FX_CLIP_JOB_CLOSED:
                await write_fx_clip_job_state(holder, {
                    **(state or {}), "status": "failed", "progress": 0.0,
                    "error": {"code": "WORKER_UNAVAILABLE", "message": "No worker picked up this edit. Please try again."},
                })
                waiting = False
            else:
                waiting = True
        if waiting:
            raise FxClipJobInProgress(holder, (state or {}).get("kind"))
        client = _fx_clip_job_redis()
        try:
            if await client.eval(_MARKER_TAKE_OVER, 1, key, holder, job_id, FX_CLIP_JOB_MARKER_TTL_SECONDS):
                return
        finally:
            await client.aclose()
    raise FxClipJobInProgress(holder)


async def enqueue_fx_clip_job(
    *,
    task_id: str,
    user_id: str,
    kind: str,
    args: Dict[str, Any],
    queue: Any = None,
    clip_ids: Optional[list[str]] = None,
) -> QueuedClipJob:
    """Queue ``fx_clip_job(ctx, job)`` on the video worker with the §13.0 payload.

    ``job = {"job_id", "task_id", "user_id", "kind", "args"}``; ``args`` are the ClipService method's arguments.
    The Redis record is written before the job is queued, so a poll right after the 202 finds it. WP18 runs the
    job (services/fx_clip_jobs.py, registered in workers/tasks.py); until a worker picks it up the record stays
    ``queued`` and the status route reports ``WORKER_UNAVAILABLE`` after 15 minutes. ``clip_ids`` (clip edits) are
    the clips the job re-renders; they are kept in the record and echoed in the 202 body.

    One job per task: while another is queued or running, nothing is queued and ``FxClipJobInProgress`` names it
    (two jobs would each hold a worker slot, and a double-click would apply a trim twice).
    """
    if kind not in FX_CLIP_JOB_KINDS:
        raise ValueError(f"Unknown FX clip job kind: {kind}")
    job_id = uuid4().hex
    await _claim_task_for_fx_clip_job(task_id, job_id)
    job = {"job_id": job_id, "task_id": task_id, "user_id": user_id, "kind": kind, "args": dict(args)}
    state = {
        "job_id": job_id,
        "task_id": task_id,
        "user_id": user_id,
        "kind": kind,
        "status": "queued",
        "progress": 0.0,
        "queued_at": time.time(),
        **({"clip_ids": list(clip_ids)} if clip_ids else {}),
    }
    try:
        await write_fx_clip_job_state(job_id, state)
    except BaseException:
        await release_fx_clip_job_marker(task_id, job_id)
        raise
    adapter = queue
    if adapter is None:
        from ..workers.job_queue import JobQueue

        adapter = JobQueue
    try:
        await adapter.enqueue_job(FX_CLIP_JOB_FUNCTION, job, _job_id=fx_clip_job_key(job_id))
    except BaseException:
        try:
            await write_fx_clip_job_state(job_id, {
                **state,
                "status": "failed",
                "error": {"code": "QUEUE_UNAVAILABLE", "message": "Could not queue the edit. Please try again."},
            })
        finally:
            await release_fx_clip_job_marker(task_id, job_id)
        raise
    logger.info("Queued FX clip job %s (%s) for task %s", job_id, kind, task_id)
    return QueuedClipJob(job_id=job_id, kind=kind, clip_ids=tuple(clip_ids or ()))


def clip_has_fx(clip: Optional[Dict[str, Any]]) -> bool:
    """Whether a clip row was rendered with dynamic edits (``fx_meta`` is set on every FX render and edit).

    Routes use it to send the clip's edits to ``fx_clip_job`` instead of rendering in the request (§13.0).
    """
    return bool(clip and clip.get("fx_meta"))


# ----------------------------------------------------------------------------- FX clip edits (§13.1-13.3)
# Trim, split and merge cut the rendered mp4 (F time), so the burned effects survive and the sidecar is shifted to
# describe the new file. Cut points arrive in F and are mapped to E with the clip's *applied* warp (AI-H4, AR-M15);
# source ranges, offsets, B-roll, plans, beats and face tracks all live in E. A clip whose plan has a length-dependent
# global (progress bar, loop ending, music bed) is re-rendered from source instead (AI-H6).

# Seconds of slack around an accent cue when checking that its words are still in the edited captions.
ACCENT_WORD_SLACK_SECONDS = 0.25
# The shortest trimmed piece trim_clip_file keeps.
MIN_TRIM_SECONDS = 0.1
# split_clip_file keeps at least this much on each side of the cut.
SPLIT_EDGE_SECONDS = 0.2
CAPTION_POSITION_Y = {"top": 0.18, "middle": 0.52, "bottom": 0.78}


@dataclass
class _FxClipState:
    """What a clip's sidecar says about its dynamic edits (lenient read; any field may be missing)."""

    style: Dict[str, Any]
    plan: Optional[FxPlan]
    beats: Optional[EditBeats]
    face_track: Optional[Dict[str, Any]]
    fx_level: Optional[str]
    warp: Warp

    def edit_duration(self, out_duration: float) -> float:
        """E length of the clip whose mp4 lasts ``out_duration`` (F)."""
        if self.plan is not None:
            return float(self.plan.edit_duration)
        return max(0.0, self.warp.to_edit(out_duration))


def _load_fx_state(path: Path) -> Optional[_FxClipState]:
    """The clip's FX sidecar, or None for a legacy clip. A corrupt field reads as missing, never as "delete"."""
    sidecar = load_clip_edit_fx(path)
    if sidecar is None:
        return None
    plan = beats = None
    try:
        plan = FxPlan.model_validate(sidecar["plan"]) if sidecar["plan"] else None
    except Exception as exc:  # noqa: BLE001 - lenient (AI-M9)
        logger.warning("fx_sidecar_corrupt clip=%s field=edit_fx error=%s", path.name, exc)
    try:
        beats = EditBeats.model_validate(sidecar["beats"]) if sidecar["beats"] else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("fx_sidecar_corrupt clip=%s field=edit_beats error=%s", path.name, exc)
    return _FxClipState(
        style=dict(sidecar["style"] or {}),
        plan=plan,
        beats=beats,
        face_track=sidecar["face_track"],
        fx_level=sidecar["fx_level"],
        warp=warp_from_json(sidecar["applied_warp"], plan.edit_duration if plan is not None else None),
    )


def _needs_source_render(plan: Optional[FxPlan]) -> bool:
    """A cut of the mp4 would break these (a bar that never completes, a broken loop, a bed cut mid-phrase)."""
    return plan is not None and (plan.progress_bar or plan.loop_ending != "none" or plan.music is not None)


def _fx_level(state: _FxClipState, clip: Dict[str, Any]) -> str:
    level = state.fx_level or ((clip.get("fx_meta") or {}).get("fx_level"))
    return level if level in FX_LEVELS else "full"


def _fx_meta(plan: FxPlan, level: str) -> Dict[str, Any]:
    """``generated_clips.fx_meta`` for an edited FX clip (the same keys ``save_fx_render`` returns)."""
    return {
        "fx_level": level,
        "timing": plan.timing,
        "intensity": plan.style.intensity,
        "template": plan.template,
    }


def _save_cut_fx(
    path: Path,
    state: _FxClipState,
    *,
    plan: Optional[FxPlan],
    beats: Optional[EditBeats],
    face_track: Optional[Dict[str, Any]],
    level: str,
    warp: Warp,
) -> None:
    """The FX keys of an mp4 cut from (or concatenated out of) FX clips. A clip whose plan was unreadable keeps no
    FX keys rather than a plan for another timeline."""
    if plan is None:
        logger.warning("fx_cut_without_plan clip=%s; the cut keeps its burned effects only", path.name)
        return
    save_clip_edit_fx(
        path,
        style=state.style,
        beats=beats.model_dump(mode="json") if beats is not None else None,
        plan=plan.model_dump(mode="json"),
        face_track=face_track,
        fx_level=level,
        applied_warp=warp.to_json(),
    )


def _blank_plan(template: FxPlan, edit_duration: float) -> FxPlan:
    """A cue-free stand-in for a legacy part of a merge (its pixels already hold whatever it shows)."""
    return template.model_copy(update={"cues": (), "edit_duration": float(edit_duration)})


_TIMING_RANK = {"none": 0, "approx": 1, "word": 2}


def _concat_beats(parts: list[tuple[Optional[EditBeats], float]]) -> Optional[EditBeats]:
    """Beats of merged clips in play order (E cursors), ids prefixed like ``concat_plans`` prefixes cue beats.

    A (source) times and word identities stay, so a later re-render re-anchors them; both hashes are cleared because
    the timeline is new.
    """
    present = [beats for beats, _ in parts if beats is not None]
    if not present:
        return None
    first = present[0]
    cursor = 0.0
    items: list[dict] = []
    reactions: list[dict] = []
    hook_end = None
    for index, (beats, duration) in enumerate(parts):
        if beats is not None:
            prefix = f"m{index}:"
            for beat in beats.beats:
                items.append({
                    **beat.model_dump(),
                    "id": _prefixed_id(beat.id, prefix, BEAT_ID_MAX),
                    "start": beat.start + cursor,
                    "end": beat.end + cursor,
                })
            reactions += [{**reaction.model_dump(), "at": reaction.at + cursor} for reaction in beats.reactions]
            if index == 0:
                hook_end = beats.hook_end
        cursor += max(0.0, float(duration))
    items.sort(key=lambda item: (item["start"], item["id"]))
    return EditBeats.model_validate({
        **first.model_dump(),
        "words_hash": "",
        "timeline_hash": "",
        "hook_end": hook_end,
        "sensitivity": "serious" if any(beats.sensitivity == "serious" for beats in present) else "normal",
        "timing": min((beats.timing for beats in present), key=lambda timing: _TIMING_RANK.get(timing, 0)),
        "beats": items[:200],
        "reactions": reactions[:3],
    })


def _concat_face_tracks(parts: list[tuple[Optional[Dict[str, Any]], float]]) -> Optional[Dict[str, Any]]:
    """Face tracks of merged clips on the merged E timeline, or None (recomputed later) unless every part has one in
    the same space and rate."""
    tracks = [track for track, _ in parts]
    if not tracks or any(not isinstance(track, dict) for track in tracks):
        return None
    first = tracks[0]
    if any(track.get("fps") != first.get("fps") or track.get("space") != first.get("space") for track in tracks):
        return None
    cursor = 0.0
    samples: list = []
    try:
        for track, duration in parts:
            for sample in track.get("samples") or ():
                t = float(sample[0])
                if 0.0 <= t < float(duration):
                    samples.append([round(t + cursor, 3), sample[1]])
            cursor += max(0.0, float(duration))
    except (TypeError, ValueError, IndexError):
        return None
    return {**first, "samples": samples}


def _drop_stale_accents(plan: FxPlan, edited: list[dict], spoken: list[dict]) -> FxPlan:
    """Accent cues whose words are no longer in the edited captions under them are dropped (§13.3).

    A cue survives when one of its displayed words is still captioned around it, or when the captions under it were
    not edited at all (a censored or formatted display text, "$1M" for "a million").
    """

    def tokens(words: list[dict], start: float, end: float) -> set[str]:
        lo, hi = start - ACCENT_WORD_SLACK_SECONDS, end + ACCENT_WORD_SLACK_SECONDS
        return {
            normalize_token(str(word.get("text", "")))
            for word in words
            if float(word.get("end", 0.0)) >= lo and float(word.get("start", 0.0)) <= hi
        } - {""}

    kept = []
    dropped = 0
    for cue in plan.cues:
        if cue.kind == "accent" and cue.text.strip():
            wanted = {normalize_token(part) for part in cue.text.split()} - {""}
            captioned = tokens(edited, cue.start, cue.end)
            said = tokens(spoken, cue.start, cue.end)
            if wanted and not (wanted & captioned) and not (said and said <= captioned):
                dropped += 1
                continue
        kept.append(cue)
    if dropped:
        logger.info("fx_caption_edit dropped %d accent cue(s) whose words were edited out", dropped)
        return plan.model_copy(update={"cues": tuple(kept)})
    return plan


def _stored_caption_override(settings: Optional[Dict[str, Any]], task: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Placement from an earlier caption edit (only caption edits store ``highlight_words``), kept by a re-render."""
    if not isinstance(settings, dict) or "highlight_words" not in settings:
        return None
    override: Dict[str, Any] = {}
    if settings.get("position_y") is not None:
        override["position_y"] = float(settings["position_y"])
    font_size = settings.get("font_size") if settings.get("font_size") is not None else task.get("font_size")
    if font_size:
        override["font_size"] = int(font_size)
    if settings.get("highlight_words"):
        override["highlight_words"] = [str(word) for word in settings["highlight_words"]]
    return override or None


def _local_timeline(video_path: Path, ranges: list, offsets: list[float]) -> tuple[list, list[float]]:
    """``ranges`` (S) in the working file's own time with their E offsets. A section download can split a range
    in pieces; each piece keeps its place on the clip timeline, so the replayed plan stays aligned."""
    local: list = []
    local_offsets: list[float] = []
    for (start, end), offset in zip(ranges, offsets):
        cursor = float(offset)
        for piece_start, piece_end in local_ranges_for(video_path, [(start, end)]):
            if piece_end - piece_start > MIN_RANGE_SECONDS:
                local.append((piece_start, piece_end))
                local_offsets.append(cursor)
            cursor += piece_end - piece_start
    return local, local_offsets


def _transcript_fx_words(video_path: Path, ranges: list, offsets: list[float]) -> list[dict]:
    """Caption words for a replayed FX render: the transcript on the clip timeline, with ids."""
    transcript = load_cached_transcript_data(video_path)
    words, _ = words_with_timing(
        fx_words_for_ranges(transcript, ranges, offsets), fx_lines_for_ranges(transcript, ranges, offsets)
    )
    return words


async def _reaction_files(plan: FxPlan, scratch: Path) -> tuple[FxPlan, Dict[str, Path]]:
    """The plan's reaction GIFs as local files (never re-picked). A reaction that can't be fetched is dropped from
    the plan, so the sidecar describes what rendered."""
    files: Dict[str, Path] = {}
    for cue in plan.cues:
        if cue.kind == "reaction":
            path = await ensure_reaction_file(cue, scratch)
            if path is not None:
                files[cue.id] = path
    missing = {cue.id for cue in plan.cues if cue.kind == "reaction" and cue.id not in files}
    if missing:
        plan = plan.model_copy(update={"cues": tuple(cue for cue in plan.cues if cue.id not in missing)})
    return plan, files


def _split_workflow_fields(
    clip: Dict[str, Any],
    first_ranges: list,
    first_offsets: list,
    second_ranges: list,
    second_offsets: list,
) -> tuple[Optional[Dict[str, Any]], Dict[str, Any]]:
    """Workflow metadata of a split's halves: ``(first half's meta, the second half's create_clip fields)``.

    Both halves are still part N of the series (or a piece of the recap); each half's metadata describes only what
    it plays.
    """
    meta = clip.get("workflow_meta") or {}
    if not (clip.get("workflow_role") or meta):
        return None, {}

    def half_meta(ranges: list, offsets: list, half: int) -> Dict[str, Any]:
        narrowed = (
            _narrow_planned_meta(meta, ranges, offsets)
            if meta.get("planned")
            else dict(meta)
        )
        if narrowed is None:
            # A merged clip's half may be footage its plan never held.
            narrowed = _unplanned_meta(meta)
        narrowed["split"] = half
        return narrowed

    return half_meta(first_ranges, first_offsets, 1), {
        "workflow_role": clip.get("workflow_role"),
        "part_number": clip.get("part_number"),
        "workflow_meta": half_meta(second_ranges, second_offsets, 2),
    }


@dataclass
class _FxRender:
    """A clip re-rendered from source with a replayed plan; its sidecar is written."""

    path: Path
    duration: float
    fx_meta: Dict[str, Any]
    caption_y: Optional[float]


class ClipEditingMixin:
    @serialized_task_edit
    async def delete_clip(self, task_id: str, clip_id: str) -> None:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")
        await self.clip_repo.delete_clip(self.db, clip_id)
        await self.clip_repo.reorder_task_clips(self.db, task_id)

    async def update_task_settings(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
        apply_to_existing: bool,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        *,
        edit_style: Any = KEEP_EDIT_STYLE,
        fx_job: Optional["FxClipJobRequest"] = None,
    ) -> "Dict[str, Any] | QueuedClipJob":
        """Update task-level settings and optionally regenerate all clips.

        ``edit_style`` (``EditStyle.to_json()``) is stored only when passed. With ``fx_job`` (the route found FX
        clips or an FX target, §13.0) and ``apply_to_existing``, the settings are stored and committed first, then
        the re-render is queued as an ``fx_clip_job`` on the video worker and a ``QueuedClipJob`` is returned
        instead of the task. Without it the legacy synchronous path runs unchanged.
        """
        if fx_job is not None and apply_to_existing:
            # Before anything is stored: these settings would wait on the running job's lock and then be saved
            # with no re-render behind them.
            try:
                in_flight = await fx_clip_job_in_flight(task_id)
            except Exception as exc:  # noqa: BLE001 - Redis down: queueing below fails and answers as before
                logger.warning("Could not check for a clip job in flight on task %s: %s", task_id, exc)
                in_flight = None
            if in_flight:
                raise FxClipJobInProgress(str(in_flight.get("job_id") or ""), in_flight.get("kind"))
            await self._store_task_settings(
                task_id, font_family, font_size, font_color, caption_template, include_broll,
                edit_style=edit_style,
            )
            try:
                return await enqueue_fx_clip_job(
                    task_id=task_id,
                    user_id=fx_job.user_id,
                    kind="settings",
                    args={
                        "font_family": font_family,
                        "font_size": font_size,
                        "font_color": font_color,
                        "caption_template": caption_template,
                        "include_broll": include_broll,
                        "cleanup_settings": cleanup_settings,
                        "edit_style": None if edit_style is KEEP_EDIT_STYLE else dict(edit_style or {}),
                    },
                    queue=fx_job.queue,
                )
            except FxClipJobInProgress:
                raise
            except Exception as exc:
                # The settings are already committed; only the re-render is missing. Retrying is safe.
                logger.warning("Could not queue the settings re-render for task %s: %s", task_id, exc)
                raise FxClipJobQueueError("The settings were saved, but the clips could not be queued") from exc
        return await self._update_task_settings_now(
            task_id, font_family, font_size, font_color, caption_template, include_broll,
            apply_to_existing, cleanup_settings, edit_style=edit_style,
        )

    @serialized_task_edit
    async def _store_task_settings(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
        *,
        edit_style: Any = KEEP_EDIT_STYLE,
    ) -> None:
        """Store the settings under the task's edit lock; the commit happens before an FX job is queued."""
        await self.task_repo.update_task_settings(
            self.db,
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
            **({"edit_style": edit_style} if edit_style is not KEEP_EDIT_STYLE else {}),
        )

    @serialized_task_edit
    async def _update_task_settings_now(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        include_broll: bool,
        apply_to_existing: bool,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        *,
        edit_style: Any = KEEP_EDIT_STYLE,
    ) -> Dict[str, Any]:
        await self.task_repo.update_task_settings(
            self.db,
            task_id,
            font_family,
            font_size,
            font_color,
            caption_template,
            include_broll,
            **({"edit_style": edit_style} if edit_style is not KEEP_EDIT_STYLE else {}),
        )

        if apply_to_existing:
            await self.regenerate_all_clips_for_task(
                task_id,
                font_family,
                font_size,
                font_color,
                caption_template,
                cleanup_settings=cleanup_settings,
            )

        return await self.get_task_with_clips(task_id) or {}

    @serialized_task_edit
    async def rerender_task_settings(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        cleanup_settings: Optional[Dict[str, Any]] = None,
        *,
        fallback_metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """The worker half of a queued settings re-render (``fx_clip_job`` kind ``settings``, §13.0).

        The route stored the settings (and ``edit_style``) before queueing. This regenerates every clip and then
        records the cleanup settings in the task's source metadata, both under the task's edit lock, like the
        synchronous route does after its regeneration. Returns the metadata written (the job mirrors it to Redis).
        """
        await self.regenerate_all_clips_for_task(
            task_id, font_family, font_size, font_color, caption_template, cleanup_settings=cleanup_settings,
        )
        task = await self.task_repo.get_task_by_id(self.db, task_id) or {}
        stored = await self.task_repo.load_source_metadata(self.db, task_id) if self.db is not None else {}
        metadata = dict(stored or fallback_metadata or {})
        # Never write back a processing job id captured earlier: a resume may have reserved a newer attempt.
        metadata.pop("processing_job_id", None)
        source_url = metadata.get("url") or task.get("source_url")
        if isinstance(source_url, str) and source_url:
            metadata["url"] = source_url
        source_type = metadata.get("source_type") or task.get("source_type")
        if isinstance(source_type, str) and source_type:
            metadata["source_type"] = source_type
        output_format = metadata.get("output_format") or task.get("output_format")
        if output_format in VALID_OUTPUT_FORMATS:
            metadata["output_format"] = output_format
        add_subtitles = (
            metadata["add_subtitles"] if isinstance(metadata.get("add_subtitles"), bool) else task.get("add_subtitles")
        )
        if isinstance(add_subtitles, bool):
            metadata["add_subtitles"] = add_subtitles
        if cleanup_settings:
            metadata.update(cleanup_settings)
        if self.db is not None:
            await self.task_repo.save_source_metadata(self.db, task_id, metadata)
        return metadata


    @serialized_task_edit
    async def regenerate_all_clips_for_task(
        self,
        task_id: str,
        font_family: Optional[str],
        font_size: Optional[int],
        font_color: Optional[str],
        caption_template: str,
        cleanup_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Regenerate all clips in a task using existing segment boundaries."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        source_url = task.get("source_url")
        source_type = task.get("source_type")
        metadata = await self._load_task_source_settings(task_id)
        output_format = metadata.get("output_format", "vertical")
        add_subtitles = metadata.get("add_subtitles", True)
        cleanup_payload = cleanup_settings or {
            "cut_long_pauses": metadata.get("cut_long_pauses"),
            "pause_threshold_ms": metadata.get("pause_threshold_ms"),
            "remove_filler_words": metadata.get("remove_filler_words"),
            "filtered_words": metadata.get("filtered_words"),
        }
        normalized_cleanup_settings = normalize_clip_cleanup_settings(
            cleanup_payload.get("cut_long_pauses"),
            cleanup_payload.get("pause_threshold_ms"),
            cleanup_payload.get("remove_filler_words"),
            cleanup_payload.get("filtered_words"),
        )
        existing_cleanup_settings = normalize_clip_cleanup_settings(
            metadata.get("cut_long_pauses"),
            metadata.get("pause_threshold_ms"),
            metadata.get("remove_filler_words"),
            metadata.get("filtered_words"),
        )
        should_recompute_cleanup = (
            cleanup_settings is not None
            and normalized_cleanup_settings != existing_cleanup_settings
        )

        if not source_url or not source_type:
            raise ValueError("Task source URL is missing; cannot regenerate clips")

        clips = await self.clip_repo.get_clips_by_task(self.db, task_id)
        if not clips:
            return

        download_quality, output_short_side = await self._media_limits(task.get("user_id"))
        include_broll = bool(task.get("include_broll"))
        segments = []
        # Dynamic edits (§13.1): each clip's stored FX keys ride on its segment, and the renderer decides per clip
        # whether to reuse, re-resolve, re-anchor or plan. Legacy clips with a legacy target add nothing.
        style = read_edit_style(task.get("edit_style"))
        clips_with_fx = 0
        for clip in clips:
            fx_keys = await run_in_thread(self._segment_fx_keys, clip)
            clips_with_fx += bool(fx_keys)
            source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
            # Same timeline, same cutaways. Recomputed cleanup moves the words,
            # so those clips get a fresh plan instead.
            broll = (
                await run_in_thread(self._get_clip_broll, clip)
                if include_broll and not should_recompute_cleanup
                else []
            )
            meta = clip.get("workflow_meta") or {}
            if meta.get("planned") and normalize_source_ranges(meta.get("ranges")):
                planned_segment = await run_in_thread(
                    self._planned_regeneration_segment,
                    clip,
                    source_ranges,
                    broll,
                    should_recompute_cleanup,
                )
                if planned_segment is not None:
                    segments.append({**planned_segment, **fx_keys})
                    continue
            # Workflow clips re-cut like any edited clip (a merged part) keep
            # their role and part number; a series still needs every part.
            workflow_fields = (
                {
                    "workflow_role": clip.get("workflow_role"),
                    "part_number": clip.get("part_number"),
                    "workflow_meta": _unplanned_meta(meta),
                    "required": clip.get("workflow_role") in ("part", "recap", "trailer"),
                }
                if clip.get("workflow_role") or meta
                else {}
            )
            bounds = source_range_bounds(source_ranges)
            if bounds:
                start_time = self._seconds_to_mmss(bounds[0])
                end_time = self._seconds_to_mmss(bounds[1])
            else:
                start_time = clip["start_time"]
                end_time = clip["end_time"]

            segments.append(
                {
                    "start_time": start_time,
                    "end_time": end_time,
                    **(
                        {"source_ranges": source_ranges}
                        if should_recompute_cleanup
                        else {"keep_ranges": source_ranges}
                    ),
                    "text": clip.get("text") or "",
                    "relevance_score": clip.get("relevance_score", 0.5),
                    "reasoning": clip.get("reasoning")
                    or "Regenerated with updated settings",
                    "virality_score": clip.get("virality_score", 0),
                    "hook_score": clip.get("hook_score", 0),
                    "engagement_score": clip.get("engagement_score", 0),
                    "value_score": clip.get("value_score", 0),
                    "shareability_score": clip.get("shareability_score", 0),
                    "hook_type": clip.get("hook_type"),
                    "hook_title": clip.get("hook_title"),
                    "broll": broll,
                    **workflow_fields,
                    **fx_keys,
                }
            )

        # Every clip goes through the FX planner when the target is FX-active or a clip carries edits: an FX → legacy
        # switch must reach rule 1 (render legacy, clear the keys). Otherwise the call is the legacy call unchanged.
        fx_target = fx_active(style, caption_template)
        fx_kwargs: Dict[str, Any] = {}
        if fx_target or clips_with_fx:
            fx_kwargs["fx_jobs"] = {
                index: FxJob(style=style, task_id=task_id, clip_index=index) for index in range(len(segments))
            }
            if fx_target and should_recompute_cleanup:
                # Cleanup moved the words: the stored plan can't be reused verbatim (rule 2 off, re-anchor instead).
                for segment in segments:
                    segment["fx_cleanup_recomputed"] = True

        video_path: Path
        if source_type == "youtube":
            downloaded = await self.video_service.download_video(
                source_url,
                quality=download_quality,
                task_id=task_id,
                ranges=ranges_from_segments(segments),
            )
            if not downloaded:
                raise ValueError("Failed to download source video for regeneration")
            video_path = Path(downloaded)
        elif source_type in {"twitch", "kick"}:
            # Its own stream-work operation; cleaned below without touching
            # any other edit of this task.
            video_path = await run_stream_download(
                download_stream_sections, source_url, segments,
                await self._source_media_duration(task_id, task), task_id,
            )
        else:
            video_path = await resolve_upload_for_edit(self.video_service, source_url)
            if not video_path.exists():
                raise ValueError("Source video file no longer exists")

        downloaded_path = video_path
        try:
            # Cap the working file before localizing timestamps. The path does
            # not change, so the timeline sidecar still applies.
            video_path = await self.video_service.prepare_source(video_path)
            downloaded_path = video_path
            video_path = await self._with_part_words(task_id, video_path)
            time_map = await run_in_thread(load_time_map, video_path)
            render_segments = [
                localize_segment(segment, time_map) if time_map else segment
                for segment in segments
            ]
            clips_info = await self.video_service.create_video_clips(
                video_path,
                render_segments,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
                add_subtitles,
                normalized_cleanup_settings,
                max_short_side=output_short_side,
                # Merged and trimmed clips can run past a short preset; only
                # cap them as tightly as they were before presets existed.
                max_clip_seconds=max(
                    clip_duration(normalize_clip_length(metadata.get("clip_length"))).maximum,
                    MAX_RENDERED_CLIP_SECONDS,
                ),
                include_broll=include_broll,
                **fx_kwargs,
            )
            if time_map:
                for clip_info in clips_info:
                    index = int(clip_info.get("clip_id") or 0) - 1
                    if 0 <= index < len(segments):
                        await run_in_thread(
                            restore_clip_timeline, clip_info, time_map, segments[index]
                        )
        finally:
            self._release_part_words(video_path)
            if source_type in {"youtube", "twitch", "kick"}:
                self._cleanup_source_video(downloaded_path, source_type, source_url)

        rendered_indexes = {int(clip_info.get("clip_id") or 0) - 1 for clip_info in clips_info}
        missing_required = [
            segment for index, segment in enumerate(segments)
            if segment.get("required") and index not in rendered_indexes
        ]
        if not clips_info or missing_required:
            # Replacing the rows with nothing (or a series without a part)
            # would lose clips the user has.
            raise ValueError("Could not regenerate the clips. Your existing clips are unchanged.")

        await self.clip_repo.delete_clips_by_task(self.db, task_id)

        clip_ids = []
        for i, clip_info in enumerate(clips_info):
            index = int(clip_info.get("clip_id") or 0) - 1
            segment = segments[index] if 0 <= index < len(segments) else {}
            workflow_fields: Dict[str, Any] = {}
            if "workflow_role" in segment and not segment.get("planned"):
                workflow_fields = {
                    "workflow_role": segment.get("workflow_role"),
                    "part_number": segment.get("part_number"),
                    "workflow_meta": segment.get("workflow_meta"),
                }
            elif segment.get("planned"):
                meta = copy.deepcopy(segment.get("workflow_meta") or {})
                group_output_starts = clip_info.get("group_output_starts") or []
                fill_output_starts(meta.get("points"), group_output_starts)
                fill_output_starts(meta.get("beats"), group_output_starts)
                bounds = source_range_bounds(normalize_source_ranges(meta.get("ranges")))
                if bounds:
                    clip_info["start_time"] = self._seconds_to_mmss(bounds[0])
                    clip_info["end_time"] = self._seconds_to_mmss(bounds[1])
                workflow_fields = {
                    "workflow_role": segment.get("workflow_role"),
                    "part_number": segment.get("part_number"),
                    "workflow_meta": meta,
                }
            clip_id = await self.clip_repo.create_clip(
                self.db,
                task_id=task_id,
                filename=clip_info["filename"],
                file_path=clip_info["path"],
                start_time=clip_info["start_time"],
                end_time=clip_info["end_time"],
                duration=clip_info["duration"],
                text=clip_info.get("text") or "",
                relevance_score=clip_info.get("relevance_score", 0.5),
                reasoning=clip_info.get("reasoning")
                or "Regenerated with updated settings",
                clip_order=i + 1,
                virality_score=clip_info.get("virality_score", 0),
                hook_score=clip_info.get("hook_score", 0),
                engagement_score=clip_info.get("engagement_score", 0),
                value_score=clip_info.get("value_score", 0),
                shareability_score=clip_info.get("shareability_score", 0),
                hook_type=clip_info.get("hook_type"),
                hook_title=clip_info.get("hook_title"),
                **workflow_fields,
                **({"fx_meta": clip_info["fx_meta"]} if clip_info.get("fx_meta") else {}),
            )
            clip_ids.append(clip_id)

        await self.task_repo.update_task_clips(self.db, task_id, clip_ids)

    def _planned_regeneration_segment(
        self,
        clip: Dict[str, Any],
        source_ranges: list,
        broll: list,
        recompute_cleanup: bool,
    ) -> Optional[Dict[str, Any]]:
        """Re-render a workflow clip on its planned cuts, never the AI-bounds path.

        With unchanged cleanup the stored ranges and their offsets play again.
        Changed cleanup (or a lost source map) cleans the planned ranges anew,
        trimmed to what the clip still plays after the user's edits. None when
        the clip holds footage outside its plan (merged): it re-renders as an
        edited clip.
        """
        file_path = clip.get("file_path")
        metadata_path = (
            MediaStorage().local_path(file_path, metadata_only=True)
            if isinstance(file_path, str) and file_path
            else None
        )
        stored = load_clip_source_ranges(metadata_path) if metadata_path else None
        offsets = (
            load_clip_output_offsets(metadata_path, source_ranges)
            if stored
            else [0.0] * len(source_ranges)
        )
        meta = _narrow_planned_meta(clip.get("workflow_meta") or {}, source_ranges, offsets)
        if meta is None:
            return None
        planned = normalize_source_ranges(meta.get("ranges"))
        # The plan was accepted at its budget; new cleanup settings may leave
        # more pause in it, and cutting the end would drop words between parts.
        planned_seconds = sum(end - start for start, end in planned)
        segment: Dict[str, Any] = {
            "start_time": self._seconds_to_mmss(planned[0][0]),
            "end_time": self._seconds_to_mmss(planned[0][1]),
            "planned": True,
            "required": clip.get("workflow_role") in ("part", "recap", "trailer"),
            "max_seconds": max(float(meta.get("max_seconds") or 0.0), planned_seconds),
            "workflow_role": clip.get("workflow_role"),
            "part_number": clip.get("part_number"),
            "workflow_meta": meta,
            "text": clip.get("text") or "",
            "relevance_score": clip.get("relevance_score", 0.5),
            "reasoning": clip.get("reasoning") or "Regenerated with updated settings",
            "virality_score": clip.get("virality_score", 0),
            "hook_score": clip.get("hook_score", 0),
            "engagement_score": clip.get("engagement_score", 0),
            "value_score": clip.get("value_score", 0),
            "shareability_score": clip.get("shareability_score", 0),
            "hook_type": clip.get("hook_type"),
            "hook_title": clip.get("hook_title"),
            "broll": broll,
        }
        if recompute_cleanup or not stored:
            segment["source_ranges"] = planned
            segment["junction_fades"] = meta.get("junction_fades") or []
        else:
            segment["keep_ranges"] = source_ranges
            segment["output_offsets"] = offsets
            segment["range_groups"] = _range_groups(source_ranges, planned)
        return segment


    @serialized_task_edit
    async def trim_clip(
        self,
        task_id: str,
        clip_id: str,
        start_offset: float,
        end_offset: float,
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        fx_state = await run_in_thread(_load_fx_state, input_path)
        if fx_state is not None:
            return await self._trim_fx_clip(task_id, clip, input_path, fx_state, start_offset, end_offset)

        output_path = await run_in_thread(
            trim_clip_file, input_path, Path(self.config.temp_dir) / "clips", start_offset, end_offset
        )
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        total = max(offset + b - a for offset, (a, b) in zip(offsets, source_ranges))
        trimmed_ranges, trimmed_offsets = slice_source_timeline(source_ranges, start_offset, total - end_offset, offsets)
        clip_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, output_path, trimmed_ranges)
        )
        bounds = source_range_bounds(trimmed_ranges)
        if not bounds:
            raise ValueError("Trimmed clip has no remaining source mapping")
        start_seconds, end_seconds = bounds
        save_clip_source_ranges(output_path, trimmed_ranges, trimmed_offsets)
        if settings := load_clip_caption_settings(input_path):
            save_clip_caption_settings(output_path, settings)
        if broll := load_clip_broll(input_path):
            await self._carry_broll(broll, [(output_path, max(0.0, start_offset))])

        new_start = self._seconds_to_mmss(start_seconds)
        new_end = self._seconds_to_mmss(end_seconds)

        # A recap's points and a trailer's beats are placed on the clip
        # timeline; the trimmed clip keeps only what it still plays, at its
        # new time. Unplanned metadata (series titles) survives as it is.
        meta = clip.get("workflow_meta") or {}
        trimmed_meta: Optional[Dict[str, Any]] = None
        if meta.get("planned"):
            trimmed_meta = _narrow_planned_meta(meta, trimmed_ranges, trimmed_offsets)
            if trimmed_meta is None:
                trimmed_meta = _unplanned_meta(meta)

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            new_start,
            new_end,
            clip_duration,
            clip.get("text") or "",
            workflow_meta=trimmed_meta,
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}


    @serialized_task_edit
    async def split_clip(
        self, task_id: str, clip_id: str, split_time: float
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        fx_state = await run_in_thread(_load_fx_state, input_path)
        if fx_state is not None:
            return await self._split_fx_clip(task_id, clip, input_path, fx_state, split_time)

        first_path, second_path = await run_in_thread(
            split_clip_file, input_path, Path(self.config.temp_dir) / "clips", split_time
        )

        clamped_split = max(0.2, min(split_time, float(clip["duration"]) - 0.2))
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        total = max(offset + b - a for offset, (a, b) in zip(offsets, source_ranges))
        first_ranges, first_offsets = slice_source_timeline(source_ranges, 0, clamped_split, offsets)
        second_ranges, second_offsets = slice_source_timeline(source_ranges, clamped_split, total, offsets)
        first_bounds = source_range_bounds(first_ranges)
        second_bounds = source_range_bounds(second_ranges)
        if not first_bounds or not second_bounds:
            raise ValueError("Split clip has invalid source mapping")
        save_clip_source_ranges(first_path, first_ranges, first_offsets)
        save_clip_source_ranges(second_path, second_ranges, second_offsets)
        if settings := load_clip_caption_settings(input_path):
            save_clip_caption_settings(first_path, settings)
            save_clip_caption_settings(second_path, settings)
        if broll := load_clip_broll(input_path):
            await self._carry_broll(broll, [(first_path, 0.0), (second_path, clamped_split)])
        first_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, first_path, first_ranges)
        )
        second_duration = max(
            0.1, await run_in_thread(rendered_clip_duration, second_path, second_ranges)
        )

        first_meta, workflow_fields = _split_workflow_fields(
            clip, first_ranges, first_offsets, second_ranges, second_offsets
        )

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            first_path.name,
            str(first_path),
            self._seconds_to_mmss(first_bounds[0]),
            self._seconds_to_mmss(first_bounds[1]),
            first_duration,
            clip.get("text") or "",
            workflow_meta=first_meta,
        )

        await self.clip_repo.make_room_for_clip(
            self.db, task_id, clip.get("clip_order", 1) + 1
        )
        await self.clip_repo.create_clip(
            self.db,
            task_id=task_id,
            filename=second_path.name,
            file_path=str(second_path),
            start_time=self._seconds_to_mmss(second_bounds[0]),
            end_time=self._seconds_to_mmss(second_bounds[1]),
            duration=second_duration,
            text=clip.get("text") or "",
            relevance_score=clip.get("relevance_score", 0.5),
            reasoning=clip.get("reasoning") or "Split from original clip",
            clip_order=clip.get("clip_order", 1) + 1,
            virality_score=clip.get("virality_score", 0),
            hook_score=clip.get("hook_score", 0),
            engagement_score=clip.get("engagement_score", 0),
            value_score=clip.get("value_score", 0),
            shareability_score=clip.get("shareability_score", 0),
            hook_type=clip.get("hook_type"),
            hook_title=clip.get("hook_title"),
            **workflow_fields,
        )

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clip split successfully"}


    @serialized_task_edit
    async def merge_clips(self, task_id: str, clip_ids: list[str]) -> Dict[str, Any]:
        if len(clip_ids) < 2 or len(set(clip_ids)) != len(clip_ids):
            raise ValueError("At least two distinct clips are required to merge")

        clips = []
        for clip_id in clip_ids:
            clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
            if not clip or clip["task_id"] != task_id:
                raise ValueError("One or more clips not found")
            clips.append(clip)

        ordered = sorted(clips, key=lambda c: c.get("clip_order", 0))
        input_paths = [await run_in_thread(MediaStorage().local_path, c["file_path"]) for c in ordered]
        fx_states = [await run_in_thread(_load_fx_state, path) for path in input_paths]
        if any(state is not None for state in fx_states):
            return await self._merge_fx_clips(task_id, ordered, input_paths, fx_states)
        merged_path = await run_in_thread(
            merge_clip_files,
            input_paths,
            Path(self.config.temp_dir) / "clips",
        )

        merged_ranges, merged_offsets, cursor = [], [], 0.0
        for clip, path in zip(ordered, input_paths):
            source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
            merged_ranges.extend(source_ranges)
            merged_offsets += [cursor + offset for offset in load_clip_output_offsets(path, source_ranges)]
            cursor += float(clip["duration"])
        merged_bounds = source_range_bounds(merged_ranges)
        if merged_bounds:
            start_time = self._seconds_to_mmss(merged_bounds[0])
            end_time = self._seconds_to_mmss(merged_bounds[1])
            duration = await run_in_thread(rendered_clip_duration, merged_path, merged_ranges)
            save_clip_source_ranges(merged_path, merged_ranges, merged_offsets)
        else:
            start_time = ordered[0]["start_time"]
            end_time = ordered[-1]["end_time"]
            duration = sum(float(c.get("duration", 0.0)) for c in ordered)
        clip_broll = [load_clip_broll(path) for path in input_paths]
        if any(clip_broll):
            merged_broll = []
            offset = 0.0
            try:
                for path, placements in zip(input_paths, clip_broll):
                    length = await run_in_thread(ffprobe_duration, path)
                    merged_broll += shift_broll_placements(placements, -offset, offset + length)
                    offset += length
                save_clip_broll(merged_path, merged_broll)
            except Exception:
                # The merged file already has the footage; only later re-renders lose it.
                logger.warning("Could not carry B-roll placements into merged clip", exc_info=True)
        text = " ".join((c.get("text") or "").strip() for c in ordered if c.get("text"))

        first = ordered[0]
        await self.clip_repo.update_clip(
            self.db,
            first["id"],
            merged_path.name,
            str(merged_path),
            start_time,
            end_time,
            duration,
            text,
            # The merged clip no longer plays one part's planned cuts or labels.
            workflow_meta=_unplanned_meta(first["workflow_meta"]) if first.get("workflow_meta") else None,
        )

        for clip in ordered[1:]:
            await self.clip_repo.delete_clip(self.db, clip["id"])

        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clips merged successfully", "clip_id": first["id"]}


    @serialized_task_edit
    async def update_clip_captions(
        self,
        task_id: str,
        clip_id: str,
        caption_text: str,
        position: str,
        highlight_words: list[str],
        *,
        font_size: Optional[int] = None,
        position_y: Optional[float] = None,
    ) -> Dict[str, Any]:
        clip = await self.clip_repo.get_clip_by_id(self.db, clip_id)
        if not clip or clip["task_id"] != task_id:
            raise ValueError("Clip not found")

        input_path = await run_in_thread(MediaStorage().local_path, clip["file_path"])
        if not input_path.exists():
            raise ValueError("Clip file not found")

        fx_state = await run_in_thread(_load_fx_state, input_path)
        if fx_state is not None and fx_state.plan is not None:
            # One render with the edited captions and the stored plan replayed (§13.3, G16).
            return await self._update_fx_clip_captions(
                task_id, clip, input_path, fx_state, caption_text, position, highlight_words,
                font_size=font_size, position_y=position_y,
            )

        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")

        download_quality, output_short_side = await self._media_limits(task.get("user_id"))
        # Always render from the source; overlaying an already captioned clip
        # stacks old and new captions and compounds quality loss on every save.
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        transcript_video_path, downloaded_source = await self._edit_source_video(
            task, task_id, source_ranges, download_quality
        )
        source_url = task.get("source_url")
        source_type = task.get("source_type")

        try:
            transcript_video_path = await self.video_service.prepare_source(transcript_video_path)
            transcript_video_path = await self._with_part_words(task_id, transcript_video_path)
            settings = await self._load_task_source_settings(task_id)
            local_ranges = await run_in_thread(
                local_ranges_for, transcript_video_path, source_ranges
            )
            bounds = source_range_bounds(local_ranges)
            if not bounds:
                raise ValueError("Clip source timing is unavailable")
            output_dir = Path(self.config.temp_dir) / "clips"
            output_dir.mkdir(parents=True, exist_ok=True)
            # The clip timeline is unchanged, so its cutaways go back on as they were.
            broll = load_clip_broll(input_path)
            broll_overlays = await broll_overlays_for(broll)
            render_report: Dict[str, Any] = {}
            # Workflow clips keep their planned junctions (fades, out-of-order beats).
            output_offsets = None
            if (clip.get("workflow_meta") or {}).get("planned") and len(local_ranges) == len(source_ranges):
                output_offsets = load_clip_output_offsets(input_path, source_ranges)
            offsets_kwargs = {"output_offsets": output_offsets} if output_offsets is not None else {}
            point_labels = point_labels_for(clip.get("workflow_meta"))
            labels_kwargs = {"point_labels": point_labels} if point_labels else {}
            with tempfile.TemporaryDirectory(prefix="caption_edit_", dir=output_dir) as temporary:
                clean_path = Path(temporary) / "clean.mp4"
                rendered = await run_in_thread(
                    create_optimized_clip, transcript_video_path, bounds[0], bounds[1], clean_path,
                    add_subtitles=False,
                    output_format=settings.get("output_format", "vertical"),
                    keep_ranges=local_ranges,
                    hook_title=burned_hook_title(clip),
                    font_family=task.get("font_family") or None,
                    font_size=task.get("font_size") or None,
                    font_color=task.get("font_color") or None,
                    caption_template=task.get("caption_template") or "default",
                    extend_to_sentence=False,
                    layout_report=render_report,
                    broll_overlays=broll_overlays,
                    **offsets_kwargs,
                    **labels_kwargs,
                )
                if not rendered:
                    raise ValueError("Could not prepare the clip for caption editing")
                if not render_report.get("broll_applied"):
                    broll = []
                output_path = await run_in_thread(
                    overlay_custom_captions,
                    clean_path, output_dir, caption_text, position, highlight_words,
                    font_family=task.get("font_family") or None,
                    font_size=font_size if font_size is not None else task.get("font_size") or None,
                    font_color=task.get("font_color") or None,
                    caption_template=task.get("caption_template") or "default",
                    transcript_video_path=transcript_video_path,
                    source_ranges=local_ranges,
                    position_y=position_y,
                    output_offsets=output_offsets,
                )
                if output_short_side:
                    try:
                        await run_in_thread(cap_video_short_side, output_path, output_short_side)
                    except Exception as e:
                        logger.warning(f"Keeping full-size caption edit for clip {clip_id}: {e}")
        finally:
            self._release_part_words(transcript_video_path)
            if downloaded_source is not None:
                self._cleanup_source_video(downloaded_source, source_type, source_url)
        save_clip_source_ranges(output_path, source_ranges, output_offsets)
        save_clip_caption_settings(output_path, {
            "font_size": font_size if font_size is not None else task.get("font_size"),
            "position": position,
            "position_y": position_y if position_y is not None else {"top": 0.18, "middle": 0.52, "bottom": 0.78}[position],
            "highlight_words": highlight_words,
        })
        save_clip_broll(output_path, broll)
        # Re-rendering also corrects durations stored before they were probed.
        rendered_duration = await run_in_thread(
            rendered_clip_duration, output_path, source_ranges, output_offsets
        )

        await self.clip_repo.update_clip(
            self.db,
            clip_id,
            output_path.name,
            str(output_path),
            clip["start_time"],
            clip["end_time"],
            rendered_duration if rendered_duration > 0 else clip["duration"],
            caption_text,
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip_id)) or {}


    # ------------------------------------------------------------------------- FX clip edits (run in fx_clip_job)

    async def _render_fx_from_source(
        self,
        task_id: str,
        *,
        task: Dict[str, Any],
        clip: Dict[str, Any],
        source_ranges: list,
        output_offsets: list[float],
        plan: FxPlan,
        style: Dict[str, Any],
        beats: Optional[EditBeats],
        face_track: Optional[Dict[str, Any]],
        broll: list[dict],
        prefix: str,
        caption_edit: Optional[Dict[str, Any]] = None,
        caption_override: Optional[Dict[str, Any]] = None,
        workflow_meta: Optional[Dict[str, Any]] = None,
    ) -> _FxRender:
        """Render ``source_ranges`` (S, at ``output_offsets`` on the E timeline) once, with ``plan`` replayed.

        Never re-plans: no resolver, no beat model. ``caption_edit`` (``text``, ``highlight_words``, ``position_y``,
        ``font_size``) burns edited captions in the same pass (§13.3); ``caption_override`` keeps an earlier caption
        edit's placement. Writes the new clip's sidecar (source map, B-roll, FX keys, vertical layout) after the
        720p cap.
        """
        download_quality, output_short_side = await self._media_limits(task.get("user_id"))
        video_path, downloaded = await self._edit_source_video(task, task_id, source_ranges, download_quality)
        output_dir = Path(self.config.temp_dir) / "clips"
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{prefix}_{uuid4().hex[:12]}.mp4"
        report: Dict[str, Any] = {}
        try:
            video_path = await self.video_service.prepare_source(video_path)
            video_path = await self._with_part_words(task_id, video_path)
            settings = await self._load_task_source_settings(task_id)
            local_ranges, local_offsets = await run_in_thread(
                _local_timeline, video_path, source_ranges, output_offsets
            )
            bounds = source_range_bounds(local_ranges)
            if not bounds:
                raise ValueError("Clip source timing is unavailable")
            override = caption_override
            add_subtitles = bool(settings.get("add_subtitles", True))
            if caption_edit is not None:
                words, spoken = await run_in_thread(
                    edited_caption_words, caption_edit["text"], video_path, local_ranges,
                    float(plan.edit_duration), local_offsets,
                )
                plan = _drop_stale_accents(plan, words, spoken)
                override = {
                    "words": words,
                    "highlight_words": list(caption_edit.get("highlight_words") or []),
                    "position_y": caption_edit.get("position_y"),
                    **({"font_size": int(caption_edit["font_size"])} if caption_edit.get("font_size") else {}),
                }
                # An edited caption is always burned, like the legacy overlay pass.
                add_subtitles = True
            else:
                words = await run_in_thread(_transcript_fx_words, video_path, local_ranges, local_offsets)
            overlays = await broll_overlays_for(broll)
            point_labels = point_labels_for(workflow_meta if workflow_meta is not None else clip.get("workflow_meta"))
            with tempfile.TemporaryDirectory(prefix="fx_clip_edit_", dir=output_dir) as scratch:
                plan, reaction_files = await _reaction_files(plan, Path(scratch))
                rendered = await run_in_thread(
                    create_optimized_clip, video_path, bounds[0], bounds[1], output_path,
                    add_subtitles=add_subtitles,
                    font_family=task.get("font_family") or None,
                    font_size=task.get("font_size") or None,
                    font_color=task.get("font_color") or None,
                    caption_template=plan.template,
                    output_format=settings.get("output_format", "vertical"),
                    keep_ranges=local_ranges,
                    hook_title=burned_hook_title(clip),
                    extend_to_sentence=False,
                    layout_report=report,
                    broll_overlays=overlays,
                    output_offsets=local_offsets,
                    **({"point_labels": point_labels} if point_labels else {}),
                    fx=FxRenderInput(plan=plan, words=words, reaction_files=reaction_files, face_track=face_track),
                    **({"caption_override": override} if override else {}),
                )
            if not rendered or not output_path.exists():
                raise ValueError("Could not render the edited clip")
            if output_short_side:
                try:
                    await run_in_thread(cap_video_short_side, output_path, output_short_side)
                except Exception as e:
                    logger.warning(f"Keeping full-size FX clip edit for clip {clip.get('id')}: {e}")
        except BaseException:
            output_path.unlink(missing_ok=True)
            raise
        finally:
            self._release_part_words(video_path)
            if downloaded is not None:
                self._cleanup_source_video(downloaded, task.get("source_type"), task.get("source_url"))
        if not report.get("broll_applied"):
            broll = []
        report.setdefault("fx_plan", plan)
        save_clip_source_ranges(output_path, source_ranges, output_offsets)
        save_clip_broll(output_path, broll)
        fx_meta = save_fx_render(
            output_path,
            style=style,
            beats=beats.model_dump(mode="json") if beats is not None else None,
            report=report,
            template=plan.template,
        )
        duration = await run_in_thread(rendered_clip_duration, output_path, source_ranges, output_offsets)
        return _FxRender(path=output_path, duration=duration, fx_meta=fx_meta, caption_y=report.get("caption_y"))

    async def _trim_fx_clip(
        self,
        task_id: str,
        clip: Dict[str, Any],
        input_path: Path,
        state: _FxClipState,
        start_offset: float,
        end_offset: float,
    ) -> Dict[str, Any]:
        """Trim an FX clip (§13.2). ``start_offset``/``end_offset`` are F seconds off the mp4's ends."""
        total_f = await run_in_thread(ffprobe_duration, input_path)
        if total_f <= 0:
            raise ValueError("Clip file could not be read")
        # The same window trim_clip_file cuts.
        f0 = min(max(0.0, float(start_offset)), total_f)
        f1 = min(max(f0 + MIN_TRIM_SECONDS, total_f - max(float(end_offset), 0.0)), total_f)
        if f1 - f0 < MIN_TRIM_SECONDS - 1e-6:
            raise ValueError("Trimmed clip has no remaining source mapping")
        warp = state.warp
        e0, e1 = warp.to_edit(f0), warp.to_edit(f1)
        edit_total = state.edit_duration(total_f)

        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        trimmed_ranges, trimmed_offsets = slice_source_timeline(source_ranges, e0, e1, offsets)
        bounds = source_range_bounds(trimmed_ranges)
        if not bounds:
            raise ValueError("Trimmed clip has no remaining source mapping")

        plan = shift_plan(state.plan, e0, e1, applied_warp=warp, f0=f0, f1=f1) if state.plan is not None else None
        beats = shift_beats(state.beats, e0, e1) if state.beats is not None else None
        broll = shift_broll_placements(load_clip_broll(input_path), e0, min(e1, edit_total) - e0)
        caption_settings = load_clip_caption_settings(input_path)

        meta = clip.get("workflow_meta") or {}
        trimmed_meta: Optional[Dict[str, Any]] = None
        if meta.get("planned"):
            trimmed_meta = _narrow_planned_meta(meta, trimmed_ranges, trimmed_offsets)
            if trimmed_meta is None:
                trimmed_meta = _unplanned_meta(meta)

        if plan is not None and _needs_source_render(state.plan):
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            if not task:
                raise ValueError("Task not found")
            render = await self._render_fx_from_source(
                task_id, task=task, clip=clip, source_ranges=trimmed_ranges, output_offsets=trimmed_offsets,
                plan=plan, style=state.style, beats=beats, face_track=None, broll=broll, prefix="trim",
                caption_override=_stored_caption_override(caption_settings, task),
                workflow_meta=trimmed_meta if trimmed_meta is not None else meta,
            )
            output_path, duration, fx_meta = render.path, render.duration, render.fx_meta
            if caption_settings:
                save_clip_caption_settings(output_path, caption_settings)
        else:
            output_path = await run_in_thread(
                trim_clip_file, input_path, Path(self.config.temp_dir) / "clips", f0, total_f - f1
            )
            save_clip_source_ranges(output_path, trimmed_ranges, trimmed_offsets)
            if caption_settings:
                save_clip_caption_settings(output_path, caption_settings)
            save_clip_broll(output_path, broll)
            level = _fx_level(state, clip)
            _save_cut_fx(
                output_path, state, plan=plan, beats=beats,
                face_track=shift_face_track(state.face_track, e0, e1), level=level, warp=slice_warp(warp, f0, f1),
            )
            duration = await run_in_thread(rendered_clip_duration, output_path, trimmed_ranges, trimmed_offsets)
            fx_meta = _fx_meta(plan, level) if plan is not None else clip.get("fx_meta")

        await self.clip_repo.update_clip(
            self.db,
            clip["id"],
            output_path.name,
            str(output_path),
            self._seconds_to_mmss(bounds[0]),
            self._seconds_to_mmss(bounds[1]),
            max(0.1, duration),
            clip.get("text") or "",
            workflow_meta=trimmed_meta,
            **({"fx_meta": fx_meta} if fx_meta else {}),
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip["id"])) or {}

    async def _split_fx_clip(
        self,
        task_id: str,
        clip: Dict[str, Any],
        input_path: Path,
        state: _FxClipState,
        split_time: float,
    ) -> Dict[str, Any]:
        """Split an FX clip at F time ``split_time`` (§13.2); a cut through a freeze keeps the hold on each side."""
        total_f = await run_in_thread(ffprobe_duration, input_path)
        if total_f <= 2 * SPLIT_EDGE_SECONDS:
            raise ValueError("Clip is too short to split")
        split_at = max(SPLIT_EDGE_SECONDS, min(float(split_time), total_f - SPLIT_EDGE_SECONDS))
        warp = state.warp
        cut_e = warp.to_edit(split_at)
        edit_total = state.edit_duration(total_f)

        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        total = max(offset + b - a for offset, (a, b) in zip(offsets, source_ranges))
        halves = []
        for e_start, e_end, f_start, f_end in ((0.0, cut_e, 0.0, split_at), (cut_e, max(total, edit_total), split_at, total_f)):
            ranges, half_offsets = slice_source_timeline(source_ranges, e_start, e_end, offsets)
            plan_end = min(e_end, edit_total)
            halves.append({
                "ranges": ranges,
                "offsets": half_offsets,
                "bounds": source_range_bounds(ranges),
                "plan": shift_plan(state.plan, e_start, plan_end, applied_warp=warp, f0=f_start, f1=f_end)
                if state.plan is not None else None,
                "beats": shift_beats(state.beats, e_start, plan_end) if state.beats is not None else None,
                "face_track": shift_face_track(state.face_track, e_start, plan_end),
                "warp": slice_warp(warp, f_start, f_end),
                "e_start": e_start,
                "e_end": plan_end,
            })
        if not halves[0]["bounds"] or not halves[1]["bounds"]:
            raise ValueError("Split clip has invalid source mapping")

        broll = load_clip_broll(input_path)
        caption_settings = load_clip_caption_settings(input_path)
        first_meta, workflow_fields = _split_workflow_fields(
            clip, halves[0]["ranges"], halves[0]["offsets"], halves[1]["ranges"], halves[1]["offsets"]
        )
        level = _fx_level(state, clip)
        if halves[0]["plan"] is not None and _needs_source_render(state.plan):
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            if not task:
                raise ValueError("Task not found")
            metas = [first_meta, workflow_fields.get("workflow_meta")]
            for index, half in enumerate(halves):
                render = await self._render_fx_from_source(
                    task_id, task=task, clip=clip, source_ranges=half["ranges"], output_offsets=half["offsets"],
                    plan=half["plan"], style=state.style, beats=half["beats"], face_track=None,
                    broll=shift_broll_placements(broll, half["e_start"], half["e_end"] - half["e_start"]),
                    prefix=f"split_{'ab'[index]}",
                    caption_override=_stored_caption_override(caption_settings, task),
                    workflow_meta=metas[index] if metas[index] is not None else clip.get("workflow_meta"),
                )
                half.update(path=render.path, duration=render.duration, fx_meta=render.fx_meta)
                if caption_settings:
                    save_clip_caption_settings(render.path, caption_settings)
        else:
            first_path, second_path = await run_in_thread(
                split_clip_file, input_path, Path(self.config.temp_dir) / "clips", split_at
            )
            for half, path in zip(halves, (first_path, second_path)):
                save_clip_source_ranges(path, half["ranges"], half["offsets"])
                if caption_settings:
                    save_clip_caption_settings(path, caption_settings)
                save_clip_broll(path, shift_broll_placements(broll, half["e_start"], half["e_end"] - half["e_start"]))
                _save_cut_fx(
                    path, state, plan=half["plan"], beats=half["beats"], face_track=half["face_track"],
                    level=level, warp=half["warp"],
                )
                half.update(
                    path=path,
                    duration=await run_in_thread(rendered_clip_duration, path, half["ranges"], half["offsets"]),
                    fx_meta=_fx_meta(half["plan"], level) if half["plan"] is not None else clip.get("fx_meta"),
                )

        first, second = halves
        await self.clip_repo.update_clip(
            self.db,
            clip["id"],
            first["path"].name,
            str(first["path"]),
            self._seconds_to_mmss(first["bounds"][0]),
            self._seconds_to_mmss(first["bounds"][1]),
            max(0.1, first["duration"]),
            clip.get("text") or "",
            workflow_meta=first_meta,
            **({"fx_meta": first["fx_meta"]} if first["fx_meta"] else {}),
        )
        await self.clip_repo.make_room_for_clip(self.db, task_id, clip.get("clip_order", 1) + 1)
        await self.clip_repo.create_clip(
            self.db,
            task_id=task_id,
            filename=second["path"].name,
            file_path=str(second["path"]),
            start_time=self._seconds_to_mmss(second["bounds"][0]),
            end_time=self._seconds_to_mmss(second["bounds"][1]),
            duration=max(0.1, second["duration"]),
            text=clip.get("text") or "",
            relevance_score=clip.get("relevance_score", 0.5),
            reasoning=clip.get("reasoning") or "Split from original clip",
            clip_order=clip.get("clip_order", 1) + 1,
            virality_score=clip.get("virality_score", 0),
            hook_score=clip.get("hook_score", 0),
            engagement_score=clip.get("engagement_score", 0),
            value_score=clip.get("value_score", 0),
            shareability_score=clip.get("shareability_score", 0),
            hook_type=clip.get("hook_type"),
            hook_title=clip.get("hook_title"),
            **workflow_fields,
            **({"fx_meta": second["fx_meta"]} if second["fx_meta"] else {}),
        )
        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clip split successfully"}

    async def _merge_fx_clips(
        self,
        task_id: str,
        ordered: list[Dict[str, Any]],
        input_paths: list[Path],
        states: list[Optional[_FxClipState]],
    ) -> Dict[str, Any]:
        """Merge clips of which at least one has dynamic edits (§13.2, AR-B3, AI-H5).

        Every cursor (source ranges, offsets, B-roll, plans, warps, beats) is the E length of each part, so a freeze
        in part 1 keeps part 2 aligned. Legacy parts count with E == F. The stored ``duration`` is the probed F length
        of the merged mp4. A merged plan with music or retention globals is re-rendered from source; otherwise the
        mp4s are concatenated and each part keeps its own bed.
        """
        template = next(state.plan for state in states if state is not None and state.plan is not None) \
            if any(state is not None and state.plan is not None for state in states) else None
        base_state = next(state for state in states if state is not None)
        parts = []
        for clip, path, state in zip(ordered, input_paths, states):
            out_duration = await run_in_thread(ffprobe_duration, path)
            if state is not None:
                edit_duration = state.edit_duration(out_duration)
                warp = state.warp if state.warp.pieces else identity_warp(edit_duration)
            else:
                edit_duration = out_duration
                warp = identity_warp(edit_duration)
            source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
            parts.append({
                "clip": clip,
                "path": path,
                "state": state,
                "edit_duration": max(0.0, float(edit_duration)),
                "warp": warp,
                "ranges": source_ranges,
                "offsets": load_clip_output_offsets(path, source_ranges),
                "broll": load_clip_broll(path),
            })

        merged_ranges: list = []
        merged_offsets: list[float] = []
        merged_broll: list[dict] = []
        cursor = 0.0
        for part in parts:
            merged_ranges.extend(part["ranges"])
            merged_offsets += [cursor + offset for offset in part["offsets"]]
            merged_broll += shift_broll_placements(part["broll"], -cursor, cursor + part["edit_duration"])
            cursor += part["edit_duration"]

        plan = (
            concat_plans([
                (part["state"].plan if part["state"] is not None and part["state"].plan is not None
                 else _blank_plan(template, part["edit_duration"]), part["edit_duration"])
                for part in parts
            ])
            if template is not None else None
        )
        warp = concat_warps([(part["warp"], part["edit_duration"]) for part in parts])
        beats = _concat_beats([
            (part["state"].beats if part["state"] is not None else None, part["edit_duration"]) for part in parts
        ])
        levels = [_fx_level(part["state"], part["clip"]) for part in parts if part["state"] is not None]
        level = min(levels, key=FX_LEVELS[::-1].index) if levels else "full"
        merged_bounds = source_range_bounds(merged_ranges)
        text = " ".join((c.get("text") or "").strip() for c in ordered if c.get("text"))
        first = ordered[0]

        if plan is not None and _needs_source_render(plan) and merged_bounds:
            task = await self.task_repo.get_task_by_id(self.db, task_id)
            if not task:
                raise ValueError("Task not found")
            render = await self._render_fx_from_source(
                task_id, task=task, clip=first, source_ranges=merged_ranges, output_offsets=merged_offsets,
                plan=plan, style=base_state.style, beats=beats, face_track=None, broll=merged_broll,
                prefix="merge", workflow_meta=_unplanned_meta(first["workflow_meta"]) if first.get("workflow_meta") else {},
            )
            merged_path, duration, fx_meta = render.path, render.duration, render.fx_meta
            start_time = self._seconds_to_mmss(merged_bounds[0])
            end_time = self._seconds_to_mmss(merged_bounds[1])
        else:
            merged_path = await run_in_thread(merge_clip_files, input_paths, Path(self.config.temp_dir) / "clips")
            if merged_bounds:
                start_time = self._seconds_to_mmss(merged_bounds[0])
                end_time = self._seconds_to_mmss(merged_bounds[1])
                save_clip_source_ranges(merged_path, merged_ranges, merged_offsets)
            else:
                start_time = ordered[0]["start_time"]
                end_time = ordered[-1]["end_time"]
            save_clip_broll(merged_path, merged_broll)
            _save_cut_fx(
                merged_path, base_state, plan=plan, beats=beats,
                face_track=_concat_face_tracks([
                    (part["state"].face_track if part["state"] is not None else None, part["edit_duration"])
                    for part in parts
                ]),
                level=level, warp=warp,
            )
            duration = await run_in_thread(rendered_clip_duration, merged_path, merged_ranges, merged_offsets)
            fx_meta = _fx_meta(plan, level) if plan is not None else next(
                (c.get("fx_meta") for c in ordered if c.get("fx_meta")), None
            )

        await self.clip_repo.update_clip(
            self.db,
            first["id"],
            merged_path.name,
            str(merged_path),
            start_time,
            end_time,
            duration,
            text,
            workflow_meta=_unplanned_meta(first["workflow_meta"]) if first.get("workflow_meta") else None,
            **({"fx_meta": fx_meta} if fx_meta else {}),
        )
        for clip in ordered[1:]:
            await self.clip_repo.delete_clip(self.db, clip["id"])
        await self.clip_repo.reorder_task_clips(self.db, task_id)
        return {"message": "Clips merged successfully", "clip_id": first["id"]}

    async def _update_fx_clip_captions(
        self,
        task_id: str,
        clip: Dict[str, Any],
        input_path: Path,
        state: _FxClipState,
        caption_text: str,
        position: str,
        highlight_words: list[str],
        *,
        font_size: Optional[int],
        position_y: Optional[float],
    ) -> Dict[str, Any]:
        """Caption edit of an FX clip in one render (§13.3, G16): the stored plan replayed (no resolver, no beat
        model), the edited words, position and size honoured, the stored face track reused (AR-M10)."""
        task = await self.task_repo.get_task_by_id(self.db, task_id)
        if not task:
            raise ValueError("Task not found")
        source_ranges = await run_in_thread(self._get_clip_source_ranges, clip)
        offsets = load_clip_output_offsets(input_path, source_ranges)
        placed_y = position_y if position_y is not None else CAPTION_POSITION_Y[position]
        size = font_size if font_size is not None else task.get("font_size")
        render = await self._render_fx_from_source(
            task_id, task=task, clip=clip, source_ranges=source_ranges, output_offsets=offsets,
            plan=state.plan, style=state.style, beats=state.beats, face_track=state.face_track,
            # The clip timeline is unchanged, so its cutaways go back on as they were.
            broll=load_clip_broll(input_path), prefix="caption",
            caption_edit={
                "text": caption_text,
                "highlight_words": highlight_words,
                "position_y": placed_y,
                "font_size": size,
            },
        )
        save_clip_caption_settings(render.path, {
            "font_size": size,
            "position": position,
            "position_y": placed_y,
            "highlight_words": highlight_words,
        })
        await self.clip_repo.update_clip(
            self.db,
            clip["id"],
            render.path.name,
            str(render.path),
            clip["start_time"],
            clip["end_time"],
            render.duration if render.duration > 0 else clip["duration"],
            caption_text,
            fx_meta=render.fx_meta,
        )
        return (await self.clip_repo.get_clip_by_id(self.db, clip["id"])) or {}

    async def _edit_source_video(
        self,
        task: Dict[str, Any],
        task_id: str,
        source_ranges: list,
        download_quality: Optional[str],
    ) -> tuple[Path, Optional[Path]]:
        """The source video a clip edit renders from, and the downloaded file to clean up afterwards (or None).

        The processing cache first, then the upload, then a fresh (section) download of the clip's ranges.
        """
        transcript_video_path: Optional[Path] = None
        source_url = task.get("source_url")
        source_type = task.get("source_type")
        processing_mode = (
            task.get("processing_mode") or self.config.default_processing_mode
        )
        if source_url and source_type:
            cache_entry = await self.cache_repo.get_cache(
                self.db,
                self._build_cache_key(source_url, source_type, processing_mode, download_quality),
            )
            cached_video_path = cache_entry.get("video_path") if cache_entry else None
            if cached_video_path:
                transcript_video_path = await run_in_thread(MediaStorage().local_path, cached_video_path)
            elif source_type not in {"youtube", "twitch", "kick"}:
                try:
                    transcript_video_path = await resolve_upload_for_edit(
                        self.video_service, source_url
                    )
                except ValueError:
                    transcript_video_path = None

        downloaded_source: Optional[Path] = None
        if not transcript_video_path or not transcript_video_path.exists():
            if source_type == "youtube" and source_url:
                downloaded = await self.video_service.download_video(
                    source_url,
                    quality=download_quality,
                    task_id=task_id,
                    ranges=source_ranges,
                )
                transcript_video_path = Path(downloaded) if downloaded else None
                downloaded_source = transcript_video_path
            elif source_type in {"twitch", "kick"} and source_url:
                transcript_video_path = await run_stream_download(
                    download_stream_sections, source_url,
                    [{"start_time": start, "end_time": end} for start, end in source_ranges],
                    await self._source_media_duration(task_id, task), task_id,
                )
                downloaded_source = transcript_video_path
            elif source_url:
                transcript_video_path = await resolve_upload_for_edit(self.video_service, source_url)
        if not transcript_video_path or not transcript_video_path.exists():
            raise ValueError("The source video is no longer available. Upload it again to edit captions.")
        return transcript_video_path, downloaded_source

    @staticmethod
    def _seconds_to_mmss(seconds: float) -> str:
        total = max(0, int(round(seconds)))
        minutes = total // 60
        secs = total % 60
        return f"{minutes:02d}:{secs:02d}"


    @staticmethod
    async def _carry_broll(broll: list[dict], cuts: list[tuple[Path, float]]) -> None:
        """Store B-roll for clips cut from a longer one (path, start offset).

        The cut files already contain the footage; losing the placements only
        means a later re-render from source drops it, so never fail the edit.
        """
        try:
            for path, offset in cuts:
                length = await run_in_thread(ffprobe_duration, path)
                save_clip_broll(path, shift_broll_placements(broll, offset, length))
        except Exception:
            logger.warning("Could not carry B-roll placements into edited clip", exc_info=True)

    @staticmethod
    def _segment_fx_keys(clip: Dict[str, Any]) -> Dict[str, Any]:
        """The clip's stored dynamic edits as the segment keys ``create_video_clips`` reads (§13.1); {} for legacy."""
        file_path = clip.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return {}
        sidecar = load_clip_edit_fx(MediaStorage().local_path(file_path, metadata_only=True))
        if sidecar is None:
            return {}
        return {
            "edit_fx": sidecar["plan"],
            "edit_beats": sidecar["beats"],
            "edit_style": sidecar["style"],
            "face_track": sidecar["face_track"],
            "edit_fx_warp": sidecar["applied_warp"],
        }

    @staticmethod
    def _get_clip_broll(clip: Dict[str, Any]) -> list[dict]:
        file_path = clip.get("file_path")
        if not isinstance(file_path, str) or not file_path:
            return []
        return load_clip_broll(MediaStorage().local_path(file_path, metadata_only=True))

    @staticmethod
    def _get_clip_source_ranges(clip: Dict[str, Any]) -> list[tuple[float, float]]:
        file_path = clip.get("file_path")
        if isinstance(file_path, str) and file_path:
            persisted = load_clip_source_ranges(MediaStorage().local_path(file_path, metadata_only=True))
            if persisted:
                return persisted

        start_seconds = parse_timestamp_to_seconds(clip["start_time"])
        end_seconds = parse_timestamp_to_seconds(clip["end_time"])
        # A workflow clip's planned ranges; its labels span every jump in between.
        # Labels reaching past them (a merge added footage) win.
        planned = normalize_source_ranges((clip.get("workflow_meta") or {}).get("ranges"))
        planned_bounds = source_range_bounds(planned)
        if planned_bounds and start_seconds >= planned_bounds[0] - 1.0 and end_seconds <= planned_bounds[1] + 1.0:
            return planned
        return [(start_seconds, end_seconds)]


    async def _with_part_words(self, task_id: str, video_path: Path) -> Path:
        """The source to render from, with word timings when the task is a picked part.

        A part's timings are kept in the task's checkpoint, never beside a shared
        download or upload, so a file fetched for an edit has none. They go on a
        private link instead (released with ``_release_part_words``). Any other
        task, or a file that already has timings, is returned unchanged.
        """
        if (await run_in_thread(load_cached_transcript_data, video_path) or {}).get("words"):
            return video_path
        settings = await self._load_task_source_settings(task_id)
        if not settings.get(SOURCE_RANGE_KEY) or self.db is None:
            return video_path
        original = await range_transcript(self.db, task_id, settings)
        if not original:
            return video_path
        return await run_in_thread(
            link_with_part_words, video_path, original, Path(self.config.temp_dir) / "source-ranges",
        )

    def _release_part_words(self, video_path: Optional[Path]) -> None:
        release_part_link(video_path, Path(self.config.temp_dir) / "source-ranges")

    async def _source_media_duration(self, task_id: str, task: Dict[str, Any]) -> Optional[float]:
        """The whole VOD's length; a picked part's task is charged (and stores) the part."""
        metadata = (
            await self.task_repo.load_source_metadata(self.db, task_id)
            if self.db is not None else {}
        )
        return media_duration(task, metadata)

    async def _load_task_source_settings(self, task_id: str) -> Dict[str, Any]:
        defaults = {
            "output_format": "vertical",
            "add_subtitles": True,
            "clip_length": "auto",
            **normalize_clip_cleanup_settings(),
        }
        parsed = (
            await self.task_repo.load_source_metadata(self.db, task_id)
            if self.db is not None else {}
        )
        if not parsed:
            redis_client = redis.Redis(
                host=self.config.redis_host,
                port=self.config.redis_port,
                password=self.config.redis_password,
                decode_responses=True,
            )
            try:
                payload = await redis_client.get(f"task_source:{task_id}")
            except Exception as exc:
                logger.warning(
                    "Falling back to default task source settings for task %s: %s",
                    task_id, exc,
                )
                return defaults
            finally:
                try:
                    await redis_client.aclose()
                except Exception:
                    pass

            if not payload:
                return defaults
            try:
                parsed = json.loads(payload)
            except json.JSONDecodeError:
                return defaults
            if not isinstance(parsed, dict):
                return defaults

        output_format = parsed.get("output_format", defaults["output_format"])
        if output_format not in VALID_OUTPUT_FORMATS:
            output_format = defaults["output_format"]

        add_subtitles = parsed.get("add_subtitles", defaults["add_subtitles"])
        if not isinstance(add_subtitles, bool):
            add_subtitles = defaults["add_subtitles"]

        return {
            "output_format": output_format,
            "add_subtitles": add_subtitles,
            "clip_length": normalize_clip_length(parsed.get("clip_length")),
            **normalize_clip_cleanup_settings(
                parsed.get("cut_long_pauses"),
                parsed.get("pause_threshold_ms"),
                parsed.get("remove_filler_words"),
                parsed.get("filtered_words"),
            ),
            # Only a task made from a picked part of its video carries this.
            **({SOURCE_RANGE_KEY: parsed[SOURCE_RANGE_KEY]} if SourceRange.from_metadata(parsed) else {}),
        }
