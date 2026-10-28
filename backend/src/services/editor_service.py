"""Background preparation and rendering for the clip studio."""

from pathlib import Path
import functools
import json
import logging
import re
import subprocess
import uuid
from typing import get_args

from ..editor_document import (
    CaptionStyle,
    EditDocument,
    atomic_json,
    editor_dir,
    editor_lock,
    render_document,
)
from ..utils.async_helpers import run_in_thread
from ..media_storage import MediaStorage, STORAGE_ERRORS, media_work
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
            billing = await BillingService(db).get_usage_summary(task["user_id"])
        short_side = billing.get("output_short_side")
        if short_side and short_side < FULL_HD_SHORT_SIDE:
            await run_in_thread(cap_video_short_side, path, short_side)
    except Exception:
        logging.getLogger(__name__).warning(
            "Keeping full-size editor render for task %s", task_id, exc_info=True
        )


def read_state(directory: Path):
    path = directory / "asset.json"
    if not path.exists():
        return {"status": "unprepared"}
    state = json.loads(path.read_text())
    if state.get("status") == "ready":
        state["draft"] = json.loads((directory / "draft.json").read_text())
        state["original"] = json.loads((directory / "original.json").read_text())
    return state


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
    }


def generated_emphasis(words: list[dict], task: dict) -> set[int]:
    """Words the generated clip accented (power words, numbers, emoji words).

    The editor seeds them as highlights so users see and can toggle them.
    """
    from ..caption_templates import get_template
    from ..emoji_captions import annotate_caption_words
    from ..media.captions import emoji_rendering_supported

    template = get_template(task.get("caption_template") or "default")
    _, emphasis = annotate_caption_words(
        words,
        enable_emoji=bool(template.get("emoji", True)) and emoji_rendering_supported(),
        enable_emphasis=template.get("animation", "karaoke") != "none",
    )
    return emphasis


# Layouts that are a composed frame, not a crop the editor can rebuild.
PRESERVED_LAYOUT_KINDS = frozenset({"split", "stack", "reaction", "overlap"})


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
    allowed = PRESERVED_LAYOUT_KINDS | ({"fit", "compositor"} if include_content else set())
    if described["kind"] not in allowed:
        return None
    return {
        "kind": described["kind"],
        "landscape_recommended": described["kind"] in {"fit", "compositor"},
        "output_format": "vertical",
        "vertical_plan": (described["video_filter"], described["mode"]),
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


def bake_editor_layout(
    clean: Path,
    output_format: str,
    source: Path,
    ranges: list,
    preserve_content: bool = False,
) -> bool:
    """Compose a split or stacked layout into ``clean`` when the clip has one."""
    from ..config import get_config
    from ..media.speaker_framing import build_clip_speaker_turns

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
    if preserve_content and layout["kind"] in {"fit", "compositor"}:
        destination = clean.with_name("layout.mp4")
        if apply_editor_layout(clean, layout, destination):
            atomic_json(clean.with_name("layout.json"), {"landscapeRecommended": bool(layout.get("landscape_recommended"))})
            return True
        return False
    return apply_editor_layout(clean, layout)


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
) -> dict:
    """Fill fields that drafts saved by older editor versions don't have.

    Missing keys would otherwise validate to defaults that drop the generated
    clip's animation and hook title.
    """
    from ..caption_templates import get_template

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
        "hookTitle": document.get("hookTitle", (clip.get("hook_title") or "")[:200]),
        # Drafts saved before the editor had B-roll pick up the generated clip's.
        "broll": document.get(
            "broll",
            editor_broll_from_placements(broll_placements or [], duration or 0)
            if duration
            else [],
        ),
    }


