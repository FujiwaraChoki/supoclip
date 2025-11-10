"""
Canvas rendering for 9:16 vertical format with 3 styles.

Styles:
1. original - Horizontal as-is (centered on 9:16 canvas)
2. flipped - Horizontal mirror (centered on 9:16 canvas)
3. blurry_bg - Blurred background + 40% foreground
"""
import logging
import cv2
import numpy as np
from pathlib import Path
from typing import Tuple, Optional

logger = logging.getLogger(__name__)


class CanvasRenderer:
    """Render horizontal clips on 9:16 vertical canvas with different styles."""

    def __init__(self, output_width: int = 1080, output_height: int = 1920):
        """
        Initialize canvas renderer.

        Args:
            output_width: Output video width (default 1080 for 9:16)
            output_height: Output video height (default 1920 for 9:16)
        """
        self.output_width = output_width
        self.output_height = output_height
        self.target_ratio = output_width / output_height  # 9/16

        logger.info(f"Canvas renderer initialized: {output_width}x{output_height} (9:16)")

    def render_original_style(
        self,
        input_path: str,
        output_path: str
    ) -> bool:
        """
        Render original style - horizontal centered on 9:16 canvas.

        Args:
            input_path: Input video path
            output_path: Output video path

        Returns:
            True if successful
        """
        logger.info(f"Rendering original style: {input_path}")

        cap = cv2.VideoCapture(input_path)

        if not cap.isOpened():
            logger.error(f"Failed to open video: {input_path}")
            return False

        # Get video properties
        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        # Setup writer
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (self.output_width, self.output_height))

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            # Create canvas
            canvas = self._create_canvas_frame(frame, style='original')
            writer.write(canvas)

            frame_idx += 1
            if frame_idx % 100 == 0:
                logger.debug(f"Processed {frame_idx}/{total_frames} frames")

        cap.release()
        writer.release()

        logger.info(f"✅ Original style rendered: {output_path}")
        return True

    def render_flipped_style(
        self,
        input_path: str,
        output_path: str
    ) -> bool:
        """
        Render flipped style - horizontal mirror centered on 9:16 canvas.

        Args:
            input_path: Input video path
            output_path: Output video path

        Returns:
            True if successful
        """
        logger.info(f"Rendering flipped style: {input_path}")

        cap = cv2.VideoCapture(input_path)

        if not cap.isOpened():
            logger.error(f"Failed to open video: {input_path}")
            return False

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (self.output_width, self.output_height))

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            canvas = self._create_canvas_frame(frame, style='flipped')
            writer.write(canvas)

            frame_idx += 1
            if frame_idx % 100 == 0:
                logger.debug(f"Processed {frame_idx}/{total_frames} frames")

        cap.release()
        writer.release()

        logger.info(f"✅ Flipped style rendered: {output_path}")
        return True

    def render_blurry_bg_style(
        self,
        input_path: str,
        output_path: str
    ) -> bool:
        """
        Render blurry_bg style - blurred background + 40% foreground centered.

        Args:
            input_path: Input video path
            output_path: Output video path

        Returns:
            True if successful
        """
        logger.info(f"Rendering blurry_bg style: {input_path}")

        cap = cv2.VideoCapture(input_path)

        if not cap.isOpened():
            logger.error(f"Failed to open video: {input_path}")
            return False

        fps = cap.get(cv2.CAP_PROP_FPS)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        writer = cv2.VideoWriter(output_path, fourcc, fps, (self.output_width, self.output_height))

        frame_idx = 0
        while True:
            ret, frame = cap.read()
            if not ret:
                break

            canvas = self._create_canvas_frame(frame, style='blurry_bg')
            writer.write(canvas)

            frame_idx += 1
            if frame_idx % 100 == 0:
                logger.debug(f"Processed {frame_idx}/{total_frames} frames")

        cap.release()
        writer.release()

        logger.info(f"✅ Blurry BG style rendered: {output_path}")
        return True

    def _create_canvas_frame(self, frame: np.ndarray, style: str) -> np.ndarray:
        """
        Create a single canvas frame with specified style.

        Args:
            frame: Input frame (horizontal)
            style: 'original', 'flipped', or 'blurry_bg'

        Returns:
            Canvas frame (9:16 vertical)
        """
        frame_height, frame_width = frame.shape[:2]

        # Create black canvas
        canvas = np.zeros((self.output_height, self.output_width, 3), dtype=np.uint8)

        if style == 'original':
            # Center horizontal frame on canvas
            resized = self._fit_to_canvas(frame)
            y_offset, x_offset = self._calculate_center_offset(resized)
            canvas[y_offset:y_offset + resized.shape[0], x_offset:x_offset + resized.shape[1]] = resized

        elif style == 'flipped':
            # Flip horizontal then center
            flipped = cv2.flip(frame, 1)  # 1 = horizontal flip
            resized = self._fit_to_canvas(flipped)
            y_offset, x_offset = self._calculate_center_offset(resized)
            canvas[y_offset:y_offset + resized.shape[0], x_offset:x_offset + resized.shape[1]] = resized

        elif style == 'blurry_bg':
            # Background: full frame stretched to 9:16 with blur
            bg = cv2.resize(frame, (self.output_width, self.output_height))
            bg = cv2.GaussianBlur(bg, (51, 51), 50)  # 50% blur approximation
            canvas = bg

            # Foreground: 40% scale centered
            fg_width = int(frame_width * 0.4)
            fg_height = int(frame_height * 0.4)
            fg = cv2.resize(frame, (fg_width, fg_height))

            # Center foreground
            y_offset = (self.output_height - fg_height) // 2
            x_offset = (self.output_width - fg_width) // 2

            # Overlay foreground
            canvas[y_offset:y_offset + fg_height, x_offset:x_offset + fg_width] = fg

        return canvas

    def _fit_to_canvas(self, frame: np.ndarray) -> np.ndarray:
        """
        Fit horizontal frame to canvas width while maintaining aspect ratio.

        Args:
            frame: Input frame

        Returns:
            Resized frame
        """
        frame_height, frame_width = frame.shape[:2]

        # Scale to fit canvas width
        scale = self.output_width / frame_width
        new_width = self.output_width
        new_height = int(frame_height * scale)

        # Don't exceed canvas height
        if new_height > self.output_height:
            scale = self.output_height / frame_height
            new_height = self.output_height
            new_width = int(frame_width * scale)

        resized = cv2.resize(frame, (new_width, new_height))
        return resized

    def _calculate_center_offset(self, frame: np.ndarray) -> Tuple[int, int]:
        """
        Calculate offset to center frame on canvas.

        Args:
            frame: Frame to center

        Returns:
            (y_offset, x_offset)
        """
        frame_height, frame_width = frame.shape[:2]

        y_offset = (self.output_height - frame_height) // 2
        x_offset = (self.output_width - frame_width) // 2

        return (y_offset, x_offset)
