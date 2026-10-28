"""Cheap visual suitability check for selected clip segments.

Selection is transcript-driven, so a strong spoken passage can sit on a
picture that is unusable as a short: a broadcaster's interruption card
("Diese Bilder dürfen ... nicht gezeigt werden"), a placeholder, a test card or
black. Those pictures hold one frame for the whole segment. Real content,
even a slow diagram or a locked-off podcast camera, keeps changing somewhere.

Frames are sampled at 2 fps and 32x18 grey, so a 60 s segment is about 70 KB
of pixels. A frame counts as the same picture when almost no pixel moved
against the start of the current run; the score is the longest such run as a
share of the sampled frames.
"""

import subprocess
from pathlib import Path
from typing import Iterable, List, Optional, Sequence, Tuple

import numpy as np

STATIC_SAMPLE_FPS = 2.0
STATIC_SAMPLE_WIDTH = 32
STATIC_SAMPLE_HEIGHT = 18
# Grey levels a pixel must move to count as changed (compression noise stays
# well below this), and the share of changed pixels a frame may have and still
# be the same picture. Measured on the 2026-10 audit sources: the rendered
# interruption card scores 0.84-1.0 even with the renderer's slow punch-in on
# top, while podcasts, news, gameplay, cooking and 3Blue1Brown animation stay
# at or below 0.6 over any 20 s window.
STATIC_PIXEL_DELTA = 12
STATIC_CHANGED_PIXEL_RATIO = 0.05
# A segment is visually static when one picture covers this share of it.
STATIC_SEGMENT_MIN_FRACTION = 0.8
STATIC_MIN_SAMPLED_FRAMES = 6


def same_picture(first: np.ndarray, second: np.ndarray) -> bool:
    changed = np.abs(first.astype(np.int16) - second.astype(np.int16)) > STATIC_PIXEL_DELTA
    return float(changed.mean()) <= STATIC_CHANGED_PIXEL_RATIO


def static_run_fraction(frames: Sequence[np.ndarray]) -> float:
    """Longest run of frames showing the run's first picture, as a share of all frames.

    Comparing against the run's first frame (not just the previous one) keeps a
    slow pan or fade from chaining into one long "static" run.
    """
    if len(frames) == 0:
        return 0.0
    best = run = 1
    anchor = frames[0]
    for frame in frames[1:]:
        if same_picture(anchor, frame):
            run += 1
        else:
            anchor = frame
            run = 1
        best = max(best, run)
    return best / len(frames)


def sample_grey_frames(video_path: Path, start: float, end: float) -> List[np.ndarray]:
    """Decode ``start``-``end`` of ``video_path`` as tiny grey frames."""
    duration = end - start
    if duration <= 0:
        return []
    width, height = STATIC_SAMPLE_WIDTH, STATIC_SAMPLE_HEIGHT
    command = [
        "ffmpeg", "-v", "error", "-nostdin",
        "-ss", f"{max(0.0, start):.3f}", "-t", f"{duration:.3f}",
        "-i", str(video_path),
        "-an", "-sn",
        "-vf", f"fps={STATIC_SAMPLE_FPS:g},scale={width}:{height}:flags=area,format=gray",
        "-f", "rawvideo", "-",
    ]
    result = subprocess.run(command, capture_output=True, timeout=max(60, int(duration * 2)))
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
    frame_bytes = width * height
    raw = result.stdout[: len(result.stdout) - len(result.stdout) % frame_bytes]
    if not raw:
        return []
    return list(np.frombuffer(raw, dtype=np.uint8).reshape(-1, height, width))


def segment_static_fraction(
    video_path: Path, ranges: Iterable[Tuple[float, float]]
) -> Optional[float]:
    """Static-run share over the segment's ranges, or None if too little was decoded.

    Ranges are played back to back, so a picture held across a range boundary
    still counts as one run.
    """
    frames: List[np.ndarray] = []
    for start, end in ranges:
        frames.extend(sample_grey_frames(video_path, float(start), float(end)))
    if len(frames) < STATIC_MIN_SAMPLED_FRAMES:
        return None
    return static_run_fraction(frames)


def is_visually_static(fraction: Optional[float]) -> bool:
    return fraction is not None and fraction >= STATIC_SEGMENT_MIN_FRACTION

