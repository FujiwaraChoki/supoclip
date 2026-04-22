"""Tests for pure helpers in ``src/clip_editor.py``."""

from types import SimpleNamespace

import pytest

from src.clip_editor import (
    EXPORT_PRESETS,
    ExportPreset,
    _double_bitrate,
    _high_quality_encode_options,
    _safe_name,
    _source_fps,
)


class TestSafeName:
    def test_has_prefix_and_suffix(self):
        name = _safe_name("trim")
        assert name.startswith("trim_")
        assert name.endswith(".mp4")

    def test_unique(self):
        names = {_safe_name("x") for _ in range(50)}
        assert len(names) == 50

    def test_includes_random_hex(self):
        name = _safe_name("foo")
        # foo_<12 hex>.mp4 = "foo_" + 12 chars + ".mp4"
        stem = name.rsplit(".", 1)[0]
        hex_part = stem.rsplit("_", 1)[1]
        assert len(hex_part) == 12
        assert all(c in "0123456789abcdef" for c in hex_part)


class TestDoubleBitrate:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("10M", "20M"),
            ("5M", "10M"),
            ("192k", "384k"),
            ("128k", "256k"),
            ("2.5M", "5M"),
            ("12M", "24M"),
        ],
    )
    def test_doubles(self, value, expected):
        assert _double_bitrate(value) == expected

    def test_case_insensitive(self):
        assert _double_bitrate("10m") == "20M"
        assert _double_bitrate("192K") == "384k"

    def test_unknown_unit_passthrough(self):
        assert _double_bitrate("1000") == "1000"
        assert _double_bitrate("10G") == "10G"


class TestSourceFps:
    def test_uses_clip_fps(self):
        clip = SimpleNamespace(fps=29.97)
        assert _source_fps(clip) == pytest.approx(29.97)

    def test_falls_back_to_30_for_zero(self):
        assert _source_fps(SimpleNamespace(fps=0)) == 30.0

    def test_falls_back_for_none(self):
        assert _source_fps(SimpleNamespace(fps=None)) == 30.0

    def test_falls_back_for_negative(self):
        assert _source_fps(SimpleNamespace(fps=-1)) == 30.0


class TestHighQualityEncodeOptions:
    def test_shape_contains_required_keys(self):
        opts = _high_quality_encode_options(30)
        assert opts["codec"] == "libx264"
        assert opts["audio_codec"] == "aac"
        assert opts["fps"] == 30
        assert opts["preset"] == "slow"
        assert "ffmpeg_params" in opts

    def test_propagates_fps(self):
        assert _high_quality_encode_options(60)["fps"] == 60

    def test_ffmpeg_params_contain_crf_and_faststart(self):
        params = _high_quality_encode_options(30)["ffmpeg_params"]
        assert "-crf" in params
        assert "+faststart" in params
        assert "yuv420p" in params


class TestExportPresets:
    @pytest.mark.parametrize("name", ["tiktok", "reels", "shorts"])
    def test_known_presets_are_vertical_1080p(self, name):
        preset = EXPORT_PRESETS[name]
        assert isinstance(preset, ExportPreset)
        assert preset.width == 1080
        assert preset.height == 1920

    def test_bitrate_format(self):
        for preset in EXPORT_PRESETS.values():
            assert preset.video_bitrate.endswith("M")
            assert preset.audio_bitrate.endswith("k")