@functools.cache
def caption_looks() -> dict:
    """Template styling the editor preview draws on top of its own caption style."""
    from ..caption_templates import CAPTION_TEMPLATES, get_template

    looks = {}
    for template_id in CAPTION_TEMPLATES:
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
    return looks


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
    from ..clip_editor import _caption_words_with_timings
    from ..clip_source_map import load_clip_caption_settings, load_clip_output_offsets, clip_source_map_path
    from ..youtube_source import local_ranges_for

    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    storage = MediaStorage()
    downloaded_source = None
    source_url = None
    source = None
    try:
        await run_in_thread(storage.restore_editor, directory)
        async with AsyncSessionLocal() as db:
            service = TaskService(db)
            clip = await service.clip_repo.get_clip_by_id(db, clip_id)
            task = await service.task_repo.get_task_by_id(db, task_id)
            if (
                not task
                or not clip
                or clip["task_id"] != task_id
                or clip["filename"] != filename
            ):
                raise ValueError("The original clip changed. Reopen the editor.")
            ranges = await run_in_thread(service._get_clip_source_ranges, clip)
            source_url, source_type = task.get("source_url"), task.get("source_type")
            mode = task.get("processing_mode") or service.config.default_processing_mode
            # Editor renders are downscaled for 720p plans, so their source can be too.
            download_quality, _ = await service._media_limits(task.get("user_id"))
            cache = await service.cache_repo.get_cache(
                db, service._build_cache_key(source_url, source_type, mode, download_quality)
            )
            source = (
                await run_in_thread(storage.local_path, cache["video_path"]) if cache and cache.get("video_path") else None
            )
            source_settings = await service._load_task_source_settings(task_id)
            caption_path = await run_in_thread(storage.local_path, clip["file_path"], metadata_only=True)
            caption_settings = load_clip_caption_settings(caption_path) or {}
            from ..clip_source_map import load_clip_broll

            broll_placements = load_clip_broll(caption_path)
            await db.close()
            if not source or not source.exists():
                if source_type == "youtube":
                    downloaded = await service.video_service.download_video(
                        source_url, task_id=task_id, ranges=ranges, quality=download_quality
                    )
                    source = Path(downloaded) if downloaded else None
                    downloaded_source = source
                elif source_type in {"twitch", "kick"}:
                    source = await run_in_thread(
                        download_stream_sections, source_url,
                        [{"start_time": start, "end_time": end} for start, end in ranges],
                        task.get("source_duration_seconds"), task_id,
                    )
                    downloaded_source = source
                else:
                    source = await run_in_thread(service.video_service.resolve_local_video_path, source_url)
            if not source or not source.exists():
                raise ValueError(
                    "The original source is unavailable. Restore the source file to edit this clip."
                )
            source = await service.video_service.prepare_source(source)
            local_ranges = await run_in_thread(local_ranges_for, source, ranges)
            directory.mkdir(parents=True, exist_ok=True)
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
            if output_format in {"vertical", "vertical_split"}:
                await run_in_thread(
                    bake_editor_layout, clean, output_format, source, local_ranges, True
                )
            metadata = await run_in_thread(make_assets, directory)
            if (directory / "layout.mp4").exists():
                metadata["hasLayout"] = True
                metadata.update(json.loads((directory / "layout.json").read_text()))
            transcript = load_cached_transcript_data(source)
            timed = get_words_for_keep_ranges(transcript, local_ranges, source_offsets) if transcript else []
            words = _caption_words_with_timings(
                (clip.get("text") or "").split(), timed, metadata["duration"]
            )
            duration = metadata["duration"]
            emphasis = generated_emphasis(words, task)
            original_format = output_format == "original"
            # Vertical clips are generated at 1080x1920; "original" keeps the source size.
            # A baked split or stack is already 1080x1920, so this vertical cover
            # shows that frame in full instead of cropping the source again.
            clip_width, clip_height = (
                (metadata["width"], metadata["height"]) if original_format else (1080, 1920)
            )
            initial = (
                EditDocument.model_validate(
                    {
                        "segments": [{"id": "original", "start": 0, "end": duration}],
                        "framing": {"aspect": "original" if original_format else "vertical", "fit": "auto" if metadata.get("hasLayout") else "cover"},
                        "captions": initial_caption_style(
                            task,
                            caption_settings,
                            source_settings.get("add_subtitles", True),
                            clip_width,
                            clip_height,
                        ),
                        "hookTitle": (clip.get("hook_title") or "")[:200],
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
                    }
                )
                .validate_duration(duration)
                .model_dump()
            )
            with editor_lock(directory):
                if not (directory / "draft.json").exists():
                    atomic_json(
                        directory / "draft.json", {"revision": 0, "document": initial}
                    )
                    atomic_json(directory / "original.json", initial)
                atomic_json(directory / "asset.json", metadata)
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Editor preparation failed")
        atomic_json(
            directory / "asset.json",
            {
                "status": "failed",
                "error": "Could not prepare the source video. Check that the source is available, then retry.",
            },
        )
    finally:
        if downloaded_source is not None:
            service._cleanup_source_video(downloaded_source, source_type, source_url)
        elif source is not None:
            await run_in_thread(storage.save_source_metadata, source)
        await run_in_thread(storage.save_editor, directory)


