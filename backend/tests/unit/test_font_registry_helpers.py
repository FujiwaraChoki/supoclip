"""Tests for pure helpers in ``src/font_registry.py``."""

import pytest

from src.font_registry import (
    _display_name,
    _lookup_meta,
    build_user_font_stem,
    sanitize_font_stem,
    sanitize_user_id_for_path,
)


class TestDisplayName:
    @pytest.mark.parametrize(
        "stem,expected",
        [
            ("tiktok-sans-regular", "Tiktok Sans Regular"),
            ("THEBOLDFONT", "Theboldfont"),
            ("noto_sans_kr", "Noto Sans Kr"),
            ("  padded  ", "Padded"),
        ],
    )
    def test_humanizes(self, stem, expected):
        assert _display_name(stem) == expected


class TestSanitizeUserIdForPath:
    @pytest.mark.parametrize(
        "user_id,expected",
        [
            ("abc123", "abc123"),
            ("user-with-dashes", "user-with-dashes"),
            ("user_with_underscore", "user_with_underscore"),
            ("user@example.com", "user-example-com"),
            ("/path/traversal/../etc", "path-traversal----etc"),
            ("", "user"),
            ("---", "user"),
        ],
    )
    def test_sanitizes(self, user_id, expected):
        assert sanitize_user_id_for_path(user_id) == expected


class TestSanitizeFontStem:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("Inter-Regular.ttf", "Inter-Regular"),
            ("My Font.otf", "My-Font"),
            ("evil$name!.ttf", "evil-name"),
            ("Only_Allowed_Chars-123", "Only_Allowed_Chars-123"),
        ],
    )
    def test_sanitizes(self, value, expected):
        assert sanitize_font_stem(value) == expected

    @pytest.mark.parametrize("value", ["---.ttf", "!@#.ttf", "   .ttf"])
    def test_invalid_raises(self, value):
        with pytest.raises(ValueError):
            sanitize_font_stem(value)


class TestBuildUserFontStem:
    def test_prefixes_and_lowercases(self):
        stem = build_user_font_stem("user-123", "MyFont-Regular")
        assert stem == "usr-user-123-myfont-regular"

    def test_sanitizes_user_id(self):
        stem = build_user_font_stem("user@example.com", "Inter")
        assert stem == "usr-user-example-com-inter"

    def test_rejects_bad_stem(self):
        with pytest.raises(ValueError):
            build_user_font_stem("user", "---")


class TestLookupMeta:
    def test_korean_hint_detection(self):
        meta = _lookup_meta("MyFont-KR")
        assert meta["language"] == "korean"

    def test_hansans_is_korean(self):
        assert _lookup_meta("BlackHanSans-Regular")["language"] == "korean"

    def test_latin_default(self):
        assert _lookup_meta("Inter-Regular")["language"] == "latin"

    def test_display_name_present(self):
        meta = _lookup_meta("some-random-font")
        assert "display_name" in meta
        assert meta["display_name"] == "Some Random Font"
