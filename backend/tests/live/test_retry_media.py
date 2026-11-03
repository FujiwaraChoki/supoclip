"""Opt-in recovery test with real Whisper, Ollama, ffmpeg and PostgreSQL.

Set RETRY_LIVE_SOURCE to a video downloaded directly with yt-dlp and
RETRY_LIVE_ARTIFACTS to an output directory. No hosted providers are used.
"""

import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import Config, set_config_override
from src.repositories.clip_repository import ClipRepository
from src.repositories.task_checkpoint_repository import TaskCheckpointRepository, checkpoint_input_key
from src.repositories.task_repository import TaskRepository
from src.services.task_service import TaskService
from src.services.video_service import VideoService
from src import youtube_utils
from tests.fixtures.factories import create_source, create_task, create_user


pytestmark = pytest.mark.skipif(not os.getenv("RETRY_LIVE_SOURCE"), reason="opt-in real media/provider test")


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def probe(path):
    media = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
    ]))
    assert {s["codec_type"] for s in media["streams"]} >= {"video", "audio"}
    assert float(media["format"]["duration"]) > 1
    # Decode the entire output, not just its container header.
    subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path),
                    "-f", "null", "-"], check=True, capture_output=True, timeout=180)
    return {"duration": float(media["format"]["duration"]), "sha256": digest(path)}


def local_config(root):
    config = Config()
    config.temp_dir = str(root)
    config.media_storage = "local"
    config.self_host = True
    config.monetization_enabled = False
    config.youtube_proxy_url = ""
    config.youtube_pot_provider_url = ""
    config.youtube_download_provider = config.youtube_metadata_provider = "yt_dlp"
    config.youtube_max_height = 480
    config.apify_api_token = ""
    config.rapidapi_youtube_api_key = ""
    config.transcription_provider = "whisper"
    config.whisper_model = "base"
    config.transcription_language = None
    config.llm = os.getenv("RETRY_LIVE_LLM", "ollama:qwen3.8:27b")
    assert config.llm.startswith("ollama:"), "Live recovery test only permits local Ollama"
    config.rerank_llm = config.llm
    config.ollama_base_url = "http://127.0.0.1:11434/v1"
    config.max_clips = config.fast_mode_max_clips = 3
    for name in ("openai_api_key", "openrouter_api_key", "anthropic_api_key", "google_api_key",
                 "assembly_ai_api_key", "youtube_data_api_key", "pexels_api_key"):
        setattr(config, name, "")
    return config


async def test_real_media_survives_analysis_failure_render_failure_and_cancellation(
    initialized_database, monkeypatch,
):
    root = Path(os.environ["RETRY_LIVE_ARTIFACTS"]).resolve() / str(uuid4())
    uploads = root / "uploads"
    uploads.mkdir(parents=True)
    source = uploads / f"source-{root.name}.mp4"
    url = f"upload://{source.name}"
    shutil.copy2(os.environ["RETRY_LIVE_SOURCE"], source)
    config = local_config(root)
    set_config_override(config)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    counts = {"transcription": 0, "analysis": 0, "render_positions": []}
    state = {"phase": "analysis_failure", "cancelled": False}
    real_transcribe = VideoService.generate_transcript
    real_analysis = VideoService.analyze_transcript
    real_render = VideoService.create_single_clip

    async def transcribe(*args, **kwargs):
        counts["transcription"] += 1
        assert counts["transcription"] == 1, "Retry repeated transcription"
        return await real_transcribe(*args, **kwargs)

    async def analyze(*args, **kwargs):
        if state["phase"] == "analysis_failure":
            raise RuntimeError("injected failure after transcription")
        counts["analysis"] += 1
        assert counts["analysis"] == 1, "Retry repeated AI analysis"
        return await real_analysis(*args, **kwargs)

    async def render(*args, **kwargs):
        position = args[2] + 1
        counts["render_positions"].append(position)
        if state["phase"] == "render_failure" and position == 2:
            return None
        result = await real_render(*args, **kwargs)
        assert result is not None, "Real ffmpeg rendering failed"
        if state["phase"] == "cancel_after_render":
            state["cancelled"] = True
        return result

    monkeypatch.setattr(VideoService, "generate_transcript", staticmethod(transcribe))
    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(analyze))
    monkeypatch.setattr(VideoService, "create_single_clip", staticmethod(render))

    async def cancelled():
        return state["cancelled"]

    try:
        async with sessions() as db:
            owner = await create_user(db)
            await db.execute(text("UPDATE users SET notify_on_completion=false WHERE id=:id"), {"id": owner["id"]})
            await db.commit()
            src = await create_source(db, source_type="video_url", url=url)
            task = await create_task(db, user_id=owner["id"], source_id=src["id"], status="queued")
            task_id = task["id"]
        key = checkpoint_input_key(url, "video_url", "fast", "auto", config)

        async def run():
            # A new DB session and service on every attempt simulate a fresh worker.
            async with sessions() as db:
                return await TaskService(db, config).process_task(
                    task_id, url, "video_url", output_format="original",
                    add_subtitles=True, should_cancel=cancelled,
                )

        async def requeue():
            async with sessions() as db:
                await TaskRepository.update_task_status(db, task_id, "queued")

        with pytest.raises(RuntimeError, match="injected failure after transcription"):
            await run()
        async with sessions() as db:
            checkpoint = await TaskCheckpointRepository.get(db, task_id, key)
            assert checkpoint["transcript_data"]["words"]
            assert "analysis" not in checkpoint
        source.with_suffix(".transcript_cache.json").unlink()

        state["phase"] = "render_failure"
        await requeue()
        with pytest.raises(ValueError, match="completed clips are saved"):
            await run()
        async with sessions() as db:
            checkpoint = await TaskCheckpointRepository.get(db, task_id, key)
            assert len(checkpoint["segments"]) >= 2, "Real selection needs multiple clips for this test"
            clips = await ClipRepository.get_clips_by_task(db, task_id)
            assert clips
            # Make a real edit before retry, not just an edited title fixture.
            await TaskService(db, config).trim_clip(task_id, clips[0]["id"], .1, .1)
            saved = await ClipRepository.get_clips_by_task(db, task_id)
        preserved = {clip["id"]: {"row": clip, "sha256": digest(clip["file_path"])} for clip in saved}
        files_before = set((root / "clips").glob("*.mp4"))
        state["phase"] = "cancel_after_render"
        await requeue()
        with pytest.raises(Exception, match="Task cancelled"):
            await run()
        assert set((root / "clips").glob("*.mp4")) == files_before, "Cancelled output leaked"
        async with sessions() as db:
            assert (await TaskRepository.get_task_by_id(db, task_id))["status"] == "cancelled"

        state.update(phase="success", cancelled=False)
        source.with_suffix(".transcript_cache.json").unlink(missing_ok=True)
        await requeue()
        result = await run()
        async with sessions() as db:
            clips = await ClipRepository.get_clips_by_task(db, task_id)
            assert result["clips_count"] == len(checkpoint["segments"]) == len(clips)
            for clip in clips:
                if clip["id"] in preserved:
                    assert clip == preserved[clip["id"]]["row"]
                    assert digest(clip["file_path"]) == preserved[clip["id"]]["sha256"]
        rendered_before = list(counts["render_positions"])
        await requeue()
        await run()
        assert counts["render_positions"] == rendered_before, "Completed plan rendered twice"
        assert counts["transcription"] == counts["analysis"] == 1
        media = {clip["id"]: probe(clip["file_path"]) for clip in clips}
        report = {"task_id": task_id, "artifacts": str(root), "counts": counts, "media": media,
                  "checks": ["transcript checkpoint restored", "one real AI analysis", "partial render kept",
                             "edited files unchanged", "cancelled render rolled back", "retry filled missing position",
                             "completed plan is idempotent", "all output fully decoded"]}
        (root / "report.json").write_text(json.dumps(report, indent=2, default=str))
        print(json.dumps(report, indent=2), flush=True)
    finally:
        set_config_override(None)


