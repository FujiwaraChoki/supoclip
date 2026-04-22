"""Tests for pure helpers in ``src/_video_helpers.py``."""

import pytest

from src._video_helpers import (
    _chunk_words_by_speaker,
    format_ms_to_timestamp,
    get_safe_vertical_position,
    get_scaled_font_size,
    get_subtitle_max_width,
    get_words_in_range,
    parse_timestamp_to_seconds,
    round_to_even,
)


class TestFormatMsToTimestamp:
    @pytest.mark.parametrize(
        "ms,expected",
        [
            (0, "00:00"),
            (1000, "00:01"),
            (59_000, "00:59"),
            (60_000, "01:00"),
            (125_500, "02:05"),  # truncates to whole seconds
        ],
    )
    def test_formats(self, ms, expected):
        assert format_ms_to_timestamp(ms) == expected


class TestRoundToEven:
    @pytest.mark.parametrize("value,expected", [(0, 0), (1, 0), (2, 2), (3, 2), (1081, 1080)])
    def test_rounds_down_to_even(self, value, expected):
        assert round_to_even(value) == expected


class TestGetScaledFontSize:
    def test_scales_linearly_with_width(self):
        # reference width is 720
        assert get_scaled_font_size(24, 720) == 24
        assert get_scaled_font_size(24, 1440) == 48

    def test_clamps_to_minimum(self):
        assert get_scaled_font_size(10, 100) == 24

    def test_clamps_to_maximum(self):
        assert get_scaled_font_size(48, 4000) == 64


class TestGetSubtitleMaxWidth:
    def test_applies_horizontal_padding(self):
        # 6% padding of 1000 = 60, min 40 → 60 each side
        assert get_subtitle_max_width(1000) == 1000 - (60 * 2)

    def test_respects_minimum_padding(self):
        # 6% of 300 = 18, min 40 → 40 each side
        assert get_subtitle_max_width(300) == 300 - (40 * 2)

    def test_respects_minimum_width(self):
        assert get_subtitle_max_width(100) == 200


class TestGetSafeVerticalPosition:
    def test_respects_top_safe_area(self):
        pos = get_safe_vertical_position(video_height=1920, text_height=60, position_y=0.0)
        assert pos >= max(40, int(1920 * 0.05))

    def test_respects_bottom_safe_area(self):
        pos = get_safe_vertical_position(video_height=1920, text_height=60, position_y=1.0)
        max_y = 1920 - max(120, int(1920 * 0.10)) - 60
        assert pos <= max_y

    def test_centered_target(self):
        pos = get_safe_vertical_position(video_height=1000, text_height=100, position_y=0.5)
        assert 400 <= pos <= 600


class TestParseTimestampToSeconds:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("00:30", 30),
            ("01:15", 75),
            ("00:01:30", 90),
            ("1:02:03", 3723),
            ("12.5", 12.5),
        ],
    )
    def test_valid_inputs(self, value, expected):
        assert parse_timestamp_to_seconds(value) == expected

    def test_invalid_returns_zero(self):
        assert parse_timestamp_to_seconds("not-a-time") == 0.0

    def test_handles_whitespace(self):
        assert parse_timestamp_to_seconds("  00:30  ") == 30


class TestGetWordsInRange:
    def test_empty_transcript(self):
        assert get_words_in_range({}, 0, 10) == []
        assert get_words_in_range({"words": []}, 0, 10) == []

    def test_filters_to_range_and_rebases_to_clip_start(self):
        transcript = {
            "words": [
                {"text": "alpha", "start": 0, "end": 500},
                {"text": "beta", "start": 1000, "end": 1500},
                {"text": "gamma", "start": 2000, "end": 2500},
            ]
        }
        words = get_words_in_range(transcript, clip_start=1.0, clip_end=2.5)
        assert [w["text"] for w in words] == ["beta", "gamma"]
        # beta starts at 1000ms in absolute, clip starts at 1000ms → rebased to 0
        assert words[0]["start"] == pytest.approx(0.0)
        assert words[0]["end"] == pytest.approx(0.5)

    def test_confidence_defaults_to_one(self):
        transcript = {"words": [{"text": "x", "start": 0, "end": 500}]}
        word = get_words_in_range(transcript, 0, 1)[0]
        assert word["confidence"] == 1.0
        assert word["speaker"] is None


class TestChunkWordsBySpeaker:
    def _w(self, text, speaker=None):
        return {"text": text, "speaker": speaker}

    def test_empty(self):
        assert _chunk_words_by_speaker([], 3) == []

    def test_zero_max_returns_empty(self):
        assert _chunk_words_by_speaker([self._w("x")], 0) == []

    def test_chunks_by_size(self):
        words = [self._w(str(i)) for i in range(7)]
        groups = _chunk_words_by_speaker(words, max_per_group=3)
        assert [len(g) for g in groups] == [3, 3, 1]

    def test_splits_on_speaker_change(self):
        words = [
            self._w("hi", "A"),
            self._w("there", "A"),
            self._w("greetings", "B"),
        ]
        groups = _chunk_words_by_speaker(words, max_per_group=10)
        assert len(groups) == 2
        assert [w["text"] for w in groups[0]] == ["hi", "there"]
        assert [w["text"] for w in groups[1]] == ["greetings"]
