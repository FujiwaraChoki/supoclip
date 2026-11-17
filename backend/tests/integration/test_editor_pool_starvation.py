"""Clip studio requests never hold one pooled connection while waiting for another.

Production incident (17 Nov 2026): overlapping ``GET .../editor`` requests on a
5-connection pool each held a connection from the AI allowance read while
``reap_jobs()`` waited for a second one, and the pool timed out. A pool of one
connection turns any such nested checkout into a deterministic failure.
"""

import asyncio
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.database import _build_engine, configure_database
from src.editor_document import EditDocument
from src.editor_state import editor_key, get_store
from src.repositories.clip_repository import ClipRepository
from tests.fixtures.factories import create_source, create_task, create_user


async def _small_pool(initialized_database, monkeypatch, size: int, timeout: int):
    monkeypatch.setenv("DATABASE_POOL_SIZE", str(size))
    monkeypatch.setenv("DATABASE_MAX_OVERFLOW", "0")
    monkeypatch.setenv("DATABASE_POOL_TIMEOUT_SECONDS", str(timeout))
    engine = _build_engine(initialized_database.url)
    configure_database(
        engine=engine,
        session_maker=async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False),
    )
    return engine


@pytest.fixture
async def studio(db_session, app, tmp_path, monkeypatch, initialized_database):
    """Five prepared clips of a Pro owner with AI editing on, as in production."""
    app.state.config.monetization_enabled = True
    app.state.config.temp_dir = str(tmp_path)
    monkeypatch.setattr("src.services.ai_edit_service.ai_editing_configured", lambda: True)
    monkeypatch.setattr("src.api.routes.editor.JobQueue.enqueue_job", AsyncMock(return_value="job"))
    user = await create_user(db_session, user_id="user-1", plan="pro", subscription_status="active")
    source = await create_source(db_session, source_type="video_url", url="upload://source.mp4")
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"])
    clips = []
    for n in range(1, 6):
        filename = f"clip-{n}.mp4"
        clip_id = await ClipRepository.create_clip(
            db_session, task["id"], filename, str(tmp_path / filename),
            "00:00", "00:02", 2, "Hello world", 1, "test", n,
        )
        clips.append({"id": clip_id, "filename": filename})
    await db_session.commit()
    doc = EditDocument.model_validate({"segments": [{"id": "one", "start": 0, "end": 2}]}).model_dump()
    for clip in clips:
        await get_store().finish_prepare(
            editor_key(task["id"], clip["id"], clip["filename"]), task["id"], clip["id"], clip["filename"],
            {"status": "ready", "duration": 2, "width": 320, "height": 180, "hasAudio": True}, doc,
        )
    engines = []

    async def pool(size: int, timeout: int = 2):
        engine = await _small_pool(initialized_database, monkeypatch, size, timeout)
        engines.append(engine)
        return engine

    try:
        yield task["id"], clips, doc, pool
    finally:
        configure_database(engine=initialized_database)
        for engine in engines:
            await engine.dispose()
        await db_session.execute(text("DELETE FROM editor_sessions WHERE task_id = :id"), {"id": task["id"]})
        await db_session.commit()


async def test_every_studio_route_works_on_a_single_connection_pool(client, auth_headers, studio):
    task_id, clips, doc, pool = studio
    await pool(1)
    base = f"/tasks/{task_id}/clips/{clips[0]['id']}/editor"

    state = await client.get(base, headers=auth_headers)
    assert state.status_code == 200, state.text
    body = state.json()
    assert body["status"] == "ready" and body["aiEditUsage"]["limit"] == 10
    assert body["jobs"] == []

    saved = await client.patch(
        base, json={"basis": clips[0]["filename"], "revision": 0, "document": doc}, headers=auth_headers
    )
    assert saved.status_code == 200, saved.text
    export = await client.post(
        base + "/exports", json={"basis": clips[0]["filename"], "document": doc, "preset": "tiktok"},
        headers=auth_headers,
    )
    assert export.status_code == 202, export.text
    job = export.json()["id"]
    assert (await client.get(f"{base}/exports/{job}", headers=auth_headers)).json()["status"] == "queued"
    assert (await client.get(base, headers=auth_headers)).json()["jobs"][0]["id"] == job
    assert (await client.delete(f"{base}/exports/{job}", headers=auth_headers)).json() == {"status": "cancelled"}
    assert (await client.get(base + "/ai", headers=auth_headers)).json()["used"] == 0

    combined = await client.post(
        f"/tasks/{task_id}/editor/combine", json={"clip_ids": [c["id"] for c in clips[:2]]}, headers=auth_headers
    )
    assert combined.status_code == 202, combined.text


async def test_overlapping_editor_loads_do_not_starve_the_pool(client, auth_headers, studio):
    """The incident's burst: 25 editor GETs across 5 clips at once, on a 2-connection pool."""
    task_id, clips, _doc, pool = studio
    await pool(2, timeout=5)
    responses = await asyncio.gather(*(
        client.get(f"/tasks/{task_id}/clips/{clip['id']}/editor", headers=auth_headers)
        for _ in range(5) for clip in clips
    ))
    assert [r.status_code for r in responses] == [200] * 25


async def test_a_full_pool_answers_503_with_retry_after(client, auth_headers, studio):
    task_id, clips, _doc, pool = studio
    engine = await pool(1, timeout=1)
    async with engine.connect():
        response = await client.get(f"/tasks/{task_id}/clips/{clips[0]['id']}/editor", headers=auth_headers)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
    # The connection is free again: the same request succeeds.
    assert (await client.get(f"/tasks/{task_id}/clips/{clips[0]['id']}/editor", headers=auth_headers)).status_code == 200


@pytest.mark.parametrize("route", ["file", "export?preset=tiktok"])
async def test_legacy_clip_routes_answer_503_on_a_full_pool(client, auth_headers, studio, route):
    """These routes turn unexpected errors into 500; a full pool is not one."""
    task_id, clips, _doc, pool = studio
    engine = await pool(1, timeout=1)
    async with engine.connect():
        response = await client.get(f"/tasks/{task_id}/clips/{clips[0]['id']}/{route}", headers=auth_headers)
    assert response.status_code == 503
    assert response.headers["retry-after"] == "5"
