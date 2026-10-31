"""Workflows on PostgreSQL: schema, task creation, read paths, gate and sharing."""

import hashlib
import hmac
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from src.models import GeneratedClip, Task
from src.repositories.clip_repository import ClipRepository
from src.repositories.task_repository import TaskRepository
from tests.fixtures.factories import create_source, create_task, create_user

MIGRATION = Path(__file__).parents[2] / "src/migrations/sql/20261031_0001_task_workflows.sql"
INIT_SQL = Path(__file__).parents[3] / "init.sql"
YOUTUBE_URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def headers_for(user_id: str) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = hmac.new(
        b"test-backend-auth-secret", f"{user_id}:{timestamp}".encode(), hashlib.sha256
    ).hexdigest()
    return {"x-supoclip-user-id": user_id, "x-supoclip-ts": timestamp, "x-supoclip-signature": signature}


@pytest.fixture
def youtube_duration(monkeypatch):
    """The YouTube preflight reports this many seconds (default 10 minutes)."""
    state = {"seconds": 600}

    async def info(_url):
        return {"duration": state["seconds"], "title": "Workflow source"}

    monkeypatch.setattr("src.api.routes.tasks.async_get_youtube_video_info", info)
    return state


@pytest.fixture
def workflows_for_all(app):
    app.state.config.workflows_access = "all"
    return app.state.config


async def create_body(client, headers, **body):
    # ASCII-escaped like browsers send it, so a lone surrogate stays valid JSON.
    return await client.post(
        "/tasks/",
        headers={**headers, "content-type": "application/json"},
        content=json.dumps({"source": {"url": YOUTUBE_URL}, **body}),
    )


async def task_row(db_session, task_id):
    return (
        await db_session.execute(
            text("SELECT workflow, workflow_options, workflow_plan FROM tasks WHERE id = :id"),
            {"id": task_id},
        )
    ).one()


async def minute_rows(db_session, user_id):
    return (
        await db_session.execute(
            text("SELECT COUNT(*) FROM minute_usage_events WHERE user_id = :user"), {"user": user_id}
        )
    ).scalar()


async def task_count(db_session, user_id):
    return (
        await db_session.execute(text("SELECT COUNT(*) FROM tasks WHERE user_id = :user"), {"user": user_id})
    ).scalar()


# --- schema -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_is_idempotent_on_top_of_init_db(db_session):
    statements = [part.strip() for part in MIGRATION.read_text().split(";") if part.strip()]
    assert len(statements) == 9
    try:
        for _ in range(2):
            for statement in statements:
                await db_session.execute(text(statement))
        columns = (
            await db_session.execute(
                text("""
                    SELECT table_name, column_name, is_nullable, column_default, data_type
                    FROM information_schema.columns
                    WHERE (table_name = 'tasks' AND column_name IN ('workflow', 'workflow_options', 'workflow_plan'))
                       OR (table_name = 'generated_clips' AND column_name IN ('workflow_role', 'part_number', 'workflow_meta'))
                """)
            )
        ).all()
        by_name = {row.column_name: row for row in columns}
        assert set(by_name) == {"workflow", "workflow_options", "workflow_plan", "workflow_role", "part_number", "workflow_meta"}
        assert by_name["workflow"].is_nullable == "NO" and "highlights" in by_name["workflow"].column_default
        assert by_name["workflow_options"].data_type == "jsonb" and by_name["workflow_options"].is_nullable == "NO"
        assert by_name["workflow_plan"].is_nullable == "YES"
        assert by_name["workflow_meta"].data_type == "jsonb" and by_name["workflow_meta"].is_nullable == "NO"
        assert by_name["part_number"].data_type == "integer"
        index = await db_session.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_tasks_user_workflow'")
        )
        assert "(user_id, workflow)" in index.scalar_one()
    finally:
        await db_session.rollback()


