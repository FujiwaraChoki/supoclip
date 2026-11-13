"""The video function, not arq, decides what happens when a job's tries run out."""

import pytest

from src.workers import tasks
from src.workers.video_job_recovery import VIDEO_JOB_MAX_TRIES


def test_arq_calls_the_video_function_once_past_its_try_budget():
    video = next(
        f for f in tasks.WorkerSettings.functions if getattr(f, "name", None) == "process_video_task"
    )
    assert video.max_tries == VIDEO_JOB_MAX_TRIES + 1
    # The other functions keep the worker default.
    assert tasks.WorkerSettings.max_tries == VIDEO_JOB_MAX_TRIES


@pytest.mark.asyncio
async def test_the_exhausted_pickup_reconciles_instead_of_rendering(monkeypatch):
    calls = []

    async def reconcile(ctx, *, task_id, user_id):
        calls.append((ctx["job_try"], task_id, user_id))
        return {"task_id": task_id, "status": "error", "reconciled": True}

    async def must_not_admit(*args, **kwargs):
        raise AssertionError("an exhausted job must not be admitted or rendered")

    monkeypatch.setattr(tasks, "fail_exhausted_video_job", reconcile)
    monkeypatch.setattr("src.workers.video_admission.admit_video_job", must_not_admit)

    ctx = {"redis": object(), "job_id": "task-1:abc", "job_try": VIDEO_JOB_MAX_TRIES + 1}
    result = await tasks.process_video_task(ctx, "task-1", "https://youtu.be/x", "youtube", "user-1")

    assert result["reconciled"] is True
    assert calls == [(VIDEO_JOB_MAX_TRIES + 1, "task-1", "user-1")]
