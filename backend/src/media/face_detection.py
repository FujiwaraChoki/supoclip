"""YuNet face detection (OpenCV ``FaceDetectorYN``) for the reframing pipeline.

The bundled ONNX model replaces MediaPipe's legacy ``mp.solutions`` detector,
which newer mediapipe wheels no longer ship (every render silently fell back to
the Haar cascade). Haar stays as the fallback when the model can't be loaded.
"""

from pathlib import Path
from types import SimpleNamespace
from typing import List, Optional, Tuple

import cv2
import numpy as np

from .common import logger

YUNET_MODEL_PATH = Path(__file__).parent / "models" / "face_detection_yunet_2023mar.onnx"

YUNET_SCORE_THRESHOLD = 0.6

YUNET_NMS_THRESHOLD = 0.3

YUNET_TOP_K = 50

# Frames wider than this are downscaled before detection: YuNet's cost grows
# with input size, and talking-head faces stay well above its ~10px minimum.
YUNET_MAX_INPUT_WIDTH = 960

FaceBox = Tuple[float, float, float, float, float]  # (x, y, w, h, score) in px


class YuNetFaceDetector:
    """Thin stateful wrapper that resizes YuNet's input per frame size."""

    def __init__(
        self,
        model_path: Path = YUNET_MODEL_PATH,
        score_threshold: float = YUNET_SCORE_THRESHOLD,
        nms_threshold: float = YUNET_NMS_THRESHOLD,
        top_k: int = YUNET_TOP_K,
        max_input_width: int = YUNET_MAX_INPUT_WIDTH,
    ):
        if not Path(model_path).is_file():
            raise FileNotFoundError(f"YuNet model not found: {model_path}")
        self._detector = cv2.FaceDetectorYN.create(
            str(model_path), "", (320, 320), score_threshold, nms_threshold, top_k
        )
        self._input_size: Optional[Tuple[int, int]] = None
        self._max_input_width = max(64, int(max_input_width))

    def detect(self, frame_bgr: np.ndarray) -> List[FaceBox]:
        """Return face boxes in the frame's own pixel coordinates."""
        height, width = frame_bgr.shape[:2]
        if width <= 0 or height <= 0:
            return []
        scale = 1.0
        image = frame_bgr
        if width > self._max_input_width:
            scale = self._max_input_width / float(width)
            image = cv2.resize(
                frame_bgr,
                (self._max_input_width, max(1, int(round(height * scale)))),
                interpolation=cv2.INTER_AREA,
            )
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        size = (image.shape[1], image.shape[0])
        if size != self._input_size:
            self._detector.setInputSize(size)
            self._input_size = size

        _, faces = self._detector.detect(np.ascontiguousarray(image))
        if faces is None:
            return []
        boxes: List[FaceBox] = []
        for face in faces:
            x, y, w, h = (float(v) / scale for v in face[:4])
            score = float(face[-1])
            # Clip to the frame: YuNet boxes can extend past the edges.
            x0, y0 = max(0.0, x), max(0.0, y)
            x1, y1 = min(float(width), x + w), min(float(height), y + h)
            if x1 - x0 > 1 and y1 - y0 > 1:
                boxes.append((x0, y0, x1 - x0, y1 - y0, score))
        return boxes

    def process(self, frame_rgb: np.ndarray) -> SimpleNamespace:
        """MediaPipe ``FaceDetection.process`` compatible result.

        Call sites written against ``mp.solutions`` read
        ``results.detections[i].location_data.relative_bounding_box`` and
        ``.score``; this keeps them working with YuNet unchanged.
        """
        height, width = frame_rgb.shape[:2]
        frame_bgr = cv2.cvtColor(frame_rgb, cv2.COLOR_RGB2BGR)
        detections = []
        for x, y, w, h, score in self.detect(frame_bgr):
            box = SimpleNamespace(
                xmin=x / width, ymin=y / height, width=w / width, height=h / height
            )
            detections.append(
                SimpleNamespace(
                    location_data=SimpleNamespace(relative_bounding_box=box),
                    score=[score],
                )
            )
        return SimpleNamespace(detections=detections or None)

    def close(self) -> None:
        """No resources to release; kept for MediaPipe-style call sites."""


def open_yunet_detector(
    model_path: Path = YUNET_MODEL_PATH, **kwargs
) -> Optional[YuNetFaceDetector]:
    """Load YuNet, or return None (callers fall back to Haar)."""
    try:
        return YuNetFaceDetector(model_path, **kwargs)
    except Exception as exc:
        logger.warning("YuNet face detector unavailable (%s); using Haar", exc)
        return None


def open_haar_cascade():
    return cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
