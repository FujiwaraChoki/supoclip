"""Face track and person matte for dynamic edits (FINAL §6.2, §6.4).

Everything here measures the *picture the viewer sees*: generation decodes the
source once through the render's own framing head (no B-roll, no subtitles), so
the boxes and the matte are in output space for every layout (crop, stack,
split, reaction...) without inverting any filter graph.

- **Face track:** YuNet with landmarks on every analysis frame (6 fps), at most
  two faces, largest first, smoothed by a one-pole EMA. Rows are normalised to
  the analysed picture (``FaceTrack`` space ``"picture"``, S == E time).
- **Person matte:** PP-HumanSeg (``cv2.dnn``, bundled ONNX) only on frames
  inside the "behind the head" windows (+0.2 s), upsampled to the analysis size,
  smoothed in time, interpolated to 15 fps, eroded 1 px and feathered. Outside
  the windows the matte is 255 (AR-B1: P2 never reads it there, and an opaque
  value can never punch a hole in the picture). A window with too little person
  in it reports ``person=False`` and its text is drawn in front.

Decorative: nothing here may cost the clip. A missing model, a failed decode or
a failed write degrades to "no face" / ``person=False`` and is logged.
Runs on the video worker only (never inside an API request), in the render's own
thread; per-render files go in the render's scratch directory.
"""

from __future__ import annotations

import math
import os
import re
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import Iterator, Optional, Sequence

import cv2
import numpy as np

from ..edit_fx.contracts import FaceTrack, MatteWindow
from .common import FACE_PRESENCE_MIN_AREA, logger
from .face_detection import YuNetFaceDetector, open_yunet_detector
from .source_limits import probe_video_geometry

PPHUMANSEG_MODEL_PATH = Path(__file__).parent / "models" / "human_segmentation_pphumanseg_2023mar.onnx"

ANALYSIS_FPS = 6.0
# Short side of the analysed picture: 1080x1920 output → 540x960.
ANALYSIS_SHORT_SIDE = 540
MATTE_FPS = 15
# Extra analysis around each behind window: the EMA warms up before the text
# appears and interpolation has a sample on both sides of every matte frame.
MATTE_PAD_SECONDS = 0.2
# Weight of the newest sample in the one-pole EMAs.
FACE_EMA = 0.5
MATTE_EMA = 0.6
# A window whose matte averages below this (0-1) has no person to cut out.
MATTE_PERSON_MIN = 0.05
MATTE_FEATHER_PX = 5
MATTE_ERODE_PX = 1
# Probability band mapped (smoothstep) onto the matte's 0-255 edge ramp.
MATTE_EDGE = (0.3, 0.7)
MAX_FACES = 2
SEGMENTER_INPUT = 192
TRACK_DECIMALS = 4

_FACE_COLUMNS = 15
_INPUT_REF = re.compile(r"\[0(?::v(?::0)?)?\]")
_OUT_LABEL = "[fxan_out]"
_STDERR_TAIL = 800

MATTE_FFV1_ARGS = ("-c:v", "ffv1", "-pix_fmt", "gray")
# The studio matte is played by browsers too: plain 4:2:0 H.264 with a grey picture.
MATTE_H264_ARGS = (
    "-vf", "scale=out_range=tv,format=yuv420p",
    "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-g", str(MATTE_FPS),
    "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
    "-color_trc", "bt709", "-color_range", "tv", "-movflags", "+faststart",
)


# ----------------------------------------------------------------------------- geometry and graphs

def analysis_size(width: int, height: int) -> tuple[int, int]:
    """Size the picture is analysed at: short side 540 (never upscaled), even dimensions."""
    width, height = max(2, int(width)), max(2, int(height))
    factor = min(1.0, ANALYSIS_SHORT_SIDE / float(min(width, height)))

    def even(value: float) -> int:
        return max(2, int(round(value * factor / 2.0)) * 2)

    return even(width), even(height)


