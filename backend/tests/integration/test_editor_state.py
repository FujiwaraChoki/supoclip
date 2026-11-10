"""The editor store contract, checked against PostgreSQL and the in-memory test double.

Unit tests of the studio use the double, so both must behave the same way.
"""

import asyncio
import uuid

import pytest
from sqlalchemy import text

from src import editor_state
from src.editor_state import (
    INTERRUPTED_PREPARE,
    STALE_PREPARE_SECONDS,
    STALE_RENDER_SECONDS,
    EditorStore,
    editor_key,
    read_state,
    reap_jobs,
)
from tests.fixtures.editor_store import MemoryEditorStore

DOCUMENT = {"version": 1, "segments": [{"id": "s", "start": 0, "end": 1}]}
ASSET = {"status": "ready", "duration": 1, "width": 320, "height": 180, "hasLayout": True}


@pytest.fixture(params=["postgres", "memory"])
def store(request, initialized_database):
    chosen = EditorStore() if request.param == "postgres" else MemoryEditorStore()
    editor_state.set_store(chosen)
    yield chosen
    editor_state.set_store(None)


@pytest.fixture
def session():
    task_id, clip_id, filename = str(uuid.uuid4()), str(uuid.uuid4()), "clip.mp4"
    return editor_key(task_id, clip_id, filename), task_id, clip_id, filename


async def age(store, db_session, *, session_key=None, job_id=None, seconds):
    """Make a row look as if nothing touched it for ``seconds``."""
    if isinstance(store, MemoryEditorStore):
        if session_key:
            store.sessions[session_key]["updated"] -= seconds
        if job_id:
            store.jobs_by_id[job_id]["updated"] -= seconds
        return
    if session_key:
        await db_session.execute(text(
            "UPDATE editor_sessions SET updated_at = NOW() - make_interval(secs => :s) WHERE id = :id"
        ), {"s": seconds, "id": session_key})
    if job_id:
        await db_session.execute(text(
            "UPDATE editor_jobs SET updated_at = NOW() - make_interval(secs => :s) WHERE id = :id"
        ), {"s": seconds, "id": job_id})
    await db_session.commit()


async def state(store, session):
    key, task_id, clip_id, filename = session
    return await read_state(key, await store.load(key), task_id=task_id, clip_id=clip_id, filename=filename)


async def test_only_one_of_many_concurrent_requests_claims_a_preparation(store, session):
    key, *identity = session
    assert await store.load(key) is None
    results = await asyncio.gather(*[
        store.claim_prepare(key, *identity, f"job-{number}") for number in range(8)
    ])
    assert sorted(results) == ["claimed"] + ["preparing"] * 7
    assert (await state(store, session)) == {"status": "preparing"}


async def test_prepare_keeps_an_existing_draft_and_a_failure_never_demotes_a_ready_session(store, session):
    key, *identity = session
    await store.claim_prepare(key, *identity, "job")
    await store.begin_prepare(key, *identity)
    await store.finish_prepare(key, *identity, ASSET, DOCUMENT)
    assert await store.save_draft(key, {**DOCUMENT, "muted": True}, 0) == 1
    assert await store.claim_prepare(key, *identity, "again") == "ready"

    # A second preparation (retry, duplicate job) must not reset the user's edits.
    await store.begin_prepare(key, *identity)
    await store.finish_prepare(key, *identity, {**ASSET, "duration": 2}, DOCUMENT)
    await store.fail_prepare(key, *identity, "late failure of a duplicate job")
    loaded = await store.load(key)
    assert loaded["asset"]["status"] == "ready" and loaded["asset"]["duration"] == 2
    assert loaded["draft"] == {"revision": 1, "document": {**DOCUMENT, "muted": True}}
    assert loaded["original"] == DOCUMENT


async def test_two_tabs_saving_the_same_revision_cannot_both_win(store, session):
    key, *identity = session
    await store.finish_prepare(key, *identity, ASSET, DOCUMENT)
    results = await asyncio.gather(*[
        store.save_draft(key, {**DOCUMENT, "volume": number / 10}, 0) for number in range(6)
    ])
    assert sorted(result for result in results if result is not None) == [1]
    assert results.count(None) == 5
    assert (await store.load(key))["draft"]["revision"] == 1
    # Drafts are only accepted once the session is prepared.
    other = editor_key("task", "unprepared", "clip.mp4")
    assert await store.save_draft(other, DOCUMENT, 0) is None


