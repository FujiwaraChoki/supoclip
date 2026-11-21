"""Background preparation and rendering for the clip studio.

A job may run on any worker. It reads its session and request from the
database, fetches media from object storage into this machine's scratch
directory, and publishes what it renders before reporting it.
"""

import asyncio
from contextlib import asynccontextmanager, suppress
from pathlib import Path
import functools
import json
import logging
import re
import subprocess
import threading
import uuid
from typing import get_args

from ..media.render_ranges import burned_hook_title
from ..editor_document import (
    CaptionStyle,
    EditDocument,
    atomic_json,
    editor_dir,
    render_document,
)
from ..editor_state import (
    PREPARE_HEARTBEAT_SECONDS,
    editor_key,
    ensure_session,
    get_store,
)
from ..stream_transfer import run_stream_download
from ..utils.async_helpers import run_in_thread
from ..media_storage import MediaStorage, STORAGE_ERRORS, media_work
from ..source_range import media_duration, range_transcript, transcript_on_media
from ..stream_source import download_stream_sections


async def cap_export_for_plan(task_id: str, path: Path) -> None:
    """Downscale an editor render for plans below 1080p.

    Editor renders start from the original source, so they need the same
    final pass as generated clips. A failed downscale keeps the full render.
    """
    from ..database import AsyncSessionLocal
    from ..media.ffmpeg import cap_video_short_side
    from .billing_service import FULL_HD_SHORT_SIDE, BillingService
    from .task_service import TaskService

    try:
        async with AsyncSessionLocal() as db:
            task = await TaskService(db).task_repo.get_task_by_id(db, task_id)
            if not task or not task.get("user_id"):
                return
            billing = await BillingService(db).get_usage_summary(
                task["user_id"], team_id=task.get("team_id")
            )
        short_side = billing.get("output_short_side")
        if short_side and short_side < FULL_HD_SHORT_SIDE:
            await run_in_thread(cap_video_short_side, path, short_side)
    except Exception:
        logging.getLogger(__name__).warning(
            "Keeping full-size editor render for task %s", task_id, exc_info=True
        )


PREPARE_FAILED = "Could not prepare the source video. Check that the source is available, then retry."


class EditorPrepareError(Exception):
    """A preparation failure whose message is safe to show in the studio."""


def youtube_download_failure(errors: list[str]) -> str:
    """Explain why the YouTube source could not be fetched again.

    The editor only downloads when no cached copy covers the clip, so the
    message says that too. Provider details stay in the worker log.
    """
    text = " ".join(errors).lower()
    lead = "No saved copy of this YouTube video is left on the server, and downloading it again failed"
    if "confirm your age" in text or "age-restricted" in text:
        return f"{lead}: YouTube requires sign-in because the video is age-restricted."
    if "not a bot" in text:
        return f"{lead}: YouTube asked this server to sign in to confirm it is not a bot. Retry later."
    if "private video" in text or "video unavailable" in text or "has been removed" in text:
        return f"{lead}: YouTube reports the video is private or no longer available."
    if "http error 429" in text or "too many requests" in text:
        return f"{lead}: YouTube is rate limiting this server. Retry later."
    return f"{lead}. Retry later."


def make_assets(directory: Path):
    import numpy as np

    source = directory / "clean.mp4"
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    metadata = json.loads(probe.stdout)
    video = next(
        stream for stream in metadata["streams"] if stream["codec_type"] == "video"
    )
    numerator, denominator = video.get("avg_frame_rate", "30/1").split("/")
    fps = float(numerator) / max(1, float(denominator))
    duration = float(metadata["format"]["duration"])
    audio = any(stream["codec_type"] == "audio" for stream in metadata["streams"])
    waveform = []
    if audio:
        samples = subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-i",
                str(source),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "8000",
                "-f",
                "f32le",
                "-",
            ],
            capture_output=True,
            check=True,
        )
        values = np.frombuffer(samples.stdout, dtype="float32")
        waveform = [
            round(float(np.max(np.abs(chunk))), 4) if len(chunk) else 0
            for chunk in np.array_split(values, 160)
        ]
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(source),
            "-vf",
            f"fps={12 / duration},scale=160:90:force_original_aspect_ratio=decrease,pad=160:90:(ow-iw)/2:(oh-ih)/2,tile=12x1",
            "-frames:v",
            "1",
            str(directory / "thumbnails.jpg"),
        ],
        capture_output=True,
        check=True,
    )
    return {
        "status": "ready",
        "width": video["width"],
        "height": video["height"],
        "fps": fps or 30,
        "duration": duration,
        "hasAudio": audio,
        "waveform": waveform,
    }


EDITOR_FONTS = set(get_args(CaptionStyle.model_fields["font"].annotation))


def initial_caption_style(
    task: dict, caption_settings: dict, enabled: bool, width: int, height: int
) -> dict:
    """Start the editor from the look the generated clip was rendered with.

    ``width``/``height`` are the generated clip's dimensions; size and position
    reproduce the generation renderer's pixel maths at that size.
    """
    from ..caption_templates import get_template
    from ..media.captions import get_safe_vertical_position, get_scaled_font_size

    template = get_template(task.get("caption_template") or "default")
    font = task.get("font_family") or template["font_family"]
    if font not in EDITOR_FONTS:
        font = template["font_family"] if template["font_family"] in EDITOR_FONTS else "TikTokSans-Regular"
    base_size = caption_settings.get("font_size") or task.get("font_size") or template["font_size"]
    font_px = get_scaled_font_size(int(base_size), width)
    color = task.get("font_color") or template["font_color"]
    accent = template.get("highlight_color") or "#FACC15"
    position_y = caption_settings.get("position_y") or template.get("position_y") or 0.78
    # Generation centres the text at this pixel row; the editor stores it as a fraction.
    center_px = round(height * float(position_y)) if caption_settings.get("position_is_center") else get_safe_vertical_position(height, int(font_px * 1.5), float(position_y))
    return {
        "enabled": enabled,
        "font": font,
        # The editor sizes text in 1080-wide units.
        "size": max(20, min(132, round(font_px * 1080 / width))),
        "color": color if re.fullmatch(r"#[0-9a-fA-F]{6}", color or "") else "#FFFFFF",
        "accent": accent if re.fullmatch(r"#[0-9a-fA-F]{6}", accent) else "#FACC15",
        "y": max(0.1, min(0.9, center_px / height)),
        "background": bool(template.get("background") and template.get("background_color")),
        "wordsPerLine": max(1, min(8, int(template.get("max_words_per_line") or 4))),
        "karaoke": template.get("animation") == "karaoke",
        "uppercase": bool(template.get("uppercase")),
        "template": task.get("caption_template") or "default",
    } | _look_caption_seed(task, caption_settings, width, height)


def _look_caption_seed(task: dict, caption_settings: dict, width: int, height: int) -> dict:
    """Listed animated templates draw an intensity look (font, size, case, keyword colour, pill): seed the editor
    with the look the clip was generated with, so the studio opens on the same captions. {} for other templates."""
    from ..edit_fx.caption_groups import LOOK_MAX_WORDS
    from ..edit_fx.caption_style import LOOKS, look_accent, look_pill
    from ..edit_fx.contracts import caption_look_for
    from ..edit_fx.settings import parse_edit_style

    template_id = task.get("caption_template") or "default"
    raw = task.get("edit_style")
    try:
        intensity = parse_edit_style(json.loads(raw) if isinstance(raw, str) else raw).intensity
    except Exception:  # noqa: BLE001 - an unreadable stored style is "off"
        intensity = "off"
    look = caption_look_for(template_id, intensity)
    if look not in LOOKS:
        return {}
    spec = LOOKS[look]
    position_y = caption_settings.get("position_y") if caption_settings.get("position_is_center") else None
    return {
        "font": spec["font"],
        "size": max(20, min(132, int(spec["px"]))),
        "y": max(0.1, min(0.9, float(position_y if position_y is not None else spec["y"]))),
        "wordsPerLine": LOOK_MAX_WORDS[look],
        "uppercase": bool(spec["uppercase"]),
        "accent": look_accent(template_id, look),
        "background": look_pill(template_id, look) is not None,
        "karaoke": False,
    }


