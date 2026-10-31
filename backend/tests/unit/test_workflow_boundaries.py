import json
import random
from collections import Counter

import pytest

from src.media import timeline
from src.media.ffmpeg import rendered_ranges_duration
from src.workflows.boundaries import (
    STRONG_BOUNDARY,
    ends_for_length,
    kept_runs,
    nearest_sentence_start,
    nearest_strong_end,
    strong_ends_around,
)
from src.workflows.units import build_units
from src.workflows.planning import rounded_ranges

CLEANUP_ON = {"cut_long_pauses": True, "pause_threshold_ms": 1200, "remove_filler_words": True, "filtered_words": []}
FILLERS = {"um", "uh"}


def _words(*items):
    return [
        {
            "text": item[0],
            "start": int(round(item[1] * 1000)),
            "end": int(round(item[2] * 1000)),
            "confidence": 0.99,
            "speaker": item[3] if len(item) > 3 else None,
        }
        for item in items
    ]


def _data(words, language_code="en"):
    return {"version": 2, "words": words, "utterances": [], "language_code": language_code}


def _strengths(index):
    return [index.boundary_strength(unit.id) for unit in index.units]


def test_boundary_strength_table():
    words = _words(
        ("One.", 0.0, 0.5, "A"),  # sentence, gap 0 -> 0.75
        ("Two.", 0.5, 1.0, "A"),  # sentence, gap 2 s -> 1.0
        ("Three.", 3.0, 3.5, "A"),  # sentence, gap 0.4, next speaker differs -> 0.9
        ("Four", 3.9, 4.2, "B"), ("now", 4.25, 4.5, "B"),  # speaker -> 0.6
        ("Five", 4.6, 4.8, "A"), ("six", 4.85, 5.0, "A"),  # pause (1 s) -> 0.55
        ("Why", 6.0, 6.2, "A"), ("not?", 6.25, 6.5, "A"),  # sentence + question, gap 0.5 -> 0.8125 + 0.05
        ("End.", 7.0, 7.5, "A"),  # eof -> 1.0
    )
    index = build_units(_data(words), "")
    assert [unit.end_kind for unit in index.units] == ["sentence", "sentence", "sentence", "speaker", "pause", "sentence", "eof"]
    assert _strengths(index) == pytest.approx([0.75, 1.0, 0.9, 0.6, 0.55, 0.8625, 1.0])


def test_clause_and_forced_strengths():
    tokens = " ".join(f"w{n}" for n in range(15)) + ", " + " ".join(f"v{n}" for n in range(30)) + "."
    items, cursor = [], 0.0
    for token in tokens.replace(" ,", ",").split():
        items.append((token, cursor, cursor + 0.15))
        cursor += 0.17
    index = build_units(_data(_words(*items)), "")
    assert index.units[0].end_kind == "clause"
    assert index.boundary_strength(1) == pytest.approx(0.35)
    forced = build_units(_data(_words(*[(f"w{n}", n * 0.17, n * 0.17 + 0.15) for n in range(50)])), "")
    assert forced.units[0].end_kind == "forced"
    assert forced.boundary_strength(1) == pytest.approx(0.1)


def test_speech_seconds_without_cleanup_is_the_rendered_range_length():
    words = _words(("Hello", 1.0, 1.4), ("there.", 1.45, 1.9), ("Next", 5.0, 5.3), ("one.", 5.35, 5.8), ("Last.", 6.0, 6.5))
    index = build_units(_data(words), "")
    assert index.speech_seconds(1, 1) == pytest.approx(index.cut_out[1] - index.cut_in[1])
    assert index.speech_seconds(1, 1) == pytest.approx(0.9 + 0.1 + 0.35)
    assert index.speech_seconds(1, 3) == pytest.approx(index.cut_out[3] - index.cut_in[1])
    # Skips split the run: two ranges, each with its own padding.
    assert index.speech_seconds(1, 3, [(2, 2)]) == pytest.approx(
        (index.cut_out[1] - index.cut_in[1]) + (index.cut_out[3] - index.cut_in[3])
    )
    assert kept_runs(1, 5, [(2, 3)]) == [(1, 1), (4, 5)]
    assert kept_runs(1, 5, [(1, 2), (5, 7)]) == [(3, 4)]


@pytest.fixture
def cached_video(tmp_path):
    def write(words, language_code="en"):
        path = tmp_path / "source.mp4"
        path.with_suffix(".transcript_cache.json").write_text(json.dumps(_data(words, language_code)), encoding="utf-8")
        return path

    return write


