"""Local visual evidence for deciding whether a vertical crop loses context.

This is deliberately conservative: a face is evidence of a possible subject,
not proof that everything outside that face is disposable. All measurements
are normalized, and motion is measured after compensating for camera movement.
No network/model downloads or additional runtime dependencies are required.
"""

from dataclasses import dataclass
from collections import Counter
from typing import Optional, Sequence

import cv2
import numpy as np


@dataclass(frozen=True)
class SceneEvidence:
    time: float
    face_area: float = 0.0
    face_span: float = 0.0
    face_count: int = 0
    face_center: float = 0.5
    motion: float = 0.0
    outside_motion: float = 0.0
    motion_spread: float = 0.0
    camera_motion: float = 0.0
    text_rows: int = 0
    cut: bool = False
    difference: float = 0.0


class ContextAnalyzer:
    """Bounded-memory analysis of the frames already decoded for face tracking."""

    def __init__(self):
        self.previous: Optional[np.ndarray] = None
        self.histogram: Optional[np.ndarray] = None

    @staticmethod
    def _text_rows(gray: np.ndarray, crop_left: int, crop_right: int) -> int:
        # Text produces repeated small components aligned in rows. Count rows
        # containing several such components outside the proposed crop, rather
        # than treating all high-frequency texture (grass/crowds) as text.
        h, w = gray.shape
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 15, 9)
        count, _, stats, centers = cv2.connectedComponentsWithStats(binary)
        rows: dict[int, list[float]] = {}
        for i in range(1, count):
            x, y, cw, ch, area = stats[i]
            if not (3 <= ch <= h * 0.075 and 1 <= cw <= ch * 1.5):
                continue
            if not (0.15 <= area / max(1, cw * ch) <= 0.9):
                continue
            cx, cy = centers[i]
            if crop_left <= cx <= crop_right:
                continue
            rows.setdefault(int(cy // 8), []).append(float(cx))
        return sum(len(xs) >= 8 and max(xs) - min(xs) > w * 0.2 for xs in rows.values())

    def measure(self, frame: np.ndarray, boxes: Sequence[Sequence[float]], time: float) -> SceneEvidence:
        h, w = frame.shape[:2]
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        crop_width = min(w, h * 9 / 16)
        significant = sorted(boxes, key=lambda b: b[2] * b[3], reverse=True)
        area, center, span = 0.0, 0.5, 0.0
        if significant:
            x, y, fw, fh = significant[0]
            area = fw * fh / (w * h)
            center = (x + fw / 2) / w
            # Ignore tiny audience faces next to a clearly dominant presenter.
            significant = [b for b in significant if b[2] * b[3] >= fw * fh * 0.3]
            span = (max(b[0] + b[2] for b in significant) - min(b[0] for b in significant)) / w
        left = int(np.clip(center * w - crop_width / 2, 0, w - crop_width))
        right = int(left + crop_width)
        hist = cv2.calcHist([frame], [0, 1, 2], None, [8, 8, 8], [0, 256] * 3)
        cv2.normalize(hist, hist)
        motion = outside = spread = camera = 0.0
        delta = 0.0
        cut = False
        if self.previous is not None:
            prev = self.previous
            delta = float(np.mean(cv2.absdiff(prev, gray)))
            hist_distance = cv2.compareHist(self.histogram, hist, cv2.HISTCMP_BHATTACHARYYA)
            cut = delta > 24 and hist_distance > 0.55
            points = cv2.goodFeaturesToTrack(prev, maxCorners=160, qualityLevel=0.02, minDistance=10)
            transform = None
            if not cut and points is not None and len(points) >= 12:
                moved, status, errors = cv2.calcOpticalFlowPyrLK(prev, gray, points, None)
                if moved is not None and status is not None:
                    back, back_status, _ = cv2.calcOpticalFlowPyrLK(gray, prev, moved, None)
                    valid = status.ravel() == 1
                    if back is not None and back_status is not None:
                        valid &= back_status.ravel() == 1
                        valid &= np.linalg.norm((back - points).reshape(-1, 2), axis=1) < 1.5
                    else:
                        valid[:] = False
                    if errors is not None:
                        valid &= errors.ravel() < 20
                    # Reverse-angle edits often share the same colors. A large
                    # image change plus collapsed correspondence detects these
                    # without confusing an ordinary, trackable camera pan.
                    if delta > 24 and (hist_distance > 0.12 or delta > 40) and np.mean(valid) < 0.2:
                        cut = True
                    source, target = points[valid], moved[valid]
                    if not cut and len(source) >= 12:
                        candidate, inliers = cv2.estimateAffinePartial2D(
                            source, target, method=cv2.RANSAC, ransacReprojThreshold=2.5)
                        if candidate is not None and inliers is not None and np.mean(inliers) >= 0.5:
                            scale = float(np.linalg.norm(candidate[:, 0]))
                            support = source[inliers.ravel() == 1].reshape(-1, 2)
                            # A team of players moving together is not camera
                            # motion. Demand support across the image height
                            # and width before compensating for the transform.
                            coverage = np.ptp(support, axis=0)
                            if 0.85 < scale < 1.18 and coverage[0] > w * 0.55 and coverage[1] > h * 0.55:
                                transform = candidate
                                camera = float(np.linalg.norm(candidate[:, 2]) / w)
            if not cut:
                valid_pixels = np.ones_like(gray, dtype=np.uint8)
                if transform is not None:
                    prev = cv2.warpAffine(prev, transform, (w, h))
                    valid_pixels = cv2.warpAffine(valid_pixels, transform, (w, h))
                # Low-frequency differences suppress codec noise and fine texture.
                diff = cv2.absdiff(cv2.GaussianBlur(prev, (7, 7), 0),
                                   cv2.GaussianBlur(gray, (7, 7), 0))
                active = ((diff > 22) & (valid_pixels > 0)).astype(np.float32)
                active[:int(h * 0.08)] = 0  # scoreboards / broadcast overlays
                active[int(h * 0.92):] = 0
                motion = float(active.mean())
                total = float(active.sum())
                outside = float((active[:, :left].sum() + active[:, right:].sum()) / max(1, total))
                columns = np.array_split(active, 6, axis=1)
                spread = sum(float(column.mean()) > 0.035 for column in columns) / 6
        self.previous, self.histogram = gray, hist
        return SceneEvidence(time, area, span, len(significant), center, motion,
                             outside, spread, camera, self._text_rows(gray, left, right), cut, delta)


def context_scene_cuts(samples: Sequence[SceneEvidence]) -> list[float]:
    """Reject sustained rapid movement: an edit must be an isolated change spike.

    Optical-flow failure alone also happens during fast sports camera pans.
    Looking at both neighbors prevents those pans from becoming dozens of cuts.
    """
    cuts = []
    for i in range(1, len(samples)):
        sample = samples[i]
        neighbors = [samples[i - 1].difference]
        if i + 1 < len(samples):
            neighbors.append(samples[i + 1].difference)
        if sample.cut and sample.difference > max(24, 1.6 * max(neighbors)):
            cuts.append(sample.time)
    return cuts


def context_reason(sample: SceneEvidence, crop_fraction: float) -> Optional[str]:
    """Return the evidence requiring a full frame, or None for a safe face crop."""
    if sample.face_area < 0.008:
        return "no_reliable_close_subject"
    if sample.face_count == 1 and sample.face_span > crop_fraction * 0.95:
        return "subject_exceeds_crop"
    if sample.face_count >= 3 and sample.face_span > crop_fraction:
        return "subjects_spread_across_frame"
    # Two people wider than the 9:16 window cannot share a dominant-face crop,
    # even when the larger face is calm and bigger than 2.5% of the frame.
    # Speaker framing may still cut between them; otherwise the shot stays full frame.
    if sample.face_count >= 2 and sample.face_span > crop_fraction * 1.05:
        return "interaction_exceeds_crop"
    peripheral = sample.face_center < 0.18 or sample.face_center > 0.82
    # A large corner webcam does not make the slide underneath disposable.
    if sample.text_rows >= 4 and (sample.face_area < 0.045 or peripheral):
        return "screen_content_outside_crop"
    if sample.face_area < 0.045 and peripheral:
        return "peripheral_presenter"
    if sample.motion > 0.065 and sample.outside_motion > 0.45 and sample.motion_spread >= 0.5:
        return "distributed_action"
    return None


def build_context_layout_plan(samples: Sequence[SceneEvidence], scene_cuts: Sequence[float],
                              duration: float, crop_fraction: float) -> list[dict]:
    """Choose one stable layout per shot; uncertain shots preserve the full image.

    Real short shots keep their own decisions. A brief missed face in an
    otherwise confident close-up does not change the layout. Sustained context
    risk wins over face tracking, including a small webcam over gameplay.
    """
    if duration <= 0 or not samples:
        return [{"start": 0.0, "end": max(0.0, duration), "kind": "fit", "reason": "analysis_unavailable"}]
    cuts = sorted({float(c) for c in scene_cuts if 0 < c < duration})
    boundaries = [0.0, *cuts, duration]
    plan = []
    for start, end in zip(boundaries, boundaries[1:]):
        shot = [s for s in samples if start <= s.time < end]
        reasons = [context_reason(s, crop_fraction) for s in shot]
        risks = [reason for reason in reasons if reason is not None]
        # Require recurrent evidence, but preserve a short shot with only one
        # available sample. This is a fraction of samples, not a speed threshold.
        fit = not shot or len(risks) / len(shot) >= 0.3
        reason = Counter(risks).most_common(1)[0][0] if risks and fit else "confident_subject"
        if not shot:
            reason = "analysis_unavailable"
        kind = "fit" if fit else "face"
        if plan and plan[-1]["kind"] == kind and plan[-1]["reason"] == reason:
            plan[-1]["end"] = end
        else:
            plan.append({"start": start, "end": end, "kind": kind, "reason": reason})
    return plan
