"""Black bars baked into a source are not picture.

A 2.39:1 film look inside a 16:9 file carries black bars at the top and bottom of every frame. Planned
as picture, they shrink a fitted frame to a quarter of the screen, come back as black bands in a
full-height crop and as a stripe between stacked panels, and they darken the blurred fill.

The bars are measured once per clip and the layout is planned on the picture between them. The plan
starts with the crop that removes them, so it still applies to the uncropped source cut: the clip
studio replays it unchanged.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np

from .common import INTERMEDIATE_CRF, logger
from .ffmpeg import ffprobe_duration, run_ffmpeg_command

PROBE_W = 320
PROBE_FRAMES = 10
# A pixel is black at or below this (0-255); a row is black when all of its pixels are.
BLACK_ROW_MAX = 24
# A row belongs to a bar when it is black in this share of the frames that show a picture at all ...
BAR_ROW_SUPPORT = 0.8
# ... and in nearly all of the rest holds no more than a line of text: at most this share of its
# pixels is lit (a subtitle written into the bar), in this share of the frames. A shot of the same
# video without bars fills those rows with picture and keeps them.
BAR_TEXT_MAX_LIT = 0.45
BAR_TEXT_SUPPORT = 0.95
# A channel logo or watermark sits in a bar for the whole clip: a row that never has more than this
# share of its pixels lit is bar as well, although it is never fully black.
BAR_LOGO_MAX_LIT = 0.2
# Frames darker than this on average say nothing about bars (a fade, a night shot).
DARK_FRAME_MEAN = 14.0
# A bar is at least this share of the height; smaller strips are encoder padding or a soft edge.
MIN_BAR_FRAC = 0.04
# The bars together are at least this share, and leave at least half of the frame as picture.
MIN_BARS_FRAC = 0.08
MIN_PICTURE_FRAC = 0.5


def bars_from_frames(frames: np.ndarray) -> Optional[Tuple[float, float]]:
    """(top, bottom) bar heights as fractions of the frame height, from grey frames (n, h, w), or None.

    A bar row is black in most frames and never holds more than a line of text, or carries only a
    small logo throughout. The source's own subtitles written into a bar go with it (the clip gets
    its own captions), and so does a watermark; a shot without bars in the same clip keeps its
    picture, and with it the bars of the other shots.
    """
    if frames.ndim != 3 or len(frames) < 3:
        return None
    lit = frames[frames.reshape(len(frames), -1).mean(axis=1) > DARK_FRAME_MEAN]
    if len(lit) < 3:
        return None
    height = lit.shape[1]
    mostly_black = (lit.max(axis=2) <= BLACK_ROW_MAX).mean(axis=0) >= BAR_ROW_SUPPORT
    lit_share = (lit > BLACK_ROW_MAX).mean(axis=2)
    text_at_most = (lit_share <= BAR_TEXT_MAX_LIT).mean(axis=0) >= BAR_TEXT_SUPPORT
    logo_at_most = (lit_share <= BAR_LOGO_MAX_LIT).mean(axis=0) >= BAR_TEXT_SUPPORT
    black = (mostly_black & text_at_most) | logo_at_most
    top = 0
    while top < height and black[top]:
        top += 1
    bottom = 0
    while bottom < height - top and black[height - 1 - bottom]:
        bottom += 1
    top_frac = top / height if top / height >= MIN_BAR_FRAC else 0.0
    bottom_frac = bottom / height if bottom / height >= MIN_BAR_FRAC else 0.0
    if top_frac + bottom_frac < MIN_BARS_FRAC or 1.0 - top_frac - bottom_frac < MIN_PICTURE_FRAC:
        return None
    return top_frac, bottom_frac


def probe_frames(input_path: Path, duration: float, probe_h: int) -> np.ndarray:
    """A few small grey frames spread over the clip, each fetched by seeking (no full decode)."""
    size = PROBE_W * probe_h
    frames = []
    for index in range(PROBE_FRAMES):
        at = duration * (index + 0.5) / PROBE_FRAMES
        try:
            result = subprocess.run(
                ["ffmpeg", "-v", "error", "-an", "-sn", "-ss", f"{at:.3f}", "-i", str(input_path), "-frames:v", "1",
                 "-vf", f"scale={PROBE_W}:{probe_h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
                capture_output=True, timeout=60,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if result.returncode == 0 and len(result.stdout) >= size:
            frames.append(np.frombuffer(result.stdout[:size], dtype=np.uint8).reshape(probe_h, PROBE_W))
    return np.stack(frames) if frames else np.empty((0, probe_h, PROBE_W), dtype=np.uint8)


def detect_letterbox(input_path: Path, width: int, height: int) -> Optional[Tuple[int, int]]:
    """(top, bottom) rows of black bar in the clip, in source pixels (even), or None."""
    if width < 16 or height < 16:
        return None
    duration = ffprobe_duration(input_path)
    if duration <= 0:
        return None
    probe_h = max(2, int(round(height * PROBE_W / width / 2)) * 2)
    bars = bars_from_frames(probe_frames(input_path, duration, probe_h))
    if bars is None:
        return None
    # One probe row of margin into the picture: the bar's edge is soft after scaling.
    margin = height / probe_h
    top = int((bars[0] * height + margin) // 2 * 2) if bars[0] else 0
    bottom = int((bars[1] * height + margin) // 2 * 2) if bars[1] else 0
    if height - top - bottom < 16:
        return None
    return top, bottom


def cropped_copy(input_path: Path, destination: Path, width: int, height: int, top: int) -> bool:
    """The clip without its bars, for the layout analysis. Audio is not needed there."""
    result = run_ffmpeg_command([
        "ffmpeg", "-y", "-i", str(input_path), "-an", "-vf", f"crop={width}:{height}:0:{top}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(INTERMEDIATE_CRF), "-pix_fmt", "yuv420p",
        str(destination),
    ])
    return result.returncode == 0 and destination.exists()


_INPUT = re.compile(r"\[0:v\]")


def plan_on_picture(video_filter: str, mode: str, width: int, height: int, top: int) -> str:
    """``video_filter`` (planned on the picture between the bars) as a plan for the whole frame.

    A simple chain gets the crop in front. A graph reads its input through ``[0:v]``; every such
    read is pointed at the cropped picture instead.
    """
    crop = f"crop={width}:{height}:0:{top}"
    if mode != "complex":
        return f"{crop},{video_filter}"
    reads = len(_INPUT.findall(video_filter))
    if reads == 0:
        logger.warning("Layout graph does not read [0:v]; keeping the letterbox bars")
        return video_filter
    if reads == 1:
        return f"[0:v]{crop}[lbx0];" + _INPUT.sub("[lbx0]", video_filter)
    labels: List[str] = [f"[lbx{index}]" for index in range(reads)]
    counter = iter(labels)
    return f"[0:v]{crop},split={reads}{''.join(labels)};" + _INPUT.sub(lambda _m: next(counter), video_filter)
