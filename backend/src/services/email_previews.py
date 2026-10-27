"""Still images for the "clips ready" email, embedded inline so no public URLs are needed.

Everything here is best effort and bounded by a deadline: an image that fails
or runs out of time is left out rather than delaying or failing the email.
"""
from __future__ import annotations

import json
import logging
import random
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

import cv2
import numpy as np
import requests

from ..media.ffmpeg import parse_ffprobe_video_size

logger = logging.getLogger(__name__)

MAX_CLIP_PREVIEWS = 3
PREVIEW_BUDGET_SECONDS = 8.0
CLIP_BUDGET_SECONDS = 4.0
SOURCE_WIDTH = 640
CLIP_WIDTH = 200
# Hook titles are burned into a clip's first ~4s; stills from that window
# show the headline and avoid mid-clip moments.
HOOK_WINDOW_SECONDS = (0.8, 3.6)
_JPEG_QUALITY = 80


@dataclass(frozen=True)
class ClipPreview:
    image: bytes
    title: Optional[str] = None


@dataclass(frozen=True)
class TaskPreviews:
    source: Optional[bytes] = None
    clips: tuple[ClipPreview, ...] = field(default_factory=tuple)


class _Deadline:
    def __init__(self, seconds: float):
        self.at = time.monotonic() + seconds

    def remaining(self) -> float:
        return max(0.0, self.at - time.monotonic())

    def expired(self) -> bool:
        return self.remaining() <= 0.5


def colorfulness(image: np.ndarray) -> float:
    """Hasler & Süsstrunk colorfulness; 0 for near-black or blown-out frames."""
    if not 35 <= float(image.mean()) <= 225:
        return 0.0
    b, g, r = (channel.astype("float") for channel in cv2.split(image))
    rg = np.abs(r - g)
    yb = np.abs(0.5 * (r + g) - b)
    return float(
        np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    )


def center_crop(image: np.ndarray, aspect: float) -> np.ndarray:
    """Crop to width/height ``aspect`` around the centre (also strips letterbox bars)."""
    height, width = image.shape[:2]
    if width / height > aspect:
        new_width = round(height * aspect)
        left = (width - new_width) // 2
        return image[:, left:left + new_width]
    new_height = round(width / aspect)
    top = (height - new_height) // 2
    return image[top:top + new_height]


def _encode(image: np.ndarray, width: int) -> Optional[bytes]:
    if image.shape[1] > width:
        height = round(image.shape[0] * width / image.shape[1])
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    ok, jpeg = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    return jpeg.tobytes() if ok else None


def _probe(video_path: Path, deadline: _Deadline) -> Optional[tuple[float, int, int]]:
    """Duration and displayed size (rotation applied), or None."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
             "format=duration:stream=width,height,codec_type:stream_tags=rotate:stream_side_data=rotation",
             "-of", "json", str(video_path)],
            capture_output=True, text=True, timeout=max(1.0, min(5.0, deadline.remaining())),
        )
        payload = json.loads(result.stdout or "{}")
        width, height = parse_ffprobe_video_size(payload)
        duration = float((payload.get("format") or {}).get("duration") or 0)
    except (subprocess.TimeoutExpired, ValueError, TypeError):
        return None
    return (duration, width, height) if duration > 0 else None


def _decode_frame(video_path: Path, seconds: float, deadline: _Deadline) -> Optional[np.ndarray]:
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", f"{seconds:.2f}", "-i", str(video_path),
             "-frames:v", "1", "-vf", "scale=640:-2",
             "-f", "image2pipe", "-c:v", "mjpeg", "-q:v", "3", "-"],
            capture_output=True, timeout=max(1.0, min(5.0, deadline.remaining())),
        )
    except subprocess.TimeoutExpired:
        return None
    if result.returncode != 0 or not result.stdout:
        return None
    return cv2.imdecode(np.frombuffer(result.stdout, np.uint8), cv2.IMREAD_COLOR)


def _most_colorful(
    video_path: Path, timestamps: Sequence[float], deadline: _Deadline
) -> Optional[np.ndarray]:
    best, best_score = None, 0.0
    for seconds in sorted(timestamps):
        if deadline.expired():
            break
        image = _decode_frame(video_path, seconds, deadline)
        score = colorfulness(image) if image is not None else 0.0
        if score > best_score:
            best, best_score = image, score
    return best


def source_still(
    video_path: Path, deadline: _Deadline, *, samples: int = 8, rng: Optional[random.Random] = None
) -> Optional[bytes]:
    """The most colorful of a few random moments, cropped to 16:9.

    Portrait sources are skipped: a 16:9 crop of them shows too little, and
    uncropped they would push the email text off the first screen.
    """
    probe = _probe(video_path, deadline)
    if not probe:
        return None
    duration, width, height = probe
    if height > width:
        return None
    rng = rng or random.Random()
    timestamps = [rng.uniform(duration * 0.1, duration * 0.9) for _ in range(samples)]
    image = _most_colorful(video_path, timestamps, deadline)
    return _encode(center_crop(image, 16 / 9), SOURCE_WIDTH) if image is not None else None


def clip_still(video_path: Path, deadline: _Deadline, *, samples: int = 3) -> Optional[bytes]:
    """A colorful frame from the hook-title seconds; 9:16 for vertical clips, else square."""
    probe = _probe(video_path, deadline)
    if not probe:
        return None
    duration, width, height = probe
    start, end = HOOK_WINDOW_SECONDS
    end = min(end, duration * 0.5)
    start = min(start, end)
    timestamps = [start + (end - start) * i / max(1, samples - 1) for i in range(samples)]
    image = _most_colorful(video_path, timestamps, deadline)
    if image is None:
        return None
    # One shape per task keeps the row tidy whatever the output format was.
    return _encode(center_crop(image, 9 / 16 if height > width else 1.0), CLIP_WIDTH)


def youtube_thumbnail(video_id: str, deadline: _Deadline) -> Optional[bytes]:
    # maxres is missing on some videos; hq always exists but is letterboxed 4:3.
    for name in ("maxresdefault", "hqdefault"):
        if deadline.expired():
            return None
        try:
            response = requests.get(
                f"https://i.ytimg.com/vi/{video_id}/{name}.jpg",
                timeout=max(1.0, min(5.0, deadline.remaining())),
            )
        except requests.RequestException:
            continue
        if not (response.ok and response.headers.get("content-type", "").startswith("image/")):
            continue
        image = cv2.imdecode(np.frombuffer(response.content, np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            return _encode(center_crop(image, 16 / 9), SOURCE_WIDTH)
    return None


def clip_preview(
    video_path: Path, title: Optional[str], *, budget_seconds: float = CLIP_BUDGET_SECONDS
) -> Optional[ClipPreview]:
    """Call right after a clip renders, while the file is still local.

    Taking stills here means the email never downloads clips back from S3/R2,
    so a stalled storage read can't hold up the notification.
    """
    try:
        image = clip_still(video_path, _Deadline(budget_seconds))
    except Exception:
        logger.warning("Could not build a clip preview for the completion email", exc_info=True)
        return None
    return ClipPreview(image=image, title=title) if image else None


def source_preview(
    *,
    source_path: Optional[Path],
    youtube_video_id: Optional[str],
    budget_seconds: float = PREVIEW_BUDGET_SECONDS,
) -> Optional[bytes]:
    deadline = _Deadline(budget_seconds)
    try:
        if youtube_video_id:
            return youtube_thumbnail(youtube_video_id, deadline)
        if source_path and source_path.exists():
            return source_still(source_path, deadline)
    except Exception:
        logger.warning("Could not build the source preview for the completion email", exc_info=True)
    return None
