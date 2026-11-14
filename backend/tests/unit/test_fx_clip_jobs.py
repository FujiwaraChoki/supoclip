"""WP18 §13.0: clip edits of dynamic-edits clips run in ``fx_clip_job`` on the video worker.

Routes answer 202 with a pollable job for FX clips and keep the synchronous path for legacy clips; the job runs the
same ClipService method under the task's edit lock and records progress, result or error in Redis.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import tasks
from src.services import clip_service, fx_clip_jobs
from src.services.clip_service import QueuedClipJob

JOB_ID = "0123456789abcdef0123456789abcdef"
FX_META = {"fx_level": "full", "timing": "word", "intensity": "bold", "template": "marker_circle"}


class FakeRedis:
    """The few async Redis calls the clip job record and the task channel use."""

    store: dict = {}
    published: list = []

    def __init__(self, *args, **kwargs):
        pass

    async def set(self, key, value, ex=None):
        type(self).store[key] = value

    async def get(self, key):
        return type(self).store.get(key)

    async def publish(self, channel, message):
        type(self).published.append((channel, json.loads(message)))

    async def aclose(self):
        pass


@pytest.fixture
def redis(monkeypatch):
    FakeRedis.store = {}
    FakeRedis.published = []
    monkeypatch.setattr(clip_service, "_fx_clip_job_redis", FakeRedis)
    monkeypatch.setattr(fx_clip_jobs, "_fx_clip_job_redis", FakeRedis)
    return FakeRedis


def record(job_id: str = JOB_ID) -> dict:
    return json.loads(FakeRedis.store[clip_service.fx_clip_job_key(job_id)])


# --------------------------------------------------------------------------- routes


class FakeQueue:
    def __init__(self, fail: bool = False):
        self.jobs: list = []
        self.fail = fail

    async def enqueue_job(self, function, *args, **kwargs):
        if self.fail:
            raise ConnectionError("redis down")
        self.jobs.append((function, args, kwargs))


@pytest.fixture
def route(monkeypatch, redis):
    """The real router with TaskService faked; clips ``fx`` and ``legacy`` (and ``legacy2``) exist on task-1."""
    rows = {
        "fx": {"id": "fx", "task_id": "task-1", "fx_meta": FX_META},
        "legacy": {"id": "legacy", "task_id": "task-1"},
        "legacy2": {"id": "legacy2", "task_id": "task-1"},
        "foreign": {"id": "foreign", "task_id": "task-2", "fx_meta": FX_META},
    }
    task = {"id": "task-1", "user_id": "user-1", "status": "completed"}

    async def get_clip(_db, clip_id):
        return rows.get(clip_id)

    service = SimpleNamespace(
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(side_effect=get_clip)),
        trim_clip=AsyncMock(return_value={"id": "legacy"}),
        split_clip=AsyncMock(return_value={"message": "Clip split successfully"}),
        merge_clips=AsyncMock(return_value={"message": "Clips merged successfully", "clip_id": "legacy"}),
        update_clip_captions=AsyncMock(return_value={"id": "legacy"}),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
    monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(side_effect=lambda *_: dict(task)))
    state = SimpleNamespace(service=service, task=task, queue=FakeQueue(), db=object())

    async def call(method: str, path: str, body: dict):
        async def database():
            yield state.db

        app = FastAPI()
        app.include_router(tasks.router)
        app.dependency_overrides[tasks.get_db] = database
        app.state.queue_adapter = state.queue
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            return await client.request(method, path, json=body)

    state.call = call
    return state


EDITS = {
    "trim": ("PATCH", "/tasks/task-1/clips/{clip}", {"start_offset": 1.0, "end_offset": 0.5}, "trim_clip",
             {"start_offset": 1.0, "end_offset": 0.5}),
    "regenerate": ("POST", "/tasks/task-1/clips/{clip}/regenerate", {"start_offset": 0.5, "end_offset": 0.0},
                   "trim_clip", {"start_offset": 0.5, "end_offset": 0.0}),
    "split": ("POST", "/tasks/task-1/clips/{clip}/split", {"split_time": 2.5}, "split_clip", {"split_time": 2.5}),
    "captions": ("PATCH", "/tasks/task-1/clips/{clip}/captions",
                 {"caption_text": "Hello world", "position": "top", "highlight_words": ["world"], "font_size": 40,
                  "position_y": 0.3},
                 "update_clip_captions",
                 {"caption_text": "Hello world", "position": "top", "highlight_words": ["world"], "font_size": 40,
                  "position_y": 0.3}),
}


@pytest.mark.parametrize("edit", sorted(EDITS))
async def test_an_fx_clip_edit_returns_202_and_queues_the_job(route, edit):
    method, path, body, service_method, args = EDITS[edit]
    response = await route.call(method, path.format(clip="fx"), body)

    assert response.status_code == 202, response.text
    payload = response.json()
    kind = "trim" if edit == "regenerate" else edit
    assert payload == {"status": "queued", "job_id": payload["job_id"], "kind": kind, "clip_ids": ["fx"]}
    getattr(route.service, service_method).assert_not_awaited()
    ((function, (job,), kwargs),) = route.queue.jobs
    assert function == "fx_clip_job" and kwargs == {"_job_id": f"fx_clip_job:{payload['job_id']}"}
    assert job == {"job_id": payload["job_id"], "task_id": "task-1", "user_id": "user-1", "kind": kind,
                   "args": {"clip_id": "fx", **args}}
    stored = record(payload["job_id"])
    assert stored["status"] == "queued" and stored["clip_ids"] == ["fx"] and stored["task_id"] == "task-1"


@pytest.mark.parametrize("edit", sorted(EDITS))
async def test_a_legacy_clip_edit_stays_synchronous(route, edit):
    method, path, body, service_method, _ = EDITS[edit]
    response = await route.call(method, path.format(clip="legacy"), body)

    assert response.status_code == 200, response.text
    getattr(route.service, service_method).assert_awaited_once()
    assert route.queue.jobs == [] and FakeRedis.store == {}


async def test_a_legacy_edit_without_a_database_never_looks_up_clips(route):
    route.db = None
    response = await route.call("PATCH", "/tasks/task-1/clips/fx", {"start_offset": 1.0})
    assert response.status_code == 200
    route.service.clip_repo.get_clip_by_id.assert_not_awaited()


async def test_another_tasks_clip_is_not_treated_as_fx(route):
    response = await route.call("PATCH", "/tasks/task-1/clips/foreign", {"start_offset": 1.0})
    assert response.status_code == 200          # the service rejects it like any unknown clip
    assert route.queue.jobs == []


async def test_merge_with_any_fx_clip_goes_to_the_worker(route):
    response = await route.call("POST", "/tasks/task-1/clips/merge", {"clip_ids": ["legacy", "fx"]})
    assert response.status_code == 202, response.text
    assert response.json()["clip_ids"] == ["legacy", "fx"]
    ((_, (job,), _),) = route.queue.jobs
    assert job["kind"] == "merge" and job["args"] == {"clip_ids": ["legacy", "fx"]}
    route.service.merge_clips.assert_not_awaited()


async def test_legacy_merge_stays_synchronous(route):
    response = await route.call("POST", "/tasks/task-1/clips/merge", {"clip_ids": ["legacy", "legacy2"]})
    assert response.status_code == 200
    route.service.merge_clips.assert_awaited_once()


async def test_an_fx_merge_is_validated_before_queueing(route):
    response = await route.call("POST", "/tasks/task-1/clips/merge", {"clip_ids": ["fx", "fx"]})
    assert response.status_code == 400
    assert route.queue.jobs == []


async def test_a_queue_failure_is_a_503_with_nothing_changed(route):
    route.queue = FakeQueue(fail=True)
    response = await route.call("POST", "/tasks/task-1/clips/fx/split", {"split_time": 2.0})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "QUEUE_UNAVAILABLE"
    route.service.split_clip.assert_not_awaited()
    (stored,) = [json.loads(value) for value in FakeRedis.store.values()]
    assert stored["status"] == "failed" and stored["error"]["code"] == "QUEUE_UNAVAILABLE"


async def test_an_fx_edit_on_a_busy_task_is_refused_before_queueing(route):
    route.task["status"] = "processing"
    response = await route.call("PATCH", "/tasks/task-1/clips/fx", {"start_offset": 1.0})
    assert response.status_code == 400
    assert route.queue.jobs == []


def test_the_202_body_names_the_clips_only_for_clip_edits():
    assert QueuedClipJob(job_id="j", kind="settings").body() == {"status": "queued", "job_id": "j", "kind": "settings"}
    assert QueuedClipJob(job_id="j", kind="split", clip_ids=("a",)).body() == {
        "status": "queued", "job_id": "j", "kind": "split", "clip_ids": ["a"],
    }


# --------------------------------------------------------------------------- the job


class _Session:
    async def __aenter__(self):
        return "db"

    async def __aexit__(self, *exc):
        return False


def _job(kind: str, args: dict) -> dict:
    return {"job_id": JOB_ID, "task_id": "task-1", "user_id": "user-1", "kind": kind, "args": args}


async def _queued(kind: str, args: dict) -> dict:
    job = _job(kind, args)
    await clip_service.write_fx_clip_job_state(JOB_ID, {
        "job_id": JOB_ID, "task_id": "task-1", "user_id": "user-1", "kind": kind, "status": "queued",
        "progress": 0.0, "queued_at": 1000.0, "clip_ids": ["c1"],
    })
    return job


async def _run(job: dict, service) -> dict:
    return await fx_clip_jobs.run_fx_clip_job(job, session_factory=_Session, service_factory=lambda db: service)


@pytest.mark.parametrize("kind,args,method,expected", [
    ("trim", {"clip_id": "c1", "start_offset": 1.0, "end_offset": 0.5}, "trim_clip",
     (("task-1", "c1", 1.0, 0.5), {})),
    ("split", {"clip_id": "c1", "split_time": 2.0}, "split_clip", (("task-1", "c1", 2.0), {})),
    ("merge", {"clip_ids": ["a", "b"]}, "merge_clips", (("task-1", ["a", "b"]), {})),
    ("captions", {"clip_id": "c1", "caption_text": "Hi", "position": "top", "highlight_words": ["Hi"],
                  "font_size": 40, "position_y": 0.3}, "update_clip_captions",
     (("task-1", "c1", "Hi", "top", ["Hi"]), {"font_size": 40, "position_y": 0.3})),
])
async def test_the_job_runs_the_service_method_and_records_the_sync_body(redis, kind, args, method, expected):
    result = {"id": "c1"} if method in ("trim_clip", "update_clip_captions") else {"message": "ok"}
    service = SimpleNamespace(**{method: AsyncMock(return_value=result)})
    final = await _run(await _queued(kind, args), service)

    getattr(service, method).assert_awaited_once_with(*expected[0], **expected[1])
    stored = record()
    assert {k: v for k, v in stored.items() if k != "updated_at"} == final
    assert stored["status"] == "done" and stored["progress"] == 1.0
    assert stored["result"] == ({"clip": result} if method in ("trim_clip", "update_clip_captions") else result)
    assert stored["queued_at"] == 1000.0 and stored["clip_ids"] == ["c1"] and stored["task_id"] == "task-1"
    statuses = [message["clip_job_status"] for channel, message in FakeRedis.published if channel == "progress:task-1"]
    assert statuses == ["running", "done"]
    assert all(message["event_type"] == "clip_job" for _, message in FakeRedis.published)


async def test_the_settings_job_re_renders_then_stores_the_cleanup_settings(redis):
    FakeRedis.store["task_source:task-1"] = json.dumps({"url": "upload://x", "processing_job_id": "old"})
    cleanup = {"cut_long_pauses": True, "pause_threshold_ms": 600, "remove_filler_words": False, "filtered_words": []}
    service = SimpleNamespace(
        rerender_task_settings=AsyncMock(return_value={"url": "upload://x", **cleanup}),
        get_task_with_clips=AsyncMock(return_value={"id": "task-1", "clips": []}),
    )
    args = {"font_family": "Inter", "font_size": 40, "font_color": "#FFFFFF", "caption_template": "marker_circle",
            "include_broll": False, "cleanup_settings": cleanup, "edit_style": {"intensity": "bold"}}
    await _run(await _queued("settings", args), service)

    service.rerender_task_settings.assert_awaited_once_with(
        "task-1", "Inter", 40, "#FFFFFF", "marker_circle", cleanup,
        fallback_metadata={"url": "upload://x", "processing_job_id": "old"},
    )
    assert json.loads(FakeRedis.store["task_source:task-1"]) == {"url": "upload://x", **cleanup}
    assert record()["result"] == {"task": {"id": "task-1", "clips": []}, "message": "Task settings updated"}


@pytest.mark.parametrize("error,code,message", [
    (ValueError("Clip not found"), "INVALID_EDIT", "Clip not found"),
    (RuntimeError("ffmpeg exploded"), "CLIP_JOB_FAILED", fx_clip_jobs.GENERIC_FAILURE),
])
async def test_a_failed_edit_records_the_error_code(redis, error, code, message):
    service = SimpleNamespace(split_clip=AsyncMock(side_effect=error))
    final = await _run(await _queued("split", {"clip_id": "c1", "split_time": 1.0}), service)
    assert final["status"] == "failed"
    assert final["error"] == {"code": code, "message": message}
    assert record()["status"] == "failed"


async def test_a_geo_restricted_source_keeps_its_code(redis):
    from src.youtube_proxy import YouTubeGeoRestrictedError

    service = SimpleNamespace(update_clip_captions=AsyncMock(side_effect=YouTubeGeoRestrictedError()))
    final = await _run(await _queued("captions", {"clip_id": "c1", "caption_text": "x"}), service)
    assert final["error"]["code"] == "YOUTUBE_GEO_RESTRICTED"


async def test_a_job_over_its_budget_fails_with_a_timeout(redis, monkeypatch):
    monkeypatch.setattr(fx_clip_jobs, "job_budget_seconds", lambda _count: 0.05)

    async def slow(*_args):
        await asyncio.sleep(5)

    final = await _run(await _queued("trim", {"clip_id": "c1"}), SimpleNamespace(trim_clip=slow))
    assert final["error"]["code"] == "CLIP_JOB_TIMEOUT"


async def test_the_heartbeat_moves_progress_while_rendering(redis, monkeypatch):
    monkeypatch.setattr(fx_clip_jobs, "HEARTBEAT_SECONDS", 0.01)
    seen = []

    async def trim(*_args):
        await asyncio.sleep(0.1)
        seen.append(record()["progress"])
        return {"id": "c1"}

    await _run(await _queued("trim", {"clip_id": "c1"}), SimpleNamespace(trim_clip=trim))
    assert seen and fx_clip_jobs.PROGRESS_START < seen[0] < fx_clip_jobs.PROGRESS_CEILING
    assert record()["status"] == "done"            # no late heartbeat overwrites the result


async def test_a_finished_job_delivered_again_is_not_rendered_twice(redis):
    await clip_service.write_fx_clip_job_state(JOB_ID, {"job_id": JOB_ID, "task_id": "task-1", "status": "done"})
    service = SimpleNamespace(trim_clip=AsyncMock())
    await _run(_job("trim", {"clip_id": "c1"}), service)
    service.trim_clip.assert_not_awaited()


async def test_an_unknown_kind_fails_without_touching_the_task(redis):
    final = await _run(_job("explode", {}), SimpleNamespace())
    assert final["status"] == "failed" and final["error"]["code"] == "INVALID_EDIT"


async def test_the_running_record_carries_the_budget_for_the_stale_check(redis):
    seen = []
    original = clip_service.write_fx_clip_job_state

    async def spy(job_id, state):
        seen.append(dict(state))
        await original(job_id, state)

    service = SimpleNamespace(trim_clip=AsyncMock(return_value={"id": "c1"}))
    with patch.object(fx_clip_jobs, "write_fx_clip_job_state", spy):
        await _run(await _queued("trim", {"clip_id": "c1"}), service)
    running = [state for state in seen if state["status"] == "running"]
    assert running and running[-1]["budget_seconds"] == fx_clip_jobs.job_budget_seconds(1)


NOW = 1_700_000_000.0


@pytest.mark.parametrize("state,stale", [
    ({"status": "running", "updated_at": NOW, "started_at": NOW - 10}, False),
    ({"status": "running", "updated_at": NOW - 121, "started_at": NOW - 200}, True),
    ({"status": "running", "started_at": NOW - 1800 - 301, "budget_seconds": 1800.0, "updated_at": NOW}, True),
    ({"status": "running", "started_at": NOW - 3600, "updated_at": NOW}, False),   # default budget is 3 h
    ({"status": "queued", "queued_at": NOW - 3 * 3600 - 301}, True),
    ({"status": "queued", "queued_at": NOW - 60}, False),
    ({"status": "done", "updated_at": 1.0, "started_at": 1.0}, False),
    ({"status": "failed", "updated_at": 1.0}, False),
    ({"status": "running", "updated_at": "garbage"}, False),
])
def test_stale_job_error(state, stale):
    error = fx_clip_jobs.stale_job_error(state, now=NOW)
    assert (error == {"code": "WORKER_STOPPED", "message": fx_clip_jobs.WORKER_STOPPED_ERROR["message"]}) is stale
    assert (error is not None) is stale


def test_budget_is_max_1800_or_120_per_clip_inside_the_worker_timeout():
    assert fx_clip_jobs.job_budget_seconds(1) == 1800
    assert fx_clip_jobs.job_budget_seconds(20) == 2400
    assert fx_clip_jobs.job_budget_seconds(10_000) < fx_clip_jobs.FX_CLIP_JOB_TIMEOUT_SECONDS


def test_progress_estimate_is_monotone_and_bounded():
    values = [fx_clip_jobs.estimated_progress(t, 3) for t in (0, 10, 60, 600, 10_000)]
    assert values == sorted(values)
    assert values[0] == fx_clip_jobs.PROGRESS_START and values[-1] < fx_clip_jobs.PROGRESS_CEILING


def test_fx_clip_job_is_registered_on_the_video_worker_with_one_try():
    from src.workers.tasks import WorkerSettings

    (registered,) = [f for f in WorkerSettings.functions if getattr(f, "name", None) == "fx_clip_job"]
    assert registered.max_tries == 1
    assert registered.timeout_s == fx_clip_jobs.FX_CLIP_JOB_TIMEOUT_SECONDS
    assert WorkerSettings.queue_name == "supoclip_tasks"


async def test_rerender_task_settings_records_cleanup_under_the_lock(isolated_clip_edits):
    from src.services.task_service import TaskService

    service = TaskService(db="db")
    service.regenerate_all_clips_for_task = AsyncMock()
    service.task_repo.get_task_by_id = AsyncMock(return_value={
        "source_url": "upload://x", "source_type": "video_url", "output_format": "vertical", "add_subtitles": True,
    })
    service.task_repo.load_source_metadata = AsyncMock(return_value={"processing_job_id": "old", "clip_length": "auto"})
    service.task_repo.save_source_metadata = AsyncMock()
    cleanup = {"cut_long_pauses": True}

    written = await service.rerender_task_settings("task-1", None, 30, "#FFF", "default", cleanup)

    service.regenerate_all_clips_for_task.assert_awaited_once_with(
        "task-1", None, 30, "#FFF", "default", cleanup_settings=cleanup,
    )
    assert written == {"clip_length": "auto", "url": "upload://x", "source_type": "video_url",
                       "output_format": "vertical", "add_subtitles": True, "cut_long_pauses": True}
    service.task_repo.save_source_metadata.assert_awaited_once_with("db", "task-1", written)


# --------------------------------------------------------------------------- the lock (PostgreSQL)


async def test_a_legacy_edit_waits_for_a_running_fx_job_on_the_same_task(
    db_session, initialized_database, tmp_path, monkeypatch, redis,
):
    """The job takes the same row lock as ``@serialized_task_edit``: a legacy split of another clip of the task,
    from another session (an API process), waits until the FX trim commits."""
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import async_sessionmaker

    from src.clip_source_map import save_clip_edit_fx, save_clip_source_ranges
    from src.config import Config
    from src.edit_fx.contracts import EditStyle, FxPlan
    from src.repositories.clip_repository import ClipRepository
    from src.services.task_service import TaskService
    from tests.fixtures.factories import create_source, create_task, create_user

    owner = await create_user(db_session)
    source = await create_source(db_session, source_type="video_url", url="upload://input.mp4")
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"], status="completed")
    fx_file, legacy_file = tmp_path / "fx.mp4", tmp_path / "legacy.mp4"
    for path in (fx_file, legacy_file):
        path.write_bytes(b"clip")
        save_clip_source_ranges(path, [(10.0, 13.0)])
    plan = FxPlan.model_validate({"style": EditStyle(intensity="bold").model_dump(), "template": "marker_circle",
                                  "seed": 1, "edit_duration": 3.0})
    save_clip_edit_fx(fx_file, style={"intensity": "bold"}, beats=None, plan=plan.model_dump(mode="json"),
                      face_track=None, fx_level="full", applied_warp=[])
    fx_id = await ClipRepository.create_clip(db_session, task["id"], fx_file.name, str(fx_file), "00:10", "00:13",
                                             3, "caption", 1, "fx", 1, fx_meta=FX_META)
    legacy_id = await ClipRepository.create_clip(db_session, task["id"], legacy_file.name, str(legacy_file),
                                                 "00:10", "00:13", 3, "caption", 1, "legacy", 2)
    await ClipRepository.reorder_task_clips(db_session, task["id"])
    await db_session.commit()

    entered, release = threading.Event(), threading.Event()

    def blocking_trim(input_path, output_dir, start, end):
        entered.set()
        assert release.wait(10), "the test never released the FX render"
        out = Path(output_dir) / f"trim_{uuid4().hex[:8]}.mp4"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"trimmed")
        return out

    def fake_split(input_path, output_dir, split_time):
        paths = [Path(output_dir) / f"split_{uuid4().hex[:8]}_{i}.mp4" for i in range(2)]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"half")
        return tuple(paths)

    monkeypatch.setattr(clip_service, "trim_clip_file", blocking_trim)
    monkeypatch.setattr(clip_service, "split_clip_file", fake_split)
    monkeypatch.setattr(clip_service, "ffprobe_duration", lambda _p: 3.0)
    config = Config()
    config.temp_dir = str(tmp_path)
    sessions = async_sessionmaker(initialized_database, expire_on_commit=False)

    job = asyncio.create_task(fx_clip_jobs.run_fx_clip_job(
        {"job_id": JOB_ID, "task_id": task["id"], "user_id": owner["id"], "kind": "trim",
         "args": {"clip_id": fx_id, "start_offset": 0.5, "end_offset": 0.0}},
        session_factory=sessions, service_factory=lambda db: TaskService(db, config=config),
    ))
    assert await asyncio.to_thread(entered.wait, 10)
    async with sessions() as other:
        legacy = asyncio.create_task(TaskService(other, config=config).split_clip(task["id"], legacy_id, 1.0))
        try:
            done, _ = await asyncio.wait([legacy], timeout=0.3)
            assert not done, "the legacy edit passed the task lock while the FX job was rendering"
        finally:
            release.set()
        final = await asyncio.wait_for(job, 15)
        await asyncio.wait_for(legacy, 15)
    assert final["status"] == "done", final
    clips = await ClipRepository.get_clips_by_task(db_session, task["id"])
    assert len(clips) == 3
    trimmed = next(clip for clip in clips if clip["id"] == fx_id)
    assert trimmed["fx_meta"]["intensity"] == "bold"
