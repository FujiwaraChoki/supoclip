"""Captions helpers for the video pipeline."""

import re
import unicodedata
from typing import Any
from typing import Callable
from typing import Dict
from typing import Iterable
from ..font_registry import FONTS_DIR
from typing import List
from typing import Optional
from ..emoji_captions import POWER_WORDS
from pathlib import Path
from typing import Sequence
from typing import Tuple
from ..edit_fx.censor import censored_words
from ..emoji_captions import annotate_caption_words
from ..font_registry import find_font_path
from ..font_registry import get_font_advance_metrics
from ..font_registry import get_font_codepoints
from ..font_registry import get_font_family_name
from ..caption_templates import get_template
from ..clip_source_map import normalize_source_ranges
from ..emoji_captions import normalize_token
import cv2
import numpy as np
import tempfile
from .common import (
    EMOJI_FONT_NAME,
    HOOK_TITLE_MIN_SECONDS,
    HOOK_TITLE_SECONDS,
    HOOK_TITLE_SIDE_MARGIN_FRAC,
    HOOK_TITLE_TOP_MARGIN_FRAC,
    _EMOJI_SUPPORT_CACHE,
    hook_title_window,
    logger,
)
from .ffmpeg import (
    rendered_ranges_duration,
    run_ffmpeg_command,
    subtitles_filter_fragment,
)
from .transcription import (
    load_cached_transcript_data,
)
from .timeline import (
    get_absolute_words_in_range,
    get_words_for_keep_ranges,
    get_words_in_range,
)


def get_scaled_font_size(base_font_size: int, video_width: int) -> int:
    """Scale caption font size by output width while preserving user choices.

    Template defaults remain readable on 1080-wide vertical clips, while the
    full 12-72 UI range produces a meaningful, monotonic size change.
    """
    scaled_size = round(base_font_size * (video_width / 560.0))
    return max(26, min(132, scaled_size))


def get_subtitle_max_width(video_width: int) -> int:
    """Return max subtitle text width with horizontal safe margins."""
    horizontal_padding = max(40, int(video_width * 0.06))
    return max(200, video_width - (horizontal_padding * 2))


def caption_text_measurer(font_family: Optional[str], font_px: int):
    """Return ``measure(text) -> px`` for text rendered by libass at ``font_px``.

    Uses the font's own advance widths when the font file is available, and a
    deliberately wide per-character estimate otherwise.
    """
    font_path = (
        find_font_path(font_family, allow_all_user_fonts=True) if font_family else None
    )
    metrics = get_font_advance_metrics(font_path) if font_path else None
    if metrics is None:
        return lambda text: len(text) * font_px * 0.62
    return lambda text: metrics.text_width(text, font_px)


def split_caption_chunk(
    widths: List[float], space_width: float, max_width: float
) -> List[Tuple[int, int]]:
    """Split one caption chunk into ``(start, end)`` runs that fit ``max_width``.

    A chunk that fits stays whole. Otherwise it is cut where the wider part is
    narrowest, and each part again, so "5,000 MILLIAMP-HOUR RECHARGEABLE
    BATTERY" becomes two balanced lines instead of one that leaves the frame.
    A single word wider than the line stays alone; the caller scales it down.
    """

    def width(start: int, end: int) -> float:
        return sum(widths[start:end]) + space_width * max(0, end - start - 1)

    def split(start: int, end: int) -> List[Tuple[int, int]]:
        if end - start <= 1 or width(start, end) <= max_width:
            return [(start, end)]
        cut = min(
            range(start + 1, end),
            key=lambda index: (max(width(start, index), width(index, end)), -index),
        )
        return split(start, cut) + split(cut, end)

    return split(0, len(widths)) if widths else []


def get_safe_vertical_position(
    video_height: int, text_height: int, position_y: float
) -> int:
    """Return subtitle y position clamped inside a top/bottom safe area."""
    min_top_padding = max(40, int(video_height * 0.05))
    min_bottom_padding = max(120, int(video_height * 0.10))

    desired_y = int(video_height * position_y - text_height // 2)
    max_y = video_height - min_bottom_padding - text_height
    return max(min_top_padding, min(desired_y, max_y))


# Spoken captions moved off burned-in source captions stay below this height,
# so they do not land on the speaker's face or under the hook title.
MIN_MOVED_CAPTION_CENTER = 0.45


def caption_center_clear_of_band(
    center_y: int, text_height: int, band: Tuple[float, float], video_height: int
) -> int:
    """Move a caption centre so its line does not overlap ``band``.

    Returns ``center_y`` unchanged when there is no overlap. Otherwise the
    caption goes just above the band, or just below it when there is no room
    above, keeping the bottom safe area clear.
    """
    gap = max(12, int(video_height * 0.015))
    half = text_height // 2
    band_top, band_bottom = int(band[0] * video_height), int(band[1] * video_height)
    if center_y + half + gap <= band_top or center_y - half - gap >= band_bottom:
        return center_y

    above = band_top - gap - half
    if above >= video_height * MIN_MOVED_CAPTION_CENTER:
        return above
    below = band_bottom + gap + half
    bottom_limit = video_height - max(120, int(video_height * 0.10)) - half
    if below <= bottom_limit:
        return below
    return max(above, int(video_height * MIN_MOVED_CAPTION_CENTER))


def emoji_rendering_supported() -> bool:
    """Whether this environment's libass renders COLOUR emojis (cached, one-shot).

    Caption emojis are only injected when this returns True, so we never burn
    ugly ".notdef" tofu boxes if the runtime's libass/FreeType can't rasterise
    the bundled colour-emoji font. The captions still get keyword emphasis either
    way. The probe burns a single emoji and checks the frame for saturated colour.
    """
    global _EMOJI_SUPPORT_CACHE
    if _EMOJI_SUPPORT_CACHE is not None:
        return _EMOJI_SUPPORT_CACHE

    result = False
    try:
        with tempfile.TemporaryDirectory(prefix="supoclip_emojiprobe_") as probe_dir:
            root = Path(probe_dir)
            ass = root / "probe.ass"
            frame = root / "probe.png"
            ass.write_text(
                "[Script Info]\n"
                "ScriptType: v4.00+\nPlayResX: 120\nPlayResY: 120\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
                "BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, Encoding\n"
                f"Style: D,{EMOJI_FONT_NAME},90,&H00FFFFFF,&H00000000,&H00000000,0,1,0,0,5,1\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,D,,0,0,0,,"
                "{\\pos(60,60)}\U0001F525\n",
                encoding="utf-8",
            )
            fonts = FONTS_DIR if FONTS_DIR.exists() else None
            fragment = subtitles_filter_fragment(ass, fonts)
            command = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=120x120:d=1",
                "-vf", fragment,
                "-frames:v", "1",
                str(frame),
            ]
            if run_ffmpeg_command(command, timeout=60).returncode == 0 and frame.exists():
                image = cv2.imread(str(frame), cv2.IMREAD_COLOR)
                if image is None:
                    raise RuntimeError("probe frame unreadable")
                arr = image.astype(np.int16)
                spread = arr.max(axis=2) - arr.min(axis=2)  # 0 for grey/tofu
                result = int((spread > 40).sum()) > 30
    except Exception as exc:
        logger.info("Emoji support probe failed (%s); disabling caption emojis", exc)
        result = False

    _EMOJI_SUPPORT_CACHE = result
    logger.info("Caption colour-emoji rendering supported: %s", result)
    return result