def test_init_sql_and_models_mirror_the_migration():
    init_sql = INIT_SQL.read_text()
    for fragment in (
        "workflow VARCHAR(20) NOT NULL DEFAULT 'highlights'",
        "CONSTRAINT tasks_workflow_check CHECK (workflow IN ('highlights', 'series', 'topic', 'recap', 'trailer'))",
        "workflow_options JSONB NOT NULL DEFAULT '{}'::jsonb",
        "workflow_plan JSONB",
        "workflow_role VARCHAR(20)",
        "part_number INTEGER",
        "workflow_meta JSONB NOT NULL DEFAULT '{}'::jsonb",
        "CREATE INDEX idx_tasks_user_workflow ON tasks(user_id, workflow)",
    ):
        assert fragment in init_sql, fragment
    assert {"workflow", "workflow_options", "workflow_plan"} <= set(Task.__table__.c.keys())
    assert {"workflow_role", "part_number", "workflow_meta"} <= set(GeneratedClip.__table__.c.keys())
    assert "tasks_workflow_check" in {constraint.name for constraint in Task.__table__.constraints}
    assert "highlights" in str(Task.__table__.c.workflow.server_default.arg)


@pytest.mark.asyncio
async def test_check_constraint_rejects_unknown_workflows(db_session):
    owner = await create_user(db_session)
    source = await create_source(db_session)
    task = await create_task(db_session, user_id=owner["id"], source_id=source["id"])
    stored = await task_row(db_session, task["id"])
    assert (stored.workflow, stored.workflow_options, stored.workflow_plan) == ("highlights", {}, None)
    with pytest.raises(DBAPIError) as error:
        await db_session.execute(text("UPDATE tasks SET workflow = 'bogus' WHERE id = :id"), {"id": task["id"]})
    assert error.value.orig.sqlstate == "23514"
    await db_session.rollback()


