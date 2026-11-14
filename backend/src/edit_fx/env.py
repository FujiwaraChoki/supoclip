"""Every environment read for dynamic edits. Nothing else reads these variables (§5.7).

Each reader is evaluated on call (no import-time caching), so tests and the admin runtime settings see changes.
Unknown values fall back to the default rather than raising: a typo in a deploy must not stop clips rendering.
"""

from __future__ import annotations

import math
import os
from pathlib import Path

DEFAULT_EDIT_PLAN_TIMEOUT_SECONDS = 45.0
DEFAULT_EDIT_PLAN_DAILY_LIMIT = 100
MIN_FX_RENDER_TIMEOUT_SECONDS = 900.0
FX_RENDER_SECONDS_PER_CLIP_SECOND = 6.0
# backend/fx_library in a checkout, /app/fx_library in the image (this file is /app/src/edit_fx/env.py there).
_DEFAULT_FX_LIBRARY_DIR = Path(__file__).resolve().parents[2] / "fx_library"


def _text(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _choice(name: str, choices: tuple[str, ...], default: str) -> str:
    value = _text(name).lower()
    return value if value in choices else default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(_text(name))
    except ValueError:
        return default
    return value if math.isfinite(value) and value > 0 else default


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(_text(name))
    except ValueError:
        return default
    return value if value > 0 else default


def edit_plan_model() -> str:
    """``EDIT_PLAN_LLM``, else the configured ``LLM`` (``provider:model``)."""
    explicit = _text("EDIT_PLAN_LLM")
    if explicit:
        return explicit
    from ..config import get_config

    return get_config().llm


def edit_plan_timeout() -> float:
    return _positive_float("EDIT_PLAN_TIMEOUT_SECONDS", DEFAULT_EDIT_PLAN_TIMEOUT_SECONDS)


def edit_plan_daily_limit() -> int:
    return _positive_int("EDIT_PLAN_DAILY_LIMIT", DEFAULT_EDIT_PLAN_DAILY_LIMIT)


def fx_runtime() -> str:
    """``deno`` (default) or ``off``, the kill switch for the P2 drawing stage."""
    return _choice("FX_RUNTIME", ("deno", "off"), "deno")


def fx_deno_bin() -> str:
    return _text("FX_DENO_BIN") or "deno"


def fx_warp_enabled() -> bool:
    return _choice("FX_WARP", ("on", "off"), "on") == "on"


def fx_render_timeout(duration: float) -> float:
    """``FX_RENDER_TIMEOUT_SECONDS``, else ``max(900, 6 * duration)``."""
    try:
        clip_seconds = max(0.0, float(duration))
    except (TypeError, ValueError):
        clip_seconds = 0.0
    if not math.isfinite(clip_seconds):
        clip_seconds = 0.0
    default = max(MIN_FX_RENDER_TIMEOUT_SECONDS, FX_RENDER_SECONDS_PER_CLIP_SECOND * clip_seconds)
    return _positive_float("FX_RENDER_TIMEOUT_SECONDS", default)


def fx_library_dir() -> Path:
    """Drop-in SFX and music (``FX_LIBRARY_DIR``). Every render machine must mount the same library."""
    explicit = _text("FX_LIBRARY_DIR")
    return Path(explicit) if explicit else _DEFAULT_FX_LIBRARY_DIR


def fx_loudnorm_mode() -> str:
    """``static`` (default, §9) or ``dynamic`` (the legacy one-pass loudnorm escape hatch)."""
    return _choice("FX_LOUDNORM", ("static", "dynamic"), "static")


def reactions_provider() -> str:
    return _choice("REACTIONS_PROVIDER", ("off", "giphy"), "off")


def giphy_api_key() -> str:
    return _text("GIPHY_API_KEY")


def fx_chain_mode() -> str:
    """``pipe`` (default) or ``file`` (P2 writes a qtrle .mov that P3 reads)."""
    return _choice("FX_CHAIN_MODE", ("pipe", "file"), "pipe")
