"""Dynamic edits on PostgreSQL: the edit_style column through create, read, dedupe, settings and clip jobs (§3.4, §5.2)."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from uuid import uuid4

import pytest
import redis.asyncio as redis
from sqlalchemy import text

from src.repositories.task_repository import TaskRepository
from src.services import clip_service
from tests.fixtures.factories import create_clip, create_source, create_task, create_user

YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
IOS = {"x-supoclip-client": "ios"}


def headers_for(user_id: str, **extra: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return {"x-supoclip-user-id": user_id, "x-supoclip-ts": timestamp, "x-supoclip-signature": signature, **extra}


@pytest.fixture(autouse=True)
def youtube_duration(monkeypatch):
    async def info(_url, **_kwargs):
        return {"duration": 600, "title": "Dynamic edits source"}

    monkeypatch.setattr("src.api.routes.tasks.async_get_youtube_video_info", info)


@pytest.fixture
async def dynamic_edits(feature_flag):
    """The dynamic_edits flag, off everywhere (admins bypass) unless a test switches it."""
    await feature_flag("dynamic_edits", web_enabled=False, ios_enabled=False, admins_bypass=True)
    return feature_flag


@pytest.fixture
def create_spy(monkeypatch):
    """Records the keyword arguments of every TaskRepository.create_task call (the INSERT shape)."""
    calls: list[dict] = []
    original = TaskRepository.create_task

    async def spy(*args, **kwargs):
        calls.append(dict(kwargs))
        return await original(*args, **kwargs)

    monkeypatch.setattr(TaskRepository, "create_task", staticmethod(spy))
    return calls


async def create(client, headers, url=YOUTUBE_URL, **body):
    return await client.post(
        "/tasks/",
        headers={**headers, "content-type": "application/json"},
        content=json.dumps({"source": {"url": url}, **body}),
    )


async def stored(db_session, task_id):
    return (
        await db_session.execute(
            text("SELECT edit_style, caption_template FROM tasks WHERE id = :id"), {"id": task_id}
        )
    ).one()


async def counts(db_session, user_id):
    tasks = (await db_session.execute(text("SELECT COUNT(*) FROM tasks WHERE user_id = :u"), {"u": user_id})).scalar()
    minutes = (
        await db_session.execute(text("SELECT COUNT(*) FROM minute_usage_events WHERE user_id = :u"), {"u": user_id})
    ).scalar()
    return tasks, minutes


def _video(n: int) -> str:
    return f"https://www.youtube.com/watch?v=dynamicEd{n:02d}"


# --------------------------------------------------------------------------- create


async def test_bold_create_stores_to_json_and_reads_it_back(client, app, db_session, dynamic_edits, create_spy):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await dynamic_edits("dynamic_edits", web_enabled=True)
    queued = app.state.queue_adapter.enqueued_jobs
    before = len(queued)

    response = await create(
        client, headers_for(user_id), url=_video(1), caption_template="slam",
        edit_style={"intensity": "bold", "music_mood": "hype", "progress_bar": True, "sfx": True},
    )

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    row = await stored(db_session, task_id)
    assert row.edit_style == {"intensity": "bold", "music_mood": "hype", "progress_bar": True}
    assert row.caption_template == "slam"
    assert create_spy[-1]["edit_style"] == {"intensity": "bold", "music_mood": "hype", "progress_bar": True}

    task = await TaskRepository.get_task_by_id(db_session, task_id)
    assert task["edit_style"] == {"intensity": "bold", "music_mood": "hype", "progress_bar": True}
    assert isinstance(task["edit_style"], dict)
    detail = (await client.get(f"/tasks/{task_id}", headers=headers_for(user_id))).json()
    assert detail["edit_style"] == {"intensity": "bold", "music_mood": "hype", "progress_bar": True}

    # The worker reads the row: the job arguments do not change.
    job = queued[before]
    assert job["function_name"] == "process_video_task"
    assert len(job["args"]) == 13
    assert not any(isinstance(arg, dict) and "intensity" in arg for arg in job["args"])


async def test_off_create_is_legacy(client, app, db_session, dynamic_edits, create_spy):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    queued = app.state.queue_adapter.enqueued_jobs

    responses = []
    for n, body in enumerate(({}, {"edit_style": {"intensity": "off", "sfx": False}}, {"edit_style": {}})):
        responses.append(await create(client, headers_for(user_id), url=_video(10 + n), **body))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]

    for response in responses:
        assert (await stored(db_session, response.json()["task_id"])).edit_style is None
        detail = (await client.get(f"/tasks/{response.json()['task_id']}", headers=headers_for(user_id))).json()
        assert detail["edit_style"] is None
    # The legacy INSERT shape: no edit_style argument at all.
    assert all("edit_style" not in call for call in create_spy[-3:])
    # Identical job arguments apart from the ids and the source URL.
    jobs = [job for job in queued if job["args"][0] in {r.json()["task_id"] for r in responses}]
    assert len(jobs) == 3
    shapes = [tuple(arg for i, arg in enumerate(job["args"]) if i not in (0, 1)) for job in jobs]
    assert shapes[0] == shapes[1] == shapes[2]


@pytest.mark.parametrize(
    "body",
    [{"edit_style": {"intensity": "bold"}}, {"caption_template": "behind_head"},
     {"caption_template": "hormozi", "edit_style": {"intensity": "max", "censor": "off"}}],
)
async def test_flag_off_is_403_before_any_charge(client, app, db_session, dynamic_edits, body):
    user_id = str(uuid4())
    app.state.config.monetization_enabled = True
    await create_user(db_session, user_id=user_id, plan="pro", subscription_status="active")

    blocked = await create(client, headers_for(user_id), url=_video(20), **body)

    assert blocked.status_code == 403, blocked.text
    assert blocked.json()["detail"]["code"] == "DYNAMIC_EDITS_NOT_AVAILABLE"
    assert await counts(db_session, user_id) == (0, 0)

    # On for iOS only: the same request from the app is accepted and charged once.
    await dynamic_edits("dynamic_edits", ios_enabled=True)
    allowed = await create(client, headers_for(user_id, **IOS), url=_video(20), **body)
    assert allowed.status_code == 200, allowed.text
    assert await counts(db_session, user_id) == (1, 1)


async def test_bad_edit_style_is_422_before_any_charge(client, app, db_session, dynamic_edits):
    user_id = str(uuid4())
    app.state.config.monetization_enabled = True
    await create_user(db_session, user_id=user_id, plan="pro", subscription_status="active")
    await dynamic_edits("dynamic_edits", web_enabled=True)

    for bad in ({"intensity": "extreme"}, {"intensity": "bold", "zoomz": True}, "bold"):
        response = await create(client, headers_for(user_id), url=_video(21), edit_style=bad)
        assert response.status_code == 422, response.text
        assert response.json()["detail"]["code"] == "INVALID_EDIT_STYLE"
    assert await counts(db_session, user_id) == (0, 0)


async def test_admins_bypass_the_flag(client, db_session, dynamic_edits):
    admin = str(uuid4())
    await create_user(db_session, user_id=admin, is_admin=True)

    response = await create(client, headers_for(admin), url=_video(22), edit_style={"intensity": "subtle"})
    assert response.status_code == 200, response.text
    edit_styles = (await client.get("/tasks/edit-styles", headers=headers_for(admin))).json()
    assert edit_styles["enabled"] is True

    await dynamic_edits("dynamic_edits", admins_bypass=False)
    blocked = await create(client, headers_for(admin), url=_video(23), edit_style={"intensity": "subtle"})
    assert blocked.status_code == 403
    assert (await client.get("/tasks/edit-styles", headers=headers_for(admin))).json()["enabled"] is False


# --------------------------------------------------------------------------- dedupe (AI-L6)


async def test_an_active_style_never_reuses_an_in_flight_task_with_another_style(client, db_session, dynamic_edits):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await dynamic_edits("dynamic_edits", web_enabled=True)
    headers = headers_for(user_id)
    url = _video(30)

    plain = (await create(client, headers, url=url)).json()["task_id"]
    bold = await create(client, headers, url=url, edit_style={"intensity": "bold"})
    assert bold.status_code == 200, bold.text
    bold_id = bold.json()["task_id"]
    assert bold_id != plain

    # The same style (and template) is the same job.
    again = await create(client, headers, url=url, edit_style={"intensity": "bold", "sfx": True})
    assert again.json()["task_id"] == bold_id
    # Another intensity, or another template, is a new task.
    subtle = (await create(client, headers, url=url, edit_style={"intensity": "subtle"})).json()["task_id"]
    assert subtle not in {plain, bold_id}
    slam = (await create(client, headers, url=url, edit_style={"intensity": "bold"},
                         caption_template="slam")).json()["task_id"]
    assert slam not in {plain, bold_id, subtle}
    style_only = (await create(client, headers, url=url, caption_template="slam")).json()["task_id"]
    assert style_only not in {plain, bold_id, subtle, slam}
    assert (await create(client, headers, url=url, caption_template="slam")).json()["task_id"] == style_only

    # The off path keeps today's check: the oldest in-flight task for the video.
    assert (await create(client, headers, url=url)).json()["task_id"] == plain
    assert (await counts(db_session, user_id))[0] == 5


async def test_style_dedupe_also_matches_tasks_saved_before_source_keys(db_session):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    url = _video(31)
    source = await create_source(db_session, url=url)
    task = await create_task(db_session, user_id=user_id, source_id=source["id"], status="queued")
    await db_session.execute(
        text("UPDATE tasks SET edit_style = CAST(:style AS JSONB), caption_template = 'slam' WHERE id = :id"),
        {"style": json.dumps({"intensity": "bold"}), "id": task["id"]},
    )
    await db_session.commit()
    from src.source_identity import source_key_for_url

    key = await source_key_for_url(db_session, user_id, url)
    found = await TaskRepository.find_active_source_task(
        db_session, user_id, key, edit_identity=({"intensity": "bold"}, "slam")
    )
    assert found is not None and found.id == task["id"]
    assert await TaskRepository.find_active_source_task(
        db_session, user_id, key, edit_identity=({"intensity": "bold"}, "default")
    ) is None
    assert await TaskRepository.find_active_source_task(
        db_session, user_id, key, edit_identity=({"intensity": "subtle"}, "slam")
    ) is None
    # A request without dynamic edits is not answered with the edited task...
    assert await TaskRepository.find_active_source_task(db_session, user_id, key) is None
    # ...but still joins an in-flight task made without them.
    plain = await create_task(db_session, user_id=user_id, source_id=source["id"], status="queued")
    await db_session.commit()
    found = await TaskRepository.find_active_source_task(db_session, user_id, key)
    assert found is not None and found.id == plain["id"]


# --------------------------------------------------------------------------- settings


async def completed_task(db_session, user_id, *, style=None, template="default"):
    source = await create_source(db_session)
    task = await create_task(db_session, user_id=user_id, source_id=source["id"])
    await db_session.execute(
        text("UPDATE tasks SET edit_style = CAST(:style AS JSONB), caption_template = :template WHERE id = :id"),
        {"style": json.dumps(style) if style else None, "template": template, "id": task["id"]},
    )
    await db_session.commit()
    return task["id"]


async def settings(client, user_id, task_id, **body):
    return await client.post(f"/tasks/{task_id}/settings", headers=headers_for(user_id), json=body)


async def test_settings_store_keep_and_clear_the_style(client, db_session, dynamic_edits):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    task_id = await completed_task(db_session, user_id, style={"intensity": "bold"}, template="slam")

    # Omitted style and template keep the stored ones; the flag is off and nothing turns on, so no 403.
    kept = await settings(client, user_id, task_id, font_size=40)
    assert kept.status_code == 200, kept.text
    assert tuple(await stored(db_session, task_id)) == ({"intensity": "bold"}, "slam")

    changed = await settings(client, user_id, task_id, edit_style={"intensity": "max", "music": False})
    assert changed.status_code == 200, changed.text
    assert (await stored(db_session, task_id)).edit_style == {"intensity": "max", "music": False}

    off = await settings(client, user_id, task_id, edit_style={"intensity": "off"}, caption_template="hormozi")
    assert off.status_code == 200, off.text
    assert tuple(await stored(db_session, task_id)) == (None, "hormozi")
    assert off.json()["task"]["edit_style"] is None

    # Back on, with the flag off: a transition, so 403 and nothing stored.
    blocked = await settings(client, user_id, task_id, edit_style={"intensity": "subtle"})
    assert blocked.status_code == 403
    assert blocked.json()["detail"]["code"] == "DYNAMIC_EDITS_NOT_AVAILABLE"
    blocked_template = await settings(client, user_id, task_id, caption_template="emoji_drop")
    assert blocked_template.status_code == 403
    assert tuple(await stored(db_session, task_id)) == (None, "hormozi")

    await dynamic_edits("dynamic_edits", web_enabled=True)
    on = await settings(client, user_id, task_id, edit_style={"intensity": "subtle"}, caption_template="emoji_drop")
    assert on.status_code == 200, on.text
    assert tuple(await stored(db_session, task_id)) == ({"intensity": "subtle"}, "emoji_drop")


async def test_settings_fx_target_with_apply_to_existing_queues_a_clip_job(client, app, db_session, dynamic_edits):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await dynamic_edits("dynamic_edits", web_enabled=True)
    task_id = await completed_task(db_session, user_id)
    queued = app.state.queue_adapter.enqueued_jobs
    before = len(queued)

    response = await settings(
        client, user_id, task_id, edit_style={"intensity": "bold"}, caption_template="marker_circle",
        apply_to_existing=True,
    )

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["status"] == "queued" and body["kind"] == "settings" and len(body["job_id"]) == 32
    assert tuple(await stored(db_session, task_id)) == ({"intensity": "bold"}, "marker_circle")
    job = queued[before]
    assert job["function_name"] == "fx_clip_job"
    (payload,) = job["args"]
    assert payload["job_id"] == body["job_id"]
    assert (payload["task_id"], payload["user_id"], payload["kind"]) == (task_id, user_id, "settings")
    assert payload["args"]["edit_style"] == {"intensity": "bold"}
    assert payload["args"]["caption_template"] == "marker_circle"
    assert job["kwargs"] == {"_job_id": f"fx_clip_job:{body['job_id']}"}

    try:
        status = await client.get(f"/tasks/{task_id}/clip-jobs/{body['job_id']}", headers=headers_for(user_id))
        assert status.status_code == 200, status.text
        assert status.json() == {"status": "queued", "kind": "settings", "progress": 0.0}

        # The worker side writes the same record.
        state = await clip_service.read_fx_clip_job_state(body["job_id"])
        await clip_service.write_fx_clip_job_state(
            body["job_id"], {**state, "status": "done", "progress": 1.0, "result": {"message": "ok"}}
        )
        done = (await client.get(f"/tasks/{task_id}/clip-jobs/{body['job_id']}", headers=headers_for(user_id)))
        assert done.json() == {"status": "done", "kind": "settings", "progress": 1.0, "result": {"message": "ok"}}

        # Another user cannot read it.
        stranger = str(uuid4())
        await create_user(db_session, user_id=stranger)
        denied = await client.get(f"/tasks/{task_id}/clip-jobs/{body['job_id']}", headers=headers_for(stranger))
        assert denied.status_code in (403, 404)
    finally:
        client_redis = redis.Redis(host=app.state.config.redis_host, port=app.state.config.redis_port)
        try:
            await client_redis.delete(clip_service.fx_clip_job_key(body["job_id"]))
        finally:
            await client_redis.aclose()


async def test_settings_on_a_task_with_fx_clips_queues_even_a_legacy_target(client, app, db_session, dynamic_edits):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    task_id = await completed_task(db_session, user_id, template="hormozi")
    clip = await create_clip(db_session, task_id=task_id)
    assert await TaskRepository.has_fx_clips(db_session, task_id) is False
    await db_session.execute(
        text("UPDATE generated_clips SET fx_meta = CAST(:meta AS JSONB) WHERE id = :id"),
        {"meta": json.dumps({"fx_level": "full", "timing": "word", "intensity": "bold", "template": "slam"}),
         "id": clip["id"]},
    )
    await db_session.commit()
    assert await TaskRepository.has_fx_clips(db_session, task_id) is True

    response = await settings(client, user_id, task_id, caption_template="hormozi", apply_to_existing=True)

    assert response.status_code == 202, response.text
    job_id = response.json()["job_id"]
    client_redis = redis.Redis(host=app.state.config.redis_host, port=app.state.config.redis_port)
    try:
        await client_redis.delete(clip_service.fx_clip_job_key(job_id))
    finally:
        await client_redis.aclose()


async def test_edit_styles_route_is_not_captured_as_a_task_id(client, db_session, dynamic_edits):
    user_id = str(uuid4())
    await create_user(db_session, user_id=user_id)
    await dynamic_edits("dynamic_edits", ios_enabled=True)

    web = await client.get("/tasks/edit-styles", headers=headers_for(user_id))
    ios = await client.get("/tasks/edit-styles", headers=headers_for(user_id, **IOS))

    assert web.status_code == 200, web.text
    assert web.json()["enabled"] is False and ios.json()["enabled"] is True
    assert web.json()["intensities"] == ["off", "subtle", "bold", "max"]
    assert web.json()["templates"] == ["highlighter_swipe", "emoji_drop", "behind_head"]   # the listed styles
