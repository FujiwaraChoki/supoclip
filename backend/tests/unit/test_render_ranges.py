"""Render range resolution: the legacy golden and locked planned segments."""

import json
from types import SimpleNamespace

import pytest

from src import video_utils
from src.media import render_ranges
from src.media import timeline
from src.services import video_service
from src.services.video_service import VideoService


def _words(*items):
    return [
        {"text": text, "start": int(start * 1000), "end": int(end * 1000), "confidence": 0.99}
        for text, start, end in items
    ]


WORDS = _words(
    ("That's", 9.6, 10.0), ("it.", 10.1, 10.4),
    ("So", 10.7, 10.9), ("here", 10.9, 11.1), ("is", 11.1, 11.2), ("the", 11.2, 11.3), ("thing.", 11.3, 11.7),
    ("Most", 12.0, 12.3), ("people", 12.3, 12.6), ("um", 12.7, 12.9), ("never", 13.6, 13.9),
    ("save", 13.9, 14.2), ("money", 14.2, 14.5), ("because", 14.5, 14.8), ("they", 14.8, 15.0),
    ("spend", 15.0, 15.3), ("first.", 15.3, 15.8),
    ("I", 20.0, 20.2), ("lost", 20.2, 20.5), ("forty", 20.5, 20.8), ("thousand", 20.8, 21.2),
    ("dollars.", 21.2, 21.7), ("It", 21.9, 22.0), ("was", 22.0, 22.2), ("brutal.", 22.2, 22.6),
    ("And", 30.0, 30.3), ("that", 30.3, 30.6), ("changed", 30.6, 31.0), ("everything", 31.0, 31.6),
    ("for", 31.6, 31.8), ("me.", 31.8, 32.2),
    ("Then", 33.5, 33.8), ("we", 33.8, 34.0), ("rebuilt", 34.0, 34.5), ("it", 34.5, 34.6),
)

CLEANUP = {"cut_long_pauses": True, "pause_threshold_ms": 500, "remove_filler_words": True, "filtered_words": []}

# name -> (segment, cleanup, cap, teaser on)
LEGACY_CASES = {
    "raw": ({"start_time": "00:13", "end_time": "00:32"}, None, 60.0, False),
    "raw_teaser": (
        {"start_time": "00:13", "end_time": "00:32", "hook_start_time": "00:20", "hook_end_time": "00:21"},
        None, 60.0, True,
    ),
    "raw_cleanup": ({"start_time": "00:10", "end_time": "00:32"}, CLEANUP, 60.0, False),
    "raw_capped": ({"start_time": "00:10", "end_time": "00:34"}, None, 8.0, False),
    "source_ranges": ({"start_time": "00:10", "end_time": "00:22", "source_ranges": [(10.6, 15.95), (19.9, 22.75)]},
                      CLEANUP, 60.0, False),
    "keep_ranges": ({"start_time": "00:10", "end_time": "00:22", "keep_ranges": [(10.6, 15.95), (19.9, 22.75)]},
                    None, 60.0, False),
}

# Captured from create_single_clip / create_clips_from_segments before the
# range block moved into media/render_ranges.py.
LEGACY_GOLDEN = {
    "raw": {
        "keep": [[11.9, 32.55]],
        "text": "Most people um never save money because they spend first. I lost forty thousand dollars. "
                "It was brutal. And that changed everything for me.",
    },
    "raw_teaser": {
        "keep": [[19.92, 21.85], [11.9, 32.55]],
        "text": "I lost forty thousand dollars. Most people um never save money because they spend first. "
                "I lost forty thousand dollars. It was brutal. And that changed everything for me.",
    },
    "raw_cleanup": {
        "keep": [[10.6, 12.7], [13.325, 16.075], [19.725, 22.875], [29.725, 32.55]],
        "text": "So here is the thing. Most people never save money because they spend first. "
                "I lost forty thousand dollars. It was brutal. And that changed everything for me.",
    },
    "raw_capped": {
        "keep": [[10.6, 16.15]],
        "text": "So here is the thing. Most people um never save money because they spend first.",
    },
    "source_ranges": {"keep": [[10.6, 12.7], [13.325, 15.95], [19.9, 22.75]], "text": "Stored text."},
    "keep_ranges": {"keep": [[10.6, 15.95], [19.9, 22.75]], "text": "Stored text."},
}


