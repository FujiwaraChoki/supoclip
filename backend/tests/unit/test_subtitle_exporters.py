"""Tests for ``src/subtitle_exporters.py``."""

import pytest

from src.subtitle_exporters import (
    DEFAULT_WORDS_PER_GROUP,
    build_word_groups_for_clip,
    word_groups_to_plaintext,
    word_groups_to_srt,
)


def _transcript(words):
    return {"words": words}


def _w(text, start_ms, end_ms, speaker=None):
    return {"text": text, "start": start_ms, "end": end_ms, "speaker": speaker}


class TestBuildWordGroupsForClip:
    def test_returns_empty_for_empty_transcript(self):
        assert build_word_groups_for_clip({}, 0.0, 10.0) == []

    def test_slices_and_chunks(self):
        words = [_w(str(i), i * 1000, i * 1000 + 500) for i in range(7)]
        groups = build_word_groups_for_clip(_transcript(words), 0.0, 10.0, words_per_group=3)
        assert [len(g) for g in groups] == [3, 3, 1]

    def test_filters_by_clip_range(self):
        words = [
            _w("a", 0, 500),
            _w("b", 2000, 2500),
            _w("c", 5000, 5500),
        ]
        groups = build_word_groups_for_clip(_transcript(words), 1.0, 3.0)
        flat = [w["text"] for group in groups for w in group]
        assert flat == ["b"]


class TestWordGroupsToSrt:
    def test_renders_minimal_srt(self):
        # Absolute ms — rebase via clip_start=0
        group = [_w("hello", 0, 400), _w("world", 500, 900)]
        srt_text = word_groups_to_srt([group], clip_start=0.0)
        assert "hello world" in srt_text
        assert "00:00:00,000" in srt_text

    def test_rebases_to_clip_start(self):
        group = [_w("x", 10_000, 10_500)]
        srt_text = word_groups_to_srt([group], clip_start=10.0)
        # Rebased to 0 ms
        assert "00:00:00,000" in srt_text

    def test_skips_empty_groups(self):
        assert word_groups_to_srt([[]], clip_start=0.0) == ""

    def test_skips_whitespace_only_text(self):
        group = [_w("   ", 0, 500)]
        assert word_groups_to_srt([group], clip_start=0.0) == ""

    def test_enforces_minimum_duration(self):
        group = [_w("tight", 1000, 1000)]  # zero duration
        srt_text = word_groups_to_srt([group], clip_start=0.0)
        # End should be start + 100ms minimum, so it's not empty
        assert "tight" in srt_text


class TestWordGroupsToPlaintext:
    def test_joins_groups_with_newlines(self):
        groups = [
            [_w("hello", 0, 400), _w("world", 500, 900)],
            [_w("bye", 1000, 1400)],
        ]
        assert word_groups_to_plaintext(groups) == "hello world\nbye"

    def test_skips_empty(self):
        assert word_groups_to_plaintext([]) == ""
        assert word_groups_to_plaintext([[]]) == ""


def test_default_words_per_group_sensible():
    assert DEFAULT_WORDS_PER_GROUP >= 1