def generated_emphasis(words: list[dict], task: dict, plan: dict | None = None) -> set[int]:
    """Words the generated clip accented (power words, numbers, emoji words).

    The editor seeds them as highlights so users see and can toggle them.
    A clip drawn with an FX caption template accented its plan's accent cues
    (``plan`` is the sidecar's ``edit_fx``); legacy templates burned the
    heuristic emphasis into their ASS, so they keep it even with dynamic edits
    on (AI-M3).
    """
    from ..caption_templates import get_template
    from ..edit_fx.contracts import is_fx_template
    from ..emoji_captions import annotate_caption_words
    from ..media.captions import emoji_rendering_supported

    template_id = task.get("caption_template") or "default"
    if plan is not None and is_fx_template(template_id):
        return plan_accent_words(words, plan)
    template = get_template(template_id)
    _, emphasis = annotate_caption_words(
        words,
        enable_emoji=bool(template.get("emoji", True)) and emoji_rendering_supported(),
        enable_emphasis=template.get("animation", "karaoke") != "none",
    )
    return emphasis


def plan_accent_words(words: list[dict], plan: dict) -> set[int]:
    """Indices of the words an FX plan accents: a word whose middle falls inside an accent cue (clip S time)."""
    spans = [
        (float(cue.get("start", 0)), float(cue.get("end", 0)))
        for cue in plan.get("cues") or []
        if isinstance(cue, dict) and cue.get("kind") == "accent"
    ]
    return {
        index
        for index, word in enumerate(words)
        if any(start - 1e-6 <= (word["start"] + word["end"]) / 2 <= end + 1e-6 for start, end in spans)
    }


def doc_fx_from_sidecar(sidecar: dict | None, duration: float) -> dict | None:
    """The draft's ``fx`` for a clip generated with dynamic edits (§14.1): the sidecar plan and beats, as editable
    cues on the clip's own timeline (clip S == clean.mp4 S, so times map one to one). None without a plan."""
    from ..edit_fx.contracts import EditBeats, FxPlan
    from ..editor_document import DocFx

    plan_raw = (sidecar or {}).get("plan")
    if not plan_raw:
        return None
    log = logging.getLogger(__name__)
    try:
        plan = FxPlan.model_validate(plan_raw)
    except ValueError:
        log.warning("fx_sidecar_corrupt field=edit_fx (studio seed)")
        return None
    beats: list = []
    if sidecar.get("beats"):
        try:
            beats = list(EditBeats.model_validate(sidecar["beats"]).beats)
        except ValueError:
            log.warning("fx_sidecar_corrupt field=edit_beats (studio seed)")
    limit = duration + 0.05
    seen: set[str] = set()
    cues = []
    for cue in plan.cues:
        if cue.end <= limit and cue.id not in seen:
            seen.add(cue.id)
            cues.append(cue)
    beat_ids: set[str] = set()
    kept_beats = []
    for beat in beats:
        if beat.end <= limit and beat.id not in beat_ids:
            beat_ids.add(beat.id)
            kept_beats.append(beat)
    return DocFx(
        style=plan.style,
        seed=plan.seed,
        mood=plan.mood,
        sensitivity=plan.sensitivity,
        timing=plan.timing,
        beats=kept_beats[:200],
        cues=cues[:400],
        music=plan.music,
        grade=plan.grade,
        grain=plan.grain,
        progressBar=plan.progress_bar,
        loopEnding=plan.loop_ending,
        hookTitleSuppressed=plan.hook_title_suppressed,
    ).model_dump(mode="json")


def layout_filter_has_zoompan(layout: dict | None) -> bool:
    """The baked layout's filter zooms (Ken Burns). The studio then adds no baseline pushes (AR-M9)."""
    plan = (layout or {}).get("vertical_plan")
    return bool(plan) and "zoompan" in str(plan[0] if isinstance(plan, (tuple, list)) else plan)


# Layouts that are a composed frame, not a crop the editor can rebuild.
PRESERVED_LAYOUT_KINDS = frozenset({"split", "stack", "reaction", "overlap", "interview", "gaming"})
# Plans kept beside the uncropped source as layout.mp4 ("Auto layout"), so the
# studio opens on the generated framing and manual crops still start from the
# full frame: content fits, the compositor, and the tracked face/speaker crops.
CONTENT_LAYOUT_KINDS = frozenset({"fit", "compositor", "crop", "speaker", "solo", "stage", "generated"})
# A stored plan is replayed only on the media it was planned for.
STORED_LAYOUT_DURATION_TOLERANCE = 0.25


def editor_preserved_layout(
    clip_path: Path,
    output_format: str,
    speaker_turns: list | None = None,
    speaker_words: list | None = None,
    include_content: bool = False,
) -> dict | None:
    """Split-screen or stacked plan to bake into the editor source.

    Returns None for original, speaker-pan, and any vertical clip whose plan
    is a plain crop. Those keep the editor's existing vertical or original crop.
    """
    from ..media.reframing import (
        describe_vertical_layout,
        detect_speaker_reframe_plan,
        ffprobe_video_size,
    )

    try:
        if output_format == "vertical_split":
            plan = detect_speaker_reframe_plan(clip_path, "vertical_split")
            if not plan or plan.get("mode") != "split":
                return None
            return {
                "kind": "split",
                "output_format": "vertical_split",
                "speaker_plan": plan,
            }
        if output_format != "vertical":
            return None
        width, height = ffprobe_video_size(clip_path)
        described = describe_vertical_layout(
            clip_path, width, height, speaker_turns, speaker_words
        )
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not read an editor reframing plan", exc_info=True
        )
        return None
    allowed = PRESERVED_LAYOUT_KINDS | (CONTENT_LAYOUT_KINDS if include_content else set())
    if described["kind"] not in allowed:
        return None
    return {
        "kind": described["kind"],
        "landscape_recommended": described["kind"] in {"fit", "compositor"},
        "output_format": "vertical",
        "vertical_plan": (described["video_filter"], described["mode"]),
        **({"segments": described["interview_segments"], "source_size": [width, height]}
           if described.get("interview_segments") else {}),
    }


def apply_editor_layout(source: Path, layout: dict, destination: Path | None = None) -> bool:
    """Replace ``source`` with the composed layout. False leaves it untouched."""
    from ..media.reframing import render_reframed_clip_ffmpeg

    dest = source.with_name(f".layout-{uuid.uuid4().hex}.mp4")
    try:
        ok, _, _ = render_reframed_clip_ffmpeg(
            source,
            dest,
            layout["output_format"],
            speaker_plan=layout.get("speaker_plan"),
            vertical_plan=layout.get("vertical_plan"),
        )
        if not ok or not dest.is_file() or dest.stat().st_size == 0:
            return False
        dest.replace(destination or source)
        return True
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not apply the editor reframing layout", exc_info=True
        )
        return False
    finally:
        dest.unlink(missing_ok=True)


def stored_editor_layout(clean: Path, stored: dict | None) -> dict | None:
    """The plan the clip was generated with, when it fits this editor source."""
    from ..media.reframing import ffprobe_video_size
    from ..media.ffmpeg import ffprobe_duration

    if not stored:
        return None
    try:
        width, height = ffprobe_video_size(clean)
        duration = ffprobe_duration(clean)
    except Exception:
        logging.getLogger(__name__).warning("Could not probe the editor source", exc_info=True)
        return None
    if (width, height) != (stored["width"], stored["height"]):
        return None
    if abs(duration - stored["duration"]) > STORED_LAYOUT_DURATION_TOLERANCE:
        return None
    return {
        "kind": stored["kind"],
        "landscape_recommended": stored["kind"] in {"fit", "compositor"},
        "output_format": "vertical",
        "vertical_plan": (stored["video_filter"], stored["mode"]),
        **({"segments": stored["segments"], "source_size": [width, height]} if stored.get("segments") else {}),
        **({"caption_band": stored["caption_band"]} if stored.get("caption_band") else {}),
    }


