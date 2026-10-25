"""Worker-level Twitch/Kick behaviour: processing, editor and re-render paths.

Download functions are mocked at the service boundary; no network or paid actors.
"""
from __future__ import annotations

import json
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import stream_source
from src.config import Config
from src.editor_document import editor_dir
from src.services import clip_service, editor_service
from src.services import video_service as video_module
from src.services.task_service import TaskService
from src.services.video_service import VideoService
from src.youtube_source import build_time_map, save_time_map

TWITCH_URL = "https://www.twitch.tv/videos/123456"
KICK_URL = "https://kick.com/creator/videos/12345678-1234-1234-1234-123456789abc"


class _Analysis:
    summary = "Summary"
    key_topics = ["topic"]

    def __init__(self, segments):
        self.most_relevant_segments = segments


def _segment(start, end):
    return SimpleNamespace(
        start_time=start, end_time=end, text="hello world", relevance_score=0.9, reasoning="good",
        virality=None, hook_title=None, hook_start_time=None, hook_end_time=None,
    )


@pytest.fixture
def stream_config(monkeypatch, tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_section_padding_seconds = 0.0
    monkeypatch.setattr(video_module, "get_config", lambda: config)
    monkeypatch.setattr(stream_source, "get_config", lambda: config)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    stream_source._operations.clear()
    yield config
    stream_source._operations.clear()


def _op_file(config, task_id, operation, name):
    path = Path(config.temp_dir) / "stream-work" / task_id / operation / "call" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"media")
    return path


def _pipeline_mocks(monkeypatch, segments):
    async def transcript(path, processing_mode="fast", source_url=None):
        Path(path).with_suffix(".transcript_cache.json").write_text(json.dumps({
            "words": [{"text": "hello", "start": 61000, "end": 61500}, {"text": "world", "start": 90000, "end": 90500}],
            "utterances": [],
        }))
        return "[01:01 - 01:02] hello world"

    monkeypatch.setattr(VideoService, "generate_transcript", staticmethod(transcript))
    monkeypatch.setattr(VideoService, "analyze_transcript", staticmethod(AsyncMock(return_value=_Analysis(segments))))
    monkeypatch.setattr(VideoService, "_get_file_duration", staticmethod(lambda _path: 600.0))
    monkeypatch.setattr(video_module, "build_clip_signal_summary", lambda *_: None)


async def test_processing_downloads_audio_then_time_mapped_sections(stream_config, monkeypatch):
    calls = []

    def audio(url, duration, task_id, *, max_duration=None, operation=None):
        calls.append(("audio", duration, task_id, max_duration, operation))
        return _op_file(stream_config, task_id, operation, "source.m4a")

    def sections(url, segments, duration, task_id, *, max_duration=None, operation=None):
        calls.append(("sections", duration, task_id, max_duration, operation))
        path = _op_file(stream_config, task_id, operation, "selected-segments.mkv")
        save_time_map(path, build_time_map([(60.0, 70.0)]))
        return path

    monkeypatch.setattr(video_module, "download_stream_audio", audio)
    monkeypatch.setattr(video_module, "download_stream_sections", sections)
    monkeypatch.setattr(video_module, "download_stream_video", lambda *a, **k: pytest.fail("full download"))
    _pipeline_mocks(monkeypatch, [_segment("01:00", "01:10")])

    result = await VideoService.process_video_complete(TWITCH_URL, "twitch", task_id="task1", max_video_duration=3600)

    assert [c[0] for c in calls] == ["audio", "sections"]
    assert all(c[3] == 3600 and c[4] == stream_source.PROCESSING_OPERATION for c in calls)
    shifted = json.loads(Path(result["video_path"]).with_suffix(".transcript_cache.json").read_text())
    # Word timings are moved onto the stitched sections' timeline (60s -> 0s).
    assert shifted["words"] == [{"text": "hello", "start": 1000, "end": 1500}]


async def test_processing_does_not_fetch_metadata_twice(stream_config, monkeypatch):
    assert not hasattr(video_module, "fetch_stream_info")