async def test_real_direct_youtube_download_and_retry_after_saved_clip(initialized_database, monkeypatch):
    root = Path(os.environ["RETRY_LIVE_ARTIFACTS"]).resolve() / f"youtube-{uuid4()}"
    root.mkdir(parents=True)
    config = local_config(root)
    config.max_clips = config.fast_mode_max_clips = 1
    set_config_override(config)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.setenv(name, "")
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"
    counts = {"download": 0, "transcription": 0, "analysis": 0, "render": 0}

    def forbidden(*_args, **_kwargs):
        pytest.fail("Proxy or paid download provider was invoked")

    monkeypatch.setattr(youtube_utils, "download_full_with_proxy", forbidden)
    monkeypatch.setattr(youtube_utils, "download_youtube_video_with_apify", forbidden)
    real_download = youtube_utils._download_youtube_video_with_ytdlp

    def download(*args, **kwargs):
        counts["download"] += 1
        return real_download(*args, **kwargs)

    monkeypatch.setattr(youtube_utils, "_download_youtube_video_with_ytdlp", download)
    for method, label in [("generate_transcript", "transcription"), ("analyze_transcript", "analysis"), ("create_single_clip", "render")]:
        real = getattr(VideoService, method)

        async def counted(*args, _real=real, _label=label, **kwargs):
            counts[_label] += 1
            return await _real(*args, **kwargs)

        monkeypatch.setattr(VideoService, method, staticmethod(counted))
    try:
        async with sessions() as db:
            # Isolate from earlier tests using this public source.
            await db.execute(text("DELETE FROM processing_cache WHERE source_url=:url"), {"url": url})
            owner = await create_user(db)
            await db.execute(text("UPDATE users SET notify_on_completion=false WHERE id=:id"), {"id": owner["id"]})
            await db.commit()
            source = await create_source(db, url=url)
            task = await create_task(db, user_id=owner["id"], source_id=source["id"], status="queued")
            task_id = task["id"]

        async def delivery_failure(*_args):
            raise RuntimeError("injected notification disconnect after clip commit")

        async with sessions() as db:
            with pytest.raises(RuntimeError, match="notification disconnect"):
                await TaskService(db, config).process_task(
                    task_id, url, "youtube", output_format="original", clip_ready_callback=delivery_failure,
                )
            before = await ClipRepository.get_clips_by_task(db, task_id)
            assert len(before) == 1
            before_hash = digest(before[0]["file_path"])
            await TaskRepository.update_task_status(db, task_id, "queued")
        async with sessions() as db:
            await TaskService(db, config).process_task(task_id, url, "youtube", output_format="original")
            after = await ClipRepository.get_clips_by_task(db, task_id)
            assert after == before
            assert digest(after[0]["file_path"]) == before_hash
        assert counts == {"download": 1, "transcription": 1, "analysis": 1, "render": 1}
        report = {"task_id": task_id, "counts": counts, "media": probe(after[0]["file_path"]), "proxy": False}
        (root / "report.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2), flush=True)
    finally:
        set_config_override(None)
