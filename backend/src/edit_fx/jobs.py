"""What the worker hands the clip renderer when dynamic edits are on (AI-M6, AP-C2). Python only."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from .contracts import EditStyle, is_fx_template


@dataclass(frozen=True)
class FxJob:
    style: EditStyle
    task_id: str
    clip_index: int
    cached_beats: dict | None = None                                  # checkpoint["edit_beats"].get(str(clip_index))
    on_beats: Callable[[int, dict], Awaitable[None]] | None = None    # merges {str(i): beats} into the checkpoint dict


def fx_active(style: EditStyle, template: str | None) -> bool:
    """True when the FX path renders: an active intensity, or an FX caption template on its own ("style only")."""
    return style.active or is_fx_template(template)
