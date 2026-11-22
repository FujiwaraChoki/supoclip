"""A clip's first and last frames belong to the clip's own shot.

A clip is cut on speech: it starts a moment before its first word and ends a moment after its last.
When the source changes shot inside that moment, the clip opens (or closes) on a few frames of a
different scene: a flash of the previous shot before the speaker appears, of the next chapter's
title after the last word, or the black between two scenes. Those frames are replaced by a hold of
the nearest frame of the clip's own shot. The audio and the clip's length are untouched, so captions and every stored time stay valid.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from .common import INTERMEDIATE_CRF, logger
from .ffmpeg import run_ffmpeg_command

# A shot that lasts no longer than this at the very edge of a clip is a sliver of a neighbouring shot.
EDGE_SHOT_SECONDS = 0.4
# How much of the clip is looked at beyond that, to tell a single cut from a fast montage.
EDGE_PROBE_SECONDS = 0.8
# Shorter clips are left alone: the edge would be a real part of them.
MIN_CLIP_SECONDS = 3.0
# Mean absolute luma change (0-255) between two frames of different shots.
CUT_MIN_DIFF = 12.0
# ... and how much larger than the motion around it the change must be.
CUT_SPIKE_RATIO = 3.0
# Frames that repeat (30 fps footage in a 60 fps file) differ by less than this and carry no motion.
DUPLICATE_FRAME_DIFF = 1.0
PROBE_W, PROBE_H = 160, 90
# A frame this dark on average (0-255) is black: the tail of a fade, or the gap between two scenes.
BLACK_FRAME_MEAN = 16.0
# A fade from or to black is held over whole, up to this long, so the held frame is the picture at full
# brightness. A longer fade is held over as far as EDGE_SHOT_SECONDS reaches (a dim frame, never a black one).
EDGE_FADE_SECONDS = 0.6
FADE_HALF, FADE_DONE = 0.5, 0.9


def _probe(path: Path) -> Optional[Tuple[int, float]]:
    """(frame count, frames per second) of the video stream."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_packets",
         "-show_entries", "stream=nb_read_packets,avg_frame_rate", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, timeout=120,
    )
    try:
        rate, count = result.stdout.strip().split("\n")[0].split(",")[:2]
        numerator, _, denominator = rate.partition("/")
        fps = float(numerator) / float(denominator or 1)
        return (int(count), fps) if fps > 0 and int(count) > 0 else None
    except (ValueError, IndexError, ZeroDivisionError):
        return None


def _frames(path: Path, seek: list[str], seconds: float) -> np.ndarray:
    """Small grey frames of one end of the clip, every frame, in order."""
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-an", "-sn", *seek, "-i", str(path), "-t", f"{seconds:.3f}",
         "-vf", f"scale={PROBE_W}:{PROBE_H}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=120,
    )
    size = PROBE_W * PROBE_H
    count = len(result.stdout) // size if result.returncode == 0 else 0
    return np.frombuffer(result.stdout[:count * size], dtype=np.uint8).reshape(count, PROBE_H, PROBE_W).astype(np.int16)


def single_cut(frames: np.ndarray) -> Optional[int]:
    """Index of the first frame after the only hard cut in ``frames``, or None.

    A hard cut is one frame-to-frame change that stands far above the motion around it and above every
    other change in the window, between pictures that stay different (a camera flash returns to the
    same picture). Two or more cuts are a montage, which is part of the clip.
    """
    if len(frames) < 4:
        return None
    steps = np.abs(np.diff(frames, axis=0)).mean(axis=(1, 2))
    peak = int(np.argmax(steps))
    others = np.delete(steps, peak)
    moving = others[others > DUPLICATE_FRAME_DIFF]
    level = float(np.median(moving)) if len(moving) else 0.0
    runner_up = float(others.max()) if len(others) else 0.0
    if steps[peak] < CUT_MIN_DIFF or steps[peak] < CUT_SPIKE_RATIO * max(level, 1.0) or steps[peak] < 2 * runner_up:
        return None
    cut = peak + 1
    before, after = frames[:cut].mean(axis=0), frames[cut:].mean(axis=0)
    if float(np.abs(after - before).mean()) < CUT_MIN_DIFF:
        return None
    return cut