def bake_editor_layout(
    clean: Path,
    output_format: str,
    source: Path,
    ranges: list,
    preserve_content: bool = False,
    stored: dict | None = None,
    report: dict | None = None,
) -> bool:
    """Compose the clip's generated layout for the editor.

    ``stored`` is the vertical plan saved when the clip was rendered
    (``load_clip_vertical_layout``). Replaying it shows exactly the framing
    of the generated clip, face tracking included. Without one (older clips,
    or a plan made on different media) the layout is detected again here.
    ``report``, when given, receives ``zoompan``: whether the baked frame has
    a Ken Burns zoom in it, and for an interview layout its ``kind``, shot
    ``segments``, ``source_size`` and ``caption_band`` (the caption rows).
    """
    from ..config import get_config
    from ..media.speaker_framing import build_clip_speaker_turns

    if output_format == "vertical_fit":
        # Blurred full-frame fit, the same filter generation used.
        layout = {"kind": "fit", "landscape_recommended": False, "output_format": "vertical_fit"}
    else:
        layout = stored_editor_layout(clean, stored) if output_format == "vertical" else None
    if layout is None and output_format != "vertical_fit":
        turns: list = []
        words: list = []
        # Clip generation only supplies diarized speakers when SPEAKER_FRAMING is
        # on. Loading them here unconditionally can bake an overlap layout the
        # generated clip never had, just by opening the editor.
        if output_format == "vertical" and get_config().speaker_framing:
            try:
                turns, words = build_clip_speaker_turns(source, ranges)
            except Exception:
                logging.getLogger(__name__).warning(
                    "Speaker turns unavailable for the editor layout", exc_info=True
                )
        layout = editor_preserved_layout(clean, output_format, turns, words, include_content=preserve_content)
    if not layout:
        return False
    if report is not None:
        report["zoompan"] = layout_filter_has_zoompan(layout)
        report["kind"] = layout["kind"]
        for key in ("segments", "source_size", "caption_band"):
            if layout.get(key):
                report[key] = layout[key]
    if preserve_content and layout["kind"] not in PRESERVED_LAYOUT_KINDS:
        destination = clean.with_name("layout.mp4")
        if apply_editor_layout(clean, layout, destination):
            atomic_json(clean.with_name("layout.json"), {"landscapeRecommended": bool(layout.get("landscape_recommended"))})
            return True
        return False
    return apply_editor_layout(clean, layout)


def editor_caption_rows(layout: dict, captions: dict, duration: float) -> dict | None:
    """The draft's ``captionRows`` for a baked interview layout (``bake_editor_layout``'s report): the per-shot rows
    of the session's look captions on clean.mp4's clock, seeded with their automatic ``captions.y``. None for other
    layouts, legacy or hidden templates (their drafts stay as they were) and layouts stored without their shots."""
    from ..edit_fx.caption_rows import interview_caption_rows
    from ..edit_fx.contracts import is_listed_fx_template

    if not layout.get("baked") or layout.get("kind") not in ("interview", "compositor") or not layout.get("segments"):
        return None
    if not is_listed_fx_template(captions.get("template")):
        return None
    rows = interview_caption_rows(
        layout["segments"], text_px=float(captions.get("size") or 54), width=1080, height=1920,
        source_size=layout.get("source_size"), band=layout.get("caption_band"), duration=duration,
    )
    if not rows:
        return None
    return {"y": float(captions["y"]), "rows": [row.model_dump(mode="json") for row in rows]}


def editor_broll_from_placements(placements: list, duration: float) -> list[dict]:
    """Editor B-roll clips for a generated clip's stored placements, without overlaps."""
    from ..broll import placement_to_editor_clip

    clips = []
    for index, placement in enumerate(placements or []):
        clip = placement_to_editor_clip(placement, index, duration)
        if clip and (not clips or clip["start"] >= clips[-1]["end"] - 0.01):
            clips.append(clip)
    return clips


def rebased_broll(document: EditDocument, cursor: float = 0) -> list[dict]:
    """Flatten cuts into editable footage pieces while retaining their dissolve boundaries."""
    from ..editor_document import mapped_broll

    return [
        {
            "id": uuid.uuid4().hex,
            "start": round(cursor + piece["start"], 4),
            "end": round(cursor + piece["start"] + piece["duration"], 4),
            "offset": round(piece["offset"], 4),
            "query": piece["query"],
            "footage": piece["footage"].model_dump(),
            "fadeIn": piece["fade_in"],
            "fadeOut": piece["fade_out"],
            "fadeDuration": piece["fade_duration"],
        }
        for piece in mapped_broll(document)
    ]


def rebased_caption_rows(document: EditDocument, cursor: float = 0) -> list[dict]:
    """A draft's interview caption rows on a combined source (like ``rebased_broll``): the rows that place its
    captions (``studio_caption_rows``, E) moved by ``cursor``. [] when they don't apply to this draft."""
    from ..editor_document import studio_caption_rows

    return [
        {"start": round(cursor + row.start, 4), "end": round(cursor + row.end, 4), "y": row.y}
        for row in studio_caption_rows(document)
    ]


def combined_caption_rows(documents: list[dict], rows: list[dict]) -> dict | None:
    """The combined draft's ``captionRows``: every part's rebased rows, seeded with the combined captions' ``y`` (the
    first draft's), or None when no part has rows."""
    if not rows:
        return None
    return {"y": float(documents[0].get("captions", {}).get("y", 0.78)), "rows": rows[:400]}


def rebased_caption_transforms(document: EditDocument, words: list[dict]) -> dict:
    """Carry caption placements to the fresh word IDs in a combined source."""
    from ..editor_document import caption_key, mapped_words
    mapped = mapped_words(document)
    result = {}
    for index in range(0, len(mapped), document.captions.wordsPerLine):
        placement = document.captionTransforms.get(caption_key(document, mapped[index:index + document.captions.wordsPerLine]))
        if placement is not None:
            key = json.dumps(["combined", words[index]["id"]], separators=(",", ":"), ensure_ascii=False)
            result[key] = placement.model_dump()
    return result


async def document_footage(document: EditDocument) -> dict:
    """Local files for a draft's B-roll footage, keyed by footage; missing ones are skipped."""
    from ..broll import ensure_footage_file

    files = {}
    for clip in sorted(document.broll, key=lambda b: b.start):
        key = clip.footage.key
        if key in files:
            continue
        path = await ensure_footage_file(clip.footage.model_dump())
        if path is None:
            logging.getLogger(__name__).warning("B-roll footage %s unavailable for export", key)
            continue
        files[key] = path
    return files


