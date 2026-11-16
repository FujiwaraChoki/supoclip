"""A preset export racing an edit that replaces the task's clips (regeneration).

The regeneration holds the task's edit lock across its render and then deletes
the clips. An export used to wait on that and insert against the deleted clip
(foreign key error, client timeout).
"""

import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.repositories.clip_export_repository import ClipExportRepository
from src.repositories.clip_repository import ClipRepository
from src.repositories.edit_transaction import hold_off_task_edits, task_edit_transaction
from tests.integration.test_clip_exports import exports_url, record_jobs, seed_clip


def sessions(initialized_database):
    return async_sessionmaker(initialized_database, class_=AsyncSession, expire_on_commit=False)


async def export_rows(db_session, clip_id: str) -> int:
    result = await db_session.execute(
        text("SELECT COUNT(*) FROM clip_exports WHERE clip_id = :clip_id"), {"clip_id": clip_id}
    )
    count = result.scalar_one()
    await db_session.commit()
    return count


async def test_export_during_an_edit_is_busy_at_once(
    client, app, db_session, initialized_database, auth_headers, tmp_path, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "clip.mp4"))

    async with sessions(initialized_database)() as editor:
        async with task_edit_transaction(editor, task_id):
            response = await asyncio.wait_for(
                client.post(exports_url(task_id, clip_id), headers=auth_headers, json={"preset": "tiktok"}),
                timeout=5,
            )

    assert response.status_code == 409
    assert response.headers["retry-after"] == "5"
    assert jobs == []
    assert await export_rows(db_session, clip_id) == 0


async def test_regeneration_wins_and_the_export_finds_the_clip_gone(
    client, app, db_session, initialized_database, auth_headers, tmp_path, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "clip.mp4"))
    url = exports_url(task_id, clip_id)

    async with sessions(initialized_database)() as editor:
        async with task_edit_transaction(editor, task_id):
            await ClipRepository.delete_clips_by_task(editor, task_id)
            during = await asyncio.wait_for(
                client.post(url, headers=auth_headers, json={"preset": "tiktok"}), timeout=5
            )
    after = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})

    assert during.status_code == 409
    assert after.status_code == 404
    assert jobs == []


async def test_export_wins_and_the_edit_waits_for_its_row(
    db_session, initialized_database, tmp_path
):
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "clip.mp4"))
    make_session = sessions(initialized_database)

    async with make_session() as exporter, make_session() as editor:
        await hold_off_task_edits(exporter, task_id)

        async def regenerate():
            async with task_edit_transaction(editor, task_id):
                await ClipRepository.delete_clips_by_task(editor, task_id)

        regeneration = asyncio.create_task(regenerate())
        await asyncio.sleep(0.3)
        assert not regeneration.done()

        await ClipExportRepository.insert(
            exporter,
            export_id="export-race-1",
            task_id=task_id,
            clip_id=clip_id,
            user_id="user-1",
            preset="tiktok",
            options_hash="hash",
            source_file_path=str(tmp_path / "clip.mp4"),
            download_filename="clip_tiktok.mp4",
            ttl_seconds=60,
        )
        await exporter.commit()
        await asyncio.wait_for(regeneration, timeout=5)

    # The edit ran after the export committed; the clip's export went with it.
    assert await export_rows(db_session, clip_id) == 0


async def test_retries_reuse_one_export_once_the_edit_is_done(
    client, app, db_session, initialized_database, auth_headers, tmp_path, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    task_id, clip_id = await seed_clip(db_session, file_path=str(tmp_path / "clip.mp4"))
    url = exports_url(task_id, clip_id)

    first = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    async with sessions(initialized_database)() as editor:
        async with task_edit_transaction(editor, task_id):
            busy = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    retry = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})

    assert first.status_code == retry.status_code == 202
    assert busy.status_code == 409
    assert retry.json()["id"] == first.json()["id"]
    assert len(jobs) == 1
    assert await export_rows(db_session, clip_id) == 1
