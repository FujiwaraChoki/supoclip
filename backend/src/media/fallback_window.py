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
CARD_TEXT_LEVEL = 140        # ... and its text is bright:
CARD_TEXT_SHARE = 0.005      # this share of the pixels at least (measured 0.017-0.035; a fade to black 0.0)
CARD_STILL_DIFF = 5.0        # median change between frames, grey levels (cards 0.0-4.4; dark gameplay 6 and up)
CARD_MIN_SECONDS = 0.75
CARD_MAX_SECONDS = 6.0       # held longer, a dark frame is a scene, not a card
CARD_LEAD_SECONDS = 20.0     # the first card comes this early, or the video is not built from chapters
CHAPTER_MIN_SECONDS = 12.0


def title_cards(video_path: Path, start: float, end: float) -> List[Tuple[float, float]]:
    """Spans (seconds) of title cards between ``start`` and ``end``: dark, still, with bright text, held briefly.

    Darkness alone is not a card: night gameplay, a dim studio and a pillarboxed vertical video are dark too.
    They move, or they stay dark for longer than anyone holds a title. A fade to black has no text.
    """
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
    if not len(frames):
        return []
    dark = (frames < CARD_DARK_LEVEL).mean(axis=(1, 2)) >= CARD_DARK_SHARE
    text = (frames >= CARD_TEXT_LEVEL).mean(axis=(1, 2)) >= CARD_TEXT_SHARE
    change = np.abs(np.diff(frames.astype(np.int16), axis=0)).mean(axis=(1, 2))
    cards: List[Tuple[float, float]] = []
    run_start = None
    for index, flag in enumerate(list(dark & text) + [False]):
        if flag and run_start is None:
            run_start = index
        elif not flag and run_start is not None:
            seconds = (index - run_start) / _FPS
            inside = change[run_start:index - 1]
            still = not len(inside) or float(np.median(inside)) <= CARD_STILL_DIFF
            if CARD_MIN_SECONDS <= seconds <= CARD_MAX_SECONDS and still:
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