def upgrade_document(
    document: dict,
    clip: dict,
    task: dict,
    broll_placements: list | None = None,
    duration: float | None = None,
    fx_sidecar: dict | None = None,
) -> dict:
    """Fill fields that drafts saved by older editor versions don't have.

    Missing keys would otherwise validate to defaults that drop the generated
    clip's animation and hook title. A draft without dynamic edits (no ``fx``
    key, or null) of a clip generated with them (``fx_sidecar`` is the clip's
    ``load_clip_edit_fx``) is seeded from its plan, the original included.
    A draft whose ``fx`` the user set, even to the inert "removed" state, keeps it.
    """
    from ..caption_templates import get_template

    if document.get("fx") is None:
        seeded = doc_fx_from_sidecar(fx_sidecar, duration or 0.0) if fx_sidecar and duration else None
        document = {key: value for key, value in document.items() if key != "fx"}
        if seeded is not None:
            document["fx"] = seeded

    captions = dict(document.get("captions") or {})
    if not {"template", "karaoke", "uppercase"} <= captions.keys():
        template_id = captions.get("template") or task.get("caption_template") or "default"
        template = get_template(template_id)
        captions.setdefault("template", template_id)
        captions.setdefault("karaoke", template.get("animation") == "karaoke")
        captions.setdefault("uppercase", bool(template.get("uppercase")))
    return {
        **document,
        "captions": captions,
        "hookTitle": document.get("hookTitle", (burned_hook_title(clip) or "")[:200]),
        # Drafts saved before the editor had B-roll pick up the generated clip's.
        "broll": document.get(
            "broll",
            editor_broll_from_placements(broll_placements or [], duration or 0)
            if duration
            else [],
        ),
    }


def caption_looks(include_fx: bool = False, keep: tuple[str, ...] = ()) -> dict:
    """Template styling the editor preview draws on top of its own caption style.

    Legacy templates only, unless ``include_fx`` (the caller is allowed dynamic
    edits, §0.4.9) adds the listed FX templates; ``keep`` adds the FX templates a
    stored draft already uses (unlisted ones included), so it always has its own look.
    """
    from ..edit_fx.contracts import FX_CAPTION_TEMPLATES, LISTED_FX_CAPTION_TEMPLATES

    fx_ids = tuple(t for t in FX_CAPTION_TEMPLATES if (include_fx and t in LISTED_FX_CAPTION_TEMPLATES) or t in keep)
    return _caption_looks(fx_ids)


@functools.cache
def _caption_looks(fx_ids: tuple[str, ...] = ()) -> dict:
    from ..caption_templates import CAPTION_TEMPLATES, get_template

    looks = {}
    for template_id in (*CAPTION_TEMPLATES, *fx_ids):
        template = get_template(template_id)
        looks[template_id] = {
            "animation": template.get("animation", "karaoke"),
            "wordPop": bool(template.get("word_pop", True)),
            "wordBox": bool(template.get("word_box")),
            "wordBoxColor": template.get("word_box_color") or template.get("highlight_color"),
            "highlightColor": template.get("highlight_color"),
            "emphasisColor": template.get("emphasis_color") or template.get("highlight_color"),
            "strokeColor": template.get("stroke_color"),
            "strokeWidth": int(template.get("stroke_width", 3) or 0),
            "shadow": bool(template.get("shadow")),
            "glow": bool(template.get("glow")),
            "backgroundColor": template.get("background_color"),
        }
        if template_id in fx_ids:
            looks[template_id].update(fx=True, fxStyle=template_id)
    return looks


@asynccontextmanager
async def _prepare_heartbeat(key: str):
    """Keep a running preparation distinguishable from one whose worker died."""

    async def beat():
        while True:
            await asyncio.sleep(PREPARE_HEARTBEAT_SECONDS)
            try:
                await get_store().touch_prepare(key)
            except Exception:
                logging.getLogger(__name__).warning("Editor preparation heartbeat failed", exc_info=True)

    task = asyncio.create_task(beat())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


class RenderJob:
    """Progress and cancellation between a render thread and its job row."""

    def __init__(self, job: dict):
        self.job_id = job["id"]
        self.claim = job["claim"]
        self.stop = threading.Event()
        self.progress = 0

    def report(self, percent: int) -> None:
        self.progress = percent

    async def pump(self) -> None:
        store = get_store()
        while not self.stop.is_set():
            await asyncio.sleep(1)
            try:
                # False: the user cancelled, or the job is no longer ours.
                if not await store.beat_job(self.job_id, self.claim, self.progress):
                    self.stop.set()
            except Exception:
                logging.getLogger(__name__).warning("Editor job heartbeat failed", exc_info=True)


@asynccontextmanager
async def tracked_render(claimed: dict):
    job = RenderJob(claimed)
    task = asyncio.create_task(job.pump())
    try:
        yield job
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task


async def claim_job(task_id: str, clip_id: str, filename: str, job_id: str) -> dict | None:
    """Take a queued job for this worker, or None when there is nothing to do."""
    from ..config import get_config

    store = get_store()
    job = await store.start_job(job_id)
    if job is None and not await store.job_exists(job_id):
        # Queued by a release that kept jobs as files next to the draft.
        key = editor_key(task_id, clip_id, filename)
        directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
        await ensure_session(key, directory, task_id=task_id, clip_id=clip_id, filename=filename, s3_backed=True)
        job = await store.start_job(job_id)
    return job


async def release_job(job: dict) -> None:
    """Hand an interrupted job back to the queue's own retry."""
    try:
        await asyncio.shield(get_store().requeue_job(job["id"], job["claim"]))
    except BaseException:
        logging.getLogger(__name__).warning("Could not release editor job %s", job["id"], exc_info=True)


SOURCE_INCOMPLETE = (
    "The source video could not be fetched in full for this clip, so it was not opened "
    "with missing footage. Retry in a minute."
)


async def _download_editor_source(service, task_id: str, task: dict, ranges: list, download_quality) -> Path:
    """Fetch the source for this clip into a directory owned by this job."""
    source_url, source_type = task.get("source_url"), task.get("source_type")
    if source_type == "youtube":
        from ..youtube_proxy import DownloadBudgetExceeded, YouTubeGeoRestrictedError

        # Reuses the YouTube source cache when it still covers the clip.
        download_errors: list[str] = []
        try:
            downloaded = await service.video_service.download_video(
                source_url,
                task_id=task_id,
                ranges=ranges,
                quality=download_quality,
                errors=download_errors,
            )
        except DownloadBudgetExceeded as exc:
            raise EditorPrepareError(
                "The source video is larger than the download limit, so it cannot be prepared for editing."
            ) from exc
        except YouTubeGeoRestrictedError as exc:
            raise EditorPrepareError(str(exc)) from exc
        if not downloaded:
            logging.getLogger(__name__).warning(
                "Editor source download failed for task %s: %s",
                task_id,
                "; ".join(download_errors) or "no reason reported",
            )
            raise EditorPrepareError(youtube_download_failure(download_errors))
        return Path(downloaded)
    return await run_stream_download(
        download_stream_sections, source_url,
        [{"start_time": start, "end_time": end} for start, end in ranges],
        task.get("source_duration_seconds"), task_id,
    )