def analysis_graph(head: str, head_label: str, width: int, height: int) -> str:
    """The filter graph decoding the framed picture at 6 fps, ``width``x``height`` bgr24, ending in ``[fxan_out]``.

    ``fps=6`` goes *before* the head (AR-M8), so the framing (crops, scales,
    stacks) runs on 6 frames a second instead of 30. Tracked crops use ``t``
    expressions and stay correct. A head with ``zoompan`` (which re-times by
    frame number) keeps its input rate and is sampled after instead.
    """
    rate = f"fps={ANALYSIS_FPS:g}"
    finish = f"scale={width}:{height}:flags=bilinear,setsar=1,format=bgr24{_OUT_LABEL}"
    refs = _INPUT_REF.findall(head)
    stripped = head.lstrip()
    if "zoompan" in head or (not refs and stripped.startswith("[")):
        return f"{head};{head_label}{rate},{finish}"
    if not refs:
        # A leading unlabelled chain reads input 0: give it the sampled stream.
        return f"[0:v]{rate}[fxan_in0];[fxan_in0]{stripped};{head_label}{finish}"
    labels = [f"[fxan_in{index}]" for index in range(len(refs))]
    source = f"[0:v]{rate}{labels[0]}" if len(labels) == 1 else f"[0:v]{rate},split={len(labels)}{''.join(labels)}"
    pending = iter(labels)
    rewritten = _INPUT_REF.sub(lambda _match: next(pending), head)
    return f"{source};{rewritten};{head_label}{finish}"


def _analysis_command(source: Path, graph: str) -> list[str]:
    return [
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(source),
        "-filter_complex", graph, "-map", _OUT_LABEL, "-an", "-sn", "-dn",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]


def _video_command(video: Path, fps: float, width: int, height: int) -> list[str]:
    return [
        "ffmpeg", "-v", "error", "-nostdin", "-i", str(video), "-an", "-sn", "-dn",
        "-vf", f"fps={fps:g},scale={width}:{height}:flags=bilinear,setsar=1,format=bgr24",
        "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1",
    ]


# ----------------------------------------------------------------------------- process runners (mocked in tests)

def _iter_frames(command: Sequence[str], width: int, height: int) -> Iterator[np.ndarray]:
    """Run one ffmpeg decode and yield its bgr24 frames as ``(height, width, 3)`` arrays.

    The one place analysis decodes video. Raises ``RuntimeError`` when ffmpeg
    fails before producing a frame.
    """
    frame_size = width * height * 3
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(list(command), stdout=subprocess.PIPE, stderr=stderr, stdin=subprocess.DEVNULL)
        count = 0
        try:
            assert process.stdout is not None
            while True:
                data = process.stdout.read(frame_size)
                if len(data) < frame_size:
                    break
                count += 1
                yield np.frombuffer(data, dtype=np.uint8).reshape(height, width, 3)
            code = process.wait()
            if code != 0 and count == 0:
                stderr.seek(0)
                tail = stderr.read().decode("utf-8", "replace")[-_STDERR_TAIL:]
                raise RuntimeError(f"analysis decode failed (exit {code}): {tail}")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            if process.stdout is not None:
                process.stdout.close()


def _write_matte(dest: Path, frames: Iterator[np.ndarray], *, width: int, height: int, fps: int,
                 codec_args: Sequence[str]) -> None:
    """Encode grey ``(height, width)`` uint8 frames to ``dest``; written to a partial file and moved into place."""
    partial = dest.with_name(f".{dest.stem}.partial{dest.suffix}")
    command = [
        "ffmpeg", "-v", "error", "-nostdin", "-y",
        "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{width}x{height}", "-r", str(fps), "-i", "pipe:0",
        *codec_args, str(partial),
    ]
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr)
        try:
            assert process.stdin is not None
            for frame in frames:
                process.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
            process.stdin.close()
            code = process.wait()
        except BaseException:
            if process.poll() is None:
                process.kill()
            process.wait()
            partial.unlink(missing_ok=True)
            raise
        if code != 0:
            stderr.seek(0)
            tail = stderr.read().decode("utf-8", "replace")[-_STDERR_TAIL:]
            partial.unlink(missing_ok=True)
            raise RuntimeError(f"matte encode failed (exit {code}): {tail}")
    os.replace(partial, dest)


