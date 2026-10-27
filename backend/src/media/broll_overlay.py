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
    *,
    min_duration: float = MIN_BROLL_SECONDS,
) -> List[Dict[str, Any]]:
    """Valid, non-overlapping overlays in play order.

    Each overlay needs ``path`` (an existing file), ``start`` and ``duration``
    in seconds of the output timeline; ``offset`` is where in the footage it
    begins (after a trim cut into it). ``fade_in``/``fade_out`` (default on)
    are off for a piece that continues a cutaway across an editor cut.
    ``fade_duration`` retains the original dissolve length after combining.
    ``min_duration`` lets editor exports keep their shorter, explicitly placed
    cutaways while generation retains its default minimum. Overlays past the
    clip end are cut to fit.
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
        fade_in = overlay.get("fade_in", True) is not False
        fade_out = overlay.get("fade_out", True) is not False
        # A standalone cutaway must be long enough to read; a piece continuing
        # one across a cut only has to exist.
        if duration < (min_duration if fade_in and fade_out else 0.04) - 1e-9:
            continue
        fade_duration = min(BROLL_FADE_SECONDS, duration / 3)
        try:
            fade_duration = min(BROLL_FADE_SECONDS, max(0.001, float(overlay.get("fade_duration") or fade_duration)))
        except (TypeError, ValueError):
            pass
        cleaned.append(
            {
                "path": path,
                "start": start,
                "duration": duration,
                "offset": offset,
                "fade_in": fade_in,
                "fade_out": fade_out,
                "fade_duration": fade_duration,
            }
        )

    cleaned.sort(key=lambda overlay: overlay["start"])
    result: List[Dict[str, Any]] = []
    for overlay in cleaned:
        if result:
            previous = result[-1]
            # Pieces of one cutaway meet exactly; allow for float rounding.
            if overlay["start"] < previous["start"] + previous["duration"] - 0.01:
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
        fade = overlay.get("fade_duration", min(BROLL_FADE_SECONDS, duration / 3))
        clip_label = f"[broll{index}]"
        out_label = f"[withbroll{index}]"
        fades = ""
        if overlay.get("fade_in", True):
            fades += f",fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1"
        if overlay.get("fade_out", True):
            fades += f",fade=t=out:st={end - fade:.3f}:d={fade:.3f}:alpha=1"
        # Shift the cutaway onto the clip timeline, fill the frame like the
        # main picture does, then fade its alpha so it dissolves over the speaker.
        # Stock files use coarse time bases (1/25): settb first, or the shift
        # rounds to whole frames and the cutaway ends a couple of frames early.
        # A little headroom plus eof_action=repeat keeps the last frame up to
        # the window's end; `enable` still cuts it off exactly.
        parts.append(
            f"[{first_input + index}:v]settb=AVTB,"
            f"trim=start={overlay.get('offset', 0.0):.3f}:duration={duration + 0.1:.3f},"
            f"setpts=PTS-STARTPTS+{start:.3f}/TB,"
            f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
            f"crop={width}:{height},setsar=1,format=yuva420p{fades}{clip_label}"
        )
        parts.append(
            f"{current}{clip_label}overlay=0:0:eof_action=repeat:"
            f"enable='between(t,{start:.3f},{end:.3f})'{out_label}"
        )
        current = out_label
    return ";".join(parts), current
