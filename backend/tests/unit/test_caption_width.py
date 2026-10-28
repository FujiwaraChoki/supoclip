"""Spoken captions must fit the frame by rendered width (#292)."""

import re
import shutil
import subprocess

import cv2
import numpy as np
import pytest

from src.font_registry import FONTS_DIR, get_font_advance_metrics, get_font_family_name
from src.media import captions


def _render_width(font, size, text, tmp_path):
    ass = tmp_path / "w.ass"
    png = tmp_path / "w.png"
    ass.write_text(
        "[Script Info]\nScriptType: v4.00+\nPlayResX: 2400\nPlayResY: 240\nWrapStyle: 2\n\n"
        "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, "
        "Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: D,{get_font_family_name(font)},{size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,"
        "1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1\n\n[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        f"Dialogue: 0,0:00:00.00,0:00:01.00,D,,0,0,0,,{{\\pos(1200,120)}}{text}\n"
    )
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=2400x240:d=1",
         "-vf", f"subtitles=filename={ass}:fontsdir={FONTS_DIR}", "-frames:v", "1", str(png)],
        check=True,
    )
    columns = np.where(cv2.imread(str(png), cv2.IMREAD_GRAYSCALE).max(axis=0) > 60)[0]
    return columns[-1] - columns[0] + 1


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg is required")
@pytest.mark.parametrize("font_name", ["THEBOLDFONT", "TikTokSans-Bold", "Montserrat-Variable-wght"])
def test_measured_width_matches_what_libass_draws(font_name, tmp_path):
    font = FONTS_DIR / f"{font_name}.ttf"
    text = "5,000 milliamp-hour rechargeable battery"
    predicted = get_font_advance_metrics(font).text_width(text, 62)
    assert _render_width(font, 62, text, tmp_path) == pytest.approx(predicted, rel=0.03)


def test_long_chunk_splits_into_lines_that_fit():
    measure = captions.caption_text_measurer("THEBOLDFONT", 62)
    words = "5,000 milliamp-hour rechargeable battery".split()
    widths = [measure(word) for word in words]

    spans = captions.split_caption_chunk(widths, measure(" "), 900)

    assert spans == [(0, 2), (2, 4)]
    for start, end in spans:
        assert sum(widths[start:end]) + measure(" ") * (end - start - 1) <= 900
    # A chunk that already fits is left alone.
    assert captions.split_caption_chunk(widths[:2], measure(" "), 900) == [(0, 2)]


def _dialogue_lines(content):
    return [line for line in content.splitlines() if line.startswith("Dialogue: 0,")]


def _build(tmp_path, monkeypatch, words, **kwargs):
    monkeypatch.setattr(captions, "emoji_rendering_supported", lambda: False)
    output = tmp_path / "captions.ass"
    assert captions.build_assemblyai_ass_subtitles(
        tmp_path / "missing.mp4", 0.0, 10.0, 1080, 1920, output,
        caption_template="default", caption_words=words, **kwargs,
    )
    return output.read_text(encoding="utf-8")


def _timed(texts):
    return [{"text": text, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, text in enumerate(texts)]


def test_mkbhd_battery_caption_fits_inside_the_safe_margins(tmp_path, monkeypatch):
    words = _timed("which is a 5,000 milliamp-hour rechargeable battery".split())
    content = _build(tmp_path, monkeypatch, words)

    measure = captions.caption_text_measurer("THEBOLDFONT", captions.get_scaled_font_size(32, 1080))
    limit = captions.get_subtitle_max_width(1080)
    shown = {re.sub(r"\{[^}]*\}", "", line.split(",", 9)[9]) for line in _dialogue_lines(content)}
    assert "5,000 milliamp-hour rechargeable battery" not in shown
    assert all(measure(text) < limit for text in shown)
    assert {word["text"] for word in words} == {w for text in shown for w in text.split()}


def test_single_overwide_token_is_scaled_down(tmp_path, monkeypatch):
    content = _build(tmp_path, monkeypatch, _timed(["Pneumonoultramicroscopicsilicovolcanoconiosis", "ok"]))
    lines = _dialogue_lines(content)

    long_line = next(line for line in lines if "Pneumono" in line)
    scale = int(re.search(r"\\pos\([^)]*\)\\fscx(\d+)", long_line).group(1))
    assert scale < 100
    measure = captions.caption_text_measurer("THEBOLDFONT", captions.get_scaled_font_size(32, 1080))
    assert measure("Pneumonoultramicroscopicsilicovolcanoconiosis") * scale / 100 <= 1080 * 0.88
    # The entrance animation ends at the reduced size, not back at 100%.
    assert f"\\t(0,140,\\fscx{scale}\\fscy{scale})" in long_line
    # Short lines keep their exact original markup.
    short_line = next(line for line in lines if line.endswith("ok"))
    assert "\\pos(540,1490)}" in short_line
