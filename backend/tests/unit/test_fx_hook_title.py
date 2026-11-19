"""The hook title of a Dynamic edits clip: what Python resolves for fx-core's card (``edit_fx.hook_title``), the
scene field, the face analysis it asks for, and the libass fallback for levels without the drawing stage."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.caption_templates import get_template
from src.edit_fx.caption_style import look_caption_style
from src.edit_fx.contracts import EditStyle, FxHookTitle, FxPlan
from src.edit_fx.hook_title import (
    HOOK_FONT,
    build_hook,
    hook_accent,
    hook_font,
    hook_look,
    hook_text,
)
from src.edit_fx.warp import identity_warp
from src.media import fx_chain
from src.media.captions import build_hook_title_ass, hook_heavy_accent
from src.media.fx_chain import FxRequest, build_scene


def plan(**extra) -> FxPlan:
    return FxPlan(style=EditStyle(intensity="bold"), template="highlighter_swipe", seed=3, edit_duration=8.0, **extra)


def test_look_follows_the_caption_look_or_the_intensity():
    assert hook_look("highlighter_swipe", "max") == "max"
    assert hook_look("highlighter_swipe", "off") == "bold"            # style only
    assert hook_look("emoji_drop", "max", "serious") == "subtle"
    assert hook_look("default", "subtle") == "subtle"                 # a classic template with edits
    assert hook_look("slam", "off") == "bold"
    assert hook_look("default", "max", "serious") == "subtle"


def test_listed_styles_use_poppins_unless_the_user_picked_a_font():
    assert hook_font("highlighter_swipe", None) == HOOK_FONT
    for look_font in ("TikTokSans-Bold", "Poppins-ExtraBold", "THEBOLDFONT"):
        assert hook_font("emoji_drop", look_font) == HOOK_FONT       # a look's own caption font is not a choice
    assert hook_font("behind_head", "TikTokSans-Regular") == "TikTokSans-Bold"   # never a regular weight
    assert hook_font("minimal", "TikTokSans-Regular") == "TikTokSans-Bold"
    assert hook_font("default", "THEBOLDFONT") == "THEBOLDFONT"
    assert hook_font("slam", None) == HOOK_FONT


def test_text_is_one_line_and_upper_case_only_for_an_unlisted_all_caps_template():
    assert hook_text("  90%  of guests\tbecome\nfriends ", "highlighter_swipe", True) == "90% of guests become friends"
    assert hook_text("He tried it", "slam", True) == "HE TRIED IT"
    assert hook_text("He tried it", "default", False) == "He tried it"


def test_marker_colour_is_the_captions_keyword_colour():
    caption = look_caption_style("highlighter_swipe", "bold", width=1080, height=1920, caption_y=None)
    assert hook_accent("highlighter_swipe", "bold", caption) == "#34FD87"
    assert hook_accent("emoji_drop", "max", None) == "#FCFB2B"        # captions off: the look's colour
    assert hook_accent("default", "bold", None, {"highlight_color": "#ffd700"}) == "#FFD700"
    assert hook_accent("default", "bold", None, {}) == "#FFE45C"


def test_build_hook_needs_a_title_and_a_window():
    kwargs = dict(template_id="highlighter_swipe", look="bold", font=None, accent="#34FD87")
    assert build_hook(None, window=(0.12, 4.0), **kwargs) is None
    assert build_hook("   ", window=(0.12, 4.0), **kwargs) is None
    assert build_hook("Title", window=None, **kwargs) is None
    assert build_hook("Title", window=(2.0, 2.0), **kwargs) is None
    hook = build_hook("Title", window=(0.12, 4.0), x=3, y=-3, scale=9, **kwargs)
    assert (hook.x, hook.y, hook.scale, hook.template) == (1.0, -1.0, 4.0, "highlighter_swipe")
    # The position is fx-core's: the contract carries no placement hints.
    assert not {"trim", "picture", "avoid"} & set(type(hook).model_fields)


def test_the_scene_carries_the_hook_unless_the_plan_suppresses_it():
    hook = build_hook("Title", template_id="highlighter_swipe", look="bold", font=None, accent="#34FD87",
                      window=(0.12, 4.0))

    def scene(current: FxPlan):
        request = FxRequest(plan=current, words=[], caption=None, hook_window=(0.12, 4.0), hook=hook)
        return build_scene(request, out_w=1080, out_h=1920, warp=identity_warp(8.0), face_track=None, matte_windows=[])

    assert scene(plan()).hook == hook
    assert scene(plan()).model_dump(mode="json")["hook"]["text"] == "Title"
    assert scene(plan(hook_title_suppressed=True)).hook is None
    assert FxHookTitle.model_validate(scene(plan()).model_dump(mode="json")["hook"]) == hook


def test_a_drawn_hook_asks_for_the_face_track_and_its_font(monkeypatch, tmp_path):
    hook = build_hook("Title", template_id="default", look="bold", font="THEBOLDFONT", accent="#FFE45C",
                      window=(0.12, 4.0))
    asked = {}

    class Analysis:
        @staticmethod
        def analyse(*_args, face, **_kwargs):
            asked["face"] = face
            return {"version": 1, "space": "picture", "fps": 6, "samples": []}, {}

    monkeypatch.setattr(fx_chain, "_analysis_module", lambda: Analysis)
    request = FxRequest(plan=plan(), words=[], caption=None, hook=hook)
    fx_chain._analysis(request, input_path=Path("in.mp4"), head="", head_label="[v]", out_w=1080, out_h=1920,
                       work=tmp_path, timings={})
    assert asked == {"face": True}   # it moves beneath a head it would cover
    # A track handed in (a re-render, the studio) is used as it is; no title, no analysis.
    asked.clear()
    track = {"version": 1, "space": "picture", "fps": 6, "samples": []}
    fx_chain._analysis(FxRequest(plan=plan(), words=[], caption=None, hook=hook, face_track=track),
                       input_path=Path("in.mp4"), head="", head_label="[v]", out_w=1080, out_h=1920, work=tmp_path,
                       timings={})
    fx_chain._analysis(FxRequest(plan=plan(), words=[], caption=None), input_path=Path("in.mp4"), head="",
                       head_label="[v]", out_w=1080, out_h=1920, work=tmp_path, timings={})
    assert asked == {}
    assert "THEBOLDFONT" in fx_chain._font_paths(None, None, hook)


# ----------------------------------------------------------------------------- the libass fallback

def heavy(title="I deleted a mega star's episode", template="minimal", **kwargs):
    return build_hook_title_ass(title, get_template(template), 1080, 1920, 15.0, "TikTok Sans", 60, heavy=True, **kwargs)


def test_the_fallback_title_is_heavy_outlined_type_without_a_box():
    style, events = heavy()
    fields = style.split(",")
    assert fields[1] == "Poppins ExtraBold" and int(fields[2]) == 91       # 0.084 W: about 65 px of em
    assert fields[3] == "&H00FFFFFF"                                        # white type
    assert fields[15] == "1" and int(fields[16]) >= 5 and fields[17] == "0"  # outline, never BorderStyle 3
    # A blurred dark copy under the crisp title stands in for a soft shadow.
    assert len(events) == 2
    assert events[0].startswith("Dialogue: 0,") and "\\blur" in events[0] and "\\alpha&H73&" in events[0]
    assert events[1].startswith("Dialogue: 1,") and "\\blur" not in events[1]
    assert "I deleted a mega\\Nstar's episode" in events[0]
    for event in events:
        assert "0:00:00.12,0:00:04.00" in event


def test_the_fallback_title_keeps_its_case_and_accents_numbers_in_a_bright_colour():
    style, events = heavy("90% of guests never call back", template="tiktok")
    assert "90%" in events[1] and "NEVER" not in events[1]
    accent = events[1].split("90%")[0].rsplit("{\\c", 1)[1].rstrip("}")
    plain = events[1].split("of")[0].rsplit("{\\c", 1)[1].rstrip("}")
    assert accent != plain == "&H00FFFFFF"
    assert hook_heavy_accent({"highlight_color": "#FFFFFF"}) == "#FFE45C"
    assert hook_heavy_accent({"emphasis_color": "#101010"}) == "#FFE45C"
    assert hook_heavy_accent({"emphasis_color": "#34fd87"}) == "#34FD87"
    assert hook_heavy_accent({}) == "#FFE45C"


def test_the_classic_title_is_untouched_by_the_fallback_look():
    args = ("I deleted a mega star's episode", get_template("default"), 1080, 1920, 15.0, "TikTok Sans", 60)
    assert build_hook_title_ass(*args) == build_hook_title_ass(*args, heavy=False)
    assert build_hook_title_ass(*args) != build_hook_title_ass(*args, heavy=True)
    assert len(build_hook_title_ass(*args)[1]) == 1
