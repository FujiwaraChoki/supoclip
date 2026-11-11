from contextlib import asynccontextmanager
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.editor_document import EditDocument, editor_dir
from src.editor_state import editor_key
from src.services.editor_service import (
    combine_editor,
    export_editor,
    generated_emphasis,
    initial_caption_style,
    prepare_editor,
)
from tests.fixtures.editor_store import stored_state


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg required")
async def test_combine_worker_keeps_inputs_and_creates_editable_draft(
    tmp_path, monkeypatch, editor_store
):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    clips = [
        {"id": f"clip{i}", "filename": f"clip{i}.mp4", "clip_order": i} for i in (1, 2)
    ]
    entries = []
    for clip in clips:
        directory = editor_dir(str(tmp_path), "task", clip["id"], clip["filename"])
        directory.mkdir(parents=True)
        subprocess.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=blue:size=320x180:rate=30:duration=1",
                "-c:v",
                "libx264",
                str(directory / "clean.mp4"),
            ],
            capture_output=True,
            check=True,
        )
        doc = EditDocument.model_validate(
            {
                "segments": [{"id": "a", "start": 0.1, "end": 0.9}],
                "words": [{"id": "w", "start": 0.2, "end": 0.8, "text": clip["id"]}],
                "framing": {"aspect": "original"},
            }
        )
        entries.append(
            {
                **clip,
                "document": doc.model_dump(),
                "state": {"width": 320, "height": 180, "hasAudio": False},
                "ranges": [[0, 1]],
            }
        )
    key = editor_store.put_ready("task", "clip1", "clip1.mp4", {"duration": 1}, entries[0]["document"])
    job = "b" * 32
    assert await editor_store.create_job(key, job, "combine", {"inputs": entries})
    created = []

    async def create(_db, *args, **kwargs):
        created.append(args)
        return kwargs["clip_id"]

    repository = SimpleNamespace(
        get_clips_by_task=AsyncMock(return_value=clips),
        create_clip=create,
        reorder_task_clips=AsyncMock(),
    )
    service = SimpleNamespace(
        clip_repo=repository, _seconds_to_mmss=lambda s: f"00:{int(s):02d}"
    )

    @asynccontextmanager
    async def sessions():
        yield object()

    @asynccontextmanager
    async def transaction(*args):
        yield

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr(
        "src.repositories.edit_transaction.task_edit_transaction", transaction
    )
    await combine_editor({}, "task", "clip1", "clip1.mp4", job)
    state = await editor_store.job(key, job)
    assert state["status"] == "completed", state
    assert state["clip_id"]
    assert len(clips) == 2
    assert len(created) == 1
    filename = created[0][1]
    combined = await stored_state("task", state["clip_id"], filename)
    assert combined["status"] == "ready"
    assert [w["text"] for w in combined["draft"]["document"]["words"]] == [
        "clip1",
        "clip2",
    ]
    assert combined["duration"] == pytest.approx(1.6, abs=0.1)
    assert combined["draft"]["document"]["hookTitle"] == ""
    repository.reorder_task_clips.assert_awaited_once()


async def test_prepare_failure_is_recoverable(tmp_path, monkeypatch, editor_store):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)

    @asynccontextmanager
    async def sessions():
        yield object()

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=None)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    await prepare_editor({}, "task", "missing", "missing.mp4")
    state = await stored_state("task", "missing", "missing.mp4")
    assert state["status"] == "failed"
    # The user retries from the studio: a failed session can be claimed again.
    key = editor_key("task", "missing", "missing.mp4")
    assert await editor_store.claim_prepare(key, "task", "missing", "missing.mp4", "job") == "claimed"


async def test_a_duplicate_prepare_job_leaves_a_ready_session_alone(tmp_path, monkeypatch, editor_store):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    document = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 1}]}).model_dump()
    key = editor_store.put_ready("task", "clip", "clip.mp4", {"duration": 1}, document, revision=4)
    monkeypatch.setattr(
        "src.services.task_service.TaskService",
        lambda _: (_ for _ in ()).throw(AssertionError("A ready session must not be prepared again")),
    )
    await prepare_editor({}, "task", "clip", "clip.mp4")
    saved = await editor_store.load(key)
    assert saved["asset"]["status"] == "ready" and saved["draft"]["revision"] == 4