@pytest.fixture
def video_path(tmp_path, monkeypatch):
    path = tmp_path / "source.mp4"
    path.touch()
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "language_code": "en", "words": WORDS, "utterances": []}), encoding="utf-8"
    )
    monkeypatch.setattr("src.media.timeline.ffprobe_duration", lambda _path: 120.0)
    monkeypatch.setattr("src.media.timeline.active_gap_ranges", lambda _video, gaps, _words: [[] for _ in gaps])
    monkeypatch.setattr("src.media.ffmpeg.ffprobe_duration", lambda _path: 0.0)
    return path


def _segment(base):
    return {"text": "Stored text.", "relevance_score": 0.5, "reasoning": "", **base}


def _rounded(ranges):
    return [[round(start, 3), round(end, 3)] for start, end in ranges]


async def _render_both(video_path, tmp_path, monkeypatch, name):
    base, cleanup, cap, teaser = LEGACY_CASES[name]
    monkeypatch.setattr(timeline, "get_config", lambda: SimpleNamespace(hook_first_teaser=teaser))
    rendered = []

    def fake_create(*args, **kwargs):
        rendered.append(args[10])
        args[3].touch()
        return True

    monkeypatch.setattr(video_service, "create_optimized_clip", fake_create)
    monkeypatch.setattr(video_utils, "create_optimized_clip", fake_create)
    single = await VideoService.create_single_clip(
        video_path, _segment(base), 0, tmp_path, cleanup_settings=cleanup, max_clip_seconds=cap
    )
    batch = video_utils.create_clips_from_segments(
        video_path, [_segment(base)], tmp_path, cleanup_settings=cleanup, max_clip_seconds=cap
    )
    return single, batch[0], rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("name", sorted(LEGACY_CASES))
async def test_legacy_ranges_match_the_golden_render(video_path, tmp_path, monkeypatch, name):
    single, batch, rendered = await _render_both(video_path, tmp_path, monkeypatch, name)
    assert _rounded(rendered[0]) == _rounded(rendered[1])
    assert single["text"] == batch["text"]
    assert {"keep": _rounded(rendered[0]), "text": single["text"]} == LEGACY_GOLDEN[name]


@pytest.mark.parametrize("name", sorted(LEGACY_CASES))
def test_resolver_legacy_branch_matches_the_golden(video_path, monkeypatch, name):
    base, cleanup, cap, teaser = LEGACY_CASES[name]
    monkeypatch.setattr(timeline, "get_config", lambda: SimpleNamespace(hook_first_teaser=teaser))

    resolved = render_ranges.resolve_render_ranges(video_path, _segment(base), cleanup, cap)

    assert {"keep": _rounded(resolved.keep_ranges), "text": resolved.text} == LEGACY_GOLDEN[name]
    assert resolved.output_offsets is None
    assert resolved.group_output_starts == []


def test_resolver_legacy_bounds_carry_the_snapped_start(video_path, monkeypatch):
    monkeypatch.setattr(timeline, "get_config", lambda: SimpleNamespace(hook_first_teaser=False))

    resolved = render_ranges.resolve_render_ranges(video_path, _segment(LEGACY_CASES["raw"][0]), None, 60.0)

    assert resolved.bounds == (pytest.approx(11.9), 32.0)


def _planned(ranges, **extra):
    return _segment({
        "start_time": "00:00", "end_time": "00:01", "source_ranges": ranges, "planned": True,
        "junction_fades": [0.1] * (len(ranges) - 1), "max_seconds": 60.0, **extra,
    })


