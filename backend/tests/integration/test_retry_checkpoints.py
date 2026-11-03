"""Real PostgreSQL retry boundaries: usable output and checkpoints survive failure."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.repositories.clip_repository import ClipRepository
from src.repositories.edit_transaction import task_edit_transaction
from src.repositories.task_checkpoint_repository import TaskCheckpointRepository, checkpoint_input_key
from src.repositories.task_repository import TaskRepository
from tests.integration.test_clip_edit_atomicity import make_task
from tests.integration.test_processing_cancellation import setup_worker


@pytest.fixture(autouse=True)
def local_media_only(monkeypatch):
    monkeypatch.setenv("MEDIA_STORAGE", "local")


async def test_failed_retry_keeps_clip_edits_exports_and_scheduled_post(db_session, tmp_path):
    task_id, ids, original = await make_task(db_session, tmp_path, status="queued")
    original.write_bytes(b"edited clip that must survive")
    task = await TaskRepository.get_task_by_id(db_session, task_id)
    post_id, export_id = str(uuid4()), str(uuid4())
    params = {"task": task_id, "clip": ids[0], "user": task["user_id"],
              "post": post_id, "export": export_id, "path": str(original)}
    await db_session.execute(text("""
        INSERT INTO social_posts (id, user_id, task_id, clip_id, provider, status, source_file_path)
        VALUES (:post, :user, :task, :clip, 'youtube', 'scheduled', :path)
    """), params)
    await db_session.execute(text("""
        INSERT INTO clip_exports (id, task_id, clip_id, user_id, preset, options_hash,
            source_file_path, status, file_path, download_filename, expires_at)
        VALUES (:export, :task, :clip, :user, 'shorts', 'test', :path,
            'succeeded', :path, 'saved.mp4', NOW()+INTERVAL '1 day')
    """), params)
    await db_session.commit()
    before = await ClipRepository.get_clip_by_id(db_session, ids[0])
    worker, new_path, new_info = setup_worker(db_session, tmp_path, original)
    worker.video_service.process_video_complete.return_value["segments_to_render"] = [{}, {}]
    worker.video_service.create_single_clip = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="Your completed clips are saved"):
        await worker.process_task(task_id, "upload://input.mp4", "upload")

    async def assert_original_survives():
        assert await ClipRepository.get_clip_by_id(db_session, ids[0]) == before
        assert original.read_bytes() == b"edited clip that must survive"
        assert (await db_session.execute(text("SELECT status FROM social_posts WHERE id=:post"), params)).scalar_one() == "scheduled"
        assert (await db_session.execute(text("SELECT status FROM clip_exports WHERE id=:export"), params)).scalar_one() == "succeeded"
        assert (await db_session.execute(text("SELECT COUNT(*) FROM media_deletions WHERE file_path=:path"), params)).scalar_one() == 0

    await assert_original_survives()
    assert (await TaskRepository.get_task_by_id(db_session, task_id))["status"] == "error"
    input_key = checkpoint_input_key("upload://input.mp4", "upload", "fast", "auto", worker.config)
    assert (await TaskCheckpointRepository.get(db_session, task_id, input_key))["completed_positions"] == [1]

    await TaskRepository.update_task_status(db_session, task_id, "queued")
    worker.video_service.create_single_clip = AsyncMock(return_value=new_info)
    result = await worker.process_task(task_id, "upload://input.mp4", "upload")
    assert result["clips_count"] == 2
    worker.video_service.create_single_clip.assert_awaited_once()
    assert worker.video_service.create_single_clip.await_args.args[2] == 1
    await assert_original_survives()
    assert (await TaskCheckpointRepository.get(db_session, task_id, input_key))["completed_positions"] == [1, 2]
    clips = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert clips[0]["id"] == ids[0]
    assert clips[1]["file_path"] == str(new_path)


async def test_checkpoint_updates_are_task_private_and_atomic_with_clips(db_session, tmp_path):
    first, _, original = await make_task(db_session, tmp_path, count=0, status="processing")
    other, _, _ = await make_task(db_session, tmp_path, count=0)
    repo = TaskCheckpointRepository()
    await repo.save(db_session, first, "input-a", {"transcript": "paid transcript", "completed_positions": []})
    await repo.save(db_session, first, "input-a", {"analysis": {"private": True}})
    assert await repo.get(db_session, other, "input-a") == {}
    assert await repo.get(db_session, first, "input-b") == {}

    with pytest.raises(RuntimeError, match="cancel before commit"):
        async with task_edit_transaction(db_session, first, processing=True):
            await ClipRepository.create_clip(db_session, first, "new.mp4", str(original),
                                              "00:00", "00:10", 10, "", 1, "", 1)
            await repo.save(db_session, first, "input-a", {"completed_positions": [1]})
            raise RuntimeError("cancel before commit")
    assert await ClipRepository.get_clips_by_task(db_session, first) == []
    checkpoint = await repo.get(db_session, first, "input-a")
    assert checkpoint == {"transcript": "paid transcript", "analysis": {"private": True}, "completed_positions": []}

    await repo.save(db_session, first, "input-b", {"transcript": "different input"})
    assert await repo.get(db_session, first, "input-b") == {"transcript": "different input"}
    await db_session.execute(text("DELETE FROM tasks WHERE id=:id"), {"id": first})
    await db_session.commit()
    assert await repo.get(db_session, first, "input-b") == {}


async def test_retry_does_not_confuse_display_order_with_completed_plan_positions(db_session, tmp_path):
    task_id, ids, original = await make_task(db_session, tmp_path, count=2, status="queued")
    worker, _, info = setup_worker(db_session, tmp_path, original)
    worker.video_service.process_video_complete.return_value["segments_to_render"] = [{}, {}, {}]
    key = checkpoint_input_key("upload://input.mp4", "upload", "fast", "auto", worker.config)
    # The two current clips came from an edit of position 1, not positions 1+2.
    await TaskCheckpointRepository.save(db_session, task_id, key, {"completed_positions": [1]})
    worker.video_service.create_single_clip = AsyncMock(return_value=info)
    result = await worker.process_task(task_id, "upload://input.mp4", "upload")
    assert result["clips_count"] == 4
    assert [call.args[2] for call in worker.video_service.create_single_clip.await_args_list] == [1, 2]
    clips = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert [clip["id"] for clip in clips[:2]] == ids
    assert [clip["clip_order"] for clip in clips] == [1, 2, 3, 4]
