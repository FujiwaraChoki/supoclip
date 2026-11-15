"""Task API for dynamic edits (§5.2): create validation and gate, /tasks/edit-styles, settings, clip-job status.

Route tests run the real router with the services faked, so they pin decisions (status codes, what is stored, what
is queued) without PostgreSQL. tests/integration/test_task_edit_style.py covers the same flows on a database.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import tasks
from src.edit_fx.contracts import EditStyle, LISTED_FX_CAPTION_TEMPLATES
from src.repositories.task_repository import KEEP_EDIT_STYLE, TaskRepository
from src.services import clip_service
from src.services.clip_service import FxClipJobRequest, QueuedClipJob
from src.services.task_service import TaskService

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
JOB_ID = "0123456789abcdef0123456789abcdef"


class _Stop(Exception):
    """Raised by the billing fake: the request got past every check that must come before a charge."""


class _Billing:
    calls: list = []

    def __init__(self, db):
        type(self).calls.append(db)
        raise _Stop()


@pytest.fixture
def billing(monkeypatch):
    _Billing.calls = []
    monkeypatch.setattr(tasks, "BillingService", _Billing)
    return _Billing


@pytest.fixture
def flag(monkeypatch):
    """The dynamic_edits flag for the caller: ``flag.value`` (default off); calls in ``flag.mock``."""
    state = SimpleNamespace(value=False)

    async def enabled(db, key, *, platform, user_id=None):
        return state.value

    state.mock = AsyncMock(side_effect=enabled)
    monkeypatch.setattr(tasks, "feature_enabled", state.mock)
    return state


def _app(db=None) -> FastAPI:
    async def database():
        yield db

    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[tasks.get_db] = database
    app.state.queue_adapter = SimpleNamespace(name="fake-queue")
    return app


async def _request(method: str, path: str, *, db=None, **kwargs):
    async with AsyncClient(transport=ASGITransport(app=_app(db)), base_url="http://test") as client:
        return await client.request(method, path, **kwargs)


@pytest.fixture
def user(monkeypatch):
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(return_value="user-1"))
    return "user-1"


# --------------------------------------------------------------------------- POST /tasks/


async def _create(body: dict, headers: dict | None = None):
    return await _request("POST", "/tasks/", json={"source": {"url": YOUTUBE_URL}, **body}, headers=headers or {})


@pytest.mark.parametrize(
    "edit_style",
    [
        "bold",
        ["bold"],
        {"intensity": "nuclear"},
        {"intensity": "bold", "laser_eyes": True},
        {"intensity": "bold", "sfx": "yes"},
        {"intensity": "bold", "sfx": 1},
        {"intensity": "bold", "censor": "beep"},
        {"intensity": "bold", "grade": "sepia"},
        {"intensity": "bold", "music_mood": None},
    ],
)
async def test_create_rejects_a_bad_edit_style_with_422_before_any_charge(user, billing, flag, edit_style):
    response = await _create({"edit_style": edit_style})

    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["code"] == "INVALID_EDIT_STYLE"
    assert detail["message"]
    assert billing.calls == []
    flag.mock.assert_not_awaited()


@pytest.mark.parametrize(
    "body",
    [
        {"edit_style": {"intensity": "bold"}},
        {"edit_style": {"intensity": "subtle", "sfx": False}},
        {"edit_style": {"intensity": "max"}, "caption_template": "hormozi"},
        {"caption_template": "slam"},                                  # style only
        {"caption_template": "behind_head", "edit_style": {}},
        {"caption_template": "face_doodles", "edit_style": {"intensity": "off"}},
    ],
)
async def test_create_turning_fx_on_needs_the_flag(user, billing, flag, body):
    response = await _create(body, headers={"x-supoclip-client": "ios"})

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == {
        "code": "DYNAMIC_EDITS_NOT_AVAILABLE",
        "message": "Dynamic edits aren't available on your account yet.",
    }
    assert billing.calls == []
    flag.mock.assert_awaited_once()
    args, kwargs = flag.mock.await_args
    assert args[1] == "dynamic_edits"
    assert kwargs == {"platform": "ios", "user_id": "user-1"}


@pytest.mark.parametrize("body", [{"edit_style": {"intensity": "bold"}}, {"caption_template": "slam"}])
async def test_create_with_the_flag_on_goes_on_to_billing(user, billing, flag, body):
    flag.value = True

    response = await _create(body)

    assert response.status_code == 500   # the billing fake stops the request right there
    assert len(billing.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"edit_style": None},
        {"edit_style": {}},
        {"edit_style": {"intensity": "off"}},
        {"edit_style": {"intensity": "off", "sfx": False}},   # a stored preference with intensity off: still off
        {"caption_template": "mrbeast"},
        {"caption_template": "not-a-template"},
    ],
)
async def test_create_without_fx_never_asks_for_the_flag(user, billing, flag, body):
    response = await _create(body)

    assert response.status_code == 500
    assert len(billing.calls) == 1
    flag.mock.assert_not_awaited()


# --------------------------------------------------------------------------- GET /tasks/edit-styles


@pytest.mark.parametrize("enabled", [True, False])
async def test_edit_styles_reports_the_flag_and_the_choices(user, flag, monkeypatch, enabled):
    flag.value = enabled
    monkeypatch.setattr(tasks, "_reactions_available", lambda: False)

    response = await _request("GET", "/tasks/edit-styles", headers={"x-supoclip-client": "ios"})

    assert response.status_code == 200, response.text
    assert response.json() == {
        "enabled": enabled,
        "intensities": ["off", "subtle", "bold", "max"],
        "defaults": {
            "sfx": True, "music": True, "music_mood": "auto", "zooms": True, "shake": True, "transitions": True,
            "speed": True, "censor": "bleep_bar", "grade": "auto", "grain": False, "progress_bar": False,
            "loop_ending": False, "wait_for_it": True, "reactions": False,
        },
        "settings": [
            {"key": "sfx", "type": "bool", "default": True},
            {"key": "music", "type": "bool", "default": True},
            {"key": "music_mood", "type": "enum", "default": "auto",
             "choices": ["auto", "hype", "chill", "dramatic", "funny", "inspiring", "dark", "aggressive"]},
            {"key": "zooms", "type": "bool", "default": True},
            {"key": "shake", "type": "bool", "default": True},
            {"key": "transitions", "type": "bool", "default": True},
            {"key": "speed", "type": "bool", "default": True},
            {"key": "censor", "type": "enum", "default": "bleep_bar",
             "choices": ["off", "bleep", "bleep_bar", "bleep_pixelate"]},
            {"key": "grade", "type": "enum", "default": "auto",
             "choices": ["auto", "none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream"]},
            {"key": "grain", "type": "bool", "default": False},
            {"key": "progress_bar", "type": "bool", "default": False},
            {"key": "loop_ending", "type": "bool", "default": False},
            {"key": "wait_for_it", "type": "bool", "default": True},
            {"key": "reactions", "type": "bool", "default": False},
        ],
        "music_moods": ["hype", "chill", "dramatic", "funny", "inspiring", "dark", "aggressive"],
        "grades": ["none", "punchy", "warm", "teal_orange", "noir", "vhs", "dream"],
        "templates": list(LISTED_FX_CAPTION_TEMPLATES),
        "reactions_available": False,
    }
    assert flag.mock.await_args.kwargs == {"platform": "ios", "user_id": "user-1"}


async def test_edit_styles_needs_authentication(monkeypatch):
    from fastapi import HTTPException

    monkeypatch.setattr(tasks, "_get_user_id_from_headers", AsyncMock(side_effect=HTTPException(401, "no")))
    response = await _request("GET", "/tasks/edit-styles")
    assert response.status_code == 401


def test_reactions_need_a_configured_provider(monkeypatch):
    monkeypatch.delenv("REACTIONS_PROVIDER", raising=False)
    monkeypatch.delenv("GIPHY_API_KEY", raising=False)
    assert tasks._reactions_available() is False


# --------------------------------------------------------------------------- POST /tasks/{id}/settings


class _SettingsService:
    def __init__(self, record: dict, *, has_fx_clips: bool = False, queued: bool = False):
        self.task_repo = SimpleNamespace(
            get_task_by_id=AsyncMock(return_value=record),
            has_fx_clips=AsyncMock(return_value=has_fx_clips),
        )
        result = QueuedClipJob(job_id=JOB_ID, kind="settings") if queued else {"id": record["id"], "clips": []}
        self.update_task_settings = AsyncMock(return_value=result)


@pytest.fixture
def settings_route(monkeypatch, flag):
    """Runs the settings route against a stored task row; returns (response, service, saved-metadata mock)."""

    async def run(record: dict, body: dict, *, db=None, has_fx_clips=False, queued=False):
        record = {"id": "task-1", "user_id": "owner-1", "source_type": "youtube",
                  "source_url": "https://youtu.be/x", "include_broll": False, **record}
        service = _SettingsService(record, has_fx_clips=has_fx_clips, queued=queued)
        saved = AsyncMock()
        monkeypatch.setattr(tasks, "TaskService", lambda _db: service)
        monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(return_value=record))
        monkeypatch.setattr(tasks, "_load_task_source_metadata", AsyncMock(return_value={"output_format": "vertical"}))
        monkeypatch.setattr(tasks, "_save_task_source_metadata", saved)
        response = await _request("POST", "/tasks/task-1/settings", json=body, db=db)
        return response, service, saved

    return run


def _update_call(service):
    call = service.update_task_settings.await_args
    return call.args, call.kwargs


async def test_settings_without_fx_is_the_legacy_call(settings_route, flag):
    response, service, saved = await settings_route(
        {"caption_template": "default"}, {"caption_template": "neon", "apply_to_existing": True}
    )

    assert response.status_code == 200, response.text
    args, kwargs = _update_call(service)
    assert args[4] == "neon" and args[6] is True
    assert kwargs == {}   # no edit_style, no fx_job: byte-identical to before
    saved.assert_awaited_once()
    flag.mock.assert_not_awaited()


async def test_settings_omitted_style_keeps_the_stored_one(settings_route, flag):
    stored = {"intensity": "bold", "music_mood": "dark"}
    response, service, _ = await settings_route(
        {"caption_template": "hormozi", "edit_style": stored}, {"caption_template": "hormozi", "font_size": 30}
    )

    assert response.status_code == 200, response.text
    _, kwargs = _update_call(service)
    assert "edit_style" not in kwargs
    flag.mock.assert_not_awaited()


async def test_settings_null_style_also_keeps_the_stored_one(settings_route):
    response, service, _ = await settings_route(
        {"caption_template": "hormozi", "edit_style": {"intensity": "bold"}},
        {"caption_template": "hormozi", "edit_style": None},
    )
    assert response.status_code == 200
    assert "edit_style" not in _update_call(service)[1]


@pytest.mark.parametrize(
    ("stored", "expected"),
    [("slam", "slam"), ("stat_counter", "stat_counter"), ("mrbeast", "default"), (None, "default")],
)
async def test_settings_omitted_template_keeps_a_stored_fx_template(settings_route, flag, stored, expected):
    flag.value = True
    response, service, _ = await settings_route({"caption_template": stored}, {"font_size": 30})

    assert response.status_code == 200, response.text
    assert _update_call(service)[0][4] == expected


@pytest.mark.parametrize(
    ("record", "body"),
    [
        ({"caption_template": "default"}, {"caption_template": "default", "edit_style": {"intensity": "bold"}}),
        ({"caption_template": "default"}, {"caption_template": "slam"}),
        ({"caption_template": "default", "edit_style": {}}, {"caption_template": "kinetic_stack"}),
        # A stored preference with intensity off is off: switching it on is a transition.
        ({"caption_template": "hormozi", "edit_style": None},
         {"caption_template": "hormozi", "edit_style": {"intensity": "subtle"}}),
        # Style-only (FX template, intensity off) turning the intensity on.
        ({"caption_template": "slam"}, {"caption_template": "slam", "edit_style": {"intensity": "bold"}}),
        # Active style switching from a legacy template to an FX one.
        ({"caption_template": "hormozi", "edit_style": {"intensity": "bold"}},
         {"caption_template": "decode_glitch"}),
    ],
)
async def test_settings_turning_fx_on_is_gated(settings_route, flag, record, body):
    response, service, saved = await settings_route(record, {**body, "apply_to_existing": True})

    assert response.status_code == 403, response.text
    assert response.json()["detail"]["code"] == "DYNAMIC_EDITS_NOT_AVAILABLE"
    service.update_task_settings.assert_not_awaited()
    saved.assert_not_awaited()
    assert flag.mock.await_args.kwargs["user_id"] == "owner-1"


@pytest.mark.parametrize(
    ("record", "body", "stored_style"),
    [
        # Turning off.
        ({"caption_template": "slam", "edit_style": {"intensity": "bold"}},
         {"caption_template": "hormozi", "edit_style": {"intensity": "off"}}, {}),
        ({"caption_template": "hormozi", "edit_style": {"intensity": "bold"}},
         {"caption_template": "hormozi", "edit_style": {}}, {}),
        # Unchanged stored FX state: a font edit, or a different intensity, is never blocked by the kill switch.
        ({"caption_template": "slam", "edit_style": {"intensity": "bold"}},
         {"caption_template": "slam", "font_size": 40}, None),
        ({"caption_template": "slam", "edit_style": {"intensity": "bold"}},
         {"edit_style": {"intensity": "max", "music": False}}, {"intensity": "max", "music": False}),
        ({"caption_template": "slam", "edit_style": json.dumps({"intensity": "bold"})},
         {"caption_template": "marker_circle"}, None),
    ],
)
async def test_settings_turning_off_or_keeping_fx_is_never_gated(settings_route, flag, record, body, stored_style):
    response, service, _ = await settings_route(record, body)

    assert response.status_code == 200, response.text
    flag.mock.assert_not_awaited()
    _, kwargs = _update_call(service)
    if stored_style is None:
        assert "edit_style" not in kwargs
    else:
        assert kwargs["edit_style"] == stored_style


async def test_settings_rejects_a_bad_style_with_422(settings_route):
    response, service, _ = await settings_route({}, {"edit_style": {"intensity": "bold", "bogus": 1}})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "INVALID_EDIT_STYLE"
    service.update_task_settings.assert_not_awaited()


@pytest.mark.parametrize(
    ("record", "body"),
    [
        ({"caption_template": "default"}, {"edit_style": {"intensity": "bold"}}),
        ({"caption_template": "default"}, {"caption_template": "highlighter_swipe"}),
        ({"caption_template": "slam", "edit_style": {"intensity": "bold"}}, {"font_size": 40}),
    ],
)
async def test_settings_with_an_fx_target_returns_202_and_a_job(settings_route, flag, record, body):
    flag.value = True
    response, service, saved = await settings_route(record, {**body, "apply_to_existing": True}, queued=True)

    assert response.status_code == 202, response.text
    assert response.json() == {"status": "queued", "job_id": JOB_ID, "kind": "settings"}
    _, kwargs = _update_call(service)
    assert kwargs["fx_job"] == FxClipJobRequest(user_id="owner-1", queue=SimpleNamespace(name="fake-queue"))
    # The job stores the cleanup settings once it has re-rendered, like the synchronous path.
    saved.assert_not_awaited()


async def test_settings_queue_failure_after_storing_is_a_retryable_503(settings_route, flag, monkeypatch):
    flag.value = True
    original = _SettingsService.__init__

    def failing(self, *args, **kwargs):
        original(self, *args, **kwargs)
        self.update_task_settings = AsyncMock(side_effect=clip_service.FxClipJobQueueError("queue down"))

    monkeypatch.setattr(_SettingsService, "__init__", failing)
    response, _, saved = await settings_route(
        {"caption_template": "slam", "edit_style": {"intensity": "bold"}},
        {"font_size": 40, "apply_to_existing": True},
    )

    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "QUEUE_UNAVAILABLE"
    saved.assert_not_awaited()


async def test_settings_fx_clips_re_render_on_the_worker_even_into_a_legacy_look(settings_route):
    response, service, _ = await settings_route(
        {"caption_template": "hormozi"},
        {"caption_template": "hormozi", "apply_to_existing": True},
        db=object(), has_fx_clips=True, queued=True,
    )

    assert response.status_code == 202, response.text
    service.task_repo.has_fx_clips.assert_awaited_once()
    assert "fx_job" in _update_call(service)[1]


async def test_settings_legacy_clips_and_target_stay_synchronous_with_a_database(settings_route):
    response, service, saved = await settings_route(
        {"caption_template": "hormozi"},
        {"caption_template": "tiktok", "apply_to_existing": True},
        db=object(),
    )

    assert response.status_code == 200
    service.task_repo.has_fx_clips.assert_awaited_once()
    assert _update_call(service)[1] == {}
    saved.assert_awaited_once()


async def test_settings_without_apply_to_existing_only_stores(settings_route, flag):
    flag.value = True
    response, service, _ = await settings_route(
        {"caption_template": "default"}, {"edit_style": {"intensity": "bold"}, "caption_template": "slam"}
    )
    assert response.status_code == 200
    _, kwargs = _update_call(service)
    assert kwargs == {"edit_style": {"intensity": "bold"}}
    service.task_repo.has_fx_clips.assert_not_awaited()


# --------------------------------------------------------------------------- TaskService.update_task_settings


@pytest.fixture
def stored_jobs(monkeypatch):
    """Redis job records (in memory) and a queue fake that records the ARQ call."""
    records: dict[str, list[dict]] = {}

    async def write(job_id, state):
        records.setdefault(job_id, []).append(dict(state))

    monkeypatch.setattr(clip_service, "write_fx_clip_job_state", write)
    queue = SimpleNamespace(calls=[])

    async def enqueue_job(function_name, *args, **kwargs):
        queue.calls.append((function_name, args, kwargs))
        return "arq-id"

    queue.enqueue_job = enqueue_job
    return records, queue


async def test_service_fx_job_stores_then_queues_the_section_13_payload(isolated_clip_edits, stored_jobs):
    records, queue = stored_jobs
    service = TaskService(db=None)
    service.task_repo.update_task_settings = AsyncMock()
    service.regenerate_all_clips_for_task = AsyncMock()
    cleanup = {"cut_long_pauses": True, "pause_threshold_ms": 700, "remove_filler_words": False,
               "filtered_words": []}

    result = await service.update_task_settings(
        "task-1", "THEBOLDFONT", 30, "#FFFFFF", "slam", True, True, cleanup,
        edit_style={"intensity": "bold"}, fx_job=FxClipJobRequest(user_id="owner-1", queue=queue),
    )

    assert isinstance(result, QueuedClipJob) and result.kind == "settings"
    service.regenerate_all_clips_for_task.assert_not_awaited()
    repo_call = service.task_repo.update_task_settings.await_args
    assert repo_call.args[1:] == ("task-1", "THEBOLDFONT", 30, "#FFFFFF", "slam", True)
    assert repo_call.kwargs == {"edit_style": {"intensity": "bold"}}
    ((function, args, kwargs),) = queue.calls
    assert function == "fx_clip_job"
    assert kwargs == {"_job_id": f"fx_clip_job:{result.job_id}"}
    (job,) = args
    assert job == {
        "job_id": result.job_id, "task_id": "task-1", "user_id": "owner-1", "kind": "settings",
        "args": {"font_family": "THEBOLDFONT", "font_size": 30, "font_color": "#FFFFFF", "caption_template": "slam",
                 "include_broll": True, "cleanup_settings": cleanup, "edit_style": {"intensity": "bold"}},
    }
    (state,) = records[result.job_id]
    assert state["status"] == "queued" and state["progress"] == 0.0 and state["kind"] == "settings"
    assert state["task_id"] == "task-1" and state["user_id"] == "owner-1"
    assert abs(state["queued_at"] - time.time()) < 5


async def test_service_fx_job_with_the_stored_style_sends_none(isolated_clip_edits, stored_jobs):
    _, queue = stored_jobs
    service = TaskService(db=None)
    service.task_repo.update_task_settings = AsyncMock()

    result = await service.update_task_settings(
        "task-1", None, None, None, "slam", False, True, None, fx_job=FxClipJobRequest(user_id="u", queue=queue),
    )

    assert isinstance(result, QueuedClipJob)
    assert service.task_repo.update_task_settings.await_args.kwargs == {}
    assert queue.calls[0][1][0]["args"]["edit_style"] is None


async def test_service_fx_job_without_apply_to_existing_is_the_plain_store(isolated_clip_edits, stored_jobs):
    _, queue = stored_jobs
    service = TaskService(db=None)
    service.task_repo.update_task_settings = AsyncMock()
    service.get_task_with_clips = AsyncMock(return_value={"id": "task-1"})

    result = await service.update_task_settings(
        "task-1", None, None, None, "slam", False, False, None,
        edit_style={}, fx_job=FxClipJobRequest(user_id="u", queue=queue),
    )

    assert result == {"id": "task-1"}
    assert queue.calls == []
    assert service.task_repo.update_task_settings.await_args.kwargs == {"edit_style": {}}


async def test_enqueue_failure_marks_the_job_failed_and_raises(stored_jobs):
    records, _ = stored_jobs

    class _Down:
        @staticmethod
        async def enqueue_job(*_args, **_kwargs):
            raise ConnectionError("redis down")

    with pytest.raises(ConnectionError):
        await clip_service.enqueue_fx_clip_job(task_id="t", user_id="u", kind="captions", args={}, queue=_Down)
    ((job_id, states),) = records.items()
    assert [state["status"] for state in states] == ["queued", "failed"]
    assert states[-1]["error"]["code"] == "QUEUE_UNAVAILABLE"


async def test_service_fx_job_queue_failure_keeps_the_stored_settings_and_says_so(isolated_clip_edits, stored_jobs):
    records, _ = stored_jobs
    service = TaskService(db=None)
    service.task_repo.update_task_settings = AsyncMock()

    class _Down:
        @staticmethod
        async def enqueue_job(*_args, **_kwargs):
            raise ConnectionError("redis down")

    with pytest.raises(clip_service.FxClipJobQueueError) as caught:
        await service.update_task_settings(
            "task-1", None, None, None, "slam", False, True, None,
            fx_job=FxClipJobRequest(user_id="owner-1", queue=_Down),
        )
    assert isinstance(caught.value.__cause__, ConnectionError)
    service.task_repo.update_task_settings.assert_awaited_once()
    ((_, states),) = records.items()
    assert states[-1]["status"] == "failed"


async def test_enqueue_rejects_an_unknown_kind(stored_jobs):
    with pytest.raises(ValueError):
        await clip_service.enqueue_fx_clip_job(task_id="t", user_id="u", kind="explode", args={}, queue=stored_jobs[1])
    assert stored_jobs[0] == {}


def test_queued_clip_job_body_is_the_202_shape():
    assert QueuedClipJob(job_id="j", kind="trim").body() == {"status": "queued", "job_id": "j", "kind": "trim"}
    assert clip_service.fx_clip_job_key("j") == "fx_clip_job:j"
    assert clip_service.FX_CLIP_JOB_KINDS == ("settings", "captions", "trim", "split", "merge")


# --------------------------------------------------------------------------- GET /tasks/{id}/clip-jobs/{job_id}


@pytest.fixture
def clip_job(monkeypatch):
    state = SimpleNamespace(record=None, error=None)

    async def read(job_id):
        if state.error:
            raise state.error
        return state.record

    monkeypatch.setattr(tasks, "read_fx_clip_job_state", read)
    monkeypatch.setattr(tasks, "TaskService", lambda _db: SimpleNamespace())
    monkeypatch.setattr(tasks, "_require_task_owner", AsyncMock(return_value={"id": "task-1"}))

    async def get(job_id=JOB_ID, task_id="task-1"):
        return await _request("GET", f"/tasks/{task_id}/clip-jobs/{job_id}")

    state.get = get
    return state


def _record(**values):
    return {"job_id": JOB_ID, "task_id": "task-1", "kind": "settings", "status": "queued", "progress": 0.0,
            "queued_at": time.time(), **values}


async def test_clip_job_queued(clip_job):
    clip_job.record = _record()
    response = await clip_job.get()
    assert response.status_code == 200
    assert response.json() == {"status": "queued", "kind": "settings", "progress": 0.0}


async def test_clip_job_still_queued_after_15_minutes_reports_worker_unavailable(clip_job):
    clip_job.record = _record(queued_at=time.time() - 15 * 60 - 5)
    body = (await clip_job.get()).json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "WORKER_UNAVAILABLE"
    assert body["error"]["message"]


async def test_clip_job_running_progress_is_clamped(clip_job):
    clip_job.record = _record(status="running", progress=1.7, kind="trim",
                              queued_at=time.time() - 3600)   # an old job that is running is not stale
    assert (await clip_job.get()).json() == {"status": "running", "kind": "trim", "progress": 1.0}


async def test_clip_job_running_with_a_fresh_heartbeat_is_running(clip_job):
    clip_job.record = _record(status="running", progress=0.4, started_at=time.time() - 600,
                              budget_seconds=1800.0, updated_at=time.time() - 20)
    assert (await clip_job.get()).json() == {"status": "running", "kind": "settings", "progress": 0.4}


async def test_clip_job_running_without_a_heartbeat_for_two_minutes_reports_worker_stopped(clip_job):
    clip_job.record = _record(status="running", progress=0.4, started_at=time.time() - 300,
                              updated_at=time.time() - 125)
    body = (await clip_job.get()).json()
    assert body["status"] == "failed"
    assert body["error"]["code"] == "WORKER_STOPPED" and body["error"]["message"]


async def test_clip_job_running_past_its_budget_reports_worker_stopped(clip_job):
    # Heartbeats still arrive (a wedged render), but the job has outlived budget + margin.
    clip_job.record = _record(status="running", progress=0.9, started_at=time.time() - 1800 - 301,
                              budget_seconds=1800.0, updated_at=time.time() - 5)
    body = (await clip_job.get()).json()
    assert body["status"] == "failed" and body["error"]["code"] == "WORKER_STOPPED"


async def test_clip_job_done_after_a_stale_report_is_done(clip_job):
    # The stale verdict is never written back: a job that finishes after all is reported done.
    result = {"clip": {"id": "c1"}}
    clip_job.record = _record(status="done", progress=1.0, started_at=time.time() - 9000,
                              updated_at=time.time() - 9000, result=result)
    assert (await clip_job.get()).json()["status"] == "done"


async def test_clip_job_done_returns_the_synchronous_body(clip_job):
    result = {"task": {"id": "task-1"}, "message": "Task settings updated"}
    clip_job.record = _record(status="done", progress=0.9, result=result)
    assert (await clip_job.get()).json() == {"status": "done", "kind": "settings", "progress": 1.0, "result": result}


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        ({"code": "SOURCE_INCOMPLETE", "message": "Source missing"}, {"code": "SOURCE_INCOMPLETE",
                                                                       "message": "Source missing"}),
        (None, {"code": "CLIP_JOB_FAILED", "message": "The edit could not be rendered."}),
        ("boom", {"code": "CLIP_JOB_FAILED", "message": "The edit could not be rendered."}),
    ],
)
async def test_clip_job_failed_has_an_error(clip_job, error, expected):
    clip_job.record = _record(status="failed", error=error)
    body = (await clip_job.get()).json()
    assert body["status"] == "failed" and body["error"] == expected


@pytest.mark.parametrize(
    ("record", "job_id"),
    [(None, JOB_ID), (_record(task_id="someone-else"), JOB_ID), (_record(), "not-a-job"), (_record(), "A" * 32)],
)
async def test_clip_job_unknown_or_foreign_is_404(clip_job, record, job_id):
    clip_job.record = record
    assert (await clip_job.get(job_id=job_id)).status_code == 404


async def test_clip_job_redis_failure_is_503(clip_job):
    clip_job.error = ConnectionError("down")
    assert (await clip_job.get()).status_code == 503


# --------------------------------------------------------------------------- repository SQL


class _Nested:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeDb:
    def __init__(self, scalar=None):
        self.statements: list = []
        self.info: dict = {}
        self._scalar = scalar

    def begin_nested(self):
        return _Nested()

    async def execute(self, statement, params=None):
        self.statements.append((str(statement), dict(params or {})))
        scalar = self._scalar if self._scalar is not None else (params or {}).get("task_id")
        return SimpleNamespace(scalar=lambda: scalar)

    async def commit(self):
        return None

    async def rollback(self):
        return None


async def test_create_task_adds_edit_style_only_when_set():
    plain, styled, empty = _FakeDb(), _FakeDb(), _FakeDb()
    kwargs = dict(status="queued", caption_template="slam", source_duration_seconds=60)
    await TaskRepository.create_task(plain, "u", "s", **kwargs)
    await TaskRepository.create_task(empty, "u", "s", edit_style={}, **kwargs)
    await TaskRepository.create_task(
        styled, "u", "s", edit_style={"music_mood": "dark", "intensity": "bold"}, **kwargs
    )

    assert "edit_style" not in plain.statements[0][0] and "edit_style" not in plain.statements[0][1]
    assert empty.statements[0][0] == plain.statements[0][0]   # {} is off: the legacy statement
    sql, params = styled.statements[0]
    assert "edit_style" in sql and "CAST(:edit_style AS JSONB)" in sql
    assert params["edit_style"] == '{"intensity": "bold", "music_mood": "dark"}'
    assert params["workflow"] == "highlights" and params["workflow_options"] == "{}"


async def test_update_task_settings_writes_edit_style_only_when_passed():
    keep, on, off = _FakeDb(), _FakeDb(), _FakeDb()
    args = ("task-1", "THEBOLDFONT", 30, "#FFFFFF", "slam", False)
    await TaskRepository.update_task_settings(keep, *args)
    await TaskRepository.update_task_settings(on, *args, edit_style={"intensity": "subtle"})
    await TaskRepository.update_task_settings(off, *args, edit_style={})

    assert "edit_style" not in keep.statements[0][0]
    assert "edit_style = CAST(:edit_style AS JSONB)" in on.statements[0][0]
    assert on.statements[0][1]["edit_style"] == '{"intensity": "subtle"}'
    assert off.statements[0][1]["edit_style"] is None   # off is stored as NULL
    assert repr(KEEP_EDIT_STYLE) == "KEEP_EDIT_STYLE"


async def test_has_fx_clips():
    assert await TaskRepository.has_fx_clips(None, "task-1") is False
    assert await TaskRepository.has_fx_clips(_FakeDb(scalar=True), "task-1") is True
    db = _FakeDb(scalar=False)
    assert await TaskRepository.has_fx_clips(db, "task-1") is False
    assert "fx_meta IS NOT NULL" in db.statements[0][0]


def test_edit_style_to_json_is_what_create_stores():
    assert EditStyle(intensity="bold", music_mood="hype", progress_bar=True).to_json() == {
        "intensity": "bold", "music_mood": "hype", "progress_bar": True,
    }
    assert EditStyle(intensity="off", sfx=False).to_json() == {}
