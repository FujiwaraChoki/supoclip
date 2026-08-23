from types import SimpleNamespace
import pytest
from src.ai import (
    parse_timestamp_to_seconds,
    _filter_overlapping_segments,
    build_transcript_analysis_prompt,
    compute_available_clip_capacity,
)


def test_parse_timestamp_to_seconds():
    assert parse_timestamp_to_seconds("00:30") == 30.0
    assert parse_timestamp_to_seconds("01:15") == 75.0
    assert parse_timestamp_to_seconds("01:02:03") == 3723.0
    assert parse_timestamp_to_seconds("") == 0.0


def test_filter_overlapping_segments():
    # Existing clips: 00:10-00:40 and 02:00-02:45
    excluded_ranges = [("00:10", "00:40"), ("02:00", "02:45")]

    candidate_segments = [
        # Overlaps with 00:10-00:40 (00:20 - 00:50) -> should be discarded
        SimpleNamespace(start_time="00:20", end_time="00:50", text="Overlapping clip 1"),
        # Completely distinct (00:50 - 01:25) -> should be kept (35s long)
        SimpleNamespace(start_time="00:50", end_time="01:25", text="Clean clip 2"),
        # Overlaps with 02:00-02:45 (02:10 - 02:30) -> should be discarded
        SimpleNamespace(start_time="02:10", end_time="02:30", text="Overlapping clip 3"),
        # Completely distinct (03:00 - 03:35) -> should be kept (35s long)
        SimpleNamespace(start_time="03:00", end_time="03:35", text="Clean clip 4"),
        # Too short (only 5s duration < min 10s) -> should be discarded
        SimpleNamespace(start_time="04:00", end_time="04:05", text="Short clip 5"),
    ]

    filtered = _filter_overlapping_segments(candidate_segments, excluded_ranges, min_clip_duration=10.0)
    assert len(filtered) == 2
    assert filtered[0].text == "Clean clip 2"
    assert filtered[1].text == "Clean clip 4"


def test_build_transcript_analysis_prompt_with_exclusions_and_min_duration():
    transcript = "[00:00 - 00:10] Hello world\n[00:10 - 00:20] More text"
    prompt = build_transcript_analysis_prompt(
        transcript=transcript,
        excluded_ranges=[("00:00", "00:10")],
        target_clip_count=3,
        min_clip_duration=30,
    )

    assert "CRITICAL EXCLUSIONS" in prompt
    assert "[00:00 - 00:10]" in prompt
    assert "Choose exactly 3 segments total" in prompt
    assert "Each chosen clip MUST be at least 30 seconds" in prompt


def test_compute_available_clip_capacity():
    total_duration = 300.0  # 5 minutes (300 seconds)

    # Exclusions: [00:30-01:30] (60s) and [02:30-03:30] (60s)
    # Available gaps:
    # Gap 1: 0 to 30s (30s) -> fits 1 clip of 30s
    # Gap 2: 90s to 150s (60s) -> fits 2 clips of 30s
    # Gap 3: 210s to 300s (90s) -> fits 3 clips of 30s
    excluded_ranges = [("00:30", "01:30"), ("02:30", "03:30")]

    capacity = compute_available_clip_capacity(
        total_duration_sec=total_duration,
        excluded_ranges=excluded_ranges,
        min_clip_duration=30.0,
    )

    assert capacity["total_unallocated_seconds"] == 180.0
    assert capacity["usable_unallocated_seconds"] == 180.0
    assert capacity["max_possible_clips"] == 6  # 1 + 2 + 3 = 6
    assert len(capacity["available_intervals"]) == 3

    # Test edge case: All video clipped -> 0 capacity
    full_exclusion = [("00:00", "05:00")]
    exhausted_capacity = compute_available_clip_capacity(
        total_duration_sec=total_duration,
        excluded_ranges=full_exclusion,
        min_clip_duration=15.0,
    )
    assert exhausted_capacity["max_possible_clips"] == 0
    assert exhausted_capacity["usable_unallocated_seconds"] == 0.0
