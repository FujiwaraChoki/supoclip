"""Applying task settings to existing clips (POST /tasks/{id}/settings).

Regression for a production failure: the regeneration held the task's row lock
in an open transaction for the whole download and render. PostgreSQL ended the
idle transaction after five minutes, the lock went with it, retried requests
started overlapping regenerations, and a partial render replaced every clip
with the ones that happened to finish.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.config import set_config_override
from src.repositories.clip_repository import ClipRepository
from src.repositories.edit_transaction import RegenerationBusy
from src.services.task_service import TaskService
from tests.integration.test_clip_edit_atomicity import config_for, make_task


def _service(session, tmp_path, original, render):
    service = TaskService(session, config=config_for(tmp_path))
    service.video_service.resolve_local_video_path = lambda _: original
    service.cache_repo.get_cache = AsyncMock(return_value={"video_path": str(original)})
    service._load_task_source_settings = AsyncMock(return_value={"output_format": "original"})
    service.video_service.create_video_clips = render
    return service


def _rendered(tmp_path, segments, indexes=None):
    """Clip infos as the renderer returns them: one per segment it managed to render."""
    output = []
    for index, segment in enumerate(segments):
        if indexes is not None and index not in indexes:
            continue
        path = tmp_path / "clips" / f"clip_{index + 1}_{uuid4().hex[:8]}.mp4"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"restyled")
        output.append({
            "clip_id": index + 1, "filename": path.name, "path": str(path),
            "start_time": segment["start_time"], "end_time": segment["end_time"], "duration": 10,
        })
    return output


async def _task_font_size(session, task_id):
    await session.rollback()
    return (await session.execute(
        text("SELECT font_size FROM tasks WHERE id = :id"), {"id": task_id}
    )).scalar()


@pytest.mark.asyncio
async def test_render_runs_without_an_open_transaction_or_task_lock(
    db_session, initialized_database, tmp_path
):
    task_id, ids, original = await make_task(db_session, tmp_path, count=2)
    await db_session.rollback()
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    observed = {}

    async with sessions() as session, sessions() as other:
        async def render(_video, segments, *_args, **_kwargs):
            observed["in_transaction"] = session.in_transaction()
            # Another edit can take the row lock while the clips render.
            await other.execute(
                text("SELECT id FROM tasks WHERE id = :id FOR UPDATE NOWAIT"), {"id": task_id}
            )
            await other.rollback()
            observed["lock_free"] = True
            return _rendered(tmp_path, segments)

        service = _service(session, tmp_path, original, render)
        await service.update_task_settings(task_id, None, 64, None, "default", False, True)

    assert observed == {"in_transaction": False, "lock_free": True}
    clips = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert len(clips) == 2 and not {clip["id"] for clip in clips} & set(ids)
    assert await _task_font_size(db_session, task_id) == 64


@pytest.mark.asyncio
async def test_idle_transaction_timeout_during_a_long_render_does_not_fail_the_edit(
    db_session, initialized_database, tmp_path
):
    """Production: PostgreSQL ended the edit's idle transaction mid-render."""
    task_id, ids, original = await make_task(db_session, tmp_path, count=2)
    await db_session.rollback()
    engine = create_async_engine(
        initialized_database.url,
        poolclass=NullPool,
        connect_args={"server_settings": {"idle_in_transaction_session_timeout": "500"}},
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            async def slow_render(_video, segments, *_args, **_kwargs):
                await asyncio.sleep(1.2)
                return _rendered(tmp_path, segments)

            service = _service(session, tmp_path, original, slow_render)
            await service.update_task_settings(task_id, None, 72, None, "default", False, True)
    finally:
        await engine.dispose()

    clips = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert len(clips) == 2 and not {clip["id"] for clip in clips} & set(ids)
    assert await _task_font_size(db_session, task_id) == 72


@pytest.mark.asyncio
async def test_overlapping_regeneration_of_one_task_is_rejected(
    db_session, initialized_database, tmp_path
):
    task_id, ids, original = await make_task(db_session, tmp_path, count=2)
    await db_session.rollback()
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked_render(_video, segments, *_args, **_kwargs):
        entered.set()
        await asyncio.wait_for(release.wait(), 10)
        return _rendered(tmp_path, segments)

    second_render = AsyncMock(side_effect=AssertionError("second regeneration rendered"))
    async with sessions() as one, sessions() as two:
        first = _service(one, tmp_path, original, blocked_render)
        second = _service(two, tmp_path, original, second_render)
        job = asyncio.create_task(
            first.update_task_settings(task_id, None, 40, None, "default", False, True)
        )
        try:
            await asyncio.wait_for(entered.wait(), 5)
            with pytest.raises(RegenerationBusy):
                await asyncio.wait_for(
                    second.update_task_settings(task_id, None, 90, None, "default", False, True), 5
                )
        finally:
            release.set()
        await asyncio.wait_for(job, 10)

    second_render.assert_not_awaited()
    assert len(await ClipRepository.get_clips_by_task(db_session, task_id)) == 2
    assert await _task_font_size(db_session, task_id) == 40


@pytest.mark.asyncio
async def test_partial_render_keeps_the_clips_that_did_not_render(
    db_session, initialized_database, tmp_path
):
    task_id, ids, original = await make_task(db_session, tmp_path, count=3)
    await db_session.rollback()
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def second_clip_fails(_video, segments, *_args, **_kwargs):
        return _rendered(tmp_path, segments, indexes={0, 2})

    async with sessions() as session:
        service = _service(session, tmp_path, original, second_clip_fails)
        await service.update_task_settings(task_id, None, 50, None, "default", False, True)

    clips = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert len(clips) == 3
    # The clip that failed to render is still the user's clip, in its place.
    assert clips[1]["id"] == ids[1] and clips[1]["file_path"] == str(original)
    assert clips[0]["id"] not in ids and clips[2]["id"] not in ids
    assert [clip["clip_order"] for clip in clips] == [1, 2, 3]
    stored = await db_session.execute(
        text("SELECT generated_clips_ids FROM tasks WHERE id = :id"), {"id": task_id}
    )
    assert list(stored.scalar()) == [clip["id"] for clip in clips]


@pytest.mark.asyncio
async def test_failed_regeneration_leaves_clips_and_settings_unchanged(
    db_session, initialized_database, tmp_path
):
    task_id, ids, original = await make_task(db_session, tmp_path, count=2)
    await db_session.rollback()
    before = await _task_font_size(db_session, task_id)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def nothing_rendered(*_args, **_kwargs):
        return []

    async with sessions() as session:
        service = _service(session, tmp_path, original, nothing_rendered)
        with pytest.raises(ValueError, match="existing clips are unchanged"):
            await service.update_task_settings(task_id, None, 99, None, "default", False, True)

    assert [clip["id"] for clip in await ClipRepository.get_clips_by_task(db_session, task_id)] == ids
    assert await _task_font_size(db_session, task_id) == before


@pytest.mark.asyncio
async def test_edit_committed_during_render_wins_and_renders_are_discarded(
    db_session, initialized_database, tmp_path, request
):
    task_id, ids, original = await make_task(db_session, tmp_path, count=3)
    await db_session.rollback()
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    rendered_paths = []
    # Media cleanup only accepts paths under TEMP_DIR.
    set_config_override(config_for(tmp_path))
    request.addfinalizer(lambda: set_config_override(None))

    async with sessions() as session, sessions() as other:
        async def render_while_user_deletes(_video, segments, *_args, **_kwargs):
            await TaskService(other, config=config_for(tmp_path)).delete_clip(task_id, ids[0])
            output = _rendered(tmp_path, segments)
            rendered_paths.extend(item["path"] for item in output)
            return output

        service = _service(session, tmp_path, original, render_while_user_deletes)
        with pytest.raises(ValueError, match="changed while"):
            await asyncio.wait_for(
                service.update_task_settings(task_id, None, 77, None, "default", False, True), 10
            )

    clips = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert [clip["id"] for clip in clips] == ids[1:]
    queued = (await db_session.execute(
        text("SELECT file_path FROM media_deletions WHERE file_path = ANY(:paths)"),
        {"paths": rendered_paths},
    )).scalars().all()
    assert sorted(queued) == sorted(rendered_paths)
    assert await _task_font_size(db_session, task_id) != 77
