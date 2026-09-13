"""
Emoji reaction overlays: short-lived emoji burned into a clip at a
user-chosen timestamp/position, alongside the hook title and word-synced
captions.

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

Rendering: reactions are burned as TRUE-COLOUR image overlays (ffmpeg
`overlay` filter) using bundled Twemoji PNGs (`backend/assets/emoji/`, see
NOTICE.txt there for source/license), not as text glyphs. This was a
deliberate fix, not a style choice: this project's ffmpeg/libass build
cannot composite full-colour glyphs (neither COLR/CPAL vector fonts nor
CBDT/bitmap fonts like Noto Color Emoji) via the `subtitles` filter — it
silently drops them (confirmed by direct testing; see the investigation
in the "Tier 1 bugs" work). `build_emoji_reactions_ass` below is kept only
as a fallback ASS-text renderer for a reaction emoji with no bundled
asset (the reaction picker only ever offers the bundled 32, so this path
is effectively dead in normal use, but keeps old/unusual data non-fatal
rather than silently dropping the reaction) — it inherits the same
monochrome/tofu risk the image-overlay path exists to avoid.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .video_utils import (
    EMOJI_FONT_NAME,
    ass_timestamp,
    escape_ass_text,
    ffprobe_duration,
    ffprobe_has_audio,
    hex_to_ass_color,
    run_ffmpeg_command,
)

# Twemoji 72x72 PNGs, one per codepoint (variation selectors stripped),
# covering exactly frontend/src/components/ui/emoji-picker.tsx's
# REACTION_EMOJIS. See backend/assets/emoji/NOTICE.txt for source/license.
EMOJI_ASSETS_DIR = Path(__file__).parent.parent / "assets" / "emoji"
REACTION_OVERLAY_FADE_SECONDS = 0.12

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


def _emoji_codepoints_filename(emoji: str) -> str:
    """Twemoji's own asset-naming convention: lowercase hex codepoints
    joined by '-', with the U+FE0F variation selector stripped (Twemoji
    ships e.g. "2764.png" for "❤️", not "2764-fe0f.png"). Sufficient for the
    curated single-codepoint reaction set; not a general emoji-to-filename
    algorithm (ZWJ sequences etc. aren't handled, and aren't needed here).
    """
    return "-".join(f"{ord(ch):x}" for ch in emoji if ord(ch) != 0xFE0F)


def resolve_emoji_asset_path(emoji: str) -> Optional[Path]:
    """Bundled Twemoji PNG for this emoji, if we have one."""
    filename = _emoji_codepoints_filename(emoji.strip())
    if not filename:
        return None
    candidate = EMOJI_ASSETS_DIR / f"{filename}.png"
    return candidate if candidate.is_file() else None


def split_reactions_by_asset_availability(
    reactions: Optional[List[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Split reactions into (has a bundled PNG, needs the ASS-text fallback)."""
    overlayable: List[Dict[str, Any]] = []
    fallback: List[Dict[str, Any]] = []
    for reaction in reactions or []:
        emoji = str(reaction.get("emoji") or "").strip()
        if emoji and resolve_emoji_asset_path(emoji):
            overlayable.append(reaction)
        elif emoji:
            fallback.append(reaction)
    return overlayable, fallback


def overlay_emoji_reactions_ffmpeg(
    input_path: Path,
    output_path: Path,
    reactions: List[Dict[str, Any]],
    video_width: int,
    video_height: int,
) -> bool:
    """Burn `reactions` (each already confirmed to have a bundled PNG via
    `resolve_emoji_asset_path`) into `input_path` as true-colour image
    overlays, writing the result to `output_path`.

    Animation is simplified to a fade in/out (not the full per-style scale
    vocabulary `_entrance_tags` gives the ASS-text path) — image overlays
    don't get libass's `\\t()` transform tags for free, and replicating all
    six animation styles as ffmpeg filter expressions was judged not worth
    the complexity for a short-lived reaction overlay. `animation_style` is
    otherwise unused by this path.
    """
    if not reactions:
        return False

    has_audio = ffprobe_has_audio(input_path)
    inputs: List[str] = ["-i", str(input_path)]
    filter_parts: List[str] = []
    last_label = "0:v"

    for index, reaction in enumerate(reactions):
        asset_path = resolve_emoji_asset_path(str(reaction.get("emoji") or ""))
        if not asset_path:
            continue
        inputs.extend(["-loop", "1", "-i", str(asset_path)])

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
        fade = min(REACTION_OVERLAY_FADE_SECONDS, duration / 2)

        position = reaction.get("position") or {}
        try:
            x_pct = min(max(float(position.get("x_pct", 0.5)), 0.0), 1.0)
        except (TypeError, ValueError):
            x_pct = 0.5
        try:
            y_pct = min(max(float(position.get("y_pct", 0.3)), 0.0), 1.0)
        except (TypeError, ValueError):
            y_pct = 0.3

        emoji_size = max(48, int(min(video_width, video_height) * 0.22))
        x = int(video_width * x_pct - emoji_size / 2)
        y = int(video_height * y_pct - emoji_size / 2)

        img_label = f"e{index}"
        overlay_label = f"ov{index}"
        filter_parts.append(
            f"[{index + 1}:v]scale={emoji_size}:{emoji_size},format=rgba,"
            f"fade=t=in:st={start:.3f}:d={fade:.3f}:alpha=1,"
            f"fade=t=out:st={(end - fade):.3f}:d={fade:.3f}:alpha=1[{img_label}]"
        )
        filter_parts.append(
            f"[{last_label}][{img_label}]overlay=x={x}:y={y}:"
            f"enable='between(t,{start:.3f},{end:.3f})'[{overlay_label}]"
        )
        last_label = overlay_label

    if last_label == "0:v":
        # None of the reactions actually had a bundled asset after all.
        return False

    filter_complex = ";".join(filter_parts)
    command = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", f"[{last_label}]",
    ]
    if has_audio:
        command += ["-map", "0:a", "-c:a", "copy"]
    # The `-loop 1` image inputs never signal EOF on their own; `-shortest`
    # alone doesn't reliably terminate this filter graph (observed hanging
    # indefinitely without an explicit output duration), so cap it directly
    # at the main video's real length.
    duration = ffprobe_duration(input_path)
    command += [
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-t", f"{duration:.3f}",
        str(output_path),
    ]
    result = run_ffmpeg_command(command, timeout=300)
    return result.returncode == 0 and output_path.exists()
