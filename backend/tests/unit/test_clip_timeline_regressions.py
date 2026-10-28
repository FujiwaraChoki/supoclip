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
from src.media import timeline
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

def _video(tmp_path, words, name="source.mp4"):
    path = tmp_path / name
    path.with_suffix(".transcript_cache.json").write_text(
        json.dumps({"version": 2, "words": words, "utterances": [], "language_code": "en"}),
        encoding="utf-8",
    )
    return path


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
