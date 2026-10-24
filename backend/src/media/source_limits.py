"""Cap source video at 1080p and 60 fps before the rest of the pipeline.

1080p means the frame fits in a 1920×1080 box in either orientation, so a
landscape 1920×1080 and a portrait 1080×1920 both pass. Anything larger is
scaled down with the aspect ratio preserved. Frame rates above 60 (60 and
59.94 stay) are dropped to 60. Audio is copied when the container allows it.

The working file is replaced in place. Transcript sidecars, storage cache
keys, and download cleanup all keep the same path. A fresh download of a
stored original is capped again the next time it is prepared.
"""

from __future__ import annotations

import fcntl
import json
import logging
import os
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_LONG_EDGE = 1920
MAX_SHORT_EDGE = 1080
MAX_OUTPUT_FPS = 60
# 60/1 and 60000/1001 (59.94) are already within the cap.
FPS_EPSILON = 0.01


# Transfers that need a tone map before an 8-bit H.264 master looks right.
HDR_TRANSFERS = {"smpte2084", "arib-std-b67"}
NTSC_RATES = (
    (24000 / 1001, "24000/1001"),
    (30000 / 1001, "30000/1001"),
    (60000 / 1001, "60000/1001"),
)


@dataclass(frozen=True)
class VideoGeometry:
    width: int
    height: int
    fps: float | None
    rotation: int
    sample_aspect: float = 1.0
    color_transfer: str | None = None
    pix_fmt: str | None = None

    def _square_pixel_size(self) -> tuple[int, int]:
        width = self.width
        if self.sample_aspect > 0 and abs(self.sample_aspect - 1.0) > 0.01:
            width = int(round(self.width * self.sample_aspect))
            width -= width % 2
            width = max(2, width)
        return width, self.height

    @property
    def anamorphic(self) -> bool:
        return self.sample_aspect > 0 and abs(self.sample_aspect - 1.0) > 0.01

    @property
    def hdr(self) -> bool:
        return (self.color_transfer or "").strip().lower() in HDR_TRANSFERS

    @property
    def display_width(self) -> int:
        width, height = self._square_pixel_size()
        if abs(self.rotation) % 180 == 90:
            return height
        return width

    @property
    def display_height(self) -> int:
        width, height = self._square_pixel_size()
        if abs(self.rotation) % 180 == 90:
            return width
        return height


