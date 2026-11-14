"""Shared constants and frame geometry for dynamic edits (§11.3), mirrored by fx-core ``constants.ts``.

Geometry is authored in units of ``u = min(W, H) / 1080`` so vertical, square and landscape outputs all work
(AP-H10, AR-M7). The Python copy is used by P1's reaction PiP placement and by tests; both copies are checked
against ``shared/fx/fixtures/geometry`` at 1080x1920, 1080x1080 and 1920x1080.
"""

from __future__ import annotations

TONE_EMOJI: dict[str, str] = {"hype": "🔥", "funny": "😂", "shocked": "😱", "sad": "😢", "angry": "😤",
    "wholesome": "🥹", "suspense": "👀", "awkward": "😅", "smug": "😏", "neutral": "✨"}
LLM_ALLOWED_EMOJI: tuple[str, ...] = ("💰", "💸", "📈", "📉", "🚀", "💯", "🤯", "😭", "🙏", "👏", "💪", "🧠", "❤️",
    "💔", "🎯", "⏰", "🏆", "🥇", "🍕", "☕", "🎉", "🤡", "💀", "👑", "🐐", "⚡", "🌍", "📱", "💻", "🎮", "🎵", "📚",
    "🤝", "🙄", "🤔", "😍", "🥶", "🥵", "🚨", "✅", "❌", "⚠️")
HOOK_BAND = 0.20

# Reaction PiP: the largest 4:3 rectangle inside this box (fractions of W and H), centred, top at REACTION_TOP.
REACTION_BOX_W = 0.70
REACTION_BOX_H = 0.50
REACTION_TOP = 0.20
REACTION_ASPECT = 4 / 3

# Caption safe area (matches media.captions.get_safe_vertical_position at 1080 wide).
CAPTION_SAFE_SIDE = 0.06


def unit(w: float, h: float) -> float:
    """The authoring unit: 1.0 on a 1080-short-side frame."""
    return min(w, h) / 1080


def reaction_rect(w: float, h: float) -> tuple[float, float, float, float]:
    """``(x, y, w, h)`` in px of the reaction GIF inset: the largest 4:3 rectangle inside a ``0.70W x 0.50H`` box,
    centred horizontally, top at ``0.20H``. 1080x1920 gives x 0.15W, w 0.70W, h 0.525W."""
    box_w = REACTION_BOX_W * w
    box_h = REACTION_BOX_H * h
    rect_w = min(box_w, box_h * REACTION_ASPECT)
    rect_h = rect_w / REACTION_ASPECT
    return ((w - rect_w) / 2, REACTION_TOP * h, rect_w, rect_h)


def caption_safe(w: float, h: float) -> dict[str, float]:
    """``side`` is a width fraction; ``top`` and ``bottom`` are px margins (``max(40u, 0.05H)``, ``max(120u, 0.10H)``)."""
    u = unit(w, h)
    return {"side": CAPTION_SAFE_SIDE, "top": max(40 * u, 0.05 * h), "bottom": max(120 * u, 0.10 * h)}
