"""edit_fx.constants: verbatim tables and the shared frame geometry (§11.3)."""

import typing

import pytest

from src.edit_fx import constants
from src.edit_fx.contracts import Tone


def test_tone_emoji_covers_every_tone():
    assert set(constants.TONE_EMOJI) == set(typing.get_args(Tone))
    assert constants.TONE_EMOJI["hype"] == "🔥" and constants.TONE_EMOJI["neutral"] == "✨"


def test_llm_allowed_emoji_is_the_verbatim_list():
    assert len(constants.LLM_ALLOWED_EMOJI) == 42
    assert len(set(constants.LLM_ALLOWED_EMOJI)) == 42
    assert constants.LLM_ALLOWED_EMOJI[0] == "💰" and constants.LLM_ALLOWED_EMOJI[-1] == "⚠️"
    assert "❤️" in constants.LLM_ALLOWED_EMOJI      # with its variation selector


def test_hook_band():
    assert constants.HOOK_BAND == 0.20


@pytest.mark.parametrize("w,h,u", [(1080, 1920, 1.0), (1080, 1080, 1.0), (1920, 1080, 1.0), (720, 1280, 720 / 1080)])
def test_unit(w, h, u):
    assert constants.unit(w, h) == pytest.approx(u)


def test_reaction_rect_vertical_matches_the_original_layout():
    x, y, w, h = constants.reaction_rect(1080, 1920)
    assert (x, y, w, h) == pytest.approx((0.15 * 1080, 0.20 * 1920, 0.70 * 1080, 0.525 * 1080))


@pytest.mark.parametrize("size", [(1080, 1920), (1080, 1080), (1920, 1080), (1280, 720), (3840, 2160)])
def test_reaction_rect_is_4_3_inside_its_box_and_frame(size):
    w, h = size
    x, y, rw, rh = constants.reaction_rect(w, h)
    assert rw / rh == pytest.approx(4 / 3)
    assert rw <= 0.70 * w + 1e-9 and rh <= 0.50 * h + 1e-9
    assert x == pytest.approx((w - rw) / 2)
    assert y == pytest.approx(0.20 * h)
    assert y + rh <= h and x >= 0 and x + rw <= w
    # largest: one side of the box is touched
    assert rw == pytest.approx(0.70 * w) or rh == pytest.approx(0.50 * h)


def test_landscape_reaction_no_longer_overflows():
    x, y, rw, rh = constants.reaction_rect(1920, 1080)
    assert rh == pytest.approx(540) and rw == pytest.approx(720)


def test_caption_safe_matches_the_ass_safe_area():
    assert constants.caption_safe(1080, 1920) == {"side": 0.06, "top": pytest.approx(96.0), "bottom": pytest.approx(192.0)}
    assert constants.caption_safe(1080, 1080) == {"side": 0.06, "top": pytest.approx(54.0), "bottom": pytest.approx(120.0)}
    small = constants.caption_safe(540, 960)
    assert small["top"] == pytest.approx(48.0) and small["bottom"] == pytest.approx(96.0)
