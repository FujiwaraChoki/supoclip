"""The clip studio's FX export (FINAL §14.1): ``render_document`` hands a document with dynamic edits to
``render_document_fx`` (one hook in ``editor_document.render_document``), which renders it through the same
``media.fx_chain.run_fx_chain`` as generation.

Clocks (§1): the document stores S (``clean.mp4`` seconds); the chain draws in E (after the studio's segment cuts,
``sum(end - start)``) and writes F (after the time warp).

- **P1 head** is ``render_document``'s own graph through B-roll (segment cuts, framing, the ``doc.effects`` filters
  before the grade, B-roll). The chain adds the reaction PiP and, for behind-the-person text, the session's
  ``matte.mp4`` taken through the same per-segment ``trim``/``concat`` and the same framing geometry (AR-B4).
- **Face track**: the session's track (S, picture space of the base video) is resampled onto E at 6 fps through
  ``locate(e)``, with a sample on each side of every cut so a face jumps with the picture instead of sliding across
  the cut. ``build_scene`` then places it in the output frame with ``picture_rect``. fx-core's ``studioScene`` mirrors
  this; ``shared/fx/fixtures/scene`` pins both.
- **Audio**: studio exports have no loudness stage (``loudness="none"``); the voice follows the volume slider, mute
  is silence, and SFX and music keep their planned levels (AR-M3).
- **Fallback ladder (§6.6)**: full → full without B-roll (only when P1 failed with B-roll in the graph) → safe (P1 →
  P3, fallback ASS with the original hook title, audio FX kept, no warp) → none (the legacy studio render with the
  fallback template). The 720p plan cap (``cap_export_for_plan``) runs after this returns, in the export job.

Inputs that live outside the document (the session face track, the restored matte, reaction GIFs) are bound for the
render by ``studio_fx_inputs`` around the ``run_in_thread(render_document, ...)`` call. Every file this module writes
lives in the render's own scratch directory and is removed before it returns (no shared disk, §0.4.2).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, AsyncIterator, Callable, Iterable, Sequence

from .edit_fx import env as fx_env
from .edit_fx.caption_style import fx_caption_style_from_document
from .edit_fx.censor import censored_words
from .edit_fx.compat import known_fields, log_dropped
from .edit_fx.contracts import FaceTrack, FxCaptionStyle, FxPlan, FxScene, MatteWindow, is_fx_template
from .edit_fx.mapping import mapped_cues
from .edit_fx.warp import build_warp, identity_warp

logger = logging.getLogger(__name__)

SESSION_FACE_FPS = 6.0          # E samples per second of the resampled session face track
FACE_MAX_GAP = 0.5              # == fx-core FACE_MAX_GAP: samples further apart are not interpolated across
LOCATE_EPSILON = 0.001          # == document.ts locate(): the last instant of a timeline belongs to its last segment
AV_TOLERANCE = 0.034            # one frame at 30 fps (§6.6)
MAX_PLAN_CUES = 400             # FxPlan.cues max_length; repeated segments can emit more after mapping
FACE_DECIMALS = 6
MATTE_NAME = "matte.mp4"
MATTE_VERSION_MARKER = ".matte-version"
AUDIO_BITRATE = "192k"

Segment = tuple[str, float, float]


# ----------------------------------------------------------------------------- inputs bound around a render

@dataclass(frozen=True)
class StudioFxInputs:
    """What a studio FX render needs beyond the document: the session's face track (``asset["faceTrack"]``:
    ``{"base": "layout"|"clean", "track": FaceTrack}``), the restored ``matte.mp4`` and the base it was computed on,
    and reaction GIFs fetched into the render's scratch (keyed by the document's S cue id)."""

    face_track: dict | None = None
    matte_path: Path | None = None
    matte_base: str | None = None
    reaction_files: dict[str, Path] = field(default_factory=dict)


_inputs: contextvars.ContextVar[StudioFxInputs | None] = contextvars.ContextVar("studio_fx_inputs", default=None)


def bound_inputs() -> StudioFxInputs:
    return _inputs.get() or StudioFxInputs()


@contextlib.contextmanager
def bind_inputs(inputs: StudioFxInputs):
    """Bind ``inputs`` for renders started in this context (``run_in_thread`` copies the context into the thread)."""
    token = _inputs.set(inputs)
    try:
        yield inputs
    finally:
        _inputs.reset(token)


def _doc_fx(document) -> Any | None:
    return getattr(document, "fx", None)


def _fx_cues(document) -> list:
    fx = _doc_fx(document)
    return list(getattr(fx, "cues", None) or []) if fx is not None else []


def document_needs_matte(document) -> bool:
    """Behind-the-person text that is not switched off (the only consumer of ``matte.mp4``)."""
    fx = _doc_fx(document)
    if fx is None or "accent" in (getattr(fx, "disabled", None) or []):
        return False
    return any(c.kind == "accent" and getattr(c, "variant", None) == "behind" for c in _fx_cues(document))


def verified_matte(directory: Path, matte_version: str | None) -> Path | None:
    """``matte.mp4`` in a session directory when its ``.matte-version`` marker is ``matte_version``."""
    if not matte_version:
        return None
    path = Path(directory) / MATTE_NAME
    marker = Path(directory) / MATTE_VERSION_MARKER
    try:
        if path.is_file() and path.stat().st_size > 0 and marker.read_text(encoding="utf-8").strip() == matte_version:
            return path
    except OSError:
        pass
    return None


async def document_fx_assets(document, scratch: Path) -> dict[str, Path]:
    """Reaction GIFs of the document's enabled reaction cues as local mp4s in ``scratch``, keyed by S cue id.

    A missing one is skipped (the render then draws no PiP and no frame for it), like missing B-roll footage.
    """
    from .edit_fx.reactions import ensure_reaction_file

    fx = _doc_fx(document)
    if fx is None or "reaction" in (getattr(fx, "disabled", None) or []):
        return {}
    files: dict[str, Path] = {}
    for cue in _fx_cues(document):
        if cue.kind != "reaction" or cue.id in files:
            continue
        path = await ensure_reaction_file(cue, scratch)
        if path is not None:
            files[cue.id] = Path(path)
    return files


@contextlib.asynccontextmanager
async def studio_fx_inputs(document, asset: dict | None, directory: Path, *, storage=None) -> AsyncIterator[StudioFxInputs]:
    """Gather and bind the FX inputs of one studio render (export or combine), then clean them up.

    ``asset`` is the session's asset row (``faceTrack``, ``matteVersion``, ``matteBase``, ``mediaVersion``,
    ``hasLayout``). The matte is restored from the bucket only when the document draws behind the person; a copy
    whose ``.matte-version`` does not match the session is never used. Usage (``export_editor``)::

        async with studio_fx_inputs(document, session["asset"], directory):
            await run_in_thread(render_document, directory, job_id, document, preset, broll_files, ...)
    """
    asset = dict(asset or {})
    fx = _doc_fx(document)
    if fx is None:
        with bind_inputs(StudioFxInputs()) as inputs:
            yield inputs
        return
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".fx-assets-", dir=str(directory)))
    try:
        reactions: dict[str, Path] = {}
        try:
            reactions = await document_fx_assets(document, scratch)
        except Exception as exc:  # noqa: BLE001 - decorative layers never cost the export (§0.4.7)
            logger.warning("fx_studio_reactions_failed (%s)", exc)
        matte: Path | None = None
        matte_version = asset.get("matteVersion")
        if matte_version and document_needs_matte(document):
            from .media_storage import MediaStorage
            from .utils.async_helpers import run_in_thread

            try:
                await run_in_thread(
                    (storage or MediaStorage()).restore_editor_media, directory,
                    has_layout=bool(asset.get("hasLayout")), version=asset.get("mediaVersion"),
                    matte_version=matte_version,
                )
            except Exception as exc:  # noqa: BLE001 - without the matte the text draws in front
                logger.warning("fx_studio_matte_restore_failed (%s)", exc)
            matte = verified_matte(directory, matte_version)
        face = asset.get("faceTrack") if isinstance(asset.get("faceTrack"), dict) else None
        inputs = StudioFxInputs(face_track=face, matte_path=matte, matte_base=asset.get("matteBase"),
                                reaction_files=reactions)
        with bind_inputs(inputs):
            yield inputs
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ----------------------------------------------------------------------------- pure studio geometry and timing

def _segments(document_or_segments) -> list[Segment]:
    """Play-order segments ``(id, s_start, s_end)`` with source-continuous splits merged, keeping the first id
    (== ``editor_document.continuous_segments``). Accepts a document, ``Segment`` models or tuples."""
    raw = getattr(document_or_segments, "segments", document_or_segments)
    merged: list[Segment] = []
    for seg in raw:
        if isinstance(seg, (tuple, list)):
            item = (str(seg[0]), float(seg[1]), float(seg[2]))
        else:
            item = (str(seg.id), float(seg.start), float(seg.end))
        if merged and abs(merged[-1][2] - item[1]) < 1e-9:
            merged[-1] = (merged[-1][0], merged[-1][1], item[2])
        else:
            merged.append(item)
    return merged


def edit_duration(segments: Sequence[Segment]) -> float:
    return sum(end - start for _, start, end in segments)


def locate(segments: Sequence[Segment], e: float) -> tuple[int, float]:
    """(segment index, S) playing at edit time ``e`` (== ``document.ts`` ``locate``)."""
    total = edit_duration(segments)
    at = min(max(e, 0.0), max(0.0, total - LOCATE_EPSILON))
    cursor = 0.0
    for index, (_, start, end) in enumerate(segments):
        length = end - start
        if cursor <= at < cursor + length:
            return index, start + at - cursor
        cursor += length
    index = len(segments) - 1
    _, start, end = segments[index]
    return index, start + at - (total - (end - start))


def output_geometry(framing, source_w: int, source_h: int, width: int, height: int) -> tuple[str, tuple[float, float, float, float]]:
    """The framing filter of ``render_document`` (scale, crop overflow, pad letterbox) and where the base picture
    lands in the output, normalised ``(x, y, w, h)`` (== ``document.ts`` ``frameRect`` / ``width``, ``height``)."""
    scale = (max if framing.fit == "cover" else min)(width / source_w, height / source_h) * framing.zoom
    rw, rh = max(2, round(source_w * scale / 2) * 2), max(2, round(source_h * scale / 2) * 2)
    cw, ch = min(width, rw), min(height, rh)
    geometry = (
        f"scale={rw}:{rh},crop={cw}:{ch}:{(rw - cw) * framing.x}:{(rh - ch) * framing.y},"
        f"pad={width}:{height}:{(width - cw) * framing.x}:{(height - ch) * framing.y}:black,setsar=1"
    )
    rect = ((width - rw) * framing.x / width, (height - rh) * framing.y / height, rw / width, rh / height)
    return geometry, rect


def effects_filter(effects, width: int) -> str:
    """``render_document``'s CSS-equivalent picture effects (applied before the FX grade, AP-H11)."""
    gain = effects.brightness / 100 * effects.contrast / 100
    offset = 128 * (1 - effects.contrast / 100)
    chain = (f"lutrgb=r='clip(val*{gain}+{offset},0,255)':g='clip(val*{gain}+{offset},0,255)':"
             f"b='clip(val*{gain}+{offset},0,255)',hue=h={effects.hue}:s={effects.saturation / 100}")
    if effects.blur:
        chain += f",gblur=sigma={effects.blur * width / 1080}"
    return chain