# --- creating tasks ------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "workflow", "options"),
    [
        ({}, "highlights", {}),
        ({"instructions": "  Skip the sponsor read "}, "highlights", {"instructions": "Skip the sponsor read"}),
        (
            {"workflow": "series", "workflow_options": {"part_count": "4", "topic": "dropped"}},
            "series",
            {"part_count": 4},
        ),
        ({"workflow": "series", "workflow_options": {"part_count": "auto"}}, "series", {}),
        (
            {"workflow": "topic", "workflow_options": {"topic": " building  a business ", "instructions": "calm"}},
            "topic",
            {"topic": "building a business", "instructions": "calm"},
        ),
        # Lone surrogates are valid JSON but not UTF-8: dropped, never a 500.
        ({"workflow": "topic", "workflow_options": {"topic": "pric\u00ading\ud800"}}, "topic", {"topic": "pricing"}),
        ({"workflow": "topic", "workflow_options": {"topic": "钱"}}, "topic", {"topic": "钱"}),
        ({"workflow": "recap", "clip_length": "long"}, "recap", {}),
        ({"workflow": "trailer", "instructions": "No spoilers\n\n\n\nplease"}, "trailer", {"instructions": "No spoilers\n\nplease"}),
    ],
)
async def test_create_persists_the_workflow_in_the_task_insert(
    client, app, db_session, auth_headers, youtube_duration, workflows_for_all, body, workflow, options
):
    await create_user(db_session, user_id="user-1")
    queued = app.state.queue_adapter.enqueued_jobs
    before = len(queued)

    response = await create_body(client, auth_headers, **body)

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    stored = await task_row(db_session, task_id)
    assert (stored.workflow, stored.workflow_options, stored.workflow_plan) == (workflow, options, None)
    # The worker reads the row: the job arguments are the same for every workflow.
    job = queued[before]
    assert job["function_name"] == "process_video_task"
    assert len(job["args"]) == 13
    assert job["args"][0] == task_id
    assert job["args"][-1] == body.get("clip_length", "auto")
    assert not any(isinstance(arg, str) and arg in {"series", "topic", "recap", "trailer"} for arg in job["args"])

    detail = (await client.get(f"/tasks/{task_id}", headers=auth_headers)).json()
    assert (detail["workflow"], detail["workflow_options"], detail["workflow_plan"]) == (workflow, options, None)
    assert detail["source_duration_seconds"] == 600
    assert detail["share_enabled"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("body", "seconds", "status", "code"),
    [
        ({"workflow": "bogus"}, 600, 422, "INVALID_WORKFLOW"),
        ({"workflow": "topic"}, 600, 422, "TOPIC_REQUIRED"),
        ({"workflow": "topic", "workflow_options": {"topic": "x" * 121}}, 600, 422, "TOPIC_TOO_LONG"),
        ({"workflow": "series", "workflow_options": {"part_count": 11}}, 600, 422, "INVALID_PART_COUNT"),
        ({"instructions": "x" * 501}, 600, 422, "INSTRUCTIONS_TOO_LONG"),
        ({"workflow": "series"}, 119, 422, "SOURCE_TOO_SHORT_FOR_SERIES"),
        ({"workflow": "series", "workflow_options": {"part_count": 8}}, 300, 422, "TOO_MANY_PARTS"),
        ({"workflow": "recap"}, 179, 422, "SOURCE_TOO_SHORT_FOR_RECAP"),
        ({"workflow": "trailer"}, 120, 422, "SOURCE_TOO_SHORT_FOR_TRAILER"),
        ({"workflow": "topic", "workflow_options": {"topic": "pricing"}}, 59, 422, "SOURCE_TOO_SHORT_FOR_TOPIC"),
    ],
)
async def test_rejections_happen_before_any_charge(
    client, app, db_session, youtube_duration, workflows_for_all, body, seconds, status, code
):
    user_id = str(uuid4())
    app.state.config.monetization_enabled = True
    await create_user(db_session, user_id=user_id, plan="pro", subscription_status="active")
    youtube_duration["seconds"] = seconds

    response = await create_body(client, headers_for(user_id), **body)

    assert response.status_code == status, response.text
    detail = response.json()["detail"]
    assert detail["code"] == code
    assert detail["message"]
    if code == "TOO_MANY_PARTS":
        assert detail["max_parts"] == 5
    assert await minute_rows(db_session, user_id) == 0
    assert await task_count(db_session, user_id) == 0

    # The same account is charged once the request is valid.
    youtube_duration["seconds"] = 600
    accepted = await create_body(client, headers_for(user_id), workflow="recap")
    assert accepted.status_code == 200, accepted.text
    assert await minute_rows(db_session, user_id) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "body",
    [
        {"workflow": "series"},
        {"workflow": "topic", "workflow_options": {"topic": "pricing"}},
        {"workflow": "recap"},
        {"workflow": "trailer"},
        {"instructions": "Focus on the hiring story"},
        {"workflow": "highlights", "workflow_options": {"instructions": "Focus"}},
    ],
)
async def test_gate_rejects_workflows_and_instructions_with_403_before_charging(
    client, app, db_session, youtube_duration, body
):
    user_id = str(uuid4())
    app.state.config.monetization_enabled = True
    app.state.config.workflows_access = "admins"
    app.state.config.workflows_allowed_emails = frozenset()
    await create_user(db_session, user_id=user_id, plan="pro", subscription_status="active")

    response = await create_body(client, headers_for(user_id), **body)

    assert response.status_code == 403, response.text
    assert response.json()["detail"] == {
        "code": "WORKFLOW_NOT_AVAILABLE",
        "message": "This option isn't available on your account yet.",
    }
    assert await minute_rows(db_session, user_id) == 0
    assert await task_count(db_session, user_id) == 0

    plain = await create_body(client, headers_for(user_id), workflow="highlights", instructions="   ")
    assert plain.status_code == 200, plain.text
    assert (await task_row(db_session, plain.json()["task_id"])).workflow == "highlights"


