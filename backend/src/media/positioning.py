"""Layout-aware and content-aware positioning engine for hook titles and captions.

Centralized engine that computes collision-free, layout-aware coordinates for:
1. Hook Titles:
   - Split-screen: places in top or bottom panel, never crossing the seam.
   - Tight close-up: clears forehead, placing in headroom or beneath chin.
   - Gameplay: avoids timers, money counters, stream chat, game titles ("HOUR 99")
     and never crosses webcam/gameplay boundaries.
2. Captions:
   - Split-screen: placed in safe area below upper face and above lower face,
     avoiding the seam and avoiding overlapping the bottom face.
   - Gameplay: avoids player character, vehicles, menus, and HUD cutouts.
   - Fit layout: positioned strictly inside actual source video boundaries,
     never on the blurred background.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Geometry Data Structures
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Rect:
    """Normalized rectangle [0, 1] relative to canvas dimensions."""
    x: float
    y: float
    w: float
    h: float

    @property
    def x2(self) -> float:
        return self.x + self.w

    @property
    def y2(self) -> float:
        return self.y + self.h

    @property
    def cx(self) -> float:
        return self.x + self.w / 2.0

    @property
    def cy(self) -> float:
        return self.y + self.h / 2.0

    @property
    def area(self) -> float:
        return max(0.0, self.w) * max(0.0, self.h)

    def overlap_area(self, other: "Rect") -> float:
        ix1 = max(self.x, other.x)
        iy1 = max(self.y, other.y)
        ix2 = min(self.x2, other.x2)
        iy2 = min(self.y2, other.y2)
        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0
        return (ix2 - ix1) * (iy2 - iy1)

    def intersects(self, other: "Rect") -> bool:
        return self.overlap_area(other) > 1e-6

    def distance_to(self, other: "Rect") -> float:
        """Euclidean distance between rectangle edges (0 if intersecting)."""
        dx = max(0.0, max(self.x - other.x2, other.x - self.x2))
        dy = max(0.0, max(self.y - other.y2, other.y - self.y2))
        return (dx * dx + dy * dy) ** 0.5

    def to_pixels(self, width: int, height: int) -> Tuple[int, int, int, int]:
        return (
            int(round(self.x * width)),
            int(round(self.y * height)),
            max(1, int(round(self.w * width))),
            max(1, int(round(self.h * height))),
        )

    @classmethod
    def from_pixels(cls, px: float, py: float, pw: float, ph: float, width: int, height: int) -> "Rect":
        w_f = float(width) if width > 0 else 1.0
        h_f = float(height) if height > 0 else 1.0
        return cls(
            x=max(0.0, min(1.0, px / w_f)),
            y=max(0.0, min(1.0, py / h_f)),
            w=max(0.0, min(1.0, pw / w_f)),
            h=max(0.0, min(1.0, ph / h_f)),
        )


class LayoutType(str, Enum):
    SPLIT = "split"          # Stacked pair, call, or comparison split
    CLOSE_UP = "close_up"    # Single presenter head-and-shoulders tight crop
    GAMEPLAY = "gameplay"    # Stream with facecam + game panel, or gameplay only
    FIT = "fit"              # Letterboxed/pillarboxed video on blurred fill
    REACTION = "reaction"    # Inset reaction webcam over source video
    DEFAULT = "default"      # Standard framing


class TargetType(str, Enum):
    HOOK_TITLE = "hook_title"
    CAPTION = "caption"


@dataclass
class PlacementResult:
    """Calculated position and metadata for title or captions."""
    x: float                     # Normalized center X [0, 1]
    y: float                     # Normalized center Y [0, 1]
    alignment: int               # ASS alignment: 2=bottom-center, 5=middle, 8=top-center
    pixel_x: int                 # Absolute pixel X
    pixel_y: int                 # Absolute pixel Y
    safe_box: Rect               # Normalized bounding box occupied by the text
    selected_region: str         # Region identifier
    confidence: float            # 0.0 to 1.0
    reason: str                  # Diagnostic explanation for the choice
    score: float                 # Placement scoring value


# ---------------------------------------------------------------------------
# Visual Detection Utilities (OCR Text, HUD, Faces)
# ---------------------------------------------------------------------------

def detect_on_screen_text_boxes(
    frame: np.ndarray,
    min_confidence: float = 0.5,
    detect_light_on_dark: bool = True,
    detect_dark_on_light: bool = True,
) -> List[Rect]:
    """Detect text boxes (timers, money counters, subtitles, HUD text) in a video frame.

    Uses fast morphological text line / component clustering with OpenCV,
    supplemented by local Tesseract OCR when available.
    """
    if frame is None or frame.size == 0:
        return []
    h, w = frame.shape[:2]
    if w <= 0 or h <= 0:
        return []

    # Downscale for fast detection if oversized
    scale = 1.0
    proc = frame
    if w > 1280:
        scale = 1280.0 / w
        proc = cv2.resize(frame, (1280, max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(proc, cv2.COLOR_BGR2GRAY) if proc.ndim == 3 else proc
    ph, pw = gray.shape

    rects: List[Rect] = []

    # Local OpenCV high-contrast text detection
    polarities = []
    if detect_light_on_dark:
        polarities.append(gray)
    if detect_dark_on_light:
        polarities.append(255 - gray)

    for img in polarities:
        # High contrast strokes
        bright = img >= 180
        dark = cv2.dilate((img <= 80).astype(np.uint8), np.ones((5, 5), np.uint8))
        strokes = (bright & (dark > 0)).astype(np.uint8)

        count, _, stats, _ = cv2.connectedComponentsWithStats(strokes)
        min_glyph, max_glyph = ph * 0.007, ph * 0.12
        glyph_mask = np.zeros_like(strokes)
        glyph_count = 0
        for idx in range(1, count):
            gx, gy, gw, gh, garea = stats[idx]
            if min_glyph <= gh <= max_glyph and gw <= gh * 4.0 and garea >= 6:
                glyph_mask[gy:gy + gh, gx:gx + gw] = 1
                glyph_count += 1

        if glyph_count < 3:
            continue

        # Close horizontally to group characters into text lines / words
        kernel = cv2.getStructuringElement(
            cv2.MORPH_RECT, (max(5, int(pw * 0.035)), max(3, int(ph * 0.008)))
        )
        closed = cv2.morphologyEx(glyph_mask, cv2.MORPH_CLOSE, kernel)
        line_count, _, line_stats, _ = cv2.connectedComponentsWithStats(closed)

        for lidx in range(1, line_count):
            lx, ly, lw, lh, larea = line_stats[lidx]
            if lh >= ph * 0.009 and lw >= pw * 0.02 and larea >= 30:
                rects.append(Rect(
                    x=float(lx) / pw,
                    y=float(ly) / ph,
                    w=float(lw) / pw,
                    h=float(lh) / ph,
                ))

    # Merge overlapping text boxes
    merged: List[Rect] = []
    for r in rects:
        expanded = False
        for i, m in enumerate(merged):
            if r.overlap_area(m) > 0.1 * min(r.area, m.area) or r.distance_to(m) < 0.015:
                # Merge
                nx1 = min(r.x, m.x)
                ny1 = min(r.y, m.y)
                nx2 = max(r.x2, m.x2)
                ny2 = max(r.y2, m.y2)
                merged[i] = Rect(nx1, ny1, nx2 - nx1, ny2 - ny1)
                expanded = True
                break
        if not expanded:
            merged.append(r)

    return merged


def sample_video_text_and_faces(
    video_path: Path,
    timestamps: Sequence[float],
    width: int,
    height: int,
    detect_faces: bool = True,
    detect_text: bool = True,
) -> Tuple[List[Rect], List[Rect]]:
    """Sample frames at timestamps and return aggregated face and text bounding boxes."""
    if not video_path.exists() or not timestamps:
        return [], []

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        return [], []

    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    faces: List[Rect] = []
    texts: List[Rect] = []

    # Optional face detector
    detector = None
    if detect_faces:
        try:
            from .face_detection import YuNetFaceDetector
            detector = YuNetFaceDetector()
        except Exception:
            detector = None

    for t in timestamps:
        frame_idx = int(round(t * fps))
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
        ret, frame = cap.read()
        if not ret or frame is None:
            continue

        fh, fw = frame.shape[:2]
        if detect_text:
            frame_texts = detect_on_screen_text_boxes(frame)
            texts.extend(frame_texts)

        if detector is not None:
            try:
                raw_faces = detector.detect(frame)
                for fbox in raw_faces:
                    fx, fy, fw_box, fh_box = fbox[:4]
                    faces.append(Rect.from_pixels(fx, fy, fw_box, fh_box, fw, fh))
            except Exception as exc:
                logger.debug("Face detect error at %.2f: %s", t, exc)

    cap.release()
    return faces, texts


# ---------------------------------------------------------------------------
# Layout Boundaries Helper
# ---------------------------------------------------------------------------

def calculate_fit_video_bounds(src_width: int, src_height: int, canvas_w: int = 1080, canvas_h: int = 1920) -> Rect:
    """Calculate the exact normalized video boundaries for a letterboxed fit layout.

    For example, a 16:9 source (1920x1080) fitted on 1080x1920:
    Width = 1080 (w=1.0)
    Height = 1080 * (1080 / 1920) = 607.5 px -> h ~ 0.3164
    Y = (1920 - 608) / 2 = 656 px -> y ~ 0.3417, y2 ~ 0.6583
    """
    if src_width <= 0 or src_height <= 0 or canvas_w <= 0 or canvas_h <= 0:
        return Rect(0.0, 0.25, 1.0, 0.5)

    src_aspect = float(src_width) / float(src_height)
    canvas_aspect = float(canvas_w) / float(canvas_h)

    if src_aspect >= canvas_aspect:
        # Width-limited (letterbox top & bottom)
        fit_w = float(canvas_w)
        fit_h = fit_w / src_aspect
        fit_x = 0.0
        fit_y = (float(canvas_h) - fit_h) / 2.0
    else:
        # Height-limited (pillarbox left & right)
        fit_h = float(canvas_h)
        fit_w = fit_h * src_aspect
        fit_y = 0.0
        fit_x = (float(canvas_w) - fit_w) / 2.0

    return Rect(
        x=fit_x / canvas_w,
        y=fit_y / canvas_h,
        w=fit_w / canvas_w,
        h=fit_h / canvas_h,
    )


# ---------------------------------------------------------------------------
# Centralized Positioning Engine
# ---------------------------------------------------------------------------

class LayoutAwarePositioner:
    """Centralized positioning engine for Hook Titles and Captions."""

    def __init__(
        self,
        canvas_width: int = 1080,
        canvas_height: int = 1920,
    ):
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height

    def position(
        self,
        target: TargetType | str,
        layout_type: LayoutType | str,
        item_width: float,
        item_height: float,
        faces: Optional[Sequence[Rect]] = None,
        ocr_boxes: Optional[Sequence[Rect]] = None,
        hud_boxes: Optional[Sequence[Rect]] = None,
        split_seams: Optional[Sequence[float]] = None,
        content_box: Optional[Rect] = None,
        timestamp: float = 0.0,
        shot_info: Optional[Dict[str, Any]] = None,
    ) -> PlacementResult:
        """Calculate optimal layout-aware coordinates for title or caption."""
        target_enum = TargetType(target)
        layout_enum = LayoutType(layout_type)

        norm_item_w = min(1.0, max(0.1, item_width if item_width <= 1.0 else item_width / self.canvas_width))
        norm_item_h = min(1.0, max(0.02, item_height if item_height <= 1.0 else item_height / self.canvas_height))

        known_faces = list(faces or [])
        known_ocr = list(ocr_boxes or [])
        known_hud = list(hud_boxes or [])
        seams = list(split_seams or ([0.5] if layout_enum == LayoutType.SPLIT else []))

        if target_enum == TargetType.HOOK_TITLE:
            return self._position_hook_title(
                layout_enum=layout_enum,
                item_w=norm_item_w,
                item_h=norm_item_h,
                faces=known_faces,
                ocr_boxes=known_ocr,
                hud_boxes=known_hud,
                seams=seams,
                content_box=content_box,
                shot_info=shot_info,
            )
        else:
            return self._position_caption(
                layout_enum=layout_enum,
                item_w=norm_item_w,
                item_h=norm_item_h,
                faces=known_faces,
                ocr_boxes=known_ocr,
                hud_boxes=known_hud,
                seams=seams,
                content_box=content_box,
                shot_info=shot_info,
            )

    # -----------------------------------------------------------------------
    # Hook Title Rules & Scoring
    # -----------------------------------------------------------------------

    def _position_hook_title(
        self,
        layout_enum: LayoutType,
        item_w: float,
        item_h: float,
        faces: List[Rect],
        ocr_boxes: List[Rect],
        hud_boxes: List[Rect],
        seams: List[float],
        content_box: Optional[Rect],
        shot_info: Optional[Dict[str, Any]],
    ) -> PlacementResult:
        """Position the hook title (first 3-4s)."""
        candidates: List[Tuple[float, float, str]] = []  # (cx, cy, region_name)

        safe_pad_x = 0.06
        safe_pad_y = 0.04

        if layout_enum == LayoutType.SPLIT:
            # Bug A1: Split-screen title rendered across seam (0.5), covering chin/chest.
            # Fix: Keep title strictly inside upper panel or bottom panel, NEVER cross seam.
            seam_y = seams[0] if seams else 0.5
            seam_gap = 0.03

            # Top panel candidates (must stay strictly above seam - seam_gap)
            top_bound = seam_y - seam_gap
            # Candidate 1: Upper safe margin of top panel (Alignment 8 / top center)
            c1_y = 0.06 + item_h / 2.0
            if c1_y + item_h / 2.0 < top_bound:
                candidates.append((0.5, c1_y, "top_panel_upper"))

            # Candidate 2: Beneath top face, but above the seam
            top_faces = [f for f in faces if f.cy < seam_y]
            if top_faces:
                lowest_top_face = max(top_faces, key=lambda f: f.y2)
                beneath_top_y = lowest_top_face.y2 + 0.02 + item_h / 2.0
                if beneath_top_y + item_h / 2.0 < top_bound:
                    candidates.append((0.5, beneath_top_y, "top_panel_below_face"))

            # Candidate 3: In bottom panel (if top panel is crowded)
            bottom_start = seam_y + seam_gap
            bottom_faces = [f for f in faces if f.cy >= seam_y]
            if bottom_faces:
                lowest_bottom_face = max(bottom_faces, key=lambda f: f.y2)
                beneath_bottom_y = lowest_bottom_face.y2 + 0.02 + item_h / 2.0
                if beneath_bottom_y + item_h / 2.0 <= 0.94:
                    candidates.append((0.5, beneath_bottom_y, "bottom_panel_below_face"))
            else:
                c_bot_y = bottom_start + 0.05 + item_h / 2.0
                if c_bot_y + item_h / 2.0 <= 0.94:
                    candidates.append((0.5, c_bot_y, "bottom_panel"))

        elif layout_enum == LayoutType.CLOSE_UP:
            # Bug A2: Tight close-up title covers forehead (around y=0.12).
            # Fix: If face is in upper third, place title in safe headroom (if available)
            # or beneath chin / mid-chest.
            close_face = faces[0] if faces else None
            if close_face is not None:
                # Check headroom above forehead
                headroom = close_face.y
                if headroom >= item_h + 0.06:
                    # Safe headroom exists above head
                    top_headroom_y = max(0.04 + item_h / 2.0, headroom / 2.0)
                    candidates.append((0.5, top_headroom_y, "headroom_above_face"))

                # Beneath chin / mid-chest
                beneath_chin_y = close_face.y2 + 0.03 + item_h / 2.0
                # Must stay above lower caption area (<= 0.65)
                if beneath_chin_y + item_h / 2.0 <= 0.68:
                    candidates.append((0.5, beneath_chin_y, "below_chin"))

                # Fallback lower chest
                lower_chest_y = min(0.64, max(0.48, close_face.y2 + 0.08))
                candidates.append((0.5, lower_chest_y, "mid_chest"))
            else:
                candidates.append((0.5, 0.12, "top_safe"))
                candidates.append((0.5, 0.52, "mid_body"))

        elif layout_enum == LayoutType.GAMEPLAY:
            # Bug A3 & A4: Overlaps existing source text (timers, money, "HOUR 99")
            # and crosses boundary between webcam and gameplay.
            cam_seam = seams[0] if seams else (
                (shot_info.get("cam_h", 0) / float(self.canvas_height)) if shot_info and shot_info.get("cam_h") else None
            )

            if cam_seam and cam_seam > 0.1:
                # Webcam present at top: NEVER cross cam_seam!
                # Candidate: In gameplay panel, safely below webcam seam
                below_cam_y = cam_seam + 0.03 + item_h / 2.0
                candidates.append((0.5, below_cam_y, "gameplay_below_webcam"))

                # Candidate: Inside webcam panel if clear of face
                cam_faces = [f for f in faces if f.cy < cam_seam]
                if not cam_faces and cam_seam >= item_h + 0.06:
                    candidates.append((0.5, cam_seam / 2.0, "inside_webcam"))
            else:
                # Pure gameplay: test clear vertical bands
                candidates.extend([
                    (0.5, 0.12, "gameplay_top"),
                    (0.5, 0.22, "gameplay_upper_mid"),
                    (0.5, 0.35, "gameplay_center_mid"),
                    (0.5, 0.58, "gameplay_lower_mid"),
                ])

        elif layout_enum == LayoutType.FIT:
            # Must stay inside actual video bounds
            bounds = content_box or calculate_fit_video_bounds(
                shot_info.get("src_w", 1920) if shot_info else 1920,
                shot_info.get("src_h", 1080) if shot_info else 1080,
                self.canvas_width, self.canvas_height
            )
            # Top inside actual video
            fit_top_y = bounds.y + 0.03 + item_h / 2.0
            if fit_top_y + item_h / 2.0 < bounds.y2:
                candidates.append((0.5, fit_top_y, "fit_video_top"))
            # Center inside actual video
            fit_mid_y = bounds.cy
            candidates.append((0.5, fit_mid_y, "fit_video_center"))

        else:
            # Default framing
            candidates.extend([
                (0.5, 0.12, "top_default"),
                (0.5, 0.50, "center_default"),
            ])

        # Evaluate and score candidates
        best_candidate = self._score_candidates(
            candidates=candidates,
            item_w=item_w,
            item_h=item_h,
            faces=faces,
            ocr_boxes=ocr_boxes,
            hud_boxes=hud_boxes,
            seams=seams,
            content_box=content_box,
            target_type=TargetType.HOOK_TITLE,
            layout_type=layout_enum,
        )

        return best_candidate

    # -----------------------------------------------------------------------
    # Caption Rules & Scoring
    # -----------------------------------------------------------------------

    def _position_caption(
        self,
        layout_enum: LayoutType,
        item_w: float,
        item_h: float,
        faces: List[Rect],
        ocr_boxes: List[Rect],
        hud_boxes: List[Rect],
        seams: List[float],
        content_box: Optional[Rect],
        shot_info: Optional[Dict[str, Any]],
    ) -> PlacementResult:
        """Position speech captions."""
        candidates: List[Tuple[float, float, str]] = []

        if layout_enum == LayoutType.SPLIT:
            # Bug B1: Split-screen captions appear at 81% and overlap bottom face.
            # Fix: Place captions in safe area below upper face and above lower face,
            # avoiding the seam itself.
            seam_y = seams[0] if seams else 0.5
            seam_gap = 0.02

            top_faces = [f for f in faces if f.cy < seam_y]
            bottom_faces = [f for f in faces if f.cy >= seam_y]

            upper_chin = max((f.y2 for f in top_faces), default=seam_y * 0.6)
            lower_head = min((f.y for f in bottom_faces), default=seam_y + 0.25)

            # Candidate 1: Below upper face, just above the seam (e.g. y = 0.44 - 0.46)
            above_seam_y = seam_y - seam_gap - item_h / 2.0
            if above_seam_y - item_h / 2.0 >= upper_chin + 0.01:
                candidates.append((0.5, above_seam_y, "between_faces_above_seam"))

            # Candidate 2: Just below seam, above lower face forehead (e.g. y = 0.53 - 0.56)
            below_seam_y = seam_y + seam_gap + item_h / 2.0
            if below_seam_y + item_h / 2.0 <= lower_head - 0.01:
                candidates.append((0.5, below_seam_y, "between_faces_below_seam"))

            # Candidate 3: Lower panel, strictly below bottom face
            if bottom_faces:
                lowest_bottom_chin = max(f.y2 for f in bottom_faces)
                below_lower_face_y = lowest_bottom_chin + 0.03 + item_h / 2.0
                if below_lower_face_y + item_h / 2.0 <= 0.88:
                    candidates.append((0.5, below_lower_face_y, "below_lower_face"))
            else:
                candidates.append((0.5, 0.82, "bottom_panel_safe"))

            # Fallback candidate in safe mid-gap
            mid_gap_y = (upper_chin + lower_head) / 2.0
            if abs(mid_gap_y - seam_y) > seam_gap:
                candidates.append((0.5, mid_gap_y, "safe_face_gap"))

        elif layout_enum == LayoutType.FIT:
            # Bug B3: Fit-layout captions appear on blurred background 130-190px below actual video (y=0.80).
            # Fix: Position inside actual video bounds, respecting visible letterbox boundaries.
            bounds = content_box or calculate_fit_video_bounds(
                shot_info.get("src_w", 1920) if shot_info else 1920,
                shot_info.get("src_h", 1080) if shot_info else 1080,
                self.canvas_width, self.canvas_height
            )
            # Place near bottom of the actual video rectangle, inside content
            # Leave safe margin (0.02) from bottom edge of video
            inside_bottom_y = bounds.y2 - 0.02 - item_h / 2.0
            if inside_bottom_y - item_h / 2.0 >= bounds.y:
                candidates.append((0.5, inside_bottom_y, "inside_video_bottom"))

            # Slightly higher inside video (if subtitle area at bottom has source text)
            inside_lower_mid_y = bounds.y2 - 0.08 - item_h / 2.0
            if inside_lower_mid_y - item_h / 2.0 >= bounds.y:
                candidates.append((0.5, inside_lower_mid_y, "inside_video_lower_mid"))

            # Inside center of video as fallback
            candidates.append((0.5, bounds.cy, "inside_video_center"))

        elif layout_enum == LayoutType.GAMEPLAY:
            # Bug B2: Gameplay captions overlap player character, vehicles or menu rows.
            # Avoid center action zone (x: 0.3..0.7, y: 0.44..0.68) and HUD cutouts
            gameplay_rows = [0.82, 0.86, 0.76, 0.72, 0.68, 0.38]
            for row in gameplay_rows:
                candidates.append((0.5, row, f"gameplay_row_{int(row * 100)}"))

        elif layout_enum == LayoutType.CLOSE_UP:
            # Close up: place below chin / mid-chest
            close_face = faces[0] if faces else None
            if close_face:
                below_chin = close_face.y2 + 0.04 + item_h / 2.0
                target_y = min(0.82, max(0.65, below_chin))
                candidates.append((0.5, target_y, "below_subject_chin"))
            else:
                candidates.append((0.5, 0.80, "standard_bottom"))

        else:
            # Default
            candidates.extend([
                (0.5, 0.80, "standard_bottom"),
                (0.5, 0.72, "higher_bottom"),
            ])

        best_candidate = self._score_candidates(
            candidates=candidates,
            item_w=item_w,
            item_h=item_h,
            faces=faces,
            ocr_boxes=ocr_boxes,
            hud_boxes=hud_boxes,
            seams=seams,
            content_box=content_box,
            target_type=TargetType.CAPTION,
            layout_type=layout_enum,
        )

        return best_candidate

    # -----------------------------------------------------------------------
    # Collision Detection and Scoring Engine
    # -----------------------------------------------------------------------

    def _score_candidates(
        self,
        candidates: List[Tuple[float, float, str]],
        item_w: float,
        item_h: float,
        faces: List[Rect],
        ocr_boxes: List[Rect],
        hud_boxes: List[Rect],
        seams: List[float],
        content_box: Optional[Rect],
        target_type: TargetType,
        layout_type: LayoutType,
    ) -> PlacementResult:
        """Score each candidate position and return the highest-scoring valid placement."""
        if not candidates:
            # Fallback default
            default_y = 0.12 if target_type == TargetType.HOOK_TITLE else 0.80
            candidates = [(0.5, default_y, "emergency_fallback")]

        best_score = -float("inf")
        best_cand: Tuple[float, float, str] = candidates[0]
        best_box = Rect(0.5 - item_w / 2.0, candidates[0][1] - item_h / 2.0, item_w, item_h)
        best_reason = candidates[0][2]

        for cx, cy, region_name in candidates:
            box = Rect(
                x=max(0.01, min(0.99 - item_w, cx - item_w / 2.0)),
                y=max(0.01, min(0.99 - item_h, cy - item_h / 2.0)),
                w=item_w,
                h=item_h,
            )

            score = 100.0
            reasons = [f"Region: {region_name}"]

            # 1. Face Collision Penalty
            for face in faces:
                overlap = box.overlap_area(face)
                if overlap > 0:
                    ratio = overlap / max(1e-5, min(box.area, face.area))
                    # Severe penalty for covering face
                    penalty = 2000.0 * ratio
                    score -= penalty
                    reasons.append(f"Face collision (-{penalty:.0f})")
                else:
                    # Clearance bonus
                    dist = box.distance_to(face)
                    if dist > 0.04:
                        score += 30.0

            # 2. OCR Text Collision Penalty
            for ocr in ocr_boxes:
                overlap = box.overlap_area(ocr)
                if overlap > 0:
                    ratio = overlap / max(1e-5, min(box.area, ocr.area))
                    penalty = 1200.0 * ratio
                    score -= penalty
                    reasons.append(f"OCR text collision (-{penalty:.0f})")

            # 3. Gameplay HUD Collision Penalty
            for hud in hud_boxes:
                overlap = box.overlap_area(hud)
                if overlap > 0:
                    ratio = overlap / max(1e-5, min(box.area, hud.area))
                    penalty = 1000.0 * ratio
                    score -= penalty
                    reasons.append(f"HUD collision (-{penalty:.0f})")

            # 4. Split-Screen Seam Crossing Penalty
            # NEVER allow text to straddle the seam boundary!
            for seam in seams:
                if box.y < seam < box.y2:
                    score -= 3000.0
                    reasons.append(f"Seam crossing at {seam:.2f} (-3000)")
                elif abs(box.cy - seam) < 0.025:
                    score -= 800.0
                    reasons.append("Too close to seam (-800)")

            # 5. Fit Layout Content Boundaries
            if layout_type == LayoutType.FIT and content_box is not None:
                # Must be fully contained inside content_box
                # Any part outside content_box lands on blurred background!
                if box.y < content_box.y - 1e-4 or box.y2 > content_box.y2 + 1e-4:
                    out_top = max(0.0, content_box.y - box.y)
                    out_bot = max(0.0, box.y2 - content_box.y2)
                    penalty = 2500.0 * (out_top + out_bot) / box.h
                    score -= penalty
                    reasons.append(f"Outside fit video bounds (-{penalty:.0f})")
                else:
                    score += 50.0  # Bonus for clean placement inside content

            # 6. Center Action Zone Penalty for Gameplay
            if layout_type == LayoutType.GAMEPLAY:
                center_action = Rect(0.28, 0.44, 0.44, 0.22)
                act_overlap = box.overlap_area(center_action)
                if act_overlap > 0:
                    penalty = 500.0 * (act_overlap / box.area)
                    score -= penalty
                    reasons.append("Center action zone (-500)")

            # 7. Safe margin preference
            if box.x < 0.05 or box.x2 > 0.95:
                score -= 200.0
            if box.y < 0.04 or box.y2 > 0.96:
                score -= 300.0

            # Target-specific preference bonuses
            if target_type == TargetType.HOOK_TITLE:
                # Prefer upper placement when safe
                if box.cy < 0.45:
                    score += 40.0
            elif target_type == TargetType.CAPTION:
                # Prefer lower placement when safe
                if 0.55 <= box.cy <= 0.86:
                    score += 40.0

            if score > best_score:
                best_score = score
                best_cand = (cx, cy, region_name)
                best_box = box
                best_reason = "; ".join(reasons)

        final_cx, final_cy, final_region = best_cand
        conf = max(0.1, min(1.0, (best_score + 1000.0) / 1200.0))

        # Alignment: default 5 (center) for titles or explicit center positions,
        # 2 (bottom-center) for standard captions
        alignment = 5
        if target_type == TargetType.HOOK_TITLE and final_region.endswith("upper"):
            alignment = 8

        return PlacementResult(
            x=final_cx,
            y=final_cy,
            alignment=alignment,
            pixel_x=int(round(final_cx * self.canvas_width)),
            pixel_y=int(round(final_cy * self.canvas_height)),
            safe_box=best_box,
            selected_region=final_region,
            confidence=round(conf, 3),
            reason=best_reason,
            score=round(best_score, 2),
        )


# Global singleton instance
_GLOBAL_POSITIONER = LayoutAwarePositioner()


def get_positioner(width: int = 1080, height: int = 1920) -> LayoutAwarePositioner:
    """Return a positioner sized for the canvas."""
    if width == 1080 and height == 1920:
        return _GLOBAL_POSITIONER
    return LayoutAwarePositioner(canvas_width=width, canvas_height=height)

