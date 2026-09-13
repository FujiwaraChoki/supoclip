"""
Caption template definitions for animated subtitles.
Each template defines styling and animation properties for different caption styles.

Newer styling fields (all optional, read with .get() in the renderer so older
templates keep working):
  - word_pop:           scale the active karaoke word up with a spring (bool)
  - emoji:              inject contextual emojis next to keywords (bool)
  - emphasis_color:     colour for emphasised power/keyword words (hex or None)
  - word_box:           draw a filled "pill" behind the active word (bool)
  - word_box_color:     colour of that pill (hex, supports 8-digit alpha)
  - uppercase:          force caption text to uppercase (bool)
  - max_words_per_line: words shown per caption line (int)
  - glow:               soft neon glow on the text outline (bool)
"""

from typing import Dict, Any, Literal

AnimationType = Literal["none", "karaoke", "pop", "fade", "bounce"]

CAPTION_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "default": {
        "name": "Default",
        "description": "Punchy word-by-word captions with a pop highlight and emojis",
        "font_family": "THEBOLDFONT",
        "font_size": 32,
        "font_color": "#FFFFFF",
        "highlight_color": "#FFE000",  # Bright yellow for the active word
        "emphasis_color": "#FFE000",  # Power/keyword words pop in the same accent
        "stroke_color": "#000000",
        "stroke_width": 3,
        "background": False,
        "background_color": None,
        "word_box": False,
        "word_box_color": None,
        "animation": "karaoke",
        "word_pop": True,
        "emoji": True,
        "uppercase": False,
        "shadow": True,
        "glow": False,
        "max_words_per_line": 4,
        "position_y": 0.80,
    },
    "hormozi": {
        "name": "Hormozi",
        "description": "Bold green highlight with a pill behind the active word",
        "font_family": "THEBOLDFONT",
        "font_size": 38,
        "font_color": "#FFFFFF",
        "highlight_color": "#00FF66",  # Bright green
        "emphasis_color": "#FFE000",
        "stroke_color": "#000000",
        "stroke_width": 4,
        "background": False,
        "background_color": None,
        "word_box": True,
        "word_box_color": "#00BF49",  # Green pill behind active word
        "animation": "karaoke",
        "word_pop": True,
        "emoji": True,
        "uppercase": True,
        "shadow": True,
        "glow": False,
        "max_words_per_line": 3,
        "position_y": 0.74,
    },
    "mrbeast": {
        "name": "MrBeast",
        "description": "Large yellow text with red pop highlights and emojis",
        "font_family": "THEBOLDFONT",
        "font_size": 42,
        "font_color": "#FFFF00",  # Yellow
        "highlight_color": "#FF2D2D",  # Red
        "emphasis_color": "#FFFFFF",
        "stroke_color": "#000000",
        "stroke_width": 5,
        "background": False,
        "background_color": None,
        "word_box": False,
        "word_box_color": None,
        "animation": "karaoke",
        "word_pop": True,
        "emoji": True,
        "uppercase": True,
        "shadow": True,
        "glow": False,
        "max_words_per_line": 3,
        "position_y": 0.70,
    },
    "minimal": {
        "name": "Minimal",
        "description": "Clean, subtle captions with a soft background",
        "font_family": "TikTokSans-Regular",
        "font_size": 26,
        "font_color": "#FFFFFF",
        "highlight_color": "#FFFFFF",
        "emphasis_color": None,
        "stroke_color": None,
        "stroke_width": 0,
        "background": True,
        "background_color": "#00000080",  # 50% transparent black
        "word_box": False,
        "word_box_color": None,
        "animation": "fade",
        "word_pop": False,
        "emoji": False,
        "uppercase": False,
        "shadow": False,
        "glow": False,
        "max_words_per_line": 6,
        "position_y": 0.82,
    },
    "tiktok": {
        "name": "TikTok",
        "description": "TikTok-style with pink pop highlights",
        "font_family": "TikTokSans-Regular",
        "font_size": 34,
        "font_color": "#FFFFFF",
        "highlight_color": "#FE2C55",  # TikTok pink
        "emphasis_color": "#FE2C55",
        "stroke_color": "#000000",
        "stroke_width": 3,
        "background": False,
        "background_color": None,
        "word_box": False,
        "word_box_color": None,
        "animation": "karaoke",
        "word_pop": True,
        "emoji": True,
        "uppercase": False,
        "shadow": True,
        "glow": False,
        "max_words_per_line": 4,
        "position_y": 0.78,
    },
    "neon": {
        "name": "Neon",
        "description": "Glowing neon text with magenta highlights",
        "font_family": "THEBOLDFONT",
        "font_size": 36,
        "font_color": "#00FFFF",  # Cyan
        "highlight_color": "#FF00FF",  # Magenta
        "emphasis_color": "#FF00FF",
        "stroke_color": "#002A6B",  # Dark blue
        "stroke_width": 2,
        "background": False,
        "background_color": None,
        "word_box": False,
        "word_box_color": None,
        "animation": "karaoke",
        "word_pop": True,
        "emoji": False,
        "uppercase": False,
        "shadow": False,
        "glow": True,
        "max_words_per_line": 4,
        "position_y": 0.76,
    },
    "podcast": {
        "name": "Podcast",
        "description": "Professional podcast-style captions",
        "font_family": "TikTokSans-Regular",
        "font_size": 28,
        "font_color": "#FFFFFF",
        "highlight_color": "#FFB800",  # Warm gold
        "emphasis_color": "#FFB800",
        "stroke_color": "#1A1A1A",
        "stroke_width": 2,
        "background": True,
        "background_color": "#1A1A1ACC",  # Dark semi-transparent
        "word_box": False,
        "word_box_color": None,
        "animation": "karaoke",
        "word_pop": False,
        "emoji": False,
        "uppercase": False,
        "shadow": False,
        "glow": False,
        "max_words_per_line": 5,
        "position_y": 0.80,
    },
}


