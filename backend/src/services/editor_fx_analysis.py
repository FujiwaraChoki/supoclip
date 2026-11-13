"""Studio face track and person matte (``editor_fx_analysis``, a video-worker job; FINAL §5.5, §14.1).

``POST .../editor/fx/analysis`` enqueues it. On the session's base video (``layout.mp4`` when the draft plays the
generated framing, else ``clean.mp4``) it computes, when missing:

- the **face track** (YuNet, 6 fps, picture space of the base video, S time) → session ``faceTrack``
  ``{"base": "layout"|"clean", "track": FaceTrack}``;
- the **person matte** ``matte.mp4`` (PP-HumanSeg, grey H.264, 540x960 at 15 fps, S time), only when the draft draws
  text behind the person. It is **published to the bucket before** the session records ``matteVersion`` (AI-H7), so
  any machine that sees the version can fetch the file (``restore_editor_media(matte_version=...)``).

Results are merged into the session's asset row only if the session is still ready with the same ``mediaVersion``
(a rebuild meanwhile discards them). ``fxAnalysis`` tells the studio how the last run went. Idempotent: a session
that already has what its draft needs is left alone, and the route's job id (``analysis_job_id``) is per
``mediaVersion``.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

from ..editor_document import editor_dir
from ..editor_fx_render import empty_face_track
from ..editor_state import editor_key, get_store
from ..media_storage import MediaStorage, media_work
from ..utils.async_helpers import run_in_thread

logger = logging.getLogger(__name__)

ANALYSIS_JOB_TIMEOUT_SECONDS = 1800
FACE_FPS = 6.0


def _raw_fx(document: dict | None) -> dict:
    fx = (document or {}).get("fx") if isinstance(document, dict) else None
    return fx if isinstance(fx, dict) else {}


def analysis_needs(document: dict | None) -> tuple[bool, bool]:
    """``(face, matte)`` a stored draft (raw JSON) asks the analysis for.

    The face track is always worth having (zooms, doodles and bleep covers anchor on it). The matte is needed only
    by behind-the-person text that is not switched off. Read leniently: an unknown cue shape is ignored.
    """
    fx = _raw_fx(document)
    disabled = set(fx.get("disabled") or [])
    matte = "accent" not in disabled and any(
        isinstance(cue, dict) and cue.get("kind") == "accent" and cue.get("variant") == "behind"
        for cue in fx.get("cues") or []
    )
    return True, matte


def session_base(document: dict | None, asset: dict) -> str:
    """The video a studio render frames: ``layout.mp4`` for auto-fit vertical drafts of sessions that have one
    (``render_document``'s rule), else ``clean.mp4``."""
    framing = (document or {}).get("framing") or {}
    auto_vertical = framing.get("fit") == "auto" and (framing.get("aspect") or "vertical") == "vertical"
    return "layout" if auto_vertical and asset.get("hasLayout") else "clean"


def analysis_job_id(key: str, media_version: str | None, *, matte: bool) -> str:
    """The ARQ job id the route uses: one face run and one matte run per preparation of a session."""
    return f"fxan:{key}:{media_version or 'v0'}:{'matte' if matte else 'face'}"


def _face_done(asset: dict, base: str) -> bool:
    face = asset.get("faceTrack")
    return isinstance(face, dict) and face.get("base") == base and isinstance(face.get("track"), dict)


def _matte_done(asset: dict, base: str) -> bool:
    return bool(asset.get("matteVersion")) and (asset.get("matteBase") or "clean") == base


async def merge_session_asset(key: str, media_version: str | None, patch: dict[str, Any]) -> bool:
    """Merge ``patch`` into the session's asset if it is still ready on ``media_version`` (compare-and-set)."""
    from sqlalchemy import text

    from ..database import get_session_maker

    async with get_session_maker()() as db:
        result = await db.execute(text("""
            UPDATE editor_sessions
            SET asset = asset || CAST(:patch AS JSONB), updated_at = NOW()
            WHERE id = :id AND asset->>'status' = 'ready'
              AND COALESCE(asset->>'mediaVersion', '') = :version
        """), {"id": key, "patch": json.dumps(patch, allow_nan=False), "version": media_version or ""})
        await db.commit()
        return bool(result.rowcount)


def _compute_matte(video: Path, directory: Path, storage: MediaStorage, version: str) -> bool:
    """Compute the matte into a scratch file beside the session's media, then install, publish and mark it as
    ``version`` in one step under the session's lock (``MediaStorage.publish_editor_matte``): a render on this
    machine never reads half a file, and a concurrent restore cannot swap the file between install and upload."""
    from ..media.fx_analysis import matte_for_video

    temporary = directory / f".matte-{uuid.uuid4().hex}.mp4"
    try:
        if not matte_for_video(video, temporary) or not temporary.is_file() or temporary.stat().st_size == 0:
            return False
        # Publish first: a recorded matteVersion must always be fetchable (AI-H7).
        storage.publish_editor_matte(directory, temporary, version)
        return True
    finally:
        temporary.unlink(missing_ok=True)


@media_work
async def editor_fx_analysis(ctx, task_id: str, clip_id: str, filename: str) -> dict:
    """Face track (and matte when the draft needs one) for a studio session. Never raises for a bad video: the
    studio then keeps its fallbacks (doodles become stickers, behind text draws in front)."""
    from ..config import get_config
    from ..media.fx_analysis import face_track_for_video
    from .editor_service import restore_session_media

    key = editor_key(task_id, clip_id, filename)
    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    session = await get_store().load(key)
    if not session or (session.get("asset") or {}).get("status") != "ready":
        logger.info("editor_fx_analysis_skipped key=%s reason=not_ready", key)
        return {"status": "skipped"}
    asset = session["asset"]
    media_version = asset.get("mediaVersion")
    document = (session.get("draft") or {}).get("document") or session.get("original") or {}
    base = session_base(document, asset)
    _, need_matte = analysis_needs(document)
    todo_face = not _face_done(asset, base)
    todo_matte = need_matte and not _matte_done(asset, base)
    if not (todo_face or todo_matte):
        return {"status": "done", "cached": True}

    # startedAt lets the studio tell a run whose worker died (the job's tries exhausted) from one still working:
    # a "running" status older than ANALYSIS_JOB_TIMEOUT_SECONDS * 2 is stale and can be queued again.
    await merge_session_asset(key, media_version, {"fxAnalysis": {"status": "running", "base": base,
                                                                   "startedAt": int(time.time())}})
    storage = MediaStorage()
    patch: dict[str, Any] = {}
    outcome = {"face": None, "matte": None}
    try:
        await restore_session_media(storage, directory, has_layout=bool(asset.get("hasLayout")), version=media_version)
        video = directory / ("layout.mp4" if base == "layout" else "clean.mp4")
        if not video.exists():
            raise FileNotFoundError(f"{video.name} is not available")
        if todo_face:
            track = await run_in_thread(face_track_for_video, video, fps=FACE_FPS)
            outcome["face"] = bool(track and track.get("samples"))
            # No detector or no frames: record an empty track so face cues settle on their fallbacks.
            patch["faceTrack"] = {"base": base, "track": track or empty_face_track(FACE_FPS)}
        if todo_matte:
            version = f"{media_version or 'local'}.{uuid.uuid4().hex[:12]}"
            made = await run_in_thread(_compute_matte, video, directory, storage, version)
            outcome["matte"] = made
            if made:
                patch.update(matteVersion=version, matteBase=base)
        patch["fxAnalysis"] = {"status": "done", "base": base, **{k: v for k, v in outcome.items() if v is not None}}
    except Exception:
        logger.exception("editor_fx_analysis_failed key=%s", key)
        patch["fxAnalysis"] = {"status": "failed", "base": base}
    recorded = await merge_session_asset(key, media_version, patch)
    if not recorded:
        # Rebuilt or deleted meanwhile: its new media gets its own analysis.
        logger.info("editor_fx_analysis_discarded key=%s media_version=%s", key, media_version)
        return {"status": "discarded"}
    logger.info("editor_fx_analysis key=%s base=%s face=%s matte=%s", key, base, outcome["face"], outcome["matte"])
    return {"status": patch["fxAnalysis"]["status"], **{k: v for k, v in outcome.items() if v is not None}}
