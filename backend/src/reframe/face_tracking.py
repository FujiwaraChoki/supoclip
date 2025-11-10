"""
Face tracking for dynamic auto-reframing.

Tracks faces within horizontal 16:9 video and applies movement tracking
to create dynamic 9:16 vertical reframes.
"""
import logging
import cv2
import numpy as np
from typing import List, Tuple, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class FaceTracker:
    """Track faces in video for auto-reframing."""

    def __init__(self):
        """Initialize face detection models."""
        # Try MediaPipe first (best accuracy)
        try:
            import mediapipe as mp
            self.mp_face_detection = mp.solutions.face_detection
            self.mp_drawing = mp.solutions.drawing_utils
            self.detector = self.mp_face_detection.FaceDetection(
                model_selection=1,  # 1 for full range (better for medium/far shots)
                min_detection_confidence=0.5
            )
            self.method = 'mediapipe'
            logger.info("✅ Using MediaPipe for face detection")
        except ImportError:
            # Fallback to OpenCV Haar cascade
            cascade_path = cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
            self.detector = cv2.CascadeClassifier(cascade_path)
            self.method = 'opencv'
            logger.info("✅ Using OpenCV Haar Cascade for face detection")

    def track_faces_in_video(
        self,
        video_path: str,
        start_time: float,
        end_time: float,
        sample_rate: int = 5
    ) -> List[Tuple[int, int]]:
        """
        Track faces throughout a video segment.

        Args:
            video_path: Path to video file
            start_time: Start time in seconds
            end_time: End time in seconds
            sample_rate: Sample every Nth frame (higher = faster but less smooth)

        Returns:
            List of (x, y) center coordinates for face tracking
        """
        logger.info(f"Tracking faces in {video_path} from {start_time:.2f}s to {end_time:.2f}s")

        cap = cv2.VideoCapture(video_path)

        if not cap.isOpened():
            logger.error(f"Failed to open video: {video_path}")
            return []

        fps = cap.get(cv2.CAP_PROP_FPS)
        start_frame = int(start_time * fps)
        end_frame = int(end_time * fps)

        cap.set(cv2.CAP_PROP_POS_FRAMES, start_frame)

        face_positions = []
        frame_idx = start_frame

        while frame_idx < end_frame:
            ret, frame = cap.read()
            if not ret:
                break

            # Sample every Nth frame
            if (frame_idx - start_frame) % sample_rate == 0:
                face_center = self._detect_face_center(frame)
                if face_center:
                    face_positions.append(face_center)

            frame_idx += 1

        cap.release()

        logger.info(f"Found {len(face_positions)} face positions in {end_frame - start_frame} frames")

        return face_positions

    def _detect_face_center(self, frame: np.ndarray) -> Optional[Tuple[int, int]]:
        """
        Detect primary face in frame and return center coordinate.

        Args:
            frame: Video frame (numpy array)

        Returns:
            (x, y) center of detected face, or None if no face found
        """
        height, width = frame.shape[:2]

        if self.method == 'mediapipe':
            # Convert BGR to RGB
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

            results = self.detector.process(rgb_frame)

            if results.detections:
                # Use first (most confident) detection
                detection = results.detections[0]
                bbox = detection.location_data.relative_bounding_box

                # Convert relative to absolute coordinates
                x = int((bbox.xmin + bbox.width / 2) * width)
                y = int((bbox.ymin + bbox.height / 2) * height)

                return (x, y)

        elif self.method == 'opencv':
            # Convert to grayscale
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)

            faces = self.detector.detectMultiScale(
                gray,
                scaleFactor=1.1,
                minNeighbors=5,
                minSize=(30, 30)
            )

            if len(faces) > 0:
                # Use largest face
                largest_face = max(faces, key=lambda f: f[2] * f[3])
                x, y, w, h = largest_face

                # Return center
                return (x + w // 2, y + h // 2)

        return None

    def calculate_smooth_trajectory(
        self,
        face_positions: List[Tuple[int, int]],
        smoothing_window: int = 5
    ) -> List[Tuple[int, int]]:
        """
        Smooth face tracking trajectory using moving average.

        Args:
            face_positions: Raw face positions
            smoothing_window: Size of moving average window

        Returns:
            Smoothed face positions
        """
        if len(face_positions) < smoothing_window:
            return face_positions

        smoothed = []

        for i in range(len(face_positions)):
            start_idx = max(0, i - smoothing_window // 2)
            end_idx = min(len(face_positions), i + smoothing_window // 2 + 1)

            window = face_positions[start_idx:end_idx]

            avg_x = sum(pos[0] for pos in window) / len(window)
            avg_y = sum(pos[1] for pos in window) / len(window)

            smoothed.append((int(avg_x), int(avg_y)))

        logger.debug(f"Smoothed {len(face_positions)} positions with window size {smoothing_window}")

        return smoothed


def calculate_reframe_path(
    face_positions: List[Tuple[int, int]],
    source_width: int,
    source_height: int,
    target_width: int,
    target_height: int
) -> List[Tuple[int, int]]:
    """
    Calculate optimal crop path for reframing based on face tracking.

    Args:
        face_positions: List of face center positions
        source_width: Original video width
        source_height: Original video height
        target_width: Target crop width (for 9:16)
        target_height: Target crop height (for 9:16)

    Returns:
        List of (x, y) top-left corner positions for crops
    """
    crop_path = []

    for face_x, face_y in face_positions:
        # Calculate crop position to center on face
        crop_x = face_x - target_width // 2
        crop_y = face_y - target_height // 2

        # Ensure crop stays within bounds
        crop_x = max(0, min(crop_x, source_width - target_width))
        crop_y = max(0, min(crop_y, source_height - target_height))

        crop_path.append((crop_x, crop_y))

    return crop_path
