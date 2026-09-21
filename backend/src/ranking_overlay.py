"""
Rank-number overlay burn-in for the Ranking tool's compilation render.

Mirrors video_utils.py::build_hook_title_ass's ASS/style conventions (same
Alignment/MarginV-based placement, same BorderStyle-3-as-filled-box trick
used for the hook's optional background pill) rather than reinventing ASS
plumbing — but renders a single digit in a fixed square tile, the
DESIGN.md-sanctioned exception to "no centered text" (see DESIGN.md's
component rules), using only the locked 4-color palette.
"""

from typing import Any, Dict, List, Optional, Tuple

from .video_utils import (
    ass_font_name,
    ass_timestamp,
    escape_ass_text,
    get_scaled_font_size,
    hex_to_ass_color,
    measure_text_width,
    render_emoji_cluster_png,
    split_text_and_emoji,
)


# Locked palette (DESIGN.md) — the only colors a rank tile may use.
_PALETTE = {
    "ink": "#0f0a01",
    "paper": "#fbfbfb",
    "teal": "#558a86",
    "blue": "#08327d",
}

# Ranking VIDEOS are explicitly exempt from the site's 4-color lock (DESIGN.md
# note, DECISIONS.md) — the per-rank medal/tier colors below are the
# deliberate departure, used only by build_ranking_overlay_ass and the hook
# builder below, never by the site UI.
_GOLD = "#ffd700"

# Per-rank number-tile colors for the "stacked" style: 1=gold, 2=silver,
# 3=bronze, 4=grey, 5=white, 6=red. Ranks beyond 6 fall back to neutral
# (paper) via build_ranking_overlay_ass's .get() default.
_RANK_TILE_COLORS = {
    1: _GOLD,
    2: "#c0c0c0",
    3: "#cd7f32",
    4: "#808080",
    5: "#fbfbfb",
    6: "#ff0000",
}

# Hook's second (right-hand input box) color — always red, independent of
# the per-rank tile colors above.
_HOOK_RED = "#ff0000"

_POSITION_ALIGNMENT = {"top": 8, "center": 5, "bottom": 2}