def parse_frame_rate(value: str | None) -> float | None:
    if not value or value in {"0/0", "N/A"}:
        return None
    try:
        if "/" in value:
            numerator, denominator = value.split("/", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return None
            rate = float(numerator) / denominator_value
        else:
            rate = float(value)
    except ValueError:
        return None
    if rate <= 0 or rate > 1000:
        return None
    return rate


def fit_within_1080p(width: int, height: int) -> tuple[int, int]:
    """Even dimensions that fit in 1920×1080, or the inputs when they already do."""
    if width <= 0 or height <= 0:
        return width, height
    long_edge = max(width, height)
    short_edge = min(width, height)
    scale = min(1.0, MAX_LONG_EDGE / long_edge, MAX_SHORT_EDGE / short_edge)
    if scale >= 1.0:
        return width, height

    def even_within(value: float, cap: int) -> int:
        rounded = int(round(value))
        if rounded % 2:
            rounded -= 1
        while rounded > cap:
            rounded -= 2
        return max(2, rounded)

    long_cap = MAX_LONG_EDGE
    short_cap = MAX_SHORT_EDGE
    if width >= height:
        return even_within(width * scale, long_cap), even_within(height * scale, short_cap)
    return even_within(width * scale, short_cap), even_within(height * scale, long_cap)


def output_rate_token(fps: float) -> str:
    """Encoder `-r` value. 23.976, 29.97 and 59.94 stay on their NTSC clocks."""
    for nominal, token in NTSC_RATES:
        if abs(fps - nominal) <= FPS_EPSILON:
            return token
    rounded = max(1, min(MAX_OUTPUT_FPS, int(round(fps))))
    return str(rounded)


def exceeds_output_limits(width: int, height: int, fps: float | None) -> bool:
    long_edge = max(width, height)
    short_edge = min(width, height)
    if long_edge > MAX_LONG_EDGE or short_edge > MAX_SHORT_EDGE:
        return True
    return fps is not None and fps > MAX_OUTPUT_FPS + FPS_EPSILON


def _rotation_degrees(stream: dict) -> int:
    # Same rotate-tag / display-matrix rule as ffprobe_video_size. The 1080p
    # cap still squares sample aspect ratio after this; crop sizes do not.
    from .ffmpeg import stream_rotation_degrees

    return stream_rotation_degrees(stream)


def _sample_aspect(value: str | None) -> float:
    if not value or value in {"N/A", "0:1", "0:0"}:
        return 1.0
    try:
        if ":" in value:
            numerator, denominator = value.split(":", 1)
            denominator_value = float(denominator)
            if denominator_value == 0:
                return 1.0
            ratio = float(numerator) / denominator_value
        else:
            ratio = float(value)
    except (TypeError, ValueError):
        return 1.0
    if ratio <= 0 or ratio > 20:
        return 1.0
    return ratio


def probe_video_geometry(path: Path) -> VideoGeometry | None:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries",
                "stream=width,height,avg_frame_rate,r_frame_rate,pix_fmt,color_transfer,sample_aspect_ratio:stream_tags=rotate:stream_side_data=rotation",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        streams = json.loads(getattr(result, "stdout", "") or "{}").get("streams") or []
    except (OSError, subprocess.TimeoutExpired, ValueError, TypeError, AttributeError):
        return None
    if not streams:
        return None
    stream = streams[0]
    try:
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    # Prefer the average. Phone VFR often advertises a high r_frame_rate
    # (120/1, or a 90k timebase) while the pictures are ~30 fps. A rate above
    # 1000 is a timebase, not a frame rate, and parse_frame_rate drops it.
    fps = parse_frame_rate(stream.get("avg_frame_rate")) or parse_frame_rate(
        stream.get("r_frame_rate")
    )
    transfer = stream.get("color_transfer")
    pix_fmt = stream.get("pix_fmt")
    return VideoGeometry(
        width,
        height,
        fps,
        _rotation_degrees(stream),
        _sample_aspect(stream.get("sample_aspect_ratio")),
        transfer if isinstance(transfer, str) else None,
        pix_fmt if isinstance(pix_fmt, str) else None,
    )


def source_within_output_limits(path: Path) -> bool:
    """True when the file is already at most 1080p/60, or when it cannot be probed.

    An unreadable file keeps the caller's existing failure path instead of
    being treated as an oversized source.
    """
    geometry = probe_video_geometry(path)
    if geometry is None:
        return True
    return not exceeds_output_limits(
        geometry.display_width, geometry.display_height, geometry.fps
    )


def _has_audio(path: Path) -> bool:
    try:
        result = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "a:0",
                "-show_entries", "stream=codec_type",
                "-of", "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0 and "audio" in result.stdout


def _video_filters(
    *,
    scale: tuple[int, int] | None,
    cap_frame_rate: bool,
    square_pixels: bool,
    tonemap: bool,
) -> list[str]:
    """Filter chain for one normalization attempt.

    Square the pixels before fitting so anamorphic SAR is not stretched.
    Scale before tone-mapping so a 4K HDR master is not graded at full size.
    """
    filters: list[str] = []
    if square_pixels:
        filters.append("scale=trunc(iw*sar/2)*2:ih,setsar=1")
    if scale is not None:
        width, height = scale
        filters.append(f"scale={width}:{height}:flags=lanczos,setsar=1")
    if tonemap:
        filters.append(
            "zscale=t=linear:npl=100,format=gbrpf32le,"
            "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
            "zscale=t=bt709:m=bt709:r=tv,format=yuv420p"
        )
    if cap_frame_rate:
        filters.append(f"fps={MAX_OUTPUT_FPS}")
    return filters


def build_normalization_command(
    source: Path,
    destination: Path,
    *,
    scale: tuple[int, int] | None,
    cap_frame_rate: bool,
    has_audio: bool,
    copy_audio: bool,
    square_pixels: bool = False,
    tonemap: bool = False,
) -> list[str]:
    """ffmpeg command that writes a capped H.264 file. `scale` is the fitted size.

    The file is a local intermediate. `+faststart` is left to the clip encode
    so this pass does not rewrite a multi-gigabyte master a second time.
    """
    filters = _video_filters(
        scale=scale,
        cap_frame_rate=cap_frame_rate,
        square_pixels=square_pixels,
        tonemap=tonemap,
    )
    command = ["ffmpeg", "-y", "-i", str(source), "-map", "0:v:0"]
    if has_audio:
        command += ["-map", "0:a?"]
    if filters:
        command += ["-vf", ",".join(filters)]
    command += [
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "18",
        "-pix_fmt", "yuv420p",
    ]
    if tonemap:
        command += [
            "-colorspace", "bt709",
            "-color_primaries", "bt709",
            "-color_trc", "bt709",
        ]
    if has_audio and copy_audio:
        command += ["-c:a", "copy"]
    elif has_audio:
        command += ["-c:a", "aac", "-b:a", "192k", "-ar", "48000"]
    else:
        command += ["-an"]
    # Force the mp4 muxer. The temp name ends in `.mp4` and then replaces a
    # source that may be `.mov` or `.webm`; the extension must not pick it.
    command += ["-f", "mp4", str(destination)]
    return command


def _run_ffmpeg(command: list[str]) -> bool:
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=10800,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        logger.warning("Source normalization failed to run: %s", exc)
        return False
    if result.returncode != 0:
        logger.warning("Source normalization ffmpeg failed: %s", result.stderr[-500:])
        return False
    return True


def _replace_source(temporary: Path, path: Path) -> None:
    """Publish a finished temp file without leaving a partial source behind.

    `os.replace` is atomic on the same filesystem. fsync first so a crash
    after the rename cannot expose a file ffmpeg had not flushed.
    """
    with temporary.open("rb+") as handle:
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def _enough_disk(path: Path) -> None:
    """Need a second copy while the original stays until the rename succeeds."""
    try:
        free = shutil.disk_usage(path.parent).free
        needed = path.stat().st_size + 32 * 1024 * 1024
    except OSError:
        return
    if free < needed:
        raise RuntimeError(
            "Not enough free disk space to prepare this video. "
            "Sources above 1080p or 60 fps are scaled down before processing."
        )


def _publish_capped_copy(path: Path) -> None:
    """Keep an S3 copy of the capped master so the next job does not transcode again."""
    try:
        from ..media_storage import MediaStorage

        MediaStorage().store_capped_source(path)
    except Exception:
        logger.warning("Capped source was kept locally; the next download will prepare it again", exc_info=True)


def _encode_attempt(
    path: Path,
    temporary: Path,
    *,
    scale: tuple[int, int] | None,
    cap_frame_rate: bool,
    has_audio: bool,
    copy_audio: bool,
    square_pixels: bool,
    tonemap: bool,
) -> bool:
    temporary.unlink(missing_ok=True)
    wrote = _run_ffmpeg(build_normalization_command(
        path, temporary,
        scale=scale,
        cap_frame_rate=cap_frame_rate,
        has_audio=has_audio,
        copy_audio=copy_audio,
        square_pixels=square_pixels,
        tonemap=tonemap,
    ))
    return wrote and temporary.exists() and temporary.stat().st_size > 0


def _normalize_locked(path: Path) -> Path:
    geometry = probe_video_geometry(path)
    if geometry is None:
        logger.warning("Could not read video geometry for %s; leaving it unchanged", path.name)
        return path
    display_width = geometry.display_width
    display_height = geometry.display_height
    if not exceeds_output_limits(display_width, display_height, geometry.fps):
        return path

    fitted = fit_within_1080p(display_width, display_height)
    needs_scale = fitted != (display_width, display_height)
    needs_fps = geometry.fps is not None and geometry.fps > MAX_OUTPUT_FPS + FPS_EPSILON
    has_audio = _has_audio(path)
    _enough_disk(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.capped.mp4")
    logger.info(
        "Normalizing source %s from %sx%s%s to %sx%s%s",
        path.name,
        display_width,
        display_height,
        f" at {geometry.fps:.2f} fps" if geometry.fps else "",
        fitted[0],
        fitted[1],
        f" at {MAX_OUTPUT_FPS} fps" if needs_fps else "",
    )
    scale = fitted if needs_scale else None
    common = {
        "scale": scale,
        "cap_frame_rate": needs_fps,
        "has_audio": has_audio,
        "square_pixels": geometry.anamorphic,
    }
    try:
        # Tone-map HDR first. A missing zscale filter fails immediately, and
        # the plain 8-bit attempt still produces a usable master.
        wrote = False
        if geometry.hdr:
            wrote = _encode_attempt(path, temporary, copy_audio=True, tonemap=True, **common)
            if not wrote and has_audio:
                wrote = _encode_attempt(path, temporary, copy_audio=False, tonemap=True, **common)
        if not wrote:
            wrote = _encode_attempt(path, temporary, copy_audio=True, tonemap=False, **common)
        if not wrote and has_audio:
            # Audio copy fails for codecs the mp4 muxer cannot store (PCM, Opus).
            wrote = _encode_attempt(path, temporary, copy_audio=False, tonemap=False, **common)
        if not wrote:
            raise RuntimeError(
                "Could not prepare this video for processing. "
                "Sources above 1080p or 60 fps are scaled down automatically, "
                "and that step failed."
            )
        _replace_source(temporary, path)
        _publish_capped_copy(path)
        return path
    finally:
        temporary.unlink(missing_ok=True)


def normalize_source_media(path: Path) -> Path:
    """Scale a local working file down to at most 1080p and 60 fps.

    Files that are already within the cap, or that cannot be probed, are
    returned unchanged. A failed transcode of an oversized file raises so the
    pipeline does not continue with a source above the cap.
    """
    path = Path(path)
    if not path.is_file():
        return path
    # Leave the lock inode in place. Unlinking it while another worker is
    # waiting lets that worker lock a new file and run alongside us.
    lock_path = path.with_name(f".{path.name}.normalize.lock")
    with lock_path.open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            return _normalize_locked(path)
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)
