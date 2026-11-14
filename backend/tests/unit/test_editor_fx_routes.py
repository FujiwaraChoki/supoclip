"""Studio routes for dynamic edits (§5.5, §0.4.8): seeding on open, the PATCH raw-key rule and gate, resolve in E,
beat planning (daily limit, staleness, refund), face/matte analysis, reaction previews and the AI edit request."""

import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src.api.routes import editor
from src.database import get_db
from src.edit_fx.contracts import FX_KINDS, FxPlan
from src.editor_document import DocFx, EditDocument
from src.services import ai_edit_service as ai
from tests.fixtures.editor_store import stored_state

WORDS = ("so here is the thing nobody tells you about money you can make ten thousand dollars a month "
         "and still be broke because the real secret is what you keep").split()
BASE = "/tasks/task/clips/clip/editor"


def words(count=60):
    return [{"id": f"w{i}", "start": i * 0.5, "end": i * 0.5 + 0.4, "text": WORDS[i % len(WORDS)]}
            for i in range(count)]


def document(**extra) -> dict:
    return EditDocument.model_validate(
        {"segments": [{"id": "s", "start": 0, "end": 30}], "words": words(), **extra}
    ).model_dump(mode="json")


def planned_fx(**fx) -> dict:
    beats = [{"id": f"b{i}", "kind": "emphasis", "start": 1.0 + 2 * i, "end": 1.8 + 2 * i, "strength": 3,
              "words": ["money"], "text": "money"} for i in range(12)]
    return DocFx.model_validate({"mood": "hype", "beats": beats, **fx}).model_dump(mode="json")


class FakeRedis:
    def __init__(self):
        self.data, self.counts = {}, {}

    async def get(self, key):
        return self.data.get(key)

    async def set(self, key, value, ex=None, nx=False):
        if nx and key in self.data:
            return False
        self.data[key] = value
        return True

    async def delete(self, key):
        self.data.pop(key, None)

    async def incr(self, key):
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def decr(self, key):
        self.counts[key] = self.counts.get(key, 0) - 1
        return self.counts[key]

    async def expire(self, key, seconds):
        return True


