"""Preview clips and their on-demand full-quality renders.

With the ``preview_renders`` flag, generation encodes each clip as a small,
fast preview and keeps the inputs of its final pass (the stitched source, the
burned captions and the framing plan) next to it. The first time someone needs
the real file (a download, a ZIP, a preset export, a social post, the studio)
this module queues ``render_full_clip`` on the ``supoclip_finals`` queue. That
job repeats the final pass at full quality from the same inputs, publishes the
file, then swaps it into ``generated_clips.file_path``. Every reader of
``file_path`` gets the full clip from then on, and a second download is free.

State lives on the clip row (``render_quality`` and ``full_render_*``), so the
API and a worker on another machine never share a disk. A clip only ever moves
from preview to full.
"""

from __future__ import annotations

import asyncio
import json
import logging
import shutil
import threading
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_config
from ..media_storage import STORAGE_ERRORS, MediaStorage, is_s3, media_work
from ..observability import set_trace_id, set_user_id
from ..repositories.clip_repository import ClipRepository, full_render_state
from ..repositories.media_repository import queue_delete, render_inputs_prefix
from ..utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

FULL_RENDER_JOB_NAME = "render_full_clip"
# Its own queue and worker process: a user is waiting, so these never sit
# behind generation jobs that can run for hours on supoclip_tasks.
FULL_RENDER_QUEUE = "supoclip_finals"
FULL_RENDER_JOB_TIMEOUT_SECONDS = 20 * 60
# A running render touches its row every few seconds.
FULL_RENDER_STALE_SECONDS = 180
# A queued render whose job was lost (Redis flushed) is queued again after this.
FULL_RENDER_QUEUED_STALE_SECONDS = 10 * 60
# Social publishing runs on a worker; it may wait this long for a render.
PUBLISH_WAIT_SECONDS = 10 * 60
POLL_SECONDS = 1.5

PREPARING_CODE = "FULL_QUALITY_PREPARING"
FAILED_CODE = "FULL_QUALITY_FAILED"
PREPARING_MESSAGE = "Preparing the full-quality clip. Try again in a moment."
FAILED_MESSAGE = "Could not prepare the full-quality clip. Try again."
INPUTS_MISSING_MESSAGE = "The full-quality version of this clip can't be made. Regenerate the clip."
WORKER_UNAVAILABLE_MESSAGE = "The render worker is unavailable. Try again shortly."
RENDER_DIR_NAME = "full-renders"
INPUTS_DIR_NAME = "render-inputs"

TRIGGERS = {"download", "zip", "export", "publish", "studio", "edit", "api"}

RENDER_INPUT_NAMES = ("render.json", "render-source.bin", "render-captions.ass")


class FullRenderUnavailable(RuntimeError):
    pass


def is_preview(clip: Optional[dict]) -> bool:
    return bool(clip) and (clip.get("render_quality") or "full") == "preview"


def job_id_for(clip_id: str) -> str:
    # One live job per clip and request window; a later request after a
    # failure or a lost job gets a fresh id.
    return f"full-render:{clip_id}:{int(time.time())}"


def local_inputs_dir(temp_dir: str, task_id: str, token: str) -> Path:
    return Path(temp_dir) / INPUTS_DIR_NAME / str(task_id) / token


def publish_render_inputs(storage: MediaStorage, directory: Path, task_id: str) -> str:
    """Make a preview's render inputs readable by any worker. Returns the recipe reference.

    With object storage the inputs go to ``render-inputs/<task>/<token>/`` and
    the local copy is removed; locally they stay where the render wrote them.
    """
    recipe = directory / RENDER_INPUT_NAMES[0]
    if not recipe.exists():
        raise FileNotFoundError(f"No render recipe in {directory}")
    if not storage.enabled:
        return str(recipe)
    prefix = storage.key(INPUTS_DIR_NAME, str(task_id), directory.name)
    uploaded: list[str] = []
    try:
        # The recipe goes last: a reference to it means everything else is there.
        for name in (*RENDER_INPUT_NAMES[1:], RENDER_INPUT_NAMES[0]):
            path = directory / name
            if path.exists():
                uploaded.append(storage.upload_file(path, f"{prefix}/{name}"))
    except BaseException:
        for reference in uploaded:
            try:
                storage.client.delete_object(Bucket=storage.bucket, Key=storage.parse(reference))
            except STORAGE_ERRORS:
                logger.warning("Could not remove partial render inputs %s", reference)
        raise
    shutil.rmtree(directory, ignore_errors=True)
    return storage.reference(f"{prefix}/{RENDER_INPUT_NAMES[0]}")


