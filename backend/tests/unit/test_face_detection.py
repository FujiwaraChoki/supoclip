from pathlib import Path

import numpy as np
import pytest

from src.media import face_detection, reframing
from src.media.face_detection import (
    YUNET_MODEL_PATH,
    YuNetFaceDetector,
    open_yunet_detector,
)


def test_bundled_yunet_model_loads():
    assert YUNET_MODEL_PATH.is_file()
    detector = open_yunet_detector()
    assert isinstance(detector, YuNetFaceDetector)


def test_yunet_returns_no_faces_on_blank_frames_of_varying_size():
    detector = open_yunet_detector()
    assert detector is not None
    # Size changes between calls exercise the per-frame setInputSize path,
    # and the 1920-wide frame exercises the downscale path.
    for shape in [(270, 480, 3), (1080, 1920, 3), (360, 640, 3)]:
        assert detector.detect(np.zeros(shape, dtype=np.uint8)) == []


def test_missing_model_falls_back_to_none(tmp_path: Path):
    assert open_yunet_detector(tmp_path / "missing.onnx") is None


def test_open_face_detectors_falls_back_to_haar_when_model_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(face_detection, "YUNET_MODEL_PATH", tmp_path / "missing.onnx")
    monkeypatch.setattr(
        reframing,
        "open_yunet_detector",
        lambda: face_detection.open_yunet_detector(face_detection.YUNET_MODEL_PATH),
    )
    detector, haar = reframing._open_face_detectors()
    assert detector is None
    assert not haar.empty()
    # The Haar path still runs (and finds nothing) on a blank frame.
    assert reframing._detect_dominant_face(np.zeros((360, 640, 3), np.uint8), None, haar) is None


class _StubDetector:
    def __init__(self, boxes):
        self.boxes = boxes

    def detect(self, frame_bgr):
        return list(self.boxes)


def test_dominant_face_prefers_largest_confident_box():
    frame = np.zeros((400, 800, 3), dtype=np.uint8)
    stub = _StubDetector(
        [
            (100.0, 100.0, 40.0, 40.0, 0.95),  # small
            (600.0, 80.0, 100.0, 120.0, 0.9),  # large -> dominant
        ]
    )
    cx_frac, area_frac = reframing._detect_dominant_face(frame, stub, None)
    assert cx_frac == pytest.approx(650.0 / 800.0)
    assert area_frac == pytest.approx((100.0 * 120.0) / (800.0 * 400.0))


def test_detect_rescales_boxes_from_downscaled_input(monkeypatch):
    detector = YuNetFaceDetector(max_input_width=480)
    seen_sizes = []

    class _FakeYuNet:
        def setInputSize(self, size):
            seen_sizes.append(size)

        def detect(self, image):
            # One face at (24, 12, 48, 60) in the 480x270 downscaled frame.
            row = np.array([[24, 12, 48, 60] + [0] * 10 + [0.88]], dtype=np.float32)
            return 1, row

    detector._detector = _FakeYuNet()
    boxes = detector.detect(np.zeros((540, 960, 3), dtype=np.uint8))
    assert seen_sizes == [(480, 270)]
    (x, y, w, h, score), = boxes
    assert (x, y, w, h) == pytest.approx((48.0, 24.0, 96.0, 120.0))
    assert score == pytest.approx(0.88)


def test_detect_clips_boxes_to_frame():
    detector = YuNetFaceDetector()

    class _FakeYuNet:
        def setInputSize(self, size):
            pass

        def detect(self, image):
            row = np.array([[-10, 300, 50, 100] + [0] * 10 + [0.7]], dtype=np.float32)
            return 1, row

    detector._detector = _FakeYuNet()
    (x, y, w, h, _), = detector.detect(np.zeros((360, 640, 3), dtype=np.uint8))
    assert (x, y, w, h) == pytest.approx((0.0, 300.0, 40.0, 60.0))


def test_process_returns_mediapipe_shaped_results():
    detector = YuNetFaceDetector()

    class _FakeYuNet:
        def setInputSize(self, size):
            pass

        def detect(self, image):
            row = np.array([[64, 36, 128, 72] + [0] * 10 + [0.9]], dtype=np.float32)
            return 1, row

    detector._detector = _FakeYuNet()
    results = detector.process(np.zeros((360, 640, 3), dtype=np.uint8))
    (det,) = results.detections
    box = det.location_data.relative_bounding_box
    assert (box.xmin, box.ymin, box.width, box.height) == pytest.approx(
        (0.1, 0.1, 0.2, 0.2)
    )
    assert det.score[0] == pytest.approx(0.9)
    detector.close()

    detector._detector = type(
        "_Empty", (), {"setInputSize": lambda self, s: None, "detect": lambda self, i: (1, None)}
    )()
    assert detector.process(np.zeros((360, 640, 3), dtype=np.uint8)).detections is None
