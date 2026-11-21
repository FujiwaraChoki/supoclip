"""A user's cancel that stops a stream download is a cancel, not a worker failure."""

import logging
import threading
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src import stream_source
from src.observability import PostHogExceptionHandler
from src.stream_source import FETCH_FAILED_MESSAGE, TIMEOUT_MESSAGE, StreamSourceError
from src.stream_transfer import run_stream_download
from src.workers import tasks as worker_tasks

TWITCH_VOD = "https://www.twitch.tv/videos/123456789"


@pytest.fixture
def worker(monkeypatch):
    import src.database
    import src.runtime_settings
    import src.services.task_service
    import src.workers.progress
    import src.workers.video_admission as admission

    db = SimpleNamespace(commit=AsyncMock(), rollback=AsyncMock())

    @asynccontextmanager
    async def session():
        yield db

    progress = SimpleNamespace(update=AsyncMock(), error=AsyncMock(), clip_ready=AsyncMock())
    state = SimpleNamespace(process_task=AsyncMock(), progress=progress, captured=[])
    monkeypatch.setattr(src.database, "AsyncSessionLocal", session)
    monkeypatch.setattr(src.runtime_settings, "load_runtime_settings_cache", AsyncMock())
    monkeypatch.setattr(
        src.services.task_service, "TaskService",
        lambda _db: SimpleNamespace(process_task=state.process_task),
    )
    monkeypatch.setattr(src.workers.progress, "ProgressTracker", lambda *_: progress)
    monkeypatch.setattr(admission, "admit_video_job", AsyncMock(return_value="run"))
    monkeypatch.setattr(admission, "hold_for_upload", AsyncMock(return_value="run"))
    state.release = AsyncMock()
    state.wake = AsyncMock()
    monkeypatch.setattr(admission, "release_video_slot", state.release)
    monkeypatch.setattr(admission, "wake_next_video_jobs", state.wake)

    handler = PostHogExceptionHandler(
        SimpleNamespace(capture_exception=lambda *a, **k: state.captured.append(a[0]))
    )
    root = logging.getLogger()
    root.addHandler(handler)
    yield state
    root.removeHandler(handler)


def _ctx(*, cancel_flag):
    async def get(key):
        if key == "task_cancel:task-1" and cancel_flag:
            return b"1"
        return None

    redis = SimpleNamespace(set=AsyncMock(), sadd=AsyncMock(), get=AsyncMock(side_effect=get))
    # Last try: a failure here would be dead-lettered.
    return {"redis": redis, "job_try": worker_tasks.WorkerSettings.max_tries}


async def _run(ctx):
    return await worker_tasks.process_video_task(ctx, "task-1", TWITCH_VOD, "twitch", "user-1")


async def _cancelled_stream_download(monkeypatch):
    """The exception a user's cancel produces, raised by the real stream download path."""

    def stalled_download(*_args, **_kwargs):
        transfer = stream_source.active_transfer()
        while True:
            transfer.check()
            threading.Event().wait(0.01)

    monkeypatch.setattr(stream_source, "_download_unbounded", stalled_download)

    async def cancelled():
        return True

    try:
        await run_stream_download(
            stream_source.download_stream_video, TWITCH_VOD, 600, "task-1", should_cancel=cancelled
        )
    except StreamSourceError as exc:
        return exc
    raise AssertionError("the download was not cancelled")


def _assert_reported_as_failure(worker, ctx, error_type):
    assert [type(e) for e in worker.captured] == [error_type]
    ctx["redis"].sadd.assert_awaited_once_with("tasks:dead_letter", "task-1")
    worker.progress.error.assert_awaited_once()
    worker.release.assert_awaited_once()


async def test_cancelled_stream_download_is_not_reported_as_a_worker_failure(worker, monkeypatch, caplog):
    error = await _cancelled_stream_download(monkeypatch)
    assert str(error) == "Stream download cancelled"
    worker.process_task.side_effect = error
    ctx = _ctx(cancel_flag=True)

    with caplog.at_level(logging.INFO):
        result = await _run(ctx)

    assert result == {"task_id": "task-1", "status": "cancelled"}
    assert worker.captured == []
    assert not [r for r in caplog.records if r.exc_info or r.levelno >= logging.ERROR]
    ctx["redis"].set.assert_not_awaited()
    ctx["redis"].sadd.assert_not_awaited()
    worker.progress.error.assert_not_awaited()
    # The render slot is still handed back.
    worker.release.assert_awaited_once()
    worker.wake.assert_awaited_once()


async def test_stream_cancel_without_the_cancel_flag_is_still_a_failure(worker):
    worker.process_task.side_effect = StreamSourceError("Stream download cancelled")
    ctx = _ctx(cancel_flag=False)

    with pytest.raises(StreamSourceError):
        await _run(ctx)

    _assert_reported_as_failure(worker, ctx, StreamSourceError)


async def test_unreadable_cancel_flag_is_still_a_failure(worker):
    worker.process_task.side_effect = StreamSourceError("Stream download cancelled")
    ctx = _ctx(cancel_flag=True)
    ctx["redis"].get = AsyncMock(side_effect=ConnectionError("redis down"))

    with pytest.raises(StreamSourceError):
        await _run(ctx)

    _assert_reported_as_failure(worker, ctx, StreamSourceError)


@pytest.mark.parametrize(
    "message",
    [
        "Stream download timed out",
        "Stream download exceeded the 2000000000 byte job limit",
        TIMEOUT_MESSAGE,
        FETCH_FAILED_MESSAGE,
    ],
)
async def test_other_stream_errors_are_failures_even_when_cancel_was_requested(worker, message):
    worker.process_task.side_effect = StreamSourceError(message)
    ctx = _ctx(cancel_flag=True)

    with pytest.raises(StreamSourceError):
        await _run(ctx)

    _assert_reported_as_failure(worker, ctx, StreamSourceError)


async def test_same_message_from_another_error_type_is_still_a_failure(worker):
    worker.process_task.side_effect = RuntimeError("Stream download cancelled")
    ctx = _ctx(cancel_flag=True)

    with pytest.raises(RuntimeError):
        await _run(ctx)

    _assert_reported_as_failure(worker, ctx, RuntimeError)
