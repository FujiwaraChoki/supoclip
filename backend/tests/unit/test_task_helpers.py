"""Tests for pure helpers extracted from ``TaskService``."""

from datetime import datetime, timedelta, timezone

import pytest

from src.services._task_helpers import (
    build_cache_key,
    is_queued_task_stale,
    seconds_to_mmss,
)


class TestBuildCacheKey:
    def test_stable_for_identical_inputs(self):
        a = build_cache_key("https://youtu.be/abc", "youtube", "fast")
        b = build_cache_key("https://youtu.be/abc", "youtube", "fast")
        assert a == b
        assert len(a) == 64  # sha256 hex digest

    def test_url_whitespace_is_trimmed(self):
        trimmed = build_cache_key("https://youtu.be/abc", "youtube", "fast")
        padded = build_cache_key("  https://youtu.be/abc  ", "youtube", "fast")
        assert trimmed == padded

    @pytest.mark.parametrize(
        "fields",
        [
            ("https://youtu.be/xyz", "youtube", "fast"),
            ("https://youtu.be/abc", "upload", "fast"),
            ("https://youtu.be/abc", "youtube", "thorough"),
        ],
    )
    def test_different_inputs_produce_different_keys(self, fields):
        base = build_cache_key("https://youtu.be/abc", "youtube", "fast")
        assert build_cache_key(*fields) != base


class TestSecondsToMmss:
    @pytest.mark.parametrize(
        "seconds,expected",
        [
            (0, "00:00"),
            (5, "00:05"),
            (59, "00:59"),
            (60, "01:00"),
            (61, "01:01"),
            (3599, "59:59"),
            (3600, "60:00"),
        ],
    )
    def test_formats(self, seconds, expected):
        assert seconds_to_mmss(seconds) == expected

    def test_fractional_rounds_to_nearest(self):
        assert seconds_to_mmss(59.4) == "00:59"
        assert seconds_to_mmss(59.6) == "01:00"

    def test_negative_clamps_to_zero(self):
        assert seconds_to_mmss(-5) == "00:00"


class TestIsQueuedTaskStale:
    def test_non_queued_tasks_never_stale(self):
        task = {"status": "processing", "created_at": None, "updated_at": None}
        assert is_queued_task_stale(task, timeout_seconds=60) is False

    def test_missing_timestamps_are_not_stale(self):
        task = {"status": "queued", "created_at": None, "updated_at": None}
        assert is_queued_task_stale(task, timeout_seconds=60) is False

    def test_fresh_queued_task_is_not_stale(self):
        now = datetime.now(timezone.utc)
        task = {"status": "queued", "created_at": now, "updated_at": now}
        assert is_queued_task_stale(task, timeout_seconds=60) is False

    def test_old_queued_task_is_stale(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        task = {"status": "queued", "created_at": old, "updated_at": old}
        assert is_queued_task_stale(task, timeout_seconds=60) is True

    def test_falls_back_to_created_at_when_updated_missing(self):
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        task = {"status": "queued", "created_at": old, "updated_at": None}
        assert is_queued_task_stale(task, timeout_seconds=60) is True

    def test_naive_datetime_is_handled(self):
        old_naive = datetime.utcnow() - timedelta(seconds=120)
        task = {"status": "queued", "created_at": old_naive, "updated_at": old_naive}
        assert is_queued_task_stale(task, timeout_seconds=60) is True