def test_planned_end_is_not_extended_to_the_sentence_end(video_path):
    # Ends after "um", mid-sentence: highlights would run on to "first." at 15.8s.
    resolved = render_ranges.resolve_render_ranges(video_path, _planned([(11.9, 13.25)]), None, 60.0)

    assert resolved.keep_ranges == [(11.9, 13.25)]
    assert resolved.text == "Most people um"
    assert resolved.output_offsets == [0.0]
    assert resolved.group_output_starts == [0.0]


def test_planned_start_is_not_snapped_and_gets_no_teaser(video_path, monkeypatch):
    monkeypatch.setattr(timeline, "get_config", lambda: SimpleNamespace(hook_first_teaser=True))
    segment = _planned([(13.5, 15.95)], hook_start_time="00:20", hook_end_time="00:21")

    resolved = render_ranges.resolve_render_ranges(video_path, segment, None, 60.0)

    assert resolved.keep_ranges == [(13.5, 15.95)]
    assert resolved.text == "never save money because they spend first."


def test_planned_cjk_end_is_not_extended(tmp_path, monkeypatch):
    video = tmp_path / "zh.mp4"
    video.with_suffix(".transcript_cache.json").write_text(json.dumps({
        "version": 2, "language_code": "zh", "utterances": [],
        "words": _words(("我们", 1.0, 1.3), ("今天", 1.3, 1.6), ("聊", 1.6, 1.8), ("钱", 1.8, 2.1),
                        ("你", 3.0, 3.2), ("好吗？", 3.2, 3.6)),
    }), encoding="utf-8")
    monkeypatch.setattr("src.media.timeline.ffprobe_duration", lambda _path: 10.0)

    resolved = render_ranges.resolve_render_ranges(video, _planned([(0.9, 2.45)]), None, 60.0)

    assert resolved.keep_ranges == [(0.9, 2.45)]
    # Written as Chinese is written: no spaces between words.
    assert resolved.text == "我们今天聊钱"


def test_planned_ranges_play_in_their_order_with_planned_fades(video_path):
    # A trailer: the payoff-free beat at 30s plays before the one at 20s.
    segment = _planned([(29.9, 32.55), (19.9, 21.87)], junction_fades=[0.04])

    resolved = render_ranges.resolve_render_ranges(video_path, segment, None, 60.0)

    assert resolved.keep_ranges == [(29.9, 32.55), (19.9, 21.87)]
    assert resolved.output_offsets == [0.0, pytest.approx(2.65 - 0.04)]
    assert resolved.group_output_starts == [0.0, pytest.approx(2.61)]
    assert resolved.text == "And that changed everything for me. I lost forty thousand dollars."


def test_planned_cleanup_cuts_only_inside_each_range(video_path):
    segment = _planned([(11.9, 15.95), (19.9, 22.75)], junction_fades=[0.1])

    resolved = render_ranges.resolve_render_ranges(video_path, segment, CLEANUP, 60.0)

    # The filler "um" goes; both planned edges stay exactly where they were.
    assert resolved.keep_ranges[0][0] == 11.9
    assert resolved.keep_ranges[-1][1] == 22.75
    assert "um" not in resolved.text.split()
    assert len(resolved.group_output_starts) == 2
    first_group = [piece for piece in resolved.keep_ranges if piece[1] <= 15.95]
    assert resolved.group_output_starts[1] == pytest.approx(
        resolved.output_offsets[len(first_group)]
    )


def test_planned_cap_is_only_a_safety_net(video_path, caplog):
    segment = _planned([(11.9, 15.95), (19.9, 22.75)], max_seconds=5.0)

    resolved = render_ranges.resolve_render_ranges(video_path, segment, None, 60.0)

    assert "over its 5.0s cap" in caplog.text
    assert render_ranges.rendered_duration(resolved.keep_ranges, resolved.output_offsets) <= 5.05
    assert len(resolved.output_offsets) == len(resolved.keep_ranges)
    assert resolved.keep_ranges[0] == (11.9, 15.95)


