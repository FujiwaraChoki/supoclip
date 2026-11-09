"""Reaction layout that changes shape with the speech: webcam, game, or both.

The rule is deliberately simple and driven only by when words are spoken, so it
needs no extra detection and is the same every run:

* a run of speech opens on the webcam alone for ``WEBCAM_OPENING`` seconds (the
  reaction), then settles into the usual webcam-over-game split;
* a silence of ``GAME_GAP`` seconds or more shows the game alone, so the viewer
  sees what happens while nobody is talking;
* everything else is the split.

Windows are in output seconds, never overlap, and each is at least
``MIN_WINDOW`` long so the picture never flickers.
"""

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .reaction_layout import build_reaction_layout

Box = Tuple[float, float, float, float]
Window = Tuple[float, float, str]

WEBCAM_OPENING = 2.5
GAME_GAP = 1.2
MIN_WINDOW = 0.8
OUT_W, OUT_H = 1080, 1920


def _speech_runs(words: Sequence[Dict[str, Any]], gap: float) -> List[Tuple[float, float]]:
    """Spans of speech, split wherever two words are ``gap`` or more apart."""
    runs: List[List[float]] = []
    for word in sorted(words, key=lambda w: float(w["start"])):
        start, end = float(word["start"]), float(word["end"])
        if end <= start:
            continue
        if runs and start - runs[-1][1] < gap:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    return [(a, b) for a, b in runs]


def plan_dynamic_windows(
    words: Sequence[Dict[str, Any]], duration: float
) -> List[Window]:
    """(start, end, mode) windows that differ from the plain split.

    ``mode`` is ``webcam`` or ``game``. Time not covered stays the split.
    """
    if duration <= 0:
        return []
    runs = _speech_runs(words, GAME_GAP)
    windows: List[Window] = []
    cursor = 0.0
    for start, end in runs:
        if start - cursor >= GAME_GAP:
            windows.append((cursor, start, "game"))
        opening_end = min(end, start + WEBCAM_OPENING)
        if opening_end - start >= MIN_WINDOW and end - start >= WEBCAM_OPENING:
            windows.append((start, opening_end, "webcam"))
        cursor = end
    if duration - cursor >= GAME_GAP and runs:
        windows.append((cursor, duration, "game"))
    return [
        (round(a, 3), round(min(b, duration), 3), mode)
        for a, b, mode in windows
        if min(b, duration) - a >= MIN_WINDOW
    ]


def _enable(start: float, end: float) -> str:
    return f"gte(t\\,{start:.3f})*lt(t\\,{end:.3f})"


def compose_dynamic(
    base: Dict[str, Any], windows: Sequence[Window], mask: Optional[Box] = None,
) -> Dict[str, Any]:
    """Layer full-screen webcam and game windows over a reaction split.

    ``base`` was built with ``src_label="dyn_a"`` and ``out_label="split"`` and
    carries ``reactor_crop``. ``mask`` is a webcam rectangle to black out of the
    game-alone shot; without one the game is shown as the source has it.
    """
    rw, rh, rx, ry = base["reactor_crop"]
    game_in = "[dyn_game]"
    if mask is not None:
        ix, iy, iw, ih = mask
        game_in = (
            f"[dyn_game]drawbox=x={int(ix)}:y={int(iy)}:w={int(iw)}:h={int(ih)}"
            ":color=black:t=fill[dyn_game_masked];[dyn_game_masked]"
        )
    graph = [
        "[0:v]split=3[dyn_a][dyn_cam][dyn_game]",
        base["video_filter"],
        # Webcam alone: the reactor crop, centred on a blurred copy of itself.
        f"[dyn_cam]crop={rw}:{rh}:{rx}:{ry},split=2[cam_bg][cam_fg]",
        f"[cam_bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},gblur=sigma=24:steps=2,setsar=1[cam_back]",
        f"[cam_fg]scale={OUT_W}:-2:flags=lanczos,setsar=1[cam_front]",
        "[cam_back][cam_front]overlay=(W-w)/2:(H-h)/2[camfull]",
        # Game alone: the source on a blurred copy of itself.
        f"{game_in}split=2[game_bg][game_fg]",
        f"[game_bg]scale={OUT_W}:{OUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUT_W}:{OUT_H},gblur=sigma=24:steps=2,setsar=1[game_back]",
        f"[game_fg]scale={OUT_W}:-2:flags=lanczos,setsar=1[game_front]",
        "[game_back][game_front]overlay=(W-w)/2:(H-h)/2[gamefull]",
    ]
    current = "split"
    for index, (start, end, mode) in enumerate(windows):
        label = "camfull" if mode == "webcam" else "gamefull"
        out = "vout" if index == len(windows) - 1 else f"dyn{index}"
        graph.append(
            f"[{current}][{label}]overlay=0:0:enable='{_enable(start, end)}'[{out}]"
        )
        current = out
    # Each full-screen layer is used once per window, so it needs one copy per use.
    graph = _split_reused_layers(graph, windows)
    return {
        "kind": "reaction",
        "video_filter": ";".join(graph),
        "caption_y": base["caption_y"],
        "windows": list(windows),
        "upscale": base["upscale"],
    }


def build_dynamic_reaction_layout(
    width: int, height: int, duration: float, face: Box, inset: Box,
    samples: Sequence[Tuple[float, Sequence[Box]]], scene_cuts: Sequence[float],
    words: Sequence[Dict[str, Any]], reactor_position: str = "top",
) -> Optional[Dict[str, Any]]:
    """The measured-inset reaction split with full-screen windows on top.

    Returns None when speech gives no window to show, so the caller keeps the
    plain split.
    """
    windows = plan_dynamic_windows(words, duration)
    if not windows:
        return None
    base = build_reaction_layout(
        width, height, duration, face, inset, samples, scene_cuts,
        reactor_position, src_label="dyn_a", out_label="split",
    )
    plan = compose_dynamic(base, windows, mask=inset)
    plan["inset"] = inset
    return plan


def _split_reused_layers(graph: List[str], windows: Sequence[Window]) -> List[str]:
    """ffmpeg labels can be read once: give every window its own copy."""
    out: List[str] = []
    counts = {"camfull": 0, "gamefull": 0}
    for _, _, mode in windows:
        counts["camfull" if mode == "webcam" else "gamefull"] += 1
    for line in graph:
        for label, total in counts.items():
            if line.endswith(f"[{label}]") and total > 1:
                line = f"{line};[{label}]split={total}" + "".join(
                    f"[{label}{i}]" for i in range(total)
                )
        out.append(line)
    seen = {"camfull": 0, "gamefull": 0}
    fixed: List[str] = []
    for line in out:
        for label, total in counts.items():
            token = f"[{label}]overlay=0:0"
            if total > 1 and token in line:
                line = line.replace(token, f"[{label}{seen[label]}]overlay=0:0", 1)
                seen[label] += 1
        fixed.append(line)
    return fixed
