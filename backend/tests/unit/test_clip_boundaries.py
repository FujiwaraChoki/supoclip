import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from src import ai, video_utils
from src.clip_source_map import (
    load_clip_source_ranges,
    save_clip_source_ranges,
    source_range_bounds,
)
from src.media import timeline
from src.media.ffmpeg import crossfade_fade_for_ranges


def _words(*items):
    return [
        {"text": text, "start": int(start * 1000), "end": int(end * 1000), "confidence": 0.99}
        for text, start, end in items
    ]


TRANSCRIPT_WORDS = _words(
    ("That's", 9.6, 10.0),
    ("it.", 10.1, 10.4),
    ("So", 10.7, 10.9),
    ("here", 10.9, 11.1),
    ("is", 11.1, 11.2),
    ("the", 11.2, 11.3),
    ("thing.", 11.3, 11.7),
    ("Most", 12.0, 12.3),
    ("people", 12.3, 12.6),
    ("never", 12.6, 12.9),
    ("save", 12.9, 13.2),
    ("money", 13.2, 13.5),
    ("because", 13.5, 13.8),
    ("they", 13.8, 14.0),
    ("spend", 14.0, 14.3),
    ("first.", 14.3, 14.8),
    ("I", 20.0, 20.2),
    ("lost", 20.2, 20.5),
    ("forty", 20.5, 20.8),
    ("thousand", 20.8, 21.2),
    ("dollars.", 21.2, 21.7),
    ("It", 21.9, 22.0),
    ("was", 22.0, 22.2),
    ("brutal.", 22.2, 22.6),
    ("And", 30.0, 30.3),
    ("that", 30.3, 30.6),
    ("changed", 30.6, 31.0),
    ("everything", 31.0, 31.6),
    ("for", 31.6, 31.8),
    ("me.", 31.8, 32.2),
)


@pytest.fixture
def video_path(tmp_path):
    path = tmp_path / "source.mp4"
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "words": TRANSCRIPT_WORDS, "utterances": []}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def transcript_data(video_path):
    return timeline.load_cached_transcript_data(video_path)


def test_start_skips_previous_sentence_tail(video_path):
    # "00:10" label: the previous sentence's "it." ends inside that second.
    ranges = timeline.snap_keep_ranges_to_sentence_start(video_path, [(10.0, 32.0)])
    assert ranges == [(pytest.approx(10.6), 32.0)]


def test_start_mid_sentence_walks_back_to_sentence_start(video_path):
    ranges = timeline.snap_keep_ranges_to_sentence_start(video_path, [(13.0, 32.0)])
    assert ranges == [(pytest.approx(11.9), 32.0)]


def test_start_trims_leading_dead_air(video_path):
    ranges = timeline.snap_keep_ranges_to_sentence_start(video_path, [(18.0, 32.0)])
    assert ranges == [(pytest.approx(19.9), 32.0)]


def test_start_never_pulls_in_previous_word_tail():
    words = [
        {"text": "done.", "start": 4.0, "end": 4.98},
        {"text": "Next", "start": 5.0, "end": 5.3},
    ]
    index = timeline._sentence_start_word_index(words, 4.9, 3.0)
    assert index == 1
    assert timeline._start_before_word(words, index, 0.1) == pytest.approx(4.98)


def test_start_without_boundary_in_window_stays_word_aligned():
    words = [
        {"text": f"w{i}", "start": i * 0.3, "end": i * 0.3 + 0.28}
        for i in range(40)
    ]
    # No punctuation or pauses: the first touched word is used.
    index = timeline._sentence_start_word_index(words, 6.1, 3.0)
    assert words[index]["start"] == pytest.approx(6.0)


def test_start_snapping_is_idempotent(video_path):
    once = timeline.snap_keep_ranges_to_sentence_start(video_path, [(13.0, 32.0)])
    twice = timeline.snap_keep_ranges_to_sentence_start(video_path, once)
    assert twice == once


def test_start_snapping_without_transcript_is_noop(tmp_path):
    ranges = timeline.snap_keep_ranges_to_sentence_start(tmp_path / "x.mp4", [(13.0, 32.0)])
    assert ranges == [(13.0, 32.0)]


def test_start_snapping_moves_body_not_teaser(video_path):
    ranges = timeline.snap_keep_ranges_to_sentence_start(
        video_path, [(19.92, 21.85), (13.0, 32.0)]
    )
    assert ranges[0] == (19.92, 21.85)
    assert ranges[1] == (pytest.approx(11.9), 32.0)


