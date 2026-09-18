"""Background preparation and rendering for the clip studio."""

from pathlib import Path
import json
import subprocess
import uuid

from ..editor_document import (
    EditDocument,
    atomic_json,
    editor_dir,
    editor_lock,
    render_document,
)
from ..utils.async_helpers import run_in_thread


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
    from ..clip_source_map import load_clip_caption_settings

    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    try:
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
            ranges = service._get_clip_source_ranges(clip)
            source_url, source_type = task.get("source_url"), task.get("source_type")
            mode = task.get("processing_mode") or service.config.default_processing_mode
            cache = await service.cache_repo.get_cache(
                db, service._build_cache_key(source_url, source_type, mode)
            )
            source = (
                Path(cache["video_path"]) if cache and cache.get("video_path") else None
            )
            source_settings = await service._load_task_source_settings(task_id)
            caption_settings = load_clip_caption_settings(Path(clip["file_path"])) or {}
            await db.close()
            if not source or not source.exists():
                if source_type == "youtube":
                    source = Path(
                        await service.video_service.download_video(source_url)
                    )
                else:
                    source = service.video_service.resolve_local_video_path(source_url)
            if not source or not source.exists():
                raise ValueError(
                    "The original source is unavailable. Restore the source file to edit this clip."
                )
            directory.mkdir(parents=True, exist_ok=True)
            clean = directory / "clean.mp4"
            rendered = await run_in_thread(
                create_optimized_clip,
                source,
                ranges[0][0],
                ranges[-1][1],
                clean,
                add_subtitles=False,
                output_format="original",
                keep_ranges=ranges,
                hook_title=None,
                extend_to_sentence=False,
            )
            if not rendered:
                raise ValueError("Could not prepare the original video")
            metadata = await run_in_thread(make_assets, directory)
            transcript = load_cached_transcript_data(source)
            timed = get_words_for_keep_ranges(transcript, ranges) if transcript else []
            words = _caption_words_with_timings(
                (clip.get("text") or "").split(), timed, metadata["duration"]
            )
            duration = metadata["duration"]
            initial = (
                EditDocument.model_validate(
                    {
                        "segments": [{"id": "original", "start": 0, "end": duration}],
                        "framing": {
                            "aspect": "original"
                            if source_settings.get("output_format") == "original"
                            else "vertical"
                        },
                        "captions": {
                            "enabled": source_settings.get("add_subtitles", True),
                            "font": task.get("font_family")
                            if task.get("font_family")
                            in {
                                "TikTokSans-Regular",
                                "Poppins-ExtraBold",
                                "THEBOLDFONT",
                            }
                            else "TikTokSans-Regular",
                            "color": task.get("font_color") or "#FFFFFF",
                            "y": max(
                                0.1,
                                min(0.9, caption_settings.get("position_y") or 0.78),
                            ),
                        },
                        "words": [
                            {
                                "id": f"word-{i}",
                                "start": max(0, min(w["start"], duration - 0.01)),
                                "end": max(0.01, min(w["end"], duration)),
                                "text": w["text"][:120],
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


async def export_editor(ctx, task_id: str, clip_id: str, filename: str, job_id: str):
    from ..config import get_config
    from ..database import AsyncSessionLocal
    from .task_service import TaskService

    directory = editor_dir(get_config().temp_dir, task_id, clip_id, filename)
    try:
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
    try:
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
            new_filename = f"combined_{uuid.uuid4().hex}.mp4"
            clip_path = Path(get_config().temp_dir) / "clips" / new_filename
            clip_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(work / f"export-{job_id}.mp4", clip_path)
            save_clip_source_ranges(clip_path, ranges)
            metadata = await run_in_thread(make_assets, work)
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
                        str(clip_path),
                        "00:00",
                        service._seconds_to_mmss(cursor),
                        cursor,
                        " ".join(w["text"] for w in all_words),
                        0,
                        "Combined in the clip editor",
                        max(c["clip_order"] for c in clips) + 1,
                    )
                    destination = editor_dir(
                        get_config().temp_dir, task_id, new_id, new_filename
                    )
                    destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(work / "clean.mp4", destination / "clean.mp4")
                    shutil.copy2(
                        work / "thumbnails.jpg", destination / "thumbnails.jpg"
                    )
                    atomic_json(
                        destination / "draft.json",
                        {"revision": 0, "document": document.model_dump()},
                    )
                    atomic_json(destination / "original.json", document.model_dump())
                    atomic_json(destination / "asset.json", metadata)
                    await service.clip_repo.reorder_task_clips(db, task_id)
            shutil.copy2(clip_path, directory / f"export-{job_id}.mp4")
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
