"""Words written on the picture, read with the local OCR (tesseract).

The 9:16 planner and the caption code both need to know what a frame says, not
just that something in it looks like text: brick courses, window bars and
foliage pass every shape test for "lines of text", and a subtitle is not a
slide. ``read_frame_words`` reads one frame and returns each word with its box,
so callers can count the words a crop would lose or compare them with speech.

Everything here degrades to "unknown": a missing binary, a failed decode or a
timeout returns None, and callers keep their no-OCR behaviour.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

READ_WIDTH = 1280            # frames are read at this width: small subtitles need tall letters
MIN_CONFIDENCE = 60.0        # tesseract's own 0-100 score; lower is mostly texture
MIN_WORD_CHARS = 2
LINE_TOLERANCE = 0.6         # words whose centres are within this share of a word height share a line
MAX_FRAME_READS = 24         # frames read per clip (about half a second each)
READ_GRID_SECONDS = 0.2      # moments this close are the same frame
BRIGHT_INK_LEVEL = 190       # grey level from which a pixel is "ink" in the bright-ink reading

_WORD = re.compile(r"[^\W_]{2,}", re.UNICODE)


@dataclass(frozen=True)
class ScreenWord:
    """One word read off a frame; coordinates are 0-1 fractions of the frame."""

    text: str
    left: float
    top: float
    right: float
    bottom: float
    confidence: float

    @property
    def centre_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return self.bottom - self.top


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def parse_tsv(tsv: str, width: int, height: int) -> List[ScreenWord]:
    """Words of a tesseract TSV page, as fractions of a ``width`` x ``height`` frame."""
    words: List[ScreenWord] = []
    for row in tsv.splitlines()[1:]:
        cells = row.split("\t")
        if len(cells) < 12 or cells[0] != "5":
            continue
        try:
            left, top, w, h = (int(cells[i]) for i in (6, 7, 8, 9))
            confidence = float(cells[10])
        except ValueError:
            continue
        text = cells[11].strip()
        if confidence < MIN_CONFIDENCE or w <= 0 or h <= 0:
            continue
        match = _WORD.search(text)
        if not match or len(match.group(0)) < MIN_WORD_CHARS:
            continue
        words.append(ScreenWord(
            text=text, left=left / width, top=top / height,
            right=(left + w) / width, bottom=(top + h) / height, confidence=confidence,
        ))
    return words


def _frame_png(video_path: Path, seconds: float, video_filter: str) -> bytes:
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{max(0.0, seconds):.3f}", "-i", str(video_path),
         "-frames:v", "1", "-an", "-sn", "-filter_complex", video_filter, "-f", "image2pipe", "-c:v", "png", "-"],
        capture_output=True, timeout=30,
    )
    if frame.returncode != 0 or not frame.stdout:
        raise RuntimeError(frame.stderr.decode("utf-8", "replace")[-200:] or "no frame")
    return frame.stdout


def _tesseract_tsv(png: bytes) -> str:
    binary = shutil.which("tesseract")
    if binary is None:
        raise RuntimeError("tesseract is not installed")
    read = subprocess.run(
        [binary, "stdin", "stdout", "--psm", "11", "tsv"], input=png, capture_output=True, timeout=45,
        env={**os.environ, "OMP_THREAD_LIMIT": "1"},
    )
    if read.returncode != 0:
        raise RuntimeError(read.stderr.decode("utf-8", "replace")[-200:])
    return read.stdout.decode("utf-8", "replace")


def read_frame_words(
    video_path: Path, seconds: float, region: Optional[Tuple[float, float]] = None,
) -> Optional[List[ScreenWord]]:
    """Words on the frame at ``seconds``, or None when the frame cannot be read.

    ``region`` (top, bottom as fractions of the height) reads only that band,
    enlarged, which is how small subtitles become legible. The frame is read
    three ways, stacked into one page for one OCR run: as it is, inverted (light
    text on footage, dark text on a card) and with only its bright pixels kept
    as ink (heavy white or yellow display captions with outlines and shadows,
    which the OCR garbles otherwise). A word found more than once counts once.
    """
    try:
        from .ffmpeg import ffprobe_video_size

        width, height = ffprobe_video_size(video_path)
        if width <= 0 or height <= 0:
            return None
        top, bottom = region if region else (0.0, 1.0)
        top, bottom = max(0.0, min(top, 1.0)), max(0.0, min(bottom, 1.0))
        if bottom - top <= 0.01:
            return None
        band_h = max(2, int(round(height * (bottom - top))) // 2 * 2)
        band_y = min(int(round(height * top)), height - band_h)
        out_w = READ_WIDTH
        out_h = max(2, int(round(band_h * out_w / width)) // 2 * 2)
        page = (
            f"crop={width}:{band_h}:0:{band_y},scale={out_w}:{out_h}:flags=lanczos,format=gray,"
            "split=3[plain][copy][bright];[copy]negate[inverted];"
            f"[bright]lut=y='if(gte(val,{BRIGHT_INK_LEVEL}),0,255)'[ink];"
            "[plain][inverted][ink]vstack=inputs=3"
        )
        found: List[ScreenWord] = []
        for word in parse_tsv(_tesseract_tsv(_frame_png(video_path, seconds, page)), out_w, out_h):
            # The page stacks three readings of the same band: as it is, inverted, and bright ink on white.
            shift = float(min(2, int(word.top)))
            if word.bottom > shift + 1.0 + 1e-6:
                continue        # a box that runs from one reading into the next is not a word
            placed = ScreenWord(
                word.text, word.left, top + (word.top - shift) * (bottom - top), word.right,
                top + (word.bottom - shift) * (bottom - top), word.confidence,
            )
            if not any(_same_place(placed, other) for other in found):
                found.append(placed)
        return found
    except Exception as exc:  # noqa: BLE001 - unknown is an answer; the caller keeps its default
        logger.debug("Screen text unavailable at %.2fs of %s (%s)", seconds, video_path, exc)
        return None


class FrameReader:
    """Reads frames of one clip, each moment once, up to a budget.

    Moments are rounded to ``READ_GRID_SECONDS`` so a plan built twice (on sampled cuts, then on exact ones)
    reads the same frames. Past the budget a moment that was not read is unknown (None), and callers keep
    their no-OCR default.
    """

    def __init__(self, video_path: Path, budget: int = MAX_FRAME_READS, read=read_frame_words):
        self.video_path = video_path
        self.budget = budget
        self._read = read
        self._frames: dict = {}

    def __call__(self, moment: float) -> Optional[List[ScreenWord]]:
        key = round(round(float(moment) / READ_GRID_SECONDS) * READ_GRID_SECONDS, 3)
        if key not in self._frames:
            if self.budget <= 0:
                return None
            self.budget -= 1
            self._frames[key] = self._read(self.video_path, key)
        return self._frames[key]


def content_tokens(words: Sequence[ScreenWord]) -> set:
    """The words of a read that identify what is written (three letters or more, lower case)."""
    return {
        token.lower()
        for word in words
        for token in _WORD.findall(word.text)
        if len(token) >= 3
    }


def _same_place(a: ScreenWord, b: ScreenWord) -> bool:
    overlap_x = min(a.right, b.right) - max(a.left, b.left)
    overlap_y = min(a.bottom, b.bottom) - max(a.top, b.top)
    return overlap_x > 0.5 * min(a.right - a.left, b.right - b.left) and overlap_y > 0.5 * min(a.height, b.height)


def text_lines(words: Sequence[ScreenWord]) -> List[List[ScreenWord]]:
    """``words`` grouped into lines, top to bottom, each left to right."""
    lines: List[List[ScreenWord]] = []
    for word in sorted(words, key=lambda w: (w.centre_y, w.left)):
        for line in lines:
            reference = line[0]
            if abs(word.centre_y - reference.centre_y) <= LINE_TOLERANCE * max(word.height, reference.height):
                line.append(word)
                break
        else:
            lines.append([word])
    return [sorted(line, key=lambda w: w.left) for line in lines]


def words_outside(words: Sequence[ScreenWord], left: float, right: float) -> List[ScreenWord]:
    """Words a crop keeping columns ``left``-``right`` (0-1) would cut or lose."""
    return [word for word in words if word.left < left or word.right > right]
