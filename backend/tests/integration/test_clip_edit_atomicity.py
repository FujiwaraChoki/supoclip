import asyncio
import threading
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.config import Config
from src.repositories.clip_repository import ClipRepository
from src.repositories.edit_transaction import task_edit_transaction
from src.services.task_service import TaskService
from tests.fixtures.factories import create_source, create_task, create_user


async def make_task(db, tmp_path, count=1, status="completed"):
    owner = await create_user(db)
    source = await create_source(db, source_type="video_url", url="upload://input.mp4")
    task = await create_task(db, user_id=owner["id"], source_id=source["id"], status=status)
    original = tmp_path / "input.mp4"
    original.touch()
    ids = []
    for order in range(1, count + 1):
        ids.append(await ClipRepository.create_clip(
            db, task["id"], original.name, str(original), "00:00", "00:10", 10,
            "caption", 1, "regression", order,
        ))
    await ClipRepository.reorder_task_clips(db, task["id"])
    return task["id"], ids, original


def config_for(tmp_path):
    config = Config()
    config.temp_dir = str(tmp_path)
    return config


def fake_render(tmp_path):
    def render(*_args):
        folder = tmp_path / str(uuid4())
        folder.mkdir()
        paths = folder / "first.mp4", folder / "second.mp4"
        for path in paths:
            path.touch()
        return paths
    return render


@pytest.mark.asyncio
@pytest.mark.parametrize("other_edit", ["split", "trim", "delete", "merge", "regenerate", "captions"])
async def test_edits_serialize_before_reading_clip_state(
    db_session, initialized_database, tmp_path, monkeypatch, other_edit
):
    task_id, ids, original = await make_task(db_session, tmp_path, count=3)
    await db_session.rollback()
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    entered = threading.Event()
    release = threading.Event()
    render = fake_render(tmp_path)
    render_count = 0

    def slow_first_split(*args):
        nonlocal render_count
        render_count += 1
        if render_count == 1:
            entered.set()
            assert release.wait(10), "Timed out releasing first render"
        return render(*args)

    monkeypatch.setattr("src.services.clip_service.split_clip_file", slow_first_split)
    monkeypatch.setattr("src.services.clip_service.trim_clip_file", lambda *_: render()[0])
    monkeypatch.setattr("src.services.clip_service.merge_clip_files", lambda *_: render()[0])
    monkeypatch.setattr("src.services.clip_service.create_optimized_clip", lambda *_a, **_k: True)
    monkeypatch.setattr("src.services.clip_service.overlay_custom_captions", lambda *_a, **_k: render()[0])
    async with sessions() as one, sessions() as two:
        first = TaskService(one, config=config_for(tmp_path))
        second = TaskService(two, config=config_for(tmp_path))
        second.video_service.resolve_local_video_path = lambda _: original
        second.cache_repo.get_cache = AsyncMock(return_value={"video_path": str(original)})
        second._load_task_source_settings = AsyncMock(return_value={"output_format": "original"})
        second.video_service.create_video_clips = AsyncMock(return_value=[{
            "filename": original.name, "path": str(original), "start_time": "00:00",
            "end_time": "00:10", "duration": 10,
        }])
        job_one = asyncio.create_task(first.split_clip(task_id, ids[-1], 3))
        assert await asyncio.to_thread(entered.wait, 5)
        actions = {
            "split": lambda: second.split_clip(task_id, ids[-1], 1),
            "trim": lambda: second.trim_clip(task_id, ids[-1], .1, .1),
            "delete": lambda: second.delete_clip(task_id, ids[-1]),
            "merge": lambda: second.merge_clips(task_id, ids[:2]),
            "regenerate": lambda: second.regenerate_all_clips_for_task(task_id, None, None, None, "default"),
            "captions": lambda: second.update_clip_captions(task_id, ids[-1], "edited", "bottom", []),
        }
        job_two = asyncio.create_task(actions[other_edit]())
        try:
            done, _ = await asyncio.wait([job_two], timeout=.15)
            assert not done, "Concurrent edit passed the task lock while first render was pending"
        finally:
            release.set()
        await asyncio.wait_for(asyncio.gather(job_one, job_two), 10)

    clips = await ClipRepository.get_clips_by_task(db_session, task_id)
    expected_counts = {"split": 5, "trim": 4, "delete": 3, "merge": 3, "regenerate": 1, "captions": 4}
    assert len(clips) == expected_counts[other_edit]
    assert [clip["clip_order"] for clip in clips] == list(range(1, len(clips) + 1))
    stored = await db_session.execute(text("SELECT generated_clips_ids FROM tasks WHERE id=:id"), {"id": task_id})
    assert list(stored.scalar()) == [clip["id"] for clip in clips]
    if other_edit == "split":
        assert [clip["id"] for clip in clips[:2]] == ids[:2]
        assert sum(clip["duration"] for clip in clips[2:]) == pytest.approx(10)


@pytest.mark.asyncio
async def test_failed_split_insert_rolls_back_original_and_orders(db_session, tmp_path, monkeypatch):
    task_id, ids, _ = await make_task(db_session, tmp_path, count=3)
    before = await ClipRepository.get_clips_by_task(db_session, task_id)
    monkeypatch.setattr("src.services.clip_service.split_clip_file", fake_render(tmp_path))

    async def fail_insert(db, **_kwargs):
        await db.execute(text("SELECT 1 / 0"))

    service = TaskService(db_session, config=config_for(tmp_path))
    service.clip_repo.create_clip = fail_insert
    with pytest.raises(DBAPIError):
        await service.split_clip(task_id, ids[0], 3)
    assert await ClipRepository.get_clips_by_task(db_session, task_id) == before
    stored = await db_session.execute(text("SELECT generated_clips_ids FROM tasks WHERE id=:id"), {"id": task_id})
    assert list(stored.scalar()) == ids


@pytest.mark.asyncio
async def test_legacy_insert_fallback_retains_outer_task_lock(db_session, initialized_database, tmp_path):
    task_id, _, original = await make_task(db_session, tmp_path)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    # DDL is rolled back at the end, restoring the current schema.
    await db_session.execute(text("ALTER TABLE generated_clips DROP COLUMN hook_title"))
    try:
        with pytest.raises(RuntimeError, match="undo legacy schema"):
            async with task_edit_transaction(db_session, task_id):
                await ClipRepository.create_clip(
                    db_session, task_id, original.name, str(original), "00:10", "00:12", 2,
                    "caption", 1, "regression", 2,
                )
                async with sessions() as contender:
                    with pytest.raises(DBAPIError) as error:
                        await contender.execute(text("SELECT id FROM tasks WHERE id=:id FOR UPDATE NOWAIT"), {"id": task_id})
                    assert error.value.orig.sqlstate == "55P03"
                raise RuntimeError("undo legacy schema")
    finally:
        await db_session.rollback()
    assert len(await ClipRepository.get_clips_by_task(db_session, task_id)) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["queued", "processing"])
async def test_active_processing_rejects_clip_edits(db_session, tmp_path, status):
    task_id, ids, _ = await make_task(db_session, tmp_path, status=status)
    with pytest.raises(ValueError, match="processing to finish"):
        await TaskService(db_session, config=config_for(tmp_path)).split_clip(task_id, ids[0], 3)
    assert len(await ClipRepository.get_clips_by_task(db_session, task_id)) == 1