async def test_failed_prepare_keeps_an_existing_draft(tmp_path, monkeypatch, editor_store):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    document = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 1}]}).model_dump()
    key = editor_store.put_ready("task", "clip", "clip.mp4", {"duration": 1}, document, revision=2)
    editor_store.sessions[key]["asset"] = {"status": "preparing"}

    @asynccontextmanager
    async def sessions():
        yield object()

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=None)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=None)),
    )
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    await prepare_editor({}, "task", "clip", "clip.mp4")
    saved = await editor_store.load(key)
    assert saved["asset"]["status"] == "failed"
    assert saved["draft"] == {"revision": 2, "document": document}


async def test_storage_failure_during_prepare_does_not_leave_preparing(tmp_path, monkeypatch, editor_store):
    """A session is ready only once its media is where every worker can fetch it."""
    from src.config import Config
    from src.media_storage import MediaStorage
    from src.services import editor_service

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    key = editor_key("task", "clip", "clip.mp4")
    assert await editor_store.claim_prepare(key, "task", "clip", "clip.mp4", "job") == "claimed"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"source")
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4"), "text": ""}
    task = {"id": "task", "user_id": "owner", "source_url": "upload://source.mp4", "source_type": "video_url"}
    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        cache_repo=SimpleNamespace(get_cache=AsyncMock(return_value={"video_path": str(source)})),
        config=SimpleNamespace(default_processing_mode="fast"),
        video_service=SimpleNamespace(prepare_source=AsyncMock(side_effect=lambda path: path)),
        _get_clip_source_ranges=lambda _clip: [[0, 1]],
        _media_limits=AsyncMock(return_value=(None, None)),
        _build_cache_key=lambda *args: "key",
        _load_task_source_settings=AsyncMock(return_value={"output_format": "original"}),
        _cleanup_source_video=lambda *args: None,
    )

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(close=AsyncMock())

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _: service)
    monkeypatch.setattr("src.youtube_source.local_ranges_for", lambda _source, ranges: ranges)
    monkeypatch.setattr("src.video_utils.create_optimized_clip", lambda *a, **k: a[3].write_bytes(b"clean") or True)
    monkeypatch.setattr("src.video_utils.load_cached_transcript_data", lambda _: None)
    monkeypatch.setattr(editor_service, "make_assets", lambda _: {
        "status": "ready", "width": 320, "height": 180, "fps": 30, "duration": 1.0, "hasAudio": False, "waveform": [],
    })
    monkeypatch.setattr(MediaStorage, "save_source_metadata", lambda *a, **k: None)
    monkeypatch.setattr(MediaStorage, "publish_editor_media", lambda *a, **k: (_ for _ in ()).throw(OSError("S3 unavailable")))
    await prepare_editor({}, "task", "clip", "clip.mp4")
    assert (await stored_state("task", "clip", "clip.mp4"))["status"] == "failed"

    # The same preparation succeeds once storage accepts the media.
    monkeypatch.setattr(MediaStorage, "publish_editor_media", lambda *a, **k: None)
    await prepare_editor({}, "task", "clip", "clip.mp4")
    assert (await stored_state("task", "clip", "clip.mp4"))["status"] == "ready"


def _export_fixture(tmp_path, monkeypatch, editor_store):
    from src.config import Config

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    document = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 1}]}).model_dump()
    key = editor_store.put_ready("task", "clip", "clip.mp4", {"duration": 1}, document)
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    directory.mkdir(parents=True)
    (directory / "clean.mp4").write_bytes(b"clean")

    @asynccontextmanager
    async def sessions():
        yield object()

    service = SimpleNamespace(clip_repo=SimpleNamespace(
        get_clip_by_id=AsyncMock(return_value={"id": "clip", "task_id": "task"})))
    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr("src.services.task_service.TaskService", lambda _db: service)
    monkeypatch.setattr("src.services.editor_service.cap_export_for_plan", AsyncMock())
    return key, directory, document


