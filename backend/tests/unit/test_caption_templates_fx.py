"""Animated (dynamic edits) caption templates: a separate catalogue that never leaks into legacy iteration (§5.3, §5.4)."""

import re

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from src import caption_templates
from src.api.routes import media
from src.caption_templates import (
    CAPTION_TEMPLATES,
    FX_CAPTION_TEMPLATES_DICT,
    TEMPLATE_DEFAULTS,
    get_all_templates,
    get_template,
    get_template_info,
    get_template_names,
)
from src.edit_fx.contracts import FX_CAPTION_TEMPLATES, LEGACY_CAPTION_TEMPLATES

# §5.3 table: ass_fallback, needs_face, needs_matte, font_family, animation
SPEC = {
    "marker_circle": ("default", False, False, "TikTokSans-Bold", "pop"),
    "emoji_drop": ("tiktok", False, False, "Poppins-ExtraBold", "none"),
    "slam": ("mrbeast", False, False, "THEBOLDFONT", "pop"),
    "behind_head": ("minimal", True, True, "TikTokSans-Bold", "fade"),
    "stat_counter": ("hormozi", False, False, "Poppins-ExtraBold", "pop"),
    "kinetic_stack": ("mrbeast", False, False, "THEBOLDFONT", "bounce"),
    "decode_glitch": ("neon", False, False, "TikTokSans-Bold", "fade"),
    "highlighter_swipe": ("minimal", False, False, "Poppins-ExtraBold", "none"),
    "face_doodles": ("default", True, False, "TikTokSans-Bold", "karaoke"),
}
FX_ONLY_KEYS = {"fx", "ass_fallback", "needs_face", "needs_matte"}
HEX6 = re.compile(r"^#[0-9A-Fa-f]{6}$")
HEX6_OR_8 = re.compile(r"^#[0-9A-Fa-f]{6}([0-9A-Fa-f]{2})?$")


def test_the_fx_catalogue_matches_the_contract_ids_in_order():
    assert tuple(FX_CAPTION_TEMPLATES_DICT) == FX_CAPTION_TEMPLATES
    assert set(SPEC) == set(FX_CAPTION_TEMPLATES)


def test_legacy_catalogue_and_its_iterators_stay_the_seven_legacy_templates():
    assert tuple(CAPTION_TEMPLATES) == LEGACY_CAPTION_TEMPLATES
    assert get_all_templates() is CAPTION_TEMPLATES
    assert get_template_names() == list(LEGACY_CAPTION_TEMPLATES)
    assert [entry["id"] for entry in get_template_info()] == list(LEGACY_CAPTION_TEMPLATES)
    assert not set(FX_CAPTION_TEMPLATES) & set(CAPTION_TEMPLATES)
    for template in CAPTION_TEMPLATES.values():
        assert not FX_ONLY_KEYS & set(template), "FX-only keys must never appear on a legacy template"


@pytest.mark.parametrize("template_id", FX_CAPTION_TEMPLATES)
def test_each_fx_template_follows_the_design_table(template_id):
    fallback, needs_face, needs_matte, font, animation = SPEC[template_id]
    template = FX_CAPTION_TEMPLATES_DICT[template_id]
    assert template["fx"] is True
    assert template["ass_fallback"] == fallback and fallback in CAPTION_TEMPLATES
    assert template["needs_face"] is needs_face
    assert template["needs_matte"] is needs_matte
    assert template["font_family"] == font
    assert template["animation"] == animation
    assert template["emoji"] is False   # libass never injects emoji for these, even in the safe level


@pytest.mark.parametrize("template_id", FX_CAPTION_TEMPLATES)
def test_each_fx_template_carries_every_legacy_key_with_valid_values(template_id):
    template = FX_CAPTION_TEMPLATES_DICT[template_id]
    legacy_keys = set(CAPTION_TEMPLATES["default"])
    # Unlisted templates carry "hidden": True (never offered, still rendered for stored tasks).
    assert set(template) - {"hidden"} == legacy_keys | FX_ONLY_KEYS
    assert template.get("hidden", False) is (template_id not in ("highlighter_swipe", "emoji_drop", "behind_head"))
    assert template["name"] and template["description"]
    assert HEX6.match(template["font_color"]) and HEX6.match(template["highlight_color"])
    assert template["emphasis_color"] is None or HEX6.match(template["emphasis_color"])
    if template["stroke_color"] is None:
        assert template["stroke_width"] == 0
    else:
        assert HEX6.match(template["stroke_color"]) and template["stroke_width"] > 0
    if template["background"]:
        assert HEX6_OR_8.match(template["background_color"])
    assert 1 <= template["max_words_per_line"] <= 8
    assert 0.5 <= template["position_y"] <= 0.95
    assert 20 <= template["font_size"] <= 40
    assert template["animation"] in ("none", "karaoke", "pop", "fade", "bounce")


def test_fx_fonts_are_bundled():
    from pathlib import Path

    fonts = Path(caption_templates.__file__).resolve().parents[1] / "fonts"
    stems = {path.stem for path in fonts.glob("*.ttf")} | {path.stem for path in fonts.glob("*.otf")}
    for template in FX_CAPTION_TEMPLATES_DICT.values():
        assert template["font_family"] in stems


@pytest.mark.parametrize("template_id", LEGACY_CAPTION_TEMPLATES)
def test_get_template_for_legacy_ids_is_unchanged(template_id):
    expected = dict(TEMPLATE_DEFAULTS)
    expected.update(CAPTION_TEMPLATES[template_id])
    assert get_template(template_id) == expected


@pytest.mark.parametrize("template_id", FX_CAPTION_TEMPLATES)
def test_get_template_finds_fx_ids(template_id):
    template = get_template(template_id)
    assert template["fx"] is True
    assert template["name"] == FX_CAPTION_TEMPLATES_DICT[template_id]["name"]


def test_unknown_ids_still_fall_back_to_default():
    assert get_template("nope") == get_template("default")
    assert "fx" not in get_template("nope")


def test_template_info_appends_fx_entries_only_when_asked():
    legacy = get_template_info()
    with_fx = get_template_info(include_fx=True)
    assert with_fx[: len(legacy)] == legacy
    extra = with_fx[len(legacy):]
    # Only the listed styles are offered (catalogue order); unlisted ones stay valid ids but are never listed.
    assert [entry["id"] for entry in extra] == [t for t in FX_CAPTION_TEMPLATES
                                                if t in ("highlighter_swipe", "emoji_drop", "behind_head")]
    by_id = {entry["id"]: entry for entry in extra}
    assert by_id["behind_head"]["requires"] == ["matte"]
    assert by_id["emoji_drop"]["requires"] == []
    for entry in extra:
        assert entry["fx"] is True
        assert set(entry) == set(legacy[0]) | {"fx", "requires"}


async def _get(path: str):
    app = FastAPI()
    app.include_router(media.router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.get(path)


async def test_caption_templates_route_lists_fx_only_with_include_fx():
    default = await _get("/caption-templates")
    assert [entry["id"] for entry in default.json()["templates"]] == list(LEGACY_CAPTION_TEMPLATES)
    assert "fx" not in default.text

    for query in ("include=fx", "include=FX", "include=foo,fx"):
        response = await _get(f"/caption-templates?{query}")
        assert response.status_code == 200
        ids = [entry["id"] for entry in response.json()["templates"]]
        assert ids == list(LEGACY_CAPTION_TEMPLATES) + [t for t in FX_CAPTION_TEMPLATES
                                                        if t in ("highlighter_swipe", "emoji_drop", "behind_head")]

    other = await _get("/caption-templates?include=other")
    assert other.json() == default.json()