@media_work
async def export_editor(ctx, task_id: str, clip_id: str, filename: str, job_id: str):
    from ..config import get_config
    from ..database import AsyncSessionLocal
    from .task_service import TaskService

    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    try:
        await run_in_thread(MediaStorage().restore_editor, directory, media=True)
        async with AsyncSessionLocal() as db:
            service = TaskService(db)
            clip = await service.clip_repo.get_clip_by_id(db, clip_id)
            if not clip or clip["task_id"] != task_id:
                raise ValueError("Clip no longer exists")
        payload = json.loads((directory / f"request-{job_id}.json").read_text())
        if (directory / f"cancel-{job_id}").exists():
            raise InterruptedError()
        document = EditDocument.model_validate(payload["document"])
        broll_files = await document_footage(document)
        await run_in_thread(
            render_document, directory, job_id, document, payload["preset"], broll_files
        )
        await cap_export_for_plan(task_id, directory / f"export-{job_id}.mp4")
        await run_in_thread(MediaStorage().persist_editor_export, directory, job_id)
        atomic_json(
            directory / f"job-{job_id}.json", {"status": "completed", "progress": 100}
        )
    except InterruptedError:
        atomic_json(
            directory / f"job-{job_id}.json", {"status": "cancelled", "progress": 0}
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Editor export failed")
        atomic_json(
            directory / f"job-{job_id}.json",
            {
                "status": "failed",
                "error": "Export failed. Your draft is safe; retry the export.",
                "progress": 0,
            },
        )
    finally:
        await run_in_thread(MediaStorage().save_editor, directory)


@media_work
async def combine_editor(ctx, task_id: str, clip_id: str, filename: str, job_id: str):
    """Create a new clip from draft cuts, keeping every input clip and its draft."""
    import shutil
    import tempfile
    from ..config import get_config
    from ..database import AsyncSessionLocal
    from ..repositories.edit_transaction import task_edit_transaction
    from ..broll import editor_piece_to_placement
    from ..clip_source_map import save_clip_broll, save_clip_source_ranges, slice_source_timeline
    from ..editor_document import mapped_broll, mapped_words, run_render
    from .task_service import TaskService

    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    storage = MediaStorage()
    committed = False
    destination = None
    clip_path = None
    reference = None
    try:
        await run_in_thread(MediaStorage().restore_editor, directory)
        payload = json.loads((directory / f"request-{job_id}.json").read_text())
        entries = payload["inputs"]
        commands, filters, joins, all_words, ranges = ["ffmpeg", "-y"], [], [], [], []
        all_broll = []
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
            await run_in_thread(MediaStorage().restore_editor, source_dir, media=True)
            commands += ["-i", str(source_dir / "clean.mp4")]
            document = EditDocument.model_validate(entry["document"])
            # Each draft's B-roll stays editable in the combined clip.
            all_broll += rebased_broll(document, cursor)
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
        document = EditDocument.model_validate(initial).validate_duration(cursor)
        with tempfile.TemporaryDirectory(prefix="combine_", dir=directory) as temp:
            work = Path(temp)
            # Cancellation/progress files share the parent job for both render passes.
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
            await run_in_thread(run_render, commands, directory, job_id, cursor)
            if (directory / f"cancel-{job_id}").exists():
                raise InterruptedError()
            # A symlink allows the second rendering pass to observe cancellation.
            (work / f"cancel-{job_id}").symlink_to(directory / f"cancel-{job_id}")
            broll_files = await document_footage(document)
            await run_in_thread(render_document, work, job_id, document, "tiktok", broll_files)
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
            metadata = await run_in_thread(make_assets, work)
            # Finish durable export delivery before committing the new clip.
            # Cleanup may reclaim clip_path immediately after that commit.
            shutil.copy2(work / f"export-{job_id}.mp4", directory / f"export-{job_id}.mp4")
            await run_in_thread(MediaStorage().persist_editor_export, directory, job_id)
            reference = await run_in_thread(storage.persist_clip, str(clip_path), task_id)
            new_id = str(uuid.uuid4())
            destination = editor_dir(get_config().temp_dir, task_id, new_id, new_filename)
            destination.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / "clean.mp4", destination / "clean.mp4")
            shutil.copy2(work / "thumbnails.jpg", destination / "thumbnails.jpg")
            atomic_json(destination / "draft.json", {"revision": 0, "document": document.model_dump()})
            atomic_json(destination / "original.json", document.model_dump())
            atomic_json(destination / "asset.json", metadata)
            await run_in_thread(storage.save_editor, destination)
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
                    )
                    if reference != str(clip_path):
                        from ..repositories.media_repository import queue_delete
                        await queue_delete(db, str(clip_path))
                    await service.clip_repo.reorder_task_clips(db, task_id)
                committed = True
        atomic_json(
            directory / f"job-{job_id}.json",
            {"status": "completed", "progress": 100, "clip_id": new_id},
        )
    except InterruptedError:
        atomic_json(
            directory / f"job-{job_id}.json", {"status": "cancelled", "progress": 0}
        )
    except Exception:
        import logging

        logging.getLogger(__name__).exception("Combining editor drafts failed")
        atomic_json(
            directory / f"job-{job_id}.json",
            {
                "status": "failed",
                "progress": 0,
                "error": "Could not combine drafts. The original clips are unchanged.",
            },
        )
    finally:
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
        try:
            await run_in_thread(storage.save_editor, directory)
        except STORAGE_ERRORS:
            logging.getLogger(__name__).warning("Editor job snapshot retained for upload retry")
