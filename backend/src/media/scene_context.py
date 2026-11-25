"""Local visual evidence for deciding whether a vertical crop loses context.

This is deliberately conservative: a face is evidence of a possible subject,
not proof that everything outside that face is disposable. All measurements
are normalized, and motion is measured after compensating for camera movement.
No network/model downloads or additional runtime dependencies are required.
"""

import logging
from dataclasses import dataclass
from collections import Counter
from typing import Any, Callable, List, Optional, Sequence, Tuple

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

# Composition hysteresis: a fitted run shorter than this beside a frame-filling
# shot fills the frame too, so a clip does not drop into a small strip for a
# couple of seconds and come back.
LAYOUT_HOLD_SECONDS = 2.5

# A frame-filling run this short between two fitted shots is a one-sample
# flash and stays fitted.
LAYOUT_FLASH_SECONDS = 0.5

# Fill first. A 9:16 clip fills the frame: a tracked crop on a confident
# subject, otherwise a ``cover`` (a still full-height crop on the shot's
# subject), the way short-form editors cut b-roll, vlogs, wide shots, groups
# and gameplay. The whole 16:9 picture on a blurred canvas (``fit``) is a
# third of the screen, so it is kept for shots that are *content*, where the
# full width is what the viewer came for: a screen or a document, a card of
# several lines (a quiz, a list, a comparison's labels). See
# ``content_from_words``.
#
# A line or two of text the crop cuts in footage (a burned-in subtitle, a
# lower third, a scoreboard) is not content: the shot fills the frame and the
# caption code deals with the source's own text (``media.source_captions``).

# Still: neither the picture nor the camera moves (a slide, a chart, a photo).
COVER_MIN_MOTION = 0.008
COVER_MIN_CAMERA_MOTION = 0.003
# Share of a shot's samples that must hint at text before its frames are read.
CONTENT_MIN_RATE = 0.35
# Without OCR: lines of an aligned paragraph that make a still picture a document.
TEXT_BLOCK_STILL_LINES = 2
# Without OCR: share of a still, faceless shot's samples in which the crop cuts a text line (a chart, a map).
STILL_CUT_TEXT_RATE = 0.6
# A face this large in most of a shot is a person talking to the camera, whatever is written around them.
CLOSE_FACE_AREA = 0.02
CLOSE_FACE_RATE = 0.5
# A face in this share of a shot's samples is where its full-height crop centres.
FILL_FACE_RATE = 0.5
# Risks that are only about text beside or under the subject. When such a shot turns out not to be content and
# shows one close face in ONE_FACE_RATE of its samples, it is a talking head: the tracked crop follows it.
TEXT_ONLY_REASONS = frozenset({"text_cut_by_crop", "screen_content_outside_crop"})
ONE_FACE_RATE = 0.6
# Share of a still shot's samples without a face that makes it a picture nobody is in.
STILL_FACELESS_RATE = 0.7

# What the words read off a frame (``screen_text``) must amount to before the frame is content.
# Measured on reviewed clips: a quiz card over a wide shot reads 8-14 words on 2-3 lines with 5-9 outside a
# centred crop; a subtitled scene 3-10 words on 1-2 lines; stone walls, barred windows and poster-covered
# rooms (which the shape tests call paragraphs) read 0-2.
PAGE_MIN_WORDS = 20
PAGE_MIN_LOST_WORDS = 6
PAGE_MIN_BLOCK_LINES = 4
CARD_MIN_LINES = 3
CARD_MIN_WORDS = 10
CARD_MIN_LOST_WORDS = 4
# Two lines belong to one block when the gap between them is at most this many line heights (a card's question
# and its rows of answers are about two apart; labels scattered over a picture are further).
BLOCK_LINE_GAP = 2.4
BLOCK_LINE_MIN_WORDS = 3
# Words a read shares with the clip's known content to be the same card in another shot.
SAME_CARD_WORDS = 4

