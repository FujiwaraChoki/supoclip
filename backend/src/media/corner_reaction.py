"""Reaction layout for a webcam in any corner, chosen by the user.

``reaction_layout`` only accepts a rectangular camera in an upper corner of a
source that also shows other faces. Streams with the player cut out over the
game in a lower corner (no rectangle, no other faces) never match, so a user
who picks a reaction layout gets this broader detector instead: a small,
steady face in any corner is the webcam. The crop around the face becomes the
webcam panel; the whole frame becomes the game panel.
"""

from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np

from .common import UPSCALE_SHARPEN_FILTER
from .ffmpeg import clamp_even, round_to_even
from .reaction_layout import REACTOR_HEIGHT, SOURCE_HEIGHT

Box = Tuple[float, float, float, float]
FaceFrame = Tuple[float, Sequence[Box]]
MIN_SAMPLES = 8
MIN_SUPPORT = 0.5


def find_corner_webcam(
    width: int, height: int, samples: Sequence[FaceFrame]
) -> Optional[Box]:
    """A small face that stays in one corner for most of the clip."""
    if len(samples) < MIN_SAMPLES:
        return None
    best: Optional[Tuple[int, Box]] = None
    for right in (True, False):
        for bottom in (True, False):
            found = []
            for _, faces in samples:
                corner = [
                    f for f in faces
                    if (f[0] + f[2] / 2 > width * .70 if right else f[0] + f[2] / 2 < width * .30)
                    and (f[1] + f[3] / 2 > height * .55 if bottom else f[1] + f[3] / 2 < height * .45)
                    and .0008 < f[2] * f[3] / (width * height) < .04
                ]
                if corner:
                    found.append(max(corner, key=lambda f: f[2] * f[3]))
            if len(found) < len(samples) * MIN_SUPPORT:
                continue
            boxes = np.asarray(found)
            centres = boxes[:, :2] + boxes[:, 2:] / 2
            if (np.std(centres[:, 0]) > width * .03
                    or np.std(centres[:, 1]) > height * .04
                    or np.std(boxes[:, 2]) > np.median(boxes[:, 2]) * .35):
                continue
            if best is None or len(found) > best[0]:
                best = (len(found), tuple(float(v) for v in np.median(boxes, axis=0)))
    return best[1] if best else None


def reactor_crop(width: int, height: int, face: Box) -> Tuple[int, int, int, int]:
    """Head-and-shoulders window around the face, in the panel's aspect."""
    fx, fy, fw, fh = face
    ch = round_to_even(int(min(height, max(fh * 2.6, 200))))
    cw = round_to_even(int(ch * 1080 / REACTOR_HEIGHT))
    if cw > width:
        cw = round_to_even(width)
        ch = round_to_even(int(cw * REACTOR_HEIGHT / 1080))
    x = clamp_even(int(fx + fw / 2 - cw / 2), 0, max(0, width - cw))
    y = clamp_even(int(fy + fh / 2 - ch * .44), 0, max(0, height - ch))
    return cw, ch, x, y


def build_corner_reaction_layout(
    width: int, height: int, face: Box, reactor_position: str = "top",
    src_label: str = "0:v", out_label: str = "vout",
) -> Dict[str, Any]:
    """Webcam panel plus the whole frame as the game panel, stacked 9:16."""
    cw, ch, cx, cy = reactor_crop(width, height, face)
    graph = [
        f"[{src_label}]split=3[reactor_src][game_bg_src][game_src]",
        f"[reactor_src]crop={cw}:{ch}:{cx}:{cy},scale=1080:{REACTOR_HEIGHT}:flags=lanczos,"
        f"{UPSCALE_SHARPEN_FILTER},setsar=1[reactor]",
        f"[game_bg_src]scale=1080:{SOURCE_HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop=1080:{SOURCE_HEIGHT},gblur=sigma=24:steps=2,setsar=1[game_back]",
        "[game_src]scale=1080:-2:flags=lanczos,setsar=1[game_front]",
        "[game_back][game_front]overlay=(W-w)/2:(H-h)/2[game]",
    ]
    if reactor_position == "bottom":
        graph.append(f"[game][reactor]vstack=inputs=2[{out_label}]")
        caption_y = SOURCE_HEIGHT / 1920
    else:
        graph.append(f"[reactor][game]vstack=inputs=2[{out_label}]")
        caption_y = REACTOR_HEIGHT / 1920
    return {
        "kind": "reaction",
        "video_filter": ";".join(graph),
        "caption_y": caption_y,
        "upscale": 1080 / cw,
        "reactor_crop": (cw, ch, cx, cy),
    }


def choose_corner_reaction_layout(
    width: int, height: int, duration: float, samples: Sequence[FaceFrame],
    reactor_position: str = "top",
    speech_words: Optional[Sequence[Dict[str, Any]]] = None,
) -> Optional[Dict[str, Any]]:
    face = find_corner_webcam(width, height, samples)
    if face is None:
        return None
    if speech_words:
        from .reaction_dynamic import compose_dynamic, plan_dynamic_windows

        windows = plan_dynamic_windows(speech_words, duration)
        if windows:
            base = build_corner_reaction_layout(
                width, height, face, reactor_position, "dyn_a", "split"
            )
            return compose_dynamic(base, windows)
    return build_corner_reaction_layout(width, height, face, reactor_position)
