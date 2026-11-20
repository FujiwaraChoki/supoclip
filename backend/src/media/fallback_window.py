"""Where a clip of a video without speech starts and ends.

When the transcript gives the AI nothing to select, the task used to get the
first 30 seconds of the video, wherever that fell. Edited videos without
commentary (comparisons, montages, tests) are cut into chapters by title
cards: a dark, still frame with a line of text. The first 30 seconds of one is
an intro and half a chapter. ``fallback_window`` looks at the picture and
returns the first whole chapter instead: from a title card to the next one.
A video without cards keeps the plain window.
"""

from pathlib import Path
from typing import List, Tuple
import logging
import subprocess

import numpy as np

logger = logging.getLogger(__name__)

SCAN_SECONDS = 90.0          # how much of the video the fallback asks for, so there is a chapter to find
_FPS = 4.0
_SIZE = (160, 90)
CARD_DARK_LEVEL = 70         # grey level under which a pixel is dark
CARD_DARK_SHARE = 0.85       # share of dark pixels on a title card (measured 0.88-1.0; gameplay at night 0.6)
CARD_MIN_SECONDS = 0.75
CARD_LEAD_SECONDS = 20.0     # the first card comes this early, or the video is not built from chapters
CHAPTER_MIN_SECONDS = 12.0


def title_cards(video_path: Path, start: float, end: float) -> List[Tuple[float, float]]:
    """Spans (seconds) of dark, held frames between ``start`` and ``end``."""
    width, height = _SIZE
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(video_path), "-t", f"{end - start:.3f}", "-an",
         "-vf", f"fps={_FPS},scale={width}:{height},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=120,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-300:])
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    frames = data[:len(data) // (width * height) * (width * height)].reshape(-1, height, width)
    dark = (frames < CARD_DARK_LEVEL).mean(axis=(1, 2)) >= CARD_DARK_SHARE
    cards: List[Tuple[float, float]] = []
    run_start = None
    for index, flag in enumerate(list(dark) + [False]):
        if flag and run_start is None:
            run_start = index
        elif not flag and run_start is not None:
            if (index - run_start) / _FPS >= CARD_MIN_SECONDS:
                cards.append((start + run_start / _FPS, start + index / _FPS))
            run_start = None
    return cards


def fallback_window(
    video_path: Path, start: float, end: float, target_seconds: float, max_seconds: float
) -> Tuple[float, float]:
    """The clip to make of ``start``-``end`` when nothing was selected: (start, end) in seconds.

    The first chapter between two title cards, opening on its card, when the video has them and the chapter fits
    ``max_seconds``. Otherwise the first ``target_seconds``, as before.
    """
    plain = (start, min(end, start + max(1.0, target_seconds)))
    if end - start <= plain[1] - plain[0] + 1.0:
        return plain
    try:
        cards = title_cards(Path(video_path), start, end)
    except Exception as exc:
        logger.warning("Fallback window: could not read %s (%s); using the first %.0fs", video_path, exc, target_seconds)
        return plain
    # A black frame the video opens on is its fade-in, not a chapter's card.
    cards = [card for card in cards if card[0] > start + 0.5]
    if not cards or cards[0][0] - start > CARD_LEAD_SECONDS:
        return plain
    opening = cards[0][0]
    for card_start, _ in cards[1:]:
        length = card_start - opening
        if length < CHAPTER_MIN_SECONDS:
            continue
        if length <= max_seconds:
            logger.info("Fallback window: chapter %.1f-%.1fs between title cards", opening, card_start)
            return opening, card_start
        break
    return plain