def _render_segments(video_path, tmp_path, monkeypatch, segment, cleanup=None):
    rendered = []

    def fake_create(video, start, end, output_path, *args, **kwargs):
        rendered.append(args[6])  # keep_ranges
        return True

    monkeypatch.setattr(video_utils, "create_optimized_clip", fake_create)
    monkeypatch.setattr(
        timeline, "get_config", lambda: SimpleNamespace(hook_first_teaser=True)
    )
    with patch("src.media.timeline.ffprobe_duration", return_value=60.0):
        infos = video_utils.create_clips_from_segments(
            video_path,
            [
                {
                    "start_time": "00:10",
                    "end_time": "00:31",
                    "text": "body text",
                    "relevance_score": 0.8,
                    "reasoning": "",
                    **segment,
                }
            ],
            tmp_path / "clips",
            cleanup_settings=cleanup,
        )
    return infos[0], rendered[0]


def test_clip_creation_snaps_start_and_extends_end(video_path, tmp_path, monkeypatch):
    info, ranges = _render_segments(video_path, tmp_path, monkeypatch, {})
    assert ranges == [(pytest.approx(10.6), pytest.approx(32.55))]
    assert info["keep_ranges"] == ranges
    # Text follows the snapped/extended timeline, not the raw labels.
    assert info["text"].startswith("So here is the thing.")
    assert info["text"].endswith("And that changed everything for me.")


def test_clip_creation_teaser_comes_first_with_matching_text(
    video_path, tmp_path, monkeypatch
):
    info, ranges = _render_segments(
        video_path,
        tmp_path,
        monkeypatch,
        {"hook_start_time": "00:20", "hook_end_time": "00:21"},
    )
    assert ranges[0] == (pytest.approx(19.92), pytest.approx(21.85))
    assert ranges[1][0] == pytest.approx(10.6)
    assert info["text"].startswith("I lost forty thousand dollars. So here is the thing.")
    assert info["text"].endswith("And that changed everything for me.")


def test_start_is_snapped_before_cleanup_so_removed_filler_stays_out(
    tmp_path, monkeypatch
):
    video = tmp_path / "filler.mp4"
    video.with_suffix(".transcript_cache.json").write_text(
        json.dumps(
            {
                "version": 2,
                "language_code": "en",
                "utterances": [],
                "words": _words(
                    ("Okay.", 9.0, 9.4),
                    ("um", 10.05, 10.3),
                    ("so", 10.4, 10.6),
                    ("this", 10.6, 10.9),
                    ("works.", 10.9, 31.0),
                ),
            }
        ),
        encoding="utf-8",
    )
    _, ranges = _render_segments(
        video, tmp_path, monkeypatch, {}, cleanup={"remove_filler_words": True}
    )
    assert ranges[0][0] == pytest.approx(10.3)


def test_supplied_ranges_are_not_start_snapped(video_path, tmp_path):
    captured = []

    def fake_render(video, ranges, output):
        captured.append(ranges)
        return False

    with patch.object(video_utils, "render_source_ranges_ffmpeg", fake_render), patch(
        "src.media.timeline.ffprobe_duration", return_value=60.0
    ):
        video_utils.create_optimized_clip(
            video_path, 13.0, 32.0, tmp_path / "out.mp4", keep_ranges=[(13.0, 32.0)]
        )
        video_utils.create_optimized_clip(video_path, 13.0, 32.0, tmp_path / "out.mp4")
    assert captured[0][0][0] == 13.0
    assert captured[1][0][0] == pytest.approx(11.9)


def test_teaser_keeps_compressed_pauses(tmp_path):
    video = tmp_path / "pause.mp4"
    video.with_suffix(".transcript_cache.json").write_text(
        json.dumps(
            {
                "version": 2,
                "language_code": "en",
                "utterances": [],
                "words": _words(
                    ("Intro", 0.0, 0.5),
                    ("words", 0.5, 9.0),
                    ("I", 10.0, 10.2),
                    ("lost", 10.2, 10.6),
                    ("everything", 12.0, 12.6),
                    ("that", 12.6, 12.8),
                    ("year.", 12.8, 13.3),
                    ("Then", 14.0, 30.0),
                ),
            }
        ),
        encoding="utf-8",
    )
    body = timeline.build_clip_keep_ranges(video, 0.0, 30.0, {"cut_long_pauses": True})
    ranges = timeline.add_hook_teaser_to_keep_ranges(video, body, 10.0, 13.0)
    teaser = ranges[: len(ranges) - len(body)]
    assert len(teaser) == 2  # the 1.4s pause inside the hook is compressed
    assert teaser[0][0] == pytest.approx(9.92)
    assert teaser[-1][1] == pytest.approx(13.45)
    assert ranges[len(teaser):] == body
    assert crossfade_fade_for_ranges(ranges) == 0.0
    words = timeline.get_words_for_keep_ranges(
        timeline.load_cached_transcript_data(video), ranges
    )
    assert [word["text"] for word in words[:6]] == ["I", "lost", "everything", "that", "year.", "Intro"]
    starts = [word["start"] for word in words]
    assert starts == sorted(starts)


