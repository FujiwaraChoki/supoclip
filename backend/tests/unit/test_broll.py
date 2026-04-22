"""Tests for ``src/broll.py`` pure helpers + model validation.

The Pexels HTTP calls are mocked — only the pure resolution logic and
Pydantic input guards are under test here.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.broll import BRollSuggestion, BRollVideo, get_video_download_url


class TestGetVideoDownloadUrl:
    def test_prefers_portrait_hd_match(self):
        video = {
            "video_files": [
                {"quality": "hd", "width": 1920, "height": 1080, "link": "landscape_hd"},
                {"quality": "hd", "width": 1080, "height": 1920, "link": "portrait_hd"},
            ]
        }
        assert get_video_download_url(video, quality="hd", orientation="portrait") == "portrait_hd"

    def test_landscape_match(self):
        video = {
            "video_files": [
                {"quality": "hd", "width": 1080, "height": 1920, "link": "portrait"},
                {"quality": "hd", "width": 1920, "height": 1080, "link": "landscape"},
            ]
        }
        assert (
            get_video_download_url(video, quality="hd", orientation="landscape")
            == "landscape"
        )

    def test_falls_back_to_any_matching_quality(self):
        # No orientation match — falls back to first matching-quality link.
        video = {
            "video_files": [
                {"quality": "hd", "width": 1080, "height": 1920, "link": "p1"},
                {"quality": "hd", "width": 720, "height": 1280, "link": "p2"},
            ]
        }
        url = get_video_download_url(video, quality="hd", orientation="landscape")
        assert url in ("p1", "p2")

    def test_last_resort_returns_first_file(self):
        # No quality match — uses the very first entry.
        video = {
            "video_files": [
                {"quality": "sd", "width": 540, "height": 960, "link": "sd1"},
            ]
        }
        assert get_video_download_url(video, quality="hd") == "sd1"

    def test_empty_files_returns_none(self):
        assert get_video_download_url({"video_files": []}) is None

    def test_missing_files_key_returns_none(self):
        assert get_video_download_url({}) is None


class TestBRollVideoModel:
    def test_valid_construction(self):
        video = BRollVideo(
            id=1,
            width=1080,
            height=1920,
            duration=10,
            url="https://pexels.com/v/1",
            image="https://pexels.com/thumb/1.jpg",
            video_files=[{"link": "a", "quality": "hd"}],
            user={"name": "Someone"},
        )
        assert video.id == 1
        assert video.duration == 10
        assert video.video_files[0]["link"] == "a"

    def test_missing_required_field(self):
        with pytest.raises(Exception):
            BRollVideo(
                id=1,
                width=1080,
                height=1920,
                duration=10,
                url="https://pexels.com/v/1",
                image="https://pexels.com/thumb/1.jpg",
                video_files=[],
                # user missing
            )


class TestBRollSuggestionModel:
    def test_valid_short_suggestion(self):
        sugg = BRollSuggestion(
            keyword="sunset",
            timestamp=5.0,
            duration=3.0,
            context="discussing weather",
        )
        assert sugg.keyword == "sunset"
        assert sugg.video_url is None
        assert sugg.local_path is None

    @pytest.mark.parametrize("duration", [1.0, 1.99, 5.01, 10.0])
    def test_duration_out_of_range(self, duration):
        with pytest.raises(Exception):
            BRollSuggestion(
                keyword="sunset",
                timestamp=5.0,
                duration=duration,
                context="context",
            )

    @pytest.mark.parametrize("duration", [2.0, 3.5, 5.0])
    def test_duration_at_boundaries(self, duration):
        sugg = BRollSuggestion(
            keyword="sunset",
            timestamp=0.0,
            duration=duration,
            context="context",
        )
        assert sugg.duration == duration


@pytest.mark.asyncio
class TestSearchBrollVideosHttpHandling:
    async def test_returns_empty_when_no_api_key(self, monkeypatch):
        from src import broll as broll_module

        monkeypatch.setattr(broll_module.config, "pexels_api_key", None)
        result = await broll_module.search_broll_videos("cats")
        assert result == []

    async def test_http_error_returns_empty(self, monkeypatch):
        from src import broll as broll_module

        monkeypatch.setattr(broll_module.config, "pexels_api_key", "fake-key")

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

            async def get(self, *args, **kwargs):
                raise RuntimeError("network down")

        monkeypatch.setattr(broll_module.httpx, "AsyncClient", FakeAsyncClient)
        result = await broll_module.search_broll_videos("cats")
        assert result == []
