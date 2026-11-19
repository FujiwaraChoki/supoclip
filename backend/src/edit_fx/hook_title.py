"""The hook title of a Dynamic edits clip, as fx-core draws it (``FxHookTitle``, fx-core ``hook.ts``).

Whenever a render runs the drawing stage, fx-core draws the title in the style of the clip's caption template (a
white label that hugs each line, or big outlined type in the template's colours; ``HOOK_VARIANTS`` in ``hook.ts``),
and the ASS layer carries no title. This module resolves what Python decides (the text as shown, the font, the marker colour,
the look and the window); fx-core lays the title out, sizes it and places it (the top of the frame, or beneath a head it would cover).
Levels without the drawing stage (``FX_RUNTIME=off``, the safe and none levels) keep the libass title
(``media.captions.build_hook_title_ass``).

Mirrored by ``frontend/src/lib/fx/studio-scene.ts`` (``studioHook``) for the studio; ``shared/fx/fixtures/scene``
pins the two.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from .caption_style import LOOKS, _hex6, look_accent
from .contracts import FxCaptionStyle, FxHookTitle, LISTED_FX_CAPTION_TEMPLATES, caption_look_for

# The card's type for the listed animated styles, whatever the intensity (their caption fonts are the looks' own).
HOOK_FONT = "Poppins-ExtraBold"
HOOK_DEFAULT_ACCENT = "#FFE45C"
# A title is never set in a regular weight: caption fonts too light for one, and what replaces them.
HOOK_HEAVY = {"TikTokSans-Regular": "TikTokSans-Bold"}
# Shown from here (clip seconds) for clips longer than HOOK_TITLE_MIN_SECONDS.
HOOK_START = 0.12
_LOOK_FONTS = frozenset(spec["font"] for spec in LOOKS.values())


def hook_look(template_id: str | None, intensity: str | None, sensitivity: str = "normal") -> str:
    """Size, tilt and entrance of the card: the caption look for listed styles, else the clip's intensity (a serious
    clip is subtle, intensity off is bold)."""
    look = caption_look_for(template_id, intensity, sensitivity)
    if look != "template":
        return look
    if sensitivity == "serious":
        return "subtle"
    return intensity if intensity in ("subtle", "bold", "max") else "bold"


def hook_font(template_id: str | None, font: str | None) -> str:
    """The listed styles' card is Poppins ExtraBold unless the user picked a font of their own (a look's own caption
    font is not a choice); other templates use their caption font (its bold when it is a regular weight)."""
    listed = template_id in LISTED_FX_CAPTION_TEMPLATES
    if listed and (not font or font in _LOOK_FONTS):
        return HOOK_FONT
    font = str(font or HOOK_FONT)[:80]
    return HOOK_HEAVY.get(font, font)


def hook_text(title: str, template_id: str | None, uppercase: bool) -> str:
    """The title as shown: one line of text, in capitals only for an all-caps template that is not a listed style."""
    text = " ".join(str(title or "").split())[:200]
    return text.upper() if uppercase and template_id not in LISTED_FX_CAPTION_TEMPLATES else text


def hook_accent(template_id: str | None, look: str, caption: FxCaptionStyle | None,
                template: Mapping[str, Any] | None = None) -> str:
    """The marker colour: the captions' keyword colour, else the look's, else the template's emphasis colour."""
    if caption is not None:
        return _hex6(caption.accent, HOOK_DEFAULT_ACCENT)
    if template_id in LISTED_FX_CAPTION_TEMPLATES:
        return look_accent(str(template_id), look)
    template = template or {}
    return _hex6(template.get("emphasis_color") or template.get("highlight_color"), HOOK_DEFAULT_ACCENT)


def build_hook(title: str | None, *, template_id: str | None, look: str, font: str | None, accent: str,
               window: Sequence[float] | None, uppercase: bool = False, x: float = 0.0, y: float = 0.0,
               scale: float = 1.0) -> FxHookTitle | None:
    """The scene's hook title, or None without a title or a window."""
    text = hook_text(title or "", template_id, uppercase)
    if not text or not window or float(window[1]) <= float(window[0]):
        return None
    return FxHookTitle(
        text=text,
        font=hook_font(template_id, font),
        accent=accent,
        look=look,
        template=str(template_id or "")[:40],
        start=float(window[0]),
        end=float(window[1]),
        x=max(-1.0, min(1.0, float(x))),
        y=max(-1.0, min(1.0, float(y))),
        scale=max(0.2, min(4.0, float(scale))),
    )