def build_rank_number_ass(
    rank: int,
    number_overlay: Dict[str, Any],
    video_width: int,
    video_height: int,
    segment_start: float,
    segment_duration: float,
    font_family: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Build the (style_line, dialogue_events) for one segment's rank-number
    tile, timed to that segment's slice of the final concatenated output.

    `number_overlay` is the template's (or the user's override of the
    template's) `{position, color, animation}` config — see
    ranking_templates.py's TEMPLATE_DEFAULTS for the shape.
    """
    tile_color = _PALETTE.get(number_overlay.get("color", "teal"), _PALETTE["teal"])
    # Off-white digit on the tile, unless the tile itself is off-white (rare
    # override), in which case fall back to ink so the digit stays legible.
    digit_color = _PALETTE["ink"] if tile_color == _PALETTE["paper"] else _PALETTE["paper"]

    font_name = ass_font_name(font_family)
    base_px = get_scaled_font_size(64, video_width, video_height)

    primary = hex_to_ass_color(digit_color, "#FBFBFB")
    back_color = hex_to_ass_color(tile_color, "#558A86")
    # BorderStyle 3 (opaque box) fills using OutlineColour, not BackColour, in
    # the libass build this renders through (verified directly — the same
    # discrepancy CLAUDE.md notes for colour emoji glyphs, a probe-don't-
    # assume libass quirk rather than a spec violation worth "fixing" away).
    # BackColour is set the same regardless, in case a different libass build
    # does honour it as the spec describes.
    outline = hex_to_ass_color(tile_color, "#558A86")

    position = number_overlay.get("position", "bottom")
    alignment = _POSITION_ALIGNMENT.get(position, 2)
    margin_v = max(48, int(video_height * 0.08))

    # BorderStyle 3 (opaque box) with generous padding reads as a fixed
    # square tile behind the digit, matching the hook title's own
    # background-pill technique.
    border_style = 3
    outline_px = max(18, round(base_px * 0.55))

    style_line = (
        f"Style: Rank,{font_name},{base_px},{primary},&H000000FF,{outline},{back_color},"
        f"1,0,0,0,100,100,0,0,{border_style},{outline_px},0,{alignment},60,60,{margin_v},1"
    )

    animation = number_overlay.get("animation", "fade_pop")
    if animation == "none":
        entrance = ""
    elif animation == "pop":
        entrance = "\\fscx70\\fscy70\\t(0,160,\\fscx100\\fscy100)"
    elif animation == "slide":
        entrance = "\\fad(80,160)\\fscy60\\t(0,180,\\fscy100)"
    else:  # fade_pop (default)
        entrance = "\\fad(120,200)\\fscx85\\fscy85\\t(0,150,\\fscx100\\fscy100)"

    override_tags = f"{{\\blur1{entrance}}}" if entrance else "{\\blur1}"
    text = escape_ass_text(str(rank))

    start = segment_start
    end = segment_start + max(0.2, segment_duration)
    events = [
        f"Dialogue: 1,{ass_timestamp(start)},{ass_timestamp(end)},Rank,,0,0,0,,"
        f"{override_tags}{text}"
    ]
    return style_line, events


def build_rank_list_ass(
    revealed_ranks: List[int],
    number_overlay: Dict[str, Any],
    video_width: int,
    video_height: int,
    segment_start: float,
    segment_duration: float,
    font_family: Optional[str] = None,
) -> Tuple[str, List[str]]:
    """Build the (style_line, dialogue_events) for the Ranking List template's
    accumulating corner list — every rank revealed so far (in render order),
    re-rendered as one event per segment so the list appears to grow across
    the compilation. Placed in the corner opposite the main rank tile
    (build_rank_number_ass) so the two never overlap.
    """
    font_name = ass_font_name(font_family)
    base_px = max(18, get_scaled_font_size(64, video_width, video_height) // 2)

    # Paper tile / ink text — the inverse of the rank tile's ink text on a
    # colored fill — so the list reads as a secondary, auxiliary element.
    primary = hex_to_ass_color(_PALETTE["ink"], "#0F0A01")
    back_color = hex_to_ass_color(_PALETTE["paper"], "#FBFBFB")
    outline = hex_to_ass_color(_PALETTE["paper"], "#FBFBFB")

    tile_position = number_overlay.get("position", "bottom")
    # Top-left, unless the main tile sits at "top" — then drop to bottom-left
    # so the two never collide.
    alignment = 1 if tile_position == "top" else 7
    margin = max(32, int(video_width * 0.05))

    border_style = 3
    outline_px = max(10, round(base_px * 0.4))

    style_line = (
        f"Style: RankList,{font_name},{base_px},{primary},&H000000FF,{outline},{back_color},"
        f"1,0,0,0,100,100,0,0,{border_style},{outline_px},0,{alignment},{margin},{margin},{margin},1"
    )

    text = "\\N".join(escape_ass_text(f"#{rank}") for rank in revealed_ranks)
    start = segment_start
    end = segment_start + max(0.2, segment_duration)
    events = [
        f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},RankList,,0,0,0,,{{\\blur1}}{text}"
    ]
    return style_line, events


def build_ranking_overlay_ass(
    ranks: List[Dict[str, Any]],
    video_width: int,
    video_height: int,
    total_duration: float,
    font_family: Optional[str] = None,
) -> Tuple[List[str], List[str], List[Dict[str, Any]]]:
    """Build the (style_lines, dialogue_events, image_overlays) for the
    "stacked" number-overlay style: every rank's number tile visible for the
    *entire* video (unlike build_rank_number_ass's one-tile-per-segment),
    stacked top (highest number) to bottom (#1) on the left, with that
    rank's text fading/bouncing in next to its number the moment its clip
    starts and persisting to the end. Each rank's tile is colored per
    _RANK_TILE_COLORS (1=gold, 2=silver, 3=bronze, 4=grey, 5=white, 6=red) —
    a deliberate exception to the site's locked 4-color palette (that lock is
    site-UI only, not ranking video output). Rank text always renders
    off-white, regardless of tile color.

    `image_overlays` carries any emoji found in a rank's text, one entry per
    rank that has some (see render_emoji_cluster_png) — `_render_compilation`
    composites these as `overlay` filter images alongside the ASS text,
    since this environment's libass can't rasterise them itself.

    `ranks` is `[{"rank": int, "text": str, "reveal_time": float}, ...]`,
    one entry per input, in any order (sorted here by rank descending so the
    stack always reads 5,4,3,2,1 top-to-bottom regardless of playback order).
    """
    font_name = ass_font_name(font_family)
    tile_px = get_scaled_font_size(56, video_width, video_height)
    text_px = get_scaled_font_size(38, video_width, video_height)

    neutral = hex_to_ass_color(_PALETTE["paper"], "#FBFBFB")
    ink = hex_to_ass_color(_PALETTE["ink"], "#0F0A01")

    tile_outline_px = max(14, round(tile_px * 0.5))
    row_height = round(tile_px * 1.9)
    sorted_ranks = sorted(ranks, key=lambda r: -r["rank"])
    block_height = row_height * max(1, len(sorted_ranks))
    # "Roughly centered, slightly above center" per spec — nudge the whole
    # block up from true vertical center, clamped so a long rank list never
    # creeps into the top safe area.
    top_y = round(video_height * 0.5 - block_height / 2 - video_height * 0.05)
    top_y = max(round(video_height * 0.12), top_y)
    x_number = round(video_width * 0.07)
    x_text = x_number + tile_px * 2
    max_text_width = max(80, video_width - x_text - round(video_width * 0.08))

    tile_style = (
        f"Style: RankTile,{font_name},{tile_px},{ink},&H000000FF,{neutral},{neutral},"
        f"1,0,0,0,100,100,0,0,3,{tile_outline_px},0,7,20,20,20,1"
    )
    text_style = (
        f"Style: RankText,{font_name},{text_px},{neutral},&H000000FF,{ink},&H00000000,"
        f"1,0,0,0,100,100,0,0,1,{max(2, round(text_px * 0.12))},2,4,10,10,10,1"
    )

    events: List[str] = []
    image_overlays: List[Dict[str, Any]] = []
    for row_index, item in enumerate(sorted_ranks):
        rank = item["rank"]
        reveal = max(0.0, float(item.get("reveal_time", 0.0)))
        label, emoji_cluster = split_text_and_emoji(item.get("text") or "")
        y = top_y + row_index * row_height
        tile_fill = hex_to_ass_color(_RANK_TILE_COLORS.get(rank), "#FBFBFB")
        # Rank text always renders white/off-white, regardless of the tile's
        # per-rank color — only the number tile carries the medal/tier color.
        accent = neutral

        # A ~0.5s bounce (per spec: overshoot then settle) timed to this
        # rank's reveal. The tile's Dialogue spans the whole video, so its
        # \t offsets are absolute ms-from-video-start; the text's Dialogue
        # starts exactly at reveal, so its own \t offsets are relative to 0.
        reveal_ms = round(reveal * 1000)
        tile_bounce = (
            f"\\t({reveal_ms},{reveal_ms + 180},\\fscx126\\fscy126)"
            f"\\t({reveal_ms + 180},{reveal_ms + 500},\\fscx100\\fscy100)"
        )
        tile_tags = f"{{\\an7\\pos({x_number},{y})\\3c{tile_fill}\\blur1{tile_bounce}}}"
        events.append(
            f"Dialogue: 1,{ass_timestamp(0)},{ass_timestamp(total_duration)},RankTile,,0,0,0,,"
            f"{tile_tags}{escape_ass_text(str(rank))}"
        )

        if label:
            text_bounce = "\\t(0,150,\\fscx114\\fscy114)\\t(150,450,\\fscx100\\fscy100)"
            text_tags = (
                f"{{\\an4\\pos({x_text},{y + tile_px // 2})\\1c{accent}"
                f"\\blur1{text_bounce}}}"
            )
            events.append(
                f"Dialogue: 1,{ass_timestamp(reveal)},{ass_timestamp(total_duration)},RankText,,"
                f"0,{max_text_width},0,,{text_tags}{escape_ass_text(label)}"
            )

        if emoji_cluster:
            glyph_px = round(text_px * 1.3)
            rendered = render_emoji_cluster_png(emoji_cluster, glyph_px)
            if rendered:
                emoji_path, emoji_w, emoji_h = rendered
                gap = round(text_px * 0.3)
                text_width = measure_text_width(label, font_family, font_name, text_px) if label else 0
                emoji_x = round(x_text + text_width + (gap if label else 0))
                emoji_y = round((y + tile_px // 2) - emoji_h / 2)
                image_overlays.append(
                    {
                        "path": emoji_path,
                        "width": emoji_w,
                        "height": emoji_h,
                        "x": emoji_x,
                        "y": emoji_y,
                        "start": reveal,
                        "end": total_duration,
                    }
                )

    return [tile_style, text_style], events, image_overlays


def build_ranking_hook_ass(
    hook_white: str,
    hook_red: str,
    video_width: int,
    video_height: int,
    total_duration: float,
    font_family: Optional[str] = None,
) -> Tuple[str, List[str], List[Dict[str, Any]]]:
    """Build the (style_line, dialogue_events, image_overlays) for the
    top-of-frame hook: a two-part title where the left input box's text
    renders white and the right input box's text renders red (e.g. "Ranking
    Best" / "Pool Fails"), visible for the whole compilation. No background
    pill — just outlined text over video, matching the reference style.

    Any emoji typed into either box is split out and composited as an image
    overlay right after the last rendered line (see render_emoji_cluster_png)
    rather than burned in as ASS text, since this environment's libass can't
    rasterise colour emoji — matching the product convention (ai.py's
    HOOK_GENERATION_RULES) that a hook's emoji sits at the very end anyway.
    """
    white_text, white_emoji = split_text_and_emoji(hook_white or "")
    red_text, red_emoji = split_text_and_emoji(hook_red or "")
    emoji_cluster = white_emoji + red_emoji

    font_name = ass_font_name(font_family)
    base_px = get_scaled_font_size(72, video_width, video_height)
    margin_v = max(48, round(video_height * 0.06))
    usable_width = video_width - 2 * max(48, round(video_width * 0.06))
    max_chars = max(10, int(usable_width / (base_px * 0.52)))

    white_color = hex_to_ass_color(_PALETTE["paper"], "#FBFBFB")
    red_color = hex_to_ass_color(_HOOK_RED, "#FF0000")
    outline_color = hex_to_ass_color(_PALETTE["ink"], "#0F0A01")
    outline_px = max(3, round(base_px * 0.11))
    shadow_px = max(2, round(base_px * 0.05))

    style_line = (
        f"Style: Hook,{font_name},{base_px},{white_color},&H000000FF,{outline_color},"
        f"&H00000000,1,0,0,0,100,100,0,0,1,{outline_px},{shadow_px},8,40,40,{margin_v},1"
    )

    combined_len = len(f"{white_text} {red_text}".strip())
    line_height = round(base_px * 1.15)
    # Two boxes' worth of text won't always fit on one line — when it
    # doesn't, break at the white/red boundary rather than mid-phrase.
    two_lines = bool(white_text and red_text and combined_len > max_chars)
    if two_lines:
        text = (
            f"{{\\fad(150,200)}}{escape_ass_text(white_text)}\\N"
            f"{{\\1c{red_color}}}{escape_ass_text(red_text)}"
        )
        last_line_text = red_text
        last_line_center_y = margin_v + line_height * 1.5
    else:
        parts = [f"{{\\fad(150,200)}}{escape_ass_text(white_text)}"] if white_text else ["{\\fad(150,200)}"]
        if red_text:
            sep = " " if white_text else ""
            parts.append(f"{sep}{{\\1c{red_color}}}{escape_ass_text(red_text)}")
        text = "".join(parts)
        last_line_text = f"{white_text} {red_text}".strip() if (white_text and red_text) else (white_text or red_text)
        last_line_center_y = margin_v + line_height * 0.5

    events = [
        f"Dialogue: 2,{ass_timestamp(0)},{ass_timestamp(total_duration)},Hook,,0,0,0,,{text}"
    ]

    image_overlays: List[Dict[str, Any]] = []
    if emoji_cluster:
        glyph_px = round(base_px * 1.1)
        rendered = render_emoji_cluster_png(emoji_cluster, glyph_px)
        if rendered:
            emoji_path, emoji_w, emoji_h = rendered
            gap = round(base_px * 0.25)
            last_line_width = measure_text_width(last_line_text, font_family, font_name, base_px)
            line_end_x = video_width / 2 + last_line_width / 2
            emoji_x = round(line_end_x + gap)
            emoji_y = round(last_line_center_y - emoji_h / 2)
            image_overlays.append(
                {
                    "path": emoji_path,
                    "width": emoji_w,
                    "height": emoji_h,
                    "x": emoji_x,
                    "y": emoji_y,
                    "start": 0.0,
                    "end": total_duration,
                }
            )

    return style_line, events, image_overlays
