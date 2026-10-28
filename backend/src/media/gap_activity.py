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


def _merge_active_windows(windows: Sequence[Tuple[float, float]], start: float, end: float) -> List[Tuple[float, float]]:
    result: List[Tuple[float, float]] = []
    for a, b in sorted(windows):
        a, b = max(start, a - .2), min(end, b + .2)
        if b <= a:
            continue
        if result and a <= result[-1][1] + .35:
            result[-1] = (result[-1][0], max(b, result[-1][1]))
        else:
            result.append((a, b))
    return result


def _local_motion(first: np.ndarray, second: np.ndarray) -> float:
    """Measure changing content after removing a confident global camera translation."""
    import cv2
    first, second = first.astype(np.float32), second.astype(np.float32)
    shift, response = cv2.phaseCorrelate(first, second)
    if response > .25 and .3 < np.hypot(*shift) < min(first.shape) / 3:
        dx, dy = shift
        aligned = cv2.warpAffine(second, np.array([[1, 0, -dx], [0, 1, -dy]], dtype=np.float32), (first.shape[1], first.shape[0]))
        border = int(np.ceil(max(abs(dx), abs(dy)))) + 1
        first, second = first[border:-border, border:-border], aligned[border:-border, border:-border]
    return float(np.abs(first - second).mean()) if first.size else 0.0


def active_gap_ranges(video_path: Path, gaps: Sequence[Tuple[float, float]], speech: Sequence[Tuple[float, float]]) -> List[List[Tuple[float, float]]]:
    """Keep action windows, allowing quiet filler on either side to be compressed.

    Audio pulses and local motion protect their own time ranges. Uniform camera
    movement alone is not enough to preserve an entire long pause. Decode failures
    preserve the uncertain gap, rather than silently removing possible action.
    """
    video_path = Path(video_path)
    if not gaps or not video_path.exists():
        return [[] for _ in gaps]
    levels, origin, speech_db = np.zeros(0), 0.0, None
    try:
        stream = select_audio_stream_index(video_path)
        if stream is not None:
            origin = min(a for a, _ in [*gaps, *speech])
            span_end = max(b for _, b in [*gaps, *speech])
            if span_end - origin <= 120:
                levels = _window_levels_db(video_path, stream, origin, span_end)
                speech_db = _speech_level_db(levels, origin, speech)
    except Exception as exc:
        logger.warning("Action audio unavailable for %s: %s", video_path, exc)
    protected = []
    for start, end in gaps:
        if end - start > 120:
            protected.append([(start, end)])
            continue
        windows = []
        if speech_db is not None:
            threshold = max(GAP_LOUD_FLOOR_DB, speech_db - GAP_LOUD_RELATIVE_DB)
            first, last = max(0, int((start - origin) / _AUDIO_WINDOW_SECONDS)), min(len(levels), int((end - origin) / _AUDIO_WINDOW_SECONDS))
            for index in range(first, last):
                if levels[index] >= threshold:
                    a = origin + index * _AUDIO_WINDOW_SECONDS
                    windows.append((a, a + _AUDIO_WINDOW_SECONDS))
        try:
            width, height = GAP_ACTIVITY_FRAME_SIZE
            fps = min(GAP_ACTIVITY_SAMPLE_FPS, 240 / max(.01, end - start))
            raw = _decode(["ffmpeg", "-v", "error", "-ss", f"{start:.6f}", "-i", str(video_path), "-t", f"{end-start:.6f}", "-an", "-vf", f"fps={fps},scale={width}:{height},format=gray", "-f", "rawvideo", "-"])
            data = np.frombuffer(raw, dtype=np.uint8)
            frames = data[:len(data) // (width * height) * (width * height)].reshape(-1, height, width)
            if len(frames) < 2:
                raise ValueError("Insufficient frames to assess action")
            for index in range(1, len(frames)):
                if _local_motion(frames[index - 1], frames[index]) >= GAP_MOTION_THRESHOLD:
                    windows.append((start + (index - 1) / fps, start + index / fps))
            protected.append(_merge_active_windows(windows, start, end))
        except Exception as exc:
            logger.warning("Action windows uncertain for %s; preserving gap: %s", video_path, exc)
            protected.append([(start, end)])
    return protected
