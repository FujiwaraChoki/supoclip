import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import editor
from src.database import get_db
from src.editor_document import EditDocument, atomic_json, editor_dir
from src.services import ai_edit_service as ai
from src.services.ai_edit_service import EditPlan, apply_plan, build_context

WORDS = "So um I started biking to work every day and um it changed everything".split()


def draft(**extra) -> EditDocument:
    words = [
        {"id": f"w{i}", "start": round(i * 0.5, 2), "end": round(i * 0.5 + 0.4, 2), "text": text}
        for i, text in enumerate(WORDS)
    ]
    return EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 10}], "words": words, **extra}
    )


def plan(*operations, summary="Done.") -> EditPlan:
    return EditPlan.model_validate({"summary": summary, "operations": list(operations)})


async def apply(*operations, document=None):
    return await apply_plan(document or draft(), plan(*operations), 10.0)


# --- applying operations -------------------------------------------------------

async def test_caption_settings_use_visual_values_after_direct_manipulation():
    doc = draft(textTransforms={"hook": {"x": .1, "y": .1, "scale": 1.4}, "captions": {"x": -.1, "y": -.2, "scale": 2}})
    context = build_context(doc, 10, 0, "Style the captions")
    assert '"size":108.0' in context and '"y":0.58' in context
    result, notes = await apply(
        {"type": "set", "setting": "captions.size", "value": "80"},
        {"type": "set", "setting": "captions.position", "value": "0.7"}, document=doc,
    )
    transform = result.textTransforms["captions"]
    assert result.captions.size * transform.scale == pytest.approx(80)
    assert result.captions.y + transform.y == pytest.approx(.7)
    assert transform.x == -.1
    assert result.textTransforms["hook"] == doc.textTransforms["hook"]
    assert result.captions.size == doc.captions.size
    assert not notes


async def test_global_caption_settings_also_update_individual_overrides():
    doc = draft(captionTransforms={'["s","w0"]': {"x": .1, "y": -.2, "scale": 1.5}})
    result, _ = await apply(
        {"type": "set", "setting": "captions.size", "value": "80"},
        {"type": "set", "setting": "captions.position", "value": "0.7"}, document=doc,
    )
    placement = result.captionTransforms['["s","w0"]']
    assert placement.x == .1
    assert placement.size * placement.scale == 80
    assert result.captions.y + placement.y == pytest.approx(.7)


async def test_removing_filler_words_cuts_them_out_of_the_segments():
    result, notes = await apply({"type": "remove_words", "word_ids": ["w1", "w9"]})

    # "um" at 0.5 s and 4.5 s are cut up to the next word.
    assert [(s.start, s.end) for s in result.segments] == [(0, 0.5), (1.0, 4.5), (5.0, 10)]
    assert notes == []


async def test_keep_ranges_trims_and_reorders():
    result, _ = await apply({"type": "keep_ranges", "ranges": [{"start": 6, "end": 8}, {"start": 1, "end": 3}]})
    assert [(s.start, s.end) for s in result.segments] == [(6, 8), (1, 3)]


async def test_cuts_that_would_empty_the_clip_are_refused():
    result, notes = await apply({"type": "cut_ranges", "ranges": [{"start": 0, "end": 10}]})
    assert [(s.start, s.end) for s in result.segments] == [(0, 10)]
    assert "whole clip" in notes[0]


async def test_pauses_are_tightened_between_words():
    doc = draft()
    words = doc.model_dump()["words"]
    words[5]["start"], words[5]["end"] = 4.0, 4.4  # a 1.6 s gap after "to"
    for later in words[6:]:
        later["start"] += 1.5
        later["end"] += 1.5
    doc = EditDocument.model_validate({**doc.model_dump(), "words": words, "segments": [{"id": "s", "start": 0, "end": 10}]})

    result, _ = await apply({"type": "remove_pauses", "min_gap": 1.0, "keep": 0.2}, document=doc)

    assert [(s.start, round(s.end, 2)) for s in result.segments][:2] == [(0, 2.5), (3.9, 10)]


@pytest.mark.parametrize("gap", [0.4, 0.5])
async def test_pause_removal_never_expands_a_gap_shorter_than_keep(gap):
    document = draft(words=[
        {"id": "a", "start": 0, "end": 1, "text": "hello"},
        {"id": "b", "start": 1 + gap, "end": 2, "text": "world"},
        {"id": "c", "start": 4, "end": 5, "text": "again"},
    ])
    result, _ = await apply(
        {"type": "remove_pauses", "min_gap": 0.3, "keep": 0.5}, document=document,
    )

    # Keep the short gap untouched, while still shortening the later long one.
    assert [(s.start, s.end) for s in result.segments] == [(0, 2.25), (3.75, 10)]


