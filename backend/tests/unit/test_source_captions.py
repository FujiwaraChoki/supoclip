"""Generated captions must avoid captions burned into the source (#291)."""

import re

import cv2
import numpy as np

from src.media import captions
from src.media.source_captions import (
    aggregate_caption_bands,
    band_through_full_height_crop,
    caption_lines_in_frame,
)


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


def test_captions_move_above_a_burned_in_caption_band(tmp_path, monkeypatch):
    words = _timed(["real", "time,", "and", "it's"])
    clean = _build(tmp_path, monkeypatch, words)
    # MKBHD clip 1: the source's own captions sit at 0.75-0.79 of the height.
    moved = _build(tmp_path, monkeypatch, words, avoid_band=band_through_full_height_crop((0.7484, 0.7893)))

    assert "\\pos(540,1490)" in clean
    y = int(re.search(r"\\pos\(540,(\d+)\)", moved).group(1))
    text_height = int(captions.get_scaled_font_size(32, 1080) * 1.5)
    assert y + text_height // 2 < 0.7484 * 1920
    assert y > 1920 * captions.MIN_MOVED_CAPTION_CENTER


def test_band_that_does_not_collide_keeps_the_caption_in_place():
    assert captions.caption_center_clear_of_band(1490, 93, (0.10, 0.18), 1920) == 1490
    # No room above a high band: go below it instead.
    below = captions.caption_center_clear_of_band(900, 93, (0.30, 0.48), 1920)
    assert below > 0.48 * 1920


def _caption_frame(text, y=0.78, size=1.6):
    frame = np.full((1920, 1080, 3), 150, np.uint8)
    cv2.circle(frame, (540, 700), 260, (90, 120, 160), -1)  # a "face"
    cv2.rectangle(frame, (250, 1100), (830, 1700), (40, 60, 160), -1)  # a "shirt"
    scale = size * 1.5
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_DUPLEX, scale, 6)
    origin = ((1080 - tw) // 2, int(1920 * y))
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_DUPLEX, scale, (0, 0, 0), 16, cv2.LINE_AA)
    cv2.putText(frame, text, origin, cv2.FONT_HERSHEY_DUPLEX, scale, (255, 255, 255), 6, cv2.LINE_AA)
    return frame


def test_detector_finds_a_burned_in_caption_band():
    texts = ["AND IT'S HONESTLY", "PICK UP THINGS", "TRIGGER IS FUNNY", "USB AND HDMI"]
    per_frame = [caption_lines_in_frame(_caption_frame(text)) for text in texts * 3]

    band = aggregate_caption_bands(per_frame)
    assert band is not None
    assert band[0] < 0.78 < band[1] + 0.03
    assert band[1] - band[0] < 0.1


def test_detector_ignores_clean_frames():
    plain = _caption_frame("")
    assert caption_lines_in_frame(plain) == []
    assert aggregate_caption_bands([[] for _ in range(12)]) is None
    # One title card is not a caption track.
    lines = [caption_lines_in_frame(_caption_frame("BREAKING"))] + [[] for _ in range(11)]
    assert aggregate_caption_bands(lines) is None


def test_band_follows_the_ken_burns_zoom_toward_its_focus_point():
    # zoompan keeps y = (ih - ih/zoom) * 0.35 fixed, so a source row y lands at
    # zoom * y - 0.35 * (zoom - 1) at the end of the 5% punch-in.
    top, bottom = band_through_full_height_crop((0.7484, 0.7893))
    assert top == 0.7484
    assert abs(bottom - (1.05 * 0.7893 - 0.35 * 0.05)) < 1e-9