async def test_a_started_preparation_that_stops_heartbeating_is_reported_and_can_be_retried(store, session, db_session):
    key, *identity = session
    await store.claim_prepare(key, *identity, "job")
    await store.begin_prepare(key, *identity)
    await age(store, db_session, session_key=key, seconds=STALE_PREPARE_SECONDS - 60)
    assert (await state(store, session))["status"] == "preparing"
    await store.touch_prepare(key)
    await age(store, db_session, session_key=key, seconds=STALE_PREPARE_SECONDS + 5)
    assert await state(store, session) == {"status": "failed", "error": INTERRUPTED_PREPARE}
    assert await store.claim_prepare(key, *identity, "retry") == "claimed"


async def test_job_lifecycle_limit_and_snapshot(store, session):
    key, *identity = session
    await store.finish_prepare(key, *identity, ASSET, DOCUMENT)
    jobs = [uuid.uuid4().hex for _ in range(4)]
    created = [await store.create_job(key, job, "export", {"document": DOCUMENT, "preset": "reels"}) for job in jobs]
    assert created == [True, True, True, False]
    assert await store.job(key, jobs[3]) is None
    assert await store.job("another-session", jobs[0]) is None

    claimed = await store.start_job(jobs[0])
    claim = claimed["claim"]
    assert claimed["request"] == {"document": DOCUMENT, "preset": "reels"} and claimed["kind"] == "export"
    # Exactly one worker gets the job, however many deliveries the queue makes.
    assert await store.start_job(jobs[0]) is None
    assert await store.beat_job(jobs[0], claim, 40) is True
    assert await store.beat_job(jobs[0], claim, 10) is True
    assert (await store.job(key, jobs[0])) == {"id": jobs[0], "status": "rendering", "progress": 40}
    assert await store.finish_job(jobs[0], claim, "completed", result={"clip_id": "new"}) is True
    assert await store.job(key, jobs[0]) == {"id": jobs[0], "status": "completed", "progress": 100, "clip_id": "new"}
    assert await store.finish_job(jobs[0], claim, "failed", error="late") is False
    # A finished job frees its slot.
    assert await store.create_job(key, jobs[3], "combine", {"inputs": []}) is True
    assert {job["id"] for job in await store.jobs(key)} == set(jobs)


async def test_cancel_reaches_the_worker_through_the_store(store, session):
    key, *identity = session
    await store.finish_prepare(key, *identity, ASSET, DOCUMENT)
    queued, running = uuid.uuid4().hex, uuid.uuid4().hex
    await store.create_job(key, queued, "export", {})
    await store.create_job(key, running, "export", {})
    claim = (await store.start_job(running))["claim"]

    assert await store.cancel_job(key, queued) == "cancelled"
    assert await store.start_job(queued) is None
    assert await store.cancel_job(key, running) == "cancelling"
    assert (await store.job(key, running))["status"] == "rendering"
    # The rendering worker learns it from its next heartbeat and closes the job.
    assert await store.beat_job(running, claim, 50) is False
    assert await store.finish_job(running, claim, "cancelled") is True
    assert await store.cancel_job(key, running) == "cancelled"
    assert await store.cancel_job(key, uuid.uuid4().hex) is None
    assert await store.cancel_job("another-session", running) is None


async def test_a_dead_workers_job_is_failed_for_the_user_or_taken_over_by_a_retry(store, session, db_session):
    key, *identity = session
    await store.finish_prepare(key, *identity, ASSET, DOCUMENT)
    reaped, retried, released = uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
    claims = {}
    for job in (reaped, retried, released):
        await store.create_job(key, job, "export", {"preset": "tiktok"})
        claims[job] = (await store.start_job(job))["claim"]

    # A live render is never taken from its worker.
    assert await store.start_job(retried) is None
    await age(store, db_session, job_id=reaped, seconds=STALE_RENDER_SECONDS + 5)
    await age(store, db_session, job_id=retried, seconds=STALE_RENDER_SECONDS + 5)
    # The queue redelivers one of them: that worker takes it over.
    takeover = await store.start_job(retried)
    assert takeover["request"] == {"preset": "tiktok"} and takeover["claim"] != claims[retried]
    # The first worker was only slow. It must not report on, cancel, close or
    # requeue the run that replaced it.
    assert await store.beat_job(retried, claims[retried], 80) is False
    assert await store.finish_job(retried, claims[retried], "cancelled") is False
    await store.requeue_job(retried, claims[retried])
    assert (await store.job(key, retried))["status"] == "rendering"
    assert await store.beat_job(retried, takeover["claim"], 30) is True
    # Nobody redelivers the other: the API fails it so the studio stops waiting.
    await reap_jobs(key)
    assert (await store.job(key, reaped))["status"] == "failed"
    assert (await store.job(key, retried))["status"] == "rendering"
    # A job the API already failed stays failed.
    assert await store.beat_job(reaped, claims[reaped], 90) is False
    assert await store.finish_job(reaped, claims[reaped], "completed") is False

    # A worker shutting down hands its job back for the queue's own retry.
    await store.requeue_job(released, claims[released])
    assert (await store.job(key, released))["status"] == "queued"
    assert await store.start_job(released) is not None