@pytest.mark.asyncio
async def test_gate_admits_admins_and_the_allowlist(client, app, db_session, youtube_duration):
    app.state.config.workflows_access = "admins"
    app.state.config.workflows_allowed_emails = frozenset({"beta@example.com"})
    admin, beta = str(uuid4()), str(uuid4())
    await create_user(db_session, user_id=admin, is_admin=True)
    await create_user(db_session, user_id=beta, email="Beta@Example.com")
    for user_id in (admin, beta):
        response = await create_body(client, headers_for(user_id), workflow="series")
        assert response.status_code == 200, response.text
    app.state.config.workflows_access = "off"
    response = await create_body(client, headers_for(admin), workflow="series")
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_workflows_route_reports_the_gate(client, app, db_session):
    user_id, admin = str(uuid4()), str(uuid4())
    await create_user(db_session, user_id=user_id)
    await create_user(db_session, user_id=admin, is_admin=True)
    app.state.config.workflows_access = "admins"
    app.state.config.workflows_allowed_emails = frozenset()

    closed = await client.get("/tasks/workflows", headers=headers_for(user_id))
    assert closed.status_code == 200, closed.text
    assert closed.json()["enabled"] is False
    assert {item["id"]: item["available"] for item in closed.json()["workflows"]} == {
        "highlights": True, "series": False, "topic": False, "recap": False, "trailer": False,
    }

    opened = (await client.get("/tasks/workflows", headers=headers_for(admin))).json()
    assert opened["enabled"] is True
    assert all(item["available"] for item in opened["workflows"])
    assert opened["topic_max_chars"] == 120 and opened["instructions_max_chars"] == 500
    series = next(item for item in opened["workflows"] if item["id"] == "series")
    assert series["part_count"] == {"min": 2, "max": 10}
    assert series["lengths"]["auto"] == [45, 150]

    app.state.config.workflows_access = "all"
    assert (await client.get("/tasks/workflows", headers=headers_for(user_id))).json()["enabled"] is True
    assert (await client.get("/tasks/workflows")).status_code == 401


@pytest.mark.asyncio
async def test_resume_enqueues_the_same_arguments(
    client, app, db_session, auth_headers, youtube_duration, workflows_for_all, monkeypatch
):
    await create_user(db_session, user_id="user-1")
    created = await create_body(
        client, auth_headers, workflow="series", workflow_options={"part_count": 3}, clip_length="short",
        instructions="Skip the intro",
    )
    assert created.status_code == 200, created.text
    task_id = created.json()["task_id"]
    create_job = app.state.queue_adapter.enqueued_jobs[-1]
    await db_session.execute(text("UPDATE tasks SET status = 'error' WHERE id = :id"), {"id": task_id})
    await db_session.commit()
    enqueue = AsyncMock(return_value="resumed-job")
    monkeypatch.setattr("src.api.routes.tasks.JobQueue.enqueue_processing_job", enqueue)

    resumed = await client.post(f"/tasks/{task_id}/resume", headers=auth_headers)

    assert resumed.status_code == 200, resumed.text
    args = enqueue.await_args.args
    assert args[0] == "process_video_task"
    assert args[1] == create_job["processing_mode"]
    assert args[2:] == create_job["args"]
    assert set(enqueue.await_args.kwargs) == {"_job_id"}
    stored = await task_row(db_session, task_id)
    assert (stored.workflow, stored.workflow_options) == ("series", {"part_count": 3, "instructions": "Skip the intro"})


# --- repositories and read paths ------------------------------------------------------


async def workflow_task(db_session, tmp_path, *, workflow="series", status="completed", user_id=None):
    owner = await create_user(db_session, user_id=user_id)
    source = await create_source(db_session, title="Workflow source")
    task_id = await TaskRepository.create_task(
        db_session, owner["id"], source["id"], status=status, source_duration_seconds=900,
        workflow=workflow, workflow_options={"part_count": 2} if workflow == "series" else {},
    )
    if status == "completed":
        await db_session.execute(text("UPDATE tasks SET completed_at = NOW() WHERE id = :id"), {"id": task_id})
        await db_session.commit()
    return owner, task_id


async def add_clip(db_session, tmp_path, task_id, order, *, commit=True, **kwargs):
    path = tmp_path / f"{task_id}-{order}.mp4"
    path.write_bytes(b"clip")
    clip_id = await ClipRepository.create_clip(
        db_session, task_id, path.name, str(path), "00:00", "01:00", 60.0, "text", 1.0, "why", order,
        **kwargs,
    )
    if commit:
        await db_session.commit()
    return clip_id