def segment_cut_graph(segments: Sequence[Segment], *, source: str, prefix: str) -> str:
    """``source`` cut to the play order (``trim`` + ``setpts`` per segment, one ``concat``).

    The result ends in an open chain (no output label) so callers append their own filters. Used for the B-roll-free
    head and for the matte, which must follow exactly the cuts of ``[0:v]`` (AR-B4).
    """
    if len(segments) == 1:
        _, start, end = segments[0]
        return f"{source}trim=start={start}:end={end},setpts=PTS-STARTPTS"
    labels = [f"[{prefix}{index}]" for index in range(len(segments))]
    split = f"{source}split={len(segments)}{''.join(f'[{prefix}in{index}]' for index in range(len(segments)))}"
    parts = [split]
    for index, (_, start, end) in enumerate(segments):
        parts.append(f"[{prefix}in{index}]trim=start={start}:end={end},setpts=PTS-STARTPTS{labels[index]}")
    parts.append(f"{''.join(labels)}concat=n={len(segments)}:v=1:a=0")
    return ";".join(parts)


def matte_filter_for(segments: Sequence[Segment], geometry: str, source_w: int, source_h: int,
                     source_fps: str = "30") -> str:
    """The filter taking ``matte.mp4`` (base-picture space, S time, grey) to output space and E time: luma expanded
    to full range, retimed to the base video's frame rate, the same per-segment cuts as ``[0:v]``, the base video's
    size, then the framing geometry. The chain appends ``scale=W:H,fps=30,format=gray`` and merges it as the alpha
    channel (``[k:v]`` is its input).

    The retime matters: ``trim`` keeps whole frames, so cutting the 15 fps matte directly would round every segment
    to 1/15 s and the matte would drift a frame per cut against the picture, whose trims round to its own rate.
    """
    cut = segment_cut_graph(segments, source="", prefix="fxm_")
    # The matte is stored as 4:2:0 H.264 (tv range) so browsers can play it: back to 0-255 before the cuts, so the
    # letterbox the geometry pads is 0 (no person) and a full matte stays 255.
    head = f"scale=in_range=tv:out_range=pc,format=gray,fps={source_fps},"
    return f"{head}{cut},scale={source_w}:{source_h}:flags=bilinear,{geometry}"


