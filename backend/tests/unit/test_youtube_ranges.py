"""Section downloads fetch what a segment plays, not the span of its labels."""

from src.youtube_source import merge_ranges, ranges_from_segments


def test_legacy_segment_downloads_its_labels_and_hook():
    segment = {"start_time": "01:00", "end_time": "01:40", "hook_start_time": "01:20", "hook_end_time": "01:24"}

    assert ranges_from_segments([segment]) == [(60.0, 100.0), (80.0, 84.0)]


def test_planned_recap_downloads_only_its_ranges():
    # Labels name the first range; the ranges jump across 40 minutes.
    segment = {
        "start_time": "02:00", "end_time": "02:20",
        "source_ranges": [[120.0, 140.0], [1300.0, 1320.0], [2500.0, 2510.0]],
        "planned": True,
    }

    assert ranges_from_segments([segment]) == [(120.0, 140.0), (1300.0, 1320.0), (2500.0, 2510.0)]
    assert sum(end - start for start, end in merge_ranges(ranges_from_segments([segment]), padding=2.0)) < 70


def test_regenerated_merged_clip_downloads_its_pieces_not_the_whole_span():
    segment = {
        "start_time": "00:10", "end_time": "30:00",
        "keep_ranges": [(10.0, 40.0), ({"start": 1770.0, "end": 1800.0})],
    }

    assert ranges_from_segments([segment]) == [(10.0, 40.0), (1770.0, 1800.0)]


def test_invalid_explicit_ranges_fall_back_to_the_labels():
    segment = {"start_time": "00:10", "end_time": "00:20", "source_ranges": [[5.0, 5.0], ["x"]]}

    assert ranges_from_segments([segment]) == [(10.0, 20.0)]