PLAN = {
    "version": "wf-v1",
    "workflow": "series",
    "language": "en",
    "coarse": False,
    "mode": "full",
    "title": "How we almost lost the company",
    "summary": "Private summary",
    "key_topics": ["money"],
    "parts": [
        {"part_number": 1, "title": "The bank calls", "delivers": "setup", "ends_on": "cliff", "post_caption": "caption",
         "first_unit": 1, "last_unit": 40, "ranges": [[1.0, 61.0]], "seconds": 60.0},
        {"part_number": 2, "title": "Twice", "delivers": "payoff", "ends_on": "end", "post_caption": "caption 2",
         "first_unit": 41, "last_unit": 80, "ranges": [[61.0, 121.0]], "seconds": 60.0},
    ],
    "notes": [{"code": "PART_COUNT_REDUCED", "params": {"requested": 3, "actual": 2}}],
    "stats": {"llm_calls": 4, "repairs": 0, "critique_fixes": 1, "plan_seconds": 12.5},
}
META = {
    "planned": True, "ranges": [[1.0, 61.0]], "junction_fades": [], "max_seconds": 155,
    "series_title": "How we almost lost the company", "part_count": 2, "title": "The bank calls",
    "delivers": "setup", "ends_on": "cliff", "post_caption": "caption", "first_unit": 1, "last_unit": 40,
}


@pytest.mark.asyncio
async def test_plan_save_overwrites_and_errors_merge(db_session, tmp_path):
    _, task_id = await workflow_task(db_session, tmp_path, status="processing")

    await TaskRepository.save_workflow_plan(db_session, task_id, PLAN)
    assert (await TaskRepository.get_task_by_id(db_session, task_id))["workflow_plan"] == PLAN

    await TaskRepository.save_workflow_plan(db_session, task_id, {"version": "wf-v1", "workflow": "series"})
    assert (await task_row(db_session, task_id)).workflow_plan == {"version": "wf-v1", "workflow": "series"}

    await TaskRepository.merge_workflow_plan_error(db_session, task_id, "no_story", {"mentions": ["12:30"]})
    assert (await task_row(db_session, task_id)).workflow_plan == {
        "version": "wf-v1", "workflow": "series", "error": {"code": "no_story", "details": {"mentions": ["12:30"]}},
    }

    await TaskRepository.save_workflow_plan(db_session, task_id, None)
    assert (await task_row(db_session, task_id)).workflow_plan is None
    await TaskRepository.merge_workflow_plan_error(db_session, task_id, "plan_failed", None)
    assert (await task_row(db_session, task_id)).workflow_plan == {"error": {"code": "plan_failed", "details": {}}}


@pytest.mark.asyncio
async def test_clip_workflow_fields_round_trip_and_upsert(db_session, tmp_path):
    _, task_id = await workflow_task(db_session, tmp_path)
    clip_id = await add_clip(
        db_session, tmp_path, task_id, 1, workflow_role="part", part_number=1, workflow_meta=META,
        hook_title="Part 1: The bank calls",
    )
    highlight_id = await add_clip(db_session, tmp_path, task_id, 2)

    clips = await ClipRepository.get_clips_by_task(db_session, task_id)
    assert [(c["workflow_role"], c["part_number"], c["workflow_meta"]) for c in clips] == [
        ("part", 1, META), (None, None, {}),
    ]
    one = await ClipRepository.get_clip_by_id(db_session, clip_id)
    assert (one["workflow_role"], one["part_number"], one["workflow_meta"], one["hook_title"]) == (
        "part", 1, META, "Part 1: The bank calls",
    )
    assert (await ClipRepository.get_clip_by_id(db_session, highlight_id))["workflow_meta"] == {}

    # Re-rendering the same order replaces the metadata along with the file.
    replaced = await add_clip(
        db_session, tmp_path, task_id, 1, workflow_role="part", part_number=1,
        workflow_meta={**META, "title": "Retitled"},
    )
    assert replaced == clip_id
    assert (await ClipRepository.get_clip_by_id(db_session, clip_id))["workflow_meta"]["title"] == "Retitled"


