"""The clip studio never prepares a clip from a source that lacks part of it.

A YouTube render file is a section timeline built for particular clips. The
editor used to accept the task's cached one for any clip: a clip outside it
failed with an IndexError, and a clip partly inside it opened truncated.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import youtube_source as source
from src.config import Config, set_config_override
from src.youtube_source import (
    SourceCoverageError,
    build_time_map,
    covered_local_ranges,
    save_time_map,
)
from tests.fixtures.editor_store import stored_state

VIDEO_ID = "abcdefghijk"
URL = f"https://youtube.com/watch?v={VIDEO_ID}"


def section_file(path, pieces):
    """A stand-in render file whose sidecar maps ``pieces`` onto t=0."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"render")
    save_time_map(path, build_time_map(pieces))
    return path


def work_render(tmp_path, name, pieces):
    return section_file(tmp_path / source.WORK_DIRNAME / name / "render.mp4", pieces)


# --- covered_local_ranges -------------------------------------------------


def test_fully_covered_ranges_map_onto_the_local_timeline(tmp_path):
    render = work_render(tmp_path, "job", [(100, 140), (300, 320)])
    assert covered_local_ranges(render, [(103, 139), (305, 310)]) == pytest.approx(
        [(3, 39), (45, 50)]
    )


def test_a_range_across_adjacent_slices_is_covered(tmp_path):
    # Two cached pieces stitched end to end, with a rounding gap between them.
    render = work_render(tmp_path, "job", [(100, 120), (120.01, 140)])
    local = covered_local_ranges(render, [(110, 130)])
    assert sum(end - start for start, end in local) == pytest.approx(20, abs=0.02)


def test_partial_overlap_is_refused_instead_of_truncated(tmp_path):
    # Production: a 36 s clip opened as 9 s from another clip's timeline.
    render = work_render(tmp_path, "job", [(91, 112)])
    with pytest.raises(SourceCoverageError) as error:
        covered_local_ranges(render, [(103, 139)])
    assert error.value.missing == pytest.approx([(112, 139)])
    # The old conversion silently clamps to the 9 s that overlap.
    assert source.local_ranges_for(render, [(103, 139)]) == pytest.approx([(12, 21)])


def test_no_overlap_is_refused_instead_of_empty_ranges(tmp_path):
    # Production: IndexError on local_ranges[0] for an empty conversion.
    render = work_render(tmp_path, "job", [(500, 530)])
    assert source.local_ranges_for(render, [(103, 139)]) == []
    with pytest.raises(SourceCoverageError) as error:
        covered_local_ranges(render, [(103, 139)])
    assert error.value.missing == pytest.approx([(103, 139)])


def test_a_gap_inside_a_multi_range_clip_is_refused(tmp_path):
    render = work_render(tmp_path, "job", [(100, 120), (200, 220)])
    with pytest.raises(SourceCoverageError) as error:
        covered_local_ranges(render, [(105, 115), (150, 160), (205, 215)])
    assert error.value.missing == pytest.approx([(150, 160)])


def test_empty_requested_ranges_are_refused(tmp_path):
    render = work_render(tmp_path, "job", [(0, 10)])
    with pytest.raises(SourceCoverageError):
        covered_local_ranges(render, [])
    with pytest.raises(SourceCoverageError):
        covered_local_ranges(render, [(5, 5)])


def test_a_section_render_without_its_timeline_is_not_a_full_file(tmp_path):
    render = tmp_path / source.WORK_DIRNAME / "job" / "render.mp4"
    render.parent.mkdir(parents=True)
    render.write_bytes(b"render")
    with pytest.raises(SourceCoverageError):
        covered_local_ranges(render, [(10, 20)])


def test_a_full_file_keeps_original_timestamps(tmp_path):
    full = tmp_path / "youtube-cache" / VIDEO_ID / "full-1080.mp4"
    full.parent.mkdir(parents=True)
    full.write_bytes(b"full")
    assert covered_local_ranges(full, [(10, 20), (30, 40)]) == [(10, 20), (30, 40)]


# --- prepare_editor -------------------------------------------------------


def prepare_fixture(tmp_path, monkeypatch, editor_store, *, cached, download, ranges, source_type="youtube"):
    from src.media_storage import MediaStorage
    from src.services import editor_service

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4"), "text": ""}
    task = {"id": "task", "user_id": "owner", "source_url": URL, "source_type": source_type}
    downloads = []

    async def download_video(url, **kwargs):
        downloads.append(kwargs["ranges"])
        return download

    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value={"video_path": str(cached)} if cached else None)),
        config=SimpleNamespace(default_processing_mode="fast"),
        video_service=SimpleNamespace(
            prepare_source=AsyncMock(side_effect=lambda path: path),
            download_video=download_video,
        ),
        _get_clip_source_ranges=lambda _clip: ranges,
        _media_limits=AsyncMock(return_value=(None, None)),
        _build_cache_key=lambda *args: "key",
        _load_task_source_settings=AsyncMock(return_value={"output_format": "original"}),
        _cleanup_source_video=lambda *args: None,
    )

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(close=AsyncMock())

    renders = []

    def create_optimized_clip(src, start, end, dest, **kwargs):
        renders.append({"source": src, "keep_ranges": kwargs["keep_ranges"]})
        dest.write_bytes(b"clean")
        return True

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    monkeypatch.setattr("src.video_utils.create_optimized_clip", create_optimized_clip)
    monkeypatch.setattr("src.video_utils.load_cached_transcript_data", lambda _: None)
    monkeypatch.setattr(editor_service, "make_assets", lambda directory: {
        "status": "ready", "width": 320, "height": 180, "fps": 30,
        "duration": sum(e - s for s, e in renders[-1]["keep_ranges"]), "hasAudio": False, "waveform": [],
    })
    monkeypatch.setattr(MediaStorage, "save_source_metadata", lambda *a, **k: None)
    monkeypatch.setattr(MediaStorage, "publish_editor_media", lambda *a, **k: None)
    return SimpleNamespace(downloads=downloads, renders=renders)