def fetch_render_inputs(storage: MediaStorage, recipe_reference: str, directory: Path) -> Path:
    """Local directory holding the inputs named by ``recipe_reference``."""
    if not is_s3(recipe_reference):
        local = Path(recipe_reference).parent
        if not (local / RENDER_INPUT_NAMES[0]).exists():
            raise FileNotFoundError(INPUTS_MISSING_MESSAGE)
        return local
    directory.mkdir(parents=True, exist_ok=True)
    base = recipe_reference.rsplit("/", 1)[0]
    if not storage.fetch(recipe_reference, directory / RENDER_INPUT_NAMES[0]):
        raise FileNotFoundError(INPUTS_MISSING_MESSAGE)
    if not storage.fetch(f"{base}/{RENDER_INPUT_NAMES[1]}", directory / RENDER_INPUT_NAMES[1]):
        raise FileNotFoundError(INPUTS_MISSING_MESSAGE)
    recipe = json.loads((directory / RENDER_INPUT_NAMES[0]).read_text())
    if recipe.get("captions") and not storage.fetch(
        f"{base}/{RENDER_INPUT_NAMES[2]}", directory / RENDER_INPUT_NAMES[2]
    ):
        raise FileNotFoundError(INPUTS_MISSING_MESSAGE)
    return directory


def update_render_recipe(directory: Path, **fields: Any) -> None:
    path = directory / RENDER_INPUT_NAMES[0]
    recipe = json.loads(path.read_text())
    recipe.update(fields)
    path.write_text(json.dumps(recipe, default=str))


def render_full_clip_file(
    inputs_dir: Path,
    output_path: Path,
    broll_overlays: Optional[list] = None,
) -> None:
    """The final pass at full quality, at the plan's resolution (one encode)."""
    from ..video_utils import render_full_from_inputs

    recipe = json.loads((inputs_dir / RENDER_INPUT_NAMES[0]).read_text())
    max_short_side = recipe.get("max_short_side")
    if not render_full_from_inputs(
        inputs_dir, output_path, broll_overlays,
        max_short_side=int(max_short_side) if max_short_side else None,
    ):
        raise RuntimeError("ffmpeg full render failed")


def serialize_state(clip: dict) -> dict:
    return {
        "clip_id": clip["id"],
        "render_quality": clip.get("render_quality") or "full",
        "full_render": full_render_state(clip),
    }


def summarize(clips: Iterable[dict]) -> dict:
    """Task-level progress over every clip, for the "Download all" flow."""
    states = [serialize_state(clip) for clip in clips]
    total = len(states)
    ready = sum(1 for state in states if state["full_render"]["status"] == "ready")
    failed = sum(1 for state in states if state["full_render"]["status"] == "failed")
    progress = (
        round(sum(
            100 if state["full_render"]["status"] == "ready" else state["full_render"]["progress"]
            for state in states
        ) / total)
        if total
        else 100
    )
    return {
        "total": total,
        "ready": ready,
        "failed": failed,
        "progress": progress,
        "status": "ready" if ready == total else ("failed" if failed else "preparing"),
        "clips": states,
    }


