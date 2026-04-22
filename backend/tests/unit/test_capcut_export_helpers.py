"""Tests for pure helpers in ``src/capcut_export.py``."""

import uuid

import pytest

from src.capcut_export import (
    ClipExportInput,
    SubtitleSegment,
    _default_clip_transform,
    _default_crop,
    _seconds_to_us,
    _uuid_lower,
    _uuid_upper,
)


class TestSecondsToUs:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, 0),
            (0.5, 500_000),
            (1, 1_000_000),
            (1.5, 1_500_000),
            (60, 60_000_000),
        ],
    )
    def test_converts_to_microseconds(self, seconds, expected):
        assert _seconds_to_us(seconds) == expected

    def test_negative_clamps_to_zero(self):
        assert _seconds_to_us(-5) == 0

    def test_rounds_half_to_nearest(self):
        # 0.0000005 seconds = 0.5 us → rounds to 1 (banker's rounds to 0 in py3)
        assert _seconds_to_us(0.0000015) in (1, 2)


class TestUuidHelpers:
    def test_upper_is_valid_uuid_upper(self):
        value = _uuid_upper()
        assert value == value.upper()
        assert uuid.UUID(value)  # raises if not a valid UUID

    def test_lower_is_valid_uuid_lower(self):
        value = _uuid_lower()
        assert value == value.lower()
        assert uuid.UUID(value)

    def test_unique(self):
        assert _uuid_upper() != _uuid_upper()
        assert _uuid_lower() != _uuid_lower()


class TestDefaultCrop:
    def test_full_frame(self):
        crop = _default_crop()
        # Full frame: LL=(0,1), LR=(1,1), UL=(0,0), UR=(1,0)
        assert crop["lower_left_x"] == 0.0
        assert crop["lower_left_y"] == 1.0
        assert crop["upper_right_x"] == 1.0
        assert crop["upper_right_y"] == 0.0

    def test_has_all_corners(self):
        crop = _default_crop()
        for corner in ("lower_left", "lower_right", "upper_left", "upper_right"):
            assert f"{corner}_x" in crop
            assert f"{corner}_y" in crop


class TestDefaultClipTransform:
    def test_identity_transform(self):
        transform = _default_clip_transform()
        assert transform["alpha"] == 1.0
        assert transform["rotation"] == 0.0
        assert transform["flip"] == {"horizontal": False, "vertical": False}


class TestSubtitleSegment:
    def test_fields(self):
        seg = SubtitleSegment(text="hello", start_seconds=1.0, end_seconds=2.5)
        assert seg.text == "hello"
        assert seg.start_seconds == 1.0
        assert seg.end_seconds == 2.5


class TestClipExportInput:
    def test_defaults(self):
        from pathlib import Path

        inp = ClipExportInput(
            clip_filename="clip.mp4",
            clip_source_path=Path("/tmp/clip.mp4"),
            duration_seconds=15.0,
        )
        assert inp.width == 1080
        assert inp.height == 1920
        assert inp.caption_text is None
        assert inp.subtitle_segments == []
        assert inp.srt_content is None
        assert inp.project_name is None

    def test_accepts_subtitle_list(self):
        from pathlib import Path

        segments = [
            SubtitleSegment("one", 0.0, 1.0),
            SubtitleSegment("two", 1.0, 2.0),
        ]
        inp = ClipExportInput(
            clip_filename="clip.mp4",
            clip_source_path=Path("/tmp/clip.mp4"),
            duration_seconds=2.0,
            subtitle_segments=segments,
        )
        assert len(inp.subtitle_segments) == 2
        assert inp.subtitle_segments[0].text == "one"
