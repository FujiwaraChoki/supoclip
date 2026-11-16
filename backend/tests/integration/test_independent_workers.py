"""A worker on another machine shares PostgreSQL, Redis and the bucket, not the disk.

"Another machine" here is a second ``TEMP_DIR``: it gets its own volume id,
its own files, and the same database and object storage.
"""

from contextlib import contextmanager
import copy
import hashlib
from pathlib import Path
import time
from unittest.mock import AsyncMock
import uuid

import pytest
import redis.asyncio as redis_asyncio
from sqlalchemy import text

from src.config import get_config, set_config_override
from src.local_volume import volume_id
from src.media.upload_ingest import (
    materialize_stored_upload,
    persist_pending_uploads,
    upload_copy_pending,
)
from src.media_storage import MediaStorage
from src.repositories.media_repository import queue_delete
from src.repositories.task_repository import TaskRepository
from src.repositories.upload_repository import claim_stored_upload, get_stored_upload
from src.workers import video_admission
from src.workers.media_cleanup import (
    copy_pending_uploads,
    drain_deletions,
    forget_lost_volumes,
    maintain_local_scratch,
)
from tests.fixtures.factories import create_source, create_task, create_user
from tests.integration.test_media_deletion_transactions import media_backend  # noqa: F401


@contextmanager
def on_other_machine(storage: MediaStorage, tmp_path: Path):
    """Run as a process whose TEMP_DIR is a different disk."""
    here = get_config()
    there = copy.copy(here)
    there.temp_dir = str(tmp_path / "render-host")
    Path(there.temp_dir).mkdir(exist_ok=True)
    set_config_override(there)
    try:
        yield MediaStorage(there, storage.client)
    finally:
        set_config_override(here)


async def pending(db, path: str):
    row = (await db.execute(
        text("SELECT local_volume FROM media_deletions WHERE file_path = :path"), {"path": path}
    )).first()
    await db.rollback()
    return row


async def test_a_queued_local_deletion_is_carried_out_only_on_the_disk_it_names(db_session, media_backend, tmp_path):
    storage = media_backend
    # Scratch directories have the same path on every machine.
    relative = Path("editor") / ("a" * 64) / "clean.mp4"
    here = Path(storage.config.temp_dir) / relative
    here.parent.mkdir(parents=True)
    here.write_bytes(b"api host copy")
    await queue_delete(db_session, str(here))
    await db_session.commit()
    assert (await pending(db_session, str(here))).local_volume == volume_id(storage.config.temp_dir)

    with on_other_machine(storage, tmp_path) as remote:
        there = Path(remote.config.temp_dir) / relative
        there.parent.mkdir(parents=True)
        there.write_bytes(b"render host copy")
        # Neither the render host's own pass nor a scheduler running there touches it.
        await drain_deletions(db_session, remote, shared=False)
        await drain_deletions(db_session, remote)
        assert await pending(db_session, str(here)) is not None
        assert here.exists() and there.exists()

    await drain_deletions(db_session, storage, shared=False)
    assert not here.exists()
    assert await pending(db_session, str(here)) is None


async def test_object_storage_and_older_rows_are_left_to_the_scheduler(db_session, media_backend):
    storage = media_backend
    key = storage.key("clips", "task", uuid.uuid4().hex, "clip.mp4")
    storage.client.objects[key] = b"clip"
    reference = storage.reference(key)
    legacy = Path(storage.config.temp_dir) / "clips" / "queued-before-upgrade.mp4"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes(b"old")
    await queue_delete(db_session, reference)
    await db_session.execute(text(
        "INSERT INTO media_deletions (file_path, is_prefix) VALUES (:path, FALSE)"), {"path": str(legacy)})
    await db_session.commit()
    assert (await pending(db_session, reference)).local_volume is None

    # A video worker only collects what was queued for its own disk.
    await maintain_local_scratch()
    assert key in storage.client.objects and legacy.exists()
    await drain_deletions(db_session, storage)
    assert key not in storage.client.objects and not legacy.exists()


async def test_deletions_for_a_disk_that_never_returns_are_dropped_after_a_month(db_session, media_backend):
    storage = media_backend
    mine = volume_id(storage.config.temp_dir)
    rows = [("/gone/recent.mp4", "f" * 32, 29), ("/gone/old.mp4", "f" * 32, 31),
            ("/mine/old.mp4", mine, 31), ("s3://private-media/media/clips/old.mp4", None, 31)]
    for path, volume, days in rows:
        await db_session.execute(text("""
            INSERT INTO media_deletions (file_path, is_prefix, local_volume, created_at, next_attempt_at)
            VALUES (:path, FALSE, :volume, NOW() - make_interval(days => :days), NOW() + INTERVAL '1 day')
        """), {"path": path, "volume": volume, "days": days})
    await db_session.commit()
    await forget_lost_volumes(db_session, storage)
    left = set((await db_session.execute(text("SELECT file_path FROM media_deletions"))).scalars())
    assert left == {"/gone/recent.mp4", "/mine/old.mp4", "s3://private-media/media/clips/old.mp4"}


async def stored_upload(db, storage, body=b"uploaded video bytes"):
    user = await create_user(db)
    name = f"{uuid.uuid4()}.mp4"
    path = Path(storage.config.temp_dir) / "uploads" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    video_path, created = await claim_stored_upload(
        db, user["id"], len(body), hashlib.sha256(body).hexdigest(), f"upload://{name}", None, storage.config
    )
    assert created
    return user, video_path, path


