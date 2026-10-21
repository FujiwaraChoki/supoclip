"""Face-aware 9:16 layouts for talking-head and video-call footage.

Auto vertical used to crop a full-height 9:16 window (about 608px wide on a
1080p frame) and scale it to 1080x1920. That is a ~1.8x upscale, and it cannot
move the subject: a webcam face sitting in the lower half of the 16:9 frame
stays in the lower half, with the background filling the top and captions
landing on the mouth.

This module picks a layout from the face boxes already measured for reframing:

- Two stable side-by-side webcams (a call) become a stacked pair of tiles.
  Each tile is a mild ~1.1x scale of that person's half, with the face in the
  upper part of the tile and captions in the gap between them.
- A single low webcam face is placed on a blurred frame, cropped wide enough
  to stay under ~1.32x and shifted so the face sits in the upper third.
- A gallery of three or more faces is shown in full (a downscale) on the blur,
  instead of zooming into one tiny tile.
- A face already framed in the upper half, including a close podcast two-shot,
  returns None so the tracked crop and active-speaker cuts stay in charge.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .common import UPSCALE_SHARPEN_FILTER
from .ffmpeg import clamp_even, round_to_even

OUT_W = 1080
OUT_H = 1920
STAGE_MAX_UPSCALE = 1.32
STAGE_FACE_Y = 0.38
STACK_TOP = 168
STACK_TILE_H = 800
STACK_BOTTOM = 1120
STACK_CAPTION_Y = ((STACK_TOP + STACK_TILE_H) + STACK_BOTTOM) / 2 / OUT_H

Box = Tuple[float, float, float, float]
FaceFrame = Tuple[float, Sequence[Box]]


def caption_y_below_subject(face_bottom: float, default: float = 0.80) -> float:
    """Caption-centre fraction under a face, or the template default when it already clears."""
    if face_bottom + 0.04 < default - 0.06:
        return default
    return min(0.84, max(default, face_bottom + 0.12))


def _median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    return ordered[len(ordered) // 2]


def _even_box(box: Box) -> Tuple[int, int, int, int]:
    x, y, w, h = box
    return (
        int(round(x)),
        int(round(y)),
        max(2, int(round(w))),
        max(2, int(round(h))),
    )


def dominant_face_bottom(samples: Sequence[FaceFrame], height: int) -> Optional[float]:
    """Median bottom edge of the largest face, as a fraction of the source height."""
    if height <= 0:
        return None
    bottoms: List[float] = []
    for _, faces in samples:
        if not faces:
            continue
        face = max(faces, key=lambda item: item[2] * item[3])
        bottoms.append((face[1] + face[3]) / height)
    if not bottoms:
        return None
    return _median(bottoms)


def _gallery_frames(samples: Sequence[FaceFrame]) -> bool:
    counted = [len(faces) for _, faces in samples if faces]
    if len(counted) < 4:
        return False
    crowded = sum(1 for count in counted if count >= 3)
    return crowded >= max(4, int(len(counted) * 0.45)) and _median(counted) >= 3


def _call_pairs(samples: Sequence[FaceFrame], width: int, height: int) -> List[Tuple[Box, Box]]:
    """Side-by-side webcam pairs: separated, similarly sized, with headroom above both."""
    pairs: List[Tuple[Box, Box]] = []
    for _, faces in samples:
        if len(faces) < 2:
            continue
        ranked = sorted(faces, key=lambda item: item[2] * item[3], reverse=True)[:2]
        larger, smaller = ranked
        if larger[2] * larger[3] <= 1 or smaller[2] * smaller[3] < larger[2] * larger[3] * 0.35:
            continue
        left, right = sorted(ranked, key=lambda item: item[0] + item[2] / 2)
        left_cx = left[0] + left[2] / 2
        right_cx = right[0] + right[2] / 2
        if right_cx - left_cx < width * 0.28:
            continue
        if right[0] < left[0] + left[2] - width * 0.02:
            continue
        left_cy = left[1] + left[3] / 2
        right_cy = right[1] + right[3] / 2
        if abs(left_cy - right_cy) > height * 0.18:
            continue
        # High, tightly framed faces belong to a camera operator (podcast wide
        # shot). Webcam tiles leave a band of room, blinds, or ceiling above
        # the head, which is what makes a full-height crop look empty on top.
        if left[1] < height * 0.14 or right[1] < height * 0.14:
            continue
        if left[3] > height * 0.46 or right[3] > height * 0.46:
            continue
        pairs.append((left, right))
    return pairs


def _median_box(boxes: Sequence[Box]) -> Box:
    return (
        _median([box[0] for box in boxes]),
        _median([box[1] for box in boxes]),
        _median([box[2] for box in boxes]),
        _median([box[3] for box in boxes]),
    )


def _blur_base(splits: int, *labels: str) -> str:
    sources = "".join(labels)
    return (
        f"[0:v]split={splits}{sources};"
        "[bgsrc]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,"
        "gblur=sigma=18:steps=2,scale=1080:1920:flags=bilinear,setsar=1[bg];"
    )


def _foreground(label: str, crop: Tuple[int, int, int, int], out_w: int, out_h: int) -> str:
    x, y, w, h = crop
    return (
        f"{label}crop={w}:{h}:{x}:{y},"
        f"scale={out_w}:{out_h}:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1"
    )


def _stage_plan(width: int, height: int, box: Box) -> Dict[str, Any]:
    x, y, w, h = box
    cx = x + w / 2
    cy = y + h / 2
    min_crop_w = max(2, round_to_even(int(OUT_W / STAGE_MAX_UPSCALE)))
    preferred = round_to_even(int(width * 0.52))
    # Keep the face large enough to read, but never zoom past the upscale cap.
    subject_limit = round_to_even(int(w / 0.16)) if w > 0 else width
    crop_w = min(width, max(min_crop_w, min(preferred, max(min_crop_w, subject_limit))))
    crop_w = max(2, round_to_even(crop_w))
    if crop_w > width:
        crop_w = round_to_even(width)
    crop_x = clamp_even(int(cx - crop_w / 2), 0, max(0, width - crop_w))

    def place(crop_y: int, crop_h: int) -> Tuple[int, int, float]:
        scale = OUT_W / crop_w
        scaled_h = min(OUT_H, round_to_even(int(round(crop_h * scale))))
        scaled_h = max(2, scaled_h)
        face_in = (cy - crop_y) / max(crop_h, 1) * scaled_h
        overlay = int(STAGE_FACE_Y * OUT_H - face_in)
        overlay = max(0, min(overlay, OUT_H - scaled_h))
        overlay = round_to_even(overlay)
        face_out = (overlay + face_in) / OUT_H
        return scaled_h, overlay, face_out

    crop_y, crop_h = 0, height
    scaled_h, overlay, face_out = place(crop_y, crop_h)
    guard = max(8, int(h * 0.35))
    if face_out > 0.44 and y > guard + 16:
        cut = round_to_even(min(int(y - guard), int(height * 0.28)))
        if cut >= 16 and height - cut >= max(64, int(h * 2)):
            crop_y, crop_h = cut, round_to_even(height - cut)
            scaled_h, overlay, face_out = place(crop_y, crop_h)

    video_bottom = (overlay + scaled_h) / OUT_H
    if video_bottom < 0.82:
        caption_y = min(0.84, video_bottom + (1 - video_bottom) * 0.45)
    else:
        face_bottom = (overlay + (y + h - crop_y) / max(crop_h, 1) * scaled_h) / OUT_H
        caption_y = caption_y_below_subject(face_bottom)
    video_filter = (
        f"{_blur_base(2, '[bgsrc]', '[fgsrc]')}"
        f"{_foreground('[fgsrc]', (crop_x, crop_y, crop_w, crop_h), OUT_W, scaled_h)}[fg];"
        f"[bg][fg]overlay=0:{overlay}[vout]"
    )
    return {
        "kind": "stage",
        "video_filter": video_filter,
        "caption_y": round(caption_y, 4),
        "upscale": OUT_W / crop_w,
        "face_y": face_out,
        "video_bottom": video_bottom,
    }


def _tile_crop(
    width: int, height: int, box: Box, tile_w: int, tile_h: int
) -> Tuple[int, int, int, int]:
    """Even source window matching the tile, biased to the face's half of the frame."""
    x, y, w, h = box
    cx = x + w / 2
    cy = y + h / 2
    crop_w = round_to_even(min(width, max(tile_w // 2, width // 2)))
    crop_h = round_to_even(max(2, int(round(crop_w * tile_h / tile_w))))
    crop_h = min(crop_h, round_to_even(height))
    # Favour the half the face lives in so the other person is not pulled in.
    if cx < width / 2:
        crop_x = clamp_even(int(min(cx - crop_w / 2, width / 2 - crop_w)), 0, max(0, width - crop_w))
    else:
        crop_x = clamp_even(
            int(max(cx - crop_w / 2, width / 2)), 0, max(0, width - crop_w)
        )
    face_in = tile_h * 0.40
    crop_face = face_in / tile_h * crop_h
    crop_y = clamp_even(int(cy - crop_face), 0, max(0, height - crop_h))
    # Keep a little room above the head when the clamp would shave it off.
    headroom = max(6, int(h * 0.25))
    if crop_y > y - headroom:
        crop_y = clamp_even(int(y - headroom), 0, max(0, height - crop_h))
    return crop_x, crop_y, crop_w, crop_h


def _stack_plan(width: int, height: int, left: Box, right: Box) -> Dict[str, Any]:
    left_crop = _tile_crop(width, height, left, OUT_W, STACK_TILE_H)
    right_crop = _tile_crop(width, height, right, OUT_W, STACK_TILE_H)
    video_filter = (
        f"{_blur_base(3, '[bgsrc]', '[lsrc]', '[rsrc]')}"
        f"{_foreground('[lsrc]', left_crop, OUT_W, STACK_TILE_H)}[lv];"
        f"{_foreground('[rsrc]', right_crop, OUT_W, STACK_TILE_H)}[rv];"
        f"[bg][lv]overlay=0:{STACK_TOP}[mid];"
        f"[mid][rv]overlay=0:{STACK_BOTTOM}[vout]"
    )
    # Where the left face centre lands, so tests can check it is not stuck low.
    _, ly, _, lh = left
    lcx_crop_y = left_crop[1]
    face_in_tile = (ly + lh / 2 - lcx_crop_y) / max(left_crop[3], 1) * STACK_TILE_H
    face_y = (STACK_TOP + face_in_tile) / OUT_H
    upscale = OUT_W / max(left_crop[2], 1)
    return {
        "kind": "stack",
        "video_filter": video_filter,
        "caption_y": round(STACK_CAPTION_Y, 4),
        "upscale": upscale,
        "face_y": face_y,
        "video_bottom": (STACK_BOTTOM + STACK_TILE_H) / OUT_H,
    }


def _fit_plan(width: int, height: int) -> Dict[str, Any]:
    scaled_h = max(2, round_to_even(int(round(OUT_W * height / max(width, 1)))))
    scaled_h = min(scaled_h, OUT_H - 200)
    overlay = clamp_even(
        int(OUT_H * 0.40 - scaled_h / 2),
        80,
        max(80, OUT_H - scaled_h - 220),
    )
    video_bottom = (overlay + scaled_h) / OUT_H
    caption_y = min(0.84, max(0.62, video_bottom + (1 - video_bottom) * 0.42))
    video_filter = (
        f"{_blur_base(2, '[bgsrc]', '[fgsrc]')}"
        "[fgsrc]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[fg];"
        f"[bg][fg]overlay=(W-w)/2:{overlay}[vout]"
    )
    return {
        "kind": "fit",
        "video_filter": video_filter,
        "caption_y": round(caption_y, 4),
        "upscale": OUT_W / max(width, 1),
        "face_y": (overlay + scaled_h / 2) / OUT_H,
        "video_bottom": video_bottom,
    }


def choose_talking_head_layout(
    width: int,
    height: int,
    samples: Sequence[FaceFrame],
    scene_cuts: Optional[Sequence[float]] = None,
) -> Optional[Dict[str, Any]]:
    """Return a stack, stage, or full-frame fit, or None to keep the tracked crop.

    ``samples`` are ``(time, [(x, y, w, h), ...])`` in source pixels. A busy
    cut-heavy clip is left alone: the scene-aware compositor already handles
    slides and b-roll mixed into talking-head footage.
    """
    if width < 2 or height < 2 or width / height <= 1.05:
        return None
    faced = [(t, list(faces)) for t, faces in samples if faces]
    if len(faced) < 4:
        return None
    if scene_cuts and len(scene_cuts) > 4:
        return None

    if _gallery_frames(faced):
        return _fit_plan(width, height)

    pairs = _call_pairs(faced, width, height)
    if len(pairs) >= 4 and len(pairs) >= int(len(faced) * 0.5):
        left = _median_box([pair[0] for pair in pairs])
        right = _median_box([pair[1] for pair in pairs])
        return _stack_plan(width, height, left, right)

    # Two real faces that are not a call stay on speaker tracking / the pan.
    multi = 0
    for _, faces in faced:
        ranked = sorted(faces, key=lambda item: item[2] * item[3], reverse=True)
        if len(ranked) >= 2 and ranked[1][2] * ranked[1][3] >= ranked[0][2] * ranked[0][3] * 0.35:
            multi += 1
    if multi >= int(len(faced) * 0.4):
        return None

    doms = [max(faces, key=lambda item: item[2] * item[3]) for _, faces in faced]
    box = _median_box(doms)
    _, y, _, h = box
    cy = y + h / 2
    if cy / height >= 0.48 or y / height >= 0.22:
        return _stage_plan(width, height, box)
    return None
