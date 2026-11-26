"""Two webcams side by side: one stacked layout for the whole clip, each pane trimmed to its picture.

Video-call and "chat with strangers" streams put two webcam pictures next to each other in one 16:9 frame,
divided by a straight seam down the middle. The face-driven layouts read such a frame through its faces, and
faces are an unreliable witness there: one person wears a mask or a costume, turns away, or sits in the dark,
and the clip either lost the call stack altogether (a small 16:9 strip on a blurred canvas) or, once the other
face came back, flipped between stacked panels and a single crop in the middle of the shot.

The seam itself does not move. ``analyze_call_split`` finds it in a handful of small grey frames: a column near
the middle where the picture jumps in nearly every frame, at the same place throughout the clip, with no face
ever across it. One side must show a face most of the time, and the other a face now and then or at least a
moving picture (a masked person still moves; a slide does not). Then both panes are stacked for the whole
clip, left on top, and nothing flips.

A pane is often not all picture: a webcam in the upper right with a black box (or a flat chat panel) below
it. ``active_rect`` trims dark and flat borders off a pane, so the panel shows the webcam and not a slab of
black. ``vertical_split`` uses the same trimming for its face tiles (``fit_tiles_to_panes``).

Everything here is deterministic and returns None whenever the evidence is weak; the caller keeps the layout
it would have used.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import subprocess

import numpy as np

from .common import UPSCALE_SHARPEN_FILTER, logger
from .ffmpeg import clamp_even, round_to_even

Box = Tuple[float, float, float, float]
FaceFrame = Tuple[float, Sequence[Box]]
Rect = Tuple[int, int, int, int]  # (x, y, w, h), source pixels

OUT_W = 1080
PANEL_H = 960
PANEL_ASPECT = OUT_W / PANEL_H

# --- Sampling ---------------------------------------------------------------
PROBE_W = 320
PROBE_MAX_FRAMES = 48
PROBE_MAX_FPS = 2.0
MIN_FRAMES = 6

# --- The seam ---------------------------------------------------------------
SEAM_BAND = 0.06            # of the width either side of the middle where the seam is looked for
SEAM_MIN_RATIO = 3.0        # its column jump over the typical column jump around it (temporal median)
SEAM_MIN_LEVEL = 6.0        # ... and at least this many grey levels on average down the column
SEAM_FRAME_RATIO = 2.2      # one frame counts as showing the seam when its jump there is this much above its own band
SEAM_MIN_PERSISTENCE = 0.8  # share of the frames that show it, within a column either side
SEAM_FACE_TOLERANCE = 0.01  # of the width a face box may reach over the seam and still be on its side
SEAM_MAX_STRADDLE = 0.05    # share of the face samples allowed to have a face across the seam

# --- Who is in the panes -------------------------------------------------------
FACE_SIDE_MAIN = 0.5        # one pane shows a face in at least this share of the samples ...
FACE_SIDE_OTHER = 0.15      # ... and the other in this share, or else moves
MOVING_PANE_LEVEL = 1.0     # mean grey-level change between samples inside a pane that counts as a live camera
CROWDED_SIDE_SHARE = 0.4    # a pane with two faces in this share of the samples is a gallery, not one webcam

# --- Dead borders inside a pane -------------------------------------------------
DEAD_BLACK_LEVEL = 24       # a pixel never brighter than this is black
DEAD_FLAT_LEVEL = 2.5       # ... or one that never changes and matches its neighbours is a flat panel
DEAD_LINE_SHARE = 0.12      # a row or a column with less live picture than this is border
ACTIVE_MIN_SHARE = 0.2      # a live area smaller than this share of the pane is not trusted

# --- Composition -----------------------------------------------------------------
PANEL_FACE_FRAC = 0.30      # face height as a share of the panel, at most (head and shoulders)
PANEL_FACE_Y = 0.42         # face centre from the top of the panel
MAX_PANEL_UPSCALE = 2.6     # never enlarge a pane more than this
CAPTION_Y = 0.5             # captions on the seam between the two panels, clear of both faces


def read_probe_frames(input_path: Path, duration: float) -> Optional[np.ndarray]:
    """Small grey frames spread over the clip, (N, h, PROBE_W) uint8, or None when they cannot be read."""
    from .ffmpeg import ffprobe_video_size

    width, height = ffprobe_video_size(input_path)
    if width <= 0 or height <= 0 or duration <= 0:
        return None
    out_h = max(2, round_to_even(int(round(PROBE_W * height / width))))
    fps = min(PROBE_MAX_FPS, PROBE_MAX_FRAMES / max(duration, 1e-3))
    try:
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-i", str(input_path), "-an", "-sn",
             "-vf", f"fps={fps:.4f},scale={PROBE_W}:{out_h},format=gray", "-f", "rawvideo", "-"],
            capture_output=True, timeout=max(60, int(duration * 2)),
        )
    except Exception as exc:  # noqa: BLE001 - no call split for this clip
        logger.debug("Call-split probe frames unavailable (%s)", exc)
        return None
    size = PROBE_W * out_h
    count = len(result.stdout) // size if result.returncode == 0 else 0
    if count < MIN_FRAMES:
        return None
    return np.frombuffer(result.stdout[:count * size], dtype=np.uint8).reshape(count, out_h, PROBE_W)


def find_seam(frames: np.ndarray) -> Optional[int]:
    """The column (probe pixels) of a straight seam near the middle that holds in nearly every frame, or None.

    Two webcam pictures meet at a column where the picture jumps: the mean jump down that column is several
    times the jump between the columns around it, frame after frame, at the same place. An edge in one camera's
    picture (a door frame, a pole) moves with the camera or fades with the light, and a cut away from the call
    loses it for the rest of the shot.
    """
    if frames is None or len(frames) < MIN_FRAMES:
        return None
    count, _h, w = frames.shape
    lo = max(1, int(w * (0.5 - SEAM_BAND)))
    hi = min(w - 2, int(w * (0.5 + SEAM_BAND)))
    if hi - lo < 6:
        return None
    jumps = np.stack([
        np.abs(np.diff(frame.astype(np.float32), axis=1)).mean(axis=0) for frame in frames
    ])                                                     # (N, w - 1): column x vs x + 1
    typical = np.median(jumps, axis=0)
    band = typical[lo:hi]
    column = lo + int(np.argmax(band))
    around = np.concatenate([typical[lo:max(lo, column - 2)], typical[column + 3:hi]])
    base = float(np.median(around)) if around.size else 0.0
    if typical[column] < SEAM_MIN_LEVEL or typical[column] < SEAM_MIN_RATIO * max(base, 1.0):
        return None
    shown = 0
    for row in jumps:
        local = row[lo:hi]
        if row[max(0, column - 1):column + 2].max() >= SEAM_FRAME_RATIO * max(float(np.median(local)), 1.0):
            shown += 1
    if shown < SEAM_MIN_PERSISTENCE * count:
        return None
    return column + 1          # the first column of the right-hand picture


def active_rect(frames: np.ndarray) -> Optional[Rect]:
    """The live part of a pane, (x, y, w, h) in the frames' pixels: dark and flat borders trimmed off.

    A pixel is dead when it is never brighter than black, or when it never changes and matches its neighbours
    (a flat chat panel, a solid fill). Rows and columns with hardly any live picture are trimmed from every
    side. None when the pane is all border, or the live part is too small to trust.
    """
    if frames is None or len(frames) < 2:
        return None
    stack = frames.astype(np.float32)
    _count, h, w = stack.shape
    if h < 4 or w < 4:
        return None
    brightest = stack.max(axis=0)
    still = stack.std(axis=0)
    mean = stack.mean(axis=0)
    # Spatial variation of the mean picture: a flat fill has none, a still but detailed background has some.
    spatial = np.zeros_like(mean)
    spatial[:, 1:] += np.abs(np.diff(mean, axis=1))
    spatial[1:, :] += np.abs(np.diff(mean, axis=0))
    dead = (brightest <= DEAD_BLACK_LEVEL) | ((still <= DEAD_FLAT_LEVEL) & (spatial <= DEAD_FLAT_LEVEL))
    live = ~dead
    rows = live.mean(axis=1)
    cols = live.mean(axis=0)
    keep_rows = np.flatnonzero(rows >= DEAD_LINE_SHARE)
    keep_cols = np.flatnonzero(cols >= DEAD_LINE_SHARE)
    if not keep_rows.size or not keep_cols.size:
        return None
    y0, y1 = int(keep_rows[0]), int(keep_rows[-1]) + 1
    x0, x1 = int(keep_cols[0]), int(keep_cols[-1]) + 1
    if (x1 - x0) * (y1 - y0) < ACTIVE_MIN_SHARE * w * h:
        return None
    return x0, y0, x1 - x0, y1 - y0


def _to_source(rect: Rect, offset_x: int, scale_x: float, scale_y: float, width: int, height: int) -> Rect:
    x, y, w, h = rect
    x0 = max(0, int(np.floor((x + offset_x) * scale_x)))
    y0 = max(0, int(np.floor(y * scale_y)))
    x1 = min(width, int(np.ceil((x + offset_x + w) * scale_x)))
    y1 = min(height, int(np.ceil((y + h) * scale_y)))
    return x0, y0, max(2, x1 - x0), max(2, y1 - y0)


def pane_rects(frames: np.ndarray, seam: int, width: int, height: int) -> Tuple[Rect, Rect]:
    """The live part of the left and right panes in source pixels (the whole half when nothing is trimmed)."""
    _count, h, w = frames.shape
    scale_x, scale_y = width / w, height / h
    halves = []
    for start, end in ((0, seam), (seam, w)):
        found = active_rect(frames[:, :, start:end])
        whole = (0, 0, end - start, h)
        halves.append(_to_source(found or whole, start, scale_x, scale_y, width, height))
    return halves[0], halves[1]


def _side_faces(face_samples: Sequence[FaceFrame], seam_x: float, width: int) -> Tuple[List[Box], List[Box], float, float, int, int]:
    """Faces on the left and right of the seam, the share of samples with a face on each, and how many
    samples hold a face across the seam / two faces on one side."""
    tolerance = SEAM_FACE_TOLERANCE * width
    left: List[Box] = []
    right: List[Box] = []
    left_seen = right_seen = straddle = crowded = 0
    for _t, faces in face_samples:
        on_left = [f for f in faces if f[0] + f[2] <= seam_x + tolerance]
        on_right = [f for f in faces if f[0] >= seam_x - tolerance]
        if any(f[0] + f[2] > seam_x + tolerance and f[0] < seam_x - tolerance for f in faces):
            straddle += 1
        if len(on_left) >= 2 or len(on_right) >= 2:
            crowded += 1
        if on_left:
            left_seen += 1
            left.append(max(on_left, key=lambda f: f[2] * f[3]))
        if on_right:
            right_seen += 1
            right.append(max(on_right, key=lambda f: f[2] * f[3]))
    total = max(1, len(face_samples))
    return left, right, left_seen / total, right_seen / total, straddle, crowded


def _pane_motion(frames: np.ndarray, start: int, end: int) -> float:
    pane = frames[:, :, start:end].astype(np.float32)
    if len(pane) < 2:
        return 0.0
    return float(np.abs(np.diff(pane, axis=0)).mean())


def _median_box(boxes: Sequence[Box]) -> Optional[Box]:
    if not boxes:
        return None
    array = np.asarray(boxes, dtype=np.float64)
    return tuple(float(v) for v in np.median(array, axis=0))  # type: ignore[return-value]


def analyze_call_split(
    frames: Optional[np.ndarray], face_samples: Sequence[FaceFrame], width: int, height: int,
) -> Optional[Dict[str, Any]]:
    """The two webcam panes of a side-by-side call, or None when the clip is not one.

    Returns ``seam`` (source x), ``panes`` (left and right live rectangles, source pixels) and ``faces`` (each
    pane's median face box, or None for a pane whose person the detector rarely finds).
    """
    if frames is None or len(frames) < MIN_FRAMES or width <= 0 or height <= 0 or width / height <= 1.05:
        return None
    seam = find_seam(frames)
    if seam is None:
        return None
    w = frames.shape[2]
    seam_x = seam * width / w
    left, right, left_share, right_share, straddle, crowded = _side_faces(face_samples, seam_x, width)
    faced = sum(1 for _t, faces in face_samples if faces)
    if faced and straddle > SEAM_MAX_STRADDLE * max(1, len(face_samples)):
        return None         # someone sits across the "seam": one camera, an edge in its picture
    if crowded > CROWDED_SIDE_SHARE * max(1, len(face_samples)):
        return None         # a gallery: its own layout shows every tile
    main, other = max(left_share, right_share), min(left_share, right_share)
    if main < FACE_SIDE_MAIN:
        return None
    if other < FACE_SIDE_OTHER:
        quiet = (seam, w) if right_share < left_share else (0, seam)
        if _pane_motion(frames, *quiet) < MOVING_PANE_LEVEL:
            return None     # a still picture beside a speaker (slides, a document): not a second camera
    left_pane, right_pane = pane_rects(frames, seam, width, height)
    return {
        "seam": int(round(seam_x)),
        "panes": [left_pane, right_pane],
        "faces": [_median_box(left) if left_share >= FACE_SIDE_OTHER else None,
                  _median_box(right) if right_share >= FACE_SIDE_OTHER else None],
    }


def pane_crop(pane: Rect, face: Optional[Box], aspect: float = PANEL_ASPECT) -> Rect:
    """An even (x, y, w, h) window of ``aspect`` inside ``pane``: around the face, else the pane's middle.

    The window fills the pane in its narrower direction, and closes in on a small face down to head and
    shoulders (``PANEL_FACE_FRAC``), never past ``MAX_PANEL_UPSCALE``.
    """
    px, py, pw, ph = pane
    ch = min(float(ph), pw / aspect)
    if face is not None:
        ch = min(ch, max(face[3] / PANEL_FACE_FRAC, PANEL_H / MAX_PANEL_UPSCALE))
    ch = max(2.0, ch)
    cw = min(float(pw), ch * aspect)
    cw_i, ch_i = max(2, round_to_even(int(cw))), max(2, round_to_even(int(ch)))
    cw_i, ch_i = min(cw_i, max(2, pw // 2 * 2)), min(ch_i, max(2, ph // 2 * 2))
    if face is not None:
        cx, cy = face[0] + face[2] / 2, face[1] + face[3] / 2
        x = cx - cw_i / 2
        y = cy - ch_i * PANEL_FACE_Y
    else:
        x = px + (pw - cw_i) / 2
        y = py + (ph - ch_i) / 2
    x = clamp_even(int(x), px + (px % 2), max(px + (px % 2), px + pw - cw_i))
    y = clamp_even(int(y), py + (py % 2), max(py + (py % 2), py + ph - ch_i))
    return x, y, cw_i, ch_i


def _panel(label: str, crop: Rect, out: str) -> str:
    x, y, w, h = crop
    return (f"{label}crop={w}:{h}:{x}:{y},scale={OUT_W}:{PANEL_H}:flags=lanczos,"
            f"{UPSCALE_SHARPEN_FILTER},setsar=1{out}")


def build_call_stack(call: Dict[str, Any]) -> Dict[str, Any]:
    """The vertical plan for ``analyze_call_split``'s result: left pane over right pane, the whole clip."""
    crops = [pane_crop(pane, face) for pane, face in zip(call["panes"], call["faces"])]
    video_filter = (
        "[0:v]split=2[cs_l][cs_r];"
        + _panel("[cs_l]", crops[0], "[cs_top]") + ";"
        + _panel("[cs_r]", crops[1], "[cs_bottom]") + ";"
        "[cs_top][cs_bottom]vstack=inputs=2[vout]"
    )
    return {
        "kind": "stack",
        "video_filter": video_filter,
        "mode": "complex",
        "caption_y": CAPTION_Y,
        "crops": crops,
        "upscale": max(OUT_W / max(crop[2], 1) for crop in crops),
    }


def choose_call_split_layout(
    input_path: Path, width: int, height: int, duration: float, face_samples: Sequence[FaceFrame],
) -> Optional[Dict[str, Any]]:
    """The stacked call layout for this clip, or None to keep the regular layouts. Never raises."""
    try:
        if not _worth_a_look(face_samples, width):
            return None
        call = analyze_call_split(read_probe_frames(input_path, duration), face_samples, width, height)
        if call is None:
            return None
        layout = build_call_stack(call)
        logger.info("Side-by-side call: seam at x=%d, panes %s, crops %s",
                    call["seam"], call["panes"], layout["crops"])
        return layout
    except Exception as exc:  # noqa: BLE001 - detection never breaks a render
        logger.warning("Call split detection failed (%s); using the regular layout", exc)
        return None


def _worth_a_look(face_samples: Sequence[FaceFrame], width: int) -> bool:
    """Cheap gate before any frame is decoded: faces kept to one half of the frame in most samples."""
    if not face_samples or width <= 0:
        return False
    middle = width / 2
    halved = 0
    for _t, faces in face_samples:
        if faces and all(f[0] + f[2] <= middle * 1.1 or f[0] >= middle * 0.9 for f in faces):
            halved += 1
    return halved >= FACE_SIDE_MAIN * len(face_samples)


def fit_tiles_to_panes(
    regions: Dict[str, Dict[str, int]], frames: Optional[np.ndarray], width: int, height: int,
) -> Dict[str, Dict[str, int]]:
    """``vertical_split`` face tiles kept inside the live part of their half of the frame.

    A tile that reaches into a pane's dead border (the black box under an upper-right webcam) is moved and,
    if it must, shrunk to the panel's shape inside the live part, still around the face. Tiles already inside
    are returned unchanged.
    """
    if frames is None or len(frames) < 2:
        return regions
    w = frames.shape[2]
    seam = find_seam(frames) or w // 2
    panes = pane_rects(frames, seam, width, height)
    fitted = dict(regions)
    for key, pane in zip(("left", "right"), panes):
        region = regions.get(key)
        if not region:
            continue
        tx, ty, tw, th = region["tile_x"], region["tile_y"], region["tile_w"], region["tile_h"]
        px, py, pw, ph = pane
        inside = tx >= px and ty >= py and tx + tw <= px + pw and ty + th <= py + ph
        if inside:
            continue
        # The panel's shape, no larger than the tile was nor than the live part allows; the face keeps its
        # place in the tile (``cluster_two_face_regions`` puts its centre 42 % down).
        ch = min(float(th), float(ph), pw / PANEL_ASPECT, max(2.0, tw / PANEL_ASPECT))
        cw = min(float(pw), ch * PANEL_ASPECT)
        cw_i, ch_i = max(2, round_to_even(int(cw))), max(2, round_to_even(int(ch)))
        x = clamp_even(int(region["center_x"] - cw_i / 2), px + (px % 2), max(px + (px % 2), px + pw - cw_i))
        y = clamp_even(int(region["center_y"] - ch_i * 0.42), py + (py % 2), max(py + (py % 2), py + ph - ch_i))
        fitted[key] = {**region, "tile_x": x, "tile_y": y, "tile_w": cw_i, "tile_h": ch_i}
    return fitted


def split_regions_from_call(call: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """``cluster_two_face_regions``-shaped tiles from a call split: for ``vertical_split`` when one of the
    two people is never found (a mask, a costume, the dark)."""
    regions = {}
    for key, pane, face in zip(("left", "right"), call["panes"], call["faces"]):
        x, y, w, h = pane_crop(pane, face)
        regions[key] = {
            "center_x": int(x + w / 2), "center_y": int(y + h / 2),
            "roi_x": x, "roi_y": y, "roi_w": w, "roi_h": h,
            "tile_x": x, "tile_y": y, "tile_w": w, "tile_h": h,
        }
    return regions
