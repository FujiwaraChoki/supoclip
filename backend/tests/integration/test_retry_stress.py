"""Failure matrices and concurrent callers exercise durable retry invariants."""

import asyncio
import hashlib
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from src.repositories.clip_repository import ClipRepository
from src.repositories.task_checkpoint_repository import TaskCheckpointRepository, checkpoint_input_key
from src.repositories.task_repository import TaskRepository
from src.repositories.task_run_guard import TaskRunBusy
from src.repositories.edit_transaction import TaskCancelled
from tests.fixtures.factories import create_user
from tests.integration.test_clip_edit_atomicity import make_task
from tests.integration.test_processing_cancellation import setup_worker


@pytest.fixture(autouse=True)
def local_media(monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE", "local")


@pytest.mark.parametrize("mask", range(1, 16))
@pytest.mark.parametrize("failure", ["none", "exception", "cancellation"])
async def test_every_partial_failure_pattern_keeps_committed_clips(
    db_session, initialized_database, tmp_path, mask, failure,
):
    task_id, _, original = await make_task(db_session, tmp_path, count=0, status="queued")
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    preserved = {}
    completed = set()
    for attempt in range(3):
        stopped = False
        async with sessions() as db:
            service, _, _ = setup_worker(db, tmp_path, original)
            service.video_service.process_video_complete.return_value["segments_to_render"] = [{"position": i} for i in range(4)]
            calls = []

            async def render(_video, segment, index, *_args, **_kwargs):
                nonlocal stopped
                assert index + 1 not in completed, "Previously committed clip was rendered again"
                calls.append(index + 1)
                failing = attempt < 2 and bool(mask & (1 << index))
                if failing and failure == "none":
                    return None
                if failing and failure == "exception":
                    raise RuntimeError("injected renderer exception")
                path = tmp_path / "clips" / f"attempt-{attempt}-position-{index}.mp4"
                path.write_bytes(f"clip-{index}-attempt-{attempt}".encode())
                path.with_suffix(".source_map.json").write_text('{}')
                if failing:
                    stopped = True
                return {"filename": path.name, "path": str(path), "start_time": "00:00",
                        "end_time": "00:10", "duration": 10, "text": f"position {index + 1}"}

            async def should_cancel():
                return stopped

            service.video_service.create_single_clip = render
            if attempt < 2:
                with pytest.raises(Exception):
                    await service.process_task(task_id, "upload://input.mp4", "upload", should_cancel=should_cancel)
            else:
                await service.process_task(task_id, "upload://input.mp4", "upload", should_cancel=should_cancel)
            clips = await ClipRepository.get_clips_by_task(db, task_id)
            for clip in clips:
                content = Path(clip["file_path"]).read_bytes()
                value = (clip, hashlib.sha256(content).hexdigest())
                if clip["id"] in preserved:
                    assert value == preserved[clip["id"]]
                preserved[clip["id"]] = value
            assert set(preserved) == {clip["id"] for clip in clips}
            key = checkpoint_input_key("upload://input.mp4", "upload", "fast", "auto", service.config)
            completed = set((await TaskCheckpointRepository.get(db, task_id, key))["completed_positions"])
            assert completed == {int(clip["text"].split()[-1]) for clip in clips}
            task = await TaskRepository.get_task_by_id(db, task_id)
            assert list(task["generated_clips_ids"]) == [clip["id"] for clip in clips]
            assert task["status"] == ("completed" if attempt == 2 else "cancelled" if failure == "cancellation" else "error")
            if attempt < 2:
                await TaskRepository.update_task_status(db, task_id, "queued")
    assert len(preserved) == 4
    assert completed == {1, 2, 3, 4}
    assert [clip["clip_order"] for clip in clips] == [1, 2, 3, 4]


async def test_twenty_concurrent_retry_requests_enqueue_once(
    client, auth_headers, db_session, tmp_path, monkeypatch,
):
    task_id, ids, _ = await make_task(db_session, tmp_path, status="error")
    await create_user(db_session, user_id="user-1")
    await db_session.execute(text("UPDATE tasks SET user_id='user-1' WHERE id=:id"), {"id": task_id})
    await db_session.commit()
    enqueue = AsyncMock(return_value="one-new-job")
    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", enqueue)
    responses = await asyncio.gather(*[
        client.post(f"/tasks/{task_id}/resume", headers=auth_headers) for _ in range(20)
    ])
    assert all(response.status_code in {200, 409} for response in responses)
    assert any(response.status_code == 200 for response in responses)
    enqueue.assert_awaited_once()
    assert [clip["id"] for clip in await ClipRepository.get_clips_by_task(db_session, task_id)] == ids


async def test_twenty_duplicate_workers_cannot_enter_active_pipeline(
    db_session, initialized_database, tmp_path,
):
    task_id, _, original = await make_task(db_session, tmp_path, count=0, status="queued")
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)
    entered, release = asyncio.Event(), asyncio.Event()
    async with sessions() as first_db:
        first, _, info = setup_worker(first_db, tmp_path, original)

        async def render(*_args, **_kwargs):
            entered.set()
            await release.wait()
            return info

        first.video_service.create_single_clip = render
        first_job = asyncio.create_task(first.process_task(task_id, "upload://input.mp4", "upload"))
        await asyncio.wait_for(entered.wait(), 5)

        async def duplicate():
            async with sessions() as db:
                service, _, _ = setup_worker(db, tmp_path, original)
                with pytest.raises(TaskRunBusy):
                    await service.process_task(task_id, "upload://input.mp4", "upload")
                service.video_service.process_video_complete.assert_not_awaited()

        try:
            await asyncio.wait_for(asyncio.gather(*[duplicate() for _ in range(20)]), 10)
        finally:
            release.set()
        await asyncio.wait_for(first_job, 5)
    assert len(await ClipRepository.get_clips_by_task(db_session, task_id)) == 1


async def test_long_transcript_checkpoint_survives_concurrent_stage_updates(
    db_session, initialized_database, tmp_path,
):
    task_id, _, _ = await make_task(db_session, tmp_path, count=0, status="error")
    # About two hours of word timings, including multilingual caption content.
    words = [{"text": "déjà 中文 café", "start": i * 300, "end": i * 300 + 250,
              "speaker": "A", "confidence": .99} for i in range(24_000)]
    transcript = {"transcript": "Long source transcript", "transcript_data": {"words": words}}
    await TaskCheckpointRepository.save(db_session, task_id, "long-source", transcript)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    async def save_stage(i):
        async with sessions() as db:
            await TaskCheckpointRepository.save(db, task_id, "long-source", {f"stage_{i}": [i]})

    await asyncio.gather(*[save_stage(i) for i in range(20)])
    checkpoint = await TaskCheckpointRepository.get(db_session, task_id, "long-source")
    assert checkpoint["transcript_data"]["words"] == words
    assert all(checkpoint[f"stage_{i}"] == [i] for i in range(20))
    assert checkpoint["transcript"] == transcript["transcript"]


async def test_deleted_task_cannot_be_resurrected_by_late_checkpoint(db_session, tmp_path):
    task_id, _, _ = await make_task(db_session, tmp_path, count=0)
    await TaskCheckpointRepository.save(db_session, task_id, "input", {"transcript": "saved"})
    await db_session.execute(text("DELETE FROM tasks WHERE id=:id"), {"id": task_id})
    await db_session.commit()
    with pytest.raises(TaskCancelled):
        await TaskCheckpointRepository.save(db_session, task_id, "input", {"analysis": {"late": True}})
    await db_session.rollback()
    assert await TaskCheckpointRepository.get(db_session, task_id, "input") == {}
