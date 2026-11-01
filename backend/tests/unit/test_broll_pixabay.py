from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

import src.broll as broll
from src.broll import (
    BRollSession,
    broll_configured,
    broll_provider,
    choose_video,
    pick_video_file,
    plan_clip_broll,
    placement_to_editor_clip,
    search_footage,
    search_stock_videos,
)

CDN = "https://cdn.pixabay.com/video/2024/01/01/{n}-{size}.mp4"


def _hit(hit_id, duration=10, tags="coffee, cup", large=True):
    def rendition(size, w, h):
        return {"url": CDN.format(n=hit_id, size=size), "width": w, "height": h, "size": 1}

    return {
        "id": hit_id,
        "duration": duration,
        "tags": tags,
        "pageURL": f"https://pixabay.com/videos/id-{hit_id}/",
        "picture_id": "abc",
        "user": "A Filmmaker",
        "videos": {
            "large": rendition("large", 3840, 2160) if large else {"url": "", "width": 0, "height": 0},
            "medium": rendition("medium", 1920, 1080),
            "small": rendition("small", 1280, 720),
            "tiny": rendition("tiny", 960, 540),
        },
    }


@pytest.fixture
def config(monkeypatch, tmp_path):
    cfg = SimpleNamespace(pexels_api_key=None, pixabay_api_key="px-key", temp_dir=str(tmp_path))
    monkeypatch.setattr(broll, "get_config", lambda: cfg)
    monkeypatch.setattr(broll, "_search_cache", {})
    monkeypatch.setattr(broll, "_rate_limited_until", 0.0)
    return cfg


def _mock_http(monkeypatch, handler):
    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        broll.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs),
    )


def test_provider_prefers_pexels_then_pixabay(config):
    assert (broll_provider(), broll_configured()) == ("pixabay", True)
    config.pexels_api_key = "pk"
    assert broll_provider() == "pexels"
    config.pexels_api_key = config.pixabay_api_key = None
    assert (broll_provider(), broll_configured()) == (None, False)


@pytest.mark.asyncio
async def test_pixabay_search_returns_pexels_shaped_videos(config, monkeypatch):
    calls = []

    def handler(request):
        calls.append(request)
        params = request.url.params
        assert (params["key"], params["video_type"], params["safesearch"]) == ("px-key", "film", "true")
        return httpx.Response(200, json={"hits": [_hit(5, large=False), {"id": 6, "videos": {}}]})

    _mock_http(monkeypatch, handler)

    videos = await search_stock_videos("coffee shop")
    assert await search_stock_videos("Coffee  Shop") == videos
    assert len(calls) == 1
    assert [v["id"] for v in videos] == [5]
    video = videos[0]
    assert video["tags"] == ["coffee", "cup"]
    assert video["user"] == {"name": "A Filmmaker"}
    assert pick_video_file(video, 1080, "landscape")["link"].endswith("5-medium.mp4")
    assert choose_video(videos, 5, "landscape", query="coffee") is video
    assert choose_video(videos, 5, "portrait") is None


@pytest.mark.asyncio
async def test_pixabay_rate_limit_and_errors_skip(config, monkeypatch):
    statuses = iter([429, 500])
    _mock_http(monkeypatch, lambda request: httpx.Response(next(statuses)))

    assert await search_stock_videos("coffee") == []
    assert await search_stock_videos("mountain") == []


@pytest.mark.asyncio
async def test_plan_clip_broll_uses_pixabay(config, monkeypatch):
    async def fake_plan(*_args):
        return [SimpleNamespace(start=4.0, duration=3.0, query="coffee", fallback_query="cup")]

    async def fake_ensure(placement):
        assert placement["provider"] == "pixabay"
        return Path("/tmp/footage.mp4")

    _mock_http(monkeypatch, lambda request: httpx.Response(200, json={"hits": [_hit(9)]}))
    monkeypatch.setattr("src.ai.plan_broll_cues", fake_plan)
    monkeypatch.setattr(broll, "ensure_broll_file", fake_ensure)
    words = [{"text": "coffee", "start": i * 0.4, "end": i * 0.4 + 0.3} for i in range(30)]

    placements = await plan_clip_broll(words, 12.0, orientation="landscape", session=BRollSession())

    assert len(placements) == 1
    assert placements[0]["provider"] == "pixabay"
    assert placements[0]["pexels_id"] == 9
    assert placements[0]["file_url"].startswith("https://cdn.pixabay.com/")
    assert placements[0]["credit"] == "A Filmmaker"


@pytest.mark.asyncio
async def test_editor_search_and_conversion_for_pixabay(config, monkeypatch):
    _mock_http(monkeypatch, lambda request: httpx.Response(200, json={"hits": [_hit(3)]}))

    results = await search_footage("coffee", "landscape", 1080)

    assert results[0]["provider"] == "pixabay"
    assert results[0]["fileUrl"].startswith("https://cdn.pixabay.com/")
    assert broll.footage_from_video({"provider": "pixabay", "id": 1, "duration": 5,
                                     "video_files": [{"link": "https://evil.example/a.mp4", "width": 1920, "height": 1080}]},
                                    1080, "landscape") is None
    placement = {
        "start": 1, "duration": 2, "provider": "pixabay", "pexels_id": 3, "file_url": results[0]["fileUrl"],
        "width": results[0]["width"], "height": results[0]["height"], "footage_duration": 10,
    }
    clip = placement_to_editor_clip(placement, 0, 10)
    assert clip["footage"]["provider"] == "pixabay"
    assert broll.editor_piece_to_placement({"start": 1, "duration": 2, "footage": clip["footage"]})["provider"] == "pixabay"


def test_pixabay_cache_files_do_not_collide_with_pexels(config):
    entry = {"pexels_id": 7, "width": 1920, "height": 1080}
    assert broll.broll_file_path(entry).name.startswith("pexels_7_")
    assert broll.broll_file_path({**entry, "provider": "pixabay"}).name.startswith("pixabay_7_")


@pytest.mark.asyncio
async def test_fetch_pixabay_video_by_id(config, monkeypatch):
    def handler(request):
        assert request.url.params["id"] == "4"
        return httpx.Response(200, json={"hits": [_hit(4)]})

    _mock_http(monkeypatch, handler)

    assert (await broll.fetch_pixabay_video(4))["id"] == 4