def probe_frame_rate(path: Path) -> str:
    """The base video's average frame rate as an ffmpeg rational (``30`` when unknown)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=avg_frame_rate,r_frame_rate",
             "-of", "json", str(path)], capture_output=True, text=True, timeout=60,
        )
        stream = (json.loads(result.stdout or "{}").get("streams") or [{}])[0]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return "30"
    for key in ("avg_frame_rate", "r_frame_rate"):
        value = str(stream.get(key) or "")
        match = re.fullmatch(r"(\d+)(?:/(\d+))?", value)
        if match and int(match.group(1)) > 0 and int(match.group(2) or 1) > 0:
            rate = int(match.group(1)) / int(match.group(2) or 1)
            if 1 <= rate <= 240:
                return value
    return "30"


# ----------------------------------------------------------------------------- face track (S → E)

def _clean_track(track: dict | None) -> FaceTrack | None:
    if not isinstance(track, dict):
        return None
    clean, dropped = known_fields(FaceTrack, track)
    log_dropped("editor_fx_render.face_track", dropped)
    try:
        return FaceTrack.model_validate(clean)
    except Exception as exc:  # noqa: BLE001 - a bad stored track only costs face-anchored drawing
        logger.warning("fx_studio_face_track_unreadable (%s)", exc)
        return None


def _round_row(row: Iterable[float]) -> list[float]:
    return [round(float(v), FACE_DECIMALS) for v in row]


def _track_at(times: Sequence[float], rows: Sequence[Sequence[Sequence[float]]], fps: float, s: float) -> list[list[float]]:
    """Face rows at source time ``s``: linear between bracketing samples ≤ ``FACE_MAX_GAP`` apart that hold the same
    number of faces with the same row lengths; else the nearer sample (ties to the earlier), but nothing across a
    longer gap more than half a sample step from either side; held up to ``FACE_MAX_GAP`` beyond the track's ends."""
    if not times:
        return []
    lo, hi, i = 0, len(times) - 1, -1
    while lo <= hi:
        mid = (lo + hi) // 2
        if times[mid] <= s:
            i, lo = mid, mid + 1
        else:
            hi = mid - 1
    if i < 0:
        return [_round_row(r) for r in rows[0]] if times[0] - s <= FACE_MAX_GAP else []
    if i == len(times) - 1:
        return [_round_row(r) for r in rows[i]] if s - times[i] <= FACE_MAX_GAP else []
    ta, tb, ra, rb = times[i], times[i + 1], rows[i], rows[i + 1]
    gap = tb - ta
    same_shape = len(ra) == len(rb) and len(ra) > 0 and all(len(a) == len(b) for a, b in zip(ra, rb))
    if same_shape and gap <= FACE_MAX_GAP:
        k = (s - ta) / gap if gap > 0 else 0.0
        return [_round_row(a[j] + (b[j] - a[j]) * k for j in range(len(a))) for a, b in zip(ra, rb)]
    near_a = s - ta <= tb - s
    near, dist = (ra, s - ta) if near_a else (rb, tb - s)
    if gap > FACE_MAX_GAP and dist > 0.5 / fps:
        return []
    return [_round_row(r) for r in near]


