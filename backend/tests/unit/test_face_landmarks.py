"""``YuNetFaceDetector.detect(landmarks=True)`` (FINAL §6.4; WP12c). The default stays the 5-tuple box."""

from __future__ import annotations

import numpy as np
import pytest

from src.media.face_detection import YuNetFaceDetector, open_yunet_detector

# YuNet row: x, y, w, h, then (right eye, left eye, nose, right mouth, left mouth) x/y pairs, then score.
ROW = [24, 12, 48, 60, 36, 30, 60, 30, 48, 42, 38, 58, 58, 58, 0.88]


class FakeYuNet:
    def __init__(self, rows):
        self.rows = rows
        self.sizes = []

    def setInputSize(self, size):
        self.sizes.append(size)

    def detect(self, image):
        return 1, np.array(self.rows, dtype=np.float32)


def detector_with(rows, **kwargs) -> YuNetFaceDetector:
    detector = YuNetFaceDetector(**kwargs)
    detector._detector = FakeYuNet(rows)
    return detector


def test_landmarks_are_returned_after_the_box_and_rescaled():
    detector = detector_with([ROW], max_input_width=480)
    (face,) = detector.detect(np.zeros((540, 960, 3), np.uint8), landmarks=True)   # downscaled 2x
    assert len(face) == 15
    assert face[:4] == pytest.approx((48.0, 24.0, 96.0, 120.0))
    assert face[4] == pytest.approx(0.88)
    assert face[5:] == pytest.approx([2 * v for v in ROW[4:14]])


def test_default_detect_is_unchanged():
    detector = detector_with([ROW], max_input_width=480)
    (face,) = detector.detect(np.zeros((540, 960, 3), np.uint8))
    assert len(face) == 5
    assert face == pytest.approx((48.0, 24.0, 96.0, 120.0, 0.88))
    assert detector.detect(np.zeros((540, 960, 3), np.uint8), landmarks=False) == [face]


def test_landmarks_are_clamped_into_the_frame():
    row = [-10, 300, 50, 100, -4, 310, 30, 310, 15, 330, -2, 380, 700, 420, 0.7]
    (face,) = detector_with([row]).detect(np.zeros((360, 640, 3), np.uint8), landmarks=True)
    assert face[:4] == pytest.approx((0.0, 300.0, 40.0, 60.0))   # the box is clipped as before
    xs, ys = face[5::2], face[6::2]
    assert min(xs) == 0.0 and max(xs) == 640.0
    assert all(0.0 <= y <= 360.0 for y in ys)


def test_boxes_clipped_away_drop_their_landmarks_too():
    row = [700, 10, 40, 40] + [710, 20] * 5 + [0.9]
    assert detector_with([row]).detect(np.zeros((360, 640, 3), np.uint8), landmarks=True) == []


def test_real_model_with_landmarks_on_blank_frames():
    detector = open_yunet_detector()
    assert detector is not None
    for shape in [(960, 540, 3), (1080, 1920, 3)]:
        assert detector.detect(np.zeros(shape, np.uint8), landmarks=True) == []
