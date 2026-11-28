"""The agent's transcript handling, timeline, tools and background edit steps (no model, no database)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from src.agent import jobs, loop, timeline, tools
from src.agent.api_client import AgentAPIError
from src.agent.prompt import SYSTEM_PROMPT, context_block
from src.agent.timeutil import local_text, parse_local

ZURICH = ZoneInfo("Europe/Zurich")


def row(row_id, role, content, display=None, run_id="run-1"):
    return {"id": row_id, "role": role, "content": content, "display": display, "run_id": run_id, "created_at": None}


# ----------------------------------------------------------------------
# Transcript
# ----------------------------------------------------------------------
def test_api_messages_merges_turns_and_puts_tool_results_first():
    rows = [
        row(1, "user", [{"type": "text", "text": "hi"}]),
        row(2, "assistant", [{"type": "tool_use", "id": "t1", "name": "get_overview", "input": {}}]),
        row(3, "user", [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}]),
        row(4, "user", [{"type": "text", "text": "<context/>"}, {"type": "text", "text": "and now?"}]),
    ]
    messages = loop.api_messages(rows)
    assert [m["role"] for m in messages] == ["user", "assistant", "user"]
    assert messages[2]["content"][0]["type"] == "tool_result"
    assert [b.get("text") for b in messages[2]["content"][1:]] == ["<context/>", "and now?"]


def test_unanswered_tool_uses_finds_calls_without_results():
    rows = [
        row(1, "assistant", [
            {"type": "tool_use", "id": "a", "name": "x", "input": {}},
            {"type": "tool_use", "id": "b", "name": "y", "input": {}},
        ]),
        row(2, "user", [{"type": "tool_result", "tool_use_id": "a", "content": "ok"}]),
    ]
    assert [b["id"] for b in loop.unanswered_tool_uses(rows)] == ["b"]
    assert loop.unanswered_tool_uses(rows[:1] + [row(3, "user", [
        {"type": "tool_result", "tool_use_id": "a", "content": ""},
        {"type": "tool_result", "tool_use_id": "b", "content": ""},
    ])]) == []


def test_declined_results_answer_every_waiting_call():
    pending = {
        "results": [{"type": "tool_result", "tool_use_id": "done", "content": "ok"}],
        "cards": {"done": {"name": "find_clips", "ok": True}},
        "waiting": [{"id": "w1", "name": "publish_now", "input": {}}],
    }
    results, cards = loop.declined_results(pending)
    assert [r["tool_use_id"] for r in results] == ["done", "w1"]
    assert results[1]["is_error"] is True
    assert cards["w1"]["declined"] is True


def test_input_brief_hides_uploads_and_long_fields():
    brief = loop.input_brief("create_project", {"url": "upload://s3/abc", "caption": "long", "workflow": "series"})
    assert brief == {"url": "upload", "workflow": "series"}


# ----------------------------------------------------------------------
# Timeline
# ----------------------------------------------------------------------
def test_timeline_shows_people_text_steps_and_notices():
    rows = [
        row(1, "user", [{"type": "text", "text": "<context/>"}, {"type": "text", "text": "Plan my week"}],
            {"kind": "user", "text": "Plan my week"}),
        row(2, "assistant", [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "tool_use", "id": "t1", "name": "find_clips", "input": {"query": "ads"}},
        ]),
        row(3, "user", [{"type": "tool_result", "tool_use_id": "t1", "content": "{}"}],
            {"kind": "tool_results", "cards": {"t1": {"name": "find_clips", "ok": True, "card": {"type": "clips", "items": []}}}}),
        row(4, "assistant", [{"type": "text", "text": "Found them."}]),
        row(5, "user", [{"type": "text", "text": "<supoclip_notice/>"}],
            {"kind": "notice", "notice": {"kind": "project_finished", "title": "Ep 1", "status": "completed"}}),
    ]
    items = timeline.build(rows, None)
    assert [i["type"] for i in items] == ["user", "tool", "text", "notice"]
    assert items[0]["text"] == "Plan my week"
    assert items[1]["status"] == "done" and items[1]["card"]["type"] == "clips"
    assert items[3]["notice"]["title"] == "Ep 1"


def test_timeline_marks_actions_waiting_for_approval():
    rows = [
        row(1, "user", [{"type": "text", "text": "post it"}], {"kind": "user", "text": "post it"}),
        row(2, "assistant", [{"type": "tool_use", "id": "p1", "name": "publish_now", "input": {"clip_id": "c"}}]),
    ]
    run = {"status": "awaiting_approval", "pending": {"cards": {}, "items": [{"id": "p1", "tool": "publish_now", "input": {}}]}}
    items = timeline.build(rows, run)
    assert items[1]["status"] == "awaiting_approval"
    assert items[1]["approval"]["tool"] == "publish_now"
    # Without an open turn the same call reads as interrupted, never as done.
    assert timeline.build(rows, None)[1]["status"] == "interrupted"


# ----------------------------------------------------------------------
# Time and prompt
# ----------------------------------------------------------------------
def test_local_times_become_utc_in_the_persons_zone():
    assert parse_local("2026-11-24T18:00", ZURICH) == datetime(2026, 11, 24, 17, 0, tzinfo=timezone.utc)
    assert parse_local("2026-11-24T18:00Z", ZURICH) == datetime(2026, 11, 24, 18, 0, tzinfo=timezone.utc)
    assert local_text("2026-11-24T17:00:00+00:00", ZURICH) == "Tue 2026-11-24 18:00"
    with pytest.raises(ValueError):
        parse_local("next tuesday", ZURICH)


def test_context_block_carries_what_varies_and_the_system_prompt_does_not():
    block = context_block(
        timezone_name="Europe/Zurich", locale="de", memories=["Posts at 7pm"],
        attachments=[{"name": "ep.mp4", "url": "upload://local/x"}], first_name="Sam",
    )
    assert "Europe/Zurich" in block and "Posts at 7pm" in block and "upload://local/x" in block and "Sam" in block
    # Anything that changes per request stays out of the cached system prompt.
    assert "{" not in SYSTEM_PROMPT and "Europe/" not in SYSTEM_PROMPT


def test_tool_definitions_are_valid_schemas():
    names = [definition["name"] for definition in tools.TOOL_DEFINITIONS]
    assert len(names) == len(set(names))
    for definition in tools.TOOL_DEFINITIONS:
        schema = definition["input_schema"]
        assert schema["type"] == "object" and schema["additionalProperties"] is False
        assert set(schema["required"]) <= set(schema["properties"])
    approvals = {tool.name for tool in tools.TOOLS if tool.needs_approval({})}
    assert approvals == {"publish_now", "delete_project"}


# ----------------------------------------------------------------------
# Tools
# ----------------------------------------------------------------------
class FakeAPI:
    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls: list[tuple[str, str, object]] = []

    async def request(self, method, path, *, json_body=None, params=None, headers=None, timeout=None):
        self.calls.append((method, path, json_body if json_body is not None else params))
        answer = self.responses.get((method, path))
        if isinstance(answer, Exception):
            raise answer
        return answer if answer is not None else {}

    async def get(self, path, **params):
        return await self.request("GET", path, params=params)

    async def post(self, path, body=None, **kwargs):
        return await self.request("POST", path, json_body=body if body is not None else {})

    async def patch(self, path, body):
        return await self.request("PATCH", path, json_body=body)

    async def delete(self, path):
        return await self.request("DELETE", path)


def ctx(api) -> tools.ToolContext:
    return tools.ToolContext(
        app=None, api=api, user_id="user-1", team_id=None, conversation_id="conv-1", run_id="run-1", tz=ZURICH,
    )


@pytest.mark.asyncio
async def test_unknown_tools_and_bad_input_come_back_as_errors():
    outcome = await tools.run_tool(ctx(FakeAPI()), "drop_database", {})
    assert not outcome.ok
    outcome = await tools.run_tool(ctx(FakeAPI()), "get_project", {"project_id": "../etc"})
    assert not outcome.ok and "id" in outcome.model_text()


@pytest.mark.asyncio
async def test_show_options_builds_cards_and_reads_slot_times_locally():
    outcome = await tools.run_tool(ctx(FakeAPI()), "show_options", {
        "style": "slots",
        "options": [{"label": "Tue", "when": "2026-11-24T18:00", "recommended": True, "icon": "nope"}],
    })
    assert outcome.ok
    option = outcome.card["options"][0]
    assert option["when"] == "2026-11-24T17:00:00+00:00"
    assert option["reply"] == "Tue" and option["recommended"] is True and "icon" not in option
    too_many = await tools.run_tool(ctx(FakeAPI()), "show_options", {"style": "chips", "options": [{"label": "x"}] * 13})
    assert not too_many.ok


@pytest.mark.asyncio
async def test_schedule_post_refuses_past_times_and_resolves_the_account():
    api = FakeAPI({
        ("GET", "/social/connections"): {"connections": [
            {"id": "acc-yt", "provider": "youtube", "status": "active", "display_name": "Chan"},
        ]},
        ("POST", "/social/posts"): {"post": {"id": "post-1", "provider": "youtube", "status": "scheduled", "privacy_level": "private",
                                             "scheduled_for": "2099-01-01T17:00:00+00:00", "account": {}}},
    })
    past = await tools.run_tool(ctx(api), "schedule_post", {"project_id": "p", "clip_id": "c", "when": "2020-01-01T10:00"})
    assert not past.ok and "passed" in past.model_text()
    done = await tools.run_tool(ctx(api), "schedule_post", {
        "project_id": "p", "clip_id": "c", "when": "2099-01-01T18:00", "platform": "youtube",
        "caption": "Hook first", "hashtags": ["#a", "b"],
    })
    assert done.ok and done.card["type"] == "post"
    assert done.content["scheduled"]["privacy"] == "private"
    assert done.card["post"]["privacy_level"] == "private"
    method, path, body = api.calls[-1]
    assert (method, path) == ("POST", "/social/posts")
    assert body["social_account_id"] == "acc-yt"
    assert body["scheduled_for"] == "2099-01-01T17:00:00+00:00"
    assert body["caption"] == "Hook first"
    assert "privacy_level" not in body


def test_bulk_scheduling_prompt_requires_confirmation_and_preserves_explicit_requests():
    confirmation_rule = next(line for line in SYSTEM_PROMPT.splitlines() if "multi-day calendar fill" in line)
    assert "more than two posts or any multi-day calendar fill" in confirmation_rule
    assert "lineup (clips, platforms and times) and posting frequency with show_options, then wait for confirmation" in confirmation_rule
    assert "current message explicitly specifies both the platforms and the cadence or posting dates" in confirmation_rule
    assert "existing posts, connected accounts, and remembered preferences do not count" in confirmation_rule
    assert "Schedule a single explicitly requested clip at the specified time and platform without asking again" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_create_project_applies_saved_font_and_honors_explicit_layout(monkeypatch):
    row = SimpleNamespace(
        default_font_family="Inter", default_font_size=24, default_font_color="#FFFFFF",
        default_clip_length="short", create_settings={"outputFormat": "vertical"},
    )
    db = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(fetchone=lambda: row)))
    monkeypatch.setattr(tools, "AsyncSessionLocal", lambda: nullcontext(db))
    api = FakeAPI({("POST", "/tasks/"): {"task_id": "project-1"}})

    outcome = await tools.run_tool(ctx(api), "create_project", {
        "url": "https://example.com/video", "title": "Demo", "output_format": "vertical_split",
    })

    assert outcome.ok
    [(method, path, body)] = api.calls
    assert (method, path) == ("POST", "/tasks/")
    assert body["font_options"] == {"font_family": "Inter", "font_size": 24, "font_color": "#FFFFFF"}
    assert body["output_format"] == "vertical_split"
    assert body["clip_length"] == "short"


@pytest.mark.asyncio
async def test_two_accounts_on_one_platform_need_an_id():
    api = FakeAPI({("GET", "/social/connections"): {"connections": [
        {"id": "a1", "provider": "tiktok", "status": "active", "display_name": "One"},
        {"id": "a2", "provider": "tiktok", "status": "active", "display_name": "Two"},
    ]}})
    outcome = await tools.run_tool(ctx(api), "schedule_post", {
        "project_id": "p", "clip_id": "c", "when": "2099-01-01T18:00", "platform": "tiktok",
    })
    assert not outcome.ok and "a1" in outcome.model_text() and "a2" in outcome.model_text()


@pytest.mark.asyncio
async def test_api_errors_reach_the_model_readably():
    api = FakeAPI({("GET", "/tasks/p1"): AgentAPIError(404, "Task not found")})
    outcome = await tools.run_tool(ctx(api), "get_project", {"project_id": "p1"})
    assert not outcome.ok and "Task not found" in outcome.model_text() and "404" in outcome.model_text()


@pytest.mark.asyncio
async def test_posting_times_skip_busy_slots_and_spread_per_day():
    tomorrow = (datetime.now(timezone.utc).astimezone(ZURICH) + timedelta(days=1)).date()
    busy = datetime(tomorrow.year, tomorrow.month, tomorrow.day, 12, 30, tzinfo=ZURICH).astimezone(timezone.utc)
    api = FakeAPI({
        ("GET", "/social/posts"): {"posts": [
            {"id": "x", "provider": "tiktok", "status": "scheduled", "scheduled_for": busy.isoformat()},
        ]},
    })
    outcome = await tools.run_tool(ctx(api), "suggest_posting_times", {
        "count": 3, "platform": "tiktok", "start_date": tomorrow.isoformat(),
    })
    assert outcome.ok
    slots = [datetime.fromisoformat(s["when"]) for s in outcome.content["slots"]]
    assert len(slots) == 3
    # One per day by default, and never within three hours of the busy 12:30 post.
    assert len({slot.date() for slot in slots}) == 3
    first_day = [slot for slot in slots if slot.date() == tomorrow]
    assert all(abs((slot.replace(tzinfo=ZURICH) - busy.astimezone(ZURICH)).total_seconds()) >= 3 * 3600 for slot in first_day)


# ----------------------------------------------------------------------
# Background studio edit
# ----------------------------------------------------------------------
@pytest.fixture
def job_store(monkeypatch):
    updates: list[dict] = []
    published: list[dict] = []

    async def update_job(job_id, **fields):
        updates.append(fields)

    async def publish(conversation_id, event, data):
        published.append(data)

    monkeypatch.setattr(jobs.store, "update_job", update_job)
    monkeypatch.setattr(jobs.events, "publish", publish)
    return updates, published


def edit_job(step, payload=None, **extra):
    return {
        "id": "job-1", "conversation_id": "conv-1", "user_id": "user-1", "team_id": None, "kind": "clip_edit",
        "status": "running", "step": step, "task_id": "task-1", "clip_id": "clip-1", "attempts": 0,
        "payload": {"instruction": "cut the ums", "render": True, **(payload or {})}, **extra,
    }


@pytest.mark.asyncio
async def test_edit_prepares_the_studio_when_it_is_not_open_yet(job_store):
    updates, _ = job_store
    api = FakeAPI({("GET", "/tasks/task-1/clips/clip-1/editor"): {"status": "unprepared"}})
    await jobs._step_edit(None, api, edit_job("start"))
    assert ("POST", "/tasks/task-1/clips/clip-1/editor/prepare", {}) in api.calls
    assert updates[-1]["step"] == "preparing"


@pytest.mark.asyncio
async def test_edit_saves_the_ai_draft_and_starts_a_render(job_store):
    updates, published = job_store
    base = "/tasks/task-1/clips/clip-1/editor"
    api = FakeAPI({
        ("GET", f"{base}/ai/aaaa"): {"status": "completed", "changed": True, "summary": "Cut 3 ums", "document": {"v": 2}},
        ("PATCH", base): {"revision": 5},
        ("POST", f"{base}/exports"): {"id": "exp-1"},
    })
    await jobs._step_edit(None, api, edit_job("ai", {"ai_job": "aaaa", "revision": 4, "basis": "clip_1.mp4"}))
    saved = next(call for call in api.calls if call[0] == "PATCH")
    assert saved[2] == {"basis": "clip_1.mp4", "revision": 4, "document": {"v": 2}}
    assert updates[-1]["step"] == "render" and updates[-1]["payload"]["export_job"] == "exp-1"
    assert published[-1]["job"]["step"] == "render"


@pytest.mark.asyncio
async def test_edit_redoes_on_a_newer_draft_after_a_conflict(job_store):
    updates, _ = job_store
    base = "/tasks/task-1/clips/clip-1/editor"
    api = FakeAPI({
        ("GET", f"{base}/ai/aaaa"): {"status": "completed", "changed": True, "document": {"v": 2}},
        ("PATCH", base): AgentAPIError(409, "edited in another tab"),
    })
    await jobs._step_edit(None, api, edit_job("ai", {"ai_job": "aaaa", "revision": 4}))
    assert updates[-1]["step"] == "edit" and updates[-1]["bump_attempts"] is True


@pytest.mark.asyncio
async def test_failed_ai_edit_fails_the_job(job_store):
    api = FakeAPI({("GET", "/tasks/task-1/clips/clip-1/editor/ai/aaaa"): {"status": "failed", "error": "Too vague"}})
    with pytest.raises(jobs.JobFailed, match="Too vague"):
        await jobs._step_edit(None, api, edit_job("ai", {"ai_job": "aaaa"}))


# ----------------------------------------------------------------------
# Attachments
# ----------------------------------------------------------------------
def test_attachments_become_blocks_claude_can_read():
    import base64

    from src.agent.attachments import content_blocks, parse_attachments, previews

    png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode()
    pdf = base64.b64encode(b"%PDF-1.4 fake").decode()
    parsed = parse_attachments([
        {"kind": "video", "url": "upload://s3/abc/video.mp4", "name": "ep12.mp4"},
        {"url": "upload://local/old.mp4", "name": "old client"},
        {"kind": "image", "name": "stats.png", "media_type": "image/png", "data": png, "preview": f"data:image/png;base64,{png}"},
        {"kind": "pdf", "name": "brief.pdf", "data": pdf},
        {"kind": "text", "name": "script.md", "text": "# Hook\nOpen on the punchline."},
    ])
    assert [a["kind"] for a in parsed] == ["video", "video", "image", "pdf", "text"]
    blocks = content_blocks(parsed)
    assert [b["type"] for b in blocks] == ["image", "document", "document"]
    assert blocks[0]["source"] == {"type": "base64", "media_type": "image/png", "data": png}
    assert blocks[1]["source"]["media_type"] == "application/pdf" and blocks[1]["title"] == "brief.pdf"
    assert blocks[2]["source"] == {"type": "text", "media_type": "text/plain", "data": "# Hook\nOpen on the punchline."}
    shown = previews(parsed)
    # The chat keeps names, sizes and the small image preview, never the files themselves.
    assert "data" not in str([{k: v for k, v in p.items() if k != "preview"} for p in shown])
    assert shown[2]["preview"].startswith("data:image/png;base64,")


def test_attachments_refuse_bad_or_oversized_files():
    import base64

    from src.agent.attachments import AttachmentError, parse_attachments

    with pytest.raises(AttachmentError, match="image type"):
        parse_attachments([{"kind": "image", "name": "x.bmp", "media_type": "image/bmp", "data": "AAAA"}])
    with pytest.raises(AttachmentError, match="couldn't be read"):
        parse_attachments([{"kind": "pdf", "name": "x.pdf", "data": "not base64!!"}])
    big = base64.b64encode(b"0" * (11 * 1024 * 1024)).decode()
    with pytest.raises(AttachmentError, match="too big"):
        parse_attachments([{"kind": "pdf", "name": "huge.pdf", "data": big}])
    with pytest.raises(AttachmentError, match="Up to"):
        parse_attachments([{"kind": "text", "name": f"{i}.txt", "text": "x"} for i in range(7)])
    # A video that isn't an upload reference is dropped, not trusted.
    assert parse_attachments([{"kind": "video", "url": "file:///etc/passwd"}]) == []
    # A forged preview (not a small image data URL) is dropped.
    png = base64.b64encode(b"png").decode()
    parsed = parse_attachments([{"kind": "image", "name": "a.png", "media_type": "image/png", "data": png, "preview": "javascript:alert(1)"}])
    assert parsed[0]["preview"] is None


# ----------------------------------------------------------------------
# Rate limiting
# ----------------------------------------------------------------------
class _FakeRateRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.ttls: dict[str, int] = {}
        self.zsets: dict[str, dict[str, float]] = {}

    async def eval(self, script: str, numkeys: int, *args):
        key = str(args[0])
        if "INCR" in script:
            window = int(args[1])
            self.counts[key] = self.counts.get(key, 0) + 1
            if self.counts[key] == 1 or self.ttls.get(key, -1) < 0:
                self.ttls[key] = window
            return self.counts[key]
        now, ttl, limit, member = float(args[1]), int(args[2]), int(args[3]), str(args[4])
        zset = {m: score for m, score in self.zsets.get(key, {}).items() if score > now}
        if len(zset) >= limit:
            self.zsets[key] = zset
            return 0
        zset[member] = now + ttl
        self.zsets[key] = zset
        self.ttls[key] = ttl + 30
        return 1

    async def zadd(self, key: str, mapping: dict[str, float]):
        zset = self.zsets.setdefault(key, {})
        zset.update(mapping)

    async def zrem(self, key: str, member: str):
        if key in self.zsets:
            self.zsets[key].pop(member, None)

    async def expire(self, key: str, seconds: int):
        self.ttls[key] = int(seconds)


@pytest.mark.asyncio
async def test_endpoint_rate_limit_counts_and_blocks_in_redis(monkeypatch):
    from src.agent import rate_limit

    fake = _FakeRateRedis()
    monkeypatch.setattr(rate_limit.JobQueue, "get_pool", AsyncMock(return_value=fake))
    monkeypatch.setenv("AGENT_WRITE_RATE_LIMIT", "2")
    monkeypatch.setenv("AGENT_WRITE_WINDOW_SECONDS", "60")
    monkeypatch.setenv("AGENT_REQUEST_RATE_LIMIT", "3")

    await rate_limit.enforce_endpoint_limit("user-a", write=True)
    await rate_limit.enforce_endpoint_limit("user-a", write=True)
    with pytest.raises(rate_limit.RequestRateLimited) as exc_info:
        await rate_limit.enforce_endpoint_limit("user-a", write=True)
    http_exc = exc_info.value.to_http_exception()
    assert http_exc.status_code == 429
    assert http_exc.detail["code"] == "AGENT_RATE_LIMITED"
    assert int(http_exc.headers["Retry-After"]) >= 1

    # Read bucket and other users remain independent.
    await rate_limit.enforce_endpoint_limit("user-a", write=False)
    await rate_limit.enforce_endpoint_limit("user-b", write=True)


@pytest.mark.asyncio
async def test_endpoint_rate_limit_fails_open_when_redis_errors(monkeypatch):
    from src.agent import rate_limit

    monkeypatch.setattr(rate_limit.JobQueue, "get_pool", AsyncMock(side_effect=RuntimeError("redis down")))
    allowed, retry_after = await rate_limit.hit_endpoint_limit("read", "user-a", limit=1, window_seconds=60)
    assert allowed is True and retry_after == 0


@pytest.mark.asyncio
async def test_sse_slot_cap_release_and_expiry(monkeypatch):
    from src.agent import rate_limit

    rate_limit.reset_local_state()
    monkeypatch.setenv("AGENT_MAX_SSE_STREAMS", "2")

    # Redis-backed path
    fake = _FakeRateRedis()
    monkeypatch.setattr(rate_limit.JobQueue, "get_pool", AsyncMock(return_value=fake))
    s1 = await rate_limit.acquire_sse_slot("user-a")
    s2 = await rate_limit.acquire_sse_slot("user-a")
    with pytest.raises(rate_limit.TooManyStreamsError):
        await rate_limit.acquire_sse_slot("user-a")
    await rate_limit.refresh_sse_slot("user-a", s1)
    await rate_limit.release_sse_slot("user-a", s1)
    s3 = await rate_limit.acquire_sse_slot("user-a")
    await rate_limit.release_sse_slot("user-a", s2)
    await rate_limit.release_sse_slot("user-a", s3)

    # Local fallback path when Redis is unavailable
    monkeypatch.setattr(rate_limit.JobQueue, "get_pool", AsyncMock(side_effect=RuntimeError("redis down")))
    now = 1_700_000_000.0
    monkeypatch.setattr(rate_limit.time, "time", lambda: now)
    l1 = await rate_limit.acquire_sse_slot("user-local")
    l2 = await rate_limit.acquire_sse_slot("user-local")
    with pytest.raises(rate_limit.TooManyStreamsError):
        await rate_limit.acquire_sse_slot("user-local")
    # Expired slots are pruned automatically.
    monkeypatch.setattr(rate_limit.time, "time", lambda: now + rate_limit.SSE_SLOT_TTL_SECONDS + 1)
    l3 = await rate_limit.acquire_sse_slot("user-local")
    await rate_limit.release_sse_slot("user-local", l3)
    assert l1 != l2
    rate_limit.reset_local_state()


def test_env_limit_helpers_clamp_invalid_values(monkeypatch):
    from src.agent import rate_limit

    monkeypatch.setenv("AGENT_DAILY_TURN_LIMIT", "not-a-number")
    monkeypatch.setenv("AGENT_BURST_TURN_LIMIT", "0")
    monkeypatch.setenv("AGENT_MAX_CONCURRENT_TURNS", "-5")
    assert rate_limit.daily_turn_limit() == rate_limit.DEFAULT_DAILY_TURNS
    assert rate_limit.burst_turn_limit() == 1
    assert rate_limit.max_concurrent_turns() == 1


@pytest.mark.asyncio
async def test_sse_slot_release_succeeds_under_anyio_cancellation(monkeypatch):
    import asyncio
    import anyio
    from src.agent import rate_limit

    fake = _FakeRateRedis()

    async def yielding_zrem(key: str, member: str):
        await asyncio.sleep(0)
        if key in fake.zsets:
            fake.zsets[key].pop(member, None)

    fake.zrem = yielding_zrem  # type: ignore[method-assign]
    monkeypatch.setattr(rate_limit.JobQueue, "get_pool", AsyncMock(return_value=fake))
    monkeypatch.setenv("AGENT_MAX_SSE_STREAMS", "1")

    slot = await rate_limit.acquire_sse_slot("user-cancel")
    assert len(fake.zsets["agent:sse:user-cancel"]) == 1

    with anyio.CancelScope() as scope:
        try:
            scope.cancel()
            await anyio.sleep(0)
        finally:
            await rate_limit.release_sse_slot("user-cancel", slot)

    assert fake.zsets["agent:sse:user-cancel"] == {}
    # A fresh connection succeeds immediately after disconnect.
    next_slot = await rate_limit.acquire_sse_slot("user-cancel")
    await rate_limit.release_sse_slot("user-cancel", next_slot)


@pytest.mark.asyncio
async def test_max_model_calls_persists_across_approval_resume(monkeypatch):
    from src.agent import store

    published: list[tuple[str, dict]] = []

    monkeypatch.setattr(loop, "get_config", lambda: SimpleNamespace(anthropic_api_key="test-key"))
    monkeypatch.setattr(store, "heartbeat", AsyncMock(return_value=False))
    monkeypatch.setattr(store, "update_run", AsyncMock())
    monkeypatch.setattr(loop.events, "publish", AsyncMock(side_effect=lambda _cid, ev, data: published.append((ev, data))))

    turn = loop.Turn(
        None,
        {"id": "run-1", "usage": {"model_calls": loop.MAX_MODEL_CALLS}},
        {"id": "conv-1", "user_id": "user-1", "team_id": None},
        timezone_name="UTC",
    )
    called = False

    async def fail_if_called(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("_call_model should not be invoked when MAX_MODEL_CALLS is already reached")

    monkeypatch.setattr(turn, "_call_model", fail_if_called)
    await turn._loop()

    assert called is False
    assert any(ev == "notice" and data.get("text") == "step_limit" for ev, data in published)
    assert any(ev == "done" and data.get("status") == "completed" for ev, data in published)