WordReader = Callable[[float], Optional[Sequence[Any]]]

# A crop centre this close to the last one continues the same composition.
COVER_SAME_X = 0.05
# One face away from the frame edge and at least this share of the frame (head
# and shoulders, or closer) is the subject: motion around it is the person's
# own gestures, or a chat overlay beside them, not action across the frame.
PRESENTER_FACE_AREA = 0.03
# How much less a column at the frame edge counts than one in the middle when looking for the subject of a shot
# without faces (see ContextAnalyzer._focus_center). Read by the interview layout's covers; the scene plan's
# covers (fill_x) keep the detail centre, which suits the handheld POV footage they were tuned on.
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


def _still(shot: Sequence[SceneEvidence]) -> bool:
    """Neither the picture nor the camera moves: a slide, a chart, a photo."""
    return (float(np.median([s.motion for s in shot])) < COVER_MIN_MOTION
            and float(np.median([s.camera_motion for s in shot])) < COVER_MIN_CAMERA_MOTION)


def _rate(shot: Sequence[SceneEvidence], test: Callable[[SceneEvidence], bool]) -> float:
    return sum(1 for sample in shot if test(sample)) / len(shot)


def fill_x(shot: Sequence[SceneEvidence]) -> float:
    """Centre (0-1 of the width) of the full-height crop that fills the frame with this shot.

    A face seen in half of the shot, however small, is the subject: the crop centres on it (on the largest one
    of a group). Otherwise the crop centres on the visible detail, which is the middle of the frame for most
    handheld footage, b-roll and gameplay.
    """
    if not shot:
        return 0.5
    faces = [s.face_center for s in shot if s.face_count >= 1 and s.face_area > 0]
    if len(faces) / len(shot) >= FILL_FACE_RATE:
        return float(np.median(faces))
    return float(np.median([(s.detail_left + s.detail_right) / 2 for s in shot]))


def _one_close_face(shot: Sequence[SceneEvidence]) -> bool:
    """One person, close and away from the frame edge, in most of the shot."""
    return _rate(shot, lambda s: s.face_count == 1 and s.face_area >= CLOSE_FACE_AREA
                 and 0.18 <= s.face_center <= 0.82) >= ONE_FACE_RATE


def _may_hold_text(shot: Sequence[SceneEvidence]) -> bool:
    """Cheap evidence that a shot shows text worth reading the frame for."""
    return (
        _rate(shot, lambda s: s.text_block_lines >= 2) >= CONTENT_MIN_RATE
        or _rate(shot, lambda s: s.text_rows >= 4) >= CONTENT_MIN_RATE
        or _rate(shot, lambda s: s.cut_text_lines >= 1) >= CONTENT_MIN_RATE
    )


def _letter_words(words: Sequence[Any]) -> List[Any]:
    """Words with two letters or more: scores, timers, ammo counts and coordinates are not text to read."""
    return [word for word in words if sum(char.isalpha() for char in word.text) >= 2]


def text_blocks(lines: Sequence[Sequence[Any]]) -> List[List[Sequence[Any]]]:
    """Lines (top to bottom) grouped into blocks: a line joins the block whose last line sits just above it
    and over the same columns. A paragraph, a list and a card with its answers are one block; the labels a
    game or a broadcast scatters over the picture (a counter here, a prompt there) are many blocks of one."""
    blocks: List[List[Sequence[Any]]] = []
    for line in lines:
        top, bottom = min(w.top for w in line), max(w.bottom for w in line)
        left, right = line[0].left, line[-1].right
        for block in blocks:
            above = block[-1]
            a_top, a_bottom = min(w.top for w in above), max(w.bottom for w in above)
            height = max(bottom - top, a_bottom - a_top)
            if (0 <= top - a_bottom + 0.5 * height and top - a_bottom <= BLOCK_LINE_GAP * height
                    and min(right, above[-1].right) > max(left, above[0].left)):
                block.append(line)
                break
        else:
            blocks.append([line])
    return blocks