def test_speech_seconds_with_cleanup_matches_the_cleanup_renderer(cached_video):
    words = _words(
        ("We", 0.0, 0.3), ("waited.", 0.35, 0.9),
        ("Then", 3.9, 4.2), ("it", 4.25, 4.4), ("came.", 4.45, 4.9),  # 3 s pause: compressed
        ("Fast.", 5.0, 5.5),
        ("Done.", 8.5, 9.0),  # another long pause
    )
    path = cached_video(words)
    index = build_units(_data(words), "")
    start, end = index.range_seconds(1, len(index))
    keep = timeline.build_clip_keep_ranges(path, start, end, CLEANUP_ON)
    assert len(keep) == 3
    assert index.speech_seconds(1, len(index), cleanup=CLEANUP_ON) == pytest.approx(rendered_ranges_duration(keep), abs=0.02)
    assert index.speech_seconds(1, len(index), cleanup=CLEANUP_ON) < index.speech_seconds(1, len(index)) - 4.5
    # Pause compression only: without it, cleanup changes nothing here.
    fillers_only = {**CLEANUP_ON, "cut_long_pauses": False}
    assert index.speech_seconds(1, len(index), cleanup=fillers_only) == pytest.approx(index.speech_seconds(1, len(index)))


def test_speech_seconds_removes_filler_words(cached_video):
    words = _words(("So", 0.0, 0.3), ("um", 0.4, 0.9), ("we", 1.0, 1.2), ("won.", 1.25, 1.6))
    path = cached_video(words)
    index = build_units(_data(words), "")
    plain = index.speech_seconds(1, 1)
    cleaned = index.speech_seconds(1, 1, cleanup=CLEANUP_ON)
    assert cleaned == pytest.approx(plain - 0.5)
    start, end = index.range_seconds(1, 1)
    keep = timeline.build_clip_keep_ranges(path, start, end, CLEANUP_ON)
    assert cleaned == pytest.approx(rendered_ranges_duration(keep), abs=0.2)
    # German "um" is a preposition: the German filler list leaves it alone.
    german = build_units(_data(words, "de"), "")
    assert german.speech_seconds(1, 1, cleanup=CLEANUP_ON) == pytest.approx(plain)


def test_cut_point_helpers():
    words = []
    cursor = 0.0
    for n, text in enumerate(["Alpha one.", "beta two", "Gamma three.", "delta four.", "epsilon five"]):
        for token in text.split():
            words.append((token, cursor, cursor + 0.4))
            cursor += 0.45
        cursor += 1.0 if n == 1 else 0.3  # unit 2 ends on a pause
    index = build_units(_data(_words(*words)), "")
    assert [unit.end_kind for unit in index.units] == ["sentence", "pause", "sentence", "sentence", "eof"]
    assert index.boundary_strength(2) < STRONG_BOUNDARY
    assert nearest_strong_end(index, 2, 2) == 1  # ties go earlier
    assert nearest_strong_end(index, 2, 0) is None
    assert strong_ends_around(index, 2) == (1, 3)
    assert nearest_sentence_start(index, 3, 2) == 3
    assert index.unit(3).starts_sentence
    window = ends_for_length(index, 1, 2.0, 4.0)
    assert window is not None
    low, high = window
    assert 2.0 <= index.speech_seconds(1, low) and index.speech_seconds(1, high) <= 4.0


# -- I2: adjacent series parts share one exact cut --------------------------


def _random_stream(rng: random.Random, *, speakers: bool):
    words, cursor, speaker = [], rng.uniform(0.0, 3.0), "A"
    for n in range(rng.randint(120, 260)):
        if rng.random() < 0.04:
            text = rng.choice(sorted(FILLERS))
            duration = rng.uniform(0.2, 0.5)
        else:
            mark = rng.choices(["", ".", "?", ","], weights=[70, 15, 4, 11])[0]
            # Some capitals, so breaths before them end units ("pause").
            text = f"{rng.choice('tttT')}{n}{mark}"
            duration = rng.uniform(0.15, 0.6)
        roll = rng.random()
        if roll < 0.02:
            start, duration = cursor, 0.0  # zero-length ASR word: repair_word_timings gives it a slot
        elif roll < 0.04 and words:
            start = cursor - 0.06  # ASR overlap with the previous word
        else:
            start = cursor
        if speakers and rng.random() < 0.05:
            speaker = "B" if speaker == "A" else "A"
        words.append((text, start, start + duration, speaker if speakers else None))
        gap = rng.choices(
            [rng.uniform(0.02, 0.15), rng.uniform(0.3, 1.0), rng.uniform(1.3, 3.0)], weights=[80, 14, 6]
        )[0]
        cursor = start + duration + gap
    return _words(*words)