def face_sample_times(segments: Sequence[Segment], fps: float = SESSION_FACE_FPS) -> list[float]:
    """E times of the resampled track: ``k / fps`` over the edit, plus ``e_j - 0.001`` and ``e_j`` at every cut."""
    total = edit_duration(segments)
    if total <= 0:
        return []
    times = {round(k / fps, FACE_DECIMALS) for k in range(int(math.floor(total * fps + 1e-9)) + 1)}
    cursor = 0.0
    for index, (_, start, end) in enumerate(segments):
        if index:
            times.add(round(max(0.0, cursor - LOCATE_EPSILON), FACE_DECIMALS))
            times.add(round(cursor, FACE_DECIMALS))
        cursor += end - start
    return sorted(t for t in times if 0.0 <= t <= total + 1e-9)


def resample_face_track(track: dict | None, segments: Sequence[Segment], *, fps: float = SESSION_FACE_FPS) -> dict | None:
    """The session face track (S, picture space) on the E clock (AR-B4): ``FaceTrack`` JSON, still space "picture"
    (``build_scene`` applies ``picture_rect``). None when the stored track is unreadable."""
    parsed = _clean_track(track)
    if parsed is None:
        return None
    ordered = sorted(parsed.samples, key=lambda sample: sample[0])
    times = [float(t) for t, _ in ordered]
    rows = [[list(row) for row in faces if len(row) >= 4] for _, faces in ordered]
    samples = []
    for e in face_sample_times(segments, fps):
        _, s = locate(segments, e)
        samples.append([e, _track_at(times, rows, parsed.fps, s)])
    return {"version": 1, "space": "picture", "fps": fps, "samples": samples}


def empty_face_track(fps: float = SESSION_FACE_FPS) -> dict:
    """A track with no faces: face cues fall back at draw time (as in the preview) instead of the chain analysing
    the composed studio picture."""
    return {"version": 1, "space": "picture", "fps": fps, "samples": []}


# ----------------------------------------------------------------------------- document → plan / request / scene

def _mapped_fx(document, segments: Sequence[Segment]) -> list:
    """The enabled cues on E (``editor_document.mapped_fx``'s rule) through ``segments``."""
    fx = _doc_fx(document)
    if fx is None:
        return []
    disabled = set(getattr(fx, "disabled", None) or [])
    return mapped_cues(list(segments), [c for c in fx.cues if c.kind not in disabled])


def studio_plan(document, segments: Sequence[Segment]) -> FxPlan:
    """``doc.fx`` as an ``FxPlan`` on the E clock (cues mapped through the segment cuts)."""
    from .edit_fx.contracts import EditStyle

    fx = _doc_fx(document)
    mapped = _mapped_fx(document, segments)
    if len(mapped) > MAX_PLAN_CUES:
        # The first MAX_PLAN_CUES in mapping order, as fx-core's studioScene keeps them (mappedFx(doc).slice).
        logger.warning("fx_studio_cues_truncated mapped=%s kept=%s", len(mapped), MAX_PLAN_CUES)
        mapped = mapped[:MAX_PLAN_CUES]
    cues = sorted(mapped, key=lambda c: (c.start, c.id))
    template = document.captions.template
    if fx is None:
        return FxPlan(style=EditStyle(), template=template, seed=0, edit_duration=edit_duration(segments))
    return FxPlan(
        style=fx.style, template=template, seed=int(fx.seed), edit_duration=edit_duration(segments),
        sensitivity=fx.sensitivity, timing=fx.timing, mood=fx.mood, cues=tuple(cues), music=fx.music,
        grade=fx.grade, grain=fx.grain, progress_bar=bool(fx.progressBar), loop_ending=fx.loopEnding,
        hook_title_suppressed=bool(fx.hookTitleSuppressed),
    )


def studio_words(document, segments: Sequence[Segment]) -> list[dict]:
    """Caption words on E with ``run`` = the continuous segment they play in (``caption_key``'s run), ids unchanged
    so a placement keyed ``[run, firstWordId]`` finds its caption. A word cut by a segment edge keeps the part
    inside. Uncensored: the chain censors under bleeps."""
    out: list[dict] = []
    ordered = sorted(document.words, key=lambda w: w.start)
    cursor = 0.0
    for segment_id, seg_start, seg_end in segments:
        for word in ordered:
            start, end = max(seg_start, word.start), min(seg_end, word.end)
            if end > start and word.text.strip():
                out.append({"id": word.id, "start": cursor + start - seg_start, "end": cursor + end - seg_start,
                            "text": word.text, "highlight": bool(word.highlight), "run": segment_id})
        cursor += seg_end - seg_start
    return out