async def test_settings_are_coerced_clamped_and_validated():
    result, notes = await apply(
        *[
            {"type": "set", "setting": setting, "value": value}
            for setting, value in [
                ("captions.size", "400"),
                ("captions.color", "#ff0000"),
                ("captions.accent", "yellow"),
                ("captions.position", "2"),
                ("captions.uppercase", "true"),
                ("captions.template", "nope"),
                ("captions.font", "Comic Sans"),
                ("framing.zoom", "9"),
                ("framing.x", "-1"),
                ("framing.aspect", "square"),
                ("effects.saturation", "500"),
                ("effects.brightness", "120%"),
                ("audio.volume", "5"),
                ("audio.muted", "maybe"),
                ("hook_title", "  I biked   to work  "),
            ]
        ]
    )

    assert result.captions.size == 132 and result.captions.color == "#FF0000"
    assert result.captions.accent == "#FACC15"  # unchanged
    assert result.captions.y == 0.9 and result.captions.uppercase is True
    assert result.captions.font == "Poppins-ExtraBold"  # unchanged
    assert result.framing.zoom == 3 and result.framing.x == 0 and result.framing.aspect == "square"
    assert result.effects.saturation == 220 and result.effects.brightness == 120
    assert result.volume == 2 and result.muted is False
    assert result.hookTitle == "I biked to work"
    assert len(notes) == 4  # accent, template, font, muted


async def test_caption_text_and_highlights_change_by_word_id():
    result, _ = await apply(
        {"type": "edit_words", "edits": [{"word_id": "w4", "text": "cycling"}, {"word_id": "missing", "text": "x"}]},
        {"type": "highlight_words", "word_ids": ["w13"], "highlight": True},
    )
    words = {w.id: w for w in result.words}
    assert words["w4"].text == "cycling" and words["w13"].highlight is True


async def test_broll_is_added_from_pexels_and_never_overlaps(monkeypatch):
    video = {
        "id": 7, "duration": 10, "width": 1080, "height": 1920, "url": "https://www.pexels.com/video/7/",
        "image": "https://images.pexels.com/7.jpg", "user": {"name": "Someone"},
        "video_files": [{"width": 1080, "height": 1920, "file_type": "video/mp4", "link": "https://videos.pexels.com/7.mp4"}],
    }
    monkeypatch.setattr("src.broll.broll_configured", lambda: True)
    monkeypatch.setattr("src.broll.search_pexels_videos", AsyncMock(return_value=[video]))

    result, notes = await apply(
        {"type": "add_broll", "start": 2.0, "end": 4.5, "query": "biking to work"},
        {"type": "add_broll", "start": 3.0, "end": 5.0, "query": "office"},
    )

    assert [(b.start, b.end, b.query, b.footage.id) for b in result.broll] == [(2.0, 4.5, "biking to work", 7)]
    assert "no free space" in notes[0]


async def test_broll_can_be_moved_and_removed():
    footage = {
        "id": 3, "width": 1080, "height": 1920, "duration": 5,
        "fileUrl": "https://videos.pexels.com/3.mp4",
    }
    doc = draft(broll=[
        {"id": "a", "start": 1, "end": 3, "footage": footage},
        {"id": "b", "start": 6, "end": 8, "footage": footage},
    ])
    result, notes = await apply(
        {"type": "move_broll", "id": "a", "start": 2, "end": 4},
        {"type": "move_broll", "id": "a", "start": 5.5, "end": 7},
        {"type": "remove_broll", "ids": ["b"]},
        document=doc,
    )
    assert [(b.id, b.start, b.end) for b in result.broll] == [("a", 2, 4)]
    assert any("overlap" in note for note in notes)


def test_context_lists_words_with_ids_and_the_request():
    context = build_context(draft(), 10.0, 4.2, "remove the ums")
    assert "w1 0.50 0.90 um" in context
    assert "Playhead: 4.20 s" in context
    assert context.rstrip().endswith("remove the ums")


def test_plans_only_accept_known_operations():
    with pytest.raises(Exception):
        EditPlan.model_validate({"summary": "x", "operations": [{"type": "add_music"}]})


# --- the worker job ------------------------------------------------------------


class FakeRedis:
    def __init__(self):
        self.data = {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None):
        self.data[key] = value

    async def delete(self, key):
        self.data.pop(key, None)


@pytest.fixture
def no_runtime_settings(monkeypatch):
    monkeypatch.setattr("src.runtime_settings.load_runtime_settings_cache", AsyncMock())

    class Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *args):
            return False

    monkeypatch.setattr("src.database.AsyncSessionLocal", Session)


