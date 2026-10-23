"""Background preparation and rendering for the clip studio."""

from pathlib import Path
import functools
import json
import logging
import re
import subprocess
import time
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
    center_px = get_safe_vertical_position(height, int(font_px * 1.5), float(position_y))
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


def upgrade_document(document: dict, clip: dict, task: dict) -> dict:
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


def ready_editor_draft(directory: Path):
    asset_path = directory / "asset.json"
    draft_path = directory / "draft.json"
    if not asset_path.is_file() or not draft_path.is_file():
        return None
    try:
        asset = json.loads(asset_path.read_text())
        draft = json.loads(draft_path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if asset.get("status") != "ready" or not isinstance(draft.get("document"), dict):
        return None
    return asset, draft


def persist_editor_mutation(storage: MediaStorage, directory: Path, mutate) -> None:
    """Rewrite editor JSON and upload it before a restore can fetch the old copy."""
    with editor_lock(directory):
        with storage.editor_sync(directory):
            mutate()
            storage.write_editor_snapshot(directory)


def should_schedule_caption_repair(state: dict) -> bool:
    from ..clip_editor import is_synthetic_caption_timing

    if state.get("status") != "ready":
        return False
    words = ((state.get("draft") or {}).get("document") or {}).get("words") or []
    if not is_synthetic_caption_timing(words, state.get("duration")):
        return False
    now = time.time()
    repair = state.get("captionTimingRepair")
    started = float(state.get("captionTimingRepairStartedAt") or 0)
    if repair == "running" and now - started < 900:
        return False
    checked = float(state.get("captionTimingCheckedAt") or 0)
    if (
        repair == "done"
        and state.get("captionTiming") == "estimated"
        and now - checked < 60
    ):
        return False
    return True


def mark_caption_repair_running(storage: MediaStorage, directory: Path) -> bool:
    decision = {"enqueue": False}

    def mutate():
        asset_path = directory / "asset.json"
        if not asset_path.exists():
            return
        asset = json.loads(asset_path.read_text())
        if asset.get("status") != "ready":
            return
        now = time.time()
        started = float(asset.get("captionTimingRepairStartedAt") or 0)
        if asset.get("captionTimingRepair") == "running" and now - started < 900:
            return
        asset["captionTiming"] = "estimated"
        asset["captionTimingRepair"] = "running"
        asset["captionTimingRepairStartedAt"] = now
        atomic_json(asset_path, asset)
        decision["enqueue"] = True

    persist_editor_mutation(storage, directory, mutate)
    return decision["enqueue"]


def finish_caption_timing(storage: MediaStorage, directory: Path, timing: str) -> None:
    def mutate():
        asset_path = directory / "asset.json"
        if not asset_path.exists():
            return
        asset = json.loads(asset_path.read_text())
        if asset.get("status") != "ready":
            return
        asset["captionTiming"] = timing
        asset["captionTimingRepair"] = "done"
        asset["captionTimingCheckedAt"] = time.time()
        atomic_json(asset_path, asset)

    persist_editor_mutation(storage, directory, mutate)


def apply_saved_caption_repair(storage, directory: Path, timed_words, duration: float) -> str:
    """Retime captions on a saved draft and upload the result. Returns speech or estimated."""
    from ..clip_editor import apply_repaired_words, is_synthetic_caption_timing

    outcome = {"timing": "estimated"}

    def mutate():
        draft_path = directory / "draft.json"
        original_path = directory / "original.json"
        asset_path = directory / "asset.json"
        if not draft_path.exists() or not asset_path.exists():
            return
        draft = json.loads(draft_path.read_text())
        document = draft.get("document") or {}
        words = document.get("words") or []
        if is_synthetic_caption_timing(words, duration):
            repaired = apply_repaired_words(document, timed_words, duration)
            if repaired is not None:
                draft["revision"] = int(draft.get("revision") or 0) + 1
                draft["document"] = repaired
                atomic_json(draft_path, draft)
                outcome["timing"] = (
                    "estimated"
                    if is_synthetic_caption_timing(repaired.get("words") or [], duration)
                    else "speech"
                )
        else:
            outcome["timing"] = "speech"
        if original_path.exists():
            original = json.loads(original_path.read_text())
            repaired_original = apply_repaired_words(original, timed_words, duration)
            if repaired_original is not None:
                atomic_json(original_path, repaired_original)
        asset = json.loads(asset_path.read_text())
        asset["captionTiming"] = outcome["timing"]
        asset["captionTimingRepair"] = "done"
        asset["captionTimingCheckedAt"] = time.time()
        atomic_json(asset_path, asset)

    persist_editor_mutation(storage, directory, mutate)
    return outcome["timing"]


async def repair_saved_captions(
    storage: MediaStorage,
    directory: Path,
    *,
    source_url: str | None,
    source_type: str | None,
    original_ranges,
    cached_video_path: str | None,
) -> str:
    """Repair an existing editor draft. Returns done, download, or prepare."""
    from ..clip_editor import is_synthetic_caption_timing, resolve_speech_words

    saved = ready_editor_draft(directory)
    if saved is None:
        return "prepare"
    asset, draft = saved
    duration = float(asset.get("duration") or 0)
    words = (draft.get("document") or {}).get("words") or []
    if not is_synthetic_caption_timing(words, duration):
        # A repaired S3 draft can land on a machine whose asset still says the
        # timings are estimated or a repair is in progress.
        if asset.get("captionTiming") == "estimated" or asset.get("captionTimingRepair") == "running":
            finish_caption_timing(storage, directory, "speech")
        return "done"
    clean = directory / "clean.mp4"

    def lookup(*, allow_transcribe: bool, segment: Path | None):
        return resolve_speech_words(
            source=None,
            original_ranges=original_ranges,
            source_url=source_url,
            source_type=source_type,
            cached_video_path=cached_video_path,
            segment_path=segment,
            allow_transcribe=allow_transcribe,
            duration=duration,
        )

    speech, missing = await run_in_thread(lookup, allow_transcribe=False, segment=clean if clean.exists() else None)
    if not missing:
        apply_saved_caption_repair(storage, directory, speech, duration)
        return "done"
    already = (
        asset.get("captionTimingRepair") == "done" and asset.get("captionTiming") == "estimated"
    )
    if not clean.exists():
        await run_in_thread(storage.restore_editor, directory, media=True)
    if clean.exists() and not already:
        speech, missing = await run_in_thread(lookup, allow_transcribe=True, segment=clean)
        apply_saved_caption_repair(storage, directory, [] if missing else speech, duration)
        return "done"
    if clean.exists():
        # Transcription already ran and the caches are still empty. Refresh the
        # cooldown without downloading the source again.
        apply_saved_caption_repair(storage, directory, [], duration)
        return "done"
    return "download"


@media_work
async def prepare_editor(ctx, task_id: str, clip_id: str, filename: str):
    from ..database import AsyncSessionLocal
    from .task_service import TaskService
    from ..config import get_config
    from ..video_utils import create_optimized_clip
    from ..clip_editor import _caption_words_with_timings, resolve_speech_words
    from ..clip_source_map import load_clip_caption_settings
    from ..youtube_source import local_ranges_for

    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    storage = MediaStorage()
    downloaded_source = None
    source_url = None
    source = None
    ready_before = False
    try:
        await run_in_thread(storage.restore_editor, directory)
        await run_in_thread(storage.adopt_remote_caption_repair, directory)
        ready_before = ready_editor_draft(directory) is not None
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
            cached_video = cache.get("video_path") if cache else None
            source_settings = await service._load_task_source_settings(task_id)
            caption_path = await run_in_thread(storage.local_path, clip["file_path"], metadata_only=True)
            caption_settings = load_clip_caption_settings(caption_path) or {}
            await db.close()
            repair = await repair_saved_captions(
                storage,
                directory,
                source_url=source_url,
                source_type=source_type,
                original_ranges=ranges,
                cached_video_path=cached_video,
            )
            if repair == "done":
                return
            source = (
                await run_in_thread(storage.local_path, cached_video) if cached_video else None
            )
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
            local_ranges = await run_in_thread(local_ranges_for, source, ranges)
            directory.mkdir(parents=True, exist_ok=True)
            clean = directory / "clean.mp4"
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
            metadata = await run_in_thread(make_assets, directory)
            duration = metadata["duration"]
            repairing = ready_editor_draft(directory) is not None
            speech, missing = await run_in_thread(
                resolve_speech_words,
                source=source,
                original_ranges=ranges,
                local_ranges=local_ranges,
                source_url=source_url,
                source_type=source_type,
                cached_video_path=cached_video,
                segment_path=clean,
                allow_transcribe=True,
                duration=duration,
            )
            if repairing:
                timing = apply_saved_caption_repair(
                    storage, directory, [] if missing else speech, duration
                )
                initial = None
            else:
                caption_words = _caption_words_with_timings(
                    (clip.get("text") or "").split(), [] if missing else speech, duration
                )
                timing = "estimated" if caption_words.estimated else "speech"
                words = caption_words.words
                emphasis = generated_emphasis(words, task)
                original_format = source_settings.get("output_format") == "original"
                # Vertical clips are generated at 1080x1920; "original" keeps the source size.
                clip_width, clip_height = (
                    (metadata["width"], metadata["height"]) if original_format else (1080, 1920)
                )
                initial = (
                    EditDocument.model_validate(
                        {
                            "segments": [{"id": "original", "start": 0, "end": duration}],
                            "framing": {"aspect": "original" if original_format else "vertical"},
                            "captions": initial_caption_style(
                                task,
                                caption_settings,
                                source_settings.get("add_subtitles", True),
                                clip_width,
                                clip_height,
                            ),
                            "hookTitle": (clip.get("hook_title") or "")[:200],
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
            metadata["captionTiming"] = timing
            metadata["captionTimingRepair"] = "done"
            metadata["captionTimingCheckedAt"] = time.time()
            with editor_lock(directory):
                if not repairing and not (directory / "draft.json").exists():
                    atomic_json(
                        directory / "draft.json", {"revision": 0, "document": initial}
                    )
                    atomic_json(directory / "original.json", initial)
                atomic_json(directory / "asset.json", metadata)
    except Exception:
        logging.getLogger(__name__).exception("Editor preparation failed")
        if ready_before and ready_editor_draft(directory) is not None:
            try:
                finish_caption_timing(storage, directory, "estimated")
            except Exception:
                logging.getLogger(__name__).exception(
                    "Could not keep the existing editor draft after caption repair failed"
                )
        else:
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
        await run_in_thread(
            render_document, directory, job_id, document, payload["preset"]
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
    from ..clip_source_map import save_clip_source_ranges, slice_source_ranges
    from ..editor_document import mapped_words, run_render
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
            all_words += [
                {
                    **w,
                    "id": uuid.uuid4().hex,
                    "start": w["start"] + cursor,
                    "end": w["end"] + cursor,
                }
                for w in mapped_words(document)
            ]
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
                cursor += segment.end - segment.start
                ranges += slice_source_ranges(
                    entry["ranges"], segment.start, segment.end
                )
        filters.append("".join(joins) + f"concat=n={len(joins)}:v=1:a=1[v][a]")
        initial = {
            **entries[0]["document"],
            "segments": [{"id": "combined", "start": 0, "end": cursor}],
            "words": all_words,
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
            await run_in_thread(render_document, work, job_id, document, "tiktok")
            await cap_export_for_plan(task_id, work / f"export-{job_id}.mp4")
            new_filename = f"combined_{uuid.uuid4().hex}.mp4"
            clip_path = Path(get_config().temp_dir) / "clips" / new_filename
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / f"export-{job_id}.mp4", clip_path)
            save_clip_source_ranges(clip_path, ranges)
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