def studio_caption_style(document, width: int, height: int) -> FxCaptionStyle | None:
    """The FX-template caption look (fx-core draws it), or None for legacy templates and disabled captions.

    The global caption transform moves the centre (``x``, ``y``) and scales the size. A per-caption transform
    replaces the global one (as in ``write_ass``), so its placement is stored relative to the global style: fx-core
    adds a placement's offsets to the style's and multiplies the scales. A size override folds into the scale.
    """
    from .caption_templates import get_template

    captions = document.captions
    if not captions.enabled or not is_fx_template(captions.template):
        return None
    transform = document.textTransforms.get("captions")
    style = captions.model_dump()
    global_x, global_scale = 0.0, 1.0
    if transform is not None:
        style["y"] = min(1.0, max(0.0, captions.y + transform.y))
        global_x = min(1.0, max(-1.0, transform.x))
        style["x"] = global_x
        global_scale = transform.scale
        style["size"] = captions.size * transform.scale
    from .edit_fx.contracts import CaptionPlacement

    style_y = style.get("y", captions.y)
    placements = []
    for key, placement in sorted(document.captionTransforms.items()):
        scale = placement.scale * ((placement.size / captions.size) if placement.size else 1.0) / global_scale
        placements.append(CaptionPlacement(key=key[:200], x=placement.x - global_x,
                                           y=placement.y + (captions.y - style_y),
                                           scale=min(4.0, max(1e-3, scale))))
    from .edit_fx.contracts import caption_look_for

    fx = document.fx
    look = caption_look_for(captions.template, fx.style.intensity if fx is not None else "off",
                            fx.sensitivity if fx is not None else "normal")
    from .editor_document import studio_caption_rows

    return fx_caption_style_from_document(style, get_template(captions.template), width=width, height=height,
                                          placements=placements, look=look,
                                          rows=studio_caption_rows(document) if look != "template" else ())


def hook_range(document, total: float) -> tuple[float, float] | None:
    """The hook title's E window (== ``document.ts`` ``hookRange``), None when there is no title."""
    if not (document.hookTitle or "").strip():
        return None
    if document.hookTiming is not None:
        start = min(max(document.hookTiming.start, 0.0), max(0.0, total - 0.04))
        return start, min(max(document.hookTiming.end, start + min(0.04, total)), total)
    if total <= 1.5:
        return 0.0, max(0.5, total)
    return 0.12, min(4.0, max(1.5, total - 0.25))


def studio_hook(document, plan: FxPlan):
    """The hook title fx-core draws for a draft (== studio-scene.ts ``studioHook``): the title at its own
    position plus the draft's hook transform, in the draft's font and accent, shown for ``hook_range``; None without a
    title or when the plan suppresses it."""
    from .edit_fx.caption_style import _hex6
    from .edit_fx.hook_title import HOOK_DEFAULT_ACCENT, build_hook, hook_look

    window = hook_range(document, plan.edit_duration)
    if window is None or plan.hook_title_suppressed:
        return None
    captions = document.captions
    template_id = captions.template or "default"
    transform = document.textTransforms.get("hook")
    return build_hook(
        document.hookTitle, template_id=template_id,
        look=hook_look(template_id, plan.style.intensity, plan.sensitivity), font=captions.font,
        accent=_hex6(captions.accent, HOOK_DEFAULT_ACCENT), window=window, uppercase=bool(captions.uppercase),
        x=transform.x if transform else 0.0, y=transform.y if transform else 0.0,
        scale=transform.scale if transform else 1.0,
    )


def broll_windows(document) -> tuple[tuple[float, float], ...]:
    """E windows of the document's B-roll pieces (``mapped_broll``)."""
    from .editor_document import mapped_broll

    return tuple((float(p["start"]), float(p["start"] + p["duration"])) for p in mapped_broll(document))


def _reaction_files_e(cues_e: Sequence, document, files_s: dict[str, Path]) -> dict[str, Path]:
    """Reaction files keyed by E cue id: a repeated segment plays the same GIF under ``id@k``."""
    if not files_s:
        return {}
    by_content = {}
    for cue in _fx_cues(document):
        if cue.kind == "reaction" and cue.id in files_s:
            by_content[(cue.query, cue.asset_id, cue.url, cue.seed)] = files_s[cue.id]
    out = {}
    for cue in cues_e:
        if cue.kind != "reaction":
            continue
        path = files_s.get(cue.id) or by_content.get((cue.query, cue.asset_id, cue.url, cue.seed))
        if path is not None:
            out[cue.id] = Path(path)
    return out


def studio_request(document, *, segments: Sequence[Segment], out_w: int, out_h: int, source_w: int, source_h: int,
                   base: str, inputs: StudioFxInputs | None = None, broll: bool = True, source_fps: str = "30"):
    """The ``FxRequest`` of one studio render (§14.1)."""
    from .media.fx_chain import FxRequest

    inputs = inputs or StudioFxInputs()
    segments = list(segments)
    plan = studio_plan(document, segments)
    geometry, rect = output_geometry(document.framing, source_w, source_h, out_w, out_h)

    face_track = None
    session_face = inputs.face_track if isinstance(inputs.face_track, dict) else None
    if session_face and session_face.get("base") == base:
        face_track = resample_face_track(session_face.get("track"), segments)
    caption = studio_caption_style(document, out_w, out_h)
    hook = studio_hook(document, plan)
    if face_track is None and (plan.needs_face or hook is not None):
        # No usable session track: draw the fallbacks the preview draws ("Analyse face" fixes both; the hook title
        # is then placed by the captions alone).
        face_track = empty_face_track()

    matte_input = matte_filter = None
    if plan.needs_matte and inputs.matte_path is not None and (inputs.matte_base or "clean") == base:
        matte_input = Path(inputs.matte_path)
        matte_filter = matte_filter_for(segments, geometry, source_w, source_h, source_fps)

    return FxRequest(
        plan=plan,
        words=studio_words(document, segments),
        caption=caption,
        picture_rect=rect,
        face_track=face_track,
        matte_input=matte_input,
        matte_filter=matte_filter,
        reaction_files=_reaction_files_e(plan.cues, document, inputs.reaction_files),
        broll_windows=broll_windows(document) if broll else (),
        hook_window=hook_range(document, plan.edit_duration),
        hook=hook,
        progress_color=_doc_progress_color(document),
        voice_gain=0.0 if document.muted else float(document.volume),
        loudness="none",
    )


