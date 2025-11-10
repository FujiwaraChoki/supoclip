"""
Title card generators for TT³ and AdLab Standard styles.

TT³ (TikTok style): White text on black rounded rectangle at top
AdLab Standard: Text overlay at bottom with background
"""
import logging
import asyncio
import os
from pathlib import Path
from typing import Optional, Literal
from PIL import Image, ImageDraw, ImageFont
import cv2
import numpy as np

logger = logging.getLogger(__name__)


TitleStyle = Literal["tt3", "adlab_standard"]


class TitleCardGenerator:
    """
    Generate title cards for video clips.

    Supports two styles:
    - TT³: Bubble top style (white text on black rounded rectangle)
    - AdLab Standard: Bottom overlay style
    """

    def __init__(self, font_dir: str = "/app/fonts"):
        """
        Initialize title card generator.

        Args:
            font_dir: Directory containing font files
        """
        self.font_dir = font_dir
        logger.info(f"Title card generator initialized with font dir: {font_dir}")

    async def add_title_card(
        self,
        input_path: str,
        output_path: str,
        title_text: str,
        style: TitleStyle = "tt3",
        font_name: str = "ProximaNova-Bold.ttf",
        duration: float = 3.0
    ) -> bool:
        """
        Add title card to beginning of video.

        Args:
            input_path: Source video file
            output_path: Output video with title card
            title_text: Title text to display
            style: Title style ("tt3" or "adlab_standard")
            font_name: Font file name
            duration: Duration of title card in seconds

        Returns:
            True if successful, False otherwise
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        try:
            # Get video dimensions
            cap = cv2.VideoCapture(input_path)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            cap.release()

            # Generate title card image
            if style == "tt3":
                title_card_path = await self._generate_tt3_card(
                    title_text,
                    width,
                    height,
                    font_name
                )
            else:  # adlab_standard
                title_card_path = await self._generate_adlab_card(
                    title_text,
                    width,
                    height,
                    font_name
                )

            # Create video from title card image
            title_video_path = title_card_path.replace('.png', '.mp4')
            await self._image_to_video(title_card_path, title_video_path, duration, fps)

            # Concatenate title card video with main video
            success = await self._concatenate_videos(
                [title_video_path, input_path],
                output_path
            )

            # Cleanup temporary files
            if os.path.exists(title_card_path):
                os.remove(title_card_path)
            if os.path.exists(title_video_path):
                os.remove(title_video_path)

            if success:
                logger.info(f"✅ Title card added: {output_path}")
                return True
            else:
                return False

        except Exception as e:
            logger.error(f"Error adding title card: {e}", exc_info=True)
            return False

    async def add_title_overlay(
        self,
        input_path: str,
        output_path: str,
        title_text: str,
        style: TitleStyle = "tt3",
        font_name: str = "ProximaNova-Bold.ttf"
    ) -> bool:
        """
        Add title overlay throughout entire video (no separate card).

        Args:
            input_path: Source video file
            output_path: Output video with title overlay
            title_text: Title text to display
            style: Title style ("tt3" or "adlab_standard")
            font_name: Font file name

        Returns:
            True if successful, False otherwise
        """
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        # Build FFmpeg drawtext filter based on style
        font_path = os.path.join(self.font_dir, font_name)

        if style == "tt3":
            # TT³: White text on black rounded rectangle at top
            # Position: 5% from top, centered
            drawtext_filter = (
                f"drawtext="
                f"fontfile='{font_path}':"
                f"text='{self._escape_text(title_text)}':"
                f"fontsize=48:"
                f"fontcolor=white:"
                f"x=(w-text_w)/2:"  # Centered
                f"y=h*0.05:"  # 5% from top
                f"box=1:"
                f"boxcolor=black@0.7:"
                f"boxborderw=20"
            )
        else:  # adlab_standard
            # AdLab: Text at bottom with semi-transparent background
            # Position: 10% from bottom, centered
            drawtext_filter = (
                f"drawtext="
                f"fontfile='{font_path}':"
                f"text='{self._escape_text(title_text)}':"
                f"fontsize=56:"
                f"fontcolor=white:"
                f"x=(w-text_w)/2:"  # Centered
                f"y=h*0.85:"  # 85% down (15% from bottom)
                f"box=1:"
                f"boxcolor=black@0.6:"
                f"boxborderw=25"
            )

        cmd = [
            'ffmpeg',
            '-i', input_path,
            '-vf', drawtext_filter,
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-c:a', 'copy',
            '-movflags', '+faststart',
            '-y',
            output_path
        ]

        logger.debug(f"FFmpeg title overlay command: {' '.join(cmd)}")

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                logger.error(f"FFmpeg title overlay failed with code {process.returncode}")
                logger.error(f"stderr: {stderr.decode()[:500]}")
                return False

            logger.info(f"✅ Title overlay added: {output_path}")
            return True

        except Exception as e:
            logger.error(f"Error adding title overlay: {e}", exc_info=True)
            return False

    async def _generate_tt3_card(
        self,
        title_text: str,
        width: int,
        height: int,
        font_name: str
    ) -> str:
        """
        Generate TT³ style title card (bubble top).

        White text on black rounded rectangle at top center.

        Args:
            title_text: Title text
            width: Video width
            height: Video height
            font_name: Font file name

        Returns:
            Path to generated PNG
        """
        # Create black background
        img = Image.new('RGB', (width, height), color='black')
        draw = ImageDraw.Draw(img)

        # Load font
        font_path = os.path.join(self.font_dir, font_name)
        font_size = int(height * 0.06)  # 6% of video height
        try:
            font = ImageFont.truetype(font_path, font_size)
        except:
            logger.warning(f"Font {font_path} not found, using default")
            font = ImageFont.load_default()

        # Calculate text bounding box
        bbox = draw.textbbox((0, 0), title_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Rounded rectangle position (top center)
        padding = int(height * 0.03)  # 3% padding
        rect_width = text_width + padding * 2
        rect_height = text_height + padding * 2
        rect_x = (width - rect_width) // 2
        rect_y = int(height * 0.05)  # 5% from top

        # Draw rounded rectangle
        draw.rounded_rectangle(
            [(rect_x, rect_y), (rect_x + rect_width, rect_y + rect_height)],
            radius=20,
            fill='black',
            outline='white',
            width=3
        )

        # Draw text centered in rectangle
        text_x = rect_x + padding
        text_y = rect_y + padding
        draw.text((text_x, text_y), title_text, font=font, fill='white')

        # Save
        output_path = f"/tmp/title_tt3_{hash(title_text)}.png"
        img.save(output_path)
        logger.debug(f"Generated TT³ title card: {output_path}")

        return output_path

    async def _generate_adlab_card(
        self,
        title_text: str,
        width: int,
        height: int,
        font_name: str
    ) -> str:
        """
        Generate AdLab Standard style title card (bottom overlay).

        Text at bottom with semi-transparent black background.

        Args:
            title_text: Title text
            width: Video width
            height: Video height
            font_name: Font file name

        Returns:
            Path to generated PNG
        """
        # Create transparent background
        img = Image.new('RGBA', (width, height), color=(0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Load font
        font_path = os.path.join(self.font_dir, font_name)
        font_size = int(height * 0.08)  # 8% of video height (larger for AdLab)
        try:
            font = ImageFont.truetype(font_path, font_size)
        except:
            logger.warning(f"Font {font_path} not found, using default")
            font = ImageFont.load_default()

        # Calculate text bounding box
        bbox = draw.textbbox((0, 0), title_text, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Bottom overlay position
        padding = int(height * 0.04)  # 4% padding
        rect_height = text_height + padding * 2
        rect_y = height - rect_height - int(height * 0.1)  # 10% from bottom

        # Draw semi-transparent background bar (full width)
        draw.rectangle(
            [(0, rect_y), (width, rect_y + rect_height)],
            fill=(0, 0, 0, 180)  # Black with 70% opacity
        )

        # Draw text centered
        text_x = (width - text_width) // 2
        text_y = rect_y + padding
        draw.text((text_x, text_y), title_text, font=font, fill='white')

        # Save
        output_path = f"/tmp/title_adlab_{hash(title_text)}.png"
        img.save(output_path)
        logger.debug(f"Generated AdLab title card: {output_path}")

        return output_path

    async def _image_to_video(
        self,
        image_path: str,
        output_path: str,
        duration: float,
        fps: float
    ) -> bool:
        """
        Convert static image to video with specified duration.

        Args:
            image_path: Input image
            output_path: Output video
            duration: Duration in seconds
            fps: Frames per second

        Returns:
            True if successful
        """
        cmd = [
            'ffmpeg',
            '-loop', '1',
            '-i', image_path,
            '-t', str(duration),
            '-r', str(fps),
            '-c:v', 'libx264',
            '-preset', 'medium',
            '-crf', '23',
            '-pix_fmt', 'yuv420p',
            '-movflags', '+faststart',
            '-y',
            output_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            await process.communicate()
            return process.returncode == 0

        except Exception as e:
            logger.error(f"Error converting image to video: {e}")
            return False

    async def _concatenate_videos(
        self,
        video_paths: list[str],
        output_path: str
    ) -> bool:
        """
        Concatenate multiple videos into one.

        Args:
            video_paths: List of video paths to concatenate
            output_path: Output video path

        Returns:
            True if successful
        """
        # Create concat file
        concat_file = f"/tmp/concat_{hash(tuple(video_paths))}.txt"
        with open(concat_file, 'w') as f:
            for path in video_paths:
                f.write(f"file '{path}'\n")

        cmd = [
            'ffmpeg',
            '-f', 'concat',
            '-safe', '0',
            '-i', concat_file,
            '-c', 'copy',
            '-movflags', '+faststart',
            '-y',
            output_path
        ]

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await process.communicate()

            # Cleanup concat file
            if os.path.exists(concat_file):
                os.remove(concat_file)

            if process.returncode != 0:
                logger.error(f"FFmpeg concat failed: {stderr.decode()[:500]}")
                return False

            return True

        except Exception as e:
            logger.error(f"Error concatenating videos: {e}")
            return False

    def _escape_text(self, text: str) -> str:
        """
        Escape text for FFmpeg drawtext filter.

        Args:
            text: Raw text

        Returns:
            Escaped text
        """
        # Escape special characters for FFmpeg
        text = text.replace("'", "'\\\\\\''")
        text = text.replace(":", "\\:")
        return text