def _random_partition(rng: random.Random, size: int):
    first = rng.randint(1, max(1, size // 4))
    last = rng.randint(max(first + 6, size - size // 4), size)
    skips = []
    position = first + 2
    while position < last - 3:
        if rng.random() < 0.08:
            length = rng.randint(1, 3)
            skips.append((position, min(position + length - 1, last - 2)))
            position += length + 2
        else:
            position += 1
    skipped = {uid for low, high in skips for uid in range(low, high + 1)}
    spine = [uid for uid in range(first, last + 1) if uid not in skipped]
    cuts = sorted(rng.sample(range(len(spine) - 1), k=min(rng.randint(1, 6), len(spine) - 1)))
    parts, start = [], 0
    for cut in cuts + [len(spine) - 1]:
        parts.append((spine[start], spine[cut]))
        start = cut + 1
    return (first, last), skips, parts


def _expected_texts(index, span, skips, cleanup, path):
    skipped = {uid for low, high in skips for uid in range(low, high + 1)}
    texts = Counter()
    for run_first, run_last in kept_runs(span[0], span[1], skips):
        assert not set(range(run_first, run_last + 1)) & skipped
        run = [index.words[p].text for uid in range(run_first, run_last + 1) for p in range(index.unit(uid).w0, index.unit(uid).w1 + 1)]
        spoken = [text for text in run if text.strip(",.?") not in FILLERS]
        if cleanup:
            # Cleanup keeps a range whole when cutting it would leave almost
            # nothing (only fillers, or under 0.5 s); then its fillers stay.
            start, end = index.range_seconds(run_first, run_last)
            whole = timeline.build_clip_keep_ranges(path, start, end, cleanup) == [(start, end)]
            texts.update(run if whole else spoken)
        else:
            texts.update(run)
    return texts


@pytest.mark.parametrize("seed", range(30))
@pytest.mark.parametrize("cleanup", [None, CLEANUP_ON], ids=["cleanup-off", "cleanup-on"])
def test_adjacent_parts_share_one_exact_cut(seed, cleanup, cached_video):
    rng = random.Random(seed)
    words = _random_stream(rng, speakers=seed % 2 == 0)
    data = _data(words)
    path = cached_video(words)
    index = build_units(data, "")
    assert len(index.units) > 8
    for _ in range(4):
        span, skips, parts = _random_partition(rng, len(index.units))
        per_part = []
        for first, last in parts:
            # Planned segments store ranges to the millisecond (planning.rounded_ranges).
            ranges = rounded_ranges(index.ranges_for(first, last, skips))
            keep = timeline.build_keep_ranges_from_source_ranges(path, ranges, cleanup) if cleanup else ranges
            heard = timeline.get_words_for_keep_ranges(data, keep)
            per_part.append(Counter(word["text"] for word in heard))
        for left, right in zip(per_part, per_part[1:]):
            shared = {text for text in left & right if text.strip(",.?") not in FILLERS}
            assert not shared, f"seed {seed}: words heard in two parts: {sorted(shared)}"
        union = Counter()
        for texts in per_part:
            union.update(texts)
        expected = Counter()
        for first, last in parts:
            expected.update(_expected_texts(index, (first, last), skips, cleanup, path))
        assert union == expected, f"seed {seed}: {sorted((union - expected).elements())} / {sorted((expected - union).elements())}"


@pytest.mark.parametrize("seed", range(10))
def test_every_range_edge_lies_on_a_unit_edge(seed):
    """I3: a cut-in never precedes the previous word's end; a cut-out never passes the next word's start."""
    index = build_units(_data(_random_stream(random.Random(seed), speakers=True)), "")
    words = index.words
    for unit in index.units:
        start, end = index.range_seconds(unit.id, unit.id)
        if unit.w0 > 0:
            assert start >= words[unit.w0 - 1].end
        if unit.w1 + 1 < len(words):
            following = words[unit.w1 + 1].start
            assert end <= following
            assert end <= max(words[unit.w1].end, following - 0.03) + 1e-9
        assert end > start