def content_from_words(
    reads: Sequence[Sequence[Any]], left: float, right: float, known: Optional[set] = None,
) -> Optional[str]:
    """Why the frames read in ``reads`` are content a crop to ``left``-``right`` must not cut, or None.

    Each read is the words on one frame (``screen_text.ScreenWord``). Only words with letters count, and what
    matters is the largest block of lines stacked over each other (``text_blocks``). A read is content when
    it is

    - a page: many words, several of them outside the crop, in a block of four lines or more (slides, a
      desktop, a document, a tweet);
    - a card: a block of three lines or more with words outside the crop (a quiz, a list, a comparison's
      labels).

    One or two lines (a subtitle, a lower third, a scoreboard) are never content, and neither is text
    scattered over a picture, moving or still (a game's HUD and menus, a map's place names, a chart's axis
    labels, shop signs), unless it is the card again:
    ``known`` holds the words of the clip's content found so far, and a read sharing SAME_CARD_WORDS of them
    shows the same card in another shot (its question before the answers appear, the card behind a
    close-up), so the clip does not drop the full width and pick it up again around one graphic. Words of
    reads judged content are added to ``known``. The shot is content when most of its reads are.
    """
    from .screen_text import content_tokens, text_lines, words_outside

    verdicts: List[Optional[str]] = []
    for words in reads:
        words = _letter_words(words)
        lines = [line for line in text_lines(words) if len(line) >= 2]
        counted = [word for line in lines for word in line]
        # A block is made of lines of text: three words or more each. A chapter card's "THE COPS" over a
        # slogan, a menu's column of one- and two-word entries and a HUD's stacked counters are not.
        blocks = text_blocks([line for line in lines if len(line) >= BLOCK_LINE_MIN_WORDS])
        block = max(blocks, key=lambda b: sum(len(line) for line in b), default=[])
        in_block = [word for line in block for word in line]
        lost = len(words_outside(counted, left, right))
        block_lost = len(words_outside(in_block, left, right))
        if len(counted) >= PAGE_MIN_WORDS and lost >= PAGE_MIN_LOST_WORDS and len(block) >= PAGE_MIN_BLOCK_LINES:
            verdicts.append("page")
        elif len(block) >= CARD_MIN_LINES and len(in_block) >= CARD_MIN_WORDS and block_lost >= CARD_MIN_LOST_WORDS:
            verdicts.append("card")
        elif known and lost >= 1 and len(content_tokens(counted) & known) >= SAME_CARD_WORDS:
            verdicts.append("card")
        else:
            verdicts.append(None)
    found = [verdict for verdict in verdicts if verdict]
    if not found or len(found) * 2 <= len(verdicts):
        return None
    if known is not None:
        for words, verdict in zip(reads, verdicts):
            if verdict:
                known |= content_tokens(words)
    return Counter(found).most_common(1)[0][0]


def _content_without_reading(shot: Sequence[SceneEvidence], reason: str) -> Optional[str]:
    """Content judged from shapes alone, for when the frame cannot be read (no OCR on this machine).

    Shapes mistake brick courses and shelves for paragraphs, so only a picture that does not move counts:
    a slide, a document, a chart. Moving footage fills the frame.
    """
    if not _still(shot):
        return None
    if _rate(shot, lambda s: s.text_block_lines >= TEXT_BLOCK_STILL_LINES) > CONTENT_MIN_RATE:
        return "text_block"
    if reason == "screen_content_outside_crop":
        return reason
    faceless = _rate(shot, lambda s: s.face_count == 0) >= STILL_FACELESS_RATE
    if faceless and _rate(shot, lambda s: s.cut_text_lines >= 1) >= STILL_CUT_TEXT_RATE:
        return "labelled_still"
    return None