# Maps an edit-time instant (seconds) to final time: the dynamic-edits time warp's ``to_output``.
TimeMap = Callable[[float], float]


def ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - (hours * 3600) - (minutes * 60)
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def hex_to_ass_color(
    value: Optional[str], fallback: str = "#FFFFFF", include_alpha: bool = True
) -> str:
    value = (value or fallback).strip()
    if value.startswith("#"):
        value = value[1:]
    alpha = 0
    if len(value) == 8:
        css_alpha = int(value[6:8], 16)
        alpha = 255 - css_alpha
        value = value[:6]
    if len(value) != 6:
        value = fallback.lstrip("#")
        if len(value) == 8:
            css_alpha = int(value[6:8], 16)
            alpha = 255 - css_alpha
            value = value[:6]
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha_part = f"{alpha:02X}" if include_alpha else "00"
    return f"&H{alpha_part}{blue}{green}{red}&"


def escape_ass_text(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
        .replace("\r", " ")
        .strip()
    )


def ass_font_name(font_family: Optional[str]) -> str:
    if not font_family:
        return "Arial"
    font_path = find_font_path(font_family, allow_all_user_fonts=True)
    if font_path:
        return get_font_family_name(Path(font_path)) or Path(font_path).stem
    return font_family or "Arial"


def ass_fonts_dir(font_family: Optional[str]) -> Optional[Path]:
    if not font_family:
        return FONTS_DIR if FONTS_DIR.exists() else None
    font_path = find_font_path(font_family, allow_all_user_fonts=True)
    if font_path:
        return font_path.parent
    return FONTS_DIR if FONTS_DIR.exists() else None


# Bundled font used when the chosen one lacks glyphs for the clip's text. Its
# width is close to THEBOLDFONT's, so hook-title line wrapping still fits.
GLYPH_FALLBACK_FONT = "TikTokSans-Bold"
# These draw lowercase letters as capitals; their fallback is uppercased so the
# clip keeps the same all-caps look.
CAPS_ONLY_FONTS = frozenset({"THEBOLDFONT"})


def ensure_glyph_coverage(
    font_family: str, template: Dict[str, Any], texts: Iterable[str]
) -> Tuple[str, Dict[str, Any]]:
    """Swap to GLYPH_FALLBACK_FONT when ``font_family`` cannot draw ``texts``.

    libass silently renders missing glyphs from another font, so the free
    THEBOLDFONT (no accented letters) printed "ACTúA" with a lowercase ú taken
    from a system font. One covering font for the whole clip looks consistent.
    """
    font_path = find_font_path(font_family, allow_all_user_fonts=True)
    fallback_path = find_font_path(GLYPH_FALLBACK_FONT)
    # ass_fonts_dir() points libass at the original font's folder only.
    if not font_path or not fallback_path or font_path.parent != fallback_path.parent:
        return font_family, template
    covered = get_font_codepoints(font_path)
    fallback_covered = get_font_codepoints(fallback_path)
    if not covered or not fallback_covered:
        return font_family, template

    missing = {
        ch for text in texts for ch in text if ch.isalpha() and ord(ch) not in covered
    }
    if not missing or any(ord(ch) not in fallback_covered for ch in missing):
        return font_family, template

    logger.info(
        "Font %s has no glyphs for %s; rendering captions with %s",
        font_family,
        "".join(sorted(missing)),
        GLYPH_FALLBACK_FONT,
    )
    if font_path.stem in CAPS_ONLY_FONTS:
        template = {**template, "uppercase": True}
    return GLYPH_FALLBACK_FONT, template


def _title_width(text: str) -> int:
    """Width in average Latin characters: a Han, kana or Hangul character is about two."""
    return sum(2 if unicodedata.east_asian_width(character) in "WF" else 1 for character in text)


def _title_lines(title: str, max_chars: int) -> List[str]:
    """One or two lines. Text written without spaces breaks between characters."""
    words = title.split()
    if len(words) != 1 or _title_width(title) <= max_chars or _title_width(title) == len(title):
        return _balance_title_lines(words, max_chars)
    middle = min(
        range(1, len(title)),
        key=lambda i: max(_title_width(title[:i]), _title_width(title[i:])),
    )
    return [title[:middle], title[middle:]]


def _balance_title_lines(words: List[str], max_chars: int) -> List[str]:
    """Split title words into one line, or two lines balanced around the middle."""
    text = " ".join(words)
    if len(text) <= max_chars or len(words) < 2:
        return [text]
    best_lines = [text]
    best_longest = len(text)
    for i in range(1, len(words)):
        first = " ".join(words[:i])
        second = " ".join(words[i:])
        longest = max(len(first), len(second))
        if longest < best_longest:
            best_longest = longest
            best_lines = [first, second]
    return best_lines


