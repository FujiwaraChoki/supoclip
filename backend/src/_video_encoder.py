"""Video encoder + font resolver helpers extracted from ``video_utils.py``.

Keep this module import-cheap; ``video_utils`` re-exports ``VideoProcessor`` and
``_resolve_font_with_style`` so ``patch("src.video_utils.VideoProcessor")`` in
existing tests continues to work.
"""

from pathlib import Path
from typing import Any, Dict, Optional
import logging

from .font_registry import detect_variants, find_font_path

logger = logging.getLogger(__name__)


def _resolve_font_with_style(
    font_family: str, bold: bool = False, italic: bool = False
) -> Optional[Path]:
    """Resolve a font path, preferring Bold/Italic sibling files when available."""
    base_path = find_font_path(font_family, allow_all_user_fonts=True)
    if not base_path:
        return None

    if not bold and not italic:
        return base_path

    variants = detect_variants(base_path)

    if bold and italic:
        if variants.get("bold_path"):
            return Path(variants["bold_path"])
        if variants.get("italic_path"):
            return Path(variants["italic_path"])
    elif bold and variants.get("bold_path"):
        return Path(variants["bold_path"])
    elif italic and variants.get("italic_path"):
        return Path(variants["italic_path"])

    if bold or italic:
        logger.info(
            f"Font '{font_family}' has no bold/italic variant; using base font "
            f"(bold={bold}, italic={italic})"
        )

    return base_path


class VideoProcessor:
    """Handles video processing operations with optimized settings."""

    def __init__(
        self,
        font_family: str = "THEBOLDFONT",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
        bold: bool = False,
        italic: bool = False,
    ):
        self.font_family = font_family
        self.font_size = font_size
        self.font_color = font_color
        self.bold = bool(bold)
        self.italic = bool(italic)
        resolved_font = _resolve_font_with_style(font_family, bold=self.bold, italic=self.italic)
        if not resolved_font:
            resolved_font = find_font_path("TikTokSans-Regular")
        if not resolved_font:
            resolved_font = find_font_path("THEBOLDFONT")
        self.font_path = str(resolved_font) if resolved_font else ""

    def get_optimal_encoding_settings(
        self, target_quality: str = "high"
    ) -> Dict[str, Any]:
        """Get optimal encoding settings for different quality levels."""
        settings = {
            "high": {
                "codec": "libx264",
                "audio_codec": "aac",
                "audio_bitrate": "256k",
                "preset": "slow",
                "ffmpeg_params": [
                    "-crf",
                    "18",
                    "-pix_fmt",
                    "yuv420p",
                    "-profile:v",
                    "high",
                    "-movflags",
                    "+faststart",
                    "-sws_flags",
                    "lanczos",
                ],
            },
            "medium": {
                "codec": "libx264",
                "audio_codec": "aac",
                "bitrate": "4000k",
                "audio_bitrate": "192k",
                "preset": "fast",
                "ffmpeg_params": ["-crf", "23", "-pix_fmt", "yuv420p"],
            },
        }
        return settings.get(target_quality, settings["high"])