@pytest.mark.parametrize("phase", ["audio", "sections"])
async def test_exhausted_stream_chain_is_not_rerun_as_full_download(stream_config, monkeypatch, phase):
    def audio(url, duration, task_id, **_kwargs):
        if phase == "audio":
            raise stream_source.StreamSourceError(stream_source.FETCH_FAILED_MESSAGE)
        return _op_file(stream_config, task_id, "job", "source.m4a")

    def sections(*_args, **_kwargs):
        raise stream_source.StreamSourceError(stream_source.FETCH_FAILED_MESSAGE)

    full = AsyncMock()
    monkeypatch.setattr(video_module, "download_stream_audio", audio)
    monkeypatch.setattr(video_module, "download_stream_sections", sections)
    monkeypatch.setattr(video_module, "download_stream_video", full)
    _pipeline_mocks(monkeypatch, [_segment("01:00", "01:10")])

    with pytest.raises(stream_source.StreamSourceError, match="couldn't fetch"):
        await VideoService.process_video_complete(KICK_URL, "kick", task_id="task1", max_video_duration=3600)
    full.assert_not_called()


async def test_too_long_stream_is_rejected_before_media(stream_config, monkeypatch):
    def audio(*_args, **_kwargs):
        raise stream_source.StreamTooLongError("Video is too long (120 min). Maximum allowed duration is 60 minutes.")

    monkeypatch.setattr(video_module, "download_stream_audio", audio)
    monkeypatch.setattr(video_module, "download_stream_video", lambda *a, **k: pytest.fail("full download"))
    with pytest.raises(stream_source.StreamTooLongError, match="Maximum allowed duration is 60 minutes"):
        await VideoService.process_video_complete(TWITCH_URL, "twitch", task_id="task1", max_video_duration=3600)


def test_source_cleanup_removes_only_that_operation(stream_config):
    job = _op_file(stream_config, "task1", "job", "source.m4a")
    edit = _op_file(stream_config, "task1", "edit-a", "selected-segments.mkv")
    TaskService._cleanup_source_video(edit, "twitch", TWITCH_URL)
    assert not edit.exists() and job.exists()
    TaskService._cleanup_source_video(job, "kick", KICK_URL)
    assert not (Path(stream_config.temp_dir) / "stream-work" / "task1").exists()


async def test_processing_seeds_preflight_details(stream_config, monkeypatch):
    service = TaskService(db=None, config=stream_config)
    service.task_repo.get_task_by_id = AsyncMock(return_value={"source_duration_seconds": 120})
    service.task_repo.load_source_metadata = AsyncMock(return_value={"stream_resolution": {
        "provider": "apify_kick_hls", "duration": 119.5, "media_url": "https://media.test/kick.m3u8",
        "resolved_at": stream_source.time.time(),
    }})
    await service._seed_stream_source("task1")
    state = stream_source._operation("task1", "job")
    assert state.expected_duration == 120
    assert "apify_kick_hls" in state.actors_run and "apify_kick_hls" in state.actor_results


def _edit_download(stream_config, calls):
    def sections(url, segments, duration, task_id, **kwargs):
        calls.append({"duration": duration, "task_id": task_id, **kwargs})
        # A fresh operation per render, as stream_source does without operation=.
        return _op_file(stream_config, task_id, f"edit-{len(calls)}", "selected-segments.mkv")
    return sections


