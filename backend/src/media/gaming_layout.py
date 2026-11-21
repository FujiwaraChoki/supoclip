"""Gaming mode: a vertical layout for gameplay footage (docs/gaming-mode.md).

A gameplay recording is recognised by what a game draws on top of its world: HUD
elements that stay in place while almost every other pixel changes. The streamer's
camera is a fixed rectangle with a face in it. From those the clip is composed as
facecam panel (top) over a gameplay panel cropped to the middle of the screen, with
the HUD elements the crop would lose cut out and placed back on the panel edges.

Everything here is deterministic computer vision on ~90 small frames of the clip.
``choose_gaming_layout`` returns None whenever the evidence is weak; the caller then
keeps the layout it would have used anyway. ``GAMING_LAYOUT=off`` (the default)
never reaches the analysis.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import logging
import os
import subprocess

import cv2
import numpy as np

from .common import HOOK_TITLE_SECONDS, UPSCALE_SHARPEN_FILTER
from .ffmpeg import clamp_even, round_to_even

logger = logging.getLogger(__name__)

Box = Tuple[float, float, float, float]

GAMING_LAYOUT_ENV = "GAMING_LAYOUT"
GAMING_LAYOUT_MODES = ("off", "auto")

CANVAS_W, CANVAS_H = 1080, 1920

# --- Sampling ---------------------------------------------------------------
PROC_WIDTH = 960
MAX_SAMPLES = 90
MAX_SAMPLE_FPS = 3.0
MIN_SAMPLES = 16

# --- Evidence (all on the PROC_WIDTH frame) -----------------------------------
EDGE_MAGNITUDE = 90.0          # Sobel magnitude that counts as an edge
LINE_MAGNITUDE = 60.0          # one-direction Sobel response for straight borders
CHANGE_DIFF = 6.0              # grey-level change between two samples that counts as "changed"
HUD_PERSISTENCE = 0.6          # an edge present in this share of samples is drawn by the game or the stream
LINE_PERSISTENCE = 0.5
PATIENT_LINE_PERSISTENCE = 0.35  # second look for a camera border, see find_facecam_rect
PATIENT_MAX_FACE_WIDTHS = 13     # ... and the widest camera it accepts, in face widths (8 on the first look)
PATIENT_OUTSIDE_CHANGE = 0.2     # mean change rate around the rectangle: the game has to move
PATIENT_CALM_RATIO = 0.6         # ... and the inside changes at most this much of that
CHANGE_STEP = 0.3              # jump in the change rate across a camera border without a drawn line
MOVING_CHANGE_RATE = 0.25      # a pixel that changed in a quarter of the sample pairs belongs to moving footage

# --- Classification -----------------------------------------------------------
MIN_MOVING_FRACTION = 0.5      # measured: gameplay 0.76-0.96, podcast 0.11-0.19, screen recordings 0.0
# With a camera inset a game may stand still (a menu, the map, a phone in the character's hand): production
# clips refused as static with a camera measured 0.16-0.5. Slides and screen recordings stay below 0.1.
MIN_MOVING_FRACTION_WITH_FACECAM = 0.15
# Below that the picture stands still (a page on the character's phone, a pause menu, but also slides with a
# webcam). The camera goes on top and the part of the picture that carries detail is shown below it, whole.
STILL_DETAIL_PERSISTENCE = 0.6
STILL_FOCUS_QUANTILES = (0.02, 0.98)
STILL_FOCUS_PAD = 0.03
STILL_ACTIVITY_RATE = 0.05     # a pixel that changed in this share of sample pairs: text scrolling, a cursor
STILL_ACTIVITY_MIN_AREA = 0.004  # of the frame: less is a clock or a counter, not what the picture is about
STILL_WINDOW_ASPECT = 0.8      # width of the part the panel shows at full height, of the picture's height
STILL_SUBJECT_SHARE = 0.75     # of what changes: inside that part it is the picture's subject
STILL_SUBJECT_DETAIL = 0.7      # ... when it also holds this much of its fair share of the picture's lines
# A camera over a picture that hardly moves must show a small head: a speaker filmed in front of a window
# or a shelf has a rectangle around the face too, and a face several times larger.
CALM_PICTURE_FACE_MAX_AREA = 0.02  # camera heads measured up to 1.7 %; a speaker in a medium shot starts around 2.5 %

# Side-by-side comparisons (two games, before and after): a divider down the middle of the screen
SPLIT_LINE_BAND = 0.006        # of the width, either side of the centre
SPLIT_LINE_COVERAGE = 0.75     # of the height one straight line must cover in a sample (splits 0.8-1.0, gameplay <= 0.5)
SPLIT_MIN_FRACTION = 0.2       # of the clip
SPLIT_LINE_MAX_SPREAD = 24.0   # grey levels between the 10th and 90th percentile along the divider: it is one flat colour
SPLIT_MIN_SECONDS = 1.5
SPLIT_PANEL_H = 960            # each half fills half of the canvas
SPLIT_GAP = 0.012              # of the width, left out on either side of the centre: the divider itself
SPLIT_CROP_DOWN = 0.85         # where the crop sits between the top (0) and the bottom (1) of the picture
MIN_HUD_REGIONS = 3            # without a facecam
MIN_HUD_REGIONS_WITH_FACECAM = 1
MIN_ANCHORED_HUD_REGIONS = 2   # without a camera or a crosshair: elements that sit at the frame's edge ...
HUD_ANCHOR_REACH = 0.02        # ... within this much of it (HUD elements measure 0.3-1.4 %; map labels 2.7 % and up)
MIN_HUD_EDGE_FRACTION = 0.004  # persistent edge pixels outside the facecam, as a share of the frame
MAX_OUTSIDE_FACE_RATE = 0.25   # samples with a real face outside the facecam: a vlog, an interview, a reaction
OUTSIDE_FACE_MIN_AREA = 0.004
MIN_VALID_FRACTION = 0.7       # share of the clip in which the stream layout is the detected one
MIN_VALID_FRACTION_WITH_FACECAM = 0.35  # streams hide the camera during cutscenes; those spans fill the frame
VALID_SAMPLE_SCORE = 0.45
MIN_FALLBACK_SECONDS = 1.5
REFINE_FPS = 30
REFINE_MAX_REACH = 1.5         # seconds decoded either side of a sampled switch, whatever the clip length

# --- HUD regions ---------------------------------------------------------------
HUD_MERGE_FRAC = 0.019         # persistent edges closer than this (of the width) are one element
HUD_MIN_EDGE_PIXELS = 30
HUD_MIN_DENSITY = 0.06         # persistent edge pixels per box pixel; measured 0.07-0.44 on real HUD boxes
HUD_MAX_AREA = 0.12
HUD_MAX_W, HUD_MAX_H = 0.75, 0.5
HUD_CENTER_BOX = 0.2           # half-size of the central box that holds the world, not the HUD
HUD_PAD_FRAC = 0.006
CROSSHAIR_RADIUS = 0.03
CROSSHAIR_MAX_SIZE = 0.07

# --- Facecam -------------------------------------------------------------------
FACE_MIN_AREA, FACE_MAX_AREA = 0.0003, 0.05
FACECAM_MIN_PRESENCE = 0.5
FACECAM_SELDOM_PRESENCE = 0.12  # a face seen this seldom still marks a camera whose border is always there
FACECAM_SELDOM_BORDER = 0.7
FACE_TRACK_MIN_SAMPLES = 5     # detections before a spot counts as a recurring face
MIN_CAMERA_MOTION = 0.03       # share of the camera rectangle that moves: a logo or an avatar never does
VIRTUAL_CAMERA_WIDTH = 4.2     # a keyed-out streamer has no rectangle: one is drawn around the head, in face widths
VIRTUAL_CAMERA_ABOVE, VIRTUAL_CAMERA_BELOW = 0.7, 1.4  # ... and in face heights above and below the face
VIRTUAL_CAMERA_FACE_MAX_AREA = 0.02  # a larger still face is a person filmed, not an inset
# A face is a picture (a logo, an avatar) when hardly any of it ever changes. Measured over 30 samples: a
# streamer sitting still 0.60-1.0 of the face box, pictures 0.0-0.13. Fewer samples cannot tell a calm moment
# from a picture, and the face counts as a person.
FACE_PIXEL_CHANGE_RATE = 0.05  # a face pixel that changed in this share of the sample pairs
MIN_FACE_MOTION = 0.3          # share of the face box made of such pixels
FACE_MOTION_MIN_SAMPLES = 30
# A pictured face (channel logo, drawn avatar) over gameplay is lifted out as a sticker and set at the top.
STICKER_REACH = (3.0, 2.0, 6.0)   # how far the picture may extend from the face: sideways, up, down (face sizes)
STICKER_MAX_FACE_AREAS = 30.0     # its box, in face boxes: an avatar to the waist is about 20, a HUD panel more
STICKER_GRID = 44                 # cells along the longer side of the cut-out mask
STICKER_MAX_BOXES = 260           # mask rectangles in the filter graph; a busier mask is not used
STICKER_WIDTH = 0.2               # of the canvas
STICKER_MAX_SCALE = 1.6
STICKER_TOP, STICKER_SIDE = 0.06, 0.035   # where it sits, of the canvas height and width
STICKER_FROM_SECONDS = HOOK_TITLE_SECONDS + 0.2   # the hook title has the top of the frame until then
KEYED_FREE_FACE_AREA = 0.006   # a keyed-out head larger than this sits at the edge of the frame, see keyed_streamer
KEYED_EDGE_REACH = 0.03        # ... within this much of it (of the frame's width and height)
HOST_MIN_PRESENCE = 0.9        # someone filmed is there for the whole clip; a game character comes and goes
HOST_MIN_SIZE = 1.4            # ... and is clearly larger than the camera's face (of its height)
HOST_HUD_REGIONS = 2           # persistent elements that make a HUD; over one, a larger face is a character

# Several cameras in one clip (a compilation, a streamer who resizes the camera)
MAX_CAMERA_LAYOUTS = 4
SEGMENT_MIN_SECONDS = 2.5      # a camera on screen for less keeps the full-frame fallback
SEGMENT_MIN_RUN_SAMPLES = 3    # a shorter run of one camera between two others is a stray detection
SEGMENT_MAX_SAMPLE_FPS = 8.0
CUT_SEARCH_SECONDS = 2.5       # two cameras this close together are separated by a hard cut, found frame by frame
SEGMENT_MIN_GAIN = 0.1         # of the clip: what several layouts must add over one to be used
FACECAM_MAX_AREA = 0.16
FACECAM_SIDE_COVERAGE = 0.7
FACECAM_CLEAN_COVERAGE = 0.92
CAM_DECORATION_REACH = 0.045   # of the width: how far above or below the camera its own decorations sit
FACECAM_INSET = 4              # proc pixels trimmed from each side: drawn camera frames are a few pixels thick

# --- Layout --------------------------------------------------------------------
FACECAM_PANEL_MIN, FACECAM_PANEL_MAX = 520, 680
GAMEPLAY_ONLY_HEIGHT = 1920    # the gameplay panel when there is no facecam
HUD_VISIBLE_IN_CROP = 0.9      # a HUD element this much inside the crop needs no cut-out
HUD_VISIBLE_CENTERED = 0.5     # ... for a bar centred on the screen: its middle is what matters
HUD_SIDE_BAND = (0.36, 0.68)   # side elements at mid height are chat and notifications: left out
HUD_MAX_SCALE = 1.3
HUD_MIN_SCALE_FACTOR = 0.6
HUD_MAX_PANEL_COVER = 0.34
CROP_SHIFT_MAX = 0.1
CAPTION_BAND = 0.09
CAPTION_CANDIDATES = (0.78, 0.76, 0.80, 0.74, 0.82, 0.72, 0.84, 0.70)


def gaming_layout_mode() -> str:
    """``off`` (default) or ``auto``: the one place the switch is read."""
    value = os.getenv(GAMING_LAYOUT_ENV, "off").strip().lower()
    return value if value in GAMING_LAYOUT_MODES else "off"


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h

    def as_list(self) -> List[int]:
        return [self.x, self.y, self.w, self.h]


@dataclass
class FrameEvidence:
    """Per-pixel statistics over the sampled frames (proc resolution)."""
    persist: np.ndarray       # share of samples with an edge here
    persist_v: np.ndarray     # ... with a vertical line (horizontal gradient)
    persist_h: np.ndarray
    change: np.ndarray        # share of sample pairs in which the pixel changed
    edges: np.ndarray         # (N, h, w) bool, for per-sample checks
    times: List[float]
    centre_line: Optional[np.ndarray] = None  # (N,) share of the height a straight line down the middle covers

    @property
    def shape(self) -> Tuple[int, int]:
        return self.persist.shape


@dataclass
class GamingProfile:
    width: int
    height: int
    is_gaming: bool
    reason: str
    facecam: Optional[Rect] = None
    face: Optional[Box] = None
    hud: List[Rect] = field(default_factory=list)
    crosshair: bool = False
    moving_fraction: float = 0.0
    hud_edge_fraction: float = 0.0
    outside_face_rate: float = 0.0
    valid_fraction: float = 0.0
    fallback_ranges: List[Tuple[float, float]] = field(default_factory=list)
    check_masks: List[np.ndarray] = field(default_factory=list, repr=False)
    # A picture that stands still beside the camera: the part of it that carries detail (source pixels).
    focus: Optional[Rect] = None
    # ... and whether what changes in it sits in that part: then it fills the panel instead of being fitted whole.
    focus_fill: bool = False
    # A pictured face over the game (a channel logo, a drawn avatar), source pixels, and its cut-out mask:
    # (columns, rows, [(x, y, w, h) cells that belong to the picture]).
    sticker: Optional[Rect] = None
    sticker_mask: Optional[Tuple[int, int, List[Tuple[int, int, int, int]]]] = field(default=None, repr=False)
    # Seconds in which the screen is split down the middle (reason ``split_screen``): the halves are stacked.
    split_ranges: List[Tuple[float, float]] = field(default_factory=list)
    virtual_camera: bool = False  # the camera has no rectangle (keyed-out streamer); one was drawn around the head
    # Where this layout applies (seconds). Empty: the whole clip.
    active: List[Tuple[float, float]] = field(default_factory=list)

    def shown_seconds(self, duration: float) -> float:
        total = sum(b - a for a, b in self.active) if self.active else duration
        return max(0.0, total - sum(b - a for a, b in self.fallback_ranges))

    def summary(self) -> Dict[str, Any]:
        return {
            "is_gaming": self.is_gaming, "reason": self.reason,
            "facecam": self.facecam.as_list() if self.facecam else None,
            "hud": [r.as_list() for r in self.hud], "crosshair": self.crosshair,
            "moving_fraction": round(self.moving_fraction, 3),
            "hud_edge_fraction": round(self.hud_edge_fraction, 4),
            "outside_face_rate": round(self.outside_face_rate, 3),
            "valid_fraction": round(self.valid_fraction, 3),
            "fallback_ranges": [[round(a, 3), round(b, 3)] for a, b in self.fallback_ranges],
            "virtual_camera": self.virtual_camera,
            "focus": self.focus.as_list() if self.focus else None,
            "focus_fill": self.focus_fill,
            "sticker": self.sticker.as_list() if self.sticker else None,
            "split_ranges": [[round(a, 3), round(b, 3)] for a, b in self.split_ranges],
            "active": [[round(a, 3), round(b, 3)] for a, b in self.active],
        }


# ---------------------------------------------------------------------------
# Sampling
# ---------------------------------------------------------------------------

def sample_clip_frames(
    path: Path, width: int, height: int, duration: float,
) -> Tuple[List[float], np.ndarray]:
    """Evenly spaced BGR frames of the whole clip at PROC_WIDTH, one sequential decode."""
    proc_w = round_to_even(min(PROC_WIDTH, width))
    proc_h = round_to_even(max(2, int(round(proc_w * height / width))))
    fps = min(MAX_SAMPLE_FPS, MAX_SAMPLES / max(duration, 1e-3))
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-an", "-sn", "-i", str(path),
         "-vf", f"fps={fps:.4f},scale={proc_w}:{proc_h}",
         "-pix_fmt", "bgr24", "-f", "rawvideo", "-threads", "0", "-"],
        capture_output=True, timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError("gaming analysis decode failed")
    frame_bytes = proc_w * proc_h * 3
    count = len(result.stdout) // frame_bytes
    frames = np.frombuffer(result.stdout[:count * frame_bytes], dtype=np.uint8).reshape(count, proc_h, proc_w, 3)
    return [i / fps for i in range(count)], frames


def detect_faces(frames: np.ndarray) -> List[List[Box]]:
    from .face_detection import open_yunet_detector

    detector = open_yunet_detector()
    if detector is None:
        return [[] for _ in frames]
    try:
        return [[tuple(float(v) for v in box[:4]) for box in detector.detect(frame)] for frame in frames]
    finally:
        detector.close()


# ---------------------------------------------------------------------------
# Evidence
# ---------------------------------------------------------------------------

def centre_line_score(vertical_lines: np.ndarray, gray: Optional[np.ndarray] = None) -> float:
    """How much of the height one straight vertical line near the middle of the screen covers.

    ``vertical_lines`` is (h, w) bool: a strong horizontal gradient. The best single column counts, so texture
    that merely has edges somewhere around the middle scores low. With ``gray`` the line must also be drawn:
    one flat colour from top to bottom beside that edge. A window mullion or a pole is shaded along its length
    and scores 0.
    """
    w = vertical_lines.shape[1]
    band = max(2, int(round(w * SPLIT_LINE_BAND)))
    lo = w // 2 - band
    columns = vertical_lines[:, lo:w // 2 + band + 1].mean(axis=0)
    best = int(np.argmax(columns))
    score = float(columns[best])
    if gray is not None and score > 0:
        # The divider's body is on one side of the edge or the other.
        spreads = []
        for x in (lo + best - 2, lo + best + 2):
            if 0 <= x < w:
                low, high = np.percentile(gray[:, x], (10, 90))
                spreads.append(float(high - low))
        if not spreads or min(spreads) > SPLIT_LINE_MAX_SPREAD:
            return 0.0
    return score


def measure_frames(frames: np.ndarray, times: Sequence[float]) -> FrameEvidence:
    """Edge persistence and change rate per pixel.

    A HUD edge is in the same place in most samples; an edge of the game world is
    somewhere else in the next one. The change rate says whether the picture
    around it moves at all (a slide deck or a locked-off camera does not).
    """
    count, h, w = frames.shape[:3]
    persist = np.zeros((h, w), np.float32)
    persist_v = np.zeros((h, w), np.float32)
    persist_h = np.zeros((h, w), np.float32)
    change = np.zeros((h, w), np.float32)
    edges = np.zeros((count, h, w), bool)
    centre_line = np.zeros(count, np.float32)
    previous = None
    for i, frame in enumerate(frames):
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
        edges[i] = np.hypot(gx, gy) > EDGE_MAGNITUDE
        persist += edges[i]
        lines_v = np.abs(gx) > LINE_MAGNITUDE
        persist_v += lines_v
        centre_line[i] = centre_line_score(lines_v, gray)
        persist_h += np.abs(gy) > LINE_MAGNITUDE
        if previous is not None:
            change += np.abs(gray - previous) > CHANGE_DIFF
        previous = gray
    n = max(1, count)
    return FrameEvidence(
        persist=persist / n, persist_v=persist_v / n, persist_h=persist_h / n,
        change=change / max(1, count - 1), edges=edges, times=list(times), centre_line=centre_line,
    )


# ---------------------------------------------------------------------------
# Facecam
# ---------------------------------------------------------------------------

@dataclass
class FaceTrack:
    """One head that sits in one place: its usual box, and the samples it was seen in."""
    box: Box
    samples: List[int]
    presence: float


def fixed_face_tracks(faces: Sequence[Sequence[Box]], w: int, h: int) -> List[FaceTrack]:
    """Every face that stays in one spot at one size, most seen first.

    A streamer's camera shows one head in one spot at one size, sample after sample. The game shows faces too
    (cutscenes, photoreal characters), often larger and right beside the camera, and a compilation shows a
    different camera in every segment. A spot is a detection that repeats almost exactly; every detection then joins
    the spot it fits best, so two cameras that sit close together in different segments stay two tracks.
    """
    if not faces:
        return []
    frame_area = float(w * h)
    usable = [
        [b for b in boxes if FACE_MIN_AREA <= b[2] * b[3] / frame_area <= FACE_MAX_AREA] for boxes in faces
    ]

    def offset(box: Box, ref: np.ndarray) -> Tuple[float, float, float]:
        return (abs(box[0] + box[2] / 2 - (ref[0] + ref[2] / 2)) / max(float(ref[2]), 1.0),
                abs(box[1] + box[3] / 2 - (ref[1] + ref[3] / 2)) / max(float(ref[3]), 1.0),
                box[3] / max(float(ref[3]), 1.0))

    # Seeds: detections that repeat almost exactly, followed through the samples in order.
    seeds: List[List[Box]] = []
    seen_at: List[List[int]] = []
    for i, boxes in enumerate(usable):
        taken: set = set()
        for box in boxes:
            best, best_d = None, None
            for k, members in enumerate(seeds):
                if k in taken:
                    continue
                dx, dy, ratio = offset(box, np.median(np.asarray(members[-9:], dtype=np.float32), axis=0))
                if dx <= 0.5 and dy <= 0.5 and 0.75 <= ratio <= 1.35 and (best_d is None or dx + dy < best_d):
                    best, best_d = k, dx + dy
            if best is None:
                seeds.append([box])
                seen_at.append([i])
                taken.add(len(seeds) - 1)
            else:
                seeds[best].append(box)
                seen_at[best].append(i)
                taken.add(best)
    # A head that leans forward for a while seeds a second spot: two spots that fit each other and were seen in
    # the same stretch of the clip are one camera.
    groups: List[Tuple[List[Box], int, int]] = []
    for members, at in sorted(zip(seeds, seen_at), key=lambda pair: -len(pair[0])):
        if len(members) < 2:
            continue
        ref = np.median(np.asarray(members, dtype=np.float32), axis=0)
        for g, (boxes_g, first, last) in enumerate(groups):
            dx, dy, ratio = offset(tuple(ref), np.median(np.asarray(boxes_g, dtype=np.float32), axis=0))
            if dx <= 0.9 and dy <= 0.9 and 0.55 <= ratio <= 1.8 and at[0] <= last and at[-1] >= first:
                groups[g] = (boxes_g + members, min(first, at[0]), max(last, at[-1]))
                break
        else:
            groups.append((list(members), at[0], at[-1]))
    spots = [np.median(np.asarray(m, dtype=np.float32), axis=0) for m, _, _ in groups if len(m) >= FACE_TRACK_MIN_SAMPLES]

    # Members: every detection goes to the spot it fits best; a lean or a turn still belongs to its camera.
    open_tracks: List[List[Tuple[int, Box]]] = [[] for _ in spots]
    for i, boxes in enumerate(usable):
        chosen: Dict[int, Tuple[float, Box]] = {}
        for box in boxes:
            best, best_d = None, None
            for k, spot in enumerate(spots):
                dx, dy, ratio = offset(box, spot)
                if dx <= 0.9 and dy <= 0.9 and 0.55 <= ratio <= 1.8:
                    d = dx + dy + abs(float(np.log(ratio)))
                    if best_d is None or d < best_d:
                        best, best_d = k, d
            if best is not None and (best not in chosen or best_d < chosen[best][0]):
                chosen[best] = (best_d, box)
        for k, (_, box) in chosen.items():
            open_tracks[k].append((i, box))

    tracks: List[FaceTrack] = []
    for members in open_tracks:
        if len(members) < FACE_TRACK_MIN_SAMPLES:
            continue
        arr = np.asarray([m[1] for m in members], dtype=np.float32)
        median = np.median(arr, axis=0)
        centres = arr[:, :2] + arr[:, 2:] / 2
        # A streamer leans and turns; a face that wanders further than its own size is not behind a fixed camera.
        if np.std(centres[:, 0]) > median[2] * 0.6 or np.std(centres[:, 1]) > median[3] * 0.6:
            continue
        tracks.append(FaceTrack(tuple(float(v) for v in median), [m[0] for m in members], len(members) / len(faces)))
    tracks.sort(key=lambda t: -len(t.samples))
    return tracks


def find_fixed_face(faces: Sequence[Sequence[Box]], w: int, h: int) -> Optional[Tuple[Box, float]]:
    """The face that sits in one place for most of the clip: (median box, presence)."""
    tracks = fixed_face_tracks(faces, w, h)
    if not tracks or tracks[0].presence < FACECAM_MIN_PRESENCE:
        return None
    return tracks[0].box, tracks[0].presence


def _line_candidates(
    profile: np.ndarray, lo: int, hi: int, edge: Optional[int], nearest_to: float, keep: int = 6,
    ends: bool = False,
) -> List[int]:
    """Positions in [lo, hi) where a persistent straight line crosses, nearest first, plus the frame edge."""
    lo, hi = max(0, lo), min(len(profile), hi)
    found = []
    i = lo
    while i < hi:
        if profile[i] >= 0.8:
            j = i
            while j + 1 < hi and profile[j + 1] >= 0.8:
                j += 1
            found.append((i + j) // 2)
            if ends and j - i >= 4:
                # A thick run is a border with something drawn against it (a sign, a shelf, a frame's ornament):
                # the border itself is at one of its ends.
                found.extend((i + 1, j - 1))
            i = j + 1
        else:
            i += 1
    if edge is not None:
        found.append(edge)
    return sorted(set(found), key=lambda p: abs(p - nearest_to))[:keep]


def find_facecam_rect(evidence: FrameEvidence, face: Box, patient: bool = False) -> Optional[Rect]:
    """The camera rectangle around a fixed face, in proc pixels.

    A camera inset has four straight borders that are there in every sample
    (a drawn frame, or just the camera picture meeting the game). Each side is
    looked for as a persistent line next to the face, or the frame edge itself,
    and the rectangle is accepted only when all four sides are lines for most of
    their length.

    ``patient`` is the second look when the first finds nothing: borders that show in fewer samples (a dark
    camera edge beside a dark cutscene) and a wider camera around a small head. The caller then asks for more
    proof that the rectangle is a camera.
    """
    h, w = evidence.shape
    fx, fy, fw, fh = face
    cx, cy = fx + fw / 2, fy + fh / 2
    # A border is a line that is drawn in most samples, or the place where the picture stops moving: the room
    # behind a streamer stands still while the game beside it changes all the time, whatever their colours.
    calm = cv2.blur(evidence.change, (3, 3))
    step_v = np.zeros((h, w), bool)
    step_h = np.zeros((h, w), bool)
    step_v[:, 2:-2] = np.abs(calm[:, 4:] - calm[:, :-4]) >= CHANGE_STEP
    step_h[2:-2, :] = np.abs(calm[4:, :] - calm[:-4, :]) >= CHANGE_STEP
    persistence = PATIENT_LINE_PERSISTENCE if patient else LINE_PERSISTENCE
    vertical = cv2.dilate(((evidence.persist_v >= persistence) | step_v).astype(np.uint8), np.ones((1, 3), np.uint8))
    horizontal = cv2.dilate(((evidence.persist_h >= persistence) | step_h).astype(np.uint8), np.ones((3, 1), np.uint8))

    y0, y1 = max(0, int(fy)), min(h, int(fy + fh) + 1)
    x0, x1 = max(0, int(fx)), min(w, int(fx + fw) + 1)
    if y1 - y0 < 4 or x1 - x0 < 4:
        return None
    col_profile = vertical[y0:y1].mean(axis=0)
    row_profile = horizontal[:, x0:x1].mean(axis=1)
    reach_x, reach_y = (fw * 8.0, fh * 6.0) if patient else (fw * 5.0, fh * 4.5)
    widest = fw * (PATIENT_MAX_FACE_WIDTHS if patient else 8)
    # A neon sign or a shelf behind the streamer draws lines too; the patient look keeps more of them.
    keep = 14 if patient else 6
    lefts = _line_candidates(col_profile, int(cx - reach_x), int(fx - fw * 0.1),
                             0 if cx - reach_x <= 0 else None, fx, keep, patient)
    rights = _line_candidates(col_profile, int(fx + fw * 1.1), int(cx + reach_x),
                              w - 1 if cx + reach_x >= w - 1 else None, fx + fw, keep, patient)
    tops = _line_candidates(row_profile, int(cy - reach_y), int(fy - fh * 0.1),
                            0 if cy - reach_y <= 0 else None, fy, keep, patient)
    bottoms = _line_candidates(row_profile, int(fy + fh * 1.1), int(cy + reach_y),
                               h - 1 if cy + reach_y >= h - 1 else None, fy + fh, keep, patient)

    def side(mask: np.ndarray, at: int, a: int, b: int, is_column: bool) -> float:
        limit = (w if is_column else h) - 1
        if at <= 0 or at >= limit:
            return 1.0  # the frame edge is a border
        line = mask[a:b, at] if is_column else mask[at, a:b]
        return float(line.mean()) if line.size else 0.0

    best: Optional[Tuple[Tuple[bool, float], Rect]] = None
    for left in lefts:
        for right in rights:
            rw = right - left
            if not fw * 1.5 <= rw <= widest:
                continue
            for top in tops:
                for bottom in bottoms:
                    rh = bottom - top
                    if rh < fh * 1.4 or not 0.9 <= rw / max(1, rh) <= 2.5:
                        continue
                    if rw * rh > FACECAM_MAX_AREA * w * h:
                        continue
                    coverage = min(
                        side(vertical, left, top, bottom, True), side(vertical, right, top, bottom, True),
                        side(horizontal, top, left, right, False), side(horizontal, bottom, left, right, False),
                    )
                    if coverage < FACECAM_SIDE_COVERAGE:
                        continue
                    # The largest rectangle with four clean sides: shelves and door frames behind the streamer
                    # close smaller ones, but rarely on all four sides for their whole length.
                    score = (coverage >= FACECAM_CLEAN_COVERAGE, rw * rh if coverage >= FACECAM_CLEAN_COVERAGE else coverage)
                    if best is None or score > best[0]:
                        best = (score, Rect(left, top, rw, rh))
    return best[1] if best else None


# ---------------------------------------------------------------------------
# HUD
# ---------------------------------------------------------------------------

def find_hud_regions(evidence: FrameEvidence, exclude: Optional[Rect] = None) -> Tuple[List[Rect], bool, np.ndarray]:
    """Boxes around persistent overlay elements (proc pixels), a crosshair flag, and the edge mask used."""
    h, w = evidence.shape
    mask = (evidence.persist >= HUD_PERSISTENCE).astype(np.uint8)
    if exclude is not None:
        pad = max(2, int(w * 0.006))
        mask[max(0, exclude.y - pad):exclude.y2 + pad + 1, max(0, exclude.x - pad):exclude.x2 + pad + 1] = 0
    size = max(3, int(round(w * HUD_MERGE_FRAC)) | 1)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (size, size))
    merged = cv2.morphologyEx(cv2.dilate(mask, kernel), cv2.MORPH_CLOSE, kernel)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(merged)
    grow = size // 2
    pad = max(1, int(round(w * HUD_PAD_FRAC)))
    regions: List[Rect] = []
    crosshair = False
    for index in range(1, count):
        x, y, bw, bh, _ = (int(v) for v in stats[index])
        pixels = int(mask[labels == index].sum())
        # Undo the dilation, then leave a little air around the element.
        x, y, bw, bh = x + grow, y + grow, bw - 2 * grow, bh - 2 * grow
        if bw <= 0 or bh <= 0 or pixels <= 0:
            continue
        x, y = max(0, x), max(0, y)
        bw, bh = min(bw, w - x), min(bh, h - y)
        if pixels < HUD_MIN_DENSITY * bw * bh:
            # A loose box (a few icons far apart) would paste a slab of the game world: keep its dense part.
            inside = (labels[y:y + bh, x:x + bw] == index) & (mask[y:y + bh, x:x + bw] > 0)
            cols, rows = inside.sum(axis=0), inside.sum(axis=1)
            keep_x = np.flatnonzero(cols >= cols.max() * 0.3)
            keep_y = np.flatnonzero(rows >= rows.max() * 0.3)
            x, bw = x + int(keep_x[0]), int(keep_x[-1] - keep_x[0]) + 1
            y, bh = y + int(keep_y[0]), int(keep_y[-1] - keep_y[0]) + 1
            pixels = int(mask[y:y + bh, x:x + bw].sum())
            if pixels < HUD_MIN_DENSITY * bw * bh:
                continue
        cx, cy = (x + bw / 2) / w, (y + bh / 2) / h
        if (abs(cx - 0.5) <= CROSSHAIR_RADIUS and abs(cy - 0.5) <= CROSSHAIR_RADIUS * w / h
                and bw <= CROSSHAIR_MAX_SIZE * w and bh <= CROSSHAIR_MAX_SIZE * w):
            crosshair = crosshair or pixels >= 4
            continue
        if pixels < HUD_MIN_EDGE_PIXELS:
            continue
        if bw * bh > HUD_MAX_AREA * w * h or bw > HUD_MAX_W * w or bh > HUD_MAX_H * h:
            continue
        if abs(cx - 0.5) < HUD_CENTER_BOX and abs(cy - 0.5) < HUD_CENTER_BOX:
            continue
        x0, y0 = max(0, x - pad), max(0, y - pad)
        regions.append(Rect(x0, y0, min(w, x + bw + pad) - x0, min(h, y + bh + pad) - y0))
    regions.sort(key=lambda r: (r.y, r.x))
    return regions, crosshair, mask


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def _rect_mask(shape: Tuple[int, int], rects: Sequence[Rect]) -> np.ndarray:
    mask = np.zeros(shape, bool)
    for r in rects:
        mask[max(0, r.y):r.y2, max(0, r.x):r.x2] = True
    return mask


def _facecam_border_mask(shape: Tuple[int, int], rect: Rect) -> np.ndarray:
    mask = np.zeros(shape, bool)
    h, w = shape
    for y in (rect.y, rect.y2):
        if 0 < y < h - 1:
            mask[y - 1:y + 2, rect.x:rect.x2] = True
    for x in (rect.x, rect.x2):
        if 0 < x < w - 1:
            mask[rect.y:rect.y2, x - 1:x + 2] = True
    return mask


def layout_check_masks(
    evidence: FrameEvidence, hud_mask: np.ndarray, facecam: Optional[Rect],
) -> List[np.ndarray]:
    """Pixel sets whose edges must be on screen while the detected stream layout is: the HUD, the camera border."""
    masks: List[np.ndarray] = []
    hud_pixels = hud_mask.astype(bool)
    if hud_pixels.sum() >= HUD_MIN_EDGE_PIXELS:
        masks.append(hud_pixels)
    if facecam is not None:
        border = _facecam_border_mask(evidence.shape, facecam) & (evidence.persist >= LINE_PERSISTENCE)
        if border.sum() >= 12:
            masks.append(border)
    return masks


def frame_layout_valid(frame_bgr: np.ndarray, masks: Sequence[np.ndarray]) -> bool:
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    edges = np.hypot(cv2.Sobel(gray, cv2.CV_32F, 1, 0), cv2.Sobel(gray, cv2.CV_32F, 0, 1)) > EDGE_MAGNITUDE
    return all(edges[mask].mean() >= VALID_SAMPLE_SCORE for mask in masks)


def refine_fallback_ranges(
    path: Path, profile: "GamingProfile", duration: float, step: float, fixed: Sequence[float] = (),
) -> List[Tuple[float, float]]:
    """Move each switch between the gaming layout and the full frame onto the frame where the stream changes.

    The samples are up to ``step`` apart; the seconds around a switch are decoded at full rate and checked frame
    by frame, so the camera panel never shows a few frames of something else.
    """
    masks = profile.check_masks
    if not masks or not profile.fallback_ranges:
        return profile.fallback_ranges
    h, w = masks[0].shape

    reach = min(step, REFINE_MAX_REACH)

    def switch(near: float, to_valid: bool) -> float:
        a = max(0.0, near - reach)
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-an", "-sn", "-ss", f"{a:.3f}", "-t", f"{2 * reach:.3f}", "-i", str(path),
             "-vf", f"fps={REFINE_FPS},scale={w}:{h}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
            capture_output=True, timeout=120,
        )
        size = w * h * 3
        count = len(result.stdout) // size if result.returncode == 0 else 0
        frames = np.frombuffer(result.stdout[:count * size], dtype=np.uint8).reshape(count, h, w, 3)
        states = [frame_layout_valid(frame, masks) for frame in frames]
        for i in range(1, len(states)):
            if states[i] == to_valid and states[i - 1] != to_valid:
                return a + (i - 0.5) / REFINE_FPS
        return near

    refined = []
    for start, end in profile.fallback_ranges:
        try:
            # An edge that is the start or end of this layout's own span is already exact.
            a = start if start <= 0.0 or any(abs(start - t) < 1e-3 for t in fixed) else switch(start, to_valid=False)
            b = end if end >= duration - 1e-3 or any(abs(end - t) < 1e-3 for t in fixed) else switch(end, to_valid=True)
        except Exception as exc:  # noqa: BLE001 - the sampled boundary still stands
            logger.debug("Gaming boundary refinement failed: %s", exc)
            a, b = start, end
        if b > a:
            refined.append((a, b))
    return refined


def layout_valid_samples(
    evidence: FrameEvidence, hud_mask: np.ndarray, facecam: Optional[Rect],
) -> np.ndarray:
    """Per sample: is the detected HUD (and camera border) actually on screen?

    Streams switch scenes (full-screen camera, a cutscene, a lobby). A sample in
    which under half of the persistent edges are present shows something else.
    """
    count = len(evidence.edges)
    valid = np.ones(count, bool)
    for mask in layout_check_masks(evidence, hud_mask, facecam):
        valid &= evidence.edges[:, mask].mean(axis=1) >= VALID_SAMPLE_SCORE
    if count >= 5:
        # One odd sample (a flash, a full-screen effect) is not a scene change.
        padded = np.pad(valid.astype(np.uint8), 2, mode="edge")
        valid = np.array([np.median(padded[i:i + 5]) for i in range(count)]) > 0
    return valid


def fallback_ranges(valid: np.ndarray, times: Sequence[float], duration: float) -> List[Tuple[float, float]]:
    """Spans (seconds) in which the gaming layout does not apply; short blips are ignored."""
    ranges: List[Tuple[float, float]] = []
    step = (times[1] - times[0]) if len(times) > 1 else duration
    start: Optional[int] = None
    for i, ok in enumerate(list(valid) + [True]):
        if not ok and start is None:
            start = i
        elif ok and start is not None:
            a = 0.0 if start == 0 else times[start] - step / 2
            b = duration if i >= len(valid) else times[i] - step / 2
            if b - a >= MIN_FALLBACK_SECONDS:
                ranges.append((max(0.0, a), min(duration, b)))
            start = None
    return ranges


def split_screen_ranges(evidence: FrameEvidence, duration: float) -> List[Tuple[float, float]]:
    """Spans (seconds) in which a divider splits the screen into a left and a right picture."""
    if evidence.centre_line is None:
        return []
    present = evidence.centre_line >= SPLIT_LINE_COVERAGE
    count = len(present)
    if count >= 3:
        # One sample is not a split (a lamp post in the middle of the road) and not the end of one (a flash).
        padded = np.pad(present.astype(np.uint8), 1, mode="edge")
        present = np.array([np.median(padded[i:i + 3]) for i in range(count)]) > 0
    times = evidence.times
    step = (times[1] - times[0]) if count > 1 else duration
    ranges: List[Tuple[float, float]] = []
    start: Optional[int] = None
    for i, on in enumerate(list(present) + [False]):
        if on and start is None:
            start = i
        elif not on and start is not None:
            a = 0.0 if start == 0 else times[start] - step / 2
            b = duration if i >= count else times[i] - step / 2
            if b - a >= SPLIT_MIN_SECONDS:
                ranges.append((max(0.0, a), min(duration, b)))
            start = None
    return ranges


def refine_split_ranges(
    path: Path, ranges: Sequence[Tuple[float, float]], duration: float, step: float, shape: Tuple[int, int],
) -> List[Tuple[float, float]]:
    """Move each start and end of a split onto the frame where the divider appears or goes."""
    h, w = shape

    reach = min(step, REFINE_MAX_REACH)

    def switch(near: float, appears: bool) -> float:
        a = max(0.0, near - reach)
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-an", "-sn", "-ss", f"{a:.3f}", "-t", f"{2 * reach:.3f}", "-i", str(path),
             "-vf", f"fps={REFINE_FPS},scale={w}:{h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=120,
        )
        count = len(result.stdout) // (w * h) if result.returncode == 0 else 0
        if count < 2:
            return near
        gray = np.frombuffer(result.stdout[:count * w * h], dtype=np.uint8).reshape(count, h, w)
        states = [
            centre_line_score(np.abs(cv2.Sobel(f.astype(np.float32), cv2.CV_32F, 1, 0)) > LINE_MAGNITUDE, f)
            >= SPLIT_LINE_COVERAGE
            for f in gray
        ]
        for i in range(1, count):
            if states[i] == appears and states[i - 1] != appears:
                return a + (i - 0.5) / REFINE_FPS
        return near

    refined = []
    for start, end in ranges:
        try:
            a = start if start <= 0.0 else switch(start, True)
            b = end if end >= duration - 1e-3 else switch(end, False)
        except Exception as exc:  # noqa: BLE001 - the sampled boundary still stands
            logger.debug("Split boundary refinement failed: %s", exc)
            a, b = start, end
        if b > a:
            refined.append((a, b))
    return refined


def fallback_ranges_within(
    valid: np.ndarray, times: Sequence[float], spans: Sequence[Tuple[float, float]],
) -> List[Tuple[float, float]]:
    """``fallback_ranges`` for a layout that applies in ``spans`` only; the samples are those inside the spans."""
    ranges: List[Tuple[float, float]] = []
    for lo, hi in spans:
        inside = [i for i, t in enumerate(times) if lo - 1e-6 <= t < hi]
        if not inside:
            continue
        step = (times[inside[1]] - times[inside[0]]) if len(inside) > 1 else hi - lo
        start: Optional[int] = None
        for n, i in enumerate(inside + [None]):
            ok = True if i is None else bool(valid[i])
            if not ok and start is None:
                start = n
            elif ok and start is not None:
                a = lo if start == 0 else times[inside[start]] - step / 2
                b = hi if i is None else times[i] - step / 2
                if b - a >= MIN_FALLBACK_SECONDS:
                    ranges.append((max(lo, a), min(hi, b)))
                start = None
    return ranges


def still_focus(evidence: FrameEvidence, facecam: Rect) -> Tuple[Rect, bool]:
    """The part of a still picture that carries detail (proc pixels): a phone held up in the game, a menu.

    Edges that are there in most samples, outside the camera; the box that holds nearly all of them, with a
    little air. A picture with detail everywhere (slides, a map) gives the whole frame. The second value says
    that what changes in the picture sits in the returned part: it may then fill the panel.
    """
    h, w = evidence.shape
    detail = evidence.persist >= STILL_DETAIL_PERSISTENCE
    detail[max(0, facecam.y - 4):facecam.y2 + 4, max(0, facecam.x - 4):facecam.x2 + 4] = False
    if detail.sum() < HUD_MIN_EDGE_PIXELS:
        return Rect(0, 0, w, h), False

    def span(profile: np.ndarray, size: int) -> Tuple[int, int]:
        total = np.cumsum(profile.astype(np.float64))
        lo = int(np.searchsorted(total, total[-1] * STILL_FOCUS_QUANTILES[0]))
        hi = int(np.searchsorted(total, total[-1] * STILL_FOCUS_QUANTILES[1]))
        pad = int(round(size * STILL_FOCUS_PAD))
        return max(0, lo - pad), min(size, hi + 1 + pad)

    x0, x1 = span(detail.sum(axis=0), w)
    y0, y1 = span(detail.sum(axis=1), h)
    # In a still picture every edge persists, grass as much as text, so detail alone often spans the frame.
    # What changes in it says where to look: a message scrolling on a phone, a cursor in a menu. When all of
    # that sits in one part of the width, that part is the picture's subject and fills the panel. Changes
    # across the width (chat on one side, a counter on the other) leave the picture whole.
    # Two changes at least, whatever the number of samples: one pop-up in a short clip is not activity.
    pairs = max(1, len(evidence.edges) - 1)
    active = evidence.change >= max(STILL_ACTIVITY_RATE, 2.0 / pairs)
    # The camera, its frame and the alerts stacked on it are not part of the picture.
    reach = int(CAM_DECORATION_REACH * w)
    active[max(0, facecam.y - reach):facecam.y2 + reach, max(0, facecam.x - reach):facecam.x2 + reach] = False
    active[:, :x0] = False
    active[:, x1:] = False
    if active.sum() >= STILL_ACTIVITY_MIN_AREA * w * h:
        # The window the panel can show at full height (a little under the frame's height wide) that holds the
        # most of it. One subject is nearly all inside; two at opposite sides are not.
        window = min(x1 - x0, max(2, int(round((y1 - y0) * STILL_WINDOW_ASPECT))))
        columns = np.concatenate(([0.0], np.cumsum(active.sum(axis=0).astype(np.float64))))
        inside = columns[x0 + window:x1 + 1] - columns[x0:x1 + 1 - window]
        if len(inside) and inside.max() >= STILL_SUBJECT_SHARE * columns[-1] and window < x1 - x0:
            best = np.flatnonzero(inside >= inside.max() - 1e-9)
            left = x0 + int(best[len(best) // 2])
            # The subject is sharp: that part holds its share of the picture's lines. A blurred backdrop
            # shimmering behind a menu changes too, and the menu's text is elsewhere.
            lines = detail[:, x0:x1].sum(axis=0).astype(np.float64)
            share = lines[left - x0:left - x0 + window].sum() / max(1.0, lines.sum())
            if share >= STILL_SUBJECT_DETAIL * window / (x1 - x0):
                return Rect(left, y0, window, max(2, y1 - y0)), True
    return Rect(x0, y0, max(2, x1 - x0), max(2, y1 - y0)), False


def sticker_region(
    evidence: FrameEvidence, face: Box
) -> Optional[Tuple[Rect, Tuple[int, int, List[Tuple[int, int, int, int]]]]]:
    """The drawn picture a still face belongs to (proc pixels) and its cut-out mask, or None.

    A logo or an avatar is drawn over the game: its pixels never change and it has lines of its own. Flat sky
    behind it does not change either but has no lines, and the game around it moves. The picture is the patch
    of unchanging pixels near persistent edges that holds the face.
    """
    h, w = evidence.shape
    fx, fy, fw, fh = face
    side, up, down = STICKER_REACH
    wx0, wx1 = max(0, int(fx - side * fw)), min(w, int(fx + (1 + side) * fw))
    wy0, wy1 = max(0, int(fy - up * fh)), min(h, int(fy + (1 + down) * fh))
    if wx1 - wx0 < 8 or wy1 - wy0 < 8:
        return None
    still = evidence.change[wy0:wy1, wx0:wx1] < FACE_PIXEL_CHANGE_RATE
    lines = (evidence.persist[wy0:wy1, wx0:wx1] >= HUD_PERSISTENCE).astype(np.uint8)
    reach = max(3, int(round(fw * 0.35))) | 1
    near_lines = cv2.dilate(lines, np.ones((reach, reach), np.uint8)) > 0
    drawn = (still & near_lines).astype(np.uint8)
    drawn = cv2.morphologyEx(drawn, cv2.MORPH_CLOSE, np.ones((reach, reach), np.uint8))
    count, labels = cv2.connectedComponents(drawn, connectivity=8)
    cy, cx = int(fy + fh / 2) - wy0, int(fx + fw / 2) - wx0
    if count < 2 or not (0 <= cy < labels.shape[0] and 0 <= cx < labels.shape[1]) or labels[cy, cx] == 0:
        return None
    body = labels == labels[cy, cx]
    ys, xs = np.nonzero(body)
    x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
    # A patch that runs out of the window on several sides is the scene (a still background), not a picture.
    # A patch that runs out of the window is more than a picture around a face: a HUD panel beside it, a bar
    # across the screen, a wordmark, or the scene itself standing still. Cut off there it would be half a logo.
    touching = sum((x0 == 0 and wx0 > 0, y0 == 0 and wy0 > 0, x1 == body.shape[1] and wx1 < w, y1 == body.shape[0] and wy1 < h))
    if touching or (x1 - x0) < fw or (y1 - y0) < fh:
        return None
    if (x1 - x0) * (y1 - y0) > STICKER_MAX_FACE_AREAS * fw * fh:
        return None
    body = body[y0:y1, x0:x1]
    scale = STICKER_GRID / max(body.shape)
    cols, rows = max(2, int(round(body.shape[1] * scale))), max(2, int(round(body.shape[0] * scale)))
    grid = cv2.resize(body.astype(np.uint8) * 255, (cols, rows), interpolation=cv2.INTER_AREA) >= 110
    # Runs along each row, with equal runs in consecutive rows joined into one taller cell.
    cells: List[Tuple[int, int, int, int]] = []
    open_runs: Dict[Tuple[int, int], int] = {}
    for row in range(rows + 1):
        runs: List[Tuple[int, int]] = []
        if row < rows:
            padded = np.concatenate(([False], grid[row], [False]))
            edges = np.flatnonzero(padded[1:] != padded[:-1])
            runs = [(int(a), int(b - a)) for a, b in zip(edges[::2], edges[1::2])]
        for run in [r for r in open_runs if r not in runs]:
            cells.append((run[0], open_runs[run], run[1], row - open_runs.pop(run)))
        for run in runs:
            open_runs.setdefault(run, row)
    if not cells or len(cells) > STICKER_MAX_BOXES:
        return None
    return Rect(wx0 + x0, wy0 + y0, x1 - x0, y1 - y0), (cols, rows, cells)


def virtual_camera_rect(face: Box, w: int, h: int) -> Rect:
    """A camera rectangle for a streamer shown without one (background keyed out): head and shoulders."""
    fx, fy, fw, fh = face
    cw = min(float(w), fw * VIRTUAL_CAMERA_WIDTH)
    x0 = int(min(max(0.0, fx + fw / 2 - cw / 2), w - cw))
    y0 = int(max(0.0, fy - fh * VIRTUAL_CAMERA_ABOVE))
    y1 = int(min(float(h), fy + fh * (1 + VIRTUAL_CAMERA_BELOW)))
    return Rect(x0, y0, int(cw), max(2, y1 - y0))


def classify_gaming(
    evidence: FrameEvidence,
    faces: Sequence[Sequence[Box]],
    width: int,
    height: int,
    duration: float,
    track: Optional[FaceTrack] = None,
    spans: Optional[Sequence[Tuple[float, float]]] = None,
) -> GamingProfile:
    """Decide whether the sampled clip is gameplay, and where its parts are (source pixels).

    ``track`` names the camera's face when the caller already knows it (one camera of several in a clip);
    ``spans`` are then the seconds the samples come from.
    """
    h, w = evidence.shape
    sx, sy = width / w, height / h

    facecam: Optional[Rect] = None
    face_box: Optional[Box] = None
    virtual = False
    tracks = [track] if track is not None else fixed_face_tracks(faces, w, h)
    candidates = [t for t in tracks if track is not None or t.presence >= FACECAM_MIN_PRESENCE]
    # A streamer in a cap who looks down at the controller is detected in a few samples only. The rectangle
    # around that head still counts when its border is on screen for the whole clip.
    seldom = [t for t in tracks if t not in candidates and t.presence >= FACECAM_SELDOM_PRESENCE]
    def always_there(rect: Rect, persistence: float) -> bool:
        border = _facecam_border_mask((h, w), rect) & (evidence.persist >= persistence)
        if border.sum() < 12:
            return False
        return float((evidence.edges[:, border].mean(axis=1) >= VALID_SAMPLE_SCORE).mean()) >= FACECAM_SELDOM_BORDER

    def alive(box: Box) -> bool:
        # A head moves and talks, so its own pixels change. A face in a channel logo or a drawn avatar never does,
        # however much the game moves behind it.
        x, y, bw, bh = box
        patch = evidence.change[int(y + bh * 0.15):int(y + bh * 0.85) + 1, int(x + bw * 0.15):int(x + bw * 0.85) + 1]
        if not patch.size or len(evidence.edges) < FACE_MOTION_MIN_SAMPLES:
            return True
        return float((patch >= FACE_PIXEL_CHANGE_RATE).mean()) >= MIN_FACE_MOTION

    def keyed_streamer(box: Box) -> bool:
        # A streamer without a camera rectangle sits small, or at the edge of the picture, beside the game.
        # A person in the middle of the frame at a size that fills it is being filmed (a vlog).
        if box[2] * box[3] > VIRTUAL_CAMERA_FACE_MAX_AREA * w * h or not alive(box):
            return False
        if box[2] * box[3] <= KEYED_FREE_FACE_AREA * w * h:
            return True
        r = virtual_camera_rect(box, w, h)
        # At the sides the head-and-shoulders rectangle touches the edge. At the top the head itself nearly
        # does (someone filmed has headroom); at the bottom the body runs out of the picture, a face height
        # below the rectangle.
        return (r.x <= KEYED_EDGE_REACH * w or r.x2 >= (1 - KEYED_EDGE_REACH) * w
                or box[1] <= 0.5 * box[3]
                or r.y2 + box[3] >= (1 - KEYED_EDGE_REACH) * h)

    def inner(rect: Rect) -> np.ndarray:
        dx, dy = max(1, int(rect.w * 0.06)), max(1, int(rect.h * 0.06))
        return evidence.change[rect.y + dy:rect.y2 - dy, rect.x + dx:rect.x2 - dx]

    def stands_apart(rect: Rect) -> bool:
        # The room in a camera is calm and the game around it is not: the proof for a border that is hard to see.
        band = max(4, int(rect.w * 0.08))
        around = np.zeros((h, w), bool)
        around[max(0, rect.y - band):rect.y2 + band, max(0, rect.x - band):rect.x2 + band] = True
        around[rect.y:rect.y2, rect.x:rect.x2] = False
        if not around.any() or not inner(rect).size:
            return False
        outside = float(evidence.change[around].mean())
        return outside >= PATIENT_OUTSIDE_CHANGE and float(inner(rect).mean()) <= PATIENT_CALM_RATIO * outside

    for patient in (False, True):
        for candidate in candidates + seldom:
            rect = find_facecam_rect(evidence, candidate.box, patient=patient)
            if rect is None or not alive(candidate.box):
                continue
            # A camera shows a person, who moves. A channel logo or a drawn avatar in a frame does not.
            if not inner(rect).size or float((inner(rect) > MOVING_CHANGE_RATE).mean()) < MIN_CAMERA_MOTION:
                continue
            if patient and not (stands_apart(rect) or always_there(rect, PATIENT_LINE_PERSISTENCE)):
                continue
            if not patient and candidate in seldom and not always_there(rect, LINE_PERSISTENCE):
                continue
            facecam, face_box = rect, candidate.box
            break
        if facecam is not None:
            break

    regions, crosshair, hud_mask = find_hud_regions(evidence, facecam)
    keyed = [t for t in candidates if keyed_streamer(t.box)]
    if facecam is None and keyed and regions and float(hud_mask.sum()) / float(w * h) >= MIN_HUD_EDGE_FRACTION:
        # No rectangle, but a head that stays put over a game that draws a HUD: a streamer with the background
        # keyed out. The top panel shows head and shoulders with the game behind them.
        facecam, face_box, virtual = virtual_camera_rect(keyed[0].box, w, h), keyed[0].box, True
        regions, crosshair, hud_mask = find_hud_regions(evidence, facecam)
    if facecam is not None:
        # A name plate or ornament drawn on the camera's frame belongs to the camera, not to the game: lifted into
        # the gameplay panel it is a stray sticker. It sits just above or below the camera, within its width,
        reach = CAM_DECORATION_REACH * w

        def decoration(r: Rect) -> bool:
            across = min(r.x2, facecam.x2) - max(r.x, facecam.x)
            gap = max(facecam.y - r.y2, r.y - facecam.y2, 0)
            if across >= 0.6 * min(r.w, facecam.w) and gap <= reach:
                return True
            # ... or beside it, along its height: a frame's side bar, the strip between the camera and the edge.
            along = min(r.y2, facecam.y2) - max(r.y, facecam.y)
            side_gap = max(facecam.x - r.x2, r.x - facecam.x2, 0)
            return along >= 0.6 * min(r.h, facecam.h) and side_gap <= reach

        regions = [r for r in regions if not decoration(r)]

    # A pictured face over the game with no camera on screen: the channel's logo or the streamer's avatar. It is
    # lifted out as a sticker; the persistent edges it produced are not HUD.
    sticker = None
    if facecam is None:
        for t in tracks:
            if t.presence >= FACECAM_MIN_PRESENCE and not alive(t.box):
                sticker = sticker_region(evidence, t.box)
                if sticker is not None:
                    break
    if sticker is not None:
        mark = sticker[0]

        def on_sticker(r: Rect) -> bool:
            ix = min(r.x2, mark.x2) - max(r.x, mark.x)
            iy = min(r.y2, mark.y2) - max(r.y, mark.y)
            return ix > 0 and iy > 0 and ix * iy >= 0.3 * min(r.w * r.h, mark.w * mark.h)

        regions = [r for r in regions if not on_sticker(r)]

    world = np.ones((h, w), bool)
    if facecam is not None:
        world &= ~_rect_mask((h, w), [facecam])
    hud_edge_fraction = float(hud_mask.sum()) / float(w * h)
    world_only = world & ~_rect_mask((h, w), regions)
    moving_fraction = float((evidence.change[world_only] > MOVING_CHANGE_RATE).mean()) if world_only.any() else 0.0

    frame_area = float(w * h)
    # A face in a logo or an avatar is nobody on screen: it is left out of the count of people.
    pictures = [t.box for t in tracks if t.presence >= FACECAM_MIN_PRESENCE and not alive(t.box)]
    outside = 0
    for boxes in faces:
        for x, y, bw, bh in boxes:
            if bw * bh / frame_area < OUTSIDE_FACE_MIN_AREA:
                continue
            cx, cy = x + bw / 2, y + bh / 2
            if any(px <= cx <= px + pw and py <= cy <= py + ph for px, py, pw, ph in pictures):
                continue
            if facecam is not None and facecam.x <= cx <= facecam.x2 and facecam.y <= cy <= facecam.y2:
                continue
            outside += 1
            break
    outside_rate = outside / max(1, len(faces))

    bordered = None if virtual else facecam
    host = None
    if facecam is not None and face_box is not None:
        for other in fixed_face_tracks(faces, w, h):
            cx, cy = other.box[0] + other.box[2] / 2, other.box[1] + other.box[3] / 2
            inside = facecam.x <= cx <= facecam.x2 and facecam.y <= cy <= facecam.y2
            if inside or other.presence < FACECAM_MIN_PRESENCE or other.box[3] <= face_box[3]:
                continue
            # Over a game that draws a HUD, a larger face that stays put is a character (a cutscene, a
            # conversation, the driver seen from behind). One persistent graphic is not a HUD: a watermark, a
            # lower third. Without a HUD the face has to be there for nearly the whole clip, clearly larger
            # and alive: then somebody is being filmed.
            if (len(regions) >= HOST_HUD_REGIONS or crosshair) and not virtual:
                continue
            if (other.presence >= HOST_MIN_PRESENCE and other.box[3] >= HOST_MIN_SIZE * face_box[3]
                    and alive(other.box)):
                host = other
                break
    # ... or is seen now and then, or too close to be tracked: any face larger than the camera's own, outside it.
    larger_outside = 0.0
    if facecam is not None and face_box is not None:
        seen = 0
        for boxes in faces:
            for x, y, bw, bh in boxes:
                cx, cy = x + bw / 2, y + bh / 2
                if bh > face_box[3] * 1.2 and not (facecam.x <= cx <= facecam.x2 and facecam.y <= cy <= facecam.y2):
                    seen += 1
                    break
        larger_outside = seen / max(1, len(faces))
    valid = layout_valid_samples(evidence, hud_mask, bordered)
    if virtual and len(faces) == len(valid) and len(valid) >= 7:
        # No rectangle to check: the head itself says whether the streamer is on screen. Gone for a stretch
        # (an edit cuts to the game alone), the panel would show the game twice.
        seen = np.array([
            any(facecam.x <= bx + bw / 2 <= facecam.x2 and facecam.y <= by + bh / 2 <= facecam.y2
                for bx, by, bw, bh in boxes)
            for boxes in faces
        ], dtype=np.uint8)
        padded = np.pad(seen, 3, mode="edge")
        valid &= np.array([np.median(padded[i:i + 7]) for i in range(len(seen))]) > 0
    valid_fraction = float(valid.mean()) if len(valid) else 0.0

    def to_source(r: Rect, inset: int = 0) -> Rect:
        x0 = clamp_even(int(round((r.x + inset) * sx)), 0, width - 2)
        y0 = clamp_even(int(round((r.y + inset) * sy)), 0, height - 2)
        x1 = clamp_even(int(round((r.x2 - inset) * sx)), x0 + 2, width)
        y1 = clamp_even(int(round((r.y2 - inset) * sy)), y0 + 2, height)
        return Rect(x0, y0, x1 - x0, y1 - y0)

    profile = GamingProfile(
        width=width, height=height, is_gaming=False, reason="",
        facecam=to_source(facecam, inset=0 if virtual else FACECAM_INSET) if facecam is not None else None,
        face=(face_box[0] * sx, face_box[1] * sy, face_box[2] * sx, face_box[3] * sy) if face_box else None,
        hud=[to_source(r) for r in regions], crosshair=crosshair,
        moving_fraction=moving_fraction, hud_edge_fraction=hud_edge_fraction,
        outside_face_rate=outside_rate, valid_fraction=valid_fraction,
        fallback_ranges=(fallback_ranges_within(valid, evidence.times, spans) if spans
                         else fallback_ranges(valid, evidence.times, duration)),
        check_masks=layout_check_masks(evidence, hud_mask, bordered),
        virtual_camera=virtual, active=[(float(a), float(b)) for a, b in spans] if spans else [],
    )
    # A channel's own graphic over moving footage is a stream already: one HUD element beside it is enough.
    needed = MIN_HUD_REGIONS_WITH_FACECAM if facecam is not None or sticker is not None else MIN_HUD_REGIONS

    def anchored(r: Rect) -> bool:
        # A game pins its HUD to the edges of the screen. Labels on a drawn map, signs in a panning shot and
        # captions sit anywhere.
        reach_x, reach_y = HUD_ANCHOR_REACH * w, HUD_ANCHOR_REACH * h
        return r.x <= reach_x or r.x2 >= w - reach_x or r.y <= reach_y or r.y2 >= h - reach_y
    if sticker is not None:
        profile.sticker, profile.sticker_mask = to_source(sticker[0]), sticker[1]
    splits = split_screen_ranges(evidence, duration) if facecam is None and track is None else []
    inset = (facecam is not None and not virtual and face_box is not None
             and face_box[2] * face_box[3] <= CALM_PICTURE_FACE_MAX_AREA * w * h)
    if len(evidence.edges) < MIN_SAMPLES:
        profile.reason = "too_short"
    elif moving_fraction < MIN_MOVING_FRACTION and not inset:
        profile.reason = "static_picture"
    elif moving_fraction < MIN_MOVING_FRACTION and inset and larger_outside > MAX_OUTSIDE_FACE_RATE:
        # A calm picture with someone larger than the inset on screen: an interview with a guest thumbnail.
        # (Over a moving game, larger faces are the game's own characters and do not count.)
        profile.reason = "people_on_screen"
    elif moving_fraction < MIN_MOVING_FRACTION_WITH_FACECAM and host is None:
        # The game stands still beside the camera: camera on top, the detailed part of the picture below.
        profile.is_gaming = True
        profile.reason = "facecam_still"
        focus, profile.focus_fill = still_focus(evidence, facecam)
        profile.focus = to_source(focus)
        profile.hud = []
    elif (splits and sum(b - a for a, b in splits) >= SPLIT_MIN_FRACTION * duration
          and outside_rate <= MAX_OUTSIDE_FACE_RATE):
        # Two pictures side by side with nobody on screen: a comparison. The halves are stacked while the
        # screen is split, and the middle of the picture fills the frame in between.
        profile.is_gaming = True
        profile.reason = "split_screen"
        profile.split_ranges = splits
        profile.hud, profile.fallback_ranges, profile.check_masks = [], [], []
    elif (facecam is None or virtual) and outside_rate > MAX_OUTSIDE_FACE_RATE:
        # With a camera rectangle found, other faces are the game's own (cutscenes, photoreal characters).
        profile.reason = "people_on_screen"
    elif host is not None:
        # Someone else sits still on screen for most of the clip, larger than the camera's face: a reaction or
        # a podcast with a small inset, not a game.
        profile.reason = "people_on_screen"
    elif (facecam is None or virtual) and (
        len(regions) < needed or hud_edge_fraction < MIN_HUD_EDGE_FRACTION
        or (facecam is None and sticker is None and not crosshair
            and sum(anchored(r) for r in regions) < MIN_ANCHORED_HUD_REGIONS)
    ):
        # A camera inset over a moving picture is a stream whether or not the game draws a HUD (story missions).
        profile.reason = "no_hud"
    elif valid_fraction < (MIN_VALID_FRACTION_WITH_FACECAM if facecam is not None else MIN_VALID_FRACTION):
        profile.reason = "layout_changes"
    else:
        profile.is_gaming = True
        profile.reason = "facecam_hud" if facecam is not None else "hud"
    return profile


def camera_timeline(tracks: Sequence[FaceTrack], count: int) -> List[Tuple[int, int, int]]:
    """Which camera each sample belongs to: runs of (track index, first sample, last sample).

    A sample with two tracked faces goes to the one seen most. Samples without a tracked face go to the camera
    around them (it is hidden for a cutscene), or are shared between the cameras before and after.
    """
    label = np.full(count, -1, dtype=np.int64)
    for k in range(len(tracks) - 1, -1, -1):
        label[np.asarray(tracks[k].samples, dtype=np.int64)] = k
    known = np.flatnonzero(label >= 0)
    if not len(known):
        return []
    label[:known[0]] = label[known[0]]
    label[known[-1] + 1:] = label[known[-1]]
    for a, b in zip(known[:-1], known[1:]):
        if b - a > 1:
            middle = b if label[a] == label[b] else (a + b + 1) // 2
            label[a + 1:middle] = label[a]
            label[middle:b] = label[b]

    def runs_of(values: np.ndarray) -> List[Tuple[int, int, int]]:
        out: List[Tuple[int, int, int]] = []
        start = 0
        for i in range(1, count + 1):
            if i == count or values[i] != values[start]:
                out.append((int(values[start]), start, i - 1))
                start = i
        return out

    runs = runs_of(label)
    while len(runs) > 1:
        short = min(range(len(runs)), key=lambda r: runs[r][2] - runs[r][1])
        k, first, last = runs[short]
        if last - first + 1 >= SEGMENT_MIN_RUN_SAMPLES:
            break
        # A stray detection: it takes the camera of the longer run beside it.
        beside = [r for r in (short - 1, short + 1) if 0 <= r < len(runs)]
        keep = max(beside, key=lambda r: runs[r][2] - runs[r][1])
        label[first:last + 1] = runs[keep][0]
        runs = runs_of(label)
    return runs


def find_cut(path: Path, start: float, end: float) -> float:
    """The hard cut between two samples that show different cameras, to the frame."""
    if end - start > CUT_SEARCH_SECONDS or end <= start:
        return (start + end) / 2
    w, h = 160, 90
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-an", "-sn", "-ss", f"{start:.3f}", "-t", f"{end - start + 1 / REFINE_FPS:.3f}",
         "-i", str(path), "-vf", f"fps={REFINE_FPS},scale={w}:{h}", "-pix_fmt", "gray", "-f", "rawvideo", "-"],
        capture_output=True, timeout=120,
    )
    count = len(result.stdout) // (w * h) if result.returncode == 0 else 0
    if count < 2:
        return (start + end) / 2
    frames = np.frombuffer(result.stdout[:count * w * h], dtype=np.uint8).reshape(count, h, w).astype(np.int16)
    change = np.abs(frames[1:] - frames[:-1]).mean(axis=(1, 2))
    # Half a frame before the first frame of the new camera: the switch is then exact however the time is rounded.
    return start + (int(np.argmax(change)) + 0.5) / REFINE_FPS


def sample_spans(
    path: Path, spans: Sequence[Tuple[float, float]], proc_w: int, proc_h: int,
) -> Tuple[List[float], np.ndarray]:
    """Frames of a few short spans, sampled densely enough to classify them on their own."""
    total = sum(b - a for a, b in spans)
    fps = min(SEGMENT_MAX_SAMPLE_FPS, max(MAX_SAMPLE_FPS, (MIN_SAMPLES + 2) / max(total, 1e-3)))
    fps = min(fps, MAX_SAMPLES / max(total, 1e-3))  # a long camera is never more frames than the whole-clip sample
    times: List[float] = []
    chunks: List[np.ndarray] = []
    size = proc_w * proc_h * 3
    for a, b in spans:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-an", "-sn", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}", "-i", str(path),
             "-vf", f"fps={fps:.4f},scale={proc_w}:{proc_h}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
            capture_output=True, timeout=300,
        )
        if result.returncode != 0:
            raise RuntimeError("gaming analysis decode failed")
        count = len(result.stdout) // size
        chunks.append(np.frombuffer(result.stdout[:count * size], dtype=np.uint8).reshape(count, proc_h, proc_w, 3))
        times.extend(a + i / fps for i in range(count))
    return times, np.concatenate(chunks) if chunks else np.zeros((0, proc_h, proc_w, 3), np.uint8)


def analyze_camera_segments(
    path: Path, width: int, height: int, duration: float,
    times: Sequence[float], frames: np.ndarray, faces: Sequence[Sequence[Box]],
) -> List[GamingProfile]:
    """One profile per camera when the clip shows several: a compilation of streamers, or one streamer whose
    camera changes size or place. Each camera is classified from its own part of the clip.
    """
    h, w = frames.shape[1:3]
    tracks = fixed_face_tracks(faces, w, h)
    runs = camera_timeline(tracks, len(frames))
    if len({k for k, _, _ in runs}) < 2:
        return []
    bounds = [0.0]
    for (_, _, last), (_, first, _) in zip(runs[:-1], runs[1:]):
        bounds.append(find_cut(path, times[last], times[first]))
    bounds.append(duration)
    spans_of: Dict[int, List[Tuple[float, float]]] = {}
    for n, (k, _, _) in enumerate(runs):
        if bounds[n + 1] > bounds[n]:
            spans_of.setdefault(k, []).append((bounds[n], bounds[n + 1]))

    profiles: List[GamingProfile] = []
    for k, spans in spans_of.items():
        total = sum(b - a for a, b in spans)
        if total < SEGMENT_MIN_SECONDS:
            continue
        chosen = [i for i, t in enumerate(times) if any(a <= t < b for a, b in spans)]
        if len(chosen) >= MIN_SAMPLES:
            part_times, part_frames = [times[i] for i in chosen], frames[chosen]
            part_faces: Sequence[Sequence[Box]] = [faces[i] for i in chosen]
        else:
            part_times, part_frames = sample_spans(path, spans, w, h)
            part_faces = detect_faces(part_frames)
        if len(part_frames) < 2:
            continue
        # The same head, found again in this camera's own samples.
        ux, uy, uw, uh = tracks[k].box
        same = [
            t for t in fixed_face_tracks(part_faces, w, h)
            if abs(t.box[0] + t.box[2] / 2 - ux - uw / 2) <= uw * 0.9 and abs(t.box[1] + t.box[3] / 2 - uy - uh / 2) <= uh * 0.9
        ]
        track = same[0] if same and same[0].presence >= FACECAM_MIN_PRESENCE else None
        if track is None:
            continue
        profile = classify_gaming(
            measure_frames(part_frames, part_times), part_faces, width, height, total, track=track, spans=spans,
        )
        if profile.is_gaming and profile.fallback_ranges and len(part_times) > 1:
            edges = [t for span in spans for t in span]
            profile.fallback_ranges = refine_fallback_ranges(
                path, profile, duration, part_times[1] - part_times[0], fixed=edges,
            )
        profiles.append(profile)
    return profiles


def analyze_gaming_clip(path: Path, width: int, height: int, duration: float) -> List[GamingProfile]:
    """The clip's profile: one for the whole clip, or one per camera when several take turns."""
    times, frames = sample_clip_frames(path, width, height, duration)
    if len(frames) < 2:
        return []
    faces = detect_faces(frames)
    evidence = measure_frames(frames, times)
    whole = classify_gaming(evidence, faces, width, height, duration)
    if whole.is_gaming and whole.fallback_ranges and len(times) > 1:
        whole.fallback_ranges = refine_fallback_ranges(path, whole, duration, times[1] - times[0])
    if whole.split_ranges and len(times) > 1:
        whole.split_ranges = refine_split_ranges(path, whole.split_ranges, duration, times[1] - times[0], evidence.shape)
        return [whole]
    del evidence
    try:
        cameras = analyze_camera_segments(path, width, height, duration, times, frames, faces)
    except Exception as exc:  # noqa: BLE001 - the whole-clip profile still stands
        logger.warning("Gaming camera segments failed (%s); using one layout", exc)
        cameras = []
    for profile in cameras:
        logger.info("Gaming camera: %s", profile.summary())
    cameras = sorted((c for c in cameras if c.is_gaming), key=lambda c: -c.shown_seconds(duration))[:MAX_CAMERA_LAYOUTS]
    shown = sum(c.shown_seconds(duration) for c in cameras)
    alone = whole.shown_seconds(duration) if whole.is_gaming else 0.0
    if cameras and shown >= MIN_VALID_FRACTION_WITH_FACECAM * duration and shown > alone + SEGMENT_MIN_GAIN * duration:
        return sorted(cameras, key=lambda c: c.active[0][0])
    return [whole]


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def facecam_crop(facecam: Rect, face: Optional[Box]) -> Tuple[Rect, int]:
    """The part of the camera shown in the top panel, and the panel height.

    The whole camera at full width when its shape allows; a taller or wider
    camera is cropped around the face so the panel stays about a third of the frame.
    """
    natural = CANVAS_W * facecam.h / facecam.w
    panel_h = round_to_even(int(min(FACECAM_PANEL_MAX, max(FACECAM_PANEL_MIN, natural))))
    aspect = CANVAS_W / panel_h
    cw, ch = facecam.w, facecam.h
    if cw / ch > aspect:
        cw = int(ch * aspect)
    else:
        ch = int(cw / aspect)
    cw, ch = round_to_even(cw), round_to_even(ch)
    if face is not None:
        fcx, fcy = face[0] + face[2] / 2, face[1] + face[3] / 2
    else:
        fcx, fcy = facecam.x + facecam.w / 2, facecam.y + facecam.h * 0.42
    x = clamp_even(int(fcx - cw / 2), facecam.x, max(facecam.x, facecam.x2 - cw))
    y = clamp_even(int(fcy - ch * 0.42), facecam.y, max(facecam.y, facecam.y2 - ch))
    return Rect(x, y, cw, ch), panel_h


