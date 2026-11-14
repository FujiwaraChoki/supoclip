"""edit_fx.reactions: the GIPHY provider behind ReactionCue (§3.2, WP13).

All HTTP goes through an ``httpx.MockTransport``; nothing here reaches the network.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
import threading
import time
import weakref
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import pytest

from src.edit_fx import reactions
from src.edit_fx.contracts import EditStyle, FxPlan, ReactionCue, SfxCue

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
MB = 1024 * 1024


def _item(asset_id: str, *, size: int = 2 * MB, host: str = "media2.giphy.com", renditions=("original_mp4",)):
    images = {}
    for name in renditions:
        images[name] = {"mp4": f"https://{host}/media/{asset_id}/{name}.mp4?cid=abc&rid=giphy.mp4&ct=g",
                        "mp4_size": str(size), "width": "480", "height": "360"}
    return {"id": asset_id, "type": "gif", "images": images}


class FakeGiphy:
    """Records every request; answers searches with ``items`` and media GETs with ``video``."""

    def __init__(self, items=None, *, search_status: int = 200, video: bytes = b"", media_status: int = 200):
        self.items = items if items is not None else [_item(f"gif{i}") for i in range(10)]
        self.search_status = search_status
        self.video = video
        self.media_status = media_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.host == "api.giphy.com":
            if self.search_status != 200:
                return httpx.Response(self.search_status, json={"message": "nope"})
            return httpx.Response(200, json={"data": self.items, "meta": {"status": 200}})
        if request.url.host.endswith("giphy.com"):
            return httpx.Response(self.media_status, content=self.video, headers={"content-type": "video/mp4"})
        return httpx.Response(599)

    @property
    def searches(self):
        return [r for r in self.requests if r.url.host == "api.giphy.com"]

    @property
    def downloads(self):
        return [r for r in self.requests if r.url.host != "api.giphy.com"]


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(reactions, "_search_cache", {})
    monkeypatch.setattr(reactions, "_file_locks", weakref.WeakKeyDictionary())
    monkeypatch.setattr(reactions, "_rate_limited_until", 0.0)
    monkeypatch.setattr(reactions, "reaction_cache_dir", lambda: tmp_path / "cache")
    monkeypatch.setattr(reactions, "_storage", lambda: None)
    monkeypatch.setenv("REACTIONS_PROVIDER", "giphy")
    monkeypatch.setenv("GIPHY_API_KEY", "test-key")


@pytest.fixture
def giphy(monkeypatch):
    def install(fake: FakeGiphy) -> FakeGiphy:
        monkeypatch.setattr(reactions, "_transport", httpx.MockTransport(fake))
        return fake

    return install


@pytest.fixture(scope="module")
def tiny_mp4(tmp_path_factory) -> bytes:
    if shutil.which("ffmpeg") is None:
        pytest.skip("ffmpeg not installed")
    path = tmp_path_factory.mktemp("gif") / "reaction.mp4"
    subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=160x120:rate=10",
         "-t", "0.5", "-pix_fmt", "yuv420p", "-c:v", "libx264", "-y", str(path)],
        check=True,
    )
    return path.read_bytes()


def _reaction(cue_id: str = "g.react0", query: str = "mind blown", **extra) -> ReactionCue:
    return ReactionCue(id=cue_id, start=4.0, end=6.0, query=query, **extra)


def _plan(*cues) -> FxPlan:
    return FxPlan(style=EditStyle(intensity="bold", reactions=True), template="default", seed=7,
                  edit_duration=30.0, cues=tuple(cues))


SFX = SfxCue(id="b1.sfx", start=2.0, end=2.0, sound="whoosh")


def _resolved_cue(asset_id: str = "gif3") -> ReactionCue:
    return _reaction(asset_id=asset_id, url=f"https://media2.giphy.com/media/{asset_id}/original_mp4.mp4")


# ----------------------------------------------------------------------------- availability


def test_unavailable_without_key_or_with_provider_off(monkeypatch):
    assert reactions.reactions_available() is True
    monkeypatch.setenv("GIPHY_API_KEY", "")
    assert reactions.reactions_available() is False
    monkeypatch.setenv("GIPHY_API_KEY", "test-key")
    monkeypatch.setenv("REACTIONS_PROVIDER", "off")
    assert reactions.reactions_available() is False
    monkeypatch.delenv("REACTIONS_PROVIDER")
    assert reactions.reactions_available() is False  # off is the default


@pytest.mark.parametrize("env", [{"GIPHY_API_KEY": ""}, {"REACTIONS_PROVIDER": "off"}])
async def test_unavailable_drops_unresolved_cues_silently_without_http(monkeypatch, giphy, env):
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    fake = giphy(FakeGiphy())
    plan = _plan(SFX, _reaction())
    out = await reactions.attach_reactions(plan, rng_seed=plan.seed)
    assert [c.kind for c in out.cues] == ["sfx"]
    assert fake.requests == []


async def test_provider_off_never_serves_files(monkeypatch, giphy, tmp_path):
    monkeypatch.setenv("REACTIONS_PROVIDER", "off")
    fake = giphy(FakeGiphy(video=b"x"))
    assert await reactions.ensure_reaction_file(_resolved_cue(), tmp_path / "scratch") is None
    assert fake.requests == []


async def test_plan_without_reactions_is_returned_unchanged(giphy):
    fake = giphy(FakeGiphy())
    plan = _plan(SFX)
    assert await reactions.attach_reactions(plan, rng_seed=1) is plan
    assert fake.requests == []


# ----------------------------------------------------------------------------- attach (happy path)


async def test_happy_path_picks_a_seeded_top8_rendition(giphy):
    fake = giphy(FakeGiphy())
    plan = _plan(SFX, _reaction())
    out = await reactions.attach_reactions(plan, rng_seed=plan.seed)

    [search] = fake.searches
    params = dict(search.url.params)
    assert params["rating"] == "pg-13" and params["limit"] == "8" and params["q"] == "mind blown"
    assert params["api_key"] == "test-key"

    cue = next(c for c in out.cues if c.kind == "reaction")
    assert cue.asset_id in {f"gif{i}" for i in range(8)}
    assert cue.url == f"https://media2.giphy.com/media/{cue.asset_id}/original_mp4.mp4"  # tracking query stripped
    assert out.cues[0] == SFX
    # Everything else about the plan is untouched; the result still validates.
    assert FxPlan.model_validate(out.model_dump(mode="json")) == out
    assert out.model_dump(exclude={"cues"}) == plan.model_dump(exclude={"cues"})

    again = await reactions.attach_reactions(plan, rng_seed=plan.seed)
    assert again == out                                   # deterministic for a seed
    assert len(fake.searches) == 1                        # second search served from the in-process cache


async def test_seed_spreads_picks_across_the_top_results(giphy):
    giphy(FakeGiphy())
    picks = set()
    for seed in range(40):
        out = await reactions.attach_reactions(_plan(_reaction()), rng_seed=seed)
        picks.add(out.cues[0].asset_id)
    assert len(picks) >= 4 and picks <= {f"gif{i}" for i in range(8)}


async def test_rendition_must_fit_8mb_and_be_a_giphy_mp4(giphy):
    items = [
        _item("big", size=9 * MB),                                             # too large
        _item("evil", host="evil.example.com"),                               # not a GIPHY media host
        {"id": "nomp4", "images": {"original": {"url": "https://media.giphy.com/x.gif"}}},
        _item("fallback", renditions=()) | {"images": {
            "original_mp4": {"mp4": "https://media.giphy.com/media/fallback/a.mp4", "mp4_size": str(20 * MB)},
            "downsized_small": {"mp4": "https://media.giphy.com/media/fallback/s.mp4", "mp4_size": "300000"}}},
    ]
    giphy(FakeGiphy(items))
    out = await reactions.attach_reactions(_plan(_reaction()), rng_seed=3)
    [cue] = out.cues
    assert cue.asset_id == "fallback" and cue.url == "https://media.giphy.com/media/fallback/s.mp4"


async def test_two_reactions_avoid_the_same_gif(giphy):
    giphy(FakeGiphy([_item("only"), _item("second")]))
    out = await reactions.attach_reactions(
        _plan(_reaction("g.react0"), _reaction("g.react1")), rng_seed=11)
    assert sorted(c.asset_id for c in out.cues) == ["only", "second"]


async def test_resolved_cues_are_kept_without_http(giphy):
    fake = giphy(FakeGiphy())
    plan = _plan(_resolved_cue())
    out = await reactions.attach_reactions(plan, rng_seed=1)
    assert out.cues == plan.cues and fake.requests == []


# ----------------------------------------------------------------------------- attach (errors → drop)


@pytest.mark.parametrize("fake", [
    FakeGiphy(search_status=500),
    FakeGiphy(search_status=403),
    FakeGiphy(items=[]),
    FakeGiphy(items=[_item("huge", size=50 * MB)]),
])
async def test_search_errors_drop_the_cue(giphy, fake):
    giphy(fake)
    out = await reactions.attach_reactions(_plan(SFX, _reaction()), rng_seed=1)
    assert [c.kind for c in out.cues] == ["sfx"]


async def test_network_error_and_bad_json_drop_the_cue(monkeypatch):
    def broken(request):
        raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(reactions, "_transport", httpx.MockTransport(broken))
    out = await reactions.attach_reactions(_plan(SFX, _reaction()), rng_seed=1)
    assert [c.kind for c in out.cues] == ["sfx"]

    monkeypatch.setattr(reactions, "_transport", httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"<html>not json")))
    out = await reactions.attach_reactions(_plan(_reaction("g.react1", "facepalm")), rng_seed=1)
    assert out.cues == ()


async def test_rate_limit_pauses_further_searches(giphy):
    fake = giphy(FakeGiphy(search_status=429))
    out = await reactions.attach_reactions(_plan(_reaction("g.react0", "a"), _reaction("g.react1", "b")), rng_seed=1)
    assert out.cues == ()
    assert len(fake.searches) == 1


# ----------------------------------------------------------------------------- files


class FakeBucket:
    enabled = True

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.uploads: list[tuple[str, dict]] = []

    def key(self, *parts):
        return "/".join(("media", *parts))

    def reference(self, key):
        return f"s3://bucket/{key}"

    def fetch(self, reference, path: Path) -> bool:
        key = reference.removeprefix("s3://bucket/")
        if key not in self.objects:
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.objects[key])
        return True

    def upload_file(self, path: Path, key: str, **extra):
        self.objects[key] = Path(path).read_bytes()
        self.uploads.append((key, extra))
        return self.reference(key)


async def test_ensure_downloads_into_scratch(giphy, tiny_mp4, tmp_path):
    fake = giphy(FakeGiphy(video=tiny_mp4))
    cue = _resolved_cue()
    scratch = tmp_path / "render"
    path = await reactions.ensure_reaction_file(cue, scratch)
    assert path is not None and path.parent == scratch and path.read_bytes() == tiny_mp4
    assert [str(r.url) for r in fake.downloads] == [cue.url]

    # A second render on this machine reuses the local cache.
    other = await reactions.ensure_reaction_file(cue, tmp_path / "render2")
    assert other is not None and other.read_bytes() == tiny_mp4
    assert len(fake.downloads) == 1


async def test_bucket_cached_second_call_makes_no_http_request(monkeypatch, giphy, tiny_mp4, tmp_path):
    bucket = FakeBucket()
    monkeypatch.setattr(reactions, "_storage", lambda: bucket)
    fake = giphy(FakeGiphy(video=tiny_mp4))
    cue = _resolved_cue("gif5")

    first = await reactions.ensure_reaction_file(cue, tmp_path / "a")
    assert first is not None and len(fake.requests) == 1
    [(key, extra)] = bucket.uploads
    assert key.startswith("media/fx-reactions/giphy/giphy_gif5_") and key.endswith(".mp4")
    assert "immutable" in extra["CacheControl"]

    # Another machine: empty local cache, same bucket.
    shutil.rmtree(tmp_path / "cache")
    second = await reactions.ensure_reaction_file(cue, tmp_path / "b")
    assert second is not None and second.read_bytes() == tiny_mp4
    assert len(fake.requests) == 1                        # no HTTP request at all
    assert len(bucket.uploads) == 1


async def test_a_bucket_failure_falls_back_to_giphy(monkeypatch, giphy, tiny_mp4, tmp_path):
    class BrokenBucket(FakeBucket):
        def fetch(self, reference, path):
            raise OSError("bucket down")

        def upload_file(self, path, key, **extra):
            raise OSError("bucket down")

    monkeypatch.setattr(reactions, "_storage", lambda: BrokenBucket())
    giphy(FakeGiphy(video=tiny_mp4))
    assert await reactions.ensure_reaction_file(_resolved_cue(), tmp_path / "a") is not None


@pytest.mark.parametrize("fake", [
    FakeGiphy(video=b"<html>error page</html>"),          # 200 but not a video
    FakeGiphy(video=b"", media_status=200),                # empty body
    FakeGiphy(video=b"x", media_status=404),
    FakeGiphy(video=b"x" * (reactions.MAX_RENDITION_BYTES + 1)),
])
@needs_ffmpeg
async def test_bad_downloads_return_none_and_leave_nothing(giphy, tmp_path, fake):
    giphy(fake)
    assert await reactions.ensure_reaction_file(_resolved_cue(), tmp_path / "scratch") is None
    for directory in (tmp_path / "cache", tmp_path / "scratch"):
        assert not directory.exists() or not list(directory.iterdir())


async def test_redirects_outside_giphy_are_refused(monkeypatch, tmp_path):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        if request.url.host == "media2.giphy.com":
            return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data"})
        return httpx.Response(200, content=b"secret")

    monkeypatch.setattr(reactions, "_transport", httpx.MockTransport(handler))
    assert await reactions.ensure_reaction_file(_resolved_cue(), tmp_path / "scratch") is None
    assert seen == ["https://media2.giphy.com/media/gif3/original_mp4.mp4"]


@pytest.mark.parametrize("url", [
    "https://evil.example.com/media/x.mp4",
    "http://media.giphy.com/media/x.mp4",
    "file:///etc/passwd",
    "https://media.giphy.com.evil.com/x.mp4",
    "https://user:pw@media.giphy.com/x.mp4",
    "https://media.giphy.com/media/../../x.mp4",
    "https://media.giphy.com:99999/media/x.mp4",
    "https://media.giphy.com:abc/media/x.mp4",
    "https://media.giphy.com:8443/media/x.mp4",
    "",
])
async def test_user_edited_cue_urls_outside_giphy_are_never_fetched(giphy, tmp_path, url):
    fake = giphy(FakeGiphy(video=b"x"))
    cue = _reaction(asset_id="abc", url=url)
    assert await reactions.ensure_reaction_file(cue, tmp_path / "scratch") is None
    assert fake.requests == []


async def test_a_malformed_cue_url_never_makes_attach_raise(giphy):
    # A studio document is user-editable: a resolved-looking cue with a broken port is re-resolved, not fatal.
    giphy(FakeGiphy())
    broken = _reaction(asset_id="gif3", url="https://media2.giphy.com:99999/media/gif3/original_mp4.mp4")
    out = await reactions.attach_reactions(_plan(SFX, broken), rng_seed=3)
    [cue] = [c for c in out.cues if c.kind == "reaction"]
    assert cue.url.startswith("https://media2.giphy.com/media/") and ":99999" not in cue.url


async def test_a_trickling_download_is_cut_off_by_the_deadline(monkeypatch, tmp_path):
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, content=b"late")

    monkeypatch.setattr(reactions, "DOWNLOAD_DEADLINE_SECONDS", 0.1)
    monkeypatch.setattr(reactions, "_transport", httpx.MockTransport(slow))
    started = time.monotonic()
    assert await reactions.ensure_reaction_file(_resolved_cue(), tmp_path / "scratch") is None
    assert time.monotonic() - started < 2


async def test_a_slow_search_is_cut_off_and_drops_the_cue(monkeypatch):
    async def slow(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"data": []})

    monkeypatch.setattr(reactions, "SEARCH_DEADLINE_SECONDS", 0.1)
    monkeypatch.setattr(reactions, "_transport", httpx.MockTransport(slow))
    out = await reactions.attach_reactions(_plan(SFX, _reaction()), rng_seed=1)
    assert [c.kind for c in out.cues] == ["sfx"]


@needs_ffmpeg
def test_renders_on_separate_event_loops_share_the_cache(giphy, tiny_mp4, tmp_path):
    # Clip generation runs in worker threads; each may drive its own loop. A lock bound to another loop must not
    # turn a cache hit into a dropped reaction.
    giphy(FakeGiphy(video=tiny_mp4))
    cue = _resolved_cue()
    barrier = threading.Barrier(3)

    def render(n):
        barrier.wait()
        return asyncio.run(reactions.ensure_reaction_file(cue, tmp_path / f"render{n}"))

    with ThreadPoolExecutor(3) as pool:
        paths = list(pool.map(render, range(3)))
    assert all(p is not None and p.read_bytes() == tiny_mp4 for p in paths)


async def test_unsafe_asset_ids_are_never_used_in_paths(giphy, tmp_path):
    fake = giphy(FakeGiphy(video=b"x"))
    cue = _reaction(asset_id="../../etc", url="https://media.giphy.com/media/x/a.mp4")
    assert await reactions.ensure_reaction_file(cue, tmp_path / "scratch") is None
    assert fake.requests == []


def test_prune_keeps_recent_files_and_drops_stale_ones(tmp_path):
    cache = tmp_path / "cache"
    cache.mkdir()
    old, fresh = cache / "giphy_old_x.mp4", cache / "giphy_new_y.mp4"
    old.write_bytes(b"1")
    fresh.write_bytes(b"2")
    import os
    import time

    stale = time.time() - reactions.CACHE_MAX_AGE_SECONDS - 10
    os.utime(old, (stale, stale))
    reactions.prune_reaction_cache()
    assert not old.exists() and fresh.exists()


async def test_failures_never_log_the_key(monkeypatch, caplog):
    # The search URL carries the key in its query string: failures are logged by exception type only.
    def broken(request):
        raise httpx.ConnectError(f"cannot reach {request.url}", request=request)

    monkeypatch.setattr(reactions, "_transport", httpx.MockTransport(broken))
    caplog.set_level("DEBUG")
    await reactions.attach_reactions(_plan(_reaction()), rng_seed=1)
    assert caplog.records
    assert "test-key" not in json.dumps([r.getMessage() for r in caplog.records])