async def test_export_job_reads_its_request_from_the_store_and_reports_completion(tmp_path, monkeypatch, editor_store):
    key, directory, document = _export_fixture(tmp_path, monkeypatch, editor_store)
    job = "c" * 32
    await editor_store.create_job(key, job, "export", {"document": document, "preset": "reels"})
    rendered = []

    def render(directory, job_id, draft, preset, broll_files, *, should_cancel, on_progress):
        rendered.append((job_id, preset, draft.model_dump() == document))
        on_progress(40)
        assert not should_cancel()
        (directory / f"export-{job_id}.mp4").write_bytes(b"export")

    monkeypatch.setattr("src.services.editor_service.render_document", render)
    await export_editor({}, "task", "clip", "clip.mp4", job)
    assert rendered == [(job, "reels", True)]
    assert await editor_store.job(key, job) == {"id": job, "status": "completed", "progress": 100}
    # Local storage: the API on this machine serves the file from disk.
    assert (directory / f"export-{job}.mp4").read_bytes() == b"export"

    # A second delivery of the same queue job has nothing left to do.
    await export_editor({}, "task", "clip", "clip.mp4", job)
    assert len(rendered) == 1


async def test_export_job_cancelled_before_a_worker_takes_it_never_renders(tmp_path, monkeypatch, editor_store):
    key, directory, document = _export_fixture(tmp_path, monkeypatch, editor_store)
    job = "d" * 32
    await editor_store.create_job(key, job, "export", {"document": document, "preset": "tiktok"})
    assert await editor_store.cancel_job(key, job) == "cancelled"
    monkeypatch.setattr(
        "src.services.editor_service.render_document",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("A cancelled job must not render")),
    )
    await export_editor({}, "task", "clip", "clip.mp4", job)
    assert (await editor_store.job(key, job))["status"] == "cancelled"


async def test_cancelling_a_running_export_stops_its_render(tmp_path, monkeypatch, editor_store):
    import asyncio
    import time

    key, directory, document = _export_fixture(tmp_path, monkeypatch, editor_store)
    job = "e" * 32
    await editor_store.create_job(key, job, "export", {"document": document, "preset": "tiktok"})

    def render(directory, job_id, draft, preset, broll_files, *, should_cancel, on_progress):
        deadline = time.monotonic() + 10
        while not should_cancel():
            assert time.monotonic() < deadline, "The render never saw the cancellation"
            time.sleep(0.05)
        raise InterruptedError("Export cancelled")

    monkeypatch.setattr("src.services.editor_service.render_document", render)
    running = asyncio.create_task(export_editor({}, "task", "clip", "clip.mp4", job))
    while (await editor_store.job(key, job))["status"] != "rendering":
        await asyncio.sleep(0.01)
    # The cancel request reaches whichever worker has the job through the store.
    assert await editor_store.cancel_job(key, job) == "cancelling"
    await asyncio.wait_for(running, timeout=10)
    assert (await editor_store.job(key, job))["status"] == "cancelled"
    assert not (directory / f"export-{job}.mp4").exists()


async def test_export_interrupted_by_worker_shutdown_goes_back_to_the_queue(tmp_path, monkeypatch, editor_store):
    import asyncio

    key, directory, document = _export_fixture(tmp_path, monkeypatch, editor_store)
    job = "f" * 32
    await editor_store.create_job(key, job, "export", {"document": document, "preset": "tiktok"})
    monkeypatch.setattr("src.services.editor_service.document_footage", AsyncMock(side_effect=asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        await export_editor({}, "task", "clip", "clip.mp4", job)
    # The queue retries the job; that attempt must be able to claim it.
    assert (await editor_store.job(key, job))["status"] == "queued"
    assert await editor_store.start_job(job) is not None


def test_editor_starts_from_the_generated_caption_template():
    from src.media.captions import get_safe_vertical_position

    style = initial_caption_style(
        {"caption_template": "hormozi", "font_family": None, "font_color": None},
        {},
        True,
        1080,
        1920,
    )
    assert style["karaoke"] is True
    assert style["template"] == "hormozi"
    assert style["uppercase"] is True
    assert style["accent"] == "#00FF66"
    assert style["background"] is False
    assert style["wordsPerLine"] == 3
    assert style["font"] == "THEBOLDFONT"
    # Same pixel size and centre row as the generated 1080x1920 clip.
    assert style["size"] == round(32 * 1080 / 560)
    assert style["y"] * 1920 == get_safe_vertical_position(1920, int(62 * 1.5), 0.74)
    EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 1}], "captions": style}
    )

    minimal = initial_caption_style(
        {"caption_template": "minimal", "font_size": 24}, {"position_y": 0.5}, False, 1080, 1920
    )
    assert minimal["karaoke"] is False
    assert minimal["background"] is True
    assert minimal["enabled"] is False
    assert minimal["y"] < 0.5  # centred, not top-aligned, like the generated clip


