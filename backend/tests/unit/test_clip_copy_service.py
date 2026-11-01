import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
import pytest

from src.api.routes import tasks
from src.database import get_db
from src.services import clip_copy_service as svc
from src.services.clip_copy_service import ClipCopyError, normalize_clip_copy

RAW = {
    "tiktok": {"title": "Hook", "description": "Watch this", "hashtags": ["a"]},
    "youtube_shorts": {"title": "T", "description": "D", "hashtags": ["a"]},
    "instagram_reels": {"title": "T", "description": "D", "hashtags": ["a"]},
}
COPY = {"generated_at": "2026-10-31T00:00:00+00:00", "platforms": normalize_clip_copy(RAW)}


def test_title_truncated_at_word_boundary():
    out = normalize_clip_copy({"tiktok": {"title": "word " * 40}})
    title = out["tiktok"]["title"]
    assert len(title) <= 100 and title.endswith("word") and not title.endswith(" ")
    out = normalize_clip_copy({"instagram_reels": {"title": "x" * 300}})
    assert len(out["instagram_reels"]["title"]) == 125


def test_description_limits_and_whitespace():
    out = normalize_clip_copy({"tiktok": {"description": "  hi   there " + "a " * 400}})
    assert len(out["tiktok"]["description"]) <= 300
    assert out["tiktok"]["description"].startswith("hi there a")


def test_hashtag_cleanup_dedupe_and_caps():
    tags = ["##Fun Stuff", "#fun stuff", "FUNSTUFF", "#", "  ", "café!", "a_b", "#x-y", "#4", "#5", "#6"]
    out = normalize_clip_copy({"tiktok": {"hashtags": tags}})["tiktok"]["hashtags"]
    assert out == ["#FunStuff", "#café", "#a_b", "#xy", "#4"]
    many = [f"t{i}" for i in range(20)]
    result = normalize_clip_copy({"instagram_reels": {"hashtags": many}})
    assert len(result["instagram_reels"]["hashtags"]) == 8


def test_shorts_tag_enforced():
    out = normalize_clip_copy({"youtube_shorts": {"hashtags": ["a", "b", "c", "d", "e", "f"]}})
    assert out["youtube_shorts"]["hashtags"] == ["#a", "#b", "#c", "#d", "#shorts"]
    out = normalize_clip_copy({"youtube_shorts": {"hashtags": ["a"]}})
    assert out["youtube_shorts"]["hashtags"] == ["#a", "#shorts"]
    out = normalize_clip_copy({"youtube_shorts": {"hashtags": ["#Shorts", "a"]}})
    assert out["youtube_shorts"]["hashtags"] == ["#Shorts", "#a"]


def test_hashtags_removed_from_description():
    out = normalize_clip_copy({"tiktok": {"description": "Great clip #fun #viral\nFollow us"}})
    assert out["tiktok"]["description"] == "Great clip\nFollow us"


def test_missing_platforms_still_shaped():
    out = normalize_clip_copy({})
    assert set(out) == {"tiktok", "youtube_shorts", "instagram_reels"}
    assert out["youtube_shorts"]["hashtags"] == ["#shorts"]


def _fake_agent(output=None, exc=None):
    async def run(prompt):
        if exc:
            raise exc
        return SimpleNamespace(output=output)

    return SimpleNamespace(run=AsyncMock(side_effect=run))


def _output():
    p = svc.PlatformCopy(title="t", description="d #x", hashtags=["x"])
    return svc.ClipCopyOutput(tiktok=p, youtube_shorts=p, instagram_reels=p)


async def test_returns_stored_copy_without_model(monkeypatch):
    build = AsyncMock()
    monkeypatch.setattr(svc, "_build_agent", build)
    repo = SimpleNamespace(set_clip_copy=AsyncMock())
    got = await svc.get_or_generate_clip_copy(None, repo, {"id": "c", "copy": COPY}, "s")
    assert got is COPY
    build.assert_not_called()
    repo.set_clip_copy.assert_not_called()


@pytest.mark.parametrize("stored,regen", [(None, False), (COPY, True)])
async def test_generates_and_persists(monkeypatch, stored, regen):
    agent = _fake_agent(_output())
    monkeypatch.setattr(svc, "_build_agent", lambda: agent)
    repo = SimpleNamespace(set_clip_copy=AsyncMock())
    clip = {"id": "c", "text": "hello", "copy": stored}
    got = await svc.get_or_generate_clip_copy("db", repo, clip, "src", regen)
    assert got["platforms"]["youtube_shorts"]["hashtags"] == ["#x", "#shorts"]
    assert got["platforms"]["tiktok"]["description"] == "d"
    assert got["generated_at"]
    repo.set_clip_copy.assert_awaited_once_with("db", "c", got)
    assert "hello" in agent.run.await_args.args[0]


