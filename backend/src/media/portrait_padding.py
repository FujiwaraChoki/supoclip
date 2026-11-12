"""Recognize portrait pictures embedded in blurred or plain landscape padding.

This is evidence of a source composition, not a face crop. Two straight inner
edges and much sharper detail between them must agree across several frames.
Ambiguous frames keep the normal scene-aware layout.
"""

from typing import Optional, Sequence

import cv2
import numpy as np

Bounds = tuple[float, float]
PaddingWindow = tuple[float, float, int, int]  # start, end, crop width, x


def portrait_padding_bounds(gray: np.ndarray) -> Optional[Bounds]:
    """Return normalized inner edges only for a clear padded portrait picture."""
    h, w = gray.shape
    if h < 64 or w <= h:
        return None
    # Median contrast demands a straight edge through most of the picture,
    # rather than a face, ball, or other detailed object near its centre.
    view = gray[int(h * .06):int(h * .94)].astype(np.float32)
    differences = np.abs(np.diff(view, axis=1))
    seams = np.median(differences, axis=0)
    detail = np.abs(cv2.Laplacian(gray, cv2.CV_32F)).mean(axis=0)
    accumulated = np.concatenate(([0.], np.cumsum(detail)))

    def sharpness(left: int, right: int) -> float:
        return float((accumulated[right] - accumulated[left]) / (right - left))

    def candidates(start: float, end: float) -> list[int]:
        a, b = int(w * start), int(w * end)
        outside = seams[:int(w * .16)] if start < .5 else seams[int(w * .84):]
        floor = max(8., float(np.median(outside)) * 4)
        return [i + 1 for i in range(a, b)
                if seams[i] >= floor and np.mean(differences[:, i] >= 8) >= .65]

    best = None
    for left in candidates(.18, .46):
        for right in candidates(.54, .82):
            if not (.43 <= (right - left) / h <= .82):
                continue
            if abs((left + right) / 2 - w / 2) > w * .06:
                continue
            inner = sharpness(left + 3, right - 3)
            sides = [sharpness(3, left - 3), sharpness(right + 3, w - 3)]
            if inner < 3 or max(sides) > inner * .25:
                continue
            # Preserve the outermost qualifying edges. A stronger seam inside
            # the portrait (a jersey, border, or caption panel) is not its edge.
            score = (right - left, min(seams[left - 1], seams[right - 1]))
            if best is None or score > best[0]:
                # Keep a small safety margin, including antialiased borders.
                best = (score, max(0, left - 2) / w, min(w, right + 2) / w)
    return (best[1], best[2]) if best else None


def plan_portrait_padding(
    samples: Sequence[tuple[float, Optional[Bounds]]],
    scene_cuts: Sequence[float],
    duration: float,
    width: int,
) -> list[PaddingWindow]:
    """Corroborate stable bounds; never carry them into an unmeasured shot.

    Three consecutive samples (normally one second at 3 fps) are required.
    Missing evidence or a geometry change ends a run, at the cut if known.
    Cuts that retain the same padded composition do not make it flash. The last
    sample owns at most its normal sampling interval, including at EOF.
    """
    if not samples or duration <= 0 or width < 2:
        return []
    times = [t for t, _ in samples]
    gaps = [b - a for a, b in zip(times, times[1:]) if b > a]
    step = float(np.median(gaps)) if gaps else 1 / 3
    windows: list[PaddingWindow] = []
    run: list[tuple[float, Bounds]] = []

    def finish(end: float) -> None:
        if len(run) < 3 or run[-1][0] - run[0][0] < .5:
            return
        left = min(bounds[0] for _, bounds in run)
        right = max(bounds[1] for _, bounds in run)
        x = max(0, int(left * width) // 2 * 2)
        edge = min(width // 2 * 2, int(np.ceil(right * width / 2)) * 2)
        windows.append((run[0][0], min(duration, end), edge - x, x))

    for time, bounds in samples:
        if time >= duration:
            break
        if run:
            previous = run[-1][0]
            changed = bounds is not None and any(
                abs(bounds[i] - run[0][1][i]) > .015 for i in (0, 1)
            )
            if bounds is None or changed or time - previous > step * 1.6:
                finish(min(time, previous + step,
                           min((c for c in scene_cuts if previous < c <= time), default=time)))
                run = []
        if bounds is not None:
            run.append((time, bounds))
    if run:
        finish(run[-1][0] + step)
    # Adjacent shots with the same bounds need one filter branch, not a flash
    # back to the original fit at each cut.
    merged: list[PaddingWindow] = []
    for window in windows:
        if (merged and abs(merged[-1][1] - window[0]) < .001
                and merged[-1][2:] == window[2:]):
            merged[-1] = (merged[-1][0], window[1], *window[2:])
        else:
            merged.append(window)
    return merged
