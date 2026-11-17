"""Preset export routes never hold a database connection while storage answers.

Production incident (17 Nov 2026): the task page polls an export's status every
few seconds, and each poll kept its transaction open across a storage HEAD.
Starting an export did the same under the options lock, the export row lock
and the task's edit hold-off. Slow storage then pinned connections and the API
pool timed out. Each test records how many pooled connections the API has
checked out at the moment storage is asked.
"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database import _build_engine, configure_database
from src.media_storage import MediaStorage
from tests.fixtures.factories import create_clip, create_source, create_task, create_user

REFERENCE = "s3://test-bucket/media/exports/finished.mp4"


@pytest.fixture
async def api_pool(initialized_database, monkeypatch):
    """A real QueuePool for the app, so checked-out connections can be counted."""
    monkeypatch.setenv("DATABASE_POOL_SIZE", "2")
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT_SECONDS", "5")
    engine = _build_engine(initialized_database.url)
    configure_database(
        engine=engine,
        session_maker=async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
    )
    try:
        yield engine
    finally:
        configure_database(engine=initialized_database)
        await engine.dispose()


@pytest.fixture
def storage(monkeypatch, api_pool):
    """Fake bucket. ``checks`` holds the API's checked-out connections per HEAD."""

    class Storage:
        present = True
        checks: list[int] = []

    def exists(self, _reference):
        Storage.checks.append(api_pool.pool.checkedout())
        return Storage.present

    monkeypatch.setattr(MediaStorage, "exists", exists)
    monkeypatch.setattr(
        MediaStorage,
        "signed_url",
        lambda self, _reference, filename, attachment=False, expires_in=None: f"https://storage.test/{filename}",
    )
    return Storage


@pytest.fixture
async def finished_export(client, app, db_session, auth_headers, monkeypatch):
    """A clip with one succeeded tiktok export stored in the bucket."""
    jobs = []

    async def record(cls, function_name, *args, **kwargs):
        jobs.append(function_name)
        return "job-test"

    monkeypatch.setattr(app.state.queue_adapter, "enqueue_job", classmethod(record))
    user = await create_user(db_session, user_id="user-1", email="user-1@example.com")
    source = await create_source(db_session, title="Source")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"])
    clip = await create_clip(db_session, task_id=task["id"])
    await db_session.execute(
        text("UPDATE generated_clips SET file_path = '/tmp/seeded.mp4', filename = 'interview.mp4' WHERE id = :id"),
        {"id": clip["id"]},
    )
    await db_session.commit()
    url = f"/tasks/{task['id']}/clips/{clip['id']}/exports"
    started = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    assert started.status_code == 202, started.text
    export_id = started.json()["id"]
    await db_session.execute(
        text(
            "UPDATE clip_exports SET status = 'succeeded', progress = 100, file_path = :path, "
            "updated_at = NOW() WHERE id = :id"
        ),
        {"path": REFERENCE, "id": export_id},
    )
    await db_session.commit()
    jobs.clear()
    return url, export_id, jobs


async def test_status_poll_releases_its_connection_before_storage(client, auth_headers, storage, finished_export):
    url, export_id, _jobs = finished_export
    response = await client.get(f"{url}/{export_id}", headers=auth_headers)
    assert response.status_code == 200, response.text
    assert response.json()["download_url"] == "https://storage.test/interview_tiktok.mp4"
    assert storage.checks == [0]


async def test_download_releases_its_connection_before_storage(client, auth_headers, storage, finished_export):
    url, export_id, _jobs = finished_export
    response = await client.get(f"{url}/{export_id}/file", headers=auth_headers)
    assert response.status_code == 307
    assert storage.checks == [0]


async def test_restart_reuses_a_present_export_without_storage_under_locks(
    client, auth_headers, storage, finished_export
):
    url, export_id, jobs = finished_export
    response = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    assert response.status_code == 202, response.text
    assert response.json()["id"] == export_id
    assert response.json()["status"] == "succeeded"
    # One HEAD, before any lock, with no connection held.
    assert storage.checks == [0]
    assert jobs == []


async def test_restart_requeues_a_missing_export_without_storage_under_locks(
    client, auth_headers, db_session, storage, finished_export
):
    url, export_id, jobs = finished_export
    storage.present = False
    response = await client.post(url, headers=auth_headers, json={"preset": "tiktok"})
    assert response.status_code == 202, response.text
    assert response.json()["id"] != export_id
    assert response.json()["status"] == "queued"
    assert storage.checks == [0]
    assert jobs == ["export_clip_preset"]
    old = await db_session.execute(
        text("SELECT status, error_message FROM clip_exports WHERE id = :id"), {"id": export_id}
    )
    status, message = old.one()
    assert status == "failed" and message == "Export file is no longer available."
