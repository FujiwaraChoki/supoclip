"""edit_fx.caption_style: FX caption looks sized like the legacy renderer."""

import random

import pytest

from src.caption_templates import get_template
from src.edit_fx.caption_style import fx_caption_style, fx_caption_style_from_document, scaled_font_px
from src.edit_fx.contracts import CaptionPlacement
from src.editor_document import CaptionStyle
from src.media.captions import get_scaled_font_size


def test_scaled_font_px_equals_the_legacy_helper_for_50_inputs():
    rng = random.Random(560)
    cases = [(12, 1080), (72, 1080), (32, 720), (28, 560), (24, 1920), (14, 280), (70, 1080), (30, 1092)]
    cases += [(rng.randint(8, 96), rng.choice([360, 540, 607, 720, 1080, 1081, 1350, 1440, 1920, 2160]))
              for _ in range(42)]
    assert len(cases) == 50
    for base, width in cases:
        assert scaled_font_px(base, width) == get_scaled_font_size(base, width), (base, width)


def test_generation_style_from_a_legacy_template():
    style = fx_caption_style(get_template("hormozi"), template_id="hormozi", font_family=None, font_size=None,
                             font_color=None, width=1080, height=1920, caption_y=None)
    template = get_template("hormozi")
    assert style.font == template["font_family"]
    assert style.size_px == get_scaled_font_size(template["font_size"], 1080)
    assert style.color == template["font_color"].upper() and style.accent == template["highlight_color"].upper()
    assert style.stroke_color == "#000000"
    assert style.stroke_px == max(template["stroke_width"], round(style.size_px * template["stroke_width"] / 26))
    assert style.y == template["position_y"] and style.words_per_line == template["max_words_per_line"]
    assert style.enabled and style.placements == ()


def test_generation_overrides():
    style = fx_caption_style(get_template("default"), template_id="slam", font_family="Poppins-ExtraBold",
                             font_size=40, font_color="#ff00aa", width=720, height=1280, caption_y=0.55,
                             enabled=False)
    assert style.template == "slam" and style.font == "Poppins-ExtraBold"
    assert style.size_px == get_scaled_font_size(40, 720) and style.color == "#FF00AA"
    assert style.y == 0.55 and not style.enabled


def test_background_box_and_no_outline():
    template = {**get_template("default"), "background": True, "background_color": "#00000080",
                "stroke_color": None, "shadow": False}
    style = fx_caption_style(template, template_id="x", font_family=None, font_size=None, font_color=None,
                             width=1080, height=1920, caption_y=None)
    assert style.background == "#00000080" and style.stroke_color is None and style.stroke_px == 0
    opaque = fx_caption_style({**template, "background_color": "#112233"}, template_id="x", font_family=None,
                              font_size=None, font_color=None, width=1080, height=1920, caption_y=None)
    assert opaque.background == "#112233FF"


def test_bad_template_values_fall_back():
    style = fx_caption_style({"font_size": 30, "font_color": "white", "highlight_color": None,
                              "max_words_per_line": 99}, template_id="x", font_family=None, font_size=None,
                             font_color=None, width=1080, height=1920, caption_y=1.4)
    assert style.color == "#FFFFFF" and style.accent == "#FFE000" and style.words_per_line == 8
    assert style.y == 1.0 and style.font == "TikTokSans-Bold"


def test_studio_style_follows_the_document():
    captions = CaptionStyle(font="THEBOLDFONT", size=60, color="#FAFAFA", accent="#00FF66", y=0.7,
                            wordsPerLine=2, uppercase=True, background=False, template="slam").model_dump()
    placement = CaptionPlacement(key='["s0","w3"]', x=0.1, y=-0.05, scale=1.2)
    style = fx_caption_style_from_document(captions, get_template("mrbeast"), width=1080, height=1920,
                                           placements=[placement])
    assert style.template == "slam" and style.font == "THEBOLDFONT"
    assert style.size_px == 60                                # 1080-wide units at 1080 wide
    assert style.color == "#FAFAFA" and style.accent == "#00FF66" and style.y == 0.7
    assert style.words_per_line == 2 and style.uppercase and style.background is None
    assert style.placements == (placement,)
    half = fx_caption_style_from_document(captions, get_template("mrbeast"), width=540, height=960)
    assert half.size_px == 30


def test_studio_default_document_has_a_box():
    style = fx_caption_style_from_document(CaptionStyle().model_dump(), get_template("default"), width=1080,
                                           height=1920)
    assert style.background == "#00000080" and style.words_per_line == 4


@pytest.mark.parametrize("width", [540, 1080, 2160])
def test_stroke_is_capped(width):
    style = fx_caption_style({**get_template("default"), "stroke_width": 30}, template_id="x", font_family=None,
                             font_size=72, font_color=None, width=width, height=1920, caption_y=None)
    assert style.stroke_px <= 40