@pytest.mark.asyncio
async def test_clip_reads_and_writes_fall_back_before_the_migration(db_session, tmp_path):
    _, task_id = await workflow_task(db_session, tmp_path)
    await add_clip(db_session, tmp_path, task_id, 1, workflow_role="part", part_number=1, workflow_meta=META,
                   hook_title="Kept", virality_score=40)
    # DDL is rolled back at the end, restoring the current schema.
    await db_session.execute(text("ALTER TABLE generated_clips DROP COLUMN workflow_meta"))
    try:
        clip_id = await add_clip(db_session, tmp_path, task_id, 2, workflow_role="part", part_number=2,
                                 workflow_meta=META, hook_title="Still stored", virality_score=55, commit=False)
        clips = await ClipRepository.get_clips_by_task(db_session, task_id)
        assert [(c["hook_title"], c["virality_score"], c["workflow_meta"]) for c in clips] == [
            ("Kept", 40, {}), ("Still stored", 55, {}),
        ]
        one = await ClipRepository.get_clip_by_id(db_session, clip_id)
        assert (one["hook_title"], one["workflow_role"], one["part_number"]) == ("Still stored", None, None)
    finally:
        await db_session.rollback()


@pytest.mark.asyncio
async def test_task_reads_tolerate_and_inserts_refuse_a_missing_workflow_column(db_session, tmp_path):
    owner, task_id = await workflow_task(db_session, tmp_path)
    source = await create_source(db_session)
    # DDL is rolled back at the end, restoring the current schema. The edit
    # flag keeps repository commits from persisting the dropped column.
    db_session.info["supoclip_edit_task"] = task_id
    await db_session.execute(text("ALTER TABLE tasks DROP COLUMN workflow CASCADE"))
    try:
        task = await TaskRepository.get_task_by_id(db_session, task_id)
        assert task["workflow"] == "highlights"
        listed = await TaskRepository.get_user_tasks(db_session, owner["id"])
        assert [(row["id"], row["workflow"]) for row in listed] == [(task_id, "highlights")]
        # A plain highlights task never needed the column.
        plain = await TaskRepository.create_task(db_session, owner["id"], source["id"], source_duration_seconds=60)
        assert plain
        # A workflow task must not silently run as highlights.
        with pytest.raises(DBAPIError):
            await TaskRepository.create_task(
                db_session, owner["id"], source["id"], source_duration_seconds=60, workflow="recap",
            )
    finally:
        db_session.info.pop("supoclip_edit_task", None)
        await db_session.rollback()
    assert (await task_row(db_session, task_id)).workflow == "series"


@pytest.mark.asyncio
async def test_task_detail_and_list_expose_workflow_fields(client, db_session, tmp_path):
    owner, series_id = await workflow_task(db_session, tmp_path, user_id="user-1")
    await TaskRepository.save_workflow_plan(db_session, series_id, PLAN)
    await add_clip(db_session, tmp_path, series_id, 1, workflow_role="part", part_number=1, workflow_meta=META,
                   virality_score=0)
    # A later part with a score must not become the cover: series covers are Part 1.
    await add_clip(db_session, tmp_path, series_id, 2, workflow_role="part", part_number=2,
                   workflow_meta={**META, "title": "Twice"}, virality_score=90)
    source = await create_source(db_session)
    highlights_id = await TaskRepository.create_task(db_session, owner["id"], source["id"], status="completed",
                                                     source_duration_seconds=60)
    await add_clip(db_session, tmp_path, highlights_id, 1, virality_score=10)
    best = await add_clip(db_session, tmp_path, highlights_id, 2, virality_score=80)
    headers = headers_for(owner["id"])

    detail = (await client.get(f"/tasks/{series_id}", headers=headers)).json()
    assert detail["workflow"] == "series"
    assert detail["workflow_options"] == {"part_count": 2}
    assert detail["workflow_plan"] == PLAN
    assert [(c["part_number"], c["workflow_role"], c["workflow_meta"]["title"]) for c in detail["clips"]] == [
        (1, "part", "The bank calls"), (2, "part", "Twice"),
    ]

    listed = {row["id"]: row for row in (await client.get("/tasks/", headers=headers)).json()["tasks"]}
    series, highlights = listed[series_id], listed[highlights_id]
    assert (series["workflow"], series["workflow_title"], series["part_count"]) == (
        "series", "How we almost lost the company", 2,
    )
    assert series["cover_clip_id"] == detail["clips"][0]["id"]
    assert (highlights["workflow"], highlights["workflow_title"], highlights["part_count"]) == ("highlights", None, None)
    assert highlights["cover_clip_id"] == best