# ----------------------------------------------------------------------------- models

_local = threading.local()


class PersonSegmenter:
    """PP-HumanSeg through ``cv2.dnn``: person probability of a BGR frame at 192x192."""

    def __init__(self, model_path: Path | None = None):
        model_path = Path(model_path or PPHUMANSEG_MODEL_PATH)
        if not model_path.is_file():
            raise FileNotFoundError(f"PP-HumanSeg model not found: {model_path}")
        self._net = cv2.dnn.readNetFromONNX(str(model_path))

    def probability(self, frame_bgr: np.ndarray) -> np.ndarray:
        image = cv2.resize(frame_bgr, (SEGMENTER_INPUT, SEGMENTER_INPUT), interpolation=cv2.INTER_AREA)
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB).astype(np.float32)
        image = (image / 255.0 - 0.5) / 0.5
        self._net.setInput(cv2.dnn.blobFromImage(image))
        output = self._net.forward()
        # Softmax over (background, person).
        return np.clip(np.asarray(output[0, 1], dtype=np.float32), 0.0, 1.0)


def load_segmenter(model_path: Path | None = None) -> Optional[PersonSegmenter]:
    """This thread's segmenter (a ``cv2.dnn.Net`` is not thread-safe), or None when the model cannot load."""
    model_path = Path(model_path or PPHUMANSEG_MODEL_PATH)
    cache = getattr(_local, "segmenters", None)
    if cache is None:
        cache = _local.segmenters = {}
    key = str(model_path)
    if key not in cache:
        try:
            cache[key] = PersonSegmenter(model_path)
        except Exception as exc:  # cv2.error, OSError: never fatal
            logger.warning("fx_matte_model_unavailable error=%s", exc)
            return None
    return cache[key]


# ----------------------------------------------------------------------------- face track

class _FaceSmoother:
    """One-pole EMA per face, matched frame to frame by centre distance; at most ``MAX_FACES``, largest first."""

    def __init__(self, alpha: float = FACE_EMA):
        self._alpha = alpha
        self._previous: list[np.ndarray] = []

    def update(self, faces: list[np.ndarray]) -> list[np.ndarray]:
        unmatched = list(range(len(self._previous)))
        smoothed: list[np.ndarray] = []
        for face in faces:
            cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
            best, best_distance = None, math.inf
            for index in unmatched:
                prev = self._previous[index]
                distance = math.hypot(cx - (prev[0] + prev[2] / 2), cy - (prev[1] + prev[3] / 2))
                if distance < best_distance:
                    best, best_distance = index, distance
            if best is not None and best_distance <= 0.75 * max(face[2], self._previous[best][2]):
                unmatched.remove(best)
                face = self._alpha * face + (1.0 - self._alpha) * self._previous[best]
            smoothed.append(face)
        smoothed.sort(key=lambda row: float(row[2] * row[3]), reverse=True)
        self._previous = smoothed
        return smoothed


def _frame_faces(detector: YuNetFaceDetector, frame: np.ndarray) -> list[np.ndarray]:
    """Normalised ``[x,y,w,h,score, 10 landmarks]`` rows, largest first, tiny boxes dropped."""
    height, width = frame.shape[:2]
    scale = np.array([width, height, width, height, 1.0] + [width, height] * 5, dtype=np.float64)
    rows = []
    for face in detector.detect(frame, landmarks=True):
        if len(face) < _FACE_COLUMNS:
            continue
        row = np.asarray(face[:_FACE_COLUMNS], dtype=np.float64) / scale
        if row[2] * row[3] >= FACE_PRESENCE_MIN_AREA:
            rows.append(row)
    rows.sort(key=lambda row: float(row[2] * row[3]), reverse=True)
    return rows[:MAX_FACES]