async def request_full_render(
    db: AsyncSession,
    clip: dict,
    *,
    queue,
    trigger: str = "api",
    user_id: Optional[str] = None,
) -> dict:
    """Queue the clip's full render unless it is ready or already under way.

    Returns the clip's state. Safe to call from many requests at once: only the
    one that moves the row to ``queued`` enqueues a job.
    """
    if not is_preview(clip):
        return serialize_state(clip)
    if not clip.get("render_inputs_path"):
        await db.execute(
            text(
                "UPDATE generated_clips SET full_render_status = 'failed', full_render_error = :error, "
                "full_render_updated_at = NOW() WHERE id = :id AND render_quality = 'preview'"
            ),
            {"id": clip["id"], "error": INPUTS_MISSING_MESSAGE},
        )
        await db.commit()
        return serialize_state(await ClipRepository.get_clip_by_id(db, clip["id"]) or clip)
    claimed = (
        await db.execute(
            text(
                """
                UPDATE generated_clips
                SET full_render_status = 'queued',
                    full_render_progress = 0,
                    full_render_error = NULL,
                    full_render_updated_at = NOW()
                WHERE id = :id
                  AND render_quality = 'preview'
                  AND (
                    full_render_status IS NULL
                    OR full_render_status = 'failed'
                    OR (full_render_status = 'running'
                        AND full_render_updated_at < NOW() - make_interval(secs => :running_stale))
                    OR (full_render_status = 'queued'
                        AND full_render_updated_at < NOW() - make_interval(secs => :queued_stale))
                  )
                RETURNING id
                """
            ),
            {
                "id": clip["id"],
                "running_stale": float(FULL_RENDER_STALE_SECONDS),
                "queued_stale": float(FULL_RENDER_QUEUED_STALE_SECONDS),
            },
        )
    ).fetchone()
    await db.commit()
    if claimed:
        try:
            await queue.enqueue_job(
                FULL_RENDER_JOB_NAME,
                clip["id"],
                _job_id=job_id_for(clip["id"]),
                _queue_name=FULL_RENDER_QUEUE,
            )
        except Exception:
            logger.exception("Could not enqueue the full render of clip %s", clip["id"])
            await _mark_failed(db, clip["id"], WORKER_UNAVAILABLE_MESSAGE, statuses=("queued",))
            raise FullRenderUnavailable(WORKER_UNAVAILABLE_MESSAGE) from None
        _capture_request(clip, trigger, user_id)
    refreshed = await ClipRepository.get_clip_by_id(db, clip["id"])
    await db.commit()
    return serialize_state(refreshed or clip)


def _capture_request(clip: dict, trigger: str, user_id: Optional[str]) -> None:
    from ..analytics import capture_event

    capture_event(
        "clip_full_quality_requested",
        user_id,
        {
            "task_id": clip.get("task_id"),
            "clip_id": clip.get("id"),
            "trigger": trigger if trigger in TRIGGERS else "api",
        },
    )


async def wait_for_full_render(db: AsyncSession, clip_id: str, timeout: float) -> Optional[dict]:
    """Poll until the clip is full quality, its render failed, or ``timeout`` passes.

    No transaction stays open between polls.
    """
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        await db.rollback()
        clip = await ClipRepository.get_clip_by_id(db, clip_id)
        await db.rollback()
        if clip is None or not is_preview(clip) or full_render_state(clip)["status"] == "failed":
            return clip
        if time.monotonic() >= deadline:
            return clip
        await asyncio.sleep(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))


async def ensure_full_quality(
    db: AsyncSession,
    clip: dict,
    *,
    queue,
    trigger: str,
    user_id: Optional[str],
    timeout: float,
) -> dict:
    """Return the clip once it is full quality, rendering it first if needed.

    Raises ``FullRenderPending`` (still rendering after ``timeout``) or
    ``FullRenderFailed``.
    """
    if not is_preview(clip):
        return clip
    state = await request_full_render(db, clip, queue=queue, trigger=trigger, user_id=user_id)
    if state["full_render"]["status"] == "failed":
        raise FullRenderFailed(state)
    current = await wait_for_full_render(db, clip["id"], timeout)
    if current is None:
        raise FullRenderFailed({"clip_id": clip["id"], "render_quality": "preview",
                                "full_render": {"status": "failed", "progress": 0, "error": "Clip not found"}})
    if not is_preview(current):
        return current
    state = serialize_state(current)
    if state["full_render"]["status"] == "failed":
        raise FullRenderFailed(state)
    raise FullRenderPending(state)


class FullRenderPending(Exception):
    def __init__(self, state: dict):
        super().__init__(PREPARING_MESSAGE)
        self.state = state


class FullRenderFailed(Exception):
    def __init__(self, state: dict):
        super().__init__((state.get("full_render") or {}).get("error") or FAILED_MESSAGE)
        self.state = state


async def _mark_failed(db: AsyncSession, clip_id: str, message: str, *, statuses=("queued", "running")) -> None:
    await db.execute(
        text(
            "UPDATE generated_clips SET full_render_status = 'failed', full_render_error = :error, "
            "full_render_progress = 0, full_render_updated_at = NOW() "
            "WHERE id = :id AND render_quality = 'preview' AND full_render_status = ANY(:statuses)"
        ),
        {"id": clip_id, "error": message, "statuses": list(statuses)},
    )
    await db.commit()


