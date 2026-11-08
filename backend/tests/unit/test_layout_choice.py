from src.media.layout_choice import (
    bind_layout,
    current_layout,
    forced_layout,
    normalize_layout,
)
from src.media.reaction_layout import build_reaction_layout


def test_normalize_layout_rejects_unknown_values():
    assert normalize_layout("reaction_bottom") == "reaction_bottom"
    assert normalize_layout("bogus") == "auto"
    assert normalize_layout(None) == "auto"


def test_bind_layout_always_overwrites():
    bind_layout("stack")
    assert current_layout() == "stack"
    bind_layout(None)
    assert current_layout() == "auto"


def test_forced_layout_restores_previous_value():
    bind_layout("auto")
    with forced_layout("speaker"):
        assert current_layout() == "speaker"
    assert current_layout() == "auto"


def _reaction(position):
    face = (1600.0, 40.0, 120.0, 120.0)
    inset = (1500.0, 0.0, 420.0, 300.0)
    return build_reaction_layout(
        1920, 1080, 10.0, face, inset, [(0.0, [face])], (), position
    )


def test_reaction_top_keeps_reactor_first_and_default_caption():
    plan = _reaction("top")
    assert plan["video_filter"].endswith("[reactor][base1]vstack=inputs=2[vout]")
    assert plan["caption_y"] == 640 / 1920


def test_reaction_bottom_puts_reactor_last():
    plan = _reaction("bottom")
    assert plan["video_filter"].endswith("[base1][reactor]vstack=inputs=2[vout]")
    assert plan["caption_y"] == 1280 / 1920
