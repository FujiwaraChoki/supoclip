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
- One person whose face would sit low, or under a band of empty room, in a
  full-height crop gets a tight tracked head-and-shoulders crop (``solo``,
  the interview close-up from ``interview_layout``): up to 2.25x on a 1080p
  source, eyes near the upper third.
- When that face is small (under ~11% of the frame height), cannot be lifted
  to the upper half within the upscale cap, or the source is too small to crop
  sharply, the clip keeps the tracked full-height crop: the frame is full and
  the captions move under the face. (It used to be shown at about 60% of the
  frame height on a blurred canvas, ``stage``; a filled frame reads better on
  a phone than a sharper picture between two blurred bands.)
- A call gallery (three or more tiles on two rows or more) is shown in full
  on the blur: no crop holds its tiles, and half-tiles look broken. Three
  people side by side in one shot are not a gallery; they keep the crop.
- A face already framed in the upper half, including a close podcast two-shot,
  returns None so the tracked crop and active-speaker cuts stay in charge.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .common import UPSCALE_SHARPEN_FILTER, logger
from .ffmpeg import clamp_even, round_to_even

OUT_W = 1080
OUT_H = 1920
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


# Tiles of a call grid sit on rows: face centres this far apart vertically (as a share of the frame height)
# are two rows. People sharing one shot (a sofa, a table, a panel) sit at about one height.
GALLERY_MIN_ROW_GAP = 0.25


def _on_rows(faces: Sequence[Box], height: int) -> bool:
    centres = [y + h / 2 for _x, y, _w, h in faces]
    return len(faces) >= 3 and max(centres) - min(centres) >= GALLERY_MIN_ROW_GAP * height


def _gallery_frames(samples: Sequence[FaceFrame], height: int = 0) -> bool:
    """A call grid: three or more faces, on two rows or more, in a good share of the clip."""
    faced = [faces for _, faces in samples if faces]
    if len(faced) < 4:
        return False
    counted = [len(faces) for faces in faced]
    crowded = sum(1 for faces in faced if len(faces) >= 3 and (height <= 0 or _on_rows(faces, height)))
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
    duration: Optional[float] = None,
    input_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Return a stack, a solo crop or the full frame for a call gallery, or None to keep the tracked crop.

    ``samples`` are ``(time, [(x, y, w, h), ...])`` in source pixels. A busy
    cut-heavy clip is left alone: the scene-aware compositor already handles
    slides and b-roll mixed into talking-head footage. ``duration`` (else
    estimated from the samples) and ``input_path`` (frame-accurate cuts)
    only serve the solo crop.
    """
    if width < 2 or height < 2 or width / height <= 1.05:
        return None
    faced = [(t, list(faces)) for t, faces in samples if faces]
    if len(faced) < 4:
        return None
    if scene_cuts and len(scene_cuts) > 4:
        return None

    if _gallery_frames(faced, height):
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
        # A full-height crop would leave the face low or under a band of
        # empty room. One person big enough to crop gets a tight tracked
        # crop (the interview close-up). A small or unreachable face keeps
        # the tracked full-height crop: the frame stays full.
        return _solo_plan(width, height, samples, scene_cuts or [], duration, input_path)
    return None


def _solo_plan(
    width: int,
    height: int,
    samples: Sequence[FaceFrame],
    scene_cuts: Sequence[float],
    duration: Optional[float],
    input_path: Optional[Path],
) -> Optional[Dict[str, Any]]:
    from .interview_layout import plan_solo_layout

    if not duration:
        times = sorted(t for t, _ in samples)
        step = min((b - a for a, b in zip(times, times[1:]) if b > a), default=1 / 3)
        duration = times[-1] + step if times else 0.0
    try:
        solo = plan_solo_layout(width, height, duration, scene_cuts, samples, input_path=input_path)
    except Exception as exc:
        logger.warning("Solo crop planning failed (%s); staging the face", exc)
        return None
    if not solo:
        return None
    return {
        "kind": "solo",
        "video_filter": solo["video_filter"],
        "caption_y": round(caption_y_below_subject(solo["face_bottom"]), 4),
        "upscale": solo["upscale"],
        "face_y": solo["face_y"],
        "video_bottom": 1.0,
        "segments": solo["segments"],
    }