@pytest.mark.asyncio
async def test_share_exposes_only_the_public_workflow_subset(client, db_session, tmp_path):
    owner, task_id = await workflow_task(db_session, tmp_path, user_id="user-1")
    plan = {
        **PLAN,
        "topic": {"query": "money", "canonical": "Money", "coverage": "strong", "found": 2,
                  "suggested_topics": ["hiring"]},
        "points": [{"label": "Point", "ranges": [[1.0, 2.0]], "output_start": 0.0}],
        "beats": [{"role": "cold_open", "ranges": [[1.0, 2.0]], "output_start": 0.0}],
        "error": {"code": "plan_failed", "details": {}},
        "notes": [*PLAN["notes"], {"code": "SPONSOR_SKIPPED", "params": {}, "skipped": [[61.0, 90.0]]}],
    }
    await TaskRepository.save_workflow_plan(db_session, task_id, plan)
    meta = {**META, "points": [{"label": "Intro", "kind": "opener", "range_index": 0, "output_start": 0.0,
                                "droppable": False}]}
    await add_clip(db_session, tmp_path, task_id, 1, workflow_role="part", part_number=1, workflow_meta=meta)
    headers = headers_for(owner["id"])
    token = (await client.post(f"/tasks/{task_id}/share", headers=headers)).json()["share_token"]

    public = (await client.get(f"/tasks/shared/{token}")).json()

    assert public["workflow"] == "series"
    assert public["workflow_plan"] == {
        "version": "wf-v1",
        "workflow": "series",
        "title": "How we almost lost the company",
        "notes": [
            {"code": "PART_COUNT_REDUCED", "params": {"requested": 3, "actual": 2}},
            {"code": "SPONSOR_SKIPPED", "params": {}},
        ],
        "parts": [{"part_number": 1, "title": "The bank calls"}, {"part_number": 2, "title": "Twice"}],
        "points": [{"label": "Point"}],
        "beats": [{"role": "cold_open"}],
        "topic": {"query": "money", "canonical": "Money", "coverage": "strong", "found": 2},
    }
    clip = public["clips"][0]
    assert (clip["part_number"], clip["workflow_role"]) == (1, "part")
    assert clip["workflow_meta"] == {
        "title": "The bank calls",
        "ends_on": "cliff",
        "part_count": 2,
        "points": [{"label": "Intro", "kind": "opener", "range_index": 0, "output_start": 0.0}],
    }
    assert "Private summary" not in str(public) and "suggested_topics" not in str(public)
    assert "plan_failed" not in str(public) and "first_unit" not in str(public)
    assert "post_caption" not in str(public) and "ranges" not in str(public)
    assert "skipped" not in str(public)


@pytest.mark.asyncio
async def test_clip_ready_payload_carries_part_numbers(db_session, tmp_path):
    _, task_id = await workflow_task(db_session, tmp_path)
    clip_id = await add_clip(db_session, tmp_path, task_id, 1, workflow_role="part", part_number=1, workflow_meta=META)
    clip = await ClipRepository.get_clip_by_id(db_session, clip_id)
    assert clip["task_id"] == task_id
    assert (clip["part_number"], clip["workflow_role"]) == (1, "part")
