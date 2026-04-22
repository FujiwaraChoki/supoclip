"""Tests for caption template lookups."""

import pytest

from src.caption_templates import (
    CAPTION_TEMPLATES,
    get_all_templates,
    get_template,
    get_template_info,
    get_template_names,
)


REQUIRED_KEYS = {
    "name",
    "description",
    "font_family",
    "font_size",
    "font_color",
    "highlight_color",
    "stroke_color",
    "stroke_width",
    "background",
    "background_color",
    "animation",
    "shadow",
    "position_y",
}


class TestGetTemplate:
    @pytest.mark.parametrize("name", list(CAPTION_TEMPLATES.keys()))
    def test_returns_template_by_name(self, name):
        template = get_template(name)
        assert template is CAPTION_TEMPLATES[name]

    def test_unknown_returns_default(self):
        assert get_template("does-not-exist") is CAPTION_TEMPLATES["default"]

    def test_all_templates_have_required_keys(self):
        for name, template in CAPTION_TEMPLATES.items():
            missing = REQUIRED_KEYS - set(template.keys())
            assert not missing, f"{name} missing keys: {missing}"

    def test_animation_is_valid(self):
        valid = {"none", "karaoke", "pop", "fade", "bounce"}
        for name, template in CAPTION_TEMPLATES.items():
            assert template["animation"] in valid, f"{name} has bad animation"

    def test_position_y_within_bounds(self):
        for name, template in CAPTION_TEMPLATES.items():
            assert 0.0 <= template["position_y"] <= 1.0, f"{name} position_y out of range"


class TestBulkAccessors:
    def test_get_all_templates_returns_mapping(self):
        assert get_all_templates() is CAPTION_TEMPLATES

    def test_get_template_names(self):
        names = get_template_names()
        assert set(names) == set(CAPTION_TEMPLATES.keys())

    def test_get_template_info_shape(self):
        info = get_template_info()
        assert len(info) == len(CAPTION_TEMPLATES)
        info_keys = {"id", "name", "description", "animation", "font_family",
                     "font_size", "font_color", "highlight_color"}
        for entry in info:
            assert set(entry.keys()) == info_keys
            assert entry["id"] in CAPTION_TEMPLATES