async def prepare(editor_store):
    from src.editor_state import editor_key
    from src.services.editor_service import prepare_editor

    key = editor_key("task", "clip", "clip.mp4")
    assert await editor_store.claim_prepare(key, "task", "clip", "clip.mp4", "job") == "claimed"
    await prepare_editor({}, "task", "clip", "clip.mp4")
    return await editor_store.load(key)


async def test_a_cached_render_that_covers_the_clip_is_used(tmp_path, monkeypatch, editor_store):
    cached = work_render(tmp_path, "processing", [(90, 150)])
    run = prepare_fixture(tmp_path, monkeypatch, editor_store, cached=cached, download=None, ranges=[(103, 139)])
    saved = await prepare(editor_store)
    assert saved["asset"]["status"] == "ready"
    assert run.downloads == []
    assert run.renders[0]["keep_ranges"] == pytest.approx([(13, 49)])
    assert saved["draft"]["document"]["segments"][0]["end"] == pytest.approx(36)


@pytest.mark.parametrize("pieces", [[(91, 112)], [(500, 530)]], ids=["partial-overlap", "no-overlap"])
async def test_a_partial_cached_render_is_replaced_by_the_clips_own_source(pieces, tmp_path, monkeypatch, editor_store):
    cached = work_render(tmp_path, "other-clip", pieces)
    own = work_render(tmp_path, "this-clip", [(95, 147)])
    run = prepare_fixture(tmp_path, monkeypatch, editor_store, cached=cached, download=own, ranges=[(103, 139)])
    saved = await prepare(editor_store)
    assert saved["asset"]["status"] == "ready"
    assert run.downloads == [[(103, 139)]]
    assert run.renders[0]["source"] == own
    assert run.renders[0]["keep_ranges"] == pytest.approx([(8, 44)])
    assert saved["draft"]["document"]["segments"][0]["end"] == pytest.approx(36)


@pytest.mark.parametrize("pieces", [[(91, 112)], [(500, 530)]], ids=["partial-overlap", "no-overlap"])
async def test_a_partial_download_fails_cleanly_and_renders_nothing(pieces, tmp_path, monkeypatch, editor_store):
    cached = work_render(tmp_path, "other-clip", [(500, 530)])
    partial = work_render(tmp_path, "this-clip", pieces)
    run = prepare_fixture(tmp_path, monkeypatch, editor_store, cached=cached, download=partial, ranges=[(103, 139)])
    saved = await prepare(editor_store)
    state = await stored_state("task", "clip", "clip.mp4")
    assert state["status"] == "failed"
    assert "missing part of this clip" in state["error"]
    assert run.renders == []
    assert saved["draft"] is None or not saved["draft"].get("document")


async def test_a_clip_without_ranges_fails_with_a_clear_message(tmp_path, monkeypatch, editor_store):
    run = prepare_fixture(tmp_path, monkeypatch, editor_store, cached=None, download=None, ranges=[])
    await prepare(editor_store)
    state = await stored_state("task", "clip", "clip.mp4")
    assert state["status"] == "failed" and "no footage" in state["error"]
    assert run.downloads == [] and run.renders == []


# --- concurrency ----------------------------------------------------------


def test_concurrent_section_renders_of_one_task_keep_their_own_timelines(tmp_path, monkeypatch):
    """Two clips of one task prepared at once must not share a render file.

    The work directory used to be ``<video>-<task>`` and was cleared on entry,
    so the second render deleted or replaced the first one's file and sidecar.
    """
    config = Config()
    config.temp_dir = str(tmp_path)
    config.youtube_proxy_url = "http://user:password@proxy.example:823"
    config.youtube_section_padding_seconds = 0
    monkeypatch.setattr(source, "_storage", lambda: None)
    both_assembling = threading.Barrier(2, timeout=5)

    def download(url, video_id, start, end, target, **kwargs):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"section")
        return target, start, end

    def assemble(slices, dest):
        both_assembling.wait()
        # Writes the sidecar like the real one, without ffmpeg.
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"render")
        time_map = build_time_map([(start, end) for _piece, start, end in slices])
        save_time_map(dest, time_map)
        return time_map

    monkeypatch.setattr(source, "download_section_file", download)
    monkeypatch.setattr(source, "assemble_timeline", assemble)

    def job(segment):
        return source.materialize_clip_source(URL, VIDEO_ID, [segment], task_id="task-1234567890", duration=1000)

    set_config_override(config)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(job, {"start_time": 100, "end_time": 140})
            second = pool.submit(job, {"start_time": 500, "end_time": 530})
            first_path, second_path = first.result(timeout=10), second.result(timeout=10)
    finally:
        set_config_override(None)

    assert first_path != second_path
    assert first_path.exists() and second_path.exists()
    assert covered_local_ranges(first_path, [(100, 140)]) == pytest.approx([(0, 40)])
    assert covered_local_ranges(second_path, [(500, 530)]) == pytest.approx([(0, 30)])
    with pytest.raises(SourceCoverageError):
        covered_local_ranges(first_path, [(500, 530)])
