"""Tell dead air apart from silent action before pause cleanup cuts a gap.

Speech timings alone call any stretch without words a pause. In gameplay,
sports or cooking footage that stretch is often the point of the clip
(gunfire, a goal, the pan going on the heat), so a gap is only compressed
when the source is also visually still and quiet. Both checks decode a few
low-resolution frames or a mono 8 kHz track, so they stay cheap.
"""

import subprocess
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

import numpy as np

from .common import logger
from .ffmpeg import select_audio_stream_index

GAP_ACTIVITY_SAMPLE_FPS = 4

GAP_ACTIVITY_FRAME_SIZE = (64, 36)

# Median mean-absolute difference between sampled grey frames (0-255). A
# talking head or a static slate stays under ~5; a moving game camera,
# hands at work or a cut between shots sits well above 15.
GAP_MOTION_THRESHOLD = 10.0

# Sound within this many dB of the clip's own speech level is not silence:
# gunfire, crowd noise, a sizzling pan, or a shout the transcript missed.
GAP_LOUD_RELATIVE_DB = 6.0

GAP_LOUD_FLOOR_DB = -40.0

# Share of 50ms audio windows in the gap that must be that loud.
GAP_LOUD_FRACTION = 0.15

# Word timings blur into the gap edges; judge only its inside.
GAP_EDGE_INSET_SECONDS = 0.15

# The motion check samples at most this much from the middle of a long gap, so
# a minute-long pause in a 4K source never means a minute of decoding.
GAP_MOTION_MAX_SAMPLE_SECONDS = 8.0

_AUDIO_RATE = 8000

_AUDIO_WINDOW_SECONDS = 0.05


def _decode(command: List[str], timeout: int = 120) -> bytes:
    result = subprocess.run(command, capture_output=True, timeout=timeout)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
    return result.stdout


def _window_levels_db(
    video_path: Path, audio_stream: int, start: float, end: float
) -> np.ndarray:
    raw = _decode(
        [
            "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(video_path),
            "-t", f"{max(0.05, end - start):.3f}", "-map", f"0:a:{audio_stream}",
            "-vn", "-ac", "1", "-ar", str(_AUDIO_RATE), "-f", "f32le", "-",
        ]
    )
    samples = np.frombuffer(raw, dtype=np.float32)
    size = int(_AUDIO_RATE * _AUDIO_WINDOW_SECONDS)
    count = len(samples) // size
    if count == 0:
        return np.zeros(0, dtype=np.float32)
    blocks = samples[: count * size].reshape(count, size).astype(np.float64)
    return (20.0 * np.log10(np.sqrt((blocks**2).mean(axis=1)) + 1e-6)).astype(np.float32)


def gap_motion_score(video_path: Path, start: float, end: float) -> float:
    """Median frame-to-frame change across ``start``-``end`` (0 when still)."""
    width, height = GAP_ACTIVITY_FRAME_SIZE
    raw = _decode(
        [
            "ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(video_path),
            "-t", f"{max(0.05, end - start):.3f}", "-an",
            "-vf", f"fps={GAP_ACTIVITY_SAMPLE_FPS},scale={width}:{height},format=gray",
            "-f", "rawvideo", "-",
        ]
    )
    frames = np.frombuffer(raw, dtype=np.uint8)
    count = len(frames) // (width * height)
    if count < 2:
        return 0.0
    frames = frames[: count * width * height].reshape(count, height, width).astype(np.float32)
    changes = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    return float(np.median(changes))


def _speech_level_db(levels: np.ndarray, origin: float, speech: Sequence[Tuple[float, float]]) -> Optional[float]:
    selected = []
    for start, end in speech:
        first = max(0, int((start - origin) / _AUDIO_WINDOW_SECONDS))
        last = min(len(levels), int((end - origin) / _AUDIO_WINDOW_SECONDS))
        if last > first:
            selected.append(levels[first:last])
    if not selected:
        return None
    return float(np.median(np.concatenate(selected)))


def active_gaps(
    video_path: Path,
    gaps: Sequence[Tuple[float, float]],
    speech: Sequence[Tuple[float, float]],
) -> List[bool]:
    """For each silent gap, whether the source shows or sounds like action there.

    ``speech`` is the clip's spoken word spans; their loudness is the reference
    for "loud". Any failure (no file, no audio, decoder error) reports the gap
    as inactive so cleanup behaves as it did before; it never fails the clip.
    """
    try:
        return _active_gaps(Path(video_path), gaps, speech)
    except Exception as exc:
        logger.warning("Gap activity check failed for %s; cutting pauses as before: %s", video_path, exc)
        return [False] * len(gaps)


def _active_gaps(
    video_path: Path,
    gaps: Sequence[Tuple[float, float]],
    speech: Sequence[Tuple[float, float]],
) -> List[bool]:
    flags = [False] * len(gaps)
    if not gaps or not video_path.exists():
        return flags

    inner = [
        (start + GAP_EDGE_INSET_SECONDS, end - GAP_EDGE_INSET_SECONDS)
        for start, end in gaps
    ]

    levels = np.zeros(0, dtype=np.float32)
    origin = 0.0
    speech_db: Optional[float] = None
    try:
        audio_stream = select_audio_stream_index(video_path)
        if audio_stream is not None:
            origin = min([start for start, _ in gaps] + [start for start, _ in speech])
            span_end = max([end for _, end in gaps] + [end for _, end in speech])
            levels = _window_levels_db(video_path, audio_stream, origin, span_end)
            speech_db = _speech_level_db(levels, origin, speech)
    except Exception as exc:
        logger.warning("Gap audio check unavailable for %s: %s", video_path, exc)

    for index, (start, end) in enumerate(inner):
        if end - start < 0.25:
            continue
        if speech_db is not None and len(levels):
            first = max(0, int((start - origin) / _AUDIO_WINDOW_SECONDS))
            last = min(len(levels), int((end - origin) / _AUDIO_WINDOW_SECONDS))
            window = levels[first:last]
            loud_db = max(GAP_LOUD_FLOOR_DB, speech_db - GAP_LOUD_RELATIVE_DB)
            if len(window) and float(np.mean(window >= loud_db)) >= GAP_LOUD_FRACTION:
                flags[index] = True
                continue
        if end - start > GAP_MOTION_MAX_SAMPLE_SECONDS:
            middle = (start + end) / 2.0
            start = middle - GAP_MOTION_MAX_SAMPLE_SECONDS / 2.0
            end = middle + GAP_MOTION_MAX_SAMPLE_SECONDS / 2.0
        try:
            flags[index] = gap_motion_score(video_path, start, end) >= GAP_MOTION_THRESHOLD
        except Exception as exc:
            logger.warning("Gap motion check unavailable for %s: %s", video_path, exc)
    return flags