async def _claim(db: AsyncSession, clip_id: str) -> Optional[dict]:
    row = (
        await db.execute(
            text(
                """
                UPDATE generated_clips
                SET full_render_status = 'running',
                    full_render_progress = 1,
                    full_render_error = NULL,
                    full_render_updated_at = NOW()
                WHERE id = :id
                  AND render_quality = 'preview'
                  AND (full_render_status = 'queued'
                       OR (full_render_status = 'running'
                           AND full_render_updated_at < NOW() - make_interval(secs => :stale)))
                RETURNING id
                """
            ),
            {"id": clip_id, "stale": float(FULL_RENDER_STALE_SECONDS)},
        )
    ).fetchone()
    await db.commit()
    if not row:
        return None
    return await ClipRepository.get_clip_by_id(db, clip_id)


async def _pump_progress(clip_id: str, progress: dict, stop: threading.Event) -> None:
    """Heartbeat while ffmpeg runs; progress is an estimate from the clip length."""
    from ..database import AsyncSessionLocal

    started = time.monotonic()
    expected = max(8.0, float(progress.get("expected") or 30.0))
    while not stop.is_set():
        await asyncio.sleep(2)
        if stop.is_set():
            return
        value = max(int(progress.get("floor") or 1), min(95, int(5 + 85 * (time.monotonic() - started) / expected)))
        try:
            async with AsyncSessionLocal() as db:
                await db.execute(
                    text(
                        "UPDATE generated_clips SET full_render_progress = GREATEST(full_render_progress, :progress), "
                        "full_render_updated_at = NOW() WHERE id = :id AND full_render_status = 'running'"
                    ),
                    {"id": clip_id, "progress": value},
                )
                await db.commit()
        except Exception:
            logger.warning("Full render %s progress update failed", clip_id, exc_info=True)


async def _swap_in(db: AsyncSession, clip: dict, reference: str) -> bool:
    """Point the clip at its full render; the preview and its inputs are deleted."""
    swapped = (
        await db.execute(
            text(
                """
                UPDATE generated_clips
                SET file_path = :reference,
                    render_quality = 'full',
                    render_inputs_path = NULL,
                    full_render_status = NULL,
                    full_render_progress = 100,
                    full_render_error = NULL,
                    full_render_updated_at = NOW(),
                    updated_at = NOW()
                WHERE id = :id AND file_path = :preview AND render_quality = 'preview'
                RETURNING id
                """
            ),
            {"id": clip["id"], "reference": reference, "preview": clip["file_path"]},
        )
    ).fetchone()
    if not swapped:
        await db.rollback()
        return False
    await queue_delete(db, clip["file_path"])
    if clip.get("render_inputs_path"):
        await queue_delete(db, render_inputs_prefix(clip["render_inputs_path"]), prefix=True)
    await db.commit()
    return True


def _store_output(storage: MediaStorage, output: Path, task_id: str, clips_dir: Path) -> str:
    """Publish the finished file with its sidecars. Local storage keeps it under clips/."""
    if storage.enabled:
        return storage.persist_clip(str(output), task_id)
    clips_dir.mkdir(parents=True, exist_ok=True)
    target = clips_dir / f"{output.stem}_full_{uuid4().hex[:8]}{output.suffix}"
    for sidecar in output.parent.glob(f"{output.stem}.*.json"):
        shutil.move(str(sidecar), str(target.with_name(f"{target.stem}{sidecar.name[len(output.stem):]}")))
    shutil.move(str(output), str(target))
    return str(target)


def _discard(storage: MediaStorage, reference: Optional[str]) -> None:
    if not reference:
        return
    try:
        storage.delete(reference)
    except (STORAGE_ERRORS + (ValueError,)):
        logger.warning("Could not remove an abandoned full render %s", reference)