def test_planned_cap_never_exceeds_the_hard_maximum(video_path):
    segment = _planned([(9.5, 34.95)], max_seconds=500.0)

    resolved = render_ranges.resolve_render_ranges(video_path, segment, None, 9.0)

    # Within the planner's cap and far below the 185s hard maximum: untouched.
    assert resolved.keep_ranges == [(9.5, 34.95)]


def test_regeneration_reuses_stored_ranges_offsets_and_groups(video_path):
    segment = _planned(
        [(11.9, 15.95), (19.9, 22.75)],
        keep_ranges=[(11.9, 12.7), (13.3, 15.95), (19.9, 22.75)],
        output_offsets=[0.0, 0.7, 3.25],
        range_groups=[0, 0, 1],
    )

    resolved = render_ranges.resolve_render_ranges(video_path, segment, CLEANUP, 60.0)

    assert resolved.keep_ranges == [(11.9, 12.7), (13.3, 15.95), (19.9, 22.75)]
    assert resolved.output_offsets == [0.0, 0.7, 3.25]
    assert resolved.group_output_starts == [0.0, 3.25]


def test_regeneration_with_mismatched_offsets_falls_back_to_hard_cuts(video_path):
    segment = _planned([], keep_ranges=[(11.9, 12.7), (19.9, 22.75)], output_offsets=[0.0])

    resolved = render_ranges.resolve_render_ranges(video_path, segment, None, 60.0)

    assert resolved.output_offsets == [0.0, pytest.approx(0.8)]
    assert resolved.group_output_starts == [0.0, pytest.approx(0.8)]


def test_output_offsets_for_junctions_mix_planned_and_cleanup_fades():
    keep = [(10.0, 12.0), (12.5, 15.0), (20.0, 21.0), (30.0, 30.06)]
    groups = [0, 0, 1, 2]

    offsets = render_ranges.output_offsets_for_junctions(keep, groups, [0.1, 0.04])

    intra = render_ranges.crossfade_fade_for_ranges([(10.0, 12.0), (12.5, 15.0)])
    assert offsets[0] == 0.0
    assert offsets[1] == pytest.approx(2.0 - intra)
    assert offsets[2] == pytest.approx(offsets[1] + 2.5 - 0.1)
    # A fade never exceeds half of the shorter side (0.06s range -> 0.03s).
    assert offsets[3] == pytest.approx(offsets[2] + 1.0 - 0.03)


def test_cumulative_offsets_and_rendered_duration():
    keep = [(1.0, 2.0), (5.0, 7.5)]
    assert render_ranges.cumulative_offsets(keep) == [0.0, 1.0]
    assert render_ranges.rendered_duration(keep, [0.0, 0.9]) == pytest.approx(3.4)
    with pytest.raises(ValueError):
        render_ranges.rendered_duration(keep, [0.0])


@pytest.mark.parametrize("cleanup", [None, CLEANUP])
def test_adjacent_series_parts_share_one_cut_after_rendering(video_path, cleanup):
    """I2 at the render: no word plays twice and none goes missing across parts."""
    from src.media.timeline import get_words_for_keep_ranges, load_cached_transcript_data
    from src.workflows.units import build_units

    transcript = load_cached_transcript_data(video_path)
    index = build_units(transcript, "")
    middle = len(index.units) // 2
    parts = [(1, middle), (middle + 1, len(index.units))]
    played = []
    for first, last in parts:
        segment = _planned([list(item) for item in index.ranges_for(first, last)])
        resolved = render_ranges.resolve_render_ranges(video_path, segment, cleanup, 60.0)
        played.append([
            (word["text"], round(word["end"] - word["start"], 3))
            for word in get_words_for_keep_ranges(transcript, resolved.keep_ranges, resolved.output_offsets)
        ])

    all_words = [word["text"] for word in timeline.get_absolute_words_in_range(transcript, 0.0, 60.0)]
    spoken = [text for part in played for text, _ in part]
    removed = {"um"} if cleanup else set()
    assert spoken == [text for text in all_words if text not in removed]