# Defaults for any styling key a template might omit, so the renderer can rely
# on get_template(...).get(key, ...) without scattering fallbacks everywhere.
TEMPLATE_DEFAULTS: Dict[str, Any] = {
    "highlight_color": "#FFE000",
    "emphasis_color": None,
    "stroke_color": "#000000",
    "stroke_width": 3,
    "background": False,
    "background_color": None,
    "word_box": False,
    "word_box_color": None,
    "animation": "karaoke",
    "word_pop": True,
    "emoji": True,
    "uppercase": False,
    "shadow": True,
    "glow": False,
    "max_words_per_line": 4,
    "position_y": 0.80,
    # Hook title (the AI-written headline burned in for the first few seconds).
    # None means "inherit the caption styling above"; the renderer resolves that.
    "hook_font_family": None,
    "hook_font_size_scale": 0.82,
    "hook_font_color": None,
    "hook_background_color": None,
    "hook_stroke_color": None,
    "hook_stroke_width": None,
    "hook_position": "top",
    "hook_duration_seconds": 4.0,
    "hook_animation": "fade_pop",
    "hook_shadow": None,
    "hook_highlight_color": "#FFE000",
    "hook_sfx": None,
}

HOOK_POSITIONS = ("top", "center", "bottom")
HOOK_ANIMATIONS = ("fade_pop", "fade", "slide_down", "zoom_punch", "bounce", "pulse", "none")

# Content-type classification for a clip's hook (AI-assigned by default, but
# user-selectable/overridable in the hook comparison UI). Kept here as the
# single source of truth so the API and frontend labels stay in sync.
HOOK_TYPES = (
    "question",
    "statement",
    "statistic",
    "story",
    "contrast",
    "callout",
    "warning",
    "none",
)

HOOK_ANIMATION_INFO = {
    "fade_pop": "Fade in with a soft scale pop",
    "fade": "Simple fade in",
    "slide_down": "Unsquashes into place from the top",
    "zoom_punch": "Fades in then punches up in scale",
    "bounce": "Overshoots on entry and settles with a bounce",
    "pulse": "Fades in with a gentle breathing pulse",
    "none": "No animation, appears instantly",
}

HOOK_TYPE_LABELS = {
    "question": "Question Hook",
    "statement": "Bold Statement",
    "statistic": "Data/Stats",
    "story": "Story Hook",
    "contrast": "Contrast Hook",
    "callout": "Callout",
    "warning": "Warning",
    "none": "No Hook",
}


def get_hook_options() -> Dict[str, Any]:
    """Hook types/animations for the frontend hook picker (mirrors get_template_info)."""
    return {
        "hook_types": [
            {"id": hook_type, "label": HOOK_TYPE_LABELS.get(hook_type, hook_type)}
            for hook_type in HOOK_TYPES
        ],
        "hook_animations": [
            {"id": animation, "label": HOOK_ANIMATION_INFO.get(animation, animation)}
            for animation in HOOK_ANIMATIONS
        ],
        "hook_positions": list(HOOK_POSITIONS),
    }


def get_template(template_name: str) -> Dict[str, Any]:
    """Get a caption template by name, returns default if not found."""
    template = CAPTION_TEMPLATES.get(template_name, CAPTION_TEMPLATES["default"])
    merged = dict(TEMPLATE_DEFAULTS)
    merged.update(template)
    return merged


def get_all_templates() -> Dict[str, Dict[str, Any]]:
    """Get all available caption templates."""
    return CAPTION_TEMPLATES


def get_template_names() -> list:
    """Get list of all template names."""
    return list(CAPTION_TEMPLATES.keys())


def get_template_info() -> list:
    """Get list of template info for API response (used to render the template picker grid)."""
    return [
        {
            "id": name,
            "name": template["name"],
            "description": template["description"],
            "animation": template["animation"],
            "font_family": template["font_family"],
            "font_size": template["font_size"],
            "font_color": template["font_color"],
            "highlight_color": template["highlight_color"],
            "word_box": merged["word_box"],
            "word_box_color": merged["word_box_color"],
            "uppercase": merged["uppercase"],
            "stroke_color": merged["stroke_color"],
            "background_color": merged["background_color"],
            "glow": merged["glow"],
        }
        for name, template in CAPTION_TEMPLATES.items()
        for merged in [get_template(name)]
    ]
