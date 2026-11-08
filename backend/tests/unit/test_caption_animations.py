import pytest

from src.caption_templates import get_template
from src.media import captions

WORDS = [
    {"text": "this", "start": 0.0, "end": 0.4},
    {"text": "is", "start": 0.4, "end": 0.7},
    {"text": "fine", "start": 0.7, "end": 1.2},
]


def _ass(tmp_path, monkeypatch, template):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    assert captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 5.0, 1080, 1920, output,
        caption_template=template, caption_words=WORDS, include_captions=True,
    )
    return [
        line for line in output.read_text(encoding="utf-8").splitlines()
        if line.startswith("Dialogue:")
    ]


@pytest.mark.parametrize("name", ["tags", "neon_flicker", "retro_glitch", "typewriter"])
def test_new_templates_resolve_with_their_own_animation(name):
    template = get_template(name)
    assert template["name"] != get_template("default")["name"]
    assert template["animation"] in {"karaoke", "flicker", "glitch", "typewriter"}


def test_typewriter_writes_one_event_per_word_and_hides_unspoken_words(tmp_path, monkeypatch):
    events = _ass(tmp_path, monkeypatch, "typewriter")
    assert len(events) == 3
    # First event: only the first word is visible, the rest keep their place.
    assert events[0].count("\\alpha&H00&") == 1
    assert events[0].count("\\alpha&HFF&") == 2
    assert events[2].count("\\alpha&H00&") == 3
    assert events[2].count("\\alpha&HFF&") == 0


def test_flicker_is_one_event_with_opacity_drops(tmp_path, monkeypatch):
    events = _ass(tmp_path, monkeypatch, "neon_flicker")
    assert len(events) == 1
    assert "\\alpha&HFF&\\t(0,40" in events[0]


def test_glitch_is_one_event_with_spacing_jitter(tmp_path, monkeypatch):
    events = _ass(tmp_path, monkeypatch, "retro_glitch")
    assert len(events) == 1
    assert "\\fsp10" in events[0]


def test_default_template_output_is_unchanged_by_the_new_animations(tmp_path, monkeypatch):
    events = _ass(tmp_path, monkeypatch, "default")
    assert not any("\\alpha&H" in event for event in events)