def _doc_progress_color(document) -> str:
    """``DocFx.progressColor`` (== studio-scene.ts), the default orange without fx."""
    from .media.fx_chain import FX_PROGRESS_COLOR

    fx = getattr(document, "fx", None)
    return str(getattr(fx, "progressColor", None) or FX_PROGRESS_COLOR)


def studio_scene(document, *, source_w: int, source_h: int, base: str, face_track: dict | None = None,
                 matte: bool = False, broll: bool = True) -> FxScene:
    """The ``FxScene`` the studio's server export draws for ``document`` (what ``run_fx_chain`` hands P2 when the
    session's matte is available iff ``matte``). Pure; the scene fixtures and fx-core's ``studioScene`` pin it."""
    from .editor_document import output_size
    from .media.fx_chain import build_scene

    segments = _segments(document)
    out_w, out_h = output_size(document, source_w, source_h)
    inputs = StudioFxInputs(face_track=face_track, matte_path=Path(MATTE_NAME) if matte else None, matte_base=base)
    request = studio_request(document, segments=segments, out_w=out_w, out_h=out_h, source_w=source_w,
                             source_h=source_h, base=base, inputs=inputs, broll=broll)
    plan = request.plan
    warp = build_warp(plan.cues, max(1.0 / 30, plan.edit_duration))
    windows = [MatteWindow(start=c.start, end=c.end, person=request.matte_input is not None)
               for c in plan.cues if c.kind == "accent" and c.variant == "behind" and c.end > c.start]
    return build_scene(request, out_w=out_w, out_h=out_h, warp=warp, face_track=request.face_track,
                       matte_windows=windows)


# ----------------------------------------------------------------------------- the render

def _hooks(ctx) -> tuple[Callable[[], bool] | None, Callable[[int], None] | None]:
    hooks = getattr(ctx, "run_render_hooks", None)
    if hooks is None:
        return None, None
    if isinstance(hooks, dict):
        return hooks.get("should_cancel"), hooks.get("on_progress")
    if isinstance(hooks, (tuple, list)):
        return (hooks[0] if hooks else None), (hooks[1] if len(hooks) > 1 else None)
    return getattr(hooks, "should_cancel", None), getattr(hooks, "on_progress", None)


def _cancelled(should_cancel) -> bool:
    if should_cancel is None:
        return False
    try:
        return bool(should_cancel())
    except Exception:  # noqa: BLE001 - a broken cancel probe never stops a render
        return False


def _base(ctx) -> str:
    base = getattr(ctx, "base_video", None)
    if base in ("layout", "clean"):
        return base
    return "layout" if Path(ctx.input_path).name == "layout.mp4" else "clean"


def _percent(on_progress):
    """run_fx_chain reports 0-1; the studio's job heartbeat takes a percent below 100 (``run_render``)."""
    if on_progress is None:
        return None

    def report(fraction: float) -> None:
        try:
            on_progress(min(99, int(float(fraction) * 100)))
        except Exception:  # noqa: BLE001 - progress never stops a render
            logger.debug("studio fx on_progress raised", exc_info=True)

    return report


def _av_mismatch(path: Path) -> float | None:
    """|video − audio| seconds of a rendered file (None without audio or when unreadable)."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,duration", "-of", "json", str(path)],
            capture_output=True, text=True, timeout=60,
        )
        streams = json.loads(result.stdout or "{}").get("streams") or []
        video = next(float(s["duration"]) for s in streams if s.get("codec_type") == "video")
        audio = next((float(s["duration"]) for s in streams if s.get("codec_type") == "audio"), None)
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, StopIteration):
        return None
    return None if audio is None else abs(video - audio)


def _fallback_document(document, *, censor: bool):
    """The document as the legacy renderer draws it: an FX template becomes its ``ass_fallback`` and, at level safe,
    caption words under a bleep are censored (the bleep tone stays in the audio)."""
    from .caption_templates import get_template

    update: dict[str, Any] = {}
    template = document.captions.template
    if is_fx_template(template):
        fallback = str(get_template(template).get("ass_fallback") or "default")
        update["captions"] = document.captions.model_copy(update={"template": fallback})
    fx = _doc_fx(document)
    if censor and fx is not None and fx.timing == "word" and "bleep" not in (getattr(fx, "disabled", None) or []):
        spans = [(c.start, c.end) for c in fx.cues if c.kind == "bleep" and c.end > c.start]
        if spans:
            words = censored_words([w.model_dump() for w in document.words], spans)
            update["words"] = [w.model_copy(update={"text": c["text"]}) for w, c in zip(document.words, words)]
    return document.model_copy(update=update) if update else document


def _fallback_ass(document, path: Path, width: int, height: int, *, censor: bool) -> Path:
    """ASS for levels safe and none: legacy captions (fallback template) and the original hook title (AR-M12)."""
    from .editor_document import write_ass

    write_ass(path, _fallback_document(document, censor=censor), width, height, hook_heavy=True)
    return path


def _head_without_broll(document, segments: Sequence[Segment], *, out_w: int, out_h: int, source_w: int,
                        source_h: int) -> tuple[str, str]:
    """``render_document``'s head with no B-roll: cuts, framing geometry and the picture effects."""
    geometry, _ = output_geometry(document.framing, source_w, source_h, out_w, out_h)
    cut = segment_cut_graph(segments, source="[0:v]", prefix="fxs_c")
    return f"{cut},{geometry},{effects_filter(document.effects, out_w)}[fxs_head]", "[fxs_head]"


