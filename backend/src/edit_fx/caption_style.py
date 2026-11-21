"""The resolved look of FX-template captions (``FxCaptionStyle``), drawn by fx-core instead of libass.

Sizes follow the legacy renderer exactly so switching a clip between a legacy and an FX template keeps its caption
size: generation scales the template size by output width (``media.captions.get_scaled_font_size``), the studio
uses its 1080-wide units (``editor_document.write_ass``). The outline follows the ASS outline formula.
"""

from __future__ import annotations

import re
from typing import Sequence

from .caption_groups import LOOK_MAX_WORDS
from .contracts import CaptionPlacement, CaptionRow, FxCaptionStyle

DEFAULT_ACCENT = "#FFE000"
DEFAULT_COLOR = "#FFFFFF"
DEFAULT_FONT = "TikTokSans-Bold"
DEFAULT_BOX = "#00000080"
MAX_STROKE_PX = 40.0
MAX_SIZE_PX = 400.0
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")


# Intensity caption looks (spec §3.1, §3.10): font, size in px at 1080 wide, centre y with no layout preference, case and
# the outline as a fraction of the size. Fonts are the closest bundled ones: TikTok Sans Bold for the DOAC/Inter
# subtitle, Poppins ExtraBold for Hormozi's Poppins Bold, THE BOLD FONT for the Anton/Bebas clipper caps.
LOOKS: dict[str, dict] = {
    "subtle": {"font": "TikTokSans-Bold", "px": 52, "y": 0.68, "uppercase": False, "stroke_em": 0.07},
    "bold": {"font": "Poppins-ExtraBold", "px": 72, "y": 0.55, "uppercase": False, "stroke_em": 0.0},
    "max": {"font": "THEBOLDFONT", "px": 84, "y": 0.56, "uppercase": True, "stroke_em": 0.09},
}
# Keyword colour (box fill for the highlighter, word colour for the others) and the highlighter's pill per look.
LOOK_ACCENTS: dict[tuple[str, str], str] = {
    ("highlighter_swipe", "subtle"): "#FFE45C", ("highlighter_swipe", "bold"): "#34FD87",
    ("highlighter_swipe", "max"): "#FFE45C",
}
LOOK_KEYWORD_COLORS: dict[str, str] = {"subtle": "#F2C14E", "bold": "#FFE00B", "max": "#FCFB2B"}
# Bold is Hormozi's dark pill for every style: white words with no outline vanish over a white shirt.
LOOK_PILLS: dict[tuple[str, str], str] = {
    ("highlighter_swipe", "bold"): "#170904D9", ("emoji_drop", "bold"): "#170904D9", ("behind_head", "bold"): "#170904D9",
}


def look_accent(template_id: str, look: str) -> str:
    return LOOK_ACCENTS.get((template_id, look), LOOK_KEYWORD_COLORS.get(look, DEFAULT_ACCENT))


def look_pill(template_id: str, look: str) -> str | None:
    return LOOK_PILLS.get((template_id, look))


def look_size_px(look: str, width: int) -> float:
    """The look's caption size at ``width`` (px at 1080 wide scaled), as the ASS sizes round: half to even."""
    return _clamp(float(max(1, round(LOOKS[look]["px"] * width / 1080))), 1.0, MAX_SIZE_PX)


def look_caption_style(template_id: str, look: str, *, width: int, height: int, caption_y: float | None,
                       font_color: str | None = None, enabled: bool = True,
                       rows: Sequence[CaptionRow] = ()) -> FxCaptionStyle:
    """Generation: the intensity look of a listed template. ``caption_y`` is the layout's caption centre (a stacked
    layout's seam), else the look's default; ``rows`` the interview layout's per-shot rows (``caption_rows``)."""
    spec = LOOKS[look]
    size_px = look_size_px(look, width)
    stroke_px = round(spec["stroke_em"] * size_px, 2)
    return FxCaptionStyle(
        template=template_id,
        font=spec["font"],
        size_px=size_px,
        color=_hex6(font_color, DEFAULT_COLOR),
        accent=look_accent(template_id, look),
        stroke_color="#000000" if stroke_px > 0 else None,
        stroke_px=stroke_px,
        shadow=True,
        background=look_pill(template_id, look),
        y=_clamp(float(caption_y if caption_y is not None else spec["y"]), 0.0, 1.0),
        words_per_line=LOOK_MAX_WORDS[look],
        uppercase=bool(spec["uppercase"]),
        enabled=bool(enabled),
        look=look,
        rows=tuple(rows),
    )


def scaled_font_px(base: float, width: int) -> float:
    """== ``media.captions.get_scaled_font_size`` (copied: ``clamp(round(base * width / 560), 26, 132)``)."""
    scaled_size = round(base * (width / 560.0))
    return float(max(26, min(132, scaled_size)))


def _hex6(value: object, fallback: str) -> str:
    match = _HEX.match(str(value or "").strip())
    if not match:
        return fallback
    return f"#{match.group(1).upper()}"


def _rgba(value: object) -> str | None:
    """``#RRGGBB`` / ``#RRGGBBAA`` (CSS alpha) → ``#RRGGBBAA``; anything else → None."""
    match = _HEX.match(str(value or "").strip())
    if not match:
        return None
    return f"#{match.group(1).upper()}{(match.group(2) or 'FF').upper()}"