async def _editor_source(service, storage, task_id: str, task: dict, cached: Path | None, ranges: list, download_quality):
    """The source file for a clip, its local ranges, and the copy this job must remove.

    Returns ``(source, downloaded, local_ranges)``. A cached file is used only
    when it holds every range in full. Another job's assembled YouTube
    timeline is hard-linked first, so that job cannot replace or delete it
    mid-read. A file that falls short is replaced by a fresh download, and a
    download that still falls short fails the preparation: the studio never
    opens a clip with part of its footage missing.
    """
    from ..youtube_source import (
        SourceCoverageError,
        is_youtube_work_path,
        local_ranges_for,
        private_work_copy,
    )
    from ..youtube_utils import get_youtube_video_id

    source_url, source_type = task.get("source_url"), task.get("source_type")
    remote = source_type in {"youtube", "twitch", "kick"}
    log = logging.getLogger(__name__)

    async def checked(path: Path) -> tuple[Path, list]:
        prepared = await service.video_service.prepare_source(path)
        return prepared, await run_in_thread(local_ranges_for, prepared, ranges, strict=True)

    owned = None
    if cached is not None and cached.exists():
        candidate = cached
        if is_youtube_work_path(cached):
            # Another job's file: read it only through a private hard link.
            video_id = get_youtube_video_id(source_url or "")
            candidate = await run_in_thread(private_work_copy, cached, video_id, task_id) if video_id else None
            owned = candidate
        if candidate is not None:
            try:
                prepared, local = await checked(candidate)
                return prepared, owned, local
            except SourceCoverageError as exc:
                log.warning("Cached source for task %s does not cover the clip: %s", task_id, exc)
                if owned is not None:
                    service._cleanup_source_video(owned, source_type, source_url)
                    owned = None
    if remote:
        downloaded = await _download_editor_source(service, task_id, task, ranges, download_quality)
        try:
            prepared, local = await checked(downloaded)
        except SourceCoverageError as exc:
            log.warning("Downloaded source for task %s does not cover the clip: %s", task_id, exc)
            service._cleanup_source_video(downloaded, source_type, source_url)
            raise EditorPrepareError(SOURCE_INCOMPLETE) from exc
        return prepared, downloaded, local

    from .video_service import resolve_upload_for_edit
    from ..media.upload_ingest import upload_copy_pending

    source = await resolve_upload_for_edit(service.video_service, source_url)
    if not source or not source.exists():
        if await upload_copy_pending(source_url or ""):
            # The file is on the API's disk and not in object storage yet.
            raise EditorPrepareError("This upload is still being saved. Try again in a minute.")
        raise EditorPrepareError(
            "The original source is unavailable. Restore the source file to edit this clip."
        )
    try:
        prepared, local = await checked(source)
    except SourceCoverageError as exc:
        raise EditorPrepareError(
            "The original upload is shorter than this clip, so it cannot be opened for editing."
        ) from exc
    return prepared, None, local


def expected_prepared_duration(ranges: list, offsets: list | None) -> float:
    """How long the editor source must play: the generated clip's own timeline."""
    if not ranges:
        return 0.0
    if offsets and len(offsets) == len(ranges):
        return float(offsets[-1]) + float(ranges[-1][1]) - float(ranges[-1][0])
    return sum(float(end) - float(start) for start, end in ranges)


def prepared_too_short(actual: float, expected: float) -> bool:
    """A prepared source shorter than its ranges lost footage on the way."""
    return expected > 0 and actual < expected - max(0.5, 0.03 * expected)


EDITOR_MEDIA_NAMES = ("clean.mp4", "layout.mp4", "layout.json", "thumbnails.jpg", ".media-version")


def write_media_version(directory: Path, version: str) -> None:
    """Record which preparation the media in this scratch directory came from."""
    (directory / ".media-version").write_text(version, encoding="utf-8")


async def restore_session_media(storage, directory: Path, *, has_layout: bool = False, version: str | None = None) -> None:
    await run_in_thread(storage.restore_editor_media, directory, has_layout=has_layout, version=version)
    if not (directory / "clean.mp4").exists():
        raise FileNotFoundError("The editor's source video is no longer stored")


@media_work
async def prepare_editor(ctx, task_id: str, clip_id: str, filename: str):
    from ..database import AsyncSessionLocal
    from .task_service import TaskService
    from ..config import get_config
    from ..video_utils import (
        create_optimized_clip,
        load_cached_transcript_data,
        get_words_for_keep_ranges,
    )
    from ..clip_editor import _caption_words_with_timings, caption_tokens
    from ..clip_source_map import load_clip_caption_settings, load_clip_output_offsets, clip_source_map_path
    key = editor_key(task_id, clip_id, filename)
    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    store = get_store()
    storage = MediaStorage()
    downloaded_source = None
    source_url = None
    source_type = None
    source = None
    service = None
    try:
        current = await store.load(key)
        if current and current["asset"].get("status") == "ready":
            # A retried or duplicate job. The session is already usable.
            return
        await store.begin_prepare(key, task_id, clip_id, filename)
        async with _prepare_heartbeat(key), AsyncSessionLocal() as db:
            service = TaskService(db)
            clip = await service.clip_repo.get_clip_by_id(db, clip_id)
            task = await service.task_repo.get_task_by_id(db, task_id)
            if (
                not task
                or not clip
                or clip["task_id"] != task_id
                or clip["filename"] != filename
            ):
                raise EditorPrepareError("The original clip changed. Reopen the editor.")
            ranges = await run_in_thread(service._get_clip_source_ranges, clip)
            source_url, source_type = task.get("source_url"), task.get("source_type")
            mode = task.get("processing_mode") or service.config.default_processing_mode
            # Editor renders are downscaled for 720p plans, so their source can be too.
            download_quality, _ = await service._media_limits(task.get("user_id"))
            cache = await service.cache_repo.get_cache(
                db, service._build_cache_key(source_url, source_type, mode, download_quality)
            )
            cached_source = (
                await run_in_thread(storage.local_path, cache["video_path"]) if cache and cache.get("video_path") else None
            )
            source_settings = await service._load_task_source_settings(task_id)
            caption_path = await run_in_thread(storage.local_path, clip["file_path"], metadata_only=True)
            caption_settings = load_clip_caption_settings(caption_path) or {}
            from ..clip_source_map import load_clip_broll, load_clip_edit_fx

            broll_placements = load_clip_broll(caption_path)
            # Dynamic edits the clip was generated with: they seed the draft (§14.1).
            fx_sidecar = load_clip_edit_fx(caption_path)
            part_words = await range_transcript(db, task_id, source_settings)
            if part_words is not None or source_settings.get("source_range"):
                # A picked part's task stores (and was charged) the part; downloads need the VOD's length.
                task = {**task, "source_duration_seconds": media_duration(task, source_settings)}
            await db.close()
            source, downloaded_source, local_ranges = await _editor_source(
                service, storage, task_id, task, cached_source, ranges, download_quality
            )
            directory.mkdir(parents=True, exist_ok=True)
            # A rebuilt session must not pick up this machine's earlier media.
            for name in EDITOR_MEDIA_NAMES:
                (directory / name).unlink(missing_ok=True)
            clean = directory / "clean.mp4"
            metadata_path = MediaStorage().local_path(clip["file_path"], metadata_only=True)
            source_offsets = load_clip_output_offsets(metadata_path, ranges)
            has_offsets = clip_source_map_path(metadata_path).exists() and "output_offsets" in json.loads(clip_source_map_path(metadata_path).read_text())
            if has_offsets and len(local_ranges) > 1:
                from ..media.ffmpeg import render_source_ranges_ffmpeg
                rendered = await run_in_thread(render_source_ranges_ffmpeg, source, local_ranges, clean, source_offsets)
            else:
                rendered = await run_in_thread(
                    create_optimized_clip,
                    source,
                    local_ranges[0][0],
                    local_ranges[-1][1],
                    clean,
                    add_subtitles=False,
                    output_format="original",
                    keep_ranges=local_ranges,
                    hook_title=None,
                    extend_to_sentence=False,
                )
            if not rendered:
                raise ValueError("Could not prepare the original video")
            from ..media.common import VALID_OUTPUT_FORMATS

            output_format = source_settings.get("output_format") or "vertical"
            if output_format not in VALID_OUTPUT_FORMATS:
                output_format = "vertical"
            # Split-screen and stacked layouts are a composed frame, not a
            # vertical or original crop. Bake that frame into the editor
            # source so preview and export show it. Other clips keep this
            # uncropped trim and the crop chosen below.
            layout_report: dict = {}
            if output_format in {"vertical", "vertical_split", "vertical_fit"}:
                from ..clip_source_map import load_clip_vertical_layout

                layout_report["baked"] = await run_in_thread(
                    bake_editor_layout, clean, output_format, source, local_ranges, True,
                    load_clip_vertical_layout(metadata_path), layout_report,
                )
            metadata = await run_in_thread(make_assets, directory)
            expected = expected_prepared_duration(ranges, source_offsets)
            # A rounded end clamped to the file's end plays that much less.
            clamped = expected_prepared_duration(ranges, None) - expected_prepared_duration(local_ranges, None)
            expected -= max(0.0, clamped)
            if prepared_too_short(metadata["duration"], expected):
                logging.getLogger(__name__).error(
                    "Editor source for clip %s is %.2fs, expected %.2fs", clip_id, metadata["duration"], expected
                )
                raise EditorPrepareError(SOURCE_INCOMPLETE)
            metadata["mediaVersion"] = uuid.uuid4().hex
            metadata["expectedDuration"] = round(expected, 3)
            if (directory / "layout.mp4").exists():
                metadata["hasLayout"] = True
                metadata.update(json.loads((directory / "layout.json").read_text()))
            # A baked Ken Burns zoom is the clip's only zoom source: the studio adds no
            # baseline pushes over it (AR-M9).
            metadata["layoutHasZoompan"] = bool(layout_report.get("zoompan"))
            transcript = load_cached_transcript_data(source)
            if not (transcript or {}).get("words") and part_words:
                # A picked part's timings live in the task's checkpoint, not beside the source.
                transcript = transcript_on_media(part_words, source)
            timed = get_words_for_keep_ranges(transcript, local_ranges, source_offsets) if transcript else []
            words = _caption_words_with_timings(
                caption_tokens(clip.get("text") or "", timed), timed, metadata["duration"]
            )
            duration = metadata["duration"]
            emphasis = generated_emphasis(words, task, (fx_sidecar or {}).get("plan"))
            original_format = output_format == "original"
            seeded_fx = doc_fx_from_sidecar(fx_sidecar, duration)
            face_track = (fx_sidecar or {}).get("face_track")
            if seeded_fx is not None and face_track and metadata.get("hasLayout") and not original_format:
                # The generated face track is relative to the framed picture, which
                # layout.mp4 replays: it is valid on that base only.
                metadata["faceTrack"] = {"base": "layout", "track": face_track}
            # Vertical clips are generated at 1080x1920; "original" keeps the source size.
            # A baked split or stack is already 1080x1920, so this vertical cover
            # shows that frame in full instead of cropping the source again.
            clip_width, clip_height = (
                (metadata["width"], metadata["height"]) if original_format else (1080, 1920)
            )
            initial_captions = initial_caption_style(
                task,
                caption_settings,
                source_settings.get("add_subtitles", True),
                clip_width,
                clip_height,
            )
            # Interview clips: the look captions keep a row per shot, as generated.
            caption_rows = None if original_format else editor_caption_rows(layout_report, initial_captions, duration)
            initial = (
                EditDocument.model_validate(
                    {
                        "segments": [{"id": "original", "start": 0, "end": duration}],
                        "framing": {"aspect": "original" if original_format else "vertical", "fit": "auto" if metadata.get("hasLayout") else "cover"},
                        "captions": initial_captions,
                        "hookTitle": (burned_hook_title(clip) or "")[:200],
                        "broll": editor_broll_from_placements(broll_placements, duration),
                        "words": [
                            {
                                "id": f"word-{i}",
                                "start": max(0, min(w["start"], duration - 0.01)),
                                "end": max(0.01, min(w["end"], duration)),
                                "text": w["text"][:120],
                                "highlight": i in emphasis,
                            }
                            for i, w in enumerate(words)
                            if w["start"] < duration and w["end"] > 0
                        ],
                        **({"fx": seeded_fx} if seeded_fx is not None else {}),
                        **({"captionRows": caption_rows} if caption_rows is not None else {}),
                    }
                )
                .validate_duration(duration)
                .model_dump()
            )
            # Ready means every worker can fetch this media, so publish first.
            await run_in_thread(storage.publish_editor_media, directory)
            await run_in_thread(write_media_version, directory, metadata["mediaVersion"])
            await store.finish_prepare(key, task_id, clip_id, filename, metadata, initial)
    except Exception as exc:
        logging.getLogger(__name__).exception("Editor preparation failed")
        await store.fail_prepare(
            key,
            task_id,
            clip_id,
            filename,
            str(exc) if isinstance(exc, EditorPrepareError) else PREPARE_FAILED,
        )
    finally:
        if downloaded_source is not None and service is not None:
            service._cleanup_source_video(downloaded_source, source_type, source_url)
        elif source is not None:
            try:
                await run_in_thread(storage.save_source_metadata, source)
            except STORAGE_ERRORS:
                logging.getLogger(__name__).warning("Source timings retained for upload retry")


