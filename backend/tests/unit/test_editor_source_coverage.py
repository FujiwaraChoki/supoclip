"""The studio never opens a clip on a source that lacks part of its footage.

Regression for a production incident: four editor preparations of one task
started within seconds. They shared one ``youtube-work`` file, so the fourth
clip (310.46-346.88s, 36.4s) was prepared from the first clip's timeline
(255.36-319.5s padded) and silently clamped to 9s.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.config import Config
from src.services import editor_service
from src.services.task_service import TaskService
from src.youtube_source import (
    SourceCoverageError,
    TimeMap,
    _work_dir,
    build_time_map,
    local_ranges_for,
    merge_ranges,
    private_work_copy,
    save_time_map,
)
from tests.fixtures.editor_store import stored_state

VIDEO_ID = "s2gtZrXGHCQ"
SOURCE_URL = f"https://www.youtube.com/watch?v={VIDEO_ID}"
CLIP_ONE = (275.36, 309.5)
CLIP_FOUR = (310.46, 346.88)


def padded(*ranges) -> TimeMap:
    return build_time_map(merge_ranges(list(ranges), padding=10.0))


def test_lenient_mapping_reproduces_the_incident():
    # The old behaviour, kept for callers that render what they can.
    local = padded(CLIP_ONE).to_local_ranges([CLIP_FOUR])
    assert round(sum(end - start for start, end in local), 2) == 9.04


def test_strict_mapping_refuses_a_timeline_without_the_clip():
    with pytest.raises(SourceCoverageError):
        padded(CLIP_ONE).to_local_ranges([CLIP_FOUR], strict=True)
    assert padded(CLIP_FOUR).to_local_ranges([CLIP_FOUR], strict=True) == [(10.0, pytest.approx(46.42))]


def test_strict_mapping_joins_adjacent_pieces_and_rejects_gaps():
    joined = build_time_map([(100.0, 120.0), (120.0, 150.0)])
    assert joined.to_local_ranges([(110.0, 140.0)], strict=True) == [(10.0, 40.0)]
    gap = build_time_map([(100.0, 120.0), (125.0, 150.0)])
    with pytest.raises(SourceCoverageError):
        gap.to_local_ranges([(110.0, 140.0)], strict=True)
    # Within a few frames of an edge is still covered.
    assert gap.to_local_ranges([(100.1, 119.9)], strict=True)


@pytest.fixture
def config(tmp_path, monkeypatch):
    runtime = Config()
    runtime.temp_dir = str(tmp_path)
    for target in ("src.config.get_config", "src.youtube_utils.get_config", "src.youtube_source.get_config"):
        monkeypatch.setattr(target, lambda: runtime)
    return runtime


def test_jobs_of_one_task_get_their_own_work_directory(config):
    first = _work_dir(VIDEO_ID, "9f0b48ff-1903-40cd-9fc6-b9806978fa78")
    (first / "render.mp4").write_bytes(b"first job")
    second = _work_dir(VIDEO_ID, "9f0b48ff-1903-40cd-9fc6-b9806978fa78")
    assert first != second
    assert (first / "render.mp4").read_bytes() == b"first job"


def test_private_copy_survives_the_owner_removing_its_directory(config):
    owner = _work_dir(VIDEO_ID, "task")
    render = owner / "render.mp4"
    render.write_bytes(b"timeline")
    save_time_map(render, padded(CLIP_FOUR))
    copy = private_work_copy(render, VIDEO_ID, "task")
    assert copy is not None and copy.parent != owner
    import shutil

    shutil.rmtree(owner)
    assert copy.read_bytes() == b"timeline"
    assert local_ranges_for(copy, [CLIP_FOUR], strict=True)


def work_render(config, ranges) -> Path:
    render = _work_dir(VIDEO_ID, "task") / "render.mp4"
    render.write_bytes(b"render")
    save_time_map(render, padded(*ranges))
    return render


def prepare_harness(monkeypatch, tmp_path, *, cached: Path | None, downloaded: Path, duration: float):
    clip = {"id": "clip4", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4"), "text": ""}
    task = {"source_url": SOURCE_URL, "source_type": "youtube", "processing_mode": "fast"}
    download = AsyncMock(return_value=downloaded)
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(
            return_value={"video_path": str(cached)} if cached else None
        )),
        video_service=SimpleNamespace(
            download_video=download,
            prepare_source=AsyncMock(side_effect=lambda path: path),
        ),
        config=SimpleNamespace(default_processing_mode="fast"),
        _build_cache_key=lambda *_: "cache-key",
        _media_limits=AsyncMock(return_value=(None, None)),
        _get_clip_source_ranges=lambda _: [CLIP_FOUR],
        _load_task_source_settings=AsyncMock(return_value={"output_format": "original"}),
        _cleanup_source_video=TaskService._cleanup_source_video,
    )
    rendered = []

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(close=AsyncMock())

    def render(source, start, end, output, **kwargs):
        rendered.append({"source": source, "ranges": kwargs["keep_ranges"]})
        output.write_bytes(b"clean clip")
        return True

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    monkeypatch.setattr("src.video_utils.create_optimized_clip", render)
    monkeypatch.setattr(editor_service, "make_assets", lambda _: {
        "status": "ready", "width": 1920, "height": 1080, "fps": 30,
        "duration": duration, "hasAudio": False, "waveform": [],
    })
    monkeypatch.setattr("src.video_utils.load_cached_transcript_data", lambda _: None)
    return download, rendered


async def test_cached_timeline_of_another_clip_is_not_used(config, monkeypatch, tmp_path):
    sibling = work_render(config, [CLIP_ONE])
    fresh = work_render(config, [CLIP_FOUR])
    download, rendered = prepare_harness(monkeypatch, tmp_path, cached=sibling, downloaded=fresh, duration=36.42)

    await editor_service.prepare_editor({}, "task", "clip4", "clip.mp4")

    state = await stored_state("task", "clip4", "clip.mp4")
    assert state["status"] == "ready", state
    download.assert_awaited_once()
    assert rendered[0]["source"] == fresh
    assert rendered[0]["ranges"] == [(10.0, pytest.approx(46.42))]
    assert state["draft"]["document"]["segments"][0]["end"] == pytest.approx(36.42)
    # The sibling job still owns its file; this job removed only its own copies.
    assert sibling.exists()
    assert not fresh.exists()


async def test_cached_timeline_with_the_clip_is_reused_without_a_download(config, monkeypatch, tmp_path):
    cached = work_render(config, [CLIP_ONE, CLIP_FOUR])
    download, rendered = prepare_harness(monkeypatch, tmp_path, cached=cached, downloaded=cached, duration=36.42)

    await editor_service.prepare_editor({}, "task", "clip4", "clip.mp4")

    assert (await stored_state("task", "clip4", "clip.mp4"))["status"] == "ready"
    download.assert_not_awaited()
    # It read a private hard link, never the other job's path.
    assert rendered[0]["source"] != cached
    assert cached.exists()


async def test_a_download_that_still_lacks_the_clip_fails_loudly(config, monkeypatch, tmp_path):
    short = work_render(config, [CLIP_ONE])
    _download, rendered = prepare_harness(monkeypatch, tmp_path, cached=None, downloaded=short, duration=9.04)

    await editor_service.prepare_editor({}, "task", "clip4", "clip.mp4")

    state = await stored_state("task", "clip4", "clip.mp4")
    assert state["status"] == "failed"
    assert state["error"] == editor_service.SOURCE_INCOMPLETE
    assert rendered == []


async def test_a_prepared_source_shorter_than_the_clip_is_rejected(config, monkeypatch, tmp_path):
    fresh = work_render(config, [CLIP_FOUR])
    prepare_harness(monkeypatch, tmp_path, cached=None, downloaded=fresh, duration=9.07)

    await editor_service.prepare_editor({}, "task", "clip4", "clip.mp4")

    state = await stored_state("task", "clip4", "clip.mp4")
    assert state["status"] == "failed"
    assert state["error"] == editor_service.SOURCE_INCOMPLETE


def test_expected_duration_follows_crossfaded_offsets():
    ranges = [(0.0, 10.0), (20.0, 30.0)]
    assert editor_service.expected_prepared_duration(ranges, None) == 20.0
    assert editor_service.expected_prepared_duration(ranges, [0.0, 9.5]) == 19.5
    assert editor_service.prepared_too_short(9.07, 36.42)
    assert not editor_service.prepared_too_short(36.1, 36.42)
