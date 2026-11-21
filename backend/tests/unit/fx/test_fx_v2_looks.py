"""fx-v2 (reference study): listed templates and their intensity caption looks, beat validation guards, the FX hook
title box, the editor seed and the mix loudness stage."""

from __future__ import annotations

import pytest

from src.caption_templates import FX_CAPTION_TEMPLATES_DICT, get_template, get_template_info
from src.edit_fx.beats import validate_llm_plan
from src.edit_fx.caption_style import LOOKS, fx_caption_style_from_document, look_caption_style
from src.edit_fx.contracts import (
    FX_CAPTION_TEMPLATES,
    LISTED_FX_CAPTION_TEMPLATES,
    BeatTagLLM,
    EditPlanLLM,
    caption_look_for,
    is_fx_template,
)
from src.media import fx_audio

HIDDEN = ("marker_circle", "slam", "stat_counter", "kinetic_stack", "decode_glitch", "face_doodles")


def test_only_three_animated_styles_are_listed_but_all_stay_valid():
    assert set(LISTED_FX_CAPTION_TEMPLATES) == {"highlighter_swipe", "emoji_drop", "behind_head"}
    listed = [t["id"] for t in get_template_info(include_fx=True) if t.get("fx")]
    assert sorted(listed) == sorted(LISTED_FX_CAPTION_TEMPLATES)
    for template_id in HIDDEN:
        assert is_fx_template(template_id) and template_id in FX_CAPTION_TEMPLATES
        assert FX_CAPTION_TEMPLATES_DICT[template_id]["hidden"] is True
        assert get_template(template_id)["fx"] is True               # stored tasks still render with their own look
    assert not [t for t in get_template_info() if t.get("fx")]        # the legacy list never changes


@pytest.mark.parametrize("template,intensity,sensitivity,expected", [
    ("highlighter_swipe", "bold", "normal", "bold"), ("emoji_drop", "max", "normal", "max"),
    ("behind_head", "subtle", "normal", "subtle"), ("highlighter_swipe", "off", "normal", "bold"),
    ("emoji_drop", "max", "serious", "subtle"), ("slam", "max", "normal", "template"),
    ("default", "bold", "normal", "template"),
])
def test_caption_look_for(template, intensity, sensitivity, expected):
    assert caption_look_for(template, intensity, sensitivity) == expected


@pytest.mark.parametrize("look,px,font,upper,stroke", [
    ("subtle", 52, "TikTokSans-Bold", False, 3.64), ("bold", 72, "Poppins-ExtraBold", False, 0.0),
    ("max", 84, "THEBOLDFONT", True, 7.56),
])
def test_look_caption_style_sizes_fonts_and_outline(look, px, font, upper, stroke):
    style = look_caption_style("highlighter_swipe", look, width=1080, height=1920, caption_y=None)
    assert (style.size_px, style.font, style.uppercase, style.stroke_px, style.look) == (px, font, upper, stroke, look)
    assert style.y == LOOKS[look]["y"]
    assert style.background == ("#170904D9" if look == "bold" else None)
    assert style.accent == {"subtle": "#FFE45C", "bold": "#34FD87", "max": "#FFE45C"}[look]
    # 720p: sizes scale with the width; a stacked layout's seam wins over the look's row.
    small = look_caption_style("emoji_drop", look, width=720, height=1280, caption_y=0.5)
    assert small.size_px == round(px * 720 / 1080) and small.y == 0.5
    assert small.accent == {"subtle": "#F2C14E", "bold": "#FFE00B", "max": "#FCFB2B"}[look]
    # Bold is the dark pill for every listed style (white words without an outline vanish over a white shirt).
    assert small.background == ("#170904D9" if look == "bold" else None)


def test_studio_document_style_follows_the_look():
    captions = {"template": "highlighter_swipe", "font": "Poppins-ExtraBold", "size": 72, "y": 0.6, "wordsPerLine": 3,
                "background": True, "accent": "#34FD87", "uppercase": False}
    style = fx_caption_style_from_document(captions, get_template("highlighter_swipe"), width=1080, height=1920,
                                           look="max")
    assert style.look == "max" and style.uppercase and style.stroke_color == "#000000" and style.background is None
    template = fx_caption_style_from_document(captions, get_template("highlighter_swipe"), width=1080, height=1920)
    assert template.look == "template"


def _plan(beats, sensitivity="normal"):
    return EditPlanLLM(mood="hype", sensitivity=sensitivity, hook_last_word=-1, beats=beats, reactions=[])


def _tag(kind, first, last, tone="neutral", emoji=""):
    return BeatTagLLM(kind=kind, first_word=first, last_word=last, strength=2, tone=tone, emoji=emoji, label="")


WORDS = [{"start": 0.2 + 0.4 * i, "end": 0.5 + 0.4 * i, "text": t} for i, t in enumerate(
    "this shit business made money on a car and I was crying".split())]


def test_llm_swears_must_be_confirmed_by_the_list():
    out = validate_llm_plan(_plan([_tag("swear", 1, 1), _tag("swear", 4, 4)]), WORDS, model="m",
                            emoji_keys=frozenset())
    swears = [b for b in out.beats if b.kind == "swear"]
    assert [b.text for b in swears] == ["shit"]                       # "money" (an ordinary word) is never bleeped


def test_sincere_clips_are_never_played_for_laughs_and_emoji_are_literal():
    out = validate_llm_plan(_plan([_tag("punchline", 9, 11, tone="funny"), _tag("emphasis", 4, 4, emoji="💰"),
                                   _tag("emphasis", 7, 7, emoji="👉")]), WORDS, model="m",
                            emoji_keys=frozenset({"💰", "👉", "🚗"}))
    assert not [b for b in out.beats if b.kind == "punchline" or b.tone in ("funny", "smug", "awkward")]
    emoji = {b.text: b.emoji for b in out.beats if b.kind == "emphasis"}
    assert emoji["money"] == "💰" and emoji["car"] != "👉"


def test_fx_hook_title_box_uses_the_template_box_colour():
    from src.media.captions import build_hook_title_ass

    style, _events = build_hook_title_ass("Big title", get_template("highlighter_swipe"), 1080, 1920, 10.0,
                                          "Poppins", 72)
    fields = style.split(",")
    assert fields[15] == "3"                                          # BorderStyle 3: an opaque box
    assert fields[5] == fields[6]                                     # filled with the box colour, not black
    legacy_style, _ = build_hook_title_ass("Big title", get_template("minimal"), 1080, 1920, 10.0, "Inter", 52)
    assert legacy_style.split(",")[5] == "&H00000000&"                # legacy look unchanged (black box)


def test_mix_gain_brings_the_mix_to_the_target_within_limits():
    assert fx_audio.mix_gain_db(-12.0) == pytest.approx(-2.0)
    assert fx_audio.mix_gain_db(None) == 0.0
    assert fx_audio.mix_gain_db(-40.0) == fx_audio.MIX_GAIN_RANGE[1]
    assert fx_audio.mix_gain_db(5.0) == fx_audio.MIX_GAIN_RANGE[0]


def test_audio_graph_applies_the_mix_gain_before_the_limiter():
    from src.edit_fx.warp import build_warp

    graph = fx_audio.audio_graph(voice_fragment="[1:a]anull[fxa_voice0]", bleeps_e=[], warp=build_warp([], 5.0),
                                 out_duration=5.0, has_bus=True, voice_gain=1.0, loudness="static",
                                 static_gain_db=2.0, loop_crossfade=0.0, mix_gain_db=-1.5)
    assert "dropout_transition=0,volume=-1.5dB,alimiter=limit=0.82" in graph