async def test_job_publishes_the_edited_draft(monkeypatch, no_runtime_settings):
    redis = FakeRedis()
    redis.data[ai.request_key("j")] = json.dumps(
        {"document": draft().model_dump(), "instruction": "remove the ums", "playhead": None, "duration": 10}
    )
    seen = {}

    async def fake_plan(context):
        seen["context"] = context
        return plan({"type": "remove_words", "word_ids": ["w1"]}, summary="Cut the um.")

    monkeypatch.setattr(ai, "plan_edit", fake_plan)

    await ai.ai_edit_job({"redis": redis}, "j")

    result = json.loads(redis.data[ai.result_key("j")])
    assert result["status"] == "completed" and result["summary"] == "Cut the um."
    assert result["changed"] is True
    assert len(result["document"]["segments"]) == 2
    assert ai.request_key("j") not in redis.data
    assert "remove the ums" in seen["context"]


async def test_job_reports_failures_without_touching_the_draft(monkeypatch, no_runtime_settings):
    redis = FakeRedis()
    redis.data[ai.request_key("k")] = json.dumps(
        {"document": draft().model_dump(), "instruction": "x", "duration": 10}
    )
    monkeypatch.setattr(ai, "plan_edit", AsyncMock(side_effect=ai.AIEditError("The AI declined that request. Try rephrasing it.")))

    await ai.ai_edit_job({"redis": redis}, "k")

    assert json.loads(redis.data[ai.result_key("k")]) == {
        "status": "failed",
        "error": "The AI declined that request. Try rephrasing it.",
    }


async def test_noop_plan_cannot_claim_a_change_that_was_not_applied(monkeypatch, no_runtime_settings):
    redis = FakeRedis()
    document = draft().model_dump()
    redis.data[ai.request_key("noop")] = json.dumps({"document": document, "instruction": "move B-roll", "duration": 10})
    monkeypatch.setattr(ai, "plan_edit", AsyncMock(return_value=plan(
        {"type": "set", "setting": "audio.volume", "value": "1.0"}, summary="Moved the B-roll.",
    )))
    await ai.ai_edit_job({"redis": redis}, "noop")
    result = json.loads(redis.data[ai.result_key("noop")])
    assert result["changed"] is False
    assert result["document"] == document
    assert result["summary"] == "Your draft is unchanged."


async def test_plan_edit_handles_refusals(monkeypatch):
    monkeypatch.setattr(ai, "get_config", lambda: SimpleNamespace(anthropic_api_key="key"))
    response = SimpleNamespace(stop_reason="refusal", parsed_output=None)

    class Client:
        def __init__(self, **kwargs):
            self.beta = SimpleNamespace(messages=SimpleNamespace(parse=AsyncMock(return_value=response)))

    monkeypatch.setattr("anthropic.AsyncAnthropic", Client)
    with pytest.raises(ai.AIEditError, match="declined"):
        await ai.plan_edit("context")


async def test_plan_edit_sends_sonnet_with_fallbacks_and_schema(monkeypatch):
    monkeypatch.setattr(ai, "get_config", lambda: SimpleNamespace(anthropic_api_key="key"))
    parsed = plan(summary="Nothing to do.")
    response = SimpleNamespace(
        stop_reason="end_turn", parsed_output=parsed, usage=SimpleNamespace(input_tokens=1, output_tokens=1)
    )
    parse = AsyncMock(return_value=response)

    class Client:
        def __init__(self, **kwargs):
            self.beta = SimpleNamespace(messages=SimpleNamespace(parse=parse))

    monkeypatch.setattr("anthropic.AsyncAnthropic", Client)

    assert await ai.plan_edit("context") is parsed
    kwargs = parse.await_args.kwargs
    assert kwargs["model"] == "claude-sonnet-5-5"
    assert kwargs["output_format"] is EditPlan
    assert kwargs["fallbacks"] == "default" and kwargs["betas"] == ["server-side-fallback-2026-07-01"]
    assert kwargs["output_config"] == {"effort": "medium"}


# --- routes --------------------------------------------------------------------


class FakePool(FakeRedis):
    def __init__(self):
        super().__init__()
        self.counts = {}

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def decr(self, key):
        self.counts[key] -= 1

    async def expire(self, key, seconds):
        pass


