"""Clip ends, pause cleanup and stored durations, built from audited clips.

The word timings are copied from the 2026-10-27 clip-quality audit
transcripts (AssemblyAI, milliseconds): Fortnite clip 1 (#280, #281, #285),
the React outro (#280) and the 60s Huberman clip (#280).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from src import video_utils
from src.media import gap_activity, timeline
from src.media.common import (
    CLIP_END_NEXT_WORD_GUARD_SECONDS,
    CLIP_END_PADDING_SECONDS,
    PAUSE_EDGE_PADDING_SECONDS,
)
from src.media.ffmpeg import (
    crossfade_fade_for_ranges,
    ffprobe_duration,
    output_offsets_for_ranges,
    render_source_ranges_ffmpeg,
    rendered_clip_duration,
    rendered_ranges_duration,
)

needs_ffmpeg = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="ffmpeg required"
)


def _ms_words(*items):
    return [
        {"text": text, "start": start, "end": end, "confidence": 0.99}
        for text, start, end in items
    ]


# Fortnite "DOUBLE PUMP PUSHER", clip 1 ("Traps are a double-edged sword").
FORTNITE_WORDS = _ms_words(
    ("I'll", 746443, 746525), ('just', 746525, 746641), ('shoot', 746641, 746839),
    ('the', 746839, 747005), ('last', 747005, 747236), ('one.', 747335, 747484),
    ('Oh,', 748426, 748476), ('there', 748492, 748608), ('he', 748608, 748823),
    ('is.', 748823, 748889), ('Say', 756376, 756538), ('this', 756538, 756765),
    ("won't", 756765, 756911), ('hit,', 756928, 757090), ('baby.', 757106, 757366),
    ('Say', 757577, 757837), ('this', 757837, 757999), ("won't", 757999, 758162),
    ('hit', 758162, 758308), ('that', 758324, 758551), ('dude.', 758551, 758697),
    ('Say', 759201, 759461), ('it', 759461, 759607), ("won't.", 759607, 759931),
    ('Say', 760435, 760678), ('it', 760678, 760743), ("won't.", 760906, 761295),
    ('All', 761344, 761376), ('right,', 761393, 761555), ("it's", 761571, 761717),
    ('not.', 761717, 761847), ('Feels', 764300, 765496),
)

# Fireship "React in 100 Seconds", clip 1 ended "...today. This has been".
REACT_WORDS = _ms_words(
    ('is', 107790, 108050), ('one', 108050, 108210), ('of', 108210, 108250),
    ('the', 108250, 108330), ('most', 108330, 108610), ('in-demand', 108610, 108950),
    ('skills', 108950, 109350), ('for', 109350, 109630), ('frontend', 109630, 109870),
    ('developers', 109870, 110290), ('today.', 110290, 110910),
    ('This', 110910, 111030), ('has', 111030, 111230), ('been', 111230, 111350),
    ('React', 111350, 111710), ('in', 111710, 111970), ('100', 111970, 112310),
    ('Seconds.', 112310, 112910), ('If', 112910, 113010),
)

# Huberman "Master Your Sleep", the 60s clip that ended "...and when you want".
HUBERMAN_WORDS = _ms_words(
    ('we', 250559, 250688), ('want', 250704, 250848), ('to', 250880, 250977),
    ('be', 250977, 251121), ('sleepy', 251121, 251490), ('and', 251683, 251747),
    ('when', 251828, 251956), ('we', 252020, 252133), ('want', 252165, 252293),
    ('to', 252390, 252454), ('be', 252486, 252566), ('awake.', 252566, 252871),
    ('That', 253513, 253642), ('block', 253706, 254156), ('of', 254156, 254204),
    ('sleep', 254236, 254798), ('and', 255296, 255360), ('when', 255601, 255729),
    ('it', 255842, 255890), ('falls', 255938, 256307), ('within', 256323, 256628),
    ('each', 256725, 256869), ('24-hour', 256901, 257624), ('cycle', 257624, 258090),
    ('is', 258523, 258636), ('governed', 258732, 259069), ('by', 259069, 259262),
    ('a', 259615, 259631), ('number', 259711, 260032), ('of', 260032, 260113),
    ('different', 260113, 260498), ('things.', 260498, 260803), ('But', 261783, 261879),
    ('the', 261895, 261959), ('most', 261959, 262200), ('powerful', 262200, 262682),
    ('thing', 262682, 262810), ("that's", 262842, 263067),
    ('governing', 263083, 263453), ('when', 263629, 263774), ('you', 263870, 263950),
    ('want', 264031, 264207), ('to', 264207, 264304), ('be', 264304, 264432),
    ('asleep', 264432, 264866), ('and', 264930, 264994), ('when', 265010, 265155),
    ('you', 265155, 265251), ('want', 265315, 265572), ('to', 265572, 265652),
    ('be', 265652, 265813), ('awake', 265813, 266150), ('is', 267001, 267146),
    ('light.', 267579, 267804),
)

# The audited cleanup settings (long-pause removal at 1,200ms, filler removal on).
AUDIT_CLEANUP = {
    "cut_long_pauses": True,
    "pause_threshold_ms": 1200,
    "remove_filler_words": True,
    "filtered_words": [],
}


def _video(tmp_path, words, name="source.mp4"):
    path = tmp_path / name
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "words": words, "utterances": [], "language_code": "en"}),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def long_source(monkeypatch):
    monkeypatch.setattr(timeline, "ffprobe_duration", lambda _path: 3600.0)


# --- #280: clip ends ---------------------------------------------------------


def test_end_padding_stops_before_the_next_sentence(tmp_path, long_source):
    # Production ended at 761.645 ("won't." + 0.35s), inside "All right, it's".
    video = _video(tmp_path, FORTNITE_WORDS)
    ranges = timeline.extend_keep_ranges_to_sentence_boundary(video, [(756.101, 761.0)])

    assert ranges == [(756.101, pytest.approx(761.344 - CLIP_END_NEXT_WORD_GUARD_SECONDS))]
    text = timeline.keep_ranges_transcript_text(video, ranges)
    assert text.endswith("Say it won't. Say it won't.")
    assert "All" not in text


def test_end_padding_does_not_leak_the_outro(tmp_path, long_source):
    # "today." is followed immediately by "This has been React in 100 Seconds."
    video = _video(tmp_path, REACT_WORDS)
    ranges = timeline.extend_keep_ranges_to_sentence_boundary(video, [(107.7, 110.5)])

    assert ranges == [(107.7, pytest.approx(110.91))]
    assert timeline.keep_ranges_transcript_text(video, ranges).endswith("developers today.")


def test_end_padding_is_unchanged_when_the_next_word_is_far(tmp_path, long_source):
    video = _video(tmp_path, HUBERMAN_WORDS)
    ranges = timeline.extend_keep_ranges_to_sentence_boundary(video, [(253.5, 260.6)])
    assert ranges == [(253.5, pytest.approx(260.803 + CLIP_END_PADDING_SECONDS))]


def test_fragment_just_after_a_finished_sentence_is_dropped(tmp_path, long_source):
    # The next sentence starts 50ms after the payoff and never finishes inside
    # the extension window; a range ending 0.3s later used to keep "Before".
    video = _video(
        tmp_path,
        _ms_words(
            ("That", 10000, 10300), ("move", 10300, 10600), ("is", 10600, 10800),
            ("the", 10800, 11000), ("whole", 11000, 11300), ("game.", 11300, 11800),
            ("Before", 11850, 12100), ("we", 12100, 12300), ("started", 12900, 13300),
            ("recording", 13300, 13800),
        ),
    )
    ranges = timeline.extend_keep_ranges_to_sentence_boundary(video, [(10.0, 12.1)])
    assert ranges == [(10.0, pytest.approx(11.85 - CLIP_END_NEXT_WORD_GUARD_SECONDS))]


def test_duration_cap_backs_off_to_the_last_full_sentence(tmp_path):
    # 205.326 + 60s lands in "...when you want to be asleep and when you want".
    video = _video(tmp_path, HUBERMAN_WORDS)
    ranges = timeline.clamp_keep_ranges_to_max_duration(video, [(205.326, 267.9)], 60.0)

    assert ranges == [(205.326, pytest.approx(260.803 + CLIP_END_PADDING_SECONDS))]
    assert timeline.keep_ranges_transcript_text(video, ranges).endswith(
        "governed by a number of different things."
    )


def test_duration_cap_backs_off_only_a_share_of_the_cap(tmp_path):
    # With a 15s cap the full stop 4.5s back is too far; end on the last word,
    # still clear of the next one.
    video = _video(tmp_path, HUBERMAN_WORDS)
    ranges = timeline.clamp_keep_ranges_to_max_duration(video, [(250.326, 267.9)], 15.0)
    assert ranges == [(250.326, pytest.approx(265.315 - CLIP_END_NEXT_WORD_GUARD_SECONDS))]


# --- #281: pause cleanup keeps action -----------------------------------------


def test_pause_with_action_is_not_cut(tmp_path, monkeypatch):
    # 749.164-756.101 is sustained gunfire with no words.
    video = _video(tmp_path, FORTNITE_WORDS)
    calls = []

    def fake_action_ranges(path, gaps, speech):
        calls.append(gaps)
        return [[gap] for gap in gaps]

    monkeypatch.setattr(timeline, "active_gap_ranges", fake_action_ranges)
    ranges = timeline.build_clip_keep_ranges(video, 746.168, 761.314, AUDIT_CLEANUP)

    assert calls == [[(pytest.approx(748.889), pytest.approx(756.376))]]
    assert ranges == [(746.168, 761.314)]


def test_still_silent_pause_is_still_compressed(tmp_path, monkeypatch):
    video = _video(tmp_path, FORTNITE_WORDS)
    monkeypatch.setattr(timeline, "active_gap_ranges", lambda _p, gaps, _s: [[] for _ in gaps])
    ranges = timeline.build_clip_keep_ranges(video, 746.168, 761.314, AUDIT_CLEANUP)

    # The production cut for this clip.
    pad = PAUSE_EDGE_PADDING_SECONDS
    assert ranges == [
        (746.168, pytest.approx(748.889 + pad)),
        (pytest.approx(756.376 - pad), 761.314),
    ]


def _synthetic_source(tmp_path, name, video_filter, gap_audio):
    """7s clip: tone "speech" at 0-2s and 5-7s, ``gap_audio`` in between."""
    path = tmp_path / name
    audio = (
        f"0.3*sin(2*PI*220*t)*(lt(t,2)+gte(t,5))+({gap_audio})*gte(t,2)*lt(t,5)"
    )
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", f"{video_filter}",
            "-f", "lavfi", "-i", f"aevalsrc='{audio}':s=16000:d=7",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(path),
        ],
        check=True,
    )
    return path


STILL = "color=c=gray:s=160x90:r=24:d=7"
MOVING = "nullsrc=s=160x90:r=24:d=7,format=gray,geq=lum='random(1)*255'"
SPEECH = [(0.1, 1.9), (5.1, 6.9)]
GAP = [(2.0, 5.0)]


@needs_ffmpeg
def test_still_and_silent_gap_is_dead_air(tmp_path):
    video = _synthetic_source(tmp_path, "still.mp4", STILL, "0")
    assert gap_activity.active_gaps(video, GAP, SPEECH) == [False]


@needs_ffmpeg
def test_loud_gap_counts_as_action(tmp_path):
    # Gunfire-like noise as loud as the speech, over a still picture.
    video = _synthetic_source(tmp_path, "loud.mp4", STILL, "0.5*(random(0)*2-1)")
    assert gap_activity.active_gaps(video, GAP, SPEECH) == [True]


@needs_ffmpeg
def test_moving_picture_counts_as_action(tmp_path):
    video = _synthetic_source(tmp_path, "moving.mp4", MOVING, "0")
    assert gap_activity.gap_motion_score(video, 2.2, 4.8) > gap_activity.GAP_MOTION_THRESHOLD
    assert gap_activity.active_gaps(video, GAP, SPEECH) == [True]


def test_gap_check_without_media_keeps_old_behaviour(tmp_path):
    assert gap_activity.active_gaps(tmp_path / "missing.mp4", GAP, SPEECH) == [False]


def test_gap_check_failure_never_fails_the_clip(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"not a video")

    def broken(*_args, **_kwargs):
        raise RuntimeError("decoder exploded")

    monkeypatch.setattr(gap_activity, "select_audio_stream_index", broken)
    monkeypatch.setattr(gap_activity, "gap_motion_score", broken)
    assert gap_activity.active_gaps(str(video), GAP, SPEECH) == [False]

    monkeypatch.setattr(gap_activity, "_active_gaps", broken)
    assert gap_activity.active_gaps(video, GAP + GAP, SPEECH) == [False, False]


def test_long_gap_motion_check_samples_only_the_middle(tmp_path, monkeypatch):
    video = tmp_path / "source.mp4"
    video.write_bytes(b"")
    monkeypatch.setattr(gap_activity, "select_audio_stream_index", lambda _p: None)
    sampled = []

    def fake_motion(_path, start, end):
        sampled.append((start, end))
        return 0.0

    monkeypatch.setattr(gap_activity, "gap_motion_score", fake_motion)
    assert gap_activity.active_gaps(video, [(10.0, 70.0)], [(0.0, 10.0)]) == [False]
    assert sampled == [
        (pytest.approx(36.0), pytest.approx(44.0))
    ]


# --- #285: stored duration ----------------------------------------------------

# Fortnite clip 1's stored source map: 19.594s of source, a 19.1s MP4.
FORTNITE_CLIP1_RANGES = [
    (717.967, 724.965),
    (727.125, 729.365),
    (734.55, 736.366),
    (746.168, 749.164),
    (756.101, 761.645),
]


def test_rendered_duration_subtracts_every_crossfade():
    fade = crossfade_fade_for_ranges(FORTNITE_CLIP1_RANGES)
    assert fade == pytest.approx(0.15)
    assert rendered_ranges_duration(FORTNITE_CLIP1_RANGES) == pytest.approx(19.594 - 4 * fade)
    offsets = output_offsets_for_ranges(FORTNITE_CLIP1_RANGES)
    assert offsets[:2] == [0.0, pytest.approx(6.998 - fade)]


def test_rendered_duration_of_a_teaser_is_a_hard_cut():
    ranges = [(30.0, 32.0), (20.0, 40.0)]
    assert rendered_ranges_duration(ranges) == pytest.approx(22.0)


def test_caption_words_use_the_same_mapping(tmp_path):
    video = _video(tmp_path, FORTNITE_WORDS)
    transcript = timeline.load_cached_transcript_data(video)
    ranges = FORTNITE_CLIP1_RANGES[-2:]
    words = timeline.get_words_for_keep_ranges(transcript, ranges)
    say = next(word for word in words if word["text"] == "Say")
    assert say["start"] == pytest.approx(
        output_offsets_for_ranges(ranges)[1] + (756.376 - 756.101)
    )


@needs_ffmpeg
def test_rendered_clip_matches_the_stored_duration(tmp_path):
    source = tmp_path / "source.mp4"
    subprocess.run(
        [
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc=s=160x90:r=30:d=12",
            "-f", "lavfi", "-i", "sine=f=220:d=12",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", str(source),
        ],
        check=True,
    )
    ranges = [(1.0, 4.0), (5.0, 7.5), (8.0, 11.0)]
    output = tmp_path / "clip.mp4"
    assert render_source_ranges_ffmpeg(source, ranges, output)

    actual = ffprobe_duration(output)
    assert sum(end - start for start, end in ranges) - actual > 0.25
    assert rendered_ranges_duration(ranges) == pytest.approx(actual, abs=0.07)
    assert rendered_clip_duration(output, ranges) == pytest.approx(actual)


@needs_ffmpeg
def test_generated_clip_stores_the_probed_file_length(tmp_path, monkeypatch):
    def fake_create(video_path, start, end, output_path, *args, **kwargs):
        subprocess.run(
            [
                "ffmpeg", "-v", "error", "-y", "-f", "lavfi",
                "-i", "color=c=black:s=64x64:r=30:d=2.5", "-pix_fmt", "yuv420p",
                str(output_path),
            ],
            check=True,
        )
        return True

    monkeypatch.setattr(video_utils, "build_clip_keep_ranges", lambda *_a, **_k: [(10.0, 11.0), (12.0, 14.0)])
    monkeypatch.setattr(video_utils, "create_optimized_clip", fake_create)
    clips = video_utils.create_clips_from_segments(
        Path(tmp_path / "source.mp4"),
        [
            {
                "start_time": "00:10",
                "end_time": "00:20",
                "text": "hello",
                "relevance_score": 0.8,
                "reasoning": "",
            }
        ],
        tmp_path / "clips",
        add_subtitles=False,
    )
    assert clips[0]["duration"] == pytest.approx(2.5)