def _track_payload(samples: list[tuple[float, list[np.ndarray]]], fps: float) -> dict:
    track = FaceTrack(
        space="picture",
        fps=fps,
        samples=tuple(
            (round(t, TRACK_DECIMALS), tuple(tuple(round(float(v), TRACK_DECIMALS) for v in face) for face in faces))
            for t, faces in samples
        ),
    )
    return track.model_dump(mode="json")


# ----------------------------------------------------------------------------- matte

class _WindowSamples:
    """Segmentation samples (192x192) inside one merged, padded analysis interval.

    ``add`` collects raw probabilities; ``smooth`` then runs the 0.6 one-pole
    EMA forward and backward and averages the two passes. At 6 fps a causal EMA
    alone lags the person by ~0.1 s (three output frames), which shows as text
    over the leading edge of a moving head and a person-shaped hole trailing it;
    the two-way pass smooths as much with no lag.
    """

    def __init__(self, start: float, end: float):
        self.start, self.end = start, end
        self.times: list[float] = []
        self.probs: list[np.ndarray] = []

    def add(self, t: float, prob: np.ndarray) -> None:
        self.times.append(t)
        self.probs.append(np.asarray(prob, dtype=np.float32))

    def smooth(self) -> None:
        count = len(self.probs)
        if count < 2:
            return
        forward = list(self.probs)
        for index in range(1, count):
            forward[index] = MATTE_EMA * self.probs[index] + (1.0 - MATTE_EMA) * forward[index - 1]
        backward = list(self.probs)
        for index in range(count - 2, -1, -1):
            backward[index] = MATTE_EMA * self.probs[index] + (1.0 - MATTE_EMA) * backward[index + 1]
        self.probs = [0.5 * (a + b) for a, b in zip(forward, backward)]

    def at(self, t: float) -> Optional[np.ndarray]:
        if not self.times:
            return None
        index = int(np.searchsorted(self.times, t, side="right")) - 1
        if index < 0:
            return self.probs[0]
        if index >= len(self.times) - 1:
            return self.probs[-1]
        t0, t1 = self.times[index], self.times[index + 1]
        k = 0.0 if t1 <= t0 else (t - t0) / (t1 - t0)
        return (1.0 - k) * self.probs[index] + k * self.probs[index + 1]


def _merge(intervals: list[tuple[float, float]]) -> list[tuple[float, float]]:
    merged: list[tuple[float, float]] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def _finish_matte(prob: np.ndarray, width: int, height: int) -> np.ndarray:
    """192x192 person probability → feathered uint8 matte at ``width``x``height``."""
    matte = cv2.resize(prob, (width, height), interpolation=cv2.INTER_LINEAR)
    # The 192 px model upsampled ~3-5x leaves a wide grey ramp at the edge:
    # tighten it around p = 0.5 so the cut-out follows the outline, not a halo.
    low, high = MATTE_EDGE
    matte = np.clip((matte - low) / (high - low), 0.0, 1.0)
    matte = matte * matte * (3.0 - 2.0 * matte)
    if MATTE_ERODE_PX > 0:
        size = 2 * MATTE_ERODE_PX + 1
        matte = cv2.erode(matte, np.ones((size, size), np.uint8))
    if MATTE_FEATHER_PX > 0:
        matte = cv2.GaussianBlur(matte, (MATTE_FEATHER_PX, MATTE_FEATHER_PX), 0)
    return np.clip(np.rint(matte * 255.0), 0, 255).astype(np.uint8)


def _in_window(t: float, windows: Sequence[tuple[float, float]]) -> bool:
    """Matte frames within half a frame of a window get the real matte, so the window's edges are covered."""
    half = 0.5 / MATTE_FPS
    return any(start - half <= t <= end + half for start, end in windows)


def _matte_at(t: float, groups: Sequence[_WindowSamples], width: int, height: int) -> Optional[np.ndarray]:
    for group in groups:
        if group.start - 1e-6 <= t <= group.end + 1e-6:
            prob = group.at(t)
            return None if prob is None else _finish_matte(prob, width, height)
    return None


