"""
Watermark overlay system using green screen videos.

Each account can have custom watermark videos that are composited onto clips.
"""
import logging
import asyncio
import os
from pathlib import Path
from typing import Optional, Dict, Any
import json

logger = logging.getLogger(__name__)


class WatermarkOverlay:
    """
    Watermark overlay system for video clips.

    Uses FFmpeg to composite green screen watermark videos onto clips.
    Supports per-account watermark customization.
    """

    def __init__(self, watermark_dir: str = "/app/watermarks"):
        """
        Initialize watermark overlay system.

        Args:
            watermark_dir: Directory containing watermark videos
        """
        self.watermark_dir = watermark_dir
        os.makedirs(watermark_dir, exist_ok=True)
        logger.info(f"Watermark overlay initialized with dir: {watermark_dir}")

    async def apply_watermark(
        self,
        input_path: str,
        output_path: str,
        watermark_path: str,
        position: str = "bottom_right",
        scale: float = 0.15,
        opacity: float = 1.0
    ) -> bool:
        """
        Apply watermark overlay to video using FFmpeg.

        Uses chromakey (green screen removal) to composite watermark.

        Args:
            input_path: Source video file
            output_path: Output video with watermark
            watermark_path: Path to watermark video (green screen)
            position: Where to place watermark (top_left, top_right, bottom_left, bottom_right, center)
            scale: Scale of watermark relative to video width (0.1 = 10% of width)
            opacity: Watermark opacity 0-1

        Returns:
            True if successful, False otherwise
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Calculate position overlay string
        position_map = {
            "top_left": "10:10",
            "top_right": "main_w-overlay_w-10:10",
            "bottom_left": "10:main_h-overlay_h-10",
            "bottom_right": "main_w-overlay_w-10:main_h-overlay_h-10",
            "center": "(main_w-overlay_w)/2:(main_h-overlay_h)/2"
        }

        pos_str = position_map.get(position, position_map["bottom_right"])

        # FFmpeg filter complex for green screen removal + overlay
        # 1. Scale watermark to percentage of main video width
        # 2. Remove green screen using chromakey
        # 3. Apply opacity
        # 4. Overlay at position
        filter_complex = (
            f"[1:v]scale=iw*{scale}:-1[wm_scaled];"
            f"[wm_scaled]chromakey=0x00FF00:0.1:0.2[wm_keyed];"
            f"[wm_keyed]format=yuva420p,colorchannelmixer=aa={opacity}[wm_opacity];"
            f"[0:v][wm_opacity]overlay={pos_str}[outv]"
        )

        cmd = [
            'ffmpeg',
            '-i', input_path,           # Main video
            '-i', watermark_path,       # Watermark video
            '-filter_complex', filter_complex,
            '-map', '[outv]',           # Map filtered video
            '-map', '0:a',              # Map original audio
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-c:a', 'copy',             # Copy audio (no re-encode)
            '-movflags', '+faststart',
            '-y',
            output_path
        ]

        logger.debug(f"FFmpeg watermark command: {' '.join(cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"FFmpeg watermark failed with code {process.returncode}")
                logger.error(f"stderr: {stderr.decode()[:500]}")
                return False

            logger.info(f"✅ Watermark applied: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error applying watermark: {e}", exc_info=True)
            return False

    async def batch_apply_watermarks(
        self,
        clips: list[Dict[str, str]],
        watermark_path: str,
        output_dir: str,
        position: str = "bottom_right",
        scale: float = 0.15,
        progress_callback=None
    ) -> list[str]:
        """
        Apply watermark to multiple clips in batch.

        Args:
            clips: List of dicts with 'input_path' and 'filename'
            watermark_path: Path to watermark video
            output_dir: Output directory
            position: Watermark position
            scale: Watermark scale
            progress_callback: Optional async function(current, total)

        Returns:
            List of output file paths
        """
        os.makedirs(output_dir, exist_ok=True)

        output_paths = []
        total = len(clips)

        for i, clip in enumerate(clips):
            input_path = clip['input_path']
            output_filename = f"watermarked_{clip['filename']}"
            output_path = os.path.join(output_dir, output_filename)

            success = await self.apply_watermark(
                input_path,
                output_path,
                watermark_path,
                position,
                scale
            )

            if success:
                output_paths.append(output_path)

            if progress_callback:
                await progress_callback(i + 1, total)

            if (i + 1) % 10 == 0 or i == total - 1:
                logger.info(f"Watermarked {i + 1}/{total} clips")

        logger.info(f"✅ Batch watermarking complete: {len(output_paths)}/{total} clips")
        return output_paths

    def get_watermark_for_account(self, account_id: str) -> Optional[str]:
        """
        Get watermark video path for specific account.

        Looks for {account_id}.mp4 in watermark directory.
        Falls back to default.mp4 if account-specific not found.

        Args:
            account_id: Account/user ID

        Returns:
            Path to watermark video, or None if not found
        """
        # Try account-specific watermark
        account_watermark = os.path.join(self.watermark_dir, f"{account_id}.mp4")
        if os.path.exists(account_watermark):
            logger.info(f"Using account watermark: {account_watermark}")
            return account_watermark

        # Fall back to default
        default_watermark = os.path.join(self.watermark_dir, "default.mp4")
        if os.path.exists(default_watermark):
            logger.info(f"Using default watermark: {default_watermark}")
            return default_watermark

        logger.warning(f"No watermark found for account {account_id}")
        return None

    def save_watermark_metadata(
        self,
        account_id: str,
        metadata: Dict[str, Any],
        metadata_file: Optional[str] = None
    ):
        """
        Save watermark metadata (position, scale, etc.) for account.

        Args:
            account_id: Account ID
            metadata: Dict with position, scale, opacity settings
            metadata_file: Path to metadata JSON (defaults to watermark_dir/metadata.json)
        """
        if metadata_file is None:
            metadata_file = os.path.join(self.watermark_dir, "metadata.json")

        # Load existing metadata
        if os.path.exists(metadata_file):
            with open(metadata_file, 'r') as f:
                all_metadata = json.load(f)
        else:
            all_metadata = {}

        # Update account metadata
        all_metadata[account_id] = metadata

        # Save
        with open(metadata_file, 'w') as f:
            json.dump(all_metadata, f, indent=2)

        logger.info(f"Saved watermark metadata for account {account_id}")

    def load_watermark_metadata(
        self,
        account_id: str,
        metadata_file: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Load watermark metadata for account.

        Args:
            account_id: Account ID
            metadata_file: Path to metadata JSON

        Returns:
            Dict with position, scale, opacity settings (defaults if not found)
        """
        if metadata_file is None:
            metadata_file = os.path.join(self.watermark_dir, "metadata.json")

        # Default settings
        defaults = {
            "position": "bottom_right",
            "scale": 0.15,
            "opacity": 1.0
        }

        if not os.path.exists(metadata_file):
            return defaults

        try:
            with open(metadata_file, 'r') as f:
                all_metadata = json.load(f)

            return all_metadata.get(account_id, defaults)

        except Exception as e:
            logger.error(f"Error loading watermark metadata: {e}")
            return defaults
