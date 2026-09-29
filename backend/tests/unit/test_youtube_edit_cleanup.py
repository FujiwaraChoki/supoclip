"""Editor downloads are temporary even when rendering fails."""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.editor_document import editor_dir
from src.services import clip_service, editor_service
from src.services.task_service import TaskService


VIDEO_ID = "abcdefghijk"
SOURCE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"


def downloaded_source(tmp_path: Path) -> Path:
    directory = tmp_path / f"videoscale-{VIDEO_ID}-test"
    directory.mkdir()
    source = directory / f"{VIDEO_ID}.mp4"
    source.write_bytes(b"temporary source")
    return source


@pytest.mark.parametrize("render_succeeds", [True, False])
async def test_editor_preparation_removes_its_download(tmp_path, monkeypatch, render_succeeds):
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setattr("src.youtube_utils.get_config", lambda: config)
    source = downloaded_source(tmp_path)
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4"), "text": ""}
    task = {"source_url": SOURCE_URL, "source_type": "youtube", "processing_mode": "fast"}
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value=None)),
        video_service=SimpleNamespace(download_video=AsyncMock(return_value=source)),
        config=SimpleNamespace(default_processing_mode="fast"),
        _build_cache_key=lambda *_: "cache-key",
        _get_clip_source_ranges=lambda _: [(0.0, 1.0)],
        _load_task_source_settings=AsyncMock(return_value={}),
        _cleanup_source_video=TaskService._cleanup_source_video,
    )

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(close=AsyncMock())

    def render(*args, **kwargs):
        if render_succeeds:
            args[3].write_bytes(b"clean clip")
        return render_succeeds

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    monkeypatch.setattr("src.video_utils.create_optimized_clip", render)
    monkeypatch.setattr(editor_service, "make_assets", lambda _: {
        "status": "ready", "width": 320, "height": 180, "fps": 30,
        "duration": 1.0, "hasAudio": False, "waveform": [],
    })
    monkeypatch.setattr("src.video_utils.load_cached_transcript_data", lambda _: None)

    await editor_service.prepare_editor({}, "task", "clip", "clip.mp4")

    assert not source.exists()
    assert not source.parent.exists()
    state = editor_service.read_state(editor_dir(str(tmp_path), "task", "clip", "clip.mp4"))
    assert state["status"] == ("ready" if render_succeeds else "failed")


@pytest.mark.parametrize("render_succeeds", [True, False])
async def test_regeneration_removes_its_download(
    isolated_clip_edits, tmp_path, monkeypatch, render_succeeds
):
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setattr("src.youtube_utils.get_config", lambda: config)
    source = downloaded_source(tmp_path)
    clip = {"id": "clip", "task_id": "task", "file_path": str(tmp_path / "clip.mp4"),
            "start_time": "00:00", "end_time": "00:01", "text": "hello"}
    service = TaskService(db=None, config=config)
    service.task_repo.get_task_by_id = AsyncMock(return_value={"source_url": SOURCE_URL, "source_type": "youtube"})
    service.task_repo.update_task_clips = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[clip])
    service.clip_repo.delete_clips_by_task = AsyncMock()
    service.clip_repo.create_clip = AsyncMock(return_value="clip")
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "vertical", "add_subtitles": True})
    service.video_service.download_video = AsyncMock(return_value=source)
    service.video_service.create_video_clips = AsyncMock(
        return_value=[] if render_succeeds else None,
        side_effect=None if render_succeeds else RuntimeError("render failed"),
    )

    if render_succeeds:
        await service.regenerate_all_clips_for_task("task", None, None, None, "default")
    else:
        with pytest.raises(RuntimeError, match="render failed"):
            await service.regenerate_all_clips_for_task("task", None, None, None, "default")

    assert not source.exists()
    assert not source.parent.exists()


@pytest.mark.parametrize("render_succeeds", [True, False])
async def test_caption_edit_removes_only_its_download(
    isolated_clip_edits, tmp_path, monkeypatch, render_succeeds
):
    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    monkeypatch.setattr("src.youtube_utils.get_config", lambda: config)
    source = downloaded_source(tmp_path)
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"saved clip")
    clip = {"id": "clip", "task_id": "task", "file_path": str(clip_path),
            "start_time": "00:00", "end_time": "00:01", "duration": 1.0}
    service = TaskService(db=None, config=config)
    service.clip_repo.get_clip_by_id = AsyncMock(return_value=clip)
    service.clip_repo.update_clip = AsyncMock()
    service.task_repo.get_task_by_id = AsyncMock(return_value={
        "source_url": SOURCE_URL, "source_type": "youtube",
    })
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.video_service.download_video = AsyncMock(return_value=source)
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "vertical"})

    def render(*args, **kwargs):
        if render_succeeds:
            args[3].write_bytes(b"clean clip")
        return render_succeeds

    def overlay(*args, **kwargs):
        output = tmp_path / "clips" / "edited.mp4"
        output.write_bytes(b"edited clip")
        return output

    monkeypatch.setattr(clip_service, "create_optimized_clip", render)
    monkeypatch.setattr(clip_service, "overlay_custom_captions", overlay)

    if render_succeeds:
        await service.update_clip_captions("task", "clip", "hello", "bottom", [])
    else:
        with pytest.raises(ValueError, match="Could not prepare the clip"):
            await service.update_clip_captions("task", "clip", "hello", "bottom", [])

    assert not source.exists()
    assert not source.parent.exists()
    assert clip_path.exists()