def _stroke_px(template: dict, font_px: float) -> float:
    """The ASS outline width (``media.captions``): scales with the font so big captions keep a chunky edge."""
    try:
        base_stroke = int(template.get("stroke_width", 3) or 0)
    except (TypeError, ValueError):
        base_stroke = 0
    if template.get("stroke_color") is None or base_stroke <= 0:
        return 0.0
    return float(min(MAX_STROKE_PX, max(base_stroke, round(font_px * base_stroke / 26))))


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _words_per_line(value: object, default: int = 3) -> int:
    try:
        count = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        count = default
    return int(_clamp(count, 1, 8))


def fx_caption_style(template: dict, *, template_id: str, font_family: str | None, font_size: int | None,
                     font_color: str | None, width: int, height: int, caption_y: float | None,
                     enabled: bool = True) -> FxCaptionStyle:
    """Generation: the template dict (``caption_templates.get_template``) with the task's font overrides.

    ``caption_y`` is the caption centre as a height fraction (the layout report's value, already moved clear of
    burned-in captions); None uses the template's ``position_y``.
    """
    base_size = font_size or template.get("font_size") or 32
    size_px = _clamp(scaled_font_px(float(base_size), width), 1.0, MAX_SIZE_PX)
    y = caption_y if caption_y is not None else float(template.get("position_y", 0.80))
    background = (
        _rgba(template.get("background_color") or DEFAULT_BOX) if template.get("background") else None
    )
    stroke_color = template.get("stroke_color")
    return FxCaptionStyle(
        template=template_id,
        font=str(font_family or template.get("font_family") or DEFAULT_FONT)[:80],
        size_px=size_px,
        color=_hex6(font_color or template.get("font_color"), DEFAULT_COLOR),
        accent=_hex6(template.get("highlight_color"), DEFAULT_ACCENT),
        stroke_color=_hex6(stroke_color, "#000000") if stroke_color is not None else None,
        stroke_px=_stroke_px(template, size_px),
        shadow=bool(template.get("shadow")),
        background=background,
        y=_clamp(float(y), 0.0, 1.0),
        words_per_line=_words_per_line(template.get("max_words_per_line"), 3),
        uppercase=bool(template.get("uppercase")),
        enabled=bool(enabled),
    )


def fx_caption_style_from_document(captions: dict, template: dict, *, width: int, height: int,
                                   placements: Sequence[CaptionPlacement] = (), look: str = "template",
                                   rows: Sequence[CaptionRow] = ()) -> FxCaptionStyle:
    """Studio: ``EditDocument.captions`` (as a dict) over the template, like ``editor_document.write_ass``.

    ``size`` is in 1080-wide units; colour, accent, position, words per line, case and the background box are the
    editor's; outline and shadow come from the template, or from the intensity ``look`` (the max look's outline is
    0.09 em, the highlighter's pill appears with the background switch, the max look is always upper case).
    ``rows`` are the draft's interview caption rows on E (``editor_document.studio_caption_rows``), looks only.
    """
    size = float(captions.get("size", 54))
    size_px = _clamp(max(1, round(size * width / 1080)), 1.0, MAX_SIZE_PX)
    if look in LOOKS:
        template_id = str(captions.get("template") or template.get("id") or "default")
        stroke_px = round(LOOKS[look]["stroke_em"] * size_px, 2)
        pill = look_pill(template_id, look)
        return FxCaptionStyle(
            template=template_id[:50],
            font=str(captions.get("font") or LOOKS[look]["font"])[:80],
            size_px=size_px,
            color=_hex6(captions.get("color"), DEFAULT_COLOR),
            accent=_hex6(captions.get("accent") or look_accent(template_id, look), DEFAULT_ACCENT),
            stroke_color="#000000" if stroke_px > 0 else None,
            stroke_px=stroke_px,
            shadow=True,
            background=pill if (pill and captions.get("background", True)) else None,
            y=_clamp(float(captions.get("y", 0.78)), 0.0, 1.0),
            x=_clamp(float(captions.get("x", 0.0)), -1.0, 1.0),
            words_per_line=_words_per_line(captions.get("wordsPerLine"), LOOK_MAX_WORDS[look]),
            uppercase=bool(LOOKS[look]["uppercase"] or captions.get("uppercase", False)),
            enabled=bool(captions.get("enabled", True)),
            placements=tuple(placements),
            look=look,
            rows=tuple(rows),
        )
    background = _rgba(template.get("background_color") or DEFAULT_BOX) if captions.get("background", True) else None
    stroke_color = template.get("stroke_color")
    template_id = str(captions.get("template") or template.get("id") or "default")
    return FxCaptionStyle(
        template=template_id[:50],
        font=str(captions.get("font") or template.get("font_family") or DEFAULT_FONT)[:80],
        size_px=size_px,
        color=_hex6(captions.get("color"), DEFAULT_COLOR),
        accent=_hex6(captions.get("accent") or template.get("highlight_color"), DEFAULT_ACCENT),
        stroke_color=_hex6(stroke_color, "#000000") if stroke_color is not None else None,
        stroke_px=_stroke_px(template, size_px),
        shadow=bool(template.get("shadow")),
        background=background,
        y=_clamp(float(captions.get("y", 0.78)), 0.0, 1.0),
        x=_clamp(float(captions.get("x", 0.0)), -1.0, 1.0),
        words_per_line=_words_per_line(captions.get("wordsPerLine"), 4),
        uppercase=bool(captions.get("uppercase", False)),
        enabled=bool(captions.get("enabled", True)),
        placements=tuple(placements),
    )