def test_editor_keeps_generated_font_and_size_extremes():
    tiktok = initial_caption_style({"caption_template": "tiktok"}, {}, True, 1080, 1920)
    assert tiktok["font"] == "TikTokSans-Bold"

    largest = initial_caption_style({"font_size": 72}, {}, True, 1080, 1920)
    assert largest["size"] == 132

    # "original" clips keep the source size; the editor re-expresses it per 1080 px.
    landscape = initial_caption_style({"font_size": 24}, {}, True, 1920, 1080)
    assert landscape["size"] == round(round(24 * 1920 / 560) * 1080 / 1920)
    for style in (tiktok, largest, landscape):
        EditDocument.model_validate(
            {"segments": [{"id": "s", "start": 0, "end": 1}], "captions": style}
        )


def test_editor_seeds_the_generated_emphasis_as_highlights():
    words = [{"text": t, "start": i, "end": i + 0.5} for i, t in enumerate("this changed everything for 90% of people".split())]
    emphasis = generated_emphasis(words, {"caption_template": "default"})
    assert {words[i]["text"] for i in emphasis} >= {"90%"}
    assert "of" not in {words[i]["text"] for i in emphasis}


async def test_combine_job_reports_while_it_fetches_its_inputs(tmp_path, monkeypatch, editor_store):
    """Downloading many clips on a cold cache must not look like a dead worker."""
    import asyncio
    import time

    from src.config import Config
    from src.editor_state import STALE_RENDER_SECONDS, reap_jobs
    from src.services import editor_service

    config = Config()
    config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.config.get_config", lambda: config)
    document = EditDocument.model_validate({"segments": [{"id": "s", "start": 0, "end": 1}]}).model_dump()
    key = editor_store.put_ready("task", "clip1", "clip1.mp4", {"duration": 1}, document)
    job = "a" * 32
    entry = {"id": "clip1", "filename": "clip1.mp4", "document": document,
             "state": {"width": 320, "height": 180, "hasAudio": False}, "ranges": [[0, 1]]}
    await editor_store.create_job(key, job, "combine", {"inputs": [entry]})
    seen = {}

    async def slow_fetch(_storage, _directory, **_kwargs):
        # As if the download had been running for longer than the stale window.
        editor_store.jobs_by_id[job]["updated"] -= STALE_RENDER_SECONDS + 60
        await asyncio.sleep(1.5)
        await reap_jobs(key)
        seen["status"] = editor_store.jobs_by_id[job]["status"]
        seen["idle"] = time.time() - editor_store.jobs_by_id[job]["updated"]
        raise FileNotFoundError("stop here; the fetch is what this test is about")

    @asynccontextmanager
    async def sessions():
        yield SimpleNamespace(commit=AsyncMock(), execute=AsyncMock())

    monkeypatch.setattr("src.database.AsyncSessionLocal", sessions)
    monkeypatch.setattr(editor_service, "restore_session_media", slow_fetch)
    await combine_editor({}, "task", "clip1", "clip1.mp4", job)
    assert seen["status"] == "rendering" and seen["idle"] < 5
    assert (await editor_store.job(key, job))["status"] == "failed"
