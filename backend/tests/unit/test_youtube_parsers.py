"""Tests for pure parsers in ``src/_youtube_parsers.py``."""

import pytest

from src._youtube_parsers import (
    _normalize_upload_date,
    _parse_iso8601_duration_to_seconds,
    _parse_optional_int,
    _pick_best_thumbnail,
    get_youtube_video_id,
    validate_youtube_url,
)


class TestParseIso8601Duration:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("PT0S", 0),
            ("PT30S", 30),
            ("PT1M", 60),
            ("PT1M30S", 90),
            ("PT1H", 3600),
            ("PT1H2M3S", 3723),
            ("P1DT2H3M4S", 86400 + 7200 + 180 + 4),
            ("PT", 0),
        ],
    )
    def test_valid(self, value, expected):
        assert _parse_iso8601_duration_to_seconds(value) == expected

    @pytest.mark.parametrize("value", ["garbage", "1H2M", ""])
    def test_invalid_raises(self, value):
        with pytest.raises(ValueError):
            _parse_iso8601_duration_to_seconds(value)

    def test_bare_p_parses_to_zero(self):
        # "P" is technically valid ISO-8601 syntax (all components optional).
        assert _parse_iso8601_duration_to_seconds("P") == 0


class TestPickBestThumbnail:
    def test_none_returns_none(self):
        assert _pick_best_thumbnail(None) is None
        assert _pick_best_thumbnail({}) is None

    def test_prefers_maxres(self):
        thumbnails = {
            "default": {"url": "d"},
            "medium": {"url": "m"},
            "maxres": {"url": "mr"},
        }
        assert _pick_best_thumbnail(thumbnails) == "mr"

    def test_falls_through_tiers(self):
        assert _pick_best_thumbnail({"high": {"url": "h"}}) == "h"
        assert _pick_best_thumbnail({"medium": {"url": "m"}}) == "m"
        assert _pick_best_thumbnail({"default": {"url": "d"}}) == "d"

    def test_falls_back_to_any_entry(self):
        thumbnails = {"weirdkey": {"url": "w"}}
        assert _pick_best_thumbnail(thumbnails) == "w"

    def test_skips_entries_without_url(self):
        thumbnails = {"default": {}, "medium": {"url": "m"}}
        assert _pick_best_thumbnail(thumbnails) == "m"


class TestNormalizeUploadDate:
    def test_parses_iso_with_z(self):
        assert _normalize_upload_date("2024-03-15T10:30:00Z") == "20240315"

    def test_parses_iso_with_offset(self):
        assert _normalize_upload_date("2024-03-15T10:30:00+00:00") == "20240315"

    def test_none_returns_none(self):
        assert _normalize_upload_date(None) is None
        assert _normalize_upload_date("") is None

    def test_invalid_returns_none(self):
        assert _normalize_upload_date("not-a-date") is None


class TestParseOptionalInt:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("42", 42),
            (42, 42),
            ("0", 0),
            (None, None),
            ("", None),
            ("abc", None),
            ([1, 2], None),
        ],
    )
    def test_values(self, value, expected):
        assert _parse_optional_int(value) == expected


class TestGetYoutubeVideoId:
    @pytest.mark.parametrize(
        "url,expected",
        [
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://youtu.be/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/embed/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
            ("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "dQw4w9WgXcQ"),
        ],
    )
    def test_valid_urls(self, url, expected):
        assert get_youtube_video_id(url) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "",
            "   ",
            "https://example.com/watch?v=dQw4w9WgXcQ",
            "not a url",
            None,
        ],
    )
    def test_invalid_returns_none(self, url):
        assert get_youtube_video_id(url) is None

    def test_trims_whitespace(self):
        assert (
            get_youtube_video_id("  https://youtu.be/dQw4w9WgXcQ  ") == "dQw4w9WgXcQ"
        )


class TestValidateYoutubeUrl:
    def test_valid(self):
        assert validate_youtube_url("https://youtu.be/dQw4w9WgXcQ") is True

    def test_invalid(self):
        assert validate_youtube_url("https://example.com") is False
