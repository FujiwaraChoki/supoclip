"""The resolved look of FX-template captions (``FxCaptionStyle``), drawn by fx-core instead of libass.

Sizes follow the legacy renderer exactly so switching a clip between a legacy and an FX template keeps its caption
size: generation scales the template size by output width (``media.captions.get_scaled_font_size``), the studio
uses its 1080-wide units (``editor_document.write_ass``). The outline follows the ASS outline formula.
"""

from __future__ import annotations

import re
from typing import Sequence

from .contracts import CaptionPlacement, FxCaptionStyle

DEFAULT_ACCENT = "#FFE000"
DEFAULT_COLOR = "#FFFFFF"
DEFAULT_FONT = "TikTokSans-Bold"
DEFAULT_BOX = "#00000080"
MAX_STROKE_PX = 40.0
MAX_SIZE_PX = 400.0
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})([0-9a-fA-F]{2})?$")


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
                                   placements: Sequence[CaptionPlacement] = ()) -> FxCaptionStyle:
    """Studio: ``EditDocument.captions`` (as a dict) over the template, like ``editor_document.write_ass``.

    ``size`` is in 1080-wide units; colour, accent, position, words per line, case and the background box are the
    editor's; outline and shadow come from the template.
    """
    size = float(captions.get("size", 54))
    size_px = _clamp(max(1, round(size * width / 1080)), 1.0, MAX_SIZE_PX)
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
        words_per_line=_words_per_line(captions.get("wordsPerLine"), 4),
        uppercase=bool(captions.get("uppercase", False)),
        enabled=bool(captions.get("enabled", True)),
        placements=tuple(placements),
    )
