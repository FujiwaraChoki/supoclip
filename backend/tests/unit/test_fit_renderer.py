"""Tests for ``src/fit_renderer.py`` pure helpers + light integration."""

import numpy as np
import pytest

from src.fit_renderer import _blur_frame, _round_to_even


class TestRoundToEven:
    @pytest.mark.parametrize(
        "value,expected",
        [
            (0, 0),
            (1, 0),
            (2, 2),
            (3, 2),
            (1080, 1080),
            (1081, 1080),
            (1920, 1920),
            (1921, 1920),
        ],
    )
    def test_rounds_down_to_even(self, value, expected):
        assert _round_to_even(value) == expected


class TestBlurFrame:
    def test_returns_same_shape(self):
        frame = np.random.randint(0, 255, (100, 100, 3), dtype=np.uint8)
        blurred = _blur_frame(frame, kernel=5)
        assert blurred.shape == frame.shape
        assert blurred.dtype == frame.dtype

    def test_forces_odd_kernel(self):
        frame = np.zeros((50, 50, 3), dtype=np.uint8)
        # Even kernel → internally bumped to odd; should not raise.
        _blur_frame(frame, kernel=4)

    def test_blur_is_not_identity(self):
        frame = np.zeros((30, 30, 3), dtype=np.uint8)
        frame[15, 15] = 255  # single bright pixel at center
        blurred = _blur_frame(frame, kernel=5)
        # With a proper Gaussian blur, the bright pixel spreads to neighbors
        assert blurred[14, 15, 0] > 0
        assert blurred[15, 15, 0] < 255