def content_reason(
    shot: Sequence[SceneEvidence], reason: str, start: float, end: float, x: float, crop_fraction: float,
    read_words: Optional[WordReader] = None, known: Optional[set] = None,
) -> Optional[str]:
    """Why a shot keeps the whole frame, or None when it fills it.

    ``read_words(time)`` returns the words on the frame at ``time`` (``screen_text.FrameReader``), or None
    when it cannot read it. Shots without a hint of text are not read at all. ``known`` carries the words of
    content already found in the clip from shot to shot (``content_from_words``).
    """
    if not shot or not _may_hold_text(shot):
        return None
    close = _rate(shot, lambda s: s.face_count >= 1 and s.face_area >= CLOSE_FACE_AREA
                  and 0.18 <= s.face_center <= 0.82) >= CLOSE_FACE_RATE
    if read_words is None:
        # Shapes cannot tell a poster behind a person from a page: a person talking to the camera is the shot.
        if close and reason != "screen_content_outside_crop":
            return None
        return _content_without_reading(shot, reason)
    span = end - start
    moments = [start + span / 2] if span < 1.5 else [start + span * share for share in (0.2, 0.5, 0.8)]
    reads = [read_words(moment) for moment in moments]
    if any(words is None for words in reads):
        if close and reason != "screen_content_outside_crop":
            return None
        return _content_without_reading(shot, reason)
    half = crop_fraction / 2
    centre = min(max(x, half), 1 - half)
    return content_from_words(reads, centre - half, centre + half, known)


def _same_layout(a: dict, b: dict) -> bool:
    if a["kind"] != b["kind"] or a["reason"] != b["reason"]:
        return False
    return a["kind"] != "cover" or abs(a["x"] - b["x"]) <= COVER_SAME_X


def _merged(plan: List[dict]) -> List[dict]:
    merged = [dict(plan[0])]
    for entry in plan[1:]:
        last = merged[-1]
        if _same_layout(entry, last):
            last["end"] = entry["end"]
        else:
            merged.append(dict(entry))
    return merged


def stabilize_layout_plan(plan: list[dict]) -> list[dict]:
    """Keep a clip from dropping into a small strip for a moment, or out of one.

    A fitted run shorter than LAYOUT_HOLD_SECONDS beside a frame-filling shot fills the frame as well (its own
    full-height crop): nobody reads a document in that time, and the jump to a third of the screen and back is
    what a viewer notices. A frame-filling run shorter than LAYOUT_FLASH_SECONDS between two fitted shots is a
    flash and stays fitted. Nothing here ever turns a frame-filling shot of any length into a fit, and a clip
    is never letterboxed as a whole because parts of it are.
    """
    plan = [dict(p) for p in plan]
    if len(plan) < 2:
        return plan

    def runs() -> List[Tuple[int, int]]:
        out, first = [], 0
        for i in range(1, len(plan) + 1):
            if i == len(plan) or (plan[i]["kind"] == "fit") != (plan[first]["kind"] == "fit"):
                out.append((first, i))
                first = i
        return out

    while True:
        groups = runs()
        if len(groups) < 2:
            break
        short = None
        for index, (a, b) in enumerate(groups):
            length = plan[b - 1]["end"] - plan[a]["start"]
            fitted = plan[a]["kind"] == "fit"
            if fitted and length < LAYOUT_HOLD_SECONDS:
                short = (a, b, True)
                break
            if not fitted and length < LAYOUT_FLASH_SECONDS and 0 < index < len(groups) - 1:
                short = (a, b, False)
                break
        if short is None:
            break
        a, b, fitted = short
        for entry in plan[a:b]:
            if fitted:
                entry.update(kind="cover", reason="brief_content", x=float(entry.get("fill_x", 0.5)))
            else:
                neighbour = plan[a - 1]
                entry.update(kind="fit", reason=neighbour["reason"])
                entry.pop("x", None)
        plan = _merged(plan)
    return plan