def test_teaser_keeps_cleanup_cuts(video_path):
    body = [(10.6, 20.45), (20.6, 32.55)]
    ranges = timeline.add_hook_teaser_to_keep_ranges(video_path, body, 20.0, 21.0)
    assert ranges[:2] == [
        (pytest.approx(19.92), 20.45),
        (20.6, pytest.approx(21.85)),
    ]
    assert ranges[2:] == body


def test_teaser_is_word_aligned_and_played_first(video_path):
    ranges = timeline.add_hook_teaser_to_keep_ranges(video_path, [(10.6, 32.55)], 20.0, 21.0)
    assert ranges[0] == (pytest.approx(19.92), pytest.approx(21.85))
    assert ranges[1] == (10.6, 32.55)


def test_teaser_trims_to_max_length_on_sentence_end(transcript_data):
    teaser = timeline.build_hook_teaser_range(transcript_data, 5.0, 40.0, 12.0, 22.0)
    # "Most people ... first." fits in 5s; the next sentence would not.
    assert teaser == (pytest.approx(11.92), pytest.approx(14.95))


@pytest.mark.parametrize(
    "body,hook",
    [
        ((10.6, 32.5), (11.0, 13.0)),  # inside the opening 3 seconds
        ((5.0, 32.5), (21.0, 22.0)),  # "It was brutal." is too short
        ((5.0, 18.0), (20.0, 21.0)),  # outside the clip body
        ((5.0, 32.5), (21.0, 20.0)),  # inverted
    ],
)
def test_teaser_rejects_invalid_ranges(transcript_data, body, hook):
    assert timeline.build_hook_teaser_range(transcript_data, *body, *hook) is None


def test_teaser_not_added_twice(video_path):
    ranges = [(19.92, 21.85), (10.6, 32.55)]
    assert timeline.add_hook_teaser_to_keep_ranges(video_path, ranges, 20.0, 21.0) == ranges


def test_segment_teaser_respects_config_flag(video_path, monkeypatch):
    segment = {"hook_start_time": "00:20", "hook_end_time": "00:21"}
    monkeypatch.setattr(
        timeline, "get_config", lambda: SimpleNamespace(hook_first_teaser=False)
    )
    assert timeline.apply_segment_hook_teaser(video_path, [(10.6, 32.55)], segment) == [
        (10.6, 32.55)
    ]
    monkeypatch.setattr(
        timeline, "get_config", lambda: SimpleNamespace(hook_first_teaser=True)
    )
    with_teaser = timeline.apply_segment_hook_teaser(video_path, [(10.6, 32.55)], segment)
    assert len(with_teaser) == 2
    malformed = {"hook_start_time": "soon", "hook_end_time": "00:21"}
    assert timeline.apply_segment_hook_teaser(video_path, [(10.6, 32.55)], malformed) == [
        (10.6, 32.55)
    ]


def test_caption_words_follow_teaser_order(transcript_data):
    ranges = [(19.92, 21.85), (10.6, 32.55)]
    words = timeline.get_words_for_keep_ranges(transcript_data, ranges)
    texts = [word["text"] for word in words]
    assert texts[:6] == ["I", "lost", "forty", "thousand", "dollars.", "So"]
    starts = [word["start"] for word in words]
    assert starts == sorted(starts)
    # Hard cut out of the teaser keeps source maps (plain concatenation) exact.
    assert crossfade_fade_for_ranges(ranges) == 0.0
    assert crossfade_fade_for_ranges([(10.6, 20.45), (20.6, 32.55)]) > 0
    assert words[5]["start"] == pytest.approx((21.85 - 19.92) + (10.7 - 10.6))


def test_source_map_round_trip_keeps_teaser_order(tmp_path):
    clip = tmp_path / "clip.mp4"
    ranges = [(19.92, 21.85), (10.6, 32.55)]
    save_clip_source_ranges(clip, ranges)
    assert load_clip_source_ranges(clip) == ranges
    assert source_range_bounds(ranges) == (10.6, 32.55)


@pytest.mark.parametrize(
    "hook,expected",
    [
        (("00:20", "00:22"), ("00:20", "00:22")),
        (("00:05", "00:07"), (None, None)),  # before the segment
        (("00:20", "00:45"), (None, None)),  # too long / past the end
        (("00:22", "00:20"), (None, None)),
        (("bad", "00:22"), (None, None)),
        ((None, None), (None, None)),
    ],
)
def test_ai_sanitizes_hook_span(hook, expected):
    segment = ai.TranscriptSegment(
        start_time="00:10",
        end_time="00:40",
        text="words words words",
        hook_start_time=hook[0],
        hook_end_time=hook[1],
    )
    ai._sanitize_hook_span(segment, 10, 40)
    assert (segment.hook_start_time, segment.hook_end_time) == expected
