"""Local visual evidence for deciding whether a vertical crop loses context.

This is deliberately conservative: a face is evidence of a possible subject,
not proof that everything outside that face is disposable. All measurements
are normalized, and motion is measured after compensating for camera movement.
No network/model downloads or additional runtime dependencies are required.
"""

import logging
from dataclasses import dataclass
from collections import Counter
from typing import Optional, Sequence

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Lines of text a face crop may slice through before the shot keeps the full
# frame. One line is often a stray texture; a sign or lower third has two.
TEXT_CUT_MIN_LINES = 2

# A paragraph line is at least this share of the frame width, and one line of
# the paragraph is at least the longer share (see ContextAnalyzer._text_block).
TEXT_BLOCK_MIN_LINE_FRAC = 0.08
TEXT_BLOCK_LONG_LINE_FRAC = 0.2
# Every this many candidate lines, a paragraph needs one more line.
TEXT_BLOCK_CLUTTER_LINES = 8

# Composition hysteresis: a run of one layout shorter than this inherits its
# neighbours, so a clip does not flip between a full-height crop and a fitted
# strip within a couple of seconds.
LAYOUT_HOLD_SECONDS = 2.5

# A fit run this short is a one-sample flash: it cannot be read anyway.
LAYOUT_FLASH_SECONDS = 0.5

# A clip whose crops and fits alternate this often uses one full-context
# composition, so it does not flicker between them.
LAYOUT_MAX_RUNS = 5

# A small but steady single face (a wide shot of the same speaker) may inherit
# a neighbouring face crop; an empty or crowded shot never does.
SOFT_SINGLE_FACE_RATE = 0.6

# Cover: a fitted shot of live footage (b-roll, a POV or handheld vlog, a wide
# scene) fills the 9:16 frame with a full-height crop instead of a letterboxed
# strip, the way short-form editors cut it. Only shots whose risk is that no
# close subject was found qualify (never crowds, cut text, slides or a
# presenter beside a screen), and only when something in the picture moves: a
# still shot is a composed graphic, a comparison or a screen, and keeps the
# full frame. A drone crossing a static wall already moves enough, and so does
# a slow push-in over a photo (camera motion with nothing moving inside it).
COVER_REASONS = frozenset({"no_reliable_close_subject", "peripheral_presenter"})
COVER_MIN_MOTION = 0.008
COVER_MIN_CAMERA_MOTION = 0.003
# Share of a shot's samples that may show an aligned paragraph (a document,
# a tweet, a slide) before it is content rather than footage. Shelf labels
# and brick courses in moving footage stay under it.
COVER_MAX_TEXT_BLOCK_RATE = 0.35
# Share of samples with two or more faces that makes a shot a group.
COVER_MAX_GROUP_RATE = 0.3
# Share of a shot's samples in which the crop would cut text, above which the shot keeps the full frame.
# Measured: an edited comparison with burned-in captions 0.28 / 0.32 / 0.52, its title card 0.0 / 0.0 / 1.0,
# a drawn map 0.01 / 0.97 / 1.0; plain gameplay 0.0 / 0.20 / 0.48.
COVER_MAX_CUT_CAPTION_RATE = 0.15   # an aligned block of two lines or more, cut
COVER_MAX_CUT_LINES_RATE = 0.6      # two lines or more cut
COVER_MAX_CUT_ALWAYS_RATE = 0.9     # some line cut, in nearly every sample
# A crop centre this close to the last one continues the same composition.
COVER_SAME_X = 0.05
# One face away from the frame edge and at least this share of the frame (head
# and shoulders, or closer) is the subject: motion around it is the person's
# own gestures, or a chat overlay beside them, not action across the frame.
PRESENTER_FACE_AREA = 0.03
# How much less a column at the frame edge counts than one in the middle when looking for the subject of a shot
# without faces (see ContextAnalyzer._focus_center). Read by the interview layout's covers; the scene plan's
# covers (_cover_x) keep the detail centre, which suits the handheld POV footage they were tuned on.
FOCUS_CENTRE_BIAS = 0.6


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
    # Horizontal span (0-1) of visible detail (edges, text, UI). Defaults to
    # the whole width, so unmeasured evidence never permits a zoomed fit.
    detail_left: float = 0.0
    detail_right: float = 1.0
    # Span of the regions a crop must not cut: faces plus text lines.
    protect_left: float = 0.0
    protect_right: float = 1.0
    # Text lines (placards, lower thirds, slides) cut by the face crop's edges.
    cut_text_lines: int = 0
    # Lines in the largest aligned paragraph (an on-screen graphic, a tweet,
    # a document). Book spines and foliage make stray "lines"; they rarely
    # stack into two or more lines sharing a left edge, centre or right edge.
    text_block_lines: int = 0
    # Centre (0-1 of the width) of the full-height 9:16 window holding the most detail and colour: the dish in a
    # food insert, the object in a product shot. The middle when unmeasured.
    focus_center: float = 0.5