async def test_model_exception_is_typed(monkeypatch):
    monkeypatch.setattr(svc, "_build_agent", lambda: _fake_agent(exc=RuntimeError("provider-detail")))
    with pytest.raises(ClipCopyError) as e:
        await svc.generate_clip_copy({"text": "x"}, None)
    assert "provider-detail" not in str(e.value)


async def test_timeout_is_typed(monkeypatch):
    async def slow(prompt):
        await asyncio.sleep(1)

    monkeypatch.setattr(svc, "_build_agent", lambda: SimpleNamespace(run=slow))
    monkeypatch.setattr(svc, "GENERATION_TIMEOUT_SECONDS", 0.01)
    with pytest.raises(ClipCopyError):
        await svc.generate_clip_copy({"text": "x"}, None)


def test_unconfigured_llm_is_typed(monkeypatch):
    monkeypatch.setattr(svc.ai, "_get_missing_llm_key_error", lambda *a: "missing key")
    with pytest.raises(ClipCopyError):
        svc._build_agent()


# ---- routes ----

@pytest.fixture
async def client(monkeypatch):
    task = {"id": "t1", "user_id": "owner", "status": "completed", "source_title": "Src"}
    clip = {"id": "c1", "task_id": "t1", "text": "hello world", "copy": None}
    repo = SimpleNamespace(
        get_clip_by_id=AsyncMock(return_value=clip), set_clip_copy=AsyncMock()
    )
    service = SimpleNamespace(
        task_repo=SimpleNamespace(get_task_by_id=AsyncMock(return_value=task)),
        clip_repo=repo,
        get_task_with_clips=AsyncMock(
            return_value={**task, "clips": [{"id": "c1", "copy": None}, {"id": "c2", "copy": COPY}]}
        ),
    )
    monkeypatch.setattr(tasks, "TaskService", lambda db: service)
    user = AsyncMock(return_value="owner")
    monkeypatch.setattr(tasks, "_get_user_id_from_headers", user)
    app = FastAPI()
    app.include_router(tasks.router)
    app.dependency_overrides[get_db] = lambda: SimpleNamespace()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http, service, clip, user


async def test_route_200_shape(client, monkeypatch):
    http, service, clip, _ = client
    gen = AsyncMock(return_value=COPY)
    monkeypatch.setattr(svc, "generate_clip_copy", gen)
    res = await http.post("/tasks/t1/clips/c1/copy")
    assert res.status_code == 200 and res.json() == {"copy": COPY}
    gen.assert_awaited_once_with(clip, "Src")
    service.clip_repo.set_clip_copy.assert_awaited_once()


async def test_route_returns_stored_unless_regenerate(client, monkeypatch):
    http, service, clip, _ = client
    clip["copy"] = COPY
    gen = AsyncMock(return_value={"generated_at": "new", "platforms": COPY["platforms"]})
    monkeypatch.setattr(svc, "generate_clip_copy", gen)
    assert (await http.post("/tasks/t1/clips/c1/copy", json={})).json() == {"copy": COPY}
    gen.assert_not_called()
    res = await http.post("/tasks/t1/clips/c1/copy", json={"regenerate": True})
    assert res.json()["copy"]["generated_at"] == "new"


async def test_route_other_users_task_rejected(client):
    http, service, _, user = client
    user.return_value = "outsider"
    res = await http.post("/tasks/t1/clips/c1/copy")
    assert res.status_code == 403
    service.clip_repo.get_clip_by_id.assert_not_called()


async def test_route_404s(client):
    http, service, clip, _ = client
    service.task_repo.get_task_by_id.return_value = None
    assert (await http.post("/tasks/t1/clips/c1/copy")).status_code == 404
    service.task_repo.get_task_by_id.return_value = {"id": "t1", "user_id": "owner"}
    clip["task_id"] = "other"
    assert (await http.post("/tasks/t1/clips/c1/copy")).status_code == 404
    service.clip_repo.get_clip_by_id.return_value = None
    assert (await http.post("/tasks/t1/clips/c1/copy")).status_code == 404


async def test_route_422_without_text(client):
    http, _, clip, _ = client
    clip["text"] = "   "
    assert (await http.post("/tasks/t1/clips/c1/copy")).status_code == 422


async def test_route_503_mapping(client, monkeypatch):
    http, *_ = client
    monkeypatch.setattr(svc, "generate_clip_copy", AsyncMock(side_effect=ClipCopyError("nope")))
    res = await http.post("/tasks/t1/clips/c1/copy")
    assert res.status_code == 503 and res.json() == {"detail": "nope"}


async def test_get_task_includes_copy(client):
    http, *_ = client
    res = await http.get("/tasks/t1")
    clips = res.json()["clips"]
    assert clips[0]["copy"] is None and clips[1]["copy"] == COPY