@media_work
async def render_full_clip(ctx, clip_id: str) -> dict:
    """ARQ job: the clip's final pass at full quality, swapped in for its preview."""
    from ..broll import broll_overlays_for
    from ..clip_source_map import clip_source_map_path, load_clip_broll
    from ..database import AsyncSessionLocal
    from ..font_registry import fetch_user_font
    from ..repositories.task_repository import TaskRepository

    set_trace_id(f"full-render-{clip_id}")
    async with AsyncSessionLocal() as db:
        clip = await _claim(db, clip_id)
        if clip is None:
            return {"clip_id": clip_id, "status": "skipped"}
        task = await TaskRepository.get_task_by_id(db, clip["task_id"]) or {}
        await db.commit()
    user_id = task.get("user_id")
    set_user_id(user_id)
    logger.info("Rendering clip %s at full quality", clip_id)

    storage = MediaStorage()
    config = get_config()
    work_dir = Path(config.temp_dir) / RENDER_DIR_NAME / f"{clip_id}-{uuid4().hex[:8]}"
    stop = threading.Event()
    progress: Dict[str, Any] = {"expected": float(clip.get("duration") or 30.0)}
    stored: Optional[str] = None
    started = time.monotonic()
    try:
        work_dir.mkdir(parents=True, exist_ok=True)
        inputs_dir = await run_in_thread(
            fetch_render_inputs, storage, clip["render_inputs_path"], work_dir / "inputs"
        )
        # The preview's sidecars (source ranges, B-roll, caption position, the
        # editor's layout) describe the full render too.
        preview_path = await run_in_thread(storage.local_path, clip["file_path"], metadata_only=True)
        output = work_dir / clip["filename"]
        source_map = clip_source_map_path(Path(preview_path))
        if source_map.exists():
            shutil.copyfile(source_map, clip_source_map_path(output))
        placements = load_clip_broll(Path(preview_path)) if source_map.exists() else []
        overlays = await broll_overlays_for(placements) if placements else []
        font_family = task.get("font_family")
        if font_family and user_id:
            try:
                await run_in_thread(fetch_user_font, font_family, user_id)
            except Exception:
                logger.warning("Could not fetch font for clip %s; captions use the default", clip_id, exc_info=True)

        pump = asyncio.create_task(_pump_progress(clip_id, progress, stop))
        try:
            await run_in_thread(render_full_clip_file, inputs_dir, output, overlays)
        finally:
            stop.set()
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):
                pass

        stored = await run_in_thread(
            _store_output, storage, output, clip["task_id"], Path(config.temp_dir) / "clips"
        )
        async with AsyncSessionLocal() as db:
            swapped = await _swap_in(db, clip, stored)
        if not swapped:
            # The clip was edited, regenerated or deleted meanwhile.
            await run_in_thread(_discard, storage, stored)
            return {"clip_id": clip_id, "status": "abandoned"}
        logger.info(
            "Clip %s is full quality (%.1fs render)", clip_id, time.monotonic() - started
        )
        return {"clip_id": clip_id, "status": "succeeded"}
    except asyncio.CancelledError:
        await _record_failure(clip_id, FAILED_MESSAGE)
        await run_in_thread(_discard, storage, stored)
        raise
    except FileNotFoundError as exc:
        logger.error("Full render of clip %s has no inputs: %s", clip_id, exc)
        await _record_failure(clip_id, INPUTS_MISSING_MESSAGE)
        return {"clip_id": clip_id, "status": "failed"}
    except Exception:
        logger.exception("Full render of clip %s failed", clip_id)
        await _record_failure(clip_id, FAILED_MESSAGE)
        await run_in_thread(_discard, storage, stored)
        return {"clip_id": clip_id, "status": "failed"}
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def _record_failure(clip_id: str, message: str) -> None:
    from ..database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as db:
            await _mark_failed(db, clip_id, message)
    except Exception:
        logger.exception("Could not record the failed full render of clip %s", clip_id)


async def finalize_locally(
    inputs_dir: Path, clip_path: Path, broll_placements: Optional[list] = None
) -> None:
    """Generation fallback when the inputs could not be stored: render full quality now."""
    from ..broll import broll_overlays_for

    overlays = await broll_overlays_for(broll_placements) if broll_placements else []
    output = clip_path.with_name(f".{clip_path.stem}.final{clip_path.suffix}")
    try:
        await run_in_thread(render_full_clip_file, inputs_dir, output, overlays)
        output.replace(clip_path)
    finally:
        output.unlink(missing_ok=True)
        shutil.rmtree(inputs_dir, ignore_errors=True)