def _matte_frame_count(analysis_frames: int) -> int:
    return max(1, math.ceil(analysis_frames * MATTE_FPS / ANALYSIS_FPS - 1e-9))


def _person_flags(windows: Sequence[tuple[float, float]], groups: Sequence[_WindowSamples],
                  width: int, height: int) -> list[bool]:
    flags = []
    for start, end in windows:
        if end <= start:
            flags.append(False)
            continue
        times = [j / MATTE_FPS for j in range(math.ceil(start * MATTE_FPS - 1e-9), math.floor(end * MATTE_FPS + 1e-9) + 1)]
        times = times or [(start + end) / 2.0]
        values = [m.mean() / 255.0 for m in (_matte_at(t, groups, width, height) for t in times) if m is not None]
        flags.append(bool(values) and float(np.mean(values)) >= MATTE_PERSON_MIN)
    return flags


def _windowed_matte_frames(count: int, windows: Sequence[tuple[float, float]], groups: Sequence[_WindowSamples],
                           width: int, height: int) -> Iterator[np.ndarray]:
    opaque = np.full((height, width), 255, dtype=np.uint8)
    for j in range(count):
        t = j / MATTE_FPS
        matte = _matte_at(t, groups, width, height) if _in_window(t, windows) else None
        yield opaque if matte is None else matte


# ----------------------------------------------------------------------------- public API

def analyse(source: Path, head: str, head_label: str, *, out_w: int, out_h: int, face: bool,
            matte_windows: list[tuple[float, float]], matte_dest: Path | None) -> tuple[dict | None, list[MatteWindow]]:
    """Face track and behind-window matte of the framed picture, in ONE decode of ``source`` through ``head``.

    Returns ``(face_track, windows)``: the track (``FaceTrack`` JSON, space
    ``"picture"``, S == E time) when ``face`` and YuNet loaded, else None; one
    ``MatteWindow`` per input window, in order. ``matte_dest`` (gray8 FFV1
    ``.mkv``, 15 fps, full length, 255 outside the windows) is written only when
    at least one window has a person; otherwise it is left absent and the
    caller draws every behind text in front.
    """
    windows = [(float(start), float(end)) for start, end in matte_windows]
    no_person = [MatteWindow(start=start, end=max(start, end), person=False) for start, end in windows]
    if matte_dest is not None:
        Path(matte_dest).unlink(missing_ok=True)
    live = [(start, end) for start, end in windows if end > start]
    if not face and not live:
        return None, no_person

    width, height = analysis_size(out_w, out_h)
    detector = open_yunet_detector() if face else None
    segmenter = load_segmenter() if live else None
    if detector is None and segmenter is None:
        return None, no_person

    groups = [
        _WindowSamples(start, end)
        for start, end in _merge([(max(0.0, start - MATTE_PAD_SECONDS), end + MATTE_PAD_SECONDS) for start, end in live])
    ] if segmenter is not None else []
    smoother = _FaceSmoother()
    samples: list[tuple[float, list[np.ndarray]]] = []
    frames = 0
    command = _analysis_command(Path(source), analysis_graph(head, head_label, width, height))
    try:
        for frame in _iter_frames(command, width, height):
            t = frames / ANALYSIS_FPS
            frames += 1
            # A model failing on a frame costs only its own output: the other
            # one keeps going on the same decode.
            if detector is not None:
                try:
                    samples.append((t, smoother.update(_frame_faces(detector, frame))))
                except Exception as exc:  # cv2.error: never fatal
                    logger.warning("fx_face_track_failed source=%s t=%.2f error=%s", source, t, exc)
                    detector, samples = None, []
            if segmenter is not None:
                for group in groups:
                    if group.start - 1e-6 <= t <= group.end + 1e-6:
                        try:
                            group.add(t, segmenter.probability(frame))
                        except Exception as exc:  # cv2.error: never fatal
                            logger.warning("fx_matte_failed source=%s t=%.2f error=%s", source, t, exc)
                            segmenter, groups = None, []
                        break
            if detector is None and segmenter is None:
                break
    except Exception as exc:
        logger.warning("fx_analysis_failed source=%s error=%s", source, exc)
        return None, no_person

    track = None
    if detector is not None and frames:
        try:
            track = _track_payload(samples, ANALYSIS_FPS)
        except Exception as exc:  # a row the contract rejects: never fatal
            logger.warning("fx_face_track_invalid source=%s error=%s", source, exc)
    if segmenter is None or not frames:
        return track, no_person

    for group in groups:
        group.smooth()
    flags = iter(_person_flags(live, groups, width, height))
    result = [
        MatteWindow(start=start, end=max(start, end), person=next(flags) if end > start else False)
        for start, end in windows
    ]
    if matte_dest is not None and any(window.person for window in result):
        try:
            _write_matte(
                Path(matte_dest),
                _windowed_matte_frames(_matte_frame_count(frames), live, groups, width, height),
                width=width, height=height, fps=MATTE_FPS, codec_args=MATTE_FFV1_ARGS,
            )
        except Exception as exc:
            logger.warning("fx_matte_write_failed dest=%s error=%s", matte_dest, exc)
            return track, no_person
    return track, result