@pytest.mark.parametrize("render_succeeds", [True, False])
async def test_editor_prepares_stream_source_and_cleans_only_its_download(stream_config, monkeypatch, tmp_path, render_succeeds):
    other_edit = _op_file(stream_config, "task", "edit-other", "selected-segments.mkv")
    calls = []
    monkeypatch.setattr(editor_service, "download_stream_sections", _edit_download(stream_config, calls))
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4"), "text": ""}
    task = {"source_url": TWITCH_URL, "source_type": "twitch", "processing_mode": "fast", "source_duration_seconds": 600}
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value=None)),
        video_service=SimpleNamespace(
            download_video=AsyncMock(side_effect=AssertionError("YouTube path")),
            prepare_source=AsyncMock(side_effect=lambda path: path),
        ),
        config=SimpleNamespace(default_processing_mode="fast"),
        _build_cache_key=lambda *_: "cache-key",
        _media_limits=AsyncMock(return_value=(None, None)),
        _get_clip_source_ranges=lambda _: [(60.0, 70.0)],
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
        "status": "ready", "width": 320, "height": 180, "fps": 30, "duration": 1.0, "hasAudio": False, "waveform": [],
    })
    monkeypatch.setattr("src.video_utils.load_cached_transcript_data", lambda _: None)

    await editor_service.prepare_editor({}, "task", "clip", "clip.mp4")

    assert calls == [{"duration": 600, "task_id": "task"}]
    assert not (Path(stream_config.temp_dir) / "stream-work" / "task" / "edit-1").exists()
    assert other_edit.exists()
    state = editor_service.read_state(editor_dir(str(tmp_path), "task", "clip", "clip.mp4"))
    assert state["status"] == ("ready" if render_succeeds else "failed")


async def test_caption_edit_rerenders_from_stream_sections(isolated_clip_edits, stream_config, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(clip_service, "download_stream_sections", _edit_download(stream_config, calls))
    clip_path = tmp_path / "clip.mp4"
    clip_path.write_bytes(b"saved clip")
    clip = {"id": "clip", "task_id": "task", "file_path": str(clip_path), "start_time": "01:00", "end_time": "01:10", "duration": 10.0}
    service = TaskService(db=None, config=stream_config)
    service.clip_repo.get_clip_by_id = AsyncMock(return_value=clip)
    service.clip_repo.update_clip = AsyncMock()
    service.task_repo.get_task_by_id = AsyncMock(return_value={"source_url": KICK_URL, "source_type": "kick", "source_duration_seconds": 600})
    service.cache_repo.get_cache = AsyncMock(return_value=None)
    service.video_service.download_video = AsyncMock(side_effect=AssertionError("YouTube path"))
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "vertical"})

    def render(*args, **kwargs):
        args[3].write_bytes(b"clean clip")
        return True

    def overlay(*args, **kwargs):
        output = tmp_path / "clips" / "edited.mp4"
        output.parent.mkdir(exist_ok=True)
        output.write_bytes(b"edited clip")
        return output

    monkeypatch.setattr(clip_service, "create_optimized_clip", render)
    monkeypatch.setattr(clip_service, "overlay_custom_captions", overlay)

    await service.update_clip_captions("task", "clip", "hello", "bottom", [])

    assert calls == [{"duration": 600, "task_id": "task"}]
    assert not (Path(stream_config.temp_dir) / "stream-work" / "task").exists()


async def test_regenerate_all_clips_uses_stream_sections(isolated_clip_edits, stream_config, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(clip_service, "download_stream_sections", _edit_download(stream_config, calls))
    clip = {"id": "clip", "task_id": "task", "file_path": str(tmp_path / "clip.mp4"), "start_time": "01:00", "end_time": "01:10", "text": "hello"}
    service = TaskService(db=None, config=stream_config)
    service.task_repo.get_task_by_id = AsyncMock(return_value={"source_url": TWITCH_URL, "source_type": "twitch", "source_duration_seconds": 600})
    service.task_repo.update_task_clips = AsyncMock()
    service.clip_repo.get_clips_by_task = AsyncMock(return_value=[clip])
    service.clip_repo.delete_clips_by_task = AsyncMock()
    service.clip_repo.create_clip = AsyncMock(return_value="clip")
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "vertical", "add_subtitles": True})
    service.video_service.download_video = AsyncMock(side_effect=AssertionError("YouTube path"))
    service.video_service.create_video_clips = AsyncMock(return_value=[{"clip_id": 1, "filename": "new.mp4", "path": "new.mp4", "start_time": "00:10", "end_time": "00:15", "duration": 3.0}])

    await service.regenerate_all_clips_for_task("task", None, None, None, "default")

    assert len(calls) == 1 and calls[0]["duration"] == 600
    assert not (Path(stream_config.temp_dir) / "stream-work" / "task").exists()