def fx_render_inputs(document: EditDocument, asset: dict | None, directory: Path, *, storage=None):
    """The session's face track, verified matte and reaction GIFs bound around a dynamic-edits render
    (``editor_fx_render.studio_fx_inputs``); nothing at all for any other draft."""
    from contextlib import nullcontext

    from ..editor_document import document_needs_fx

    if not document_needs_fx(document):
        return nullcontext()
    from ..editor_fx_render import studio_fx_inputs

    return studio_fx_inputs(document, asset or {}, directory, storage=storage)


@media_work
async def export_editor(ctx, task_id: str, clip_id: str, filename: str, job_id: str):
    from ..config import get_config
    from ..database import AsyncSessionLocal
    from ..repositories.media_repository import queue_delete
    from .task_service import TaskService

    job = await claim_job(task_id, clip_id, filename, job_id)
    if job is None:
        return
    key = editor_key(task_id, clip_id, filename)
    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    store = get_store()
    storage = MediaStorage()
    output = directory / f"export-{job_id}.mp4"
    closed = False
    try:
        session = await store.load(key)
        if not session or session["asset"].get("status") != "ready":
            raise ValueError("Editor session is not ready")
        async with AsyncSessionLocal() as db:
            service = TaskService(db)
            clip = await service.clip_repo.get_clip_by_id(db, clip_id)
            if not clip or clip["task_id"] != task_id:
                raise ValueError("Clip no longer exists")
        payload = job["request"]
        document = EditDocument.model_validate(payload["document"])
        async with tracked_render(job) as render:
            await restore_session_media(
                storage, directory,
                has_layout=bool(session["asset"].get("hasLayout")),
                version=session["asset"].get("mediaVersion"),
            )
            broll_files = await document_footage(document)
            async with fx_render_inputs(document, session["asset"], directory, storage=storage):
                await run_in_thread(
                    render_document,
                    directory,
                    job_id,
                    document,
                    payload["preset"],
                    broll_files,
                    should_cancel=render.stop.is_set,
                    on_progress=render.report,
                )
            await cap_export_for_plan(task_id, output)
            await run_in_thread(storage.persist_editor_export, directory, job_id)
        finished = await store.finish_job(job_id, job["claim"], "completed")
        closed = True
        if not finished and storage.enabled:
            # Cancelled or timed out while the file uploaded. Nothing links to it.
            async with AsyncSessionLocal() as db:
                await queue_delete(db, storage.editor_reference(directory, output.name))
                await db.commit()
    except InterruptedError:
        closed = True
        await store.finish_job(job_id, job["claim"], "cancelled")
    except Exception:
        closed = True
        logging.getLogger(__name__).exception("Editor export failed")
        await store.finish_job(
            job_id, job["claim"], "failed", error="Export failed. Your draft is safe; retry the export."
        )
    finally:
        if not closed:
            await release_job(job)
        if storage.enabled:
            # The download comes from object storage; this copy was scratch.
            output.unlink(missing_ok=True)


def _prefixed_id(value: str, prefix: str, limit: int) -> str:
    """``prefix + value`` within the id limit; an over-long id keeps a hash of itself so it stays unique."""
    from ..edit_fx.rng import fnv1a32

    if len(prefix) + len(value) <= limit:
        return prefix + value
    tag = f"~{fnv1a32(value):08x}"
    return prefix + value[: max(0, limit - len(prefix) - len(tag))] + tag


