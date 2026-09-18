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

