"""Per-account video slots stay off the worker until a slot is free."""

import os
import secrets
import time
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
    wake_next_video_jobs,
)


class _Settings:
    video_job_user_concurrency = 1
    video_job_defer_seconds = 20


def _redis():
    return redis_asyncio.Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        decode_responses=True,
    )


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
    monkeypatch.setattr(video_admission, "queued_video_tasks", AsyncMock(return_value=[]))
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
    monkeypatch.setattr(video_admission, "queued_video_tasks", AsyncMock(return_value=[]))
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


class _Account:
    """One account's tasks in submission order, with their jobs on a real Redis.

    Task rows live here instead of PostgreSQL; slots, cancel flags, upload
    waits, ARQ job keys and the queue are real Redis keys, as in production.
    """

    def __init__(self, monkeypatch, name, count, *, limit=1):
        self.client = _redis()
        self.user = f"fifo-{name}-{secrets.token_hex(4)}"
        self.queue = f"test:fifo-queue:{self.user}"
        self.order = []
        self.status = {}
        self.job = {}
        self.keys = {video_admission.ACTIVE_KEY.format(user_id=self.user), self.queue}
        self.enqueued = []
        self._count = count
        settings = type(
            "S",
            (),
            {
                "video_job_user_concurrency": limit,
                "video_job_defer_seconds": 20,
                "upload_copy_wait_seconds": 1800,
            },
        )

        async def get_task(_db, task_id):
            return {"status": self.status[task_id]}

        async def queued(_db, _user):
            return [(t, self.job[t]) for t in self.order if self.status[t] == "queued"]

        async def active(_db, _user):
            return {t for t in self.status if self.status[t] in {"queued", "processing"}}

        async def save(_db, task_id, metadata):
            self.job[task_id] = metadata["processing_job_id"]

        async def enqueue(_name, *_args, **kwargs):
            # Mirror ARQ: a job key plus a queue entry due later.
            job_id = kwargs["_job_id"]
            self.enqueued.append(job_id)
            await self._put_job(job_id, kwargs["_defer_by"])
            return job_id

        monkeypatch.setattr(video_admission.TaskRepository, "get_task_by_id", get_task)
        monkeypatch.setattr(video_admission.TaskRepository, "save_source_metadata", save)
        monkeypatch.setattr(video_admission, "queued_video_tasks", queued)
        monkeypatch.setattr(video_admission, "active_task_ids", active)
        monkeypatch.setattr(video_admission, "get_config", lambda: settings())
        monkeypatch.setattr(video_admission, "DEFAULT_QUEUE_NAME", self.queue)
        monkeypatch.setattr(JobQueue, "enqueue_job", enqueue)

    async def _put_job(self, job_id, defer_seconds):
        key = f"arq:job:{job_id}"
        self.keys.add(key)
        await self.client.set(key, "x")
        due = int((time.time() + defer_seconds) * 1000)
        await self.client.zadd(self.queue, {job_id: due})

    async def add_task(self):
        task_id = f"{self.user}-t{len(self.order) + 1}"
        self.order.append(task_id)
        self.status[task_id] = "queued"
        self.job[task_id] = f"{task_id}:job0"
        await self._put_job(self.job[task_id], 20)
        return task_id

    async def setup(self):
        for _ in range(self._count):
            await self.add_task()
        return self

    async def admit(self, task_id, url=None):
        """One run of the task's job, as the worker's admission sees it."""
        decision = await admit_video_job(
            {"redis": self.client}, db=object(), task_id=task_id,
            user_id=self.user, job_args=(task_id,), url=url,
        )
        if decision == "run":
            self.status[task_id] = "processing"
        return decision

    async def finish(self, task_id):
        self.status[task_id] = "completed"
        await release_video_slot(self.client, self.user, task_id)

    async def flag(self, template, task_id, value="1"):
        key = template.format(task_id=task_id)
        self.keys.add(key)
        await self.client.set(key, value)

    async def cleanup(self):
        await self.client.delete(*self.keys)
        await self.client.aclose()


async def _account(monkeypatch, name, count, **kwargs):
    return await _Account(monkeypatch, name, count, **kwargs).setup()


def _upload_pending(monkeypatch, pending: bool):
    async def upload_copy_pending(_url):
        return pending

    monkeypatch.setattr("src.media.upload_ingest.upload_copy_pending", upload_copy_pending)


@pytest.mark.asyncio
async def test_renders_start_in_submission_order(monkeypatch):
    account = await _account(monkeypatch, "order", 3)
    t1, t2, t3 = account.order
    try:
        # Newer jobs that poll first wait for the oldest.
        assert await account.admit(t3) == "deferred"
        assert await account.admit(t2) == "deferred"
        assert await account.admit(t1) == "run"
        # While it renders, nobody else gets in.
        assert await account.admit(t3) == "deferred"
        assert await account.admit(t2) == "deferred"
        # When it ends, the next oldest wins even if a newer one polls first.
        await account.finish(t1)
        assert await account.admit(t3) == "deferred"
        assert await account.admit(t2) == "run"
        await account.finish(t2)
        assert await account.admit(t3) == "run"
        # Every deferral queued exactly one later attempt and recorded it.
        assert len(account.enqueued) == 5
        assert account.job[t3] == account.enqueued[-1]
    finally:
        await account.cleanup()


