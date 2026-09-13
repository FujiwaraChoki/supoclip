"""
Emoji reaction overlays: short-lived emoji glyphs burned into a clip at a
user-chosen timestamp/position, alongside the hook title and word-synced
captions (see `video_utils.build_hook_title_ass` for the sibling burned-in
overlay this mirrors — same ASS event/animation-tag conventions).

A reaction row (see `clip_repository.update_clip_reactions` / the
`generated_clips.reactions` column) looks like:

    {
        "id": str,
        "emoji": str,
        "timestamp_seconds": float,
        "animation_style": str,   # one of REACTION_ANIMATIONS
        "duration_seconds": float,
        "position": {"x_pct": float, "y_pct": float},  # fraction of frame, 0-1
    }

Rendering note: this always attempts to burn the emoji glyph using whatever
font libass resolves (the dedicated colour-emoji font when the runtime has
one, per `video_utils.EMOJI_FONT_NAME`/`emoji_rendering_supported`).  Most
bundled caption fonts in `backend/fonts/` are not colour-emoji fonts, so on
a runtime without a colour-emoji font installed the glyph may render as a
monochrome/tofu fallback — that's an acceptable degradation for this pass
(no hard failure), not a correctness bug. Adding a bundled colour-emoji font
asset is tracked as a follow-up, out of scope here.
"""

from typing import Any, Dict, List, Optional, Tuple

from .video_utils import (
    EMOJI_FONT_NAME,
    ass_timestamp,
    escape_ass_text,
    hex_to_ass_color,
)

# Same animation-style vocabulary as caption_templates.HOOK_ANIMATIONS, reused
# here so the two "burned-in overlay" features share one mental model.
REACTION_ANIMATIONS: Tuple[str, ...] = (
    "fade_pop",
    "fade",
    "slide_down",
    "zoom_punch",
    "bounce",
    "pulse",
    "none",
)

DEFAULT_REACTION_DURATION_SECONDS = 1.6
DEFAULT_REACTION_FONT_SCALE = 0.14  # fraction of the shorter frame dimension


def _entrance_tags(animation_style: Optional[str]) -> str:
    """Override tags for the reaction's entrance animation.

    Mirrors `video_utils.build_hook_title_ass`'s animation-tag construction so
    a reaction "pops"/"bounces"/etc. the same way a hook title does.
    """
    style = animation_style or "fade_pop"
    if style == "none":
        return ""
    if style == "slide_down":
        return "\\fad(120,200)\\fscy60\\t(0,200,\\fscy100)"
    if style == "fade":
        return "\\fad(180,200)"
    if style == "zoom_punch":
        return "\\fad(100,200)\\fscx100\\fscy100\\t(0,260,\\fscx115\\fscy115)"
    if style == "bounce":
        return (
            "\\fad(80,200)\\fscx55\\fscy55"
            "\\t(0,140,\\fscx115\\fscy115)"
            "\\t(140,230,\\fscx92\\fscy92)"
            "\\t(230,300,\\fscx100\\fscy100)"
        )
    if style == "pulse":
        return (
            "\\fad(160,200)\\fscx100\\fscy100"
            "\\t(350,600,\\fscx112\\fscy112)"
            "\\t(600,850,\\fscx100\\fscy100)"
        )
    # fade_pop (default)
    return "\\fad(120,200)\\fscx85\\fscy85\\t(0,140,\\fscx100\\fscy100)"


def build_emoji_reactions_ass(
    reactions: Optional[List[Dict[str, Any]]],
    video_width: int,
    video_height: int,
) -> Tuple[str, List[str]]:
    """Build the (style_line, dialogue_events) for burned-in emoji reactions.

    Positioned via `position.x_pct`/`position.y_pct` (fraction of frame width/
    height), timed by `timestamp_seconds`/`duration_seconds`. Uses `\\pos` with
    Alignment 5 (vertical/horizontal centre anchor) — the same anchor
    convention as the caption/hook renderers in `video_utils.py`.
    """
    if not reactions:
        return "", []

    font_px = max(24, int(min(video_width, video_height) * DEFAULT_REACTION_FONT_SCALE))
    primary = hex_to_ass_color("#FFFFFF", "#FFFFFF")
    outline = hex_to_ass_color("#000000", "#000000")

    style_line = (
        f"Style: Reaction,{EMOJI_FONT_NAME},{font_px},{primary},&H000000FF,{outline},&H00000000&,"
        f"1,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1"
    )

    events: List[str] = []
    for reaction in reactions:
        emoji = str(reaction.get("emoji") or "").strip()
        if not emoji:
            continue

        try:
            start = max(0.0, float(reaction.get("timestamp_seconds") or 0.0))
        except (TypeError, ValueError):
            start = 0.0
        try:
            duration = float(
                reaction.get("duration_seconds") or DEFAULT_REACTION_DURATION_SECONDS
            )
        except (TypeError, ValueError):
            duration = DEFAULT_REACTION_DURATION_SECONDS
        duration = max(0.2, duration)
        end = start + duration

        position = reaction.get("position") or {}
        try:
            x_pct = float(position.get("x_pct", 0.5))
        except (TypeError, ValueError):
            x_pct = 0.5
        try:
            y_pct = float(position.get("y_pct", 0.3))
        except (TypeError, ValueError):
            y_pct = 0.3
        x_pct = min(max(x_pct, 0.0), 1.0)
        y_pct = min(max(y_pct, 0.0), 1.0)
        x = int(video_width * x_pct)
        y = int(video_height * y_pct)

        entrance = _entrance_tags(reaction.get("animation_style"))
        override_tags = f"{{\\pos({x},{y}){entrance}}}"
        events.append(
            f"Dialogue: 2,{ass_timestamp(start)},{ass_timestamp(end)},Reaction,,0,0,0,,"
            f"{override_tags}{escape_ass_text(emoji)}"
        )

    return style_line, events