@pytest.fixture
async def studio(tmp_path, monkeypatch, editor_store):
    task = {"id": "task", "user_id": "owner", "status": "completed"}
    clip = {"id": "clip", "task_id": "task", "filename": "clip.mp4", "file_path": str(tmp_path / "clip.mp4")}
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=SimpleNamespace(get_clip_by_id=AsyncMock(return_value=clip)),
        config=SimpleNamespace(temp_dir=str(tmp_path)),
    )
    monkeypatch.setattr(editor, "TaskService", lambda db: service)
    monkeypatch.setattr("src.api.routes.tasks._get_user_id_from_headers", AsyncMock(return_value="owner"))
    enqueue = AsyncMock(return_value="job")
    monkeypatch.setattr(editor.JobQueue, "enqueue_job", enqueue)
    pool = FakeRedis()
    monkeypatch.setattr(editor.JobQueue, "get_pool", AsyncMock(return_value=pool))
    flag = {"on": False}

    async def fx_flag(db, request, user_id):
        return flag["on"]

    monkeypatch.setattr(editor, "_fx_flag", fx_flag)
    sidecar = {"value": None}
    monkeypatch.setattr(editor, "_clip_edit_fx", lambda clip: sidecar["value"])
    app = FastAPI()
    app.include_router(editor.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(close=AsyncMock())
    editor_store.put_ready("task", "clip", "clip.mp4", {"duration": 30, "width": 1080, "height": 1920,
                                                        "mediaVersion": "v1"}, document())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield SimpleNamespace(http=http, store=editor_store, flag=flag, sidecar=sidecar, enqueue=enqueue, pool=pool)


def sidecar_plan() -> dict:
    plan = FxPlan.model_validate({
        "style": {"intensity": "bold"}, "template": "default", "seed": 42, "edit_duration": 30, "mood": "hype",
        "cues": [{"kind": "sfx", "id": "b0.sfx", "start": 1.0, "end": 1.0, "sound": "pop"}],
    })
    return {"style": {"intensity": "bold"}, "beats": None, "plan": plan.model_dump(mode="json"), "face_track": None,
            "fx_level": "full", "applied_warp": []}


def put(studio, doc, **asset):
    studio.store.put_ready("task", "clip", "clip.mp4", {"duration": 30, "width": 1080, "height": 1920,
                                                       "mediaVersion": "v1", **asset}, doc)


# --------------------------------------------------------------------------- open, save


async def test_opening_seeds_old_drafts_and_looks_follow_the_flag(studio):
    studio.sidecar["value"] = sidecar_plan()
    state = (await studio.http.get(BASE)).json()
    assert state["draft"]["document"]["fx"]["seed"] == 42
    assert state["original"]["fx"]["seed"] == 42
    assert "emoji_drop" not in state["captionLooks"]
    studio.flag["on"] = True
    looks = (await studio.http.get(BASE)).json()["captionLooks"]
    assert looks["emoji_drop"]["fx"] is True and "slam" not in looks      # unlisted styles are never offered


async def test_a_stored_fx_template_always_gets_its_look(studio):
    put(studio, document(captions={"template": "behind_head"}))
    looks = (await studio.http.get(BASE)).json()["captionLooks"]
    assert looks["behind_head"]["fxStyle"] == "behind_head" and "slam" not in looks


async def test_patch_without_fx_keeps_the_stored_edits(studio):
    stored = document(fx=planned_fx(style={"intensity": "bold"}))
    put(studio, stored)
    for body in (document(), {**document(), "fx": None}):
        revision = (await stored_state("task", "clip", "clip.mp4"))["draft"]["revision"]
        response = await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": revision, "document": body})
        assert response.status_code == 200, response.text
        assert response.json()["document"]["fx"] == stored["fx"]


async def test_patch_turning_dynamic_edits_on_needs_the_flag(studio):
    body = {"basis": "clip.mp4", "revision": 0, "document": document(captions={"template": "slam"})}
    response = await studio.http.patch(BASE, json=body)
    assert response.status_code == 403 and response.json()["detail"]["code"] == "DYNAMIC_EDITS_NOT_AVAILABLE"
    assert (await stored_state("task", "clip", "clip.mp4"))["draft"]["document"]["captions"]["template"] == "default"
    studio.flag["on"] = True
    assert (await studio.http.patch(BASE, json=body)).status_code == 200


async def test_patch_of_an_unchanged_fx_state_never_needs_the_flag(studio):
    stored = document(captions={"template": "slam"}, fx=planned_fx(style={"intensity": "bold"}))
    put(studio, stored)
    edited = {**stored, "captions": {**stored["captions"], "font": "THEBOLDFONT"}}
    response = await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": 0, "document": edited})
    assert response.status_code == 200
    # Turning it off is always allowed too.
    inert = DocFx(disabled=list(FX_KINDS)).model_dump(mode="json")
    off = {**edited, "captions": {**edited["captions"], "template": "default"}, "fx": inert}
    assert (await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": 1, "document": off})).status_code == 200


async def test_patch_turning_more_edits_on_needs_the_flag(studio):
    """A draft that already has edits: a legacy -> FX template or the intensity from off is still a transition to
    FX (§0.4.8, like the task settings route); raising an active intensity is not."""
    stored = document(fx=planned_fx(style={"intensity": "bold"}))
    put(studio, stored)
    styled = {**stored, "captions": {**stored["captions"], "template": "slam"}}
    response = await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": 0, "document": styled})
    assert response.status_code == 403 and response.json()["detail"]["code"] == "DYNAMIC_EDITS_NOT_AVAILABLE"
    louder = {**stored, "fx": {**stored["fx"], "style": {**stored["fx"]["style"], "intensity": "max"}}}
    assert (await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": 0, "document": louder})).status_code == 200

    style_only = document(captions={"template": "slam"}, fx=planned_fx())
    put(studio, style_only)
    active = {**style_only, "fx": {**style_only["fx"], "style": {**style_only["fx"]["style"], "intensity": "subtle"}}}
    response = await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": 0, "document": active})
    assert response.status_code == 403
    studio.flag["on"] = True
    assert (await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": 0, "document": active})).status_code == 200


async def test_a_seeded_clip_saves_without_the_flag(studio):
    """A generated clip with edits opens seeded; saving it is not "turning edits on" (§0.4.8)."""
    studio.sidecar["value"] = sidecar_plan()
    seeded = (await studio.http.get(BASE)).json()["draft"]["document"]
    response = await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": 0, "document": seeded})
    assert response.status_code == 200, response.text


async def test_patch_refuses_freezes_over_the_hold_budget(studio):
    studio.flag["on"] = True
    freezes = [{"kind": "freeze", "id": f"u{i}", "start": 2.0 + i, "end": 2.0 + i, "hold": 1.0, "origin": "user"}
               for i in range(2)]
    body = document(fx=DocFx.model_validate({"cues": freezes}).model_dump(mode="json"))
    response = await studio.http.patch(BASE, json={"basis": "clip.mp4", "revision": 0, "document": body})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "FX_HOLD_BUDGET"


async def test_the_matte_is_served_like_the_other_editor_media(studio, tmp_path):
    from src.editor_document import editor_dir

    directory = editor_dir(str(tmp_path), "task", "clip", "clip.mp4")
    directory.mkdir(parents=True, exist_ok=True)
    assert (await studio.http.get(f"{BASE}/media/matte.mp4")).status_code == 404
    (directory / "matte.mp4").write_bytes(b"matte")
    response = await studio.http.get(f"{BASE}/media/matte.mp4")
    assert response.status_code == 200 and response.content == b"matte"


# --------------------------------------------------------------------------- resolve


async def test_resolve_asks_for_a_plan_first(studio):
    studio.flag["on"] = True
    response = await studio.http.post(f"{BASE}/fx/resolve", json={"document": document(),
                                                                  "style": {"intensity": "bold"}})
    assert response.status_code == 409 and response.json()["detail"]["code"] == "NEEDS_PLAN"


async def test_resolve_is_gated_only_when_it_turns_edits_on(studio):
    body = {"document": document(fx=planned_fx()), "style": {"intensity": "bold"}}
    response = await studio.http.post(f"{BASE}/fx/resolve", json=body)
    assert response.status_code == 403
    studio.flag["on"] = True
    response = await studio.http.post(f"{BASE}/fx/resolve", json=body)
    assert response.status_code == 200, response.text
    resolved = response.json()["document"]
    assert resolved["fx"]["style"]["intensity"] == "bold" and resolved["fx"]["cues"]
    EditDocument.model_validate(resolved).validate_duration(30)
    # The draft is not saved by resolving.
    assert "fx" not in (await stored_state("task", "clip", "clip.mp4"))["draft"]["document"]
    # Once saved, changing the intensity works with the flag off.
    put(studio, resolved)
    studio.flag["on"] = False
    again = await studio.http.post(f"{BASE}/fx/resolve", json={"document": resolved, "style": {"intensity": "subtle"}})
    assert again.status_code == 200


async def test_resolve_gives_a_new_container_the_clips_own_seed(studio):
    """A container the browser made with the default seed 0 resolves with the clip's studio seed, so every clip
    doesn't get the same random choices; a planned draft keeps its seed."""
    from src.editor_document import studio_fx_seed

    studio.flag["on"] = True
    body = {"document": document(fx=planned_fx()), "style": {"intensity": "subtle"}}
    response = await studio.http.post(f"{BASE}/fx/resolve", json=body)
    assert response.status_code == 200 and response.json()["document"]["fx"]["seed"] == studio_fx_seed("task", "clip")
    body = {"document": document(fx=planned_fx(seed=77)), "style": {"intensity": "subtle"}}
    assert (await studio.http.post(f"{BASE}/fx/resolve", json=body)).json()["document"]["fx"]["seed"] == 77


async def test_resolve_validates_the_style(studio):
    response = await studio.http.post(f"{BASE}/fx/resolve", json={"document": document(), "style": {"intensity": "extreme"}})
    assert response.status_code == 422 and response.json()["detail"]["code"] == "INVALID_EDIT_STYLE"


@pytest.mark.parametrize("asset, pushes", [
    ({"hasLayout": True, "layoutHasZoompan": True}, False),
    ({"hasLayout": True, "layoutHasZoompan": False}, True),
    ({"hasLayout": True}, False),            # prepared before the studio recorded it: assume a baked zoom
    ({}, True),
])
async def test_resolve_adds_baseline_pushes_only_without_a_baked_zoom(studio, asset, pushes):
    studio.flag["on"] = True
    doc = document(framing={"fit": "auto"}, fx=planned_fx(beats=[]))
    put(studio, doc, **asset)
    # Baseline pushes exist at subtle (and style-only); bold and max reframe instead.
    response = await studio.http.post(f"{BASE}/fx/resolve", json={"document": doc, "style": {"intensity": "subtle"}})
    assert response.status_code == 200, response.text
    assert any(c["id"].startswith("kb") for c in response.json()["document"]["fx"]["cues"]) is pushes


# --------------------------------------------------------------------------- plan


async def test_plan_queues_the_draft_words_on_the_ai_worker(studio):
    studio.flag["on"] = True
    doc = document(segments=[{"id": "a", "start": 0, "end": 5}, {"id": "b", "start": 20, "end": 25}])
    response = await studio.http.post(f"{BASE}/fx/plan", json={"document": doc})
    assert response.status_code == 202
    job_id = response.json()["job_id"]
    request = json.loads(studio.pool.data[ai.fx_plan_request_key(job_id)])
    assert request["clip_seconds"] == 10 and request["user_id"] == "owner"
    assert all(w["start"] < 5 or w["start"] >= 20 for w in request["words"]) and request["words"][0]["id"] == "w0"
    assert studio.enqueue.await_args.args == ("edit_fx_plan_job", job_id)
    assert studio.enqueue.await_args.kwargs["_queue_name"] == "supoclip_ai"
    status = await studio.http.get(f"{BASE}/fx/plan/{job_id}")
    assert status.json()["status"] == "queued"
    studio.pool.data[ai.fx_plan_result_key(job_id)] = json.dumps({"status": "done", "beats": {"beats": []}})
    assert (await studio.http.get(f"{BASE}/fx/plan/{job_id}")).json()["status"] == "done"
    assert (await studio.http.get(f"/tasks/task/clips/other/editor/fx/plan/{job_id}")).status_code == 404


async def test_plan_needs_the_flag_unless_the_draft_has_edits(studio):
    assert (await studio.http.post(f"{BASE}/fx/plan", json={"document": document()})).status_code == 403
    put(studio, document(captions={"template": "slam"}))
    assert (await studio.http.post(f"{BASE}/fx/plan", json={"document": document()})).status_code == 202


async def test_the_daily_plan_limit(studio, monkeypatch):
    studio.flag["on"] = True
    monkeypatch.setenv("EDIT_PLAN_DAILY_LIMIT", "2")
    for _ in range(2):
        assert (await studio.http.post(f"{BASE}/fx/plan", json={"document": document()})).status_code == 202
    response = await studio.http.post(f"{BASE}/fx/plan", json={"document": document()})
    assert response.status_code == 429 and response.json()["detail"]["code"] == "EDIT_PLAN_LIMIT_REACHED"
    assert list(studio.pool.counts.values()) == [2]


async def test_a_failed_enqueue_refunds_the_plan(studio):
    studio.flag["on"] = True
    studio.enqueue.side_effect = RuntimeError("redis down")
    assert (await studio.http.post(f"{BASE}/fx/plan", json={"document": document()})).status_code == 503
    assert list(studio.pool.counts.values()) == [0]


async def test_a_plan_no_worker_answers_fails_and_refunds_once(studio, monkeypatch):
    studio.flag["on"] = True
    monkeypatch.setenv("EDIT_PLAN_TIMEOUT_SECONDS", "10")
    job_id = (await studio.http.post(f"{BASE}/fx/plan", json={"document": document()})).json()["job_id"]
    meta = json.loads(studio.pool.data[ai.fx_plan_meta_key(job_id)])
    studio.pool.data[ai.fx_plan_meta_key(job_id)] = json.dumps({**meta, "queued_at": time.time() - 31})
    for _ in range(3):
        body = (await studio.http.get(f"{BASE}/fx/plan/{job_id}")).json()
        assert body["status"] == "failed" and body["error"]["code"] == "WORKER_UNAVAILABLE"
    assert list(studio.pool.counts.values()) == [0]


# --------------------------------------------------------------------------- analysis, reactions


async def test_analysis_is_queued_once_per_media_and_need(studio):
    response = await studio.http.post(f"{BASE}/fx/analysis", json={})
    assert response.status_code == 202 and response.json() == {"status": "queued"}
    args, kwargs = studio.enqueue.await_args.args, studio.enqueue.await_args.kwargs
    assert args == ("editor_fx_analysis", "task", "clip", "clip.mp4")
    assert kwargs["_job_id"].endswith(":v1:face:clean")
    put(studio, document(), faceTrack={"base": "clean", "track": {"samples": []}})
    assert (await studio.http.post(f"{BASE}/fx/analysis", json={})).json() == {"status": "ready"}
    behind = DocFx.model_validate({"cues": [
        {"kind": "accent", "id": "b0.behind", "start": 1, "end": 2, "variant": "behind", "text": "money"}]})
    put(studio, document(fx=behind.model_dump(mode="json")), faceTrack={"base": "clean", "track": {"samples": []}})
    assert (await studio.http.post(f"{BASE}/fx/analysis", json={})).json() == {"status": "queued"}
    assert studio.enqueue.await_args.kwargs["_job_id"].endswith(":v1:matte:clean")
    # A matte made on the other base video does not count.
    put(studio, document(fx=behind.model_dump(mode="json")), faceTrack={"base": "clean", "track": {"samples": []}},
        matteVersion="m1", matteBase="layout")
    assert (await studio.http.post(f"{BASE}/fx/analysis", json={})).json() == {"status": "queued"}
    put(studio, document(fx=behind.model_dump(mode="json")), faceTrack={"base": "clean", "track": {"samples": []}},
        matteVersion="m1")
    assert (await studio.http.post(f"{BASE}/fx/analysis", json={})).json() == {"status": "ready"}


async def test_analysis_retries_a_failed_or_dead_run_under_a_fresh_job_id(studio, monkeypatch):
    from src.api.routes import editor as routes

    monkeypatch.setattr(routes.time, "time", lambda: 10_000_000.0)
    await studio.http.post(f"{BASE}/fx/analysis", json={})
    first = studio.enqueue.await_args.kwargs["_job_id"]
    assert first.endswith(":v1:face:clean")
    # Still running (recently started): the same id, so ARQ dedupes it.
    put(studio, document(), fxAnalysis={"status": "running", "base": "clean", "startedAt": 10_000_000 - 60})
    await studio.http.post(f"{BASE}/fx/analysis", json={})
    assert studio.enqueue.await_args.kwargs["_job_id"] == first
    # Failed, or "running" long past the job timeout (worker died): a fresh id per minute.
    for run in ({"status": "failed", "base": "clean"},
                {"status": "running", "base": "clean", "startedAt": 10_000_000 - 3 * 3600}):
        put(studio, document(), fxAnalysis=run)
        assert (await studio.http.post(f"{BASE}/fx/analysis", json={})).json() == {"status": "queued"}
        assert studio.enqueue.await_args.kwargs["_job_id"] == f"{first}:r{10_000_000 // 60}"
    # A failure on the other base does not change this base's id.
    put(studio, document(), fxAnalysis={"status": "failed", "base": "layout"})
    await studio.http.post(f"{BASE}/fx/analysis", json={})
    assert studio.enqueue.await_args.kwargs["_job_id"] == first


async def test_reaction_preview_streams_a_saved_cue(studio, monkeypatch, tmp_path):
    reaction = {"kind": "reaction", "id": "g.react0", "start": 3, "end": 5, "query": "shocked",
                "asset_id": "abc", "url": "https://media.giphy.com/media/abc/giphy.mp4"}
    put(studio, document(fx=DocFx.model_validate({"cues": [reaction]}).model_dump(mode="json")))

    async def ensure(cue, scratch):
        path = scratch / f"{cue.asset_id}.mp4"
        path.write_bytes(b"gif")
        return path

    monkeypatch.setattr("src.edit_fx.reactions.ensure_reaction_file", ensure)
    response = await studio.http.get(f"{BASE}/fx/reaction/g.react0")
    assert response.status_code == 200 and response.content == b"gif"
    assert (await studio.http.get(f"{BASE}/fx/reaction/missing")).status_code == 404
    assert (await studio.http.get(f"{BASE}/fx/reaction/..%2Fetc")).status_code == 404

    async def unavailable(cue, scratch):
        return None

    monkeypatch.setattr("src.edit_fx.reactions.ensure_reaction_file", unavailable)
    assert (await studio.http.get(f"{BASE}/fx/reaction/g.react0")).status_code == 404


# --------------------------------------------------------------------------- AI edit request (§14.1)


@pytest.fixture
def ai_route(studio, monkeypatch):
    class Allowance:
        async def summary(self, user_id):
            return {"plan": "pro", "limit": 10, "used": 0, "remaining": 10, "resetsAt": "x", "allowed": True}

        async def reserve(self, user_id, job_id):
            return await self.summary(user_id)

    monkeypatch.setattr(editor, "AIEditAllowanceService", lambda db: Allowance())
    monkeypatch.setattr(ai, "ai_editing_configured", lambda: True)
    return studio


async def test_ai_requests_carry_dynamic_edits_only_for_allowed_callers(ai_route):
    studio = ai_route
    body = {"basis": "clip.mp4", "document": document(), "instruction": "add a whoosh"}
    job = (await studio.http.post(f"{BASE}/ai", json=body)).json()["id"]
    request = json.loads(studio.pool.data[ai.request_key(job)])
    assert set(request) == {"document", "instruction", "playhead", "duration", "width", "height"}

    studio.flag["on"] = True
    job = (await studio.http.post(f"{BASE}/ai", json=body)).json()["id"]
    request = json.loads(studio.pool.data[ai.request_key(job)])
    assert request["fx_allowed"] is True and request["fx_flag"] is True and isinstance(request["fx_seed"], int)

    # A draft that already has edits keeps its AI edits working when the flag is switched off.
    studio.flag["on"] = False
    styled = {**body, "document": document(captions={"template": "slam"})}
    job = (await studio.http.post(f"{BASE}/ai", json=styled)).json()["id"]
    assert json.loads(studio.pool.data[ai.request_key(job)])["fx_allowed"] is True
