"""A rendered clip is uploaded with no transaction open, then saved in a short one.

The worker's connections here are killed after one second idle in a transaction,
and the upload takes longer than that. Uploading inside the task's transaction
lost the connection, held the task row against a cancel, and left the uploaded
object behind when the save rolled back.
"""

import asyncio
import threading
import time
from pathlib import PurePosixPath
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import set_config_override
from src.media_storage import MediaStorage, is_s3
from src.repositories.clip_repository import ClipRepository
from src.repositories.task_repository import TaskRepository
from tests.integration.test_clip_edit_atomicity import config_for, make_task
from tests.integration.test_processing_cancellation import setup_worker

WORKER = "upload-tx-worker"
IDLE_TIMEOUT_MS = 1000
UPLOAD_SECONDS = 2.5


@pytest.fixture()
async def worker_sessions(test_database_url):
    engine = create_async_engine(test_database_url, poolclass=NullPool, connect_args={"server_settings": {
        "application_name": WORKER,
        "idle_in_transaction_session_timeout": str(IDLE_TIMEOUT_MS),
    }})
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


class FakeBucket:
    """Object storage whose clip upload takes ``delay`` seconds."""

    def __init__(self):
        self.delay = UPLOAD_SECONDS
        self.started = threading.Event()
        self.uploads = []

    def persist_clip(self, path, task_id):
        if is_s3(path):
            return path
        self.started.set()
        time.sleep(self.delay)
        reference = f"s3://test-bucket/clips/{task_id}/{uuid4().hex}/{PurePosixPath(path).name}"
        self.uploads.append(reference)
        return reference


@pytest.fixture()
def bucket(monkeypatch, tmp_path):
    set_config_override(config_for(tmp_path))
    fake = FakeBucket()
    monkeypatch.setattr(MediaStorage, "persist_clip", lambda _storage, path, task_id: fake.persist_clip(path, task_id))
    yield fake
    set_config_override(None)


async def worker_transactions(db):
    return (await db.execute(text(
        "SELECT count(*) FROM pg_stat_activity WHERE application_name = :name AND state LIKE 'idle in transaction%'"
    ), {"name": WORKER})).scalar()


async def queued_deletions(db, *paths):
    rows = await db.execute(text(
        "SELECT file_path FROM media_deletions WHERE file_path = ANY(CAST(:paths AS text[])) AND hold_token IS NULL"
    ), {"paths": list(paths)})
    return {row.file_path for row in rows}


async def wait_for_upload(bucket):
    assert await asyncio.to_thread(bucket.started.wait, 5), "the clip was never uploaded"


@pytest.mark.asyncio
async def test_slow_upload_saves_clip_with_no_transaction_open(
    db_session, initialized_database, worker_sessions, bucket, tmp_path
):
    uploads = bucket.uploads
    task_id, _, original = await make_task(db_session, tmp_path, count=0, status="queued")
    observer_sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    async with worker_sessions() as worker, observer_sessions() as observer:
        service, clip_path, _ = setup_worker(worker, tmp_path, original)
        job = asyncio.create_task(service.process_task(task_id, "upload://input.mp4", "upload"))
        await wait_for_upload(bucket)
        open_during_upload = []
        while not uploads and not job.done():
            open_during_upload.append(await worker_transactions(observer))
            await observer.rollback()
            await asyncio.sleep(0.1)
        result = await asyncio.wait_for(job, 10)

    assert open_during_upload and not any(open_during_upload)
    assert result["clips_count"] == 1
    current = await TaskRepository.get_task_by_id(db_session, task_id)
    assert current["status"] == "completed"
    [clip] = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert clip["file_path"] == uploads[0]
    # The local render is removed once the bucket copy is saved; the upload is kept.
    assert await queued_deletions(db_session, str(clip_path), uploads[0]) == {str(clip_path)}


@pytest.mark.asyncio
async def test_cancel_during_upload_is_not_blocked_and_queues_the_upload(
    db_session, initialized_database, worker_sessions, bucket, tmp_path
):
    uploads = bucket.uploads
    task_id, _, original = await make_task(db_session, tmp_path, count=0, status="queued")
    api_sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    async with worker_sessions() as worker, api_sessions() as api:
        service, clip_path, _ = setup_worker(worker, tmp_path, original)
        job = asyncio.create_task(service.process_task(task_id, "upload://input.mp4", "upload"))
        await wait_for_upload(bucket)
        # The cancel route's status write must not wait for the upload to finish.
        cancel_started = time.monotonic()
        await api.execute(text("SET LOCAL lock_timeout = '300ms'"))
        await TaskRepository.update_task_status(api, task_id, "cancelled")
        assert time.monotonic() - cancel_started < 1
        assert not uploads, "the cancel waited for the upload"
        # The save re-reads the task under its lock and sees the cancel.
        with pytest.raises(Exception, match="Task cancelled"):
            await asyncio.wait_for(job, 10)

    current = await TaskRepository.get_task_by_id(db_session, task_id)
    assert current["status"] == "cancelled"
    assert await ClipRepository.get_clips_by_task(db_session, task_id) == []
    assert await queued_deletions(db_session, *uploads) == set(uploads)
    assert not clip_path.exists()


@pytest.mark.asyncio
async def test_rolled_back_save_queues_the_upload_for_cleanup(
    db_session, worker_sessions, bucket, tmp_path
):
    # A quick upload: the rollback, not the timeout, is under test.
    bucket.delay = 0
    uploads = bucket.uploads
    task_id, _, original = await make_task(db_session, tmp_path, count=0, status="queued")
    async with worker_sessions() as worker:
        service, clip_path, _ = setup_worker(worker, tmp_path, original)
        create_clip = service.clip_repo.create_clip

        async def insert_then_fail(*args, **kwargs):
            await create_clip(*args, **kwargs)
            raise RuntimeError("save failed")

        service.clip_repo.create_clip = insert_then_fail
        with pytest.raises(RuntimeError, match="save failed"):
            await asyncio.wait_for(service.process_task(task_id, "upload://input.mp4", "upload"), 10)

    current = await TaskRepository.get_task_by_id(db_session, task_id)
    assert current["status"] == "error"
    assert await ClipRepository.get_clips_by_task(db_session, task_id) == []
    assert len(uploads) == 1
    assert await queued_deletions(db_session, *uploads) == set(uploads)
    assert not clip_path.exists()