_VOICE_INPUT = re.compile(r"\[1:a(?=[\]:])")


def _legacy_render(document, ctx, *, head: str, head_label: str, broll_inputs: list[str], ass: Path | None,
                   total: float, should_cancel, on_progress, output: Path) -> None:
    """Level none: the studio's legacy single-pass render (as before dynamic edits) of the fallback document."""
    from .editor_document import run_render
    from .media.ffmpeg import subtitles_filter_fragment

    filters = [head]
    label = head_label
    if ass is not None:
        # render_document's legacy graph falls back to ./fonts the same way.
        fonts = getattr(ctx, "fonts_dir", None) or Path("fonts")
        filters.append(f"{label}{subtitles_filter_fragment(ass, fonts)}[fxs_subs]")
        label = "[fxs_subs]"
    filters.append(f"{label}fps=30[fxs_out]")
    voice = getattr(ctx, "voice_fragment", None)
    if voice:
        # The fragment reads the source as P3's input 1; here it is input 0.
        filters.append(_VOICE_INPUT.sub("[0:a", voice))
        filters.append(f"[fxa_voice0]volume={0 if document.muted else document.volume}[fxs_a]")
    command = ["ffmpeg", "-y", "-i", str(ctx.input_path), *broll_inputs,
               "-filter_complex", ";".join(filters), "-map", "[fxs_out]"]
    if voice:
        command += ["-map", "[fxs_a]", "-c:a", "aac", "-b:a", AUDIO_BITRATE]
    else:
        command += ["-an"]
    encode = list(getattr(ctx, "encode_args", None) or [])
    if "-movflags" not in encode:
        encode += ["-movflags", "+faststart"]
    command += [*encode, "-t", f"{total:.6f}", str(output)]
    run_render(command, output.parent, uuid.uuid4().hex, total, should_cancel=should_cancel, on_progress=on_progress)


def _full_level(document, ctx, request, chain, *, head: str, head_label: str, inputs_args: list[str], failed,
                rendered: Path, stage: str = "full"):
    """Level full: P1 → P2 → P3 with the document's own ASS (FX-template captions are drawn by fx-core, the hook
    title only when the plan does not suppress it). A warped render whose audio and video disagree by more than a
    frame is redone with an identity warp (§6.6). Returns the last ``FxResult``, or None when the ASS failed."""
    plan = request.plan
    # fx-core draws the title (request.hook); the ASS carries it only when there is none to draw.
    hook = None if (plan.hook_title_suppressed or request.hook is not None) else ((document.hookTitle or "").strip() or None)
    include_captions = document.captions.enabled and not is_fx_template(document.captions.template)
    spans = [(c.start, c.end) for c in plan.cues if c.kind == "bleep" and c.end > c.start] \
        if plan.timing == "word" else []

    def ass_for(warp) -> Path | None:
        path = ctx.subtitle_ass_builder(include_captions=include_captions,
                                        time_map=warp.to_output if warp.nontrivial else None,
                                        censor_spans=spans or None, hook_title=hook)
        return Path(path) if path else None

    warp = build_warp(plan.cues, max(1.0 / 30, plan.edit_duration)) if fx_env.fx_warp_enabled() \
        else identity_warp(plan.edit_duration)
    try:
        ass = ass_for(warp)
    except Exception as exc:  # noqa: BLE001 - without its ASS the full level cannot run
        logger.exception("fx_studio_ass_failed")
        failed(stage, f"ass: {exc}")
        return None
    result = chain("full", request, head=head, head_label=head_label, inputs_args=inputs_args, ass=ass)
    if result.ok and result.applied_warp:
        mismatch = _av_mismatch(rendered)
        if mismatch is not None and mismatch > AV_TOLERANCE:
            failed(stage, f"av: audio and video differ by {mismatch:.3f}s with a warp")
            unwarped = replace(request, plan=plan.model_copy(update={
                "cues": tuple(c for c in plan.cues if c.kind not in ("speed", "freeze"))}))
            try:
                ass = ass_for(identity_warp(plan.edit_duration))
            except Exception as exc:  # noqa: BLE001
                failed(stage, f"ass: {exc}")
                return None
            result = chain("full", unwarped, head=head, head_label=head_label, inputs_args=inputs_args, ass=ass)
    if not result.ok:
        failed(stage, result.error)
    return result


