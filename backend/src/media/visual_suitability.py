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

A still picture is not always unusable: a news anchor or presenter standing
in front of a fixed backdrop barely moves at this resolution (the 2026-10
tagesschau "Endlager" clip scores 1.0, the same as the interruption card). So
a static segment is only rejected when its held picture shows no face.
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


# Face check on the held picture: frames decoded this wide, a face must be at
# least this share of the frame width (an anchor in a wide studio shot is
# about 6%) and YuNet must be this sure, so crowds of tiny faces and printed
# text do not count.
STATIC_FACE_FRAME_WIDTH = 640
STATIC_FACE_MIN_WIDTH_RATIO = 0.03
STATIC_FACE_MIN_SCORE = 0.8
STATIC_FACE_SAMPLES = 3


def same_picture(first: np.ndarray, second: np.ndarray) -> bool:
    changed = np.abs(first.astype(np.int16) - second.astype(np.int16)) > STATIC_PIXEL_DELTA
    return float(changed.mean()) <= STATIC_CHANGED_PIXEL_RATIO


def longest_static_run(frames: Sequence[np.ndarray]) -> Tuple[int, int]:
    """(first index, length) of the longest run of frames showing the run's first picture.

    Comparing against the run's first frame (not just the previous one) keeps a
    slow pan or fade from chaining into one long "static" run.
    """
    if len(frames) == 0:
        return 0, 0
    best_start, best = 0, 1
    run_start, run = 0, 1
    anchor = frames[0]
    for index, frame in enumerate(frames[1:], start=1):
        if same_picture(anchor, frame):
            run += 1
        else:
            anchor = frame
            run_start, run = index, 1
        if run > best:
            best_start, best = run_start, run
    return best_start, best


def static_run_fraction(frames: Sequence[np.ndarray]) -> float:
    """Longest static run as a share of all frames (see ``longest_static_run``)."""
    if len(frames) == 0:
        return 0.0
    return longest_static_run(frames)[1] / len(frames)


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


def _sample_times(ranges: Sequence[Tuple[float, float]]) -> List[float]:
    """Local timestamp of each frame ``sample_grey_frames`` returns for ``ranges``."""
    times: List[float] = []
    for start, end in ranges:
        start, end = float(start), float(end)
        count = int(np.ceil(max(0.0, end - start) * STATIC_SAMPLE_FPS))
        times.extend(max(0.0, start) + index / STATIC_SAMPLE_FPS for index in range(count))
    return times


def _decode_frame_bgr(video_path: Path, time: float) -> Optional[np.ndarray]:
    width = STATIC_FACE_FRAME_WIDTH
    command = [
        "ffmpeg", "-v", "error", "-nostdin",
        "-ss", f"{max(0.0, time):.3f}", "-i", str(video_path),
        "-frames:v", "1", "-an", "-sn",
        "-vf", f"scale={width}:-2", "-f", "rawvideo", "-pix_fmt", "bgr24", "-",
    ]
    result = subprocess.run(command, capture_output=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
    height = len(result.stdout) // (width * 3)
    if height <= 0:
        return None
    return np.frombuffer(result.stdout[: height * width * 3], dtype=np.uint8).reshape(height, width, 3)


def still_picture_shows_face(
    video_path: Path, ranges: Sequence[Tuple[float, float]], detector=None
) -> bool:
    """Whether the segment's held picture shows a face (a presenter, not a card).

    Decodes a few frames from inside the longest static run and runs YuNet on
    them. Raises when the picture cannot be checked, so the caller can keep
    the segment.
    """
    ranges = [(float(start), float(end)) for start, end in ranges]
    frames: List[np.ndarray] = []
    for start, end in ranges:
        frames.extend(sample_grey_frames(video_path, start, end))
    if not frames:
        raise RuntimeError("no frames decoded")
    times = _sample_times(ranges)
    run_start, run_length = longest_static_run(frames)
    picks = sorted(
        {
            run_start + (run_length - 1) * step // max(1, STATIC_FACE_SAMPLES - 1)
            for step in range(STATIC_FACE_SAMPLES)
        }
    )
    if detector is None:
        from .face_detection import open_yunet_detector

        detector = open_yunet_detector()
        if detector is None:
            raise RuntimeError("YuNet face detector unavailable")
    for pick in picks:
        if pick >= len(times):
            continue
        frame = _decode_frame_bgr(video_path, times[pick])
        if frame is None:
            continue
        for _x, _y, face_width, _h, score in detector.detect(frame):
            if (
                score >= STATIC_FACE_MIN_SCORE
                and face_width >= STATIC_FACE_MIN_WIDTH_RATIO * frame.shape[1]
            ):
                return True
    return False


def is_interruption_notice(text: str) -> bool:
    """Explicit unavailability notices, including animated/zoomed broadcaster slates."""
    import re
    text = " ".join((text or "").lower().split())
    patterns = [r"kurze unterbrechung", r"(?:these|this) (?:images|pictures|footage).{0,80}(?:cannot|can't|not allowed).{0,40}(?:show|display)",
                r"(?:footage|images|video) (?:is |are )?(?:unavailable|not available)",
                r"(?:technical difficulties|please stand by|we(?:'ll| will) be right back)",
                r"(?:diese|die) bilder.{0,100}(?:nicht|leider nicht).{0,30}(?:zeigen|gezeigt)",
                r"aus (?:rechtlichen|lizenzrechtlichen) gründen.{0,80}(?:nicht|keine).{0,30}(?:zeigen|bilder)"]
    return any(re.search(pattern, text) for pattern in patterns)


def segment_shows_interruption_notice(video_path: Path, ranges: Sequence[Tuple[float, float]]) -> bool:
    try:
        return _segment_shows_interruption_notice(video_path, ranges)
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return False


def _segment_shows_interruption_notice(video_path: Path, ranges: Sequence[Tuple[float, float]]) -> bool:
    """Read three picture samples locally; motion/zoom cannot disguise a notice.

    A majority must contain an explicit interruption message. No model or
    network is involved. Missing OCR or a failed sample keeps uncertain footage.
    """
    import os
    import shutil
    import cv2

    binary = shutil.which("tesseract")
    total = sum(max(0., end - start) for start, end in ranges)
    if binary is None or total <= 0 or not Path(video_path).is_file():
        return False
    matches = 0
    for fraction in (.15, .5, .85):
        cursor, target = 0., total * fraction
        for start, end in ranges:
            if target < cursor + end - start:
                timestamp = start + target - cursor
                break
            cursor += end - start
        else:
            continue
        frame = _decode_frame_bgr(video_path, timestamp)
        if frame is None:
            return False
        frame = cv2.resize(frame, None, fx=1.5, fy=1.5, interpolation=cv2.INTER_CUBIC)
        ok, encoded = cv2.imencode(".png", frame)
        if not ok:
            return False
        result = subprocess.run([binary, "stdin", "stdout", "-l", "eng", "--psm", "11"],
                                input=encoded.tobytes(), capture_output=True, timeout=10,
                                env={**os.environ, "OMP_THREAD_LIMIT": "1"})
        if result.returncode:
            return False
        matches += is_interruption_notice(result.stdout.decode("utf-8", "replace"))
    return matches >= 2
