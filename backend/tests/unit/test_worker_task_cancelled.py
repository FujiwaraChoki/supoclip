"""A user's cancel is not a worker failure: no traceback, dead letter or retry."""

import logging
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from arq import Retry

from src.observability import PostHogExceptionHandler
from src.repositories.edit_transaction import TaskCancelled
from src.workers import tasks as worker_tasks


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
    state = SimpleNamespace(process_task=AsyncMock(), progress=progress)
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

    posthog = SimpleNamespace(capture_exception=lambda *a, **k: state.captured.append(a[0]))
    state.captured = []
    handler = PostHogExceptionHandler(posthog)
    root = logging.getLogger()
    root.addHandler(handler)
    yield state
    root.removeHandler(handler)


def _ctx(job_try):
    redis = SimpleNamespace(set=AsyncMock(), sadd=AsyncMock(), get=AsyncMock(return_value=None))
    return {"redis": redis, "job_try": job_try}


@pytest.mark.parametrize("error", [TaskCancelled(), Exception("Task cancelled")])
async def test_cancelled_task_is_not_reported_as_a_worker_failure(worker, error, caplog):
    worker.process_task.side_effect = error
    # Last try: a failure here would be dead-lettered.
    ctx = _ctx(job_try=worker_tasks.WorkerSettings.max_tries)

    with caplog.at_level(logging.INFO):
        result = await worker_tasks.process_video_task(ctx, "task-1", "https://x", "youtube", "user-1")

    assert result == {"task_id": "task-1", "status": "cancelled"}
    assert worker.captured == []
    assert not [r for r in caplog.records if r.exc_info or r.levelno >= logging.ERROR]
    ctx["redis"].set.assert_not_awaited()
    ctx["redis"].sadd.assert_not_awaited()
    worker.progress.error.assert_not_awaited()
    # The render slot is still handed back.
    worker.release.assert_awaited_once()
    worker.wake.assert_awaited_once()


async def test_real_failure_is_still_reported_and_dead_lettered(worker):
    worker.process_task.side_effect = RuntimeError("render exploded")
    ctx = _ctx(job_try=worker_tasks.WorkerSettings.max_tries)

    with pytest.raises(RuntimeError):
        await worker_tasks.process_video_task(ctx, "task-1", "https://x", "youtube", "user-1")

    assert [type(e) for e in worker.captured] == [RuntimeError]
    ctx["redis"].sadd.assert_awaited_once_with("tasks:dead_letter", "task-1")
    worker.progress.error.assert_awaited_once()
    worker.release.assert_awaited_once()


async def test_busy_run_guard_still_retries(worker):
    from src.repositories.task_run_guard import TaskRunBusy

    worker.process_task.side_effect = TaskRunBusy()
    with pytest.raises(Retry):
        await worker_tasks.process_video_task(_ctx(1), "task-1", "https://x", "youtube", "user-1")
