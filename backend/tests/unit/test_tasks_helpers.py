from src.api.routes.tasks import (
    _normalize_font_color,
    _normalize_font_family,
    _normalize_font_size,
    _normalize_optional_font_color,
)


def test_normalize_font_size_bounds_values():
    assert _normalize_font_size("4") == 12
    assert _normalize_font_size("120") == 72


def test_normalize_font_color_accepts_hex_values():
    assert _normalize_font_color("#abcdef") == "#ABCDEF"
    assert _normalize_font_color("blue") == "#FFFFFF"


def test_normalize_font_family_uses_default_for_empty_values():
    assert _normalize_font_family("  ") == "TikTokSans-Regular"
    assert _normalize_font_family("Inter") == "Inter"


def test_normalize_optional_font_color_returns_none_when_absent_or_invalid():
    # None/absent and malformed values yield None so the renderer keeps the
    # caption template's baked colour (unlike _normalize_font_color → white).
    assert _normalize_optional_font_color(None) is None
    assert _normalize_optional_font_color("") is None
    assert _normalize_optional_font_color("red") is None
    assert _normalize_optional_font_color("#ABC") is None
    # Valid #RRGGBB is upper-cased and returned.
    assert _normalize_optional_font_color("#00ff00") == "#00FF00"