def rebased_fx(document: EditDocument, cursor: float, index: int) -> dict:
    """One draft's dynamic edits on a combined source (§14.1): beats and enabled cues mapped through the draft's
    segments (S → its E, which is where it sits in the combined clean.mp4) and moved by ``cursor``, like
    ``rebased_broll``. Ids get ``e{index}:``. The freeze hold budget is not applied (AR-M14)."""
    from ..edit_fx.mapping import BEAT_ID_MAX, CUE_ID_MAX, mapped_beats
    from ..editor_document import fx_segments, mapped_fx

    if document.fx is None:
        return {"beats": [], "cues": [], "suppressed": []}
    prefix = f"e{index}:"
    cues = []
    for cue in mapped_fx(document):
        data = cue.model_dump(mode="json")
        data.update(
            id=_prefixed_id(cue.id, prefix, CUE_ID_MAX),
            beat=_prefixed_id(cue.beat, prefix, BEAT_ID_MAX) if cue.beat else "",
            start=round(cursor + cue.start, 4),
            end=round(cursor + cue.end, 4),
        )
        cues.append(data)
    beats = []
    for beat in mapped_beats(fx_segments(document), document.fx.beats):
        data = beat.model_dump(mode="json")
        data.update(
            id=_prefixed_id(beat.id, prefix, BEAT_ID_MAX),
            start=round(cursor + beat.start, 4),
            end=round(cursor + beat.end, 4),
        )
        beats.append(data)
    suppressed = [_prefixed_id(cue_id, prefix, CUE_ID_MAX) for cue_id in document.fx.suppressed]
    return {"beats": beats, "cues": cues, "suppressed": suppressed}


_TIMING_PRECISION = ("none", "approx", "word")


def combined_fx(documents: list[EditDocument], parts: list[dict]) -> dict | None:
    """The combined draft's ``fx``: globals (style, music, grade, …) from the first draft that has dynamic edits, a
    container even when the first draft has none (AI-L4), every draft's rebased beats and cues. None when no draft
    has dynamic edits, so a combination of legacy drafts stays byte-identical."""
    from ..editor_document import DocFx

    with_fx = [document.fx for document in documents if document.fx is not None]
    if not with_fx:
        return None
    first = with_fx[0]
    beats = sorted((b for part in parts for b in part["beats"]), key=lambda b: (b["start"], b["id"]))
    cues = sorted((c for part in parts for c in part["cues"]), key=lambda c: (c["start"], c["id"]))
    suppressed = [cue_id for part in parts for cue_id in part["suppressed"]]
    return DocFx.model_validate({
        **first.model_dump(mode="json"),
        "sensitivity": "serious" if any(fx.sensitivity == "serious" for fx in with_fx) else "normal",
        "timing": _TIMING_PRECISION[min(_TIMING_PRECISION.index(fx.timing) for fx in with_fx)],
        "beats": beats[:200],
        "cues": cues[:400],
        "suppressed": suppressed[:400],
        # Each draft's switched-off kinds were left out above; every kept cue plays.
        "disabled": [],
        # The combined clip has no hook title.
        "hookTitleSuppressed": False,
    }).model_dump(mode="json")


def combined_fx_sidecar(document: EditDocument, words: list[dict], outcome) -> dict:
    """``save_clip_edit_fx`` arguments for a combined clip with dynamic edits (AR-M14): its plan in combined-clean S,
    the level and the warp the render applied, so later trims and re-renders can map F → E."""
    from ..edit_fx.beats import words_hash
    from ..edit_fx.contracts import EditBeats, FxPlan
    from ..editor_document import edit_duration, fx_render_outcome, mapped_fx

    fx = document.fx
    level, applied_warp = fx_render_outcome(outcome)
    plan = FxPlan(
        style=fx.style,
        template=document.captions.template,
        seed=fx.seed,
        edit_duration=edit_duration(document),
        sensitivity=fx.sensitivity,
        timing=fx.timing,
        mood=fx.mood,
        cues=tuple(mapped_fx(document)),
        music=fx.music,
        grade=fx.grade,
        grain=fx.grain,
        progress_bar=fx.progressBar,
        loop_ending=fx.loopEnding,
        hook_title_suppressed=fx.hookTitleSuppressed,
    )
    beats = EditBeats(
        words_hash=words_hash(words), mood=fx.mood or "hype", sensitivity=fx.sensitivity, timing=fx.timing,
        beats=tuple(fx.beats),
    ) if fx.beats else None
    return {
        "style": fx.style.to_json(),
        "beats": None if beats is None else beats.model_dump(mode="json"),
        "plan": plan.model_dump(mode="json"),
        "face_track": None,
        "fx_level": level,
        "applied_warp": applied_warp,
    }


