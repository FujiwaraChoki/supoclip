from src.font_registry import FONTS_DIR, get_font_codepoints
from src.media import captions


def _build(tmp_path, monkeypatch, hook_title, words=None):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    built = captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4",
        0.0,
        5.0,
        1080,
        1920,
        output,
        caption_template="default",
        hook_title=hook_title,
        caption_words=words,
        include_captions=bool(words),
    )
    assert built
    return output.read_text(encoding="utf-8")


def test_get_font_codepoints_reads_the_cmap():
    bold = get_font_codepoints(FONTS_DIR / "THEBOLDFONT.ttf")
    fallback = get_font_codepoints(FONTS_DIR / f"{captions.GLYPH_FALLBACK_FONT}.ttf")

    assert ord("A") in bold and ord("a") in bold
    assert ord("ú") not in bold and ord("Ñ") not in bold
    assert {ord(ch) for ch in "áéíóúÁÉÍÓÚñÑüÜ"} <= fallback


def test_accented_hook_title_switches_to_a_font_that_has_the_glyphs(
    tmp_path, monkeypatch
):
    content = _build(tmp_path, monkeypatch, "Claude ahora actúa sin que le pidas")
    fallback = captions.ass_font_name(captions.GLYPH_FALLBACK_FONT)

    assert f"Style: Hook,{fallback}," in content
    assert f"Style: Default,{fallback}," in content
    # THEBOLDFONT only draws capitals, so the fallback keeps the all-caps look.
    assert "ACTÚA" in content
    assert "actúa" not in content


def test_accented_caption_words_also_trigger_the_fallback(tmp_path, monkeypatch):
    words = [
        {"text": "Publicá", "start": 0.2, "end": 0.6},
        {"text": "todos", "start": 0.6, "end": 1.0},
        {"text": "días", "start": 1.0, "end": 1.4},
    ]
    content = _build(tmp_path, monkeypatch, None, words)

    assert f"\\fn{captions.ass_font_name(captions.GLYPH_FALLBACK_FONT)}" in content
    assert "DÍAS" in content


def test_plain_ascii_text_keeps_the_template_font(tmp_path, monkeypatch):
    content = _build(tmp_path, monkeypatch, "Claude acts before you ask")
    template_font = captions.ass_font_name("THEBOLDFONT")

    assert f"Style: Hook,{template_font}," in content
    assert "}acts" in content and "ACTS" not in content