async def test_file_based_session_is_imported_once(store, session):
    key, *identity = session
    job = uuid.uuid4().hex
    legacy = {
        "asset": ASSET,
        "draft": {"revision": 5, "document": {**DOCUMENT, "muted": True}},
        "original": DOCUMENT,
        "jobs": [{"id": job, "kind": "combine", "status": "completed", "progress": 100, "error": None,
                  "result": {"clip_id": "made"}, "request": {"inputs": []}, "created": 1_700_000_000.0}],
    }
    await store.import_legacy(key, *identity, legacy)
    await store.save_draft(key, DOCUMENT, 5)
    # A second import (another API process racing the first) changes nothing.
    await store.import_legacy(key, *identity, legacy)
    loaded = await store.load(key)
    assert loaded["draft"] == {"revision": 6, "document": DOCUMENT}
    assert await store.jobs(key) == [{"id": job, "status": "completed", "progress": 100, "clip_id": "made"}]


async def test_deleting_a_session_removes_its_jobs(store, session):
    key, *identity = session
    await store.finish_prepare(key, *identity, ASSET, DOCUMENT)
    job = uuid.uuid4().hex
    await store.create_job(key, job, "export", {})
    await store.delete(key)
    assert await store.load(key) is None
    assert await store.job(key, job) is None and not await store.job_exists(job)


async def test_sessions_follow_their_clip_like_the_media_does(db_session, initialized_database):
    """Kept while a clip has this task, id and filename; removed after the grace period otherwise."""
    from src.media_storage import MediaStorage
    from src.repositories.clip_repository import ClipRepository
    from src.workers.media_cleanup import delete_orphaned_editor_sessions
    from tests.fixtures.factories import create_source, create_task, create_user

    store = EditorStore()
    user = await create_user(db_session)
    source = await create_source(db_session)
    task = await create_task(db_session, user_id=user["id"], source_id=source["id"], status="completed")
    clip_id = await ClipRepository.create_clip(
        db_session, task["id"], "kept.mp4", "/tmp/kept.mp4", "00:00", "00:01", 1, "", 0, "", 1
    )
    await db_session.commit()
    live = editor_key(task["id"], clip_id, "kept.mp4")
    replaced = editor_key(task["id"], clip_id, "before-trim.mp4")
    fresh = editor_key(task["id"], str(uuid.uuid4()), "combined.mp4")
    await store.finish_prepare(live, task["id"], clip_id, "kept.mp4", ASSET, DOCUMENT)
    await store.finish_prepare(replaced, task["id"], clip_id, "before-trim.mp4", ASSET, DOCUMENT)
    await store.finish_prepare(fresh, task["id"], str(uuid.uuid4()), "combined.mp4", ASSET, DOCUMENT)
    storage = MediaStorage()
    grace = storage.config.media_orphan_grace_seconds
    await db_session.execute(text(
        "UPDATE editor_sessions SET updated_at = NOW() - make_interval(secs => :s) WHERE id IN (:live, :replaced)"
    ), {"s": grace + 60, "live": live, "replaced": replaced})
    await db_session.commit()

    await delete_orphaned_editor_sessions(db_session, storage)
    assert await store.load(live) is not None
    assert await store.load(replaced) is None
    # Written moments ago, clip row not committed yet (a combine in progress).
    assert await store.load(fresh) is not None
    await store.delete(live)
    await store.delete(fresh)
