"""A preset export racing an edit that replaces the task's clips (regeneration).

The regeneration holds the task's edit lock across its render and then deletes
the clips. An export used to wait on that and insert against the deleted clip
(foreign key error, client timeout).
"""

import asyncio

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.repositories.clip_export_repository import ClipExportRepository
from src.repositories.clip_repository import ClipRepository
from src.repositories.edit_transaction import hold_off_task_edits, task_edit_transaction
from tests.integration.test_clip_exports import exports_url, headers_for, record_jobs, seed_clip
from tests.fixtures.factories import create_user


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


@pytest.mark.parametrize("delete_clip", [True, False])
async def test_export_rereads_a_clip_changed_after_authorization(
    client, app, db_session, initialized_database, auth_headers, monkeypatch, delete_clip
):
    from src.api.routes import clip_exports

    jobs = record_jobs(monkeypatch, app)
    task_id, clip_id = await seed_clip(db_session, file_path="s3://test-bucket/media/clips/old.mp4")
    new_path = "s3://test-bucket/media/clips/regenerated.mp4"
    original_guard = clip_exports.hold_off_task_edits

    async def edit_before_lock(db, requested_task):
        # The route already authorized and read the original clip. Commit an
        # edit now, before its guard can protect the subsequent read/insert.
        async with sessions(initialized_database)() as editor:
            async with task_edit_transaction(editor, requested_task):
                if delete_clip:
                    await ClipRepository.delete_clips_by_task(editor, requested_task)
                else:
                    await editor.execute(
                        text("UPDATE generated_clips SET file_path = :path WHERE id = :id"),
                        {"path": new_path, "id": clip_id},
                    )
        await original_guard(db, requested_task)

    monkeypatch.setattr(clip_exports, "hold_off_task_edits", edit_before_lock)
    response = await asyncio.wait_for(
        client.post(exports_url(task_id, clip_id), headers=auth_headers, json={"preset": "tiktok"}),
        timeout=5,
    )
    if delete_clip:
        assert response.status_code == 404
        assert response.json()["detail"] == "Clip not found"
        assert jobs == []
        assert await export_rows(db_session, clip_id) == 0
    else:
        assert response.status_code == 202
        row = await ClipExportRepository.get(db_session, response.json()["id"])
        assert row["source_file_path"] == new_path
        assert len(jobs) == 1


async def test_export_guard_allows_progress_updates_and_other_exports(
    client, app, db_session, initialized_database, auth_headers, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    task_id, clip_id = await seed_clip(db_session)
    async with sessions(initialized_database)() as exporter, sessions(initialized_database)() as updater:
        await hold_off_task_edits(exporter, task_id)
        await asyncio.wait_for(updater.execute(
            text("UPDATE tasks SET progress = 50 WHERE id = :id"), {"id": task_id}
        ), timeout=5)
        # Leave the progress update uncommitted as well. Neither its NO KEY
        # UPDATE lock nor the first export's KEY SHARE should reject these.
        responses = await asyncio.wait_for(asyncio.gather(*[
            client.post(exports_url(task_id, clip_id), headers=auth_headers, json={"preset": "tiktok"})
            for _ in range(3)
        ]), timeout=5)
        await updater.rollback()
        await exporter.rollback()

    assert [response.status_code for response in responses] == [202, 202, 202]
    assert len({response.json()["id"] for response in responses}) == 1
    assert len(jobs) == 1
    assert await export_rows(db_session, clip_id) == 1


async def test_busy_task_still_checks_authorization_and_preset_first(
    client, app, db_session, initialized_database, auth_headers, monkeypatch
):
    jobs = record_jobs(monkeypatch, app)
    task_id, clip_id = await seed_clip(db_session)
    await create_user(db_session, user_id="outsider", email="outsider@example.com")
    url = exports_url(task_id, clip_id)
    async with sessions(initialized_database)() as editor:
        async with task_edit_transaction(editor, task_id):
            outsider = await asyncio.wait_for(
                client.post(url, headers=headers_for("outsider"), json={"preset": "tiktok"}), timeout=5
            )
            invalid = await asyncio.wait_for(
                client.post(url, headers=auth_headers, json={"preset": "invalid"}), timeout=5
            )

    assert outsider.status_code == 403
    assert invalid.status_code == 400
    assert "retry-after" not in outsider.headers
    assert jobs == []
    assert await export_rows(db_session, clip_id) == 0