# The hook title of a Dynamic edits clip rendered without the drawing stage (build_hook_title_ass heavy=True).
HOOK_HEAVY_FONT = "Poppins-ExtraBold"
# libass sizes a font by ascender + descender (1.4 em for Poppins): this is about 0.060 of the width in em.
HOOK_HEAVY_SIZE_FRAC = 0.084


def hook_heavy_accent(template: Dict[str, Any]) -> str:
    """A bright accent for numbers and power words on video: the template's, unless it is dark or white."""
    value = str(template.get("emphasis_color") or template.get("highlight_color") or "").lstrip("#")[:6]
    try:
        r, g, b = (int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return "#FFE45C"
    luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255
    return "#FFE45C" if luma < 0.45 or (luma > 0.9 and max(r, g, b) - min(r, g, b) < 64) else f"#{value.upper()}"


# How long a recap's point stays on screen, and the least worth showing.
POINT_LABEL_SECONDS = 3.0
POINT_LABEL_MIN_SECONDS = 1.2


def build_point_label_ass(
    labels: List[Dict[str, Any]],
    template: Dict[str, Any],
    video_width: int,
    video_height: int,
    output_duration: float,
    font_name: str,
    caption_font_px: int,
    position_y: Optional[float] = None,
    hook_end: float = 0.0,
    time_map: Optional[TimeMap] = None,
) -> Tuple[str, List[str]]:
    """Text naming each point of a recap, on a dark backing, for about 3 s as the point starts.

    It sits where the hook title sits, the place the layouts keep clear of the
    captions and faces, so a label that would meet the title waits for it to go.
    ``labels`` are {"text", "start"} on the clip timeline, in play order.
    ``time_map`` (edit → final seconds) moves each event onto a time-warped clip.
    """
    primary = hex_to_ass_color(template.get("font_color"), "#FFFFFF")
    base_px = min(64, max(34, int(round(caption_font_px * 0.8))))
    usable_width = video_width - 2 * max(48, int(video_width * HOOK_TITLE_SIDE_MARGIN_FRAC))
    margin_v = max(48, int(video_height * HOOK_TITLE_TOP_MARGIN_FRAC))
    backing_px = max(6, base_px // 4)

    style_line = (
        f"Style: Point,{font_name},{base_px},{primary},&H000000FF,&H59000000,&H59000000,"
        f"1,0,0,0,100,100,0,0,3,{backing_px},0,8,60,60,{margin_v},1"
    )
    events: List[str] = []
    starts = [max(0.0, float(label["start"])) for label in labels]
    for position, label in enumerate(labels):
        raw = " ".join(str(label.get("text") or "").split())
        if not raw:
            continue
        text_value = raw.upper() if template.get("uppercase") else raw
        start = max(starts[position], hook_end)
        end = min(
            start + POINT_LABEL_SECONDS,
            output_duration,
            starts[position + 1] - 0.05 if position + 1 < len(labels) else output_duration,
        )
        if end - start < POINT_LABEL_MIN_SECONDS:
            continue
        max_chars = max(10, int(usable_width / (base_px * 0.52)))
        lines = _title_lines(text_value, max_chars)
        longest = max(_title_width(line) for line in lines)
        size = f"\\fs{max(24, int(usable_width / (longest * 0.52)))}" if longest > max_chars else ""
        placement = f"\\an2\\pos({video_width // 2},{round(video_height * position_y)})" if position_y is not None else ""
        body = "\\N".join(escape_ass_text(line) for line in lines)
        if time_map is not None:
            start, end = time_map(start), time_map(end)
        events.append(
            f"Dialogue: 1,{ass_timestamp(start)},{ass_timestamp(end)},Point,,0,0,0,,"
            f"{{\\fad(120,160){placement}{size}}}{body}"
        )
    return style_line, events


def build_hook_title_ass(
    hook_title: str,
    template: Dict[str, Any],
    video_width: int,
    video_height: int,
    output_duration: float,
    font_name: str,
    caption_font_px: int,
    position_y: Optional[float] = None,
    editor_style: Optional[Dict[str, Any]] = None,
    time_map: Optional[TimeMap] = None,
    center_y: Optional[float] = None,
    heavy: bool = False,
) -> Tuple[str, List[str]]:
    """Build the (style_line, dialogue_events) for a burned-in hook title.

    ``heavy`` is the title of a Dynamic edits clip rendered without the drawing
    stage (fx-core draws the real one, a white card): heavy white type about
    6 % of the width tall with a black outline and a soft shadow, never a box.

    ``center_y`` centres the title on that output fraction instead (the seam of
    a stacked interview two-shot); ``position_y`` anchors its bottom edge.

    The title sits in the top safe area (Alignment 8), styled off the caption
    template so it reads as part of the same design system: same font, an
    outline/backing for contrast, power words and numbers in the template's
    highlight colour, and a quick fade+pop entrance. ``time_map`` (edit →
    final seconds) moves the event onto a time-warped clip.
    """
    uppercase = bool(template.get("uppercase"))
    title_text = hook_title.upper() if uppercase else hook_title

    primary = hex_to_ass_color(template.get("font_color"), "#FFFFFF")
    highlight = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")

    # Larger than the captions: the headline is what a scrolling viewer reads
    # before the spoken line. Long titles wrap to two lines, and a line that
    # still does not fit (a long compound word, or two long lines) shrinks
    # below the preferred size so it is not clipped at the frame edge.
    base_px = min(96, max(caption_font_px + 8, int(round(caption_font_px * 1.35))))
    if heavy:
        base_px = max(30, int(round(min(video_width, video_height) * HOOK_HEAVY_SIZE_FRAC)))
        font_name = ass_font_name(HOOK_HEAVY_FONT) if find_font_path(HOOK_HEAVY_FONT) else font_name
        primary, uppercase, title_text = "&H00FFFFFF", False, hook_title
    elif editor_style is not None:
        base_px = int(editor_style["base_px"])
    usable_width = video_width - 2 * max(48, int(video_width * HOOK_TITLE_SIDE_MARGIN_FRAC))
    max_chars = max(10, int(usable_width / (base_px * 0.52)))
    lines = _title_lines(title_text, max_chars)
    longest = max(_title_width(line) for line in lines)
    hook_px = base_px
    if longest > max_chars:
        fitted = int(usable_width / (longest * 0.52))
        hook_px = min(base_px, max(1, fitted))
        if editor_style is not None:
            hook_px = max(30, hook_px)
    original_px = hook_px
    if editor_style is not None:
        hook_px *= float(editor_style.get("scale", 1))

    base_stroke = int(template.get("stroke_width", 3) or 0)
    has_outline = template.get("stroke_color") is not None and base_stroke > 0
    border_style = 3 if (not has_outline and template.get("background_color")) else 1
    outline_px = (
        max(base_stroke, round(hook_px * base_stroke / 26)) if has_outline else 0
    )
    if border_style == 3:
        outline_px = max(4, hook_px // 6)  # backing-box padding
        if template.get("fx"):
            # libass fills a BorderStyle-3 box with the OUTLINE colour: for animated templates use the template's
            # box colour, so dark text on a light card (or white on a dark pill) stays readable. Legacy templates
            # keep their exact look (black box).
            outline = back_color
    elif outline_px == 0:
        outline_px = max(2, hook_px // 16)  # always keep contrast on video
    shadow_px = max(2, hook_px // 20) if template.get("shadow") else 0
    if heavy:
        border_style, outline, back_color = 1, "&H00000000", "&H80000000"
        outline_px, shadow_px = max(3, round(hook_px * 0.065)), 0
        highlight = hex_to_ass_color(hook_heavy_accent(template), "#FFE45C")
    margin_v = max(48, int(video_height * HOOK_TITLE_TOP_MARGIN_FRAC))

    style_line = (
        f"Style: Hook,{font_name},{hook_px},{primary},&H000000FF,{outline},{back_color},"
        f"1,0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},8,60,60,{margin_v},1"
    )

    # Accent power words / numbers in the template highlight colour.
    rendered_lines: List[str] = []
    for line in lines:
        spans: List[str] = []
        for word in line.split():
            token = normalize_token(word)
            accented = bool(token) and (token in POWER_WORDS or any(c.isdigit() for c in token))
            color = highlight if accented else primary
            spans.append(f"{{\\c{color}}}{escape_ass_text(word)}")
        rendered_lines.append(" ".join(spans))
    text = "\\N".join(rendered_lines)

    start = 0.12
    end = hook_title_window(output_duration)
    if output_duration <= HOOK_TITLE_MIN_SECONDS:
        start, end = 0.0, max(0.5, output_duration)
    if editor_style is not None and "start" in editor_style:
        start = max(0.0, min(float(editor_style["start"]), max(0.0, output_duration - .04)))
        end = max(start + min(.04, output_duration), min(float(editor_style["end"]), output_duration))
    span = max(0.001, end - start)
    fade_in, fade_out = min(.16, span / 3), min(.24, span / 3)
    entrance = f"\\fad({round(fade_in * 1000)},{round(fade_out * 1000)})"
    if editor_style is not None:
        x = video_width * (0.5 + float(editor_style.get("x", 0)))
        y = margin_v + original_px * 1.2 * len(lines) / 2 + video_height * float(editor_style.get("y", 0))
        entrance += f"\\an5\\pos({round(x)},{round(y)})"
    elif center_y is not None:
        entrance += f"\\an5\\pos({video_width // 2},{round(video_height * center_y)})"
    elif position_y is not None:
        entrance += f"\\an2\\pos({video_width // 2},{round(video_height * position_y)})"
    if template.get("word_pop", True):
        entrance += f"\\fscx90\\fscy90\\t(0,{round(fade_in * 1000)},\\fscx100\\fscy100)"
    if time_map is not None:
        start, end = time_map(start), time_map(end)
    events = [
        f"Dialogue: 1,{ass_timestamp(start)},{ass_timestamp(end)},Hook,,0,0,0,,"
        f"{{{entrance}}}{text}"
    ]
    if heavy:
        # libass has no soft shadow: a blurred, half-transparent black copy with a wider edge, under the crisp title.
        plain = "\\N".join(escape_ass_text(line) for line in lines)
        blur = max(4, round(hook_px * 0.09))
        events.insert(0, (
            f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Hook,,0,0,0,,"
            f"{{{entrance}\\1c&H000000&\\3c&H000000&\\alpha&H73&\\bord{outline_px * 2}\\blur{blur}}}{plain}"
        ))
    return style_line, events


# A planned clip that jumps this far in the source (or backwards) starts a
# new passage: a recap point, a trailer beat, the far side of a skipped tangent.
PASSAGE_JUMP_SECONDS = 2.0


def _passage_breaks(
    transcript_data: Dict[str, Any], keep_ranges: List[Tuple[float, float]]
) -> List[int]:
    """Word indices (in get_words_for_keep_ranges order) that open a new passage."""
    ranges = normalize_source_ranges(keep_ranges)
    breaks: List[int] = []
    count = 0
    for index, (start, end) in enumerate(ranges):
        if index and count:
            previous_start, previous_end = ranges[index - 1]
            if start < previous_start or start - previous_end > PASSAGE_JUMP_SECONDS:
                breaks.append(count)
        count += len(get_absolute_words_in_range(transcript_data, start, end))
    return breaks


CAPTION_LINE_MAX_PAUSE_SECONDS = 1.0
# A line sits on a shot's own caption row when it is spoken inside that shot, give or take this much.
CAPTION_ROW_EDGE_SECONDS = 0.15
# A line stays up until the next one when the gap is this short, and is on screen at least MIN_HOLD
# (never past the next line's start). Same values as the look captions (edit_fx.caption_groups).
CAPTION_LINE_BRIDGE_SECONDS = 0.30
CAPTION_LINE_MIN_HOLD_SECONDS = 0.40
# A line that the next one replaces sooner than this cannot be read; it joins a neighbour instead.
CAPTION_LINE_MIN_READABLE_SECONDS = 0.30

_SENTENCE_END = re.compile(r"[.!?…。！？][\"'”’)\]]*$")
# "Mr." and "J." end with a full stop without ending a sentence.
_NOT_A_SENTENCE_END = frozenset({"mr", "mrs", "ms", "dr", "prof", "st", "jr", "sr", "vs", "mt", "no", "etc"})


def _pause_breaks(words: List[Dict[str, Any]]) -> List[int]:
    """Indices of words that follow a pause too long for one caption line to hold through."""
    return [
        index for index in range(1, len(words))
        if float(words[index]["start"]) - float(words[index - 1]["end"]) > CAPTION_LINE_MAX_PAUSE_SECONDS
    ]


def ends_sentence(text: str) -> bool:
    """True when a transcript word closes a sentence ("here?", "guess.", "go!")."""
    word = (text or "").strip()
    if not _SENTENCE_END.search(word):
        return False
    bare = re.sub(r"[^\w]+", "", word, flags=re.UNICODE).lower()
    if word.endswith(".") and (bare in _NOT_A_SENTENCE_END or (len(bare) == 1 and bare.isalpha())):
        return False
    return True


def _sentence_breaks(words: List[Dict[str, Any]]) -> List[int]:
    """Indices of words that open a sentence, so a caption line never joins the end of one sentence
    (or speaker) to the start of the next ("SIGN. YEAH, LISTEN, LET'S")."""
    return [
        index for index in range(1, len(words))
        if ends_sentence(str(words[index - 1].get("text", "")))
    ]


def _merge_flash_lines(
    lines: List[Tuple[int, int]], words: List[Dict[str, Any]], chunk_size: int, hard_breaks: Iterable[int],
) -> List[Tuple[int, int]]:
    """Join a line that the next one would replace too soon to read to a neighbour.

    In fast speech a one-word sentence ("Yeah.") is followed by the next line a fifth of a second
    later. Such a line goes with the line after it, or with the one before when they run together,
    as long as the result stays within ``chunk_size`` words. Lines are never joined across a pause
    or a passage break (``hard_breaks``).
    """
    hard = set(hard_breaks)
    lines = list(lines)
    index = 0
    while index < len(lines) - 1:
        start, stop = lines[index]
        next_start, next_stop = lines[index + 1]
        shown = float(words[next_start]["start"]) - float(words[start]["start"])
        if shown >= CAPTION_LINE_MIN_READABLE_SECONDS:
            index += 1
            continue
        if next_start not in hard and next_stop - start <= chunk_size:
            lines[index:index + 2] = [(start, next_stop)]
            continue
        if index and start not in hard:
            prev_start, prev_stop = lines[index - 1]
            gap = float(words[start]["start"]) - float(words[prev_stop - 1]["end"])
            if stop - prev_start <= chunk_size and gap <= CAPTION_LINE_BRIDGE_SECONDS:
                lines[index - 1:index + 1] = [(prev_start, stop)]
                index -= 1
                continue
        index += 1
    return lines


def _line_display_ends(lines: List[Tuple[float, float]], before_cut: Iterable[int] = ()) -> List[float]:
    """When each caption line leaves the screen, given (first word start, last word end) per line.

    A line ends with its last word, stays for the minimum hold, and bridges a short gap to the next
    line. It never overlaps the next line and never waits out a long pause. A line that is the last
    of its passage (``before_cut``: line indices) ends with its last word, so it is gone when a
    planned clip jumps to its next moment.
    """
    cut = set(before_cut)
    ends: List[float] = []
    for index, (start, end) in enumerate(lines):
        if index in cut:
            ends.append(end)
            continue
        shown_until = max(end, start + CAPTION_LINE_MIN_HOLD_SECONDS)
        if index + 1 < len(lines):
            next_start = lines[index + 1][0]
            if next_start - shown_until <= CAPTION_LINE_BRIDGE_SECONDS:
                shown_until = next_start
            shown_until = min(shown_until, max(next_start, start + 0.05))
        ends.append(shown_until)
    return ends


def _chunk_bounds(word_count: int, chunk_size: int, breaks: List[int], balanced: bool = False):
    """Caption lines of ``chunk_size`` words that never straddle a passage break.

    A line showing the next beat's words before the cut would give the edit away.

    ``balanced`` shares the words between two breaks evenly over the lines they need, so a sentence
    of five words reads 3 + 2 instead of 4 and a single word left over.
    """
    start = 0
    for limit in [*sorted(set(breaks)), word_count]:
        limit = min(limit, word_count)
        if balanced and limit > start:
            run = limit - start
            lines = -(-run // chunk_size)
            base, extra = divmod(run, lines)
            for index in range(lines):
                stop = start + base + (1 if index < extra else 0)
                yield start, stop
                start = stop
            continue
        while start < limit:
            stop = min(limit, start + chunk_size)
            yield start, stop
            start = stop


def build_assemblyai_ass_subtitles(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    video_width: int,
    video_height: int,
    output_ass_path: Path,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    keep_ranges: Optional[List[Tuple[float, float]]] = None,
    caption_cues: Optional[List[Dict[str, Any]]] = None,
    hook_title: Optional[str] = None,
    include_captions: bool = True,
    caption_words: Optional[List[Dict[str, Any]]] = None,
    position_y_override: Optional[float] = None,
    highlight_words: Optional[List[str]] = None,
    template_override: Optional[Dict[str, Any]] = None,
    font_px_override: Optional[int] = None,
    center_y_override: Optional[int] = None,
    emphasis_indices: Optional[Iterable[int]] = None,
    wrap_lines: bool = False,
    center_x_override: Optional[int] = None,
    hook_editor_style: Optional[Dict[str, Any]] = None,
    hook_center_y: Optional[float] = None,
    hook_position_y: Optional[float] = None,
    hook_heavy: bool = False,
    caption_placements: Optional[List[Optional[Dict[str, Any]]]] = None,
    avoid_band: Optional[Tuple[float, float]] = None,
    caption_layout_report: Optional[Dict[str, Any]] = None,
    output_offsets: Optional[List[float]] = None,
    point_labels: Optional[List[Dict[str, Any]]] = None,
    time_map: Optional[TimeMap] = None,
    censor_spans: Optional[Sequence[Tuple[float, float]]] = None,
    drop_screen_words: bool = False,
    caption_rows: Optional[Sequence[Tuple[float, float, int]]] = None,
    caption_rows_after_hook: bool = False,
) -> bool:
    """Generate animated word-synced ASS subtitles from cached AssemblyAI words.

    ``caption_rows`` are (start, end, centre y in pixels) in clip seconds: a line spoken wholly inside one sits
    on that row instead of the clip's own (a stacked two-shot's seam, see ``video_utils``). A line that reaches
    past a row's ends keeps the clip's row. With ``caption_rows_after_hook`` the rows start once the hook title
    has gone (the title has the seam while it shows).


    Renders OpusClip-style captions: a per-word active highlight that pops, an
    accent colour on emphasised power/keyword words, contextual emojis, a thick
    scaled outline + drop shadow, and an optional pill behind the active word.
    When ``hook_title`` is set, an AI-written headline is burned into the top
    safe area while the hook plays out (it renders even when word-synced
    captions are unavailable or disabled via ``include_captions``).

    The clip editor holds final style values itself: ``template_override``
    replaces the named template, ``font_px_override`` skips width scaling,
    ``center_y_override`` places the caption centre without the safe-area
    clamp, ``emphasis_indices`` replaces automatic emphasis with exactly those
    words, and ``wrap_lines`` lets libass wrap lines wider than the frame.
    ``avoid_band`` is a (top, bottom) height fraction holding captions burned
    into the source; the spoken captions move clear of it. ``output_offsets``
    are a workflow clip's explicit range starts on the clip timeline (planned
    junction fades); without them the renderer's default crossfades apply.
    ``point_labels`` are a recap's on-screen points (``build_point_label_ass``).
    ``drop_screen_words`` leaves out words the picture already shows (a game's own subtitles) and stray
    sounds heard as words (``media/screen_words.py``).

    Dynamic edits only (both default to off, which leaves the output unchanged):
    ``time_map`` maps every event's start and end from edit to final seconds
    (the clip's time warp: freezes and speed ramps), and ``censor_spans`` are
    bleeped edit-time spans whose words are masked (``edit_fx.censor``).
    """
    transcript_data = load_cached_transcript_data(video_path)

    template = template_override or get_template(caption_template)
    effective_font_family = font_family or template["font_family"]
    effective_font_size = int(font_size) if font_size else int(template["font_size"])
    effective_font_color = font_color or template["font_color"]
    animation = template.get("animation", "karaoke")

    relevant_words: List[Dict[str, Any]] = list(caption_words or [])
    passage_breaks: List[int] = []
    # Lines cut from the transcript follow speech (pauses, sentences). Words handed in by the editor or
    # an FX caption edit keep their fixed groups: per-line placements are keyed by them.
    natural_lines = False
    hard_breaks: List[int] = []
    jump_breaks: set = set()
    if (
        include_captions
        and not relevant_words
        and transcript_data
        and transcript_data.get("words")
    ):
        every_word = transcript_data
        if drop_screen_words:
            try:
                from .screen_words import without_screen_words
                transcript_data = without_screen_words(
                    video_path, transcript_data, keep_ranges or [(clip_start, clip_end)])
            except Exception as exc:  # noqa: BLE001 - captions for every word rather than none
                logger.warning("Screen-word check failed (%s); captioning every word", exc)
        if keep_ranges:
            relevant_words = get_words_for_keep_ranges(transcript_data, keep_ranges, output_offsets)
            if output_offsets is not None:
                passage_breaks = _passage_breaks(transcript_data, keep_ranges)
        else:
            relevant_words = get_words_in_range(transcript_data, clip_start, clip_end)
        # A caption line holds each word until the next one starts. Across a pause (a laugh, a silent
        # stretch of gameplay, a line that was left out) that is seconds of the same words with the
        # next ones already readable, so a line ends at a pause and at the end of a sentence.
        jump_breaks = set(passage_breaks)
        hard_breaks = sorted({*passage_breaks, *_pause_breaks(relevant_words)})
        passage_breaks = sorted({*hard_breaks, *_sentence_breaks(relevant_words)})
        natural_lines = True
    if not relevant_words and not hook_title and not point_labels:
        logger.warning("No words or hook title available for ASS subtitles")
        return False
    if censor_spans:
        relevant_words = censored_words(relevant_words, censor_spans)

    effective_font_family, template = ensure_glyph_coverage(
        effective_font_family,
        template,
        [hook_title or ""]
        + [str(label.get("text", "")) for label in point_labels or []]
        + [str(word.get("text", "")) for word in relevant_words],
    )

    # --- styling knobs (new template fields, all optional) ---
    uppercase = bool(template.get("uppercase"))
    # Only inject emojis when the runtime can actually render them in colour.
    enable_emoji = bool(template.get("emoji", True)) and emoji_rendering_supported()
    word_pop = bool(template.get("word_pop", True))
    word_box = bool(template.get("word_box"))
    glow = bool(template.get("glow"))
    has_outline = template.get("stroke_color") is not None
    # Emphasis colouring only makes sense when something distinguishes words.
    enable_emphasis = (
        emphasis_indices is not None or animation != "none" or bool(highlight_words)
    )

    primary = hex_to_ass_color(effective_font_color)
    highlight = hex_to_ass_color(template.get("highlight_color"), "#FFE000")
    emphasis_color = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")
    box_color = hex_to_ass_color(
        template.get("word_box_color") or template.get("highlight_color"), "#00BF49"
    )

    font_px = font_px_override or get_scaled_font_size(effective_font_size, video_width)
    base_stroke = int(template.get("stroke_width", 3) or 0)
    # Scale the outline with the font so big captions keep a chunky, readable edge.
    outline_px = (
        max(base_stroke, round(font_px * base_stroke / 26))
        if (has_outline and base_stroke)
        else 0
    )
    shadow_px = max(2, font_px // 20) if template.get("shadow") else 0
    box_bord = max(outline_px + 2, font_px // 5)
    pos_y = (
        float(position_y_override)
        if position_y_override is not None
        else float(template.get("position_y", 0.80))
    )
    est_text_height = int(font_px * 1.5)
    y_pos = (
        int(center_y_override)
        if center_y_override is not None
        else get_safe_vertical_position(video_height, est_text_height, pos_y)
    )
    if avoid_band is not None and center_y_override is None:
        y_pos = caption_center_clear_of_band(y_pos, est_text_height, avoid_band, video_height)
    if caption_layout_report is not None:
        caption_layout_report["caption_y"] = y_pos / video_height
    font_name = ass_font_name(effective_font_family)
    border_style = (
        3
        if template.get("background") and template.get("background_color")
        else 1
    )
    # libass fills BorderStyle 3 boxes with the outline colour and pads them by
    # the outline width, so an outline-less template would get no box at all.
    style_outline, style_bord = outline, outline_px
    if border_style == 3:
        style_outline, style_bord = back_color, max(outline_px, font_px // 6)

    hook_style_block = ""
    hook_events: List[str] = []
    if keep_ranges and output_offsets:
        last_start, last_end = normalize_source_ranges(keep_ranges)[-1]
        output_duration = max(0.0, float(output_offsets[-1]) + last_end - last_start)
    elif keep_ranges:
        output_duration = rendered_ranges_duration(keep_ranges)
    else:
        output_duration = max(0.0, clip_end - clip_start)
    if hook_title:
        hook_style_line, hook_events = build_hook_title_ass(
            hook_title,
            template,
            video_width,
            video_height,
            output_duration,
            font_name,
            font_px,
            # An upper-third caption seam belongs to a reaction stack. Keep
            # the headline below the source subject, off the reactor's face.
            position_y=hook_position_y if hook_position_y is not None else (
                0.94 if position_y_override is not None and position_y_override < .4 else None),
            editor_style=hook_editor_style,
            heavy=hook_heavy,
            **({"time_map": time_map} if time_map is not None else {}),
            center_y=hook_center_y,
        )
        hook_style_block = f"{hook_style_line}\n"
    if point_labels:
        point_style_line, point_events = build_point_label_ass(
            point_labels,
            template,
            video_width,
            video_height,
            output_duration,
            font_name,
            font_px,
            position_y=0.94 if position_y_override is not None and position_y_override < .4 else None,
            hook_end=min(HOOK_TITLE_SECONDS, output_duration) + 0.1 if hook_title else 0.0,
            **({"time_map": time_map} if time_map is not None else {}),
        )
        hook_style_block += f"{point_style_line}\n"
        hook_events = hook_events + point_events

    # Contextual emoji + emphasis annotations over the whole clip word list.
    emoji_by_idx, emphasis_idx = annotate_caption_words(
        relevant_words,
        caption_cues,
        enable_emoji=enable_emoji,
        enable_emphasis=enable_emphasis and emphasis_indices is None,
    )
    if emphasis_indices is not None:
        emphasis_idx = set(emphasis_indices)
    requested_highlights = {
        normalize_token(word)
        for word in (highlight_words or [])
        if normalize_token(word)
    }
    emphasis_idx.update(
        index
        for index, word in enumerate(relevant_words)
        if normalize_token(str(word.get("text", ""))) in requested_highlights
    )

    max_words = max(1, int(template.get("max_words_per_line", 4) or 4))
    chunk_size = max_words

    # Word-count chunks can be far wider than the frame ("5,000 MILLIAMP-HOUR
    # RECHARGEABLE BATTERY"). Measure each chunk as libass will draw it, split
    # the ones that overflow the side margins, and shrink a single word that
    # still does not fit. The editor lets libass wrap instead (``wrap_lines``).
    measure = caption_text_measurer(effective_font_family, font_px)
    space_width = measure(" ")
    edge_px = max(style_bord, box_bord if word_box else 0) + shadow_px
    peak_scale = {"pop": 1.06, "bounce": 1.12}.get(animation, 1.0)
    available_width = (get_subtitle_max_width(video_width) - 2 * edge_px) / peak_scale
    emoji_width = space_width + font_px * 1.2

    def display_width(index: int, word: Dict[str, Any]) -> float:
        text = str(word.get("text", ""))
        width = measure(text.upper() if uppercase else text)
        return width + (emoji_width if emoji_by_idx.get(index) else 0.0)

    word_widths = [display_width(index, word) for index, word in enumerate(relevant_words)]
    chunk_spans: List[Tuple[int, int, float]] = []
    line_bounds = list(_chunk_bounds(len(relevant_words), chunk_size, passage_breaks, natural_lines))
    if natural_lines:
        line_bounds = _merge_flash_lines(line_bounds, relevant_words, chunk_size, hard_breaks)
    for chunk_start, chunk_stop in line_bounds:
        if wrap_lines:
            chunk_spans.append((chunk_start, chunk_stop, 1.0))
            continue
        widths = word_widths[chunk_start:chunk_stop]
        for start, end in split_caption_chunk(widths, space_width, available_width):
            line_width = sum(widths[start:end]) + space_width * (end - start - 1)
            scale = min(1.0, available_width / line_width) if line_width > 0 else 1.0
            chunk_spans.append((chunk_start + start, chunk_start + end, scale))

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: {0 if wrap_lines else 2}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_px},{primary},&H000000FF,{style_outline},{back_color},1,0,0,0,100,100,0,0,{border_style},{style_bord},{shadow_px},5,60,60,60,1
{hook_style_block}
[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    x_pos = video_width // 2 if center_x_override is None else center_x_override
    def scaled(percent: int, scale: float) -> int:
        return max(1, round(percent * scale))

    def line_prefix(scale: float, y: Optional[int] = None) -> str:
        tags = f"\\pos({x_pos},{y_pos if y is None else y})" + ("\\blur4" if glow else "")
        if scale < 1.0:
            tags += f"\\fscx{scaled(100, scale)}\\fscy{scaled(100, scale)}"
        return f"{{{tags}}}"

    # Every word span re-declares the caption font, so an emoji's \fn override
    # can never leak into the following word.
    font_tag = f"\\fn{font_name}"

    def render_text(global_idx: int, word: Dict[str, Any]) -> str:
        text = str(word.get("text", ""))
        if uppercase:
            text = text.upper()
        disp = escape_ass_text(text)
        emoji = emoji_by_idx.get(global_idx)
        if emoji:
            # Force the colour-emoji font for the glyph; the next word's span
            # re-declares the caption font, so no explicit restore is needed.
            disp = f"{disp} {{\\fn{EMOJI_FONT_NAME}}}{emoji}"
        return disp

    # The active word is distinguished by COLOUR only (and an optional box). We
    # deliberately do NOT scale individual words: scaling a word changes its
    # advance width, which reflows the centre-anchored line and makes the whole
    # caption visibly vibrate as each word pops. The "pop" lives as a one-shot
    # line entrance instead (see below).
    def active_span(disp: str) -> str:
        tags = f"{font_tag}\\c{highlight}"
        if word_box:
            tags += f"\\3c{box_color}\\bord{box_bord}\\shad0"
        return f"{{{tags}}}{disp}"

    def idle_span(global_idx: int, disp: str) -> str:
        color = emphasis_color if (enable_emphasis and global_idx in emphasis_idx) else primary
        tags = f"{font_tag}\\c{color}"
        if word_box:
            tags += f"\\3c{outline}\\bord{outline_px}\\shad{shadow_px}"
        return f"{{{tags}}}{disp}"

    # Subtle one-shot entrance for the whole line (uniform scale, centred), shown
    # only as the first word of a chunk appears — gives a pop without any
    # per-word reflow/vibration.
    def line_entrance(scale: float) -> str:
        if not word_pop:
            return ""
        small, full = scaled(92, scale), scaled(100, scale)
        return f"\\fscx{small}\\fscy{small}\\t(0,140,\\fscx{full}\\fscy{full})"

    events: List[str] = []
    original_geometry = (outline_px, shadow_px, box_bord)
    rows_from = hook_title_window(output_duration) if (caption_rows_after_hook and hook_title) else 0.0
    display_ends = _line_display_ends(
        [(float(relevant_words[a]["start"]), float(relevant_words[b - 1]["end"])) for a, b, _ in chunk_spans],
        [index for index, (_, stop, _) in enumerate(chunk_spans) if stop in jump_breaks],
    ) if natural_lines else None
    for line_index, (chunk_start, chunk_stop, chunk_scale) in enumerate(chunk_spans):
        chunk = relevant_words[chunk_start:chunk_stop]
        indices = list(range(chunk_start, chunk_stop))
        chunk_end = display_ends[line_index] if display_ends is not None else float(chunk[-1]["end"])
        prefix = line_prefix(chunk_scale)
        entrance_tags = line_entrance(chunk_scale)
        outline_px, shadow_px, box_bord = original_geometry
        placement = (caption_placements[chunk_start // chunk_size]
                     if caption_placements and chunk_start // chunk_size < len(caption_placements) else None)
        if placement is None and caption_rows and time_map is None:
            line_start = float(chunk[0]["start"])
            for row_start, row_end, row_y in caption_rows:
                if (line_start >= max(row_start - CAPTION_ROW_EDGE_SECONDS, rows_from)
                        and float(chunk[-1]["end"]) <= row_end + CAPTION_ROW_EDGE_SECONDS):
                    prefix = line_prefix(chunk_scale, int(row_y))
                    break
        if placement is not None:
            px = placement["font_px"]
            outline_px = max(base_stroke, round(px * base_stroke / 26)) if has_outline else 0
            shadow_px = max(2, px // 20) if template.get("shadow") else 0
            box_bord = max(outline_px + 2, px // 5)
            border = max(outline_px, px // 6) if border_style == 3 else outline_px
            prefix = f"{{\\pos({placement['x']},{placement['y']})\\fs{px}\\bord{border}\\shad{shadow_px}" + ("\\blur4" if glow else "") + "}"

        if animation == "karaoke":
            for local_i, word in enumerate(chunk):
                start = float(word["start"])
                end = (
                    float(chunk[local_i + 1]["start"])
                    if local_i + 1 < len(chunk)
                    else chunk_end
                )
                if end <= start:
                    end = start + 0.05
                if time_map is not None:
                    start, end = time_map(start), time_map(end)
                parts = []
                for local_j, other in enumerate(chunk):
                    gj = indices[local_j]
                    disp = render_text(gj, other)
                    parts.append(active_span(disp) if local_j == local_i else idle_span(gj, disp))
                line = " ".join(parts)
                # Entrance only on the first word's event so it plays once, not
                # once per word.
                entrance = f"{{{entrance_tags}}}" if (entrance_tags and local_i == 0) else ""
                events.append(
                    f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{prefix}{entrance}{line}"
                )
        else:
            start = float(chunk[0]["start"])
            end = chunk_end
            if end <= start:
                end = start + 0.05
            if time_map is not None:
                start, end = time_map(start), time_map(end)
            spans = []
            for local_j, word in enumerate(chunk):
                gj = indices[local_j]
                disp = render_text(gj, word)
                color = emphasis_color if (enable_emphasis and gj in emphasis_idx) else primary
                spans.append(f"{{{font_tag}\\c{color}}}{disp}")
            chunk_text = " ".join(spans)

            effect = ""
            # (from %, peak %, ms to peak, ms to settle) of the scale animation
            scale_steps = {"pop": (88, 106, 130, 250), "bounce": (70, 112, 120, 240)}
            if animation == "fade":
                effect = "{\\fad(120,120)}"
            elif animation in scale_steps:
                low, peak, rise, settle = scale_steps[animation]
                low, peak, full = (scaled(p, chunk_scale) for p in (low, peak, 100))
                effect = (
                    f"{{\\fscx{low}\\fscy{low}\\t(0,{rise},\\fscx{peak}\\fscy{peak})"
                    f"\\t({rise},{settle},\\fscx{full}\\fscy{full})}}"
                )
            events.append(
                f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{prefix}{effect}{chunk_text}"
            )

    all_events = hook_events + events
    output_ass_path.write_text(header + "\n".join(all_events) + "\n", encoding="utf-8")
    logger.info(
        "Wrote ASS subtitles: %s (%d events%s)",
        output_ass_path,
        len(all_events),
        ", hook title" if hook_events else "",
    )
    return True