def render_document_fx(document, ctx) -> dict:
    """Render a studio document with dynamic edits into ``ctx.output_path`` (§14.1, ladder §6.6).

    Returns ``{"level": "full"|"safe"|"none", "applied_warp": [...]}``: the level that produced the file and the warp
    it actually rendered (``[]`` unless level full with speed or freeze, AI-H4), which a combined clip's sidecar
    stores. Raises ``InterruptedError`` when cancelled and ``RuntimeError`` only when even the legacy render fails.
    """
    from .clip_editor import _ffprobe_size
    from .media.fx_chain import run_fx_chain

    started = time.monotonic()
    should_cancel, on_progress = _hooks(ctx)
    progress = _percent(on_progress)
    output = Path(ctx.output_path)
    work = Path(tempfile.mkdtemp(prefix=".fx-studio-", dir=str(output.parent)))
    temporary = work / "render.mp4"
    out_w, out_h = int(ctx.out_w), int(ctx.out_h)
    segments = _segments(getattr(ctx, "segments", None) or document)
    total = edit_duration(segments)
    base = _base(ctx)
    inputs = bound_inputs()
    has_audio = bool(getattr(ctx, "voice_fragment", None))
    broll_inputs = list(getattr(ctx, "broll_inputs", None) or [])
    fonts_dir = getattr(ctx, "fonts_dir", None)
    encode_args = list(getattr(ctx, "encode_args", None) or []) or None
    level = "none"
    applied_warp: list[dict] = []
    attempts: list[str] = []

    def check_cancel() -> None:
        if _cancelled(should_cancel):
            raise InterruptedError("Export cancelled")

    def failed(stage: str, error: str | None) -> None:
        attempts.append(f"{stage}: {error}")
        logger.warning("fx_render_failed surface=studio stage=%s error=%s", stage, (error or "")[:500])
        if error == "cancelled" or (error or "").startswith("cancelled"):
            raise InterruptedError("Export cancelled")

    try:
        source_w, source_h = _ffprobe_size(Path(ctx.input_path))
        request = None
        try:
            request = studio_request(
                document, segments=segments, out_w=out_w, out_h=out_h, source_w=source_w, source_h=source_h,
                base=base, inputs=inputs, broll=bool(broll_inputs),
                source_fps=probe_frame_rate(Path(ctx.input_path)) if inputs.matte_path is not None else "30",
            )
        except Exception as exc:  # noqa: BLE001 - an unbuildable plan still exports the clip (level none)
            logger.exception("fx_studio_request_failed")
            failed("plan", str(exc))

        def chain(mode: str, req, *, head: str, head_label: str, inputs_args: list[str], ass: Path | None):
            check_cancel()
            temporary.unlink(missing_ok=True)
            return run_fx_chain(
                input_path=Path(ctx.input_path), output_path=temporary, head=head, head_label=head_label,
                out_w=out_w, out_h=out_h, broll_overlays=[], broll_inputs=inputs_args, subtitle_ass_path=ass,
                fonts_dir=fonts_dir, request=req, mode=mode, voice_fragment=getattr(ctx, "voice_fragment", None),
                scratch_dir=work, encode_args=encode_args, has_audio=has_audio,
                should_cancel=should_cancel, on_progress=progress,
            )

        head, head_label, head_inputs = ctx.head, ctx.head_label, broll_inputs
        runtime = fx_env.fx_runtime()
        if request is not None and runtime == "deno":
            result = _full_level(document, ctx, request, chain, head=head, head_label=head_label,
                                 inputs_args=head_inputs, failed=failed, rendered=temporary)
            if result is not None and result.ok:
                level, applied_warp = "full", list(result.applied_warp)
            elif result is not None and (result.error or "").startswith("p1") and head_inputs:
                # B-roll is the usual P1 breaker (AR-M11): the same plan without cutaways.
                head, head_label = _head_without_broll(document, segments, out_w=out_w, out_h=out_h,
                                                       source_w=source_w, source_h=source_h)
                head_inputs = []
                request = replace(request, broll_windows=())
                result = _full_level(document, ctx, request, chain, head=head, head_label=head_label,
                                     inputs_args=[], failed=failed, rendered=temporary,
                                     stage="full_without_broll")
                if result is not None and result.ok:
                    level, applied_warp = "full", list(result.applied_warp)

        if level == "none" and request is not None and runtime != "legacy":
            # Safe: no deno and no warp; the bus (SFX, music, bleeps) stays, captions fall back to libass.
            # ``FX_RUNTIME=legacy`` skips it too: the export takes the legacy render.
            try:
                ass_safe = _fallback_ass(document, work / "safe.ass", out_w, out_h, censor=True)
            except Exception as exc:  # noqa: BLE001 - the legacy level writes its own
                logger.exception("fx_studio_safe_ass_failed")
                failed("safe", f"ass: {exc}")
            else:
                result = chain("safe", request, head=head, head_label=head_label, inputs_args=head_inputs,
                               ass=ass_safe)
                if result.ok:
                    level = "safe"
                else:
                    failed("safe", result.error)

        if level == "none":
            check_cancel()
            temporary.unlink(missing_ok=True)
            ass_none = _fallback_ass(document, work / "none.ass", out_w, out_h, censor=False)
            try:
                _legacy_render(document, ctx, head=head, head_label=head_label, broll_inputs=head_inputs,
                               ass=ass_none, total=total, should_cancel=should_cancel, on_progress=on_progress,
                               output=temporary)
            except RuntimeError as exc:
                if not head_inputs or _cancelled(should_cancel):
                    raise
                # B-roll footage that ffmpeg cannot read must not cost the export (as generation's B-roll retry).
                failed("none", str(exc))
                check_cancel()
                temporary.unlink(missing_ok=True)
                head, head_label = _head_without_broll(document, segments, out_w=out_w, out_h=out_h,
                                                       source_w=source_w, source_h=source_h)
                head_inputs = []
                _legacy_render(document, ctx, head=head, head_label=head_label, broll_inputs=[], ass=ass_none,
                               total=total, should_cancel=should_cancel, on_progress=on_progress,
                               output=temporary)
        check_cancel()
        os.replace(temporary, output)
        logger.info(
            "fx_render surface=studio fx_level=%s template=%s intensity=%s cues=%s warp=%s duration_ms=%s "
            "downgrades=%s",
            level, document.captions.template,
            getattr(getattr(_doc_fx(document), "style", None), "intensity", "off"),
            len(request.plan.cues) if request is not None else 0,
            bool(applied_warp), round((time.monotonic() - started) * 1000), json.dumps(attempts)[:1000],
        )
        return {"level": level, "applied_warp": applied_warp}
    finally:
        shutil.rmtree(work, ignore_errors=True)