class ContextAnalyzer:
    """Bounded-memory analysis of the frames already decoded for face tracking."""

    def __init__(self):
        self.previous: Optional[np.ndarray] = None
        self.histogram: Optional[np.ndarray] = None

    @staticmethod
    def _text_components(gray: np.ndarray) -> list[tuple[float, float, int, int, int]]:
        # Text produces repeated small components aligned in rows, rather than
        # treating all high-frequency texture (grass/crowds) as text.
        h, _ = gray.shape
        binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                       cv2.THRESH_BINARY_INV, 15, 9)
        count, _, stats, centers = cv2.connectedComponentsWithStats(binary)
        components = []
        for i in range(1, count):
            x, y, cw, ch, area = stats[i]
            if not (3 <= ch <= h * 0.075 and 1 <= cw <= ch * 1.5):
                continue
            if not (0.15 <= area / max(1, cw * ch) <= 0.9):
                continue
            cx, cy = centers[i]
            components.append((float(cx), float(cy), int(x), int(cw), int(ch)))
        return components

    @staticmethod
    def _text_rows(components, width: int, crop_left: int, crop_right: int) -> int:
        # Rows containing several text components outside the proposed crop.
        rows: dict[int, list[float]] = {}
        for cx, cy, *_ in components:
            if crop_left <= cx <= crop_right:
                continue
            rows.setdefault(int(cy // 8), []).append(cx)
        return sum(len(xs) >= 8 and max(xs) - min(xs) > width * 0.2 for xs in rows.values())

    @staticmethod
    def _text_lines(gray: np.ndarray) -> list[tuple[int, int, int, int]]:
        """Boxes (x0, y0, x1, y1) of text lines: signs, lower thirds, slides.

        Letter-sized components of either polarity (dark-on-light placards,
        light-on-dark lower thirds) are chained when they share a baseline,
        a similar height, and sit closer than a character height or so apart.
        At analysis resolution neighbouring letters often touch, so a
        component may be a few letters wide.
        """
        h, _ = gray.shape
        letters = []
        for image in (gray, 255 - gray):
            binary = cv2.adaptiveThreshold(image, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                           cv2.THRESH_BINARY_INV, 15, 9)
            count, _, stats, _ = cv2.connectedComponentsWithStats(binary)
            for i in range(1, count):
                x, y, cw, ch, area = stats[i]
                if 4 <= ch <= h * 0.075 and cw <= ch * 3 and 0.15 <= area / (cw * ch) <= 0.9:
                    letters.append((int(x), int(y), int(x + cw), int(y + ch)))
        letters.sort()
        parent = list(range(len(letters)))

        def root(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, (x0, y0, x1, y1) in enumerate(letters):
            ch = y1 - y0
            for j in range(i + 1, len(letters)):
                u0, v0, u1, v1 = letters[j]
                if u0 - x1 > ch * 1.2:
                    break
                other = v1 - v0
                if 0.6 * ch <= other <= ch / 0.6 and abs((v0 + v1) - (y0 + y1)) <= ch * 0.6:
                    parent[root(j)] = root(i)
        groups: dict[int, list[tuple[int, int, int, int]]] = {}
        for i, box in enumerate(letters):
            groups.setdefault(root(i), []).append(box)
        lines = []
        for boxes in groups.values():
            x0 = min(b[0] for b in boxes)
            x1 = max(b[2] for b in boxes)
            heights = sorted(b[3] - b[1] for b in boxes)
            if len(boxes) >= 4 and x1 - x0 >= heights[len(heights) // 2] * 4:
                lines.append((x0, min(b[1] for b in boxes), x1, max(b[3] for b in boxes)))
        return lines

    @staticmethod
    def _text_block(lines: Sequence[tuple[int, int, int, int]], width: int) -> int:
        """Lines in the largest stack of aligned, similar text lines.

        Two lines belong to one paragraph when their heights match, the gap
        between them is at most about a line height, and their left edges,
        centres or right edges line up. Each must be a real line (at least
        TEXT_BLOCK_MIN_LINE_FRAC of the width, clear of the frame edge, where
        every clipped texture "ends") and the stack needs one line of
        TEXT_BLOCK_LONG_LINE_FRAC: a sentence, not a word. Foliage and book
        spines make many candidate lines, and some line up by chance, so a
        cluttered frame needs a taller stack.
        """
        tolerance = max(2.0, width * 0.01)
        edge = max(3, int(width * 0.01))
        usable = [box for box in lines
                  if box[2] - box[0] >= width * TEXT_BLOCK_MIN_LINE_FRAC
                  and box[0] > edge and box[2] < width - edge]
        usable.sort(key=lambda box: box[1])
        parent = list(range(len(usable)))

        def root(i: int) -> int:
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for i, (x0, y0, x1, y1) in enumerate(usable):
            height = y1 - y0
            for j in range(i + 1, len(usable)):
                u0, v0, u1, v1 = usable[j]
                if v0 - y1 > height * 1.2:
                    break
                other = v1 - v0
                if not (0.6 * height <= other <= height / 0.6) or v0 < y0 + height * 0.6:
                    continue
                if (abs(x0 - u0) <= tolerance or abs(x1 - u1) <= tolerance
                        or abs((x0 + x1) - (u0 + u1)) <= 2 * tolerance):
                    parent[root(j)] = root(i)
        groups: dict[int, list[tuple[int, int, int, int]]] = {}
        for i, box in enumerate(usable):
            groups.setdefault(root(i), []).append(box)
        cluttered = 2 + len(usable) // TEXT_BLOCK_CLUTTER_LINES
        best = 0
        for boxes in groups.values():
            long_lines = sum(b[2] - b[0] >= width * TEXT_BLOCK_LONG_LINE_FRAC for b in boxes)
            # Two aligned sentences are a paragraph; one sentence beside
            # aligned fragments only counts in an uncluttered frame.
            if long_lines >= 2 or (long_lines == 1 and len(boxes) >= cluttered):
                best = max(best, len(boxes))
        return best

    @staticmethod
    def _detail_span(gray: np.ndarray) -> tuple[float, float]:
        """Leftmost/rightmost columns containing visible detail, as 0-1 fractions.

        Flat letterbox margins and empty slide backgrounds have no edges; any
        column with a couple of edge pixels (text, UI, objects) counts as content.
        """
        h, w = gray.shape
        edges = cv2.Canny(gray, 50, 150)
        edges[:, :2] = 0
        edges[:, w - 2:] = 0
        columns = np.convolve((edges > 0).sum(axis=0).astype(np.float32), np.ones(3) / 3, "same")
        active = np.flatnonzero(columns >= 2)
        if not len(active):
            return 0.5, 0.5
        return float(active[0]) / w, float(active[-1] + 1) / w

    @staticmethod
    def _focus_center(frame: np.ndarray, gray: np.ndarray, crop_width: float) -> float:
        """Centre of the crop-wide window that holds the subject of a shot without faces.

        Per column: edge pixels (what is in focus, what has texture) plus saturated colour (food, a product),
        each relative to its average over the frame, weighted towards the middle, where a subject framed for
        16:9 usually is. Edges alone follow a wicker basket or a tablecloth past the plate beside them; without
        the weighting a bright object at the side beats the thing the camera is pointed at.
        """
        _, w = gray.shape
        window = int(round(crop_width))
        if window < 2 or window >= w:
            return 0.5
        edges = (cv2.Canny(gray, 50, 150) > 0).sum(axis=0).astype(np.float32)
        hsv = cv2.cvtColor(cv2.GaussianBlur(frame, (0, 0), 2), cv2.COLOR_BGR2HSV).astype(np.float32)
        colour = hsv[..., 1] * hsv[..., 2] / 255.0
        colour = np.maximum(colour - float(np.median(colour)), 0).sum(axis=0)
        score = edges / max(float(edges.mean()), 1e-6) + colour / max(float(colour.mean()), 1e-6)
        score *= 1 - FOCUS_CENTRE_BIAS * np.abs(np.arange(w) - (w - 1) / 2) / (w / 2)
        sums = np.convolve(score, np.ones(window, dtype=np.float32), "valid")
        peak = float(sums.max())
        if peak <= 0:
            return 0.5
        best = np.flatnonzero(sums >= peak * 0.97)
        return float((np.median(best) + window / 2) / w)

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
        components = self._text_components(gray)
        # Text-line and detail extents only refine the layout. If they fail,
        # fall back to "no cut text, full-width detail" (the previous behaviour)
        # instead of discarding the whole clip analysis.
        cut_lines, protect_left, protect_right = 0, 0.0, 1.0
        detail_left, detail_right = 0.0, 1.0
        block = 0
        focus = 0.5
        try:
            focus = self._focus_center(frame, gray, crop_width)
            lines = self._text_lines(gray)
            block = self._text_block(lines, w)
            margin = max(2, int(w * 0.005))
            cut_lines = sum(
                any(x0 < edge - margin and x1 > edge + margin for edge in (left, right) if 0 < edge < w)
                for x0, _, x1, _ in lines
            )
            protected = [(x0, x1) for x0, _, x1, _ in lines]
            protected += [(b[0], b[0] + b[2]) for b in significant]
            protect_left = min((a for a, _ in protected), default=w / 2) / w
            protect_right = max((b for _, b in protected), default=w / 2) / w
            detail_left, detail_right = self._detail_span(gray)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Text/detail measurement failed at %.2fs (%s); ignoring it", time, exc)
            cut_lines, protect_left, protect_right = 0, 0.0, 1.0
            detail_left, detail_right = 0.0, 1.0
            block = 0
            focus = 0.5
        return SceneEvidence(
            time, area, span, len(significant), center, motion, outside, spread, camera,
            self._text_rows(components, w, left, right), cut, delta,
            detail_left=detail_left, detail_right=detail_right,
            protect_left=max(0.0, protect_left), protect_right=min(1.0, protect_right),
            cut_text_lines=cut_lines, text_block_lines=block, focus_center=focus,
        )


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
    # A placard or lower third running past the crop edge would lose letters.
    if sample.cut_text_lines >= TEXT_CUT_MIN_LINES:
        return "text_cut_by_crop"
    peripheral = sample.face_center < 0.18 or sample.face_center > 0.82
    # A large corner webcam does not make the slide underneath disposable.
    if sample.text_rows >= 4 and (sample.face_area < 0.045 or peripheral):
        return "screen_content_outside_crop"
    if sample.face_area < 0.045 and peripheral:
        return "peripheral_presenter"
    if sample.motion > 0.065 and sample.outside_motion > 0.45 and sample.motion_spread >= 0.5:
        if sample.face_count == 1 and sample.face_area >= PRESENTER_FACE_AREA and not peripheral:
            return None
        return "distributed_action"
    return None


def _cover_x(shot: Sequence[SceneEvidence], reason: str) -> Optional[float]:
    """Centre (0-1 of the width) of a full-height crop for a fitted shot of live footage, or None to keep the fit.

    A lone face, however small, is the subject: the crop centres on it. Otherwise the crop centres on the visible
    detail, which is the middle of the frame for most handheld and b-roll footage.
    """
    if reason not in COVER_REASONS or not shot:
        return None
    if (float(np.median([s.motion for s in shot])) < COVER_MIN_MOTION
            and float(np.median([s.camera_motion for s in shot])) < COVER_MIN_CAMERA_MOTION):
        return None
    if sum(s.text_block_lines >= 2 for s in shot) / len(shot) > COVER_MAX_TEXT_BLOCK_RATE:
        return None
    # Text the full-height crop would cut through keeps the whole picture: a caption burned into an edited
    # video (in a seventh of the shot is a caption that comes and goes), a title card, labels across a map.
    cut_captions = sum(s.text_block_lines >= 2 and s.cut_text_lines >= 1 for s in shot) / len(shot)
    cut_lines = sum(s.cut_text_lines >= 2 for s in shot) / len(shot)
    cut_always = sum(s.cut_text_lines >= 1 for s in shot) / len(shot)
    if (cut_captions >= COVER_MAX_CUT_CAPTION_RATE or cut_lines >= COVER_MAX_CUT_LINES_RATE
            or cut_always >= COVER_MAX_CUT_ALWAYS_RATE):
        return None
    if sum(s.face_count >= 2 for s in shot) / len(shot) >= COVER_MAX_GROUP_RATE:
        return None
    faces = [s.face_center for s in shot if s.face_count == 1 and s.face_area > 0]
    if len(faces) / len(shot) >= SOFT_SINGLE_FACE_RATE:
        return float(np.median(faces))
    return float(np.median([(s.detail_left + s.detail_right) / 2 for s in shot]))


def _same_layout(a: dict, b: dict) -> bool:
    if a["kind"] != b["kind"] or a["reason"] != b["reason"]:
        return False
    return a["kind"] != "cover" or abs(a["x"] - b["x"]) <= COVER_SAME_X


def _soft_fit(shot: Sequence[SceneEvidence], reason: str) -> bool:
    """A fit chosen only because one steady speaker looks small (a wide shot)."""
    if reason != "no_reliable_close_subject" or not shot:
        return False
    single = sum(s.face_count == 1 and s.face_area > 0 for s in shot)
    return single / len(shot) >= SOFT_SINGLE_FACE_RATE


def stabilize_layout_plan(plan: list[dict]) -> list[dict]:
    """Give each composition a minimum hold so neighbouring shots stay coherent.

    A face crop shorter than LAYOUT_HOLD_SECONDS next to a fitted shot becomes
    a fit as well: widening never loses content. A short fitted shot between
    face crops becomes a crop only when it is soft (the same lone speaker in a
    wider shot) or a sub-second flash. Content, crowds and cut text stay fit.
    """
    plan = [dict(p) for p in plan]

    def groups():
        out, start = [], 0
        for i in range(1, len(plan) + 1):
            if i == len(plan) or plan[i]["kind"] != plan[start]["kind"]:
                out.append((start, i))
                start = i
        return out

    while True:
        runs = groups()
        candidates = []
        for index, (a, b) in enumerate(runs):
            length = plan[b - 1]["end"] - plan[a]["start"]
            if length >= LAYOUT_HOLD_SECONDS or len(runs) == 1:
                continue
            neighbours = [runs[j] for j in (index - 1, index + 1) if 0 <= j < len(runs)]
            if plan[a]["kind"] == "face":
                # Widening never loses content; a crop next to a cover already fills the frame like it.
                flip, rank = any(plan[r[0]]["kind"] == "fit" for r in neighbours), 1
                neighbours = [r for r in neighbours if plan[r[0]]["kind"] == "fit"]
            elif plan[a]["kind"] == "cover":
                flip, rank = length < LAYOUT_FLASH_SECONDS, 2
            else:
                soft = all(p.get("soft") for p in plan[a:b])
                flip = soft or length < LAYOUT_FLASH_SECONDS
                # A brief wide shot of the lone speaker between two crops
                # joins them; otherwise fast close/wide cutting would widen
                # every crop and letterbox the whole clip.
                rank = 0 if soft and len(neighbours) == 2 else 2
            if flip:
                donor = max(neighbours, key=lambda r: plan[r[1] - 1]["end"] - plan[r[0]]["start"])
                candidates.append((rank, length, a, b, donor))
        if not candidates:
            break
        # Soft wide shots between crops become crops first; then short crops
        # widen; other short fits only inherit a crop once the crops around
        # them are known to hold.
        *_, a, b, donor = min(candidates)
        # Continue the composition of the shot right next to the flipped run.
        source = plan[donor[1] - 1] if donor[1] <= a else plan[donor[0]]
        for entry in plan[a:b]:
            entry["kind"] = source["kind"]
            entry["reason"] = source["reason"]
            entry["soft"] = source.get("soft", False)
            entry.pop("x", None)
            if "x" in source:
                entry["x"] = source["x"]
        merged = [plan[0]]
        for entry in plan[1:]:
            last = merged[-1]
            if _same_layout(entry, last):
                last["end"] = entry["end"]
                last["soft"] = last.get("soft", False) and entry.get("soft", False)
            else:
                merged.append(entry)
        plan = merged
    runs = groups()
    hard_fit = any(p["kind"] == "fit" and not p.get("soft")
                   and p["end"] - p["start"] >= LAYOUT_FLASH_SECONDS for p in plan)
    fit_duration = sum(p["end"] - p["start"] for p in plan if p["kind"] == "fit")
    duration = plan[-1]["end"] - plan[0]["start"] if plan else 0
    # Repeated crop/fit changes or a predominantly wide demonstration use one
    # full-context composition. First resolve soft/brief shots above, so a lone
    # speaker's wide angle does not needlessly shrink the whole clip.
    # A speaker with a graph or a document cut in (crop, fit, crop) keeps the crops around it; cover shots fill the
    # frame like crops. Only a clip that is mostly fitted content, or a speaker that keeps flipping to the screen,
    # letterboxes throughout. A documentary montage (footage between documents) keeps its covers at every cut.
    flipping = len(runs) >= LAYOUT_MAX_RUNS and not any(p["kind"] == "cover" for p in plan)
    if hard_fit and len(runs) > 1 and (fit_duration > duration * .5 or flipping):
        return [{"start": plan[0]["start"], "end": plan[-1]["end"], "kind": "fit", "reason": "stable_context", "soft": False}]
    return plan


def build_context_layout_plan(samples: Sequence[SceneEvidence], scene_cuts: Sequence[float],
                              duration: float, crop_fraction: float) -> list[dict]:
    """Choose one stable layout per shot; uncertain shots preserve the full image.

    A brief missed face in an otherwise confident close-up does not change the
    layout. Sustained context risk wins over face tracking, including a small
    webcam over gameplay. A fitted shot of moving footage without a close
    subject (b-roll, a POV) becomes a ``cover`` shot: a full-height crop
    centred at ``x`` (see ``_cover_x``). Short shots then inherit their neighbours'
    composition (see ``stabilize_layout_plan``).
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
        soft = kind == "fit" and _soft_fit(shot, reason)
        entry = {"start": start, "end": end, "kind": kind, "reason": reason, "soft": soft}
        x = _cover_x(shot, reason) if fit else None
        if x is not None:
            entry.update(kind="cover", soft=False, x=x)
        if plan and _same_layout(plan[-1], entry):
            plan[-1]["end"] = end
            plan[-1]["soft"] = plan[-1]["soft"] and entry["soft"]
        else:
            plan.append(entry)
    try:
        return stabilize_layout_plan(plan)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Layout hysteresis failed (%s); keeping per-shot layout", exc)
        return plan
