import pytest
from sqlalchemy import text
from tests.fixtures.factories import create_clip, create_source, create_task, create_user


@pytest.mark.asyncio
async def test_clip_repository_inserts_and_upserts_without_database_id_default(db_session):
    from src.repositories.clip_repository import ClipRepository

    owner = await create_user(db_session)
    source = await create_source(db_session)
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"])
    # Match Prisma's schema, which supplies UUIDs in its client, not in SQL.
    await db_session.execute(text("ALTER TABLE generated_clips ALTER COLUMN id DROP DEFAULT"))
    kwargs = dict(task_id=task["id"], filename="real.mp4", file_path="/tmp/real.mp4",
                  start_time="00:00", end_time="00:17", duration=17,
                  text="Transcript", relevance_score=0.9, reasoning="Test", clip_order=1)
    first_id = await ClipRepository.create_clip(db_session, **kwargs)
    second_id = await ClipRepository.create_clip(db_session, **{**kwargs, "filename": "updated.mp4"})
    assert first_id == second_id
    assert (await ClipRepository.get_clip_by_id(db_session, first_id))["filename"] == "updated.mp4"



@pytest.mark.asyncio
async def test_splitting_first_clip_preserves_following_clips(db_session, tmp_path, monkeypatch):
    from src.config import Config
    from src.repositories.clip_repository import ClipRepository
    from src.services.task_service import TaskService
    from src.services import clip_service

    owner = await create_user(db_session)
    source = await create_source(db_session)
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"])
    input_path = tmp_path / "input.mp4"
    input_path.touch()
    ids = []
    for order in range(1, 4):
        ids.append(await ClipRepository.create_clip(db_session, task_id=task["id"],
            filename=f"clip-{order}.mp4", file_path=str(input_path), start_time="00:00",
            end_time="00:10", duration=10, text=f"Clip {order}", relevance_score=0.9,
            reasoning="Regression", clip_order=order))
    await db_session.commit()
    monkeypatch.setattr(clip_service, "split_clip_file", lambda *args: (tmp_path / "first.mp4", tmp_path / "second.mp4"))
    config = Config()
    config.temp_dir = str(tmp_path)
    await TaskService(db_session, config=config).split_clip(task["id"], ids[0], 5)
    clips = await ClipRepository.get_clips_by_task(db_session, task["id"])
    assert len(clips) == 4
    assert [c["id"] for c in clips][2:] == ids[1:]
    assert [c["filename"] for c in clips][2:] == ["clip-2.mp4", "clip-3.mp4"]
    assert [c["clip_order"] for c in clips] == [1, 2, 3, 4]
    stored = await db_session.execute(text("SELECT generated_clips_ids FROM tasks WHERE id = :id"), {"id": task["id"]})
    assert list(stored.scalar()) == [c["id"] for c in clips]
