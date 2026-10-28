"""Detect captions already burned into a source video.

Shorts and reposted clips often carry their own captions. Burning a second,
differently timed caption layer on top of them makes both unreadable, so the
render moves its captions clear of the band found here. Detection is local
(OpenCV only) and conservative: a band must hold caption-like text, centred
and outlined, at the same height in a good share of the sampled frames.
"""

import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .common import KENBURNS_FOCUS_Y, KENBURNS_ZOOM_DELTA, logger

Band = Tuple[float, float]  # (top, bottom) as fractions of the frame height

ANALYSIS_WIDTH = 540
# Only the lower part of the frame matters: that is where captions are drawn.
SEARCH_TOP = 0.40
SEARCH_BOTTOM = 0.97
SAMPLE_COUNT = 12
# Share of sampled frames that must show a caption line at the same height.
MIN_FRAME_SHARE = 0.4
BAND_TOLERANCE = 0.06


def caption_lines_in_frame(frame: np.ndarray) -> List[Band]:
    """Return centred, outlined text lines in the lower part of ``frame``.

    Burned captions are bright glyphs edged by a dark outline or shadow, many
    glyphs side by side on one line around the horizontal centre. Natural
    edges (a shirt print, a window) rarely produce that combination.
    """
    if frame is None or frame.size == 0:
        return []
    height, width = frame.shape[:2]
    scale = ANALYSIS_WIDTH / float(width)
    if scale != 1.0:
        frame = cv2.resize(
            frame, (ANALYSIS_WIDTH, max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return _caption_lines_polarity(gray) + _caption_lines_polarity(255 - gray)


def _caption_lines_polarity(gray: np.ndarray) -> List[Band]:
    h, w = gray.shape
    top, bottom = int(h * SEARCH_TOP), int(h * SEARCH_BOTTOM)
    region = gray[top:bottom]

    bright = region >= 195
    dark = cv2.dilate((region <= 70).astype(np.uint8), np.ones((5, 5), np.uint8))
    strokes = (bright & (dark > 0)).astype(np.uint8)

    # Glyph-sized pieces of outlined bright stroke.
    count, _, stats, centers = cv2.connectedComponentsWithStats(strokes)
    glyph_mask = np.zeros_like(strokes)
    glyphs: List[Tuple[float, float]] = []
    min_glyph, max_glyph = h * 0.008, h * 0.07
    for index in range(1, count):
        x, y, cw, ch, area = stats[index]
        if not (min_glyph <= ch <= max_glyph and cw <= ch * 3 and area >= 6):
            continue
        glyph_mask[y:y + ch, x:x + cw] = 1
        glyphs.append((float(centers[index][0]), float(centers[index][1])))
    if len(glyphs) < 5:
        return []

    # Join glyphs into lines.
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(3, int(w * 0.05)), max(3, int(h * 0.006)))
    )
    lines = cv2.morphologyEx(glyph_mask, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(lines)
    found: List[Band] = []
    for index in range(1, count):
        x, y, cw, ch, _area = stats[index]
        if not (h * 0.015 <= ch <= h * 0.10 and cw >= w * 0.25):
            continue
        if abs((x + cw / 2) - w / 2) > w * 0.15:
            continue
        inside = [
            gx for gx, gy in glyphs
            if x <= gx <= x + cw and y <= gy <= y + ch
        ]
        # Several glyphs spread across the line, not one long edge.
        if len(inside) < 6 or (max(inside) - min(inside)) < cw * 0.6:
            continue
        found.append(((top + y) / h, (top + y + ch) / h))
    return found


def aggregate_caption_bands(
    frame_lines: Sequence[Sequence[Band]], min_share: float = MIN_FRAME_SHARE
) -> Optional[Band]:
    """Return the band holding caption lines in enough frames, else None."""
    sampled = len(frame_lines)
    if not sampled:
        return None
    lines = [line for frame in frame_lines for line in frame]
    if not lines:
        return None
    centre = float(np.median([(a + b) / 2 for a, b in lines]))
    hits = [
        [line for line in frame if abs((line[0] + line[1]) / 2 - centre) <= BAND_TOLERANCE]
        for frame in frame_lines
    ]
    hit_frames = sum(1 for frame in hits if frame)
    if hit_frames < max(2, int(np.ceil(sampled * min_share))):
        return None
    tops = [line[0] for frame in hits for line in frame]
    bottoms = [line[1] for frame in hits for line in frame]
    return float(np.percentile(tops, 10)), float(np.percentile(bottoms, 90))


def detect_burned_caption_band(
    video_path: Path, sample_count: int = SAMPLE_COUNT
) -> Optional[Band]:
    """Sample frames across ``video_path`` and find a burned-in caption band."""
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            return None
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frame_count <= 0:
            return None
        frame_lines: List[List[Band]] = []
        for step in range(sample_count):
            index = int(frame_count * (step + 0.5) / sample_count)
            capture.set(cv2.CAP_PROP_POS_FRAMES, index)
            ok, frame = capture.read()
            if not ok:
                continue
            frame_lines.append(caption_lines_in_frame(frame))
    finally:
        capture.release()
    band = aggregate_caption_bands(frame_lines)
    if band:
        logger.info(
            "Source has burned-in captions at %.2f-%.2f of the frame height", *band
        )
    return band


def band_through_full_height_crop(band: Band, zoom_delta: float = KENBURNS_ZOOM_DELTA) -> Band:
    """Where ``band`` can appear after a full-height crop with a slow punch-in.

    A crop that keeps the whole source height leaves vertical positions alone;
    the Ken Burns zoom (up to ``1 + zoom_delta``, toward ``KENBURNS_FOCUS_Y``)
    then moves a source row ``y`` to ``zoom * y - KENBURNS_FOCUS_Y * (zoom - 1)``.
    The band covers both ends of the zoom.
    """
    zoom = 1 + zoom_delta

    def zoomed(y: float) -> float:
        return zoom * y - KENBURNS_FOCUS_Y * (zoom - 1)

    top, bottom = band
    return max(0.0, min(top, zoomed(top))), min(1.0, max(bottom, zoomed(bottom)))


def detect_rendered_caption_band(video_path: Path, video_filter: str, mode: str, duration: float) -> Optional[Band]:
    """Inspect the actual composed layout, so fit/split/cropped sources use output coordinates.

    Only called after source text is detected; ordinary footage avoids this extra pass.
    Sampling follows composition, preserving its crop, zoom and split placement.
    """
    rate = SAMPLE_COUNT / max(1.0, duration)
    tail = f"fps={rate:.9f},scale=540:960,format=gray"
    command = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video_path), "-an", "-sn"]
    if mode == "complex":
        command += ["-filter_complex", f"{video_filter};[vout]{tail}[caption_probe]", "-map", "[caption_probe]"]
    else:
        command += ["-vf", f"{video_filter},{tail}"]
    command += ["-frames:v", str(SAMPLE_COUNT), "-f", "rawvideo", "-"]
    result = subprocess.run(command, capture_output=True, timeout=max(60, int(duration * 4)))
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    size = 540 * 960
    frames = data[:len(data) // size * size].reshape(-1, 960, 540)
    return aggregate_caption_bands([caption_lines_in_frame(frame) for frame in frames])