def dark_run(frames: np.ndarray, reach: float = FADE_HALF) -> int:
    """How many leading frames are black or still fading up from black, or 0.

    The source faded out of the previous scene, or cut through black, a moment before the first
    word. The run ends where the picture reaches ``reach`` of the brightness it settles at (half by
    default; ``FADE_DONE`` for the whole fade). A scene that is dark throughout is not a fade.
    """
    if len(frames) < 4:
        return 0
    means = frames.reshape(len(frames), -1).mean(axis=1)
    if means[0] > BLACK_FRAME_MEAN:
        return 0
    settled = float(np.median(means[len(means) // 2:]))
    # The picture has to stand clear of the black: three times as bright as the first frame, and not black itself.
    if settled < max(1.5 * BLACK_FRAME_MEAN, 3 * float(means[0])):
        return 0
    run = 0
    while run < len(means) and means[run] < reach * settled:
        run += 1
    return run


def dark_edge(frames: np.ndarray, limit: int, fade_limit: int) -> int:
    """Frames to hold over at an edge that is black or runs through black, or 0.

    The edge starts on black: the whole fade is held over when it is short, else its dark half. Or the
    neighbouring scene is still fading when the clip starts and black follows within the sliver (a dip
    to black): then everything up to the clip's own picture is held over, provided that picture is a
    different one (a strobe inside one scene returns to the same picture and is left alone).
    """
    if len(frames) < 4:
        return 0
    means = frames.reshape(len(frames), -1).mean(axis=1)
    start = 0
    if means[0] > BLACK_FRAME_MEAN:
        black = np.nonzero(means[:limit] <= BLACK_FRAME_MEAN)[0]
        if not len(black):
            return 0
        start = int(black[0])
    for reach, most in ((FADE_DONE, fade_limit), (FADE_HALF, limit)):
        run = dark_run(frames[start:], reach)
        if not 0 < run or start + run > most or start + run >= len(frames):
            continue
        if start and float(np.abs(frames[start + run] - frames[0]).mean()) < CUT_MIN_DIFF:
            return 0
        return start + run
    return 0


def stray_edge_frames(path: Path) -> Tuple[int, int, int]:
    """(stray frames at the start, stray frames at the end, total frames) of a rendered source cut."""
    probed = _probe(path)
    if probed is None:
        return 0, 0, 0
    total, fps = probed
    if total / fps < MIN_CLIP_SECONDS:
        return 0, 0, total
    limit = max(1, int(round(EDGE_SHOT_SECONDS * fps)))
    fade_limit = max(limit, int(round(EDGE_FADE_SECONDS * fps)))
    window = max(4, int(round(EDGE_PROBE_SECONDS * fps)))
    # A fade is measured against the picture after it, so the look at black reaches past the longest fade.
    # Cuts are looked for in the same window as before.
    reach = EDGE_PROBE_SECONDS + EDGE_FADE_SECONDS
    head_long = _frames(path, [], reach)
    tail_long = _frames(path, ["-sseof", f"-{reach:.3f}"], reach + 1.0)
    head_frames, tail_frames = head_long[:window], tail_long[-window:]
    head = single_cut(head_frames)
    tail_cut = single_cut(tail_frames)
    tail = len(tail_frames) - tail_cut if tail_cut is not None else 0
    # Black frames, or a fade from or to black, at the very edge: the same sliver without a hard cut.
    head = head if head is not None and 0 < head <= limit else dark_edge(head_long, limit, fade_limit)
    tail = tail if 0 < tail <= limit else dark_edge(tail_long[::-1], limit, fade_limit)
    return head, tail, total


def hold_stray_edge_shots(path: Path) -> Tuple[int, int]:
    """Replace a sliver of a neighbouring shot at either end of ``path`` with a held frame, in place.

    Returns the number of frames replaced at the start and at the end. The audio stream is copied.
    """
    head, tail, total = stray_edge_frames(path)
    if not head and not tail:
        return 0, 0
    fps = _probe(path)[1]
    bounds = ([f"start_frame={head}"] if head else []) + ([f"end_frame={total - tail}"] if tail else [])
    # tpad counts frames from a duration and needs a known frame rate after the trim.
    pads = ([f"start_duration={head / fps:.6f}", "start_mode=clone"] if head else []) + (
        [f"stop_duration={tail / fps:.6f}", "stop_mode=clone"] if tail else [])
    steps = ["trim=" + ":".join(bounds), "setpts=PTS-STARTPTS", f"fps={fps:.6f}", "tpad=" + ":".join(pads)]
    held = path.with_name(f"{path.stem}.held{path.suffix}")
    result = run_ffmpeg_command([
        "ffmpeg", "-y", "-i", str(path), "-map", "0:v:0", "-map", "0:a?", "-vf", ",".join(steps),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(INTERMEDIATE_CRF), "-pix_fmt", "yuv420p",
        "-c:a", "copy", "-movflags", "+faststart", str(held),
    ])
    if result.returncode != 0 or not held.exists():
        held.unlink(missing_ok=True)
        return 0, 0
    held.replace(path)
    logger.info("Held %d frame(s) at the start and %d at the end over a neighbouring shot", head, tail)
    return head, tail
