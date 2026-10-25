"""Composite stock B-roll cutaways into the final clip render.

B-roll is overlaid on the reframed picture inside the same filter graph that
burns captions, so captions and the hook title stay on screen over it and the
clip is still encoded once. Each cutaway dissolves in and out over the speaker
instead of dipping to black.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple

BROLL_FADE_SECONDS = 0.25
MIN_BROLL_SECONDS = 0.6


def normalize_broll_overlays(
    overlays: Iterable[Dict[str, Any]] | None,
    clip_duration: float | None = None,
) -> List[Dict[str, Any]]:
    """Valid, non-overlapping overlays in play order.

    Each overlay needs ``path`` (an existing file), ``start`` and ``duration``
    in seconds of the output timeline; ``offset`` is where in the footage it
    begins (after a trim cut into it). Overlays past the clip end are cut to
    fit; anything too short to read as a cutaway is dropped.
    """
    cleaned: List[Dict[str, Any]] = []
    for overlay in overlays or []:
        try:
            path = Path(overlay["path"])
            start = max(0.0, float(overlay["start"]))
            duration = float(overlay["duration"])
        except (KeyError, TypeError, ValueError):
            continue
        if not path.is_file():
            continue
        try:
            offset = max(0.0, float(overlay.get("offset") or 0.0))
        except (TypeError, ValueError):
            offset = 0.0
        if clip_duration is not None:
            duration = min(duration, clip_duration - start)
        if duration < MIN_BROLL_SECONDS:
            continue
        cleaned.append({"path": path, "start": start, "duration": duration, "offset": offset})

    cleaned.sort(key=lambda overlay: overlay["start"])
    result: List[Dict[str, Any]] = []
    for overlay in cleaned:
        if result:
            previous = result[-1]
            if overlay["start"] < previous["start"] + previous["duration"]:
                continue
        result.append(overlay)
    return result


def broll_input_args(overlays: List[Dict[str, Any]]) -> List[str]:
    """ffmpeg input args for the overlays, read only as far as they play."""
    args: List[str] = []
    for overlay in overlays:
        # A little slack past the cutaway so the fade-out has frames to use.
        read = overlay.get("offset", 0.0) + overlay["duration"] + 0.5
        args += ["-t", f"{read:.3f}", "-i", str(overlay["path"])]
    return args


def broll_overlay_graph(
    base_label: str,
    overlays: List[Dict[str, Any]],
    width: int,
    height: int,
    first_input: int = 1,
) -> Tuple[str, str]:
    """Filter graph fragment that overlays each cutaway on ``base_label``.

    Returns the fragment (without a leading ``;``) and its output label.
    """
    parts: List[str] = []
    current = base_label
    for index, overlay in enumerate(overlays):
        start = overlay["start"]
        duration = overlay["duration"]
        end = start + duration
        fade = min(BROLL_FADE_SECONDS, duration / 3)
        clip_label = f"[broll{index}]"
        out_label = f"[withbroll{index}]"
        # Shift the cutaway onto the clip timeline, fill the frame like the
        # main picture does, then fade its alpha so it dissolves over the speaker.
        parts.append(
            f"[{first_input + index}:v]trim=start={overlay.get('offset', 0.0):.3f}:duration={duration:.3f},"
            f"setpts=PTS-STARTPTS+{start:.3f}/TB,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},setsar=1,format=yuva420p,"
            f"fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={end - fade:.3f}:d={fade:.3f}:alpha=1{clip_label}"
        )
        parts.append(
            f"{current}{clip_label}overlay=0:0:eof_action=pass:"
            f"enable='between(t,{start:.3f},{end:.3f})'{out_label}"
        )
        current = out_label
    return ";".join(parts), current
