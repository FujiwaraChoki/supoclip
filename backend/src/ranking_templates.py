"""
Ranking template registry — data-driven, folder-based (per CLAUDE.md's
"Multi-Tool Platform Shell" conventions), unlike caption_templates.py's
hardcoded dict. Each folder under `backend/templates/ranking/` is one
template: `config.json` (layout/transition/number-overlay defaults) plus a
`preview.svg`/`preview.png` thumbnail for the picker. Adding a template means
dropping a new folder — no code change.

"rapid_fire", "countdown", and "ranking_list" ship today — see
docs/architecture.md's "Multi-Tool Platform & Ranking Tool" section for how
`layout`/`render_order`/`list_overlay` map onto RankingService's render
dispatch. `head_to_head` and `tier_grid` remain deferred: both need a
structurally different ffmpeg filter graph (split-screen / grid stacking)
plus new `ranking_inputs` data (pairing, a tier column) rather than just a
new config.json.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "ranking"

# Applied for any config.json key a template omits.
TEMPLATE_DEFAULTS: Dict[str, Any] = {
    # "full_screen" is the only layout RankingService currently renders;
    # "head_to_head"/"tier_grid" are reserved for the deferred templates.
    "layout": "full_screen",
    # "ascending" plays rank #1 first (Rapid Fire); "descending" plays worst
    # first, building suspense up to #1 (Countdown, Ranking List).
    "render_order": "ascending",
    # Accumulating corner list of ranks revealed so far, in render order —
    # the Ranking List template's differentiator from Countdown.
    "list_overlay": False,
    "transition": "cut",
    "transition_duration_seconds": 0.3,
    # An sfx filename from backend/sfx/ (find_sfx_path's SFX_DIR), mixed in
    # at every cut boundary — None plays silent hard cuts, the only
    # supported visual transition today (see docs/architecture.md's
    # Ranking tool section on why a real crossfade stays deferred).
    "transition_sfx": None,
    # A filename from backend/music/ (find_music_path's MUSIC_DIR) looped to
    # the compilation's length and auto-ducked under each clip's own audio.
    # None ships with every template today — see backend/music/README.md
    # for why no track is bundled.
    "background_music": None,
    "background_music_volume": 0.22,
    # When True (only "ranking_classic" today), transition SFX comes from the
    # user's Settings -> Ranking upload (Config.ranking_sfx_filename) instead
    # of `transition_sfx`, applies the configured offset-before-cut, and adds
    # one extra end-aligned instance so the SFX's tail lands exactly at video
    # end — the decorative templates' curated `transition_sfx` keeps its
    # simpler fixed-at-the-cut, boundaries-only behavior unchanged.
    "use_global_sfx": False,
    "number_overlay": {
        "position": "bottom",
        "color": "teal",
        "animation": "fade_pop",
        # "tile": the original single current-segment tile (rapid_fire,
        # countdown, ranking_list). "stacked": all ranks stacked on the left
        # for the whole video, text reveals per rank and persists, #1 gold —
        # see ranking_overlay.py::build_ranking_overlay_ass.
        "style": "tile",
    },
    "intro": None,
    "outro": None,
    "caption_placement": None,
}


def _load_config(folder: Path) -> Optional[Dict[str, Any]]:
    config_path = folder / "config.json"
    if not config_path.exists():
        return None
    try:
        raw = json.loads(config_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("ranking_templates: failed to load %s: %s", config_path, exc)
        return None

    merged = dict(TEMPLATE_DEFAULTS)
    merged.update(raw)
    merged["id"] = folder.name
    preview = folder / "preview.svg"
    if not preview.exists():
        preview = folder / "preview.png"
    merged["preview_path"] = str(preview) if preview.exists() else None
    return merged


def get_all_templates() -> Dict[str, Dict[str, Any]]:
    """Every ranking template folder, keyed by folder name (= template id)."""
    if not TEMPLATES_DIR.exists():
        return {}
    templates: Dict[str, Dict[str, Any]] = {}
    for folder in sorted(TEMPLATES_DIR.iterdir()):
        if not folder.is_dir():
            continue
        config = _load_config(folder)
        if config is not None:
            templates[folder.name] = config
    return templates


def get_template_preview_path(template_id: str) -> Optional[Path]:
    """Resolve a template's on-disk preview file for GET
    /ranking/templates/{id}/preview — a strict lookup (no fallback-to-first-
    template) so an unknown id 404s instead of serving the wrong preview."""
    folder = TEMPLATES_DIR / template_id
    if not folder.is_dir():
        return None
    preview = folder / "preview.svg"
    if preview.exists():
        return preview
    preview = folder / "preview.png"
    return preview if preview.exists() else None


def get_template(template_id: str) -> Dict[str, Any]:
    """Get a ranking template by id, falling back to the first available
    template (there is always at least "rapid_fire")."""
    templates = get_all_templates()
    if template_id in templates:
        return templates[template_id]
    if templates:
        return next(iter(templates.values()))
    # No template folders on disk at all — return bare defaults so callers
    # never crash on a fresh checkout missing the templates/ directory.
    return dict(TEMPLATE_DEFAULTS, id="rapid_fire", preview_path=None)


def get_template_info() -> List[Dict[str, Any]]:
    """List shape for the frontend's template picker. `preview_url` (not the
    raw filesystem `preview_path`) points at GET /ranking/templates/{id}/preview
    — never leak the on-disk path to the client."""
    return [
        {
            "id": template_id,
            "name": template.get("name", template_id),
            "description": template.get("description", ""),
            "transition": template["transition"],
            "render_order": template["render_order"],
            "list_overlay": template["list_overlay"],
            "number_overlay": template["number_overlay"],
            "preview_url": f"/ranking/templates/{template_id}/preview"
            if template["preview_path"]
            else None,
        }
        for template_id, template in get_all_templates().items()
    ]
