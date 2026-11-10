"""Per-account video slots stay off the worker until a slot is free."""

from unittest.mock import AsyncMock

import pytest
import redis.asyncio as redis_asyncio

from src.workers.job_queue import DEFAULT_QUEUE_NAME, JobQueue
from src.workers import video_admission
from src.workers.video_admission import (
    acquire_video_slot,
    admit_video_job,
    prune_video_slots,
    release_video_slot,
)


class _Settings:
    video_job_user_concurrency = 1
    video_job_defer_seconds = 20


def _redis():
    return redis_asyncio.Redis(host="127.0.0.1", port=6379, decode_responses=True)


@pytest.mark.asyncio
async def test_slot_limit_is_per_account_and_a_retry_keeps_its_slot():
    client = _redis()
    user_a = "slot-account-a"
    user_b = "slot-account-b"
    try:
        assert await acquire_video_slot(client, user_a, "task-1", 1) is True
        assert await acquire_video_slot(client, user_a, "task-1", 1) is True
        assert await acquire_video_slot(client, user_a, "task-2", 1) is False
        assert await acquire_video_slot(client, user_b, "task-3", 1) is True
        await release_video_slot(client, user_a, "task-1")
        assert await acquire_video_slot(client, user_a, "task-2", 1) is True
    finally:
        await client.delete(
            video_admission.ACTIVE_KEY.format(user_id=user_a),
            video_admission.ACTIVE_KEY.format(user_id=user_b),
        )
        await client.aclose()


@pytest.mark.asyncio
async def test_prune_drops_finished_tasks_and_keeps_queued_ones():
    client = _redis()
    user = "slot-account-prune"
    key = video_admission.ACTIVE_KEY.format(user_id=user)
    try:
        await client.sadd(key, "live", "finished")
        await prune_video_slots(client, user, {"live"})
        assert await client.smembers(key) == {"live"}
    finally:
        await client.delete(key)
        await client.aclose()


class _Redis:
    def __init__(self, cancel=None):
        self.cancel = cancel

    async def get(self, _key):
        return self.cancel

    async def smembers(self, _key):
        return set()

    async def srem(self, _key, *_members):
        return 0


async def _admit(monkeypatch, *, status="queued", cancel=None, acquired=True, acquire_error=None):
    saved = []
    enqueued = []

    async def get_task(_db, _task_id):
        return {"status": status}

    async def save(_db, _task_id, metadata):
        saved.append(metadata)

    async def enqueue(_name, *_args, **kwargs):
        enqueued.append(kwargs)
        return kwargs["_job_id"]

    async def acquire(*_args, **_kwargs):
        if acquire_error:
            raise acquire_error
        return acquired

    monkeypatch.setattr(video_admission.TaskRepository, "get_task_by_id", get_task)
    monkeypatch.setattr(video_admission.TaskRepository, "save_source_metadata", save)
    monkeypatch.setattr(video_admission, "active_task_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(video_admission, "acquire_video_slot", acquire)
    monkeypatch.setattr(video_admission, "get_config", lambda: _Settings())
    monkeypatch.setattr(JobQueue, "enqueue_job", enqueue)
    decision = await admit_video_job(
        {"redis": _Redis(cancel)},
        db=object(),
        task_id="task-9",
        user_id="account-9",
        job_args=("task-9", "https://example.test/v"),
    )
    return decision, enqueued, saved


@pytest.mark.asyncio
async def test_a_second_video_is_requeued_without_running(monkeypatch):
    decision, enqueued, saved = await _admit(monkeypatch, acquired=False)
    assert decision == "deferred"
    assert len(enqueued) == 1
    assert enqueued[0]["_defer_by"] == 20
    assert enqueued[0]["_queue_name"] == DEFAULT_QUEUE_NAME
    assert enqueued[0]["_job_id"].startswith("task-9:")
    assert saved == [{"processing_job_id": enqueued[0]["_job_id"]}]


@pytest.mark.asyncio
async def test_cancelled_and_finished_tasks_are_not_requeued(monkeypatch):
    decision, enqueued, _saved = await _admit(monkeypatch, status="cancelled")
    assert decision == "skipped"
    assert enqueued == []
    decision, enqueued, _saved = await _admit(monkeypatch, cancel=b"1")
    assert decision == "skipped"
    assert enqueued == []


@pytest.mark.asyncio
async def test_a_failed_save_removes_the_extra_job(monkeypatch):
    deleted = []
    enqueued = []

    class _Pool:
        async def delete(self, key):
            deleted.append(("delete", key))

        async def zrem(self, queue, job_id):
            deleted.append(("zrem", queue, job_id))

    async def save(*_args, **_kwargs):
        raise RuntimeError("database down")

    async def enqueue(_name, *_args, **kwargs):
        enqueued.append(kwargs["_job_id"])
        return kwargs["_job_id"]

    async def get_pool():
        return _Pool()

    monkeypatch.setattr(video_admission.TaskRepository, "get_task_by_id", AsyncMock(return_value={"status": "queued"}))
    monkeypatch.setattr(video_admission.TaskRepository, "save_source_metadata", save)
    monkeypatch.setattr(video_admission, "active_task_ids", AsyncMock(return_value=set()))
    monkeypatch.setattr(video_admission, "acquire_video_slot", AsyncMock(return_value=False))
    monkeypatch.setattr(video_admission, "get_config", lambda: _Settings())
    monkeypatch.setattr(JobQueue, "enqueue_job", enqueue)
    monkeypatch.setattr(JobQueue, "get_pool", get_pool)

    with pytest.raises(RuntimeError, match="database down"):
        await admit_video_job(
            {"redis": _Redis()},
            db=object(),
            task_id="task-9",
            user_id="account-9",
            job_args=("task-9",),
        )
    assert enqueued
    assert ("delete", f"arq:job:{enqueued[0]}") in deleted
    assert ("zrem", DEFAULT_QUEUE_NAME, enqueued[0]) in deleted


@pytest.mark.asyncio
async def test_a_redis_error_runs_the_job_instead_of_dropping_it(monkeypatch):
    decision, enqueued, _saved = await _admit(
        monkeypatch, acquire_error=RuntimeError("redis down")
    )
    assert decision == "run"
    assert enqueued == []
