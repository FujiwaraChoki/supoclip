from src.api.routes.tasks import (
    _normalize_font_color,
    _normalize_font_family,
    _normalize_font_size,
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