@pytest.mark.asyncio
async def test_an_older_task_waiting_for_its_upload_does_not_block_newer_ones(monkeypatch):
    account = await _account(monkeypatch, "upload", 2)
    t1, t2 = account.order
    _upload_pending(monkeypatch, True)
    try:
        await account.flag(video_admission.UPLOAD_WAIT_KEY, t1, str(time.time()))
        assert await account.admit(t2) == "run"
        # Its own attempt keeps waiting for the copy and keeps the wait marker.
        assert await account.admit(t1, url="upload://local/v.mp4") == "deferred"
        assert await account.client.exists(video_admission.UPLOAD_WAIT_KEY.format(task_id=t1))
    finally:
        await account.cleanup()


@pytest.mark.asyncio
async def test_an_upload_that_arrives_puts_its_task_back_ahead_of_newer_ones(monkeypatch):
    account = await _account(monkeypatch, "upload-back", 3)
    t1, t2, t3 = account.order
    _upload_pending(monkeypatch, False)
    try:
        await account.flag(video_admission.UPLOAD_WAIT_KEY, t1, str(time.time()))
        assert await account.admit(t2) == "run"
        # The older task's attempt sees its copy arrived and clears the wait...
        assert await account.admit(t1, url="upload://local/v.mp4") == "deferred"
        assert not await account.client.exists(video_admission.UPLOAD_WAIT_KEY.format(task_id=t1))
        # ...so when the slot frees, the newer task waits for it.
        await account.finish(t2)
        assert await account.admit(t3) == "deferred"
        assert await account.admit(t1) == "run"
    finally:
        await account.cleanup()


@pytest.mark.asyncio
async def test_cancelled_or_jobless_older_tasks_are_skipped(monkeypatch):
    account = await _account(monkeypatch, "cancel", 4)
    t1, t2, t3, t4 = account.order
    try:
        account.status[t1] = "cancelled"  # left the queue
        await account.flag("task_cancel:{task_id}", t2)  # cancel requested, still queued
        await account.client.delete(f"arq:job:{account.job[t3]}")  # its job is gone
        assert await account.admit(t2) == "skipped"
        assert await account.admit(t4) == "run"
    finally:
        await account.cleanup()


@pytest.mark.asyncio
async def test_with_a_limit_of_two_the_oldest_two_start(monkeypatch):
    account = await _account(monkeypatch, "limit2", 3, limit=2)
    t1, t2, t3 = account.order
    try:
        assert await account.admit(t3) == "deferred"
        assert await account.admit(t2) == "run"
        # One slot left and t1 is ahead of t3.
        assert await account.admit(t3) == "deferred"
        assert await account.admit(t1) == "run"
        assert await account.admit(t3) == "deferred"
        await account.finish(t2)
        assert await account.admit(t3) == "run"
    finally:
        await account.cleanup()


@pytest.mark.asyncio
async def test_a_retry_of_a_started_render_is_not_queued_behind_older_tasks(monkeypatch):
    account = await _account(monkeypatch, "retry", 2)
    _t1, t2 = account.order
    try:
        account.status[t2] = "processing"
        assert await account.admit(t2) == "run"
    finally:
        await account.cleanup()


@pytest.mark.asyncio
async def test_a_freed_slot_wakes_the_next_task_in_line(monkeypatch):
    account = await _account(monkeypatch, "wake", 3)
    t1, t2, t3 = account.order
    client = account.client
    try:
        assert await account.admit(t1) == "run"
        due_before = await client.zscore(account.queue, account.job[t2])

        # While the slot is busy nothing moves.
        assert await wake_next_video_jobs(object(), client, account.user) == []

        await account.finish(t1)
        assert await wake_next_video_jobs(object(), client, account.user) == [t2]
        now_ms = time.time() * 1000
        assert await client.zscore(account.queue, account.job[t2]) <= now_ms < due_before
        # Only the next task moves, through its existing job: nothing new is queued.
        assert await client.zscore(account.queue, account.job[t3]) > now_ms
        assert account.enqueued == []
        assert await client.zcard(account.queue) == 3

        # A second wake for the same slot changes nothing.
        assert await wake_next_video_jobs(object(), client, account.user) == []

        # A job that already left the queue is never put back.
        await client.zrem(account.queue, account.job[t2])
        assert await wake_next_video_jobs(object(), client, account.user) == []
        assert await client.zscore(account.queue, account.job[t2]) is None
    finally:
        await account.cleanup()


@pytest.mark.asyncio
async def test_an_upload_hold_hands_its_slot_to_the_next_task(monkeypatch):
    account = await _account(monkeypatch, "upload-wake", 2)
    t1, t2 = account.order
    _upload_pending(monkeypatch, True)
    try:
        account.keys.add(video_admission.UPLOAD_WAIT_KEY.format(task_id=t1))
        assert await account.admit(t1) == "run"
        decision = await video_admission.hold_for_upload(
            {"redis": account.client}, db=object(), task_id=t1, user_id=account.user,
            url="upload://local/v.mp4", job_args=(t1,),
        )
        assert decision == "deferred"
        assert await account.client.zscore(account.queue, account.job[t2]) <= time.time() * 1000
        assert await account.admit(t2) == "run"
    finally:
        await account.cleanup()
