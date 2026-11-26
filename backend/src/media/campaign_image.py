"""Campaign image: a creator's picture shown above the video on every clip of a project.

Reward campaigns ask creators to keep a banner on screen for the whole short. On a 9:16 clip
whose source sits in the middle of the frame (the full-frame fit), the band above the video is
free, so the image goes there: scaled to fit the band, centred, for the whole clip.

Two halves:
- ``normalize_campaign_image`` checks an upload and re-encodes it as a PNG (alpha kept,
  metadata dropped, long side capped) before it is stored.
- ``apply_campaign_image`` burns the stored image into a rendered clip in place, as the last
  pass after the resolution cap, so it sits at the same place on every plan.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Tuple

import cv2
import numpy as np

from .ffmpeg import build_final_video_encode_args, ffprobe_video_size, run_ffmpeg_command

logger = logging.getLogger(__name__)

MAX_CAMPAIGN_IMAGE_UPLOAD_BYTES = 8 * 1024 * 1024
# Stored images never need more than this: the band is at most 1080 wide.
MAX_CAMPAIGN_IMAGE_SIDE = 1600
MIN_CAMPAIGN_IMAGE_SIDE = 16
SUPPORTED_CAMPAIGN_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp"}
SUPPORTED_CAMPAIGN_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}

# The band above a full-frame fit: a 16:9 source on a 1080x1920 canvas starts at about 34% of
# the height. The box stops just short of it and keeps a small top margin and side margins.
BAND_TOP_FRAC = 0.045
BAND_BOTTOM_FRAC = 0.335
BAND_WIDTH_FRAC = 0.90


class CampaignImageError(ValueError):
    """The upload is not an image this feature can use."""


@dataclass(frozen=True)
class NormalizedImage:
    png: bytes
    width: int
    height: int


def normalize_campaign_image(data: bytes) -> NormalizedImage:
    """Decode an uploaded PNG/JPEG/WebP and return it as a clean PNG."""
    if not data:
        raise CampaignImageError("The image is empty.")
    if len(data) > MAX_CAMPAIGN_IMAGE_UPLOAD_BYTES:
        raise CampaignImageError("The image is larger than 8 MB.")
    buffer = np.frombuffer(data, dtype=np.uint8)
    is_jpeg = data[:3] == b"\xff\xd8\xff"
    # IMREAD_COLOR applies a JPEG's EXIF rotation (phone photos); other formats keep their alpha.
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR if is_jpeg else cv2.IMREAD_UNCHANGED)
    if image is None:
        raise CampaignImageError("Use a PNG, JPEG or WebP image.")
    if image.dtype != np.uint8:
        image = cv2.convertScaleAbs(image, alpha=255.0 / 65535.0)
    if image.ndim == 2:
        image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    height, width = image.shape[:2]
    if min(width, height) < MIN_CAMPAIGN_IMAGE_SIDE:
        raise CampaignImageError("The image is too small.")
    longest = max(width, height)
    if longest > MAX_CAMPAIGN_IMAGE_SIDE:
        scale = MAX_CAMPAIGN_IMAGE_SIDE / longest
        width, height = max(1, round(width * scale)), max(1, round(height * scale))
        image = cv2.resize(image, (width, height), interpolation=cv2.INTER_AREA)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        raise CampaignImageError("The image could not be read.")
    return NormalizedImage(png=encoded.tobytes(), width=width, height=height)


def campaign_image_box(frame_width: int, frame_height: int) -> Tuple[int, int, int, int]:
    """The box the image is fitted into, as ``(x, y, width, height)`` in output pixels."""
    box_width = int(frame_width * BAND_WIDTH_FRAC)
    top = int(frame_height * BAND_TOP_FRAC)
    box_height = int(frame_height * BAND_BOTTOM_FRAC) - top
    return (frame_width - box_width) // 2, top, box_width, box_height


def apply_campaign_image(video_path: Path, image_path: Path) -> bool:
    """Overlay ``image_path`` in the band above the video, in place. Returns whether it changed.

    Landscape and square output has no band above the video, so it is left alone.
    """
    width, height = ffprobe_video_size(video_path)
    if height <= width:
        logger.info("Campaign image skipped for %s: output is not portrait", video_path.name)
        return False
    x, y, box_width, box_height = campaign_image_box(width, height)
    output_path = video_path.with_name(f".{video_path.stem}.campaign{video_path.suffix}")
    graph = (
        f"[1:v]scale=w={box_width}:h={box_height}:force_original_aspect_ratio=decrease:flags=lanczos,"
        "format=rgba[campaign];"
        f"[0:v][campaign]overlay=x={x}+({box_width}-w)/2:y={y}+({box_height}-h)/2:"
        "eof_action=repeat:format=auto,format=yuv420p,setsar=1[v]"
    )
    command = [
        "ffmpeg", "-y", "-v", "error",
        "-i", str(video_path),
        "-i", str(image_path),
        "-filter_complex", graph,
        "-map", "[v]", "-map", "0:a?",
        *build_final_video_encode_args(),
        "-c:a", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    result = run_ffmpeg_command(command)
    if result.returncode != 0 or not output_path.exists():
        output_path.unlink(missing_ok=True)
        raise RuntimeError(f"Unable to add the campaign image to {video_path.name}: {result.stderr[-500:]}")
    output_path.replace(video_path)
    return True