def gameplay_crop(width: int, height: int, panel_h: int, facecam: Optional[Rect]) -> Rect:
    """Full-height crop of the screen centre (crosshair, or the character a follow camera keeps there).

    Slides sideways, a little, only to keep the camera inset out of the gameplay panel.
    """
    cw = round_to_even(min(width, int(round(height * CANVAS_W / panel_h))))
    x = (width - cw) // 2
    if facecam is not None:
        limit = int(width * CROP_SHIFT_MAX)
        if facecam.x + facecam.w / 2 < width / 2 and facecam.x2 > x:
            x = min(x + limit, facecam.x2, width - cw)
        elif facecam.x + facecam.w / 2 >= width / 2 and facecam.x < x + cw:
            x = max(x - limit, facecam.x - cw, 0)
    return Rect(clamp_even(x, 0, width - cw), 0, cw, round_to_even(height))


def _overlap(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    w = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    h = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return max(0.0, w) * max(0.0, h)


def place_hud(
    regions: Sequence[Rect], crop: Rect, width: int, panel_y: int, panel_h: int,
) -> Tuple[List[Dict[str, Any]], List[Tuple[float, float, float, float]]]:
    """Where each HUD element goes on the canvas.

    Returns (cut-outs, occupied): cut-outs are elements the crop loses, slid
    inwards to the nearest panel edge at their own height; ``occupied`` also
    lists the elements still visible inside the crop (captions avoid both).
    """
    scale = panel_h / crop.h
    occupied: List[Tuple[float, float, float, float]] = []
    pending: List[Dict[str, Any]] = []
    for r in regions:
        visible = max(0, min(r.x2, crop.x2) - max(r.x, crop.x)) / r.w
        centre = (r.x + r.w / 2) / width
        anchor = "left" if centre < 0.4 else "right" if centre > 0.6 else "center"
        if visible >= (HUD_VISIBLE_CENTERED if anchor == "center" else HUD_VISIBLE_IN_CROP):
            x0, x1 = max(r.x, crop.x), min(r.x2, crop.x2)
            occupied.append(((x0 - crop.x) * scale, panel_y + r.y * scale, (x1 - x0) * scale, r.h * scale))
            continue
        if HUD_SIDE_BAND[0] <= (r.y + r.h / 2) / crop.h <= HUD_SIDE_BAND[1]:
            continue
        pending.append({"source": r, "anchor": anchor})

    def laid_out(factor: float) -> List[Dict[str, Any]]:
        k = min(scale, HUD_MAX_SCALE) * factor
        placed = []
        for item in pending:
            r: Rect = item["source"]
            kk = min(k, CANVAS_W * 0.96 / r.w)
            ow, oh = round_to_even(int(r.w * kk)), round_to_even(int(r.h * kk))
            if item["anchor"] == "left":
                ox = r.x * kk
            elif item["anchor"] == "right":
                ox = CANVAS_W - (width - r.x) * kk
            else:
                ox = CANVAS_W / 2 + (r.x + r.w / 2 - width / 2) * kk - ow / 2
            ox = int(min(max(0, ox), CANVAS_W - ow))
            # Same height on the panel as on the screen, so top elements stay on top and bars at the bottom.
            oy = panel_y + (r.y + r.h / 2) * scale - oh / 2
            oy = int(min(max(panel_y, oy), panel_y + panel_h - oh))
            placed.append({**item, "x": ox, "y": oy, "w": ow, "h": oh})
        return placed

    def collisions(placed: List[Dict[str, Any]]) -> List[Tuple[int, int]]:
        hits = []
        for i in range(len(placed)):
            for j in range(i + 1, len(placed)):
                a, b = placed[i], placed[j]
                if _overlap((a["x"], a["y"], a["w"], a["h"]), (b["x"], b["y"], b["w"], b["h"])) > 0:
                    hits.append((i, j))
        return hits

    factor = 1.0
    placed = laid_out(factor)
    while collisions(placed) and factor > HUD_MIN_SCALE_FACTOR:
        factor = max(HUD_MIN_SCALE_FACTOR, factor - 0.1)
        placed = laid_out(factor)
    # Still touching: the smaller element of each pair gives way.
    while True:
        hits = collisions(placed)
        if not hits:
            break
        i, j = hits[0]
        drop = i if placed[i]["w"] * placed[i]["h"] < placed[j]["w"] * placed[j]["h"] else j
        pending.pop(drop)
        placed = laid_out(factor)

    # Nothing sits on the middle of the panel (crosshair, character), and cut-outs never take over the picture.
    cx, cy = CANVAS_W / 2, panel_y + panel_h / 2
    middle = (cx - CANVAS_W * 0.14, cy - panel_h * 0.1, CANVAS_W * 0.28, panel_h * 0.2)
    kept: List[Dict[str, Any]] = []
    budget = HUD_MAX_PANEL_COVER * CANVAS_W * panel_h
    for item in sorted(placed, key=lambda p: p["w"] * p["h"]):
        rect = (item["x"], item["y"], item["w"], item["h"])
        if _overlap(rect, middle) > 0 or item["w"] * item["h"] > budget:
            continue
        budget -= item["w"] * item["h"]
        kept.append(item)
        occupied.append(rect)
    kept.sort(key=lambda p: (p["y"], p["x"]))
    return kept, occupied


def choose_caption_y(
    occupied: Sequence[Tuple[float, float, float, float]], panel_y: int, panel_h: int,
) -> float:
    """Caption centre (canvas fraction): the first row below the action that crosses no HUD element."""
    centre_zone = (CANVAS_W * 0.3, panel_y + panel_h * 0.44, CANVAS_W * 0.4, panel_h * 0.12)
    best, best_cost = CAPTION_CANDIDATES[0], None
    for candidate in CAPTION_CANDIDATES:
        band = (CANVAS_W * 0.1, (candidate - CAPTION_BAND / 2) * CANVAS_H, CANVAS_W * 0.8, CAPTION_BAND * CANVAS_H)
        if band[1] < panel_y:
            continue
        cost = sum(_overlap(band, rect) for rect in list(occupied) + [centre_zone])
        if best_cost is None or cost < best_cost - 1e-6:
            best, best_cost = candidate, cost
        if cost == 0:
            break
    return best


def _between(ranges: Sequence[Tuple[float, float]]) -> str:
    return "+".join(f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in ranges)


def _minus(ranges: Sequence[Tuple[float, float]], holes: Sequence[Tuple[float, float]]) -> List[Tuple[float, float]]:
    out = [(a, b) for a, b in ranges if b > a]
    for lo, hi in holes:
        out = [piece for a, b in out for piece in ((a, min(b, lo)), (max(a, hi), b)) if piece[1] > piece[0]]
    return out


def _compose(profile: GamingProfile, tag: str, out: str) -> Dict[str, Any]:
    """One camera's layout as filter lines reading ``[<tag>_*]`` copies of the source and ending in ``out``."""
    width, height = profile.width, profile.height
    if profile.facecam is not None:
        cam, cam_h = facecam_crop(profile.facecam, profile.face)
        panel_y, panel_h = cam_h, CANVAS_H - cam_h
    else:
        cam, cam_h = None, 0
        panel_y, panel_h = (CANVAS_H - GAMEPLAY_ONLY_HEIGHT) // 2, GAMEPLAY_ONLY_HEIGHT
    fitted: Optional[Tuple[int, int]] = None
    if profile.focus is not None:
        crop, fitted = still_crop(profile.focus, width, height, panel_h, fill=profile.focus_fill,
                                  avoid=profile.facecam)
    else:
        # A keyed-out streamer and a pictured logo sit over the game: the crop slides off them where it can,
        # so neither is shown twice or cut in half.
        aside = profile.facecam if cam else profile.sticker
        if profile.virtual_camera and aside is not None and not (
                aside.x <= KEYED_EDGE_REACH * width or aside.x2 >= (1 - KEYED_EDGE_REACH) * width):
            aside = None        # a keyed-out head away from the sides is in the game's picture: nothing to step off
        crop = gameplay_crop(width, height, panel_h, aside)
        if cam is None and aside is not None and min(crop.x2, aside.x2) > max(crop.x, aside.x):
            # A logo is stepped off only when that frees it. One the shifted crop would still cut stays in
            # the centred picture, whole or as it was.
            crop = gameplay_crop(width, height, panel_h, None)
    cutouts, occupied = place_hud(profile.hud, crop, width, panel_y, panel_h)

    inputs = [f"{tag}_game"] + ([f"{tag}_cam"] if cam else []) + [f"{tag}_h{i}" for i in range(len(cutouts))]
    sharpen = f",{UPSCALE_SHARPEN_FILTER}" if crop.w < CANVAS_W else ""
    if fitted is not None:
        # Wider than the panel: shown whole across the width, over a blurred copy of itself.
        fit_w, fit_h = fitted
        inputs.append(f"{tag}_fill")
        lines = [
            f"[{tag}_fill]crop={crop.w}:{crop.h}:{crop.x}:{crop.y},scale={CANVAS_W}:{panel_h}:"
            f"force_original_aspect_ratio=increase,crop={CANVAS_W}:{panel_h},boxblur=24:2,eq=brightness=-0.12[{tag}_blur]",
            f"[{tag}_game]crop={crop.w}:{crop.h}:{crop.x}:{crop.y},"
            f"scale={fit_w}:{fit_h}:flags=lanczos{sharpen},setsar=1[{tag}_fitted]",
            f"[{tag}_blur][{tag}_fitted]overlay={(CANVAS_W - fit_w) // 2}:{(panel_h - fit_h) // 2},setsar=1[{tag}_p0]",
        ]
    else:
        lines = [
            f"[{tag}_game]crop={crop.w}:{crop.h}:{crop.x}:{crop.y},"
            f"scale={CANVAS_W}:{panel_h}:flags=lanczos{sharpen},setsar=1[{tag}_p0]"
        ]
    for i, item in enumerate(cutouts):
        r: Rect = item["source"]
        lines.append(f"[{tag}_h{i}]crop={r.w}:{r.h}:{r.x}:{r.y},scale={item['w']}:{item['h']}:flags=lanczos[{tag}_c{i}]")
        lines.append(f"[{tag}_p{i}][{tag}_c{i}]overlay={item['x']}:{item['y'] - panel_y}[{tag}_p{i + 1}]")
    panel = f"[{tag}_p{len(cutouts)}]"
    mark = profile.sticker
    # The sticker goes up only when the crop does not show the picture where it is: it cannot be taken out
    # of the game, and twice is worse than once. (_compose already slid the crop off a picture at its edge.)
    if mark is not None and profile.sticker_mask and cam is None and (mark.x2 <= crop.x or mark.x >= crop.x2):
        cols, rows, cells = profile.sticker_mask
        out_w = clamp_even(int(round(min(CANVAS_W * STICKER_WIDTH, mark.w * STICKER_MAX_SCALE))), 2, CANVAS_W)
        out_h = clamp_even(int(round(out_w * mark.h / mark.w)), 2, CANVAS_H)
        top = panel_y + int(CANVAS_H * STICKER_TOP)
        side = int(CANVAS_W * STICKER_SIDE)
        spots = [CANVAS_W - side - out_w, side]
        if mark.x + mark.w / 2 < width / 2:
            spots.reverse()                                          # on the side it came from, if that is free
        free = [x for x in spots if not any(_overlap((x, top, out_w, out_h), box) > 0 for box in occupied)]
        if free:
            x = free[0]
            inputs += [f"{tag}_st", f"{tag}_sm"]
            boxes = ",".join(f"drawbox=x={cx}:y={cy}:w={cw}:h={ch}:color=white:t=fill" for cx, cy, cw, ch in cells)
            lines += [
                # The mask is drawn on a copy of the picture so it runs as long as the clip does.
                f"[{tag}_sm]crop={2 * cols}:{2 * rows}:0:0,scale={cols}:{rows}:out_range=tv,format=yuv444p,"
                f"drawbox=x=0:y=0:w={cols}:h={rows}:color=black:t=fill,{boxes},"
                f"scale={out_w}:{out_h}:flags=bilinear,format=gray[{tag}_sk]",
                f"[{tag}_st]crop={mark.w}:{mark.h}:{mark.x}:{mark.y},scale={out_w}:{out_h}:flags=lanczos,"
                f"format=yuv444p[{tag}_sc]",
                f"[{tag}_sc][{tag}_sk]alphamerge[{tag}_sa]",
                f"{panel}[{tag}_sa]overlay={x}:{top - panel_y}:enable='gte(t,{STICKER_FROM_SECONDS:g})'[{tag}_ps]",
            ]
            panel = f"[{tag}_ps]"
            occupied = list(occupied) + [(x, top, out_w, out_h)]
    if cam:
        lines.append(
            f"[{tag}_cam]crop={cam.w}:{cam.h}:{cam.x}:{cam.y},"
            f"scale={CANVAS_W}:{cam_h}:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1[{tag}_top]"
        )
        lines.append(f"[{tag}_top]{panel}vstack=inputs=2{out}")
    elif panel_h == CANVAS_H:
        lines.append(f"{panel}null{out}")
    else:
        lines.append(f"{panel}pad={CANVAS_W}:{CANVAS_H}:0:{panel_y}:black{out}")
    report = {
        "facecam": cam.as_list() if cam else None,
        "facecam_panel": [0, 0, CANVAS_W, cam_h] if cam else None,
        "gameplay": crop.as_list(),
        "gameplay_panel": [0, panel_y, CANVAS_W, panel_h],
        "hud": [{"source": item["source"].as_list(), "at": [item["x"], item["y"], item["w"], item["h"]]}
                for item in cutouts],
        "crosshair": profile.crosshair,
    }
    return {"inputs": inputs, "lines": lines, "report": report, "occupied": occupied,
            "cam_h": cam_h, "panel_y": panel_y, "panel_h": panel_h}


def still_crop(
    focus: Rect, width: int, height: int, panel_h: int, fill: bool = False, avoid: Optional[Rect] = None
) -> Tuple[Rect, Optional[Tuple[int, int]]]:
    """How a still picture's detailed part goes into the gameplay panel: (crop, fitted size or None).

    Narrower than the panel: the crop widens around it and fills the panel. Wider: more of the picture above and
    below is taken in. When the whole height is not enough, a picture whose subject is ``focus`` (``fill``) is
    cropped to the panel around it; any other is fitted to the panel width instead of cut. ``avoid`` is the
    camera, already shown on top: the crop steps off it sideways when the frame has the room.
    """
    aspect = CANVAS_W / panel_h
    cx, cy = focus.x + focus.w / 2, focus.y + focus.h / 2
    if focus.w <= focus.h * aspect:
        cw = clamp_even(int(round(focus.h * aspect)), 2, width)
        ch = clamp_even(int(round(cw / aspect)), 2, height)
    else:
        ch = int(round(focus.w / aspect))
        if ch > height and not fill:
            x = clamp_even(focus.x, 0, width - 2)
            cw = clamp_even(focus.w, 2, width - x)
            fit_h = clamp_even(int(round(CANVAS_W * height / cw)), 2, panel_h)
            return Rect(x, 0, cw, round_to_even(height)), (CANVAS_W, fit_h)
        if ch > height:
            ch = round_to_even(height)
            cw = clamp_even(int(round(ch * aspect)), 2, width)
        else:
            cw, ch = clamp_even(focus.w, 2, width), clamp_even(ch, 2, height)
    x = clamp_even(int(round(cx - cw / 2)), 0, width - cw)
    y = clamp_even(int(round(cy - ch / 2)), 0, height - ch)
    if avoid is not None and x < avoid.x2 and avoid.x < x + cw and y < avoid.y2 and avoid.y < y + ch:
        # Off the camera, as long as the subject stays in the picture: the shift ends where the focus would
        # leave the crop.
        clear = avoid.x2 if avoid.x + avoid.w / 2 < width / 2 else avoid.x - cw
        lowest, highest = max(0, focus.x2 - cw), min(width - cw, focus.x)
        if focus.w <= cw and lowest <= clear <= highest:
            x = clamp_even(clear, 0, width - cw)
    return Rect(x, y, cw, ch), None


def _cover_lines(width: int, height: int) -> List[str]:
    # Without the camera (a cutscene, a loading screen) the picture fills the frame: a full-height crop of
    # its middle, where games put what matters. A letterboxed strip would shrink it to a third of the screen.
    cover_w = clamp_even(int(round(height * CANVAS_W / CANVAS_H)), 2, width)
    return [
        f"[gm_fit]crop={cover_w}:{height}:{clamp_even((width - cover_w) // 2, 0, width - cover_w)}:0,"
        f"scale={CANVAS_W}:{CANVAS_H}:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1[gm_alt]"
    ]


def build_gaming_layout(profile: GamingProfile, duration: float) -> Dict[str, Any]:
    """The filter graph and report for a clip classified as gameplay.

    One self-contained ``filter_complex`` ending in ``[vout]`` (1080x1920): the
    camera panel, the gameplay crop with HUD cut-outs overlaid, and, for spans
    where the stream shows something else, the whole frame over a blurred fill.
    """
    fallbacks = [(a, b) for a, b in profile.fallback_ranges if b > a]
    # The last span runs to the final frame even when the stream is a little longer than probed.
    fallbacks = [(a, 1e6 if b >= duration - 0.05 else b) for a, b in fallbacks]
    part = _compose(profile, "gm", "[gm_out]" if fallbacks else "[vout]")

    labels = part["inputs"] + (["gm_bg", "gm_fit"] if fallbacks else [])
    graph = [f"[0:v]split={len(labels)}" + "".join(f"[{name}]" for name in labels)] + part["lines"]
    if fallbacks:
        graph.append("[gm_bg]nullsink")
        graph.extend(_cover_lines(profile.width, profile.height))
        graph.append(f"[gm_out][gm_alt]overlay=0:0:enable='{_between(fallbacks)}'[vout]")

    report = dict(part["report"])
    crosshair = report.pop("crosshair")
    report["fallback_ranges"] = [[round(a, 3), round(min(b, duration), 3)] for a, b in fallbacks]
    report["crosshair"] = crosshair
    return {
        "kind": "gaming",
        "video_filter": ";".join(graph),
        "mode": "complex",
        "caption_y": choose_caption_y(part["occupied"], part["panel_y"], part["panel_h"]),
        # The hook title sits on the seam under the camera; without a camera it keeps its usual place.
        "hook_center_y": (part["cam_h"] / CANVAS_H) if part["cam_h"] else None,
        "gaming": report,
    }


def build_split_layout(profile: GamingProfile, duration: float) -> Dict[str, Any]:
    """A side-by-side comparison: the left picture over the right one while the screen is split, and the
    full-height middle of the picture in between."""
    width, height = profile.width, profile.height
    gap = clamp_even(int(round(width * SPLIT_GAP)), 2, width // 8)
    half_w = clamp_even(width // 2 - gap, 2, width // 2)
    half_h = clamp_even(int(round(half_w * SPLIT_PANEL_H / CANVAS_W)), 2, height)
    half_w = clamp_even(int(round(half_h * CANVAS_W / SPLIT_PANEL_H)), 2, half_w)
    # Comparison videos put their title across the top of the divider: the crop sits low to leave it out.
    y = clamp_even(int(round((height - half_h) * SPLIT_CROP_DOWN)), 0, height - half_h)
    left = Rect(clamp_even((width // 2 - gap - half_w) // 2, 0, width // 2 - gap - half_w), y, half_w, half_h)
    right_x0 = width // 2 + gap
    right = Rect(clamp_even(right_x0 + (width - right_x0 - half_w) // 2, right_x0, width - half_w), y, half_w, half_h)
    spans = [(a, 1e6 if b >= duration - 0.05 else b) for a, b in profile.split_ranges if b > a]
    graph = ["[0:v]split=3[gm_fit][sp_l][sp_r]"] + _cover_lines(width, height)
    for tag, r, name in (("sp_l", left, "sp_top"), ("sp_r", right, "sp_bottom")):
        graph.append(
            f"[{tag}]crop={r.w}:{r.h}:{r.x}:{r.y},scale={CANVAS_W}:{SPLIT_PANEL_H}:flags=lanczos,"
            f"{UPSCALE_SHARPEN_FILTER},setsar=1[{name}]"
        )
    graph.append("[sp_top][sp_bottom]vstack=inputs=2[sp_out]")
    graph.append(f"[gm_alt][sp_out]overlay=0:0:enable='{_between(spans)}'[vout]")
    cover_w = clamp_even(int(round(height * CANVAS_W / CANVAS_H)), 2, width)
    report = {
        "facecam": None, "facecam_panel": None,
        "gameplay": [clamp_even((width - cover_w) // 2, 0, width - cover_w), 0, cover_w, round_to_even(height)],
        "gameplay_panel": [0, 0, CANVAS_W, CANVAS_H],
        "hud": [],
        "fallback_ranges": [],
        "crosshair": False,
        "split": {"left": left.as_list(), "right": right.as_list(),
                  "between": [[round(a, 3), round(min(b, duration), 3)] for a, b in spans]},
    }
    return {
        "kind": "gaming",
        "video_filter": ";".join(graph),
        "mode": "complex",
        "caption_y": CAPTION_CANDIDATES[0],
        "hook_center_y": None,
        "gaming": report,
    }


def build_camera_layouts(profiles: Sequence[GamingProfile], duration: float) -> Dict[str, Any]:
    """The filter graph for a clip in which several cameras take turns (``profile.active`` says when).

    The full-frame crop is the base; each camera's layout is laid over it for the seconds that camera is on
    screen. Captions and the hook title keep one place for the whole clip: the one the longest camera needs.
    """
    width, height = profiles[0].width, profiles[0].height
    parts = []
    for n, profile in enumerate(profiles):
        shown = _minus(profile.active, profile.fallback_ranges)
        if shown:
            parts.append((profile, shown, _compose(profile, f"g{n}", f"[g{n}_out]"), f"[g{n}_out]"))
    labels = [name for _, _, part, _ in parts for name in part["inputs"]] + ["gm_fit"]
    graph = [f"[0:v]split={len(labels)}" + "".join(f"[{name}]" for name in labels)]
    for _, _, part, _ in parts:
        graph.extend(part["lines"])
    graph.extend(_cover_lines(width, height))
    previous = "[gm_alt]"
    for n, (_, shown, _, out) in enumerate(parts):
        enable = _between([(a, 1e6 if b >= duration - 0.05 else b) for a, b in shown])
        target = "[vout]" if n == len(parts) - 1 else f"[gm_m{n}]"
        graph.append(f"{previous}{out}overlay=0:0:enable='{enable}'{target}")
        previous = target

    def seconds(item) -> float:
        return sum(b - a for a, b in item[1])

    lead = max(parts, key=seconds)[2]
    segments = [
        {**part["report"], "between": [[round(a, 3), round(min(b, duration), 3)] for a, b in shown]}
        for _, shown, part, _ in parts
    ]
    everything = [(a, b) for _, shown, _, _ in parts for a, b in shown]
    report = dict(lead["report"])
    report["fallback_ranges"] = [[round(a, 3), round(b, 3)] for a, b in _minus([(0.0, duration)], everything)]
    report["segments"] = segments
    # One caption row for the clip: the candidate that crosses the least HUD over all cameras, by time on screen.
    panel_y = max(part["panel_y"] for _, _, part, _ in parts)
    best, best_cost = CAPTION_CANDIDATES[0], None
    for candidate in CAPTION_CANDIDATES:
        band = (CANVAS_W * 0.1, (candidate - CAPTION_BAND / 2) * CANVAS_H, CANVAS_W * 0.8, CAPTION_BAND * CANVAS_H)
        if band[1] < panel_y:
            continue
        cost = 0.0
        for item in parts:
            part = item[2]
            centre_zone = (CANVAS_W * 0.3, part["panel_y"] + part["panel_h"] * 0.44, CANVAS_W * 0.4, part["panel_h"] * 0.12)
            cost += seconds(item) * sum(_overlap(band, rect) for rect in list(part["occupied"]) + [centre_zone])
        if best_cost is None or cost < best_cost - 1e-6:
            best, best_cost = candidate, cost
        if cost == 0:
            break
    return {
        "kind": "gaming",
        "video_filter": ";".join(graph),
        "mode": "complex",
        "caption_y": best,
        "hook_center_y": (lead["cam_h"] / CANVAS_H) if lead["cam_h"] else None,
        "gaming": report,
    }


def choose_gaming_layout(path: Path, width: int, height: int, duration: float) -> Optional[Dict[str, Any]]:
    """The gaming layout for this clip, or None to keep the regular layouts.

    None when the switch is off, the clip is not gameplay, or anything fails.
    """
    if gaming_layout_mode() != "auto":
        return None
    try:
        profiles = analyze_gaming_clip(path, width, height, duration)
        for profile in profiles:
            logger.info("Gaming analysis: %s", profile.summary())
        profiles = [profile for profile in profiles if profile.is_gaming]
        if not profiles:
            return None
        if profiles[0].split_ranges:
            return build_split_layout(profiles[0], duration)
        if len(profiles) == 1 and not profiles[0].active:
            return build_gaming_layout(profiles[0], duration)
        return build_camera_layouts(profiles, duration)
    except Exception as exc:  # noqa: BLE001 - detection never breaks a render
        logger.warning("Gaming layout failed (%s); using the regular layout", exc)
        return None