@media_work
async def combine_editor(ctx, task_id: str, clip_id: str, filename: str, job_id: str):
    """Create a new clip from draft cuts, keeping every input clip and its draft."""
    import shutil
    import tempfile
    from ..config import get_config
    from ..database import AsyncSessionLocal
    from ..repositories.edit_transaction import task_edit_transaction
    from ..broll import editor_piece_to_placement
    from ..clip_source_map import save_clip_broll, save_clip_edit_fx, save_clip_source_ranges, slice_source_timeline
    from ..editor_document import document_needs_fx, mapped_broll, mapped_words, run_render
    from .task_service import TaskService

    job = await claim_job(task_id, clip_id, filename, job_id)
    if job is None:
        return
    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    store = get_store()
    storage = MediaStorage()
    committed = False
    closed = False
    destination = None
    destination_key = None
    clip_path = None
    reference = None
    try:
        directory.mkdir(parents=True, exist_ok=True)
        entries = job["request"]["inputs"]
        # On a machine with a cold cache, fetching many clips can outlast the
        # heartbeat window. Report while it happens so the job is not taken
        # for a dead worker's.
        async with tracked_render(job) as fetching:
            for entry in entries:
                await restore_session_media(
                    storage, editor_dir(get_config().temp_dir, task_id, entry["id"], entry["filename"]),
                    version=(entry.get("state") or {}).get("mediaVersion"),
                )
            if fetching.stop.is_set():
                raise InterruptedError()
        commands, filters, joins, all_words, ranges = ["ffmpeg", "-y"], [], [], [], []
        all_broll = []
        all_caption_rows: list[dict] = []
        drafts: list[EditDocument] = []
        fx_parts: list[dict] = []
        combined_offsets = []
        all_caption_transforms = {}
        cursor = 0.0
        width, height = entries[0]["state"]["width"], entries[0]["state"]["height"]
        scale = min(1, 1920 / max(width, height))
        width, height = int(width * scale) // 2 * 2, int(height * scale) // 2 * 2
        for i, entry in enumerate(entries):
            source_dir = editor_dir(
                get_config().temp_dir, task_id, entry["id"], entry["filename"]
            )
            commands += ["-i", str(source_dir / "clean.mp4")]
            document = EditDocument.model_validate(entry["document"])
            # Each draft's B-roll stays editable in the combined clip.
            all_broll += rebased_broll(document, cursor)
            # And its interview caption rows, where they place its captions.
            all_caption_rows += rebased_caption_rows(document, cursor)
            # So do its dynamic edits, on the combined timeline.
            drafts.append(document)
            fx_parts.append(rebased_fx(document, cursor, i))
            entry_words = [
                {
                    **w,
                    "id": uuid.uuid4().hex,
                    "start": w["start"] + cursor,
                    "end": w["end"] + cursor,
                }
                for w in mapped_words(document)
            ]
            all_caption_transforms.update(rebased_caption_transforms(document, entry_words))
            all_words += entry_words
            for j, segment in enumerate(document.segments):
                key = f"{i}_{j}"
                filters.append(
                    f"[{i}:v]trim=start={segment.start}:end={segment.end},setpts=PTS-STARTPTS,scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30[v{key}]"
                )
                if entry["state"]["hasAudio"]:
                    filters.append(
                        f"[{i}:a]atrim=start={segment.start}:end={segment.end},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo[a{key}]"
                    )
                else:
                    filters.append(
                        f"anullsrc=r=48000:cl=stereo,atrim=duration={segment.end - segment.start}[a{key}]"
                    )
                joins += [f"[v{key}][a{key}]"]
                sliced, offsets = slice_source_timeline(entry["ranges"], segment.start, segment.end, entry.get("source_offsets", [])) if entry.get("source_offsets") else ([], [])
                if not offsets:
                    from ..clip_source_map import slice_source_ranges
                    sliced = slice_source_ranges(entry["ranges"], segment.start, segment.end)
                    offset = 0.0
                    for a, b in sliced:
                        offsets.append(offset); offset += b - a
                ranges += sliced
                combined_offsets += [cursor + offset for offset in offsets]
                cursor += segment.end - segment.start
        filters.append("".join(joins) + f"concat=n={len(joins)}:v=1:a=1[v][a]")
        initial = {
            **entries[0]["document"],
            "segments": [{"id": "combined", "start": 0, "end": cursor}],
            "words": all_words,
            "broll": all_broll,
            "captionTransforms": all_caption_transforms,
            # The first clip's headline doesn't describe the combination.
            "hookTitle": "",
        }
        initial.pop("fx", None)
        initial.pop("captionRows", None)
        caption_rows = combined_caption_rows([entry["document"] for entry in entries], all_caption_rows)
        if caption_rows is not None:
            initial["captionRows"] = caption_rows
        fx = combined_fx(drafts, fx_parts)
        if fx is not None:
            initial["fx"] = fx
        document = EditDocument.model_validate(initial).validate_duration(cursor)
        fx_render = document_needs_fx(document)
        with tempfile.TemporaryDirectory(prefix="combine_", dir=directory) as temp:
            work = Path(temp)
            commands += [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[v]",
                "-map",
                "[a]",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
                str(work / "clean.mp4"),
            ]
            async with tracked_render(job) as render:
                # Two passes share one job: joining the cuts, then drawing the draft.
                await run_in_thread(
                    run_render,
                    commands,
                    directory,
                    job_id,
                    cursor,
                    should_cancel=render.stop.is_set,
                    on_progress=lambda percent: render.report(percent // 2),
                )
                if render.stop.is_set():
                    raise InterruptedError()
                broll_files = await document_footage(document)
                # The combined source has no face track or matte of its own yet (§14.1).
                async with fx_render_inputs(document, {}, work, storage=storage):
                    outcome = await run_in_thread(
                        render_document,
                        work,
                        job_id,
                        document,
                        "tiktok",
                        broll_files,
                        should_cancel=render.stop.is_set,
                        on_progress=lambda percent: render.report(50 + percent // 2),
                    )
                fx_sidecar = combined_fx_sidecar(document, all_words, outcome) if fx_render and document.fx is not None else None
                await cap_export_for_plan(task_id, work / f"export-{job_id}.mp4")
                new_filename = f"combined_{uuid.uuid4().hex}.mp4"
                clip_path = Path(get_config().temp_dir) / "clips" / new_filename
                clip_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(work / f"export-{job_id}.mp4", clip_path)
                save_clip_source_ranges(clip_path, ranges, combined_offsets)
                save_clip_broll(clip_path, [
                    editor_piece_to_placement(piece)
                    for piece in mapped_broll(document)
                    if piece["footage"].key in broll_files
                ])
                if fx_sidecar is not None:
                    save_clip_edit_fx(clip_path, **fx_sidecar)
                metadata = await run_in_thread(make_assets, work)
                # Finish durable export delivery before committing the new clip.
                # Cleanup may reclaim clip_path immediately after that commit.
                shutil.copy2(work / f"export-{job_id}.mp4", directory / f"export-{job_id}.mp4")
                await run_in_thread(storage.persist_editor_export, directory, job_id)
                reference = await run_in_thread(storage.persist_clip, str(clip_path), task_id)
                new_id = str(uuid.uuid4())
                destination = editor_dir(get_config().temp_dir, task_id, new_id, new_filename)
                destination.mkdir(parents=True, exist_ok=True)
                shutil.copy2(work / "clean.mp4", destination / "clean.mp4")
                shutil.copy2(work / "thumbnails.jpg", destination / "thumbnails.jpg")
                # The new clip opens in the studio without another preparation.
                await run_in_thread(storage.publish_editor_media, destination)
                destination_key = editor_key(task_id, new_id, new_filename)
                await store.finish_prepare(
                    destination_key, task_id, new_id, new_filename, metadata, document.model_dump()
                )
                async with AsyncSessionLocal() as db:
                    service = TaskService(db)
                    async with task_edit_transaction(db, task_id):
                        clips = await service.clip_repo.get_clips_by_task(db, task_id)
                        if not all(
                            any(
                                c["id"] == entry["id"]
                                and c["filename"] == entry["filename"]
                                for c in clips
                            )
                            for entry in entries
                        ):
                            raise ValueError("An input clip changed while combining")
                        new_id = await service.clip_repo.create_clip(
                            db,
                            task_id,
                            new_filename,
                            reference,
                            "00:00",
                            service._seconds_to_mmss(cursor),
                            cursor,
                            " ".join(w["text"] for w in all_words),
                            0,
                            "Combined in the clip editor",
                            max(c["clip_order"] for c in clips) + 1,
                            clip_id=new_id,
                            # Every clip with dynamic edits in its sidecar carries fx_meta (§3.4).
                            **({"fx_meta": {
                                "fx_level": fx_sidecar["fx_level"],
                                "timing": document.fx.timing,
                                "intensity": document.fx.style.intensity,
                                "template": document.captions.template,
                            }} if fx_sidecar is not None else {}),
                        )
                        if reference != str(clip_path):
                            from ..repositories.media_repository import queue_delete
                            await queue_delete(db, str(clip_path))
                        await service.clip_repo.reorder_task_clips(db, task_id)
                    committed = True
        await store.finish_job(job_id, job["claim"], "completed", result={"clip_id": new_id})
        closed = True
    except InterruptedError:
        closed = True
        await store.finish_job(job_id, job["claim"], "cancelled")
    except Exception:
        closed = True
        logging.getLogger(__name__).exception("Combining editor drafts failed")
        await store.finish_job(
            job_id,
            job["claim"],
            "failed",
            error="Could not combine drafts. The original clips are unchanged.",
        )
    finally:
        if not closed and committed:
            # Interrupted after the clip was saved. A retry would save it twice.
            try:
                await asyncio.shield(
                    store.finish_job(job_id, job["claim"], "completed", result={"clip_id": new_id})
                )
            except BaseException:
                logging.getLogger(__name__).warning("Could not close combine job %s", job_id, exc_info=True)
        elif not closed:
            await release_job(job)
        if not committed:
            # Queue exact staged objects. A fresh DB reference check protects
            # against a commit that succeeded before its response was lost.
            from ..repositories.media_repository import queue_delete
            async with AsyncSessionLocal() as db:
                for path, prefix in [(str(directory / f"export-{job_id}.mp4"), False),
                                     (storage.editor_reference(directory, f"export-{job_id}.mp4"), False) if storage.enabled else (None, False),
                                     (reference, False),
                                     (str(clip_path) if clip_path else None, False),
                                     (str(destination) if destination else None, True),
                                     (storage.editor_reference(destination) if storage.enabled and destination else None, True)]:
                    if path:
                        await queue_delete(db, path, prefix=prefix)
                await db.commit()
            if destination_key:
                await store.delete(destination_key)
        elif storage.enabled:
            # The download comes from object storage; this copy was scratch.
            (directory / f"export-{job_id}.mp4").unlink(missing_ok=True)