@pytest.fixture
async def client(tmp_path, monkeypatch):
    task = {"id": "task", "user_id": "owner", "status": "completed"}
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4")}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        config=SimpleNamespace(temp_dir=str(tmp_path)),
    )
    pool = FakePool()
    monkeypatch.setattr(editor, "TaskService", lambda db: service)
    monkeypatch.setattr("src.api.routes.tasks._get_user_id_from_headers", AsyncMock(return_value="owner"))
    monkeypatch.setattr(editor.JobQueue, "get_pool", AsyncMock(return_value=pool))
    enqueue = AsyncMock(return_value="job")
    monkeypatch.setattr(editor.JobQueue, "enqueue_job", enqueue)
    monkeypatch.setattr(ai, "ai_editing_configured", lambda: True)
    app = FastAPI()
    app.include_router(editor.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(close=AsyncMock())
    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    document = draft().model_dump()
    atomic_json(directory / "draft.json", {"revision": 0, "document": document})
    atomic_json(directory / "original.json", document)
    atomic_json(directory / "asset.json", {"status": "ready", "duration": 10, "width": 1080, "height": 1920})
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, document, pool, enqueue


async def test_instruction_is_queued_on_the_ai_worker(client):
    http, document, pool, enqueue = client
    response = await http.post(
        "/tasks/task/clips/clip/editor/ai",
        json={"basis": "clip.mp4", "document": document, "instruction": "  remove   the ums ", "playhead": 4.2},
    )

    assert response.status_code == 202
    job_id = response.json()["id"]
    request = json.loads(pool.data[ai.request_key(job_id)])
    assert request["instruction"] == "remove the ums" and request["playhead"] == 4.2
    assert enqueue.await_args.args == ("ai_edit_job", job_id)
    assert enqueue.await_args.kwargs == {"_queue_name": "supoclip_ai"}

    # Polling: queued until the worker publishes, then the result.
    status = await http.get(f"/tasks/task/clips/clip/editor/ai/{job_id}")
    assert status.json()["status"] == "queued"
    pool.data[ai.result_key(job_id)] = json.dumps({"status": "completed", "summary": "Cut."})
    assert (await http.get(f"/tasks/task/clips/clip/editor/ai/{job_id}")).json()["summary"] == "Cut."


async def test_results_are_private_to_their_clip(client, monkeypatch):
    http, document, pool, _ = client
    job_id = (await http.post(
        "/tasks/task/clips/clip/editor/ai",
        json={"basis": "clip.mp4", "document": document, "instruction": "x"},
    )).json()["id"]
    pool.data[f"ai_edit:owner:{job_id}"] = "task:other-clip"
    assert (await http.get(f"/tasks/task/clips/clip/editor/ai/{job_id}")).status_code == 404
    monkeypatch.setattr("src.api.routes.tasks._get_user_id_from_headers", AsyncMock(return_value="outsider"))
    assert (await http.get(f"/tasks/task/clips/clip/editor/ai/{job_id}")).status_code == 403


async def test_instructions_are_validated_and_limited(client, monkeypatch):
    http, document, pool, _ = client
    send = lambda **body: http.post("/tasks/task/clips/clip/editor/ai", json={"basis": "clip.mp4", "document": document, **body})

    assert (await send(instruction="   ")).status_code == 400
    assert (await send(instruction="x" * 1001)).status_code == 400
    assert (await http.post("/tasks/task/clips/clip/editor/ai", json={"basis": "old.mp4", "document": document, "instruction": "x"})).status_code == 409
    monkeypatch.setattr(editor, "AI_EDIT_DAILY_LIMIT", 1)
    assert (await send(instruction="first")).status_code == 202
    assert (await send(instruction="second")).status_code == 429
    monkeypatch.setattr(ai, "ai_editing_configured", lambda: False)
    assert (await send(instruction="third")).status_code == 503


async def test_editor_state_says_whether_ai_editing_is_available(client):
    http, *_ = client
    assert (await http.get("/tasks/task/clips/clip/editor")).json()["aiEditing"] is True


def test_output_schema_stays_small_enough_for_structured_outputs():
    """The API rejects schemas whose grammar is too large; optional fields were the cause."""
    from anthropic import transform_schema

    schema = json.dumps(transform_schema(EditPlan))
    assert '"null"' not in schema
    assert len(schema) < 8000


@pytest.mark.parametrize("instruction", [None, 42, True, [], {"text": "mute"}])
async def test_nontext_instructions_never_spend_quota(client, instruction):
    http, document, pool, enqueue = client
    response = await http.post("/tasks/task/clips/clip/editor/ai", json={
        "basis": "clip.mp4", "document": document, "instruction": instruction,
    })
    assert response.status_code == 400
    assert pool.counts == {}
    enqueue.assert_not_awaited()


@pytest.mark.parametrize("playhead,expected", [(-999, 0), (999, 10), (True, None), ("3", None), (10**400, 10), (-10**400, 0)])
async def test_playhead_is_bounded_or_ignored(client, playhead, expected):
    http, document, pool, _ = client
    response = await http.post("/tasks/task/clips/clip/editor/ai", json={
        "basis": "clip.mp4", "document": document, "instruction": "mute", "playhead": playhead,
    })
    request = json.loads(pool.data[ai.request_key(response.json()["id"])])
    assert request["playhead"] == expected


@pytest.mark.parametrize("playhead", ["NaN", "Infinity", "-Infinity"])
async def test_nonfinite_playhead_is_not_sent_to_the_model(client, playhead):
    http, document, pool, _ = client
    response = await http.post("/tasks/task/clips/clip/editor/ai", content=json.dumps({
        "basis": "clip.mp4", "document": document, "instruction": "mute", "playhead": 0,
    }).replace('"playhead": 0', f'"playhead": {playhead}'), headers={"Content-Type": "application/json"})
    assert response.status_code == 202
    request = json.loads(pool.data[ai.request_key(response.json()["id"])])
    assert request["playhead"] is None


async def test_worker_reports_expired_and_corrupt_requests(no_runtime_settings):
    redis = FakeRedis()
    await ai.ai_edit_job({"redis": redis}, "expired")
    assert json.loads(redis.data[ai.result_key("expired")])["status"] == "failed"
    redis.data[ai.request_key("corrupt")] = "{broken"
    await ai.ai_edit_job({"redis": redis}, "corrupt")
    assert json.loads(redis.data[ai.result_key("corrupt")])["status"] == "failed"
    assert ai.request_key("corrupt") not in redis.data


async def test_worker_cancellation_does_not_leave_a_running_result(monkeypatch, no_runtime_settings):
    import asyncio

    redis = FakeRedis()
    redis.data[ai.request_key("cancel")] = json.dumps({"document": draft().model_dump(), "instruction": "mute", "duration": 10})
    monkeypatch.setattr(ai, "plan_edit", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await ai.ai_edit_job({"redis": redis}, "cancel")
    assert json.loads(redis.data[ai.result_key("cancel")])["status"] == "failed"
    assert ai.request_key("cancel") not in redis.data


async def test_sixteen_concurrent_jobs_keep_results_separate(monkeypatch, no_runtime_settings):
    import asyncio

    redis = FakeRedis()
    async def fake_plan(context):
        await asyncio.sleep(0)
        return plan({"type": "set", "setting": "hook_title", "value": context.split("Request:\n")[-1]})
    monkeypatch.setattr(ai, "plan_edit", fake_plan)
    for i in range(16):
        redis.data[ai.request_key(str(i))] = json.dumps({
            "document": draft().model_dump(), "instruction": f"job {i}", "duration": 10,
        })
    await asyncio.gather(*(ai.ai_edit_job({"redis": redis}, str(i)) for i in range(16)))
    for i in range(16):
        result = json.loads(redis.data[ai.result_key(str(i))])
        assert result["status"] == "completed"
        assert result["document"]["hookTitle"] == f"job {i}"
        assert ai.request_key(str(i)) not in redis.data


@pytest.mark.parametrize("failure", ["metadata", "enqueue"])
async def test_queue_failure_refunds_quota_and_removes_partial_request(client, monkeypatch, failure):
    http, document, pool, enqueue = client
    if failure == "metadata":
        original_set = pool.set
        async def fail_owner(key, value, ex=None):
            if key.startswith("ai_edit:owner:"):
                raise ConnectionError("Redis unavailable")
            await original_set(key, value, ex)
        monkeypatch.setattr(pool, "set", fail_owner)
    else:
        enqueue.side_effect = ConnectionError("Queue unavailable")
    response = await http.post("/tasks/task/clips/clip/editor/ai", json={
        "basis": "clip.mp4", "document": document, "instruction": "mute",
    })
    assert response.status_code == 503
    assert list(pool.counts.values()) == [0]
    assert pool.data == {}


async def test_concurrent_requests_cannot_exceed_daily_quota(client, monkeypatch):
    import asyncio

    http, document, pool, enqueue = client
    monkeypatch.setattr(editor, "AI_EDIT_DAILY_LIMIT", 5)
    responses = await asyncio.gather(*(http.post("/tasks/task/clips/clip/editor/ai", json={
        "basis": "clip.mp4", "document": document, "instruction": f"request {i}",
    }) for i in range(50)))
    assert sum(r.status_code == 202 for r in responses) == 5
    assert sum(r.status_code == 429 for r in responses) == 45
    assert enqueue.await_count == 5
    assert len([key for key in pool.data if key.startswith("ai_edit:request:")]) == 5