async def test_an_upload_waits_for_its_copy_on_another_machine_and_is_readable_there_afterwards(
    db_session, media_backend, tmp_path
):
    storage = media_backend
    _user, video_path, path = await stored_upload(db_session, storage)
    row = await get_stored_upload(db_session, video_path)
    await db_session.rollback()
    assert row.staging_volume == volume_id(storage.config.temp_dir) and row.storage_reference is None

    # The API's background copy never ran (restart, storage error).
    assert await upload_copy_pending(video_path) is False  # the staging machine reads its own file
    with on_other_machine(storage, tmp_path):
        assert await upload_copy_pending(video_path) is True
        # Nothing on this disk to copy: the recovery job is a no-op here.
        assert await persist_pending_uploads() == 0
        assert await upload_copy_pending(video_path) is True

    assert await copy_pending_uploads({}) == {"copied": 1}
    assert await copy_pending_uploads({}) == {"copied": 0}
    row = await get_stored_upload(db_session, video_path)
    await db_session.rollback()
    assert row.storage_reference and storage.client.objects[storage.parse(row.storage_reference)] == path.read_bytes()

    with on_other_machine(storage, tmp_path):
        assert await upload_copy_pending(video_path) is False
        fetched = await materialize_stored_upload(video_path)
        assert fetched.read_bytes() == b"uploaded video bytes"
        assert Path(get_config().temp_dir) in fetched.parents


async def test_a_lost_upload_fails_instead_of_waiting_forever(db_session, media_backend, tmp_path):
    storage = media_backend
    _user, video_path, path = await stored_upload(db_session, storage)
    # The staging machine lost the file before copying it: nothing will ever arrive.
    path.unlink()
    assert await upload_copy_pending(video_path) is False
    assert await persist_pending_uploads() == 0


@pytest.fixture
async def redis():
    client = redis_asyncio.Redis(host="127.0.0.1", port=6379, decode_responses=True)
    yield client
    await client.aclose()


async def test_video_job_is_deferred_until_its_upload_is_in_object_storage(
    db_session, media_backend, tmp_path, redis, monkeypatch
):
    storage = media_backend
    user, video_path, _path = await stored_upload(db_session, storage)
    source = await create_source(db_session, source_type="video_url", url=video_path)
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="queued")
    enqueue = AsyncMock(return_value="job")
    monkeypatch.setattr(video_admission.JobQueue, "enqueue_job", enqueue)
    job_args = (task["id"], video_path, "video_url", user["id"])
    slot = video_admission.ACTIVE_KEY.format(user_id=user["id"])
    wait_key = video_admission.UPLOAD_WAIT_KEY.format(task_id=task["id"])
    hold = dict(task_id=task["id"], user_id=user["id"], url=video_path, job_args=job_args)
    try:
        # On the machine that has the file the job runs straight away.
        assert await video_admission.hold_for_upload({"redis": redis}, db_session, **hold) == "run"
        enqueue.assert_not_awaited()

        with on_other_machine(storage, tmp_path):
            await video_admission.acquire_video_slot(redis, user["id"], task["id"], 1)
            assert await video_admission.hold_for_upload({"redis": redis}, db_session, **hold) == "deferred"
            # One later attempt is queued, and the account's render slot is free meanwhile.
            call = enqueue.await_args
            assert call.args == ("process_video_task", *job_args)
            assert call.kwargs["_defer_by"] == get_config().video_job_defer_seconds
            assert await redis.smembers(slot) == set()
            metadata = await TaskRepository.load_source_metadata(db_session, task["id"])
            assert metadata["processing_job_id"] == call.kwargs["_job_id"]
            assert (await TaskRepository.get_task_by_id(db_session, task["id"]))["status"] == "queued"

        await persist_pending_uploads()
        with on_other_machine(storage, tmp_path):
            assert await video_admission.hold_for_upload({"redis": redis}, db_session, **hold) == "run"
            assert enqueue.await_count == 1
            # YouTube and direct-to-storage uploads are never held.
            for url in ("https://www.youtube.com/watch?v=abcdefghijk", "upload://s3/owner/id/video.mp4"):
                assert await video_admission.hold_for_upload(
                    {"redis": redis}, db_session, **{**hold, "url": url}) == "run"
    finally:
        await redis.delete(slot, wait_key)


async def test_a_copy_that_never_arrives_fails_the_task(db_session, media_backend, tmp_path, redis, monkeypatch):
    storage = media_backend
    user, video_path, _path = await stored_upload(db_session, storage)
    source = await create_source(db_session, source_type="video_url", url=video_path)
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="queued")
    enqueue = AsyncMock(return_value="job")
    monkeypatch.setattr(video_admission.JobQueue, "enqueue_job", enqueue)
    wait_key = video_admission.UPLOAD_WAIT_KEY.format(task_id=task["id"])
    hold = dict(task_id=task["id"], user_id=user["id"], url=video_path,
                job_args=(task["id"], video_path, "video_url", user["id"]))
    try:
        with on_other_machine(storage, tmp_path):
            assert await video_admission.hold_for_upload({"redis": redis}, db_session, **hold) == "deferred"
            # The wait is measured from the first attempt, not from each retry.
            await redis.set(wait_key, str(time.time() - get_config().upload_copy_wait_seconds - 5))
            assert await video_admission.hold_for_upload({"redis": redis}, db_session, **hold) == "failed"
            assert enqueue.await_count == 1
        failed = await TaskRepository.get_task_by_id(db_session, task["id"])
        assert failed["status"] == "error"
        assert failed["progress_message"] == video_admission.UPLOAD_LOST_MESSAGE
        assert await redis.get(wait_key) is None
    finally:
        await redis.delete(wait_key, video_admission.ACTIVE_KEY.format(user_id=user["id"]))