def build_context_layout_plan(samples: Sequence[SceneEvidence], scene_cuts: Sequence[float],
                              duration: float, crop_fraction: float,
                              read_words: Optional[WordReader] = None) -> list[dict]:
    """Choose one layout per shot. A shot fills the frame unless it is content.

    - ``face``: a confident subject; the tracked crop follows it. A brief missed face in an otherwise
      confident close-up does not change the layout.
    - ``cover``: anything else that is footage (no close subject, a group, a wide shot, b-roll, gameplay,
      a subtitled scene): a full-height crop centred at ``x`` (``fill_x``).
    - ``fit``: content, where the full width is the point (``content_reason``): the whole frame on a
      blurred canvas. Only this kind shows the picture small, and only for the shots that are content.

    ``read_words(time)`` reads the words on a frame; without it only a still picture can be content. Every
    entry carries ``fill_x``, the crop it would get if it filled the frame.
    """
    if duration <= 0:
        return [{"start": 0.0, "end": 0.0, "kind": "cover", "reason": "analysis_unavailable", "x": 0.5, "fill_x": 0.5}]
    if not samples:
        # Nothing is known about the picture. The centre of a 16:9 frame is where its subject usually is.
        return [{"start": 0.0, "end": duration, "kind": "cover", "reason": "analysis_unavailable",
                 "x": 0.5, "fill_x": 0.5}]
    cuts = sorted({float(c) for c in scene_cuts if 0 < c < duration})
    boundaries = [0.0, *cuts, duration]
    shots = []
    for start, end in zip(boundaries, boundaries[1:]):
        shot = [s for s in samples if start <= s.time < end]
        risks = [reason for reason in (context_reason(s, crop_fraction) for s in shot) if reason is not None]
        # Require recurrent evidence, but respect a short shot with only one
        # available sample. This is a fraction of samples, not a speed threshold.
        risky = bool(shot) and len(risks) / len(shot) >= 0.3
        reason = Counter(risks).most_common(1)[0][0] if risky else "confident_subject"
        shots.append({"start": start, "end": end, "shot": shot, "risky": risky, "reason": reason,
                      "x": fill_x(shot), "content": None})
    known: set = set()
    # Shots that are not a confident face crop are read first. Once the clip is known to show content, every
    # shot that hints at text is read again against it: the same card behind a close-up keeps the full width.
    for again in (False, True):
        if again and not known:
            break
        for entry in shots:
            if entry["content"] or not entry["shot"] or not (entry["risky"] or again):
                continue
            entry["content"] = content_reason(
                entry["shot"], entry["reason"], entry["start"], entry["end"], entry["x"], crop_fraction,
                read_words, known)
    plan: List[dict] = []
    for item in shots:
        start, end, x = item["start"], item["end"], item["x"]
        if item["content"]:
            entry = {"start": start, "end": end, "kind": "fit", "reason": item["content"]}
        elif not item["shot"]:
            entry = {"start": start, "end": end, "kind": "cover", "reason": "analysis_unavailable", "x": x}
        elif not item["risky"]:
            entry = {"start": start, "end": end, "kind": "face", "reason": "confident_subject"}
        elif item["reason"] in TEXT_ONLY_REASONS and _one_close_face(item["shot"]):
            # The only doubt was text the crop would cut, and it was not content (posters behind a speaker,
            # a subtitle under them): a person talking to the camera, followed like any other.
            entry = {"start": start, "end": end, "kind": "face", "reason": "confident_subject"}
        else:
            entry = {"start": start, "end": end, "kind": "cover", "reason": item["reason"], "x": x}
        entry["fill_x"] = x
        if plan and _same_layout(plan[-1], entry):
            plan[-1]["end"] = end
        else:
            plan.append(entry)
    try:
        return stabilize_layout_plan(plan)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Layout hysteresis failed (%s); keeping per-shot layout", exc)
        return plan
