"""Tests for ``src/_video_encoder.py``."""

import pytest

from src._video_encoder import VideoProcessor


class TestVideoProcessorInit:
    def test_stores_font_meta(self):
        proc = VideoProcessor(
            font_family="TikTokSans-Regular",
            font_size=30,
            font_color="#FFEE00",
            bold=True,
        )
        assert proc.font_family == "TikTokSans-Regular"
        assert proc.font_size == 30
        assert proc.font_color == "#FFEE00"
        assert proc.bold is True
        assert proc.italic is False

    def test_falls_back_when_font_missing(self):
        # A made-up font name should fall back through registry without raising.
        proc = VideoProcessor(font_family="definitely-not-a-real-font-xyz")
        # font_path is a string — empty allowed if no fallback resolved either
        assert isinstance(proc.font_path, str)

    def test_coerces_bold_italic_to_bool(self):
        proc = VideoProcessor(bold=1, italic="truthy")  # type: ignore[arg-type]
        assert proc.bold is True
        assert proc.italic is True


class TestEncodingSettings:
    def test_high_quality_defaults(self):
        settings = VideoProcessor().get_optimal_encoding_settings("high")
        assert settings["codec"] == "libx264"
        assert settings["audio_codec"] == "aac"
        assert settings["audio_bitrate"] == "256k"
        assert settings["preset"] == "slow"
        assert "-crf" in settings["ffmpeg_params"]
        assert "18" in settings["ffmpeg_params"]

    def test_medium_quality(self):
        settings = VideoProcessor().get_optimal_encoding_settings("medium")
        assert settings["preset"] == "fast"
        assert settings["bitrate"] == "4000k"
        assert "23" in settings["ffmpeg_params"]

    def test_unknown_quality_falls_back_to_high(self):
        unknown = VideoProcessor().get_optimal_encoding_settings("ultra-premium-bogus")
        high = VideoProcessor().get_optimal_encoding_settings("high")
        assert unknown == high
