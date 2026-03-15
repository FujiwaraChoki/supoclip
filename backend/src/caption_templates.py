"""
Caption template definitions for animated subtitles.
Each template defines styling and animation properties for different caption styles.
"""

from typing import Dict, Any, Literal

AnimationType = Literal["none", "karaoke", "pop", "fade", "bounce"]

CAPTION_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "default": {
        "name": "Default",
        "description": "Clean white text with black outline",
        "font_family": "THEBOLDFONT",
        "font_size": 28,
        "font_color": "#FFFFFF",
        "highlight_color": "#FFD700",  # Gold for current word in karaoke mode
        "stroke_color": "#000000",
        "stroke_width": 2,
        "background": False,
        "background_color": None,
        "animation": "none",
        "shadow": False,
        "position_y": 0.75,  # 75% down the video
    },
    "hormozi": {
        "name": "Hormozi",
        "description": "Bold green highlights like Alex Hormozi's videos",
        "font_family": "THEBOLDFONT",
        "font_size": 36,
        "font_color": "#FFFFFF",
        "highlight_color": "#00FF00",  # Bright green
        "stroke_color": "#000000",
        "stroke_width": 3,
        "background": True,
        "background_color": "#000000AA",  # Semi-transparent black
        "animation": "karaoke",
        "shadow": True,
        "position_y": 0.75,
    },
    "mrbeast": {
        "name": "MrBeast",
        "description": "Large yellow text with red highlights",
        "font_family": "THEBOLDFONT",
        "font_size": 42,
        "font_color": "#FFFF00",  # Yellow
        "highlight_color": "#FF0000",  # Red
        "stroke_color": "#000000",
        "stroke_width": 4,
        "background": False,
        "background_color": None,
        "animation": "pop",
        "shadow": True,
        "position_y": 0.70,  # Slightly higher
    },
    "minimal": {
        "name": "Minimal",
        "description": "Clean, subtle captions with transparent background",
        "font_family": "TikTokSans-Regular",
        "font_size": 24,
        "font_color": "#FFFFFF",
        "highlight_color": "#CCCCCC",
        "stroke_color": None,
        "stroke_width": 0,
        "background": True,
        "background_color": "#00000080",  # 50% transparent black
        "animation": "fade",
        "shadow": False,
        "position_y": 0.80,
    },
    "tiktok": {
        "name": "TikTok",
        "description": "TikTok-style with pink highlights",
        "font_family": "TikTokSans-Regular",
        "font_size": 32,
        "font_color": "#FFFFFF",
        "highlight_color": "#FE2C55",  # TikTok pink
        "stroke_color": "#000000",
        "stroke_width": 2,
        "background": False,
        "background_color": None,
        "animation": "karaoke",
        "shadow": True,
        "position_y": 0.75,
    },
    "neon": {
        "name": "Neon",
        "description": "Glowing neon effect with cyan highlights",
        "font_family": "THEBOLDFONT",
        "font_size": 34,
        "font_color": "#00FFFF",  # Cyan
        "highlight_color": "#FF00FF",  # Magenta
        "stroke_color": "#000066",  # Dark blue
        "stroke_width": 2,
        "background": False,
        "background_color": None,
        "animation": "karaoke",
        "shadow": True,
        "position_y": 0.75,
    },
    "podcast": {
        "name": "Podcast",
        "description": "Professional podcast-style captions",
        "font_family": "TikTokSans-Regular",
        "font_size": 26,
        "font_color": "#FFFFFF",
        "highlight_color": "#FFB800",  # Warm gold
        "stroke_color": "#333333",
        "stroke_width": 1,
        "background": True,
        "background_color": "#1A1A1ACC",  # Dark semi-transparent
        "animation": "fade",
        "shadow": False,
        "position_y": 0.78,
    },
}


def get_template(template_name: str) -> Dict[str, Any]:
    """Get a caption template by name, returns default if not found."""
    return CAPTION_TEMPLATES.get(template_name, CAPTION_TEMPLATES["default"])


def get_all_templates() -> Dict[str, Dict[str, Any]]:
    """Get all available caption templates."""
    return CAPTION_TEMPLATES


def get_template_names() -> list:
    """Get list of all template names."""
    return list(CAPTION_TEMPLATES.keys())


def get_template_info() -> list:
    """Get list of template info for API response."""
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
        }
        for name, template in CAPTION_TEMPLATES.items()
    ]