def face_track_for_video(video: Path, *, fps: float = 6.0) -> dict | None:
    """Face track of a whole video (the studio's base video, S time), space ``"picture"``; None on failure."""
    geometry = probe_video_geometry(Path(video))
    if geometry is None:
        logger.warning("fx_face_track_unprobeable video=%s", video)
        return None
    detector = open_yunet_detector()
    if detector is None:
        return None
    display_width = getattr(geometry, "display_width", geometry.width)
    display_height = getattr(geometry, "display_height", geometry.height)
    width, height = analysis_size(display_width, display_height)
    smoother = _FaceSmoother()
    samples: list[tuple[float, list[np.ndarray]]] = []
    try:
        for index, frame in enumerate(_iter_frames(_video_command(Path(video), fps, width, height), width, height)):
            samples.append((index / fps, smoother.update(_frame_faces(detector, frame))))
    except Exception as exc:
        logger.warning("fx_face_track_failed video=%s error=%s", video, exc)
        return None
    if not samples:
        return None
    try:
        return _track_payload(samples, fps)
    except Exception as exc:  # e.g. fps outside the contract's (0, 30]
        logger.warning("fx_face_track_invalid video=%s error=%s", video, exc)
        return None


def matte_for_video(video: Path, dest: Path) -> bool:
    """Full-length person matte of a whole video (the studio's ``matte.mp4``): grey H.264 CRF 18, 15 fps, S time.

    Every frame is segmented (the studio does not know its behind windows ahead
    of time). Returns False, with ``dest`` absent, when the model or the video
    cannot be read.
    """
    dest = Path(dest)
    dest.unlink(missing_ok=True)
    segmenter = load_segmenter()
    if segmenter is None:
        return False
    geometry = probe_video_geometry(Path(video))
    if geometry is None:
        logger.warning("fx_matte_unprobeable video=%s", video)
        return False
    display_width = getattr(geometry, "display_width", geometry.width)
    display_height = getattr(geometry, "display_height", geometry.height)
    width, height = analysis_size(display_width, display_height)

    def mattes() -> Iterator[np.ndarray]:
        previous: Optional[np.ndarray] = None
        for frame in _iter_frames(_video_command(Path(video), MATTE_FPS, width, height), width, height):
            prob = segmenter.probability(frame)
            if previous is not None:
                prob = MATTE_EMA * prob + (1.0 - MATTE_EMA) * previous
            previous = prob
            yield _finish_matte(prob, width, height)
        if previous is None:
            raise RuntimeError("the video has no frames")

    try:
        _write_matte(dest, mattes(), width=width, height=height, fps=MATTE_FPS, codec_args=MATTE_H264_ARGS)
    except Exception as exc:
        logger.warning("fx_matte_failed video=%s error=%s", video, exc)
        dest.unlink(missing_ok=True)
        return False
    return dest.is_file()
