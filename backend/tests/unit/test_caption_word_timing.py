"""Zero-duration ASR words must reach the captions (#289).

Word fragments are copied from the audited AssemblyAI transcripts (times in ms).
"""

import pytest

from src.media.timeline import (
    get_absolute_words_in_range,
    get_words_for_keep_ranges,
    get_words_in_range,
    repair_word_timings,
)


def _words(*items):
    return [{"text": text, "start": start, "end": end, "confidence": 0.99} for text, start, end in items]


# Clix reaction (#289): "game" and "And I did zero" carry start == end.
CLIX_GAME = _words(
    ("a", 541160, 541460), ("game", 541460, 541460), ("in", 541460, 541560),
    ("my", 541560, 541720), ("life.", 541720, 542100),
)
CLIX_ZERO = _words(
    ("bro.", 580850, 581250), ("And", 581250, 581250), ("I", 581250, 581250),
    ("did", 581250, 581250), ("zero", 581250, 581250), ("damage", 581250, 581690),
    ("to", 581690, 581870), ("Shark", 581870, 582270),
)
# Gordon Ramsay: "you" in "Do you know why?".
COOKING_YOU = _words(
    ("Do", 51420, 51460), ("you", 51460, 51460), ("know", 51460, 51580), ("why?", 51580, 51880),
)
# AssemblyAI tie between two tiny neighbours ("Very valid. as").
TIGHT = _words(("Very", 473670, 473730), ("valid.", 473730, 473730), ("as", 473730, 473770))


@pytest.mark.parametrize("words", [CLIX_GAME, CLIX_ZERO, COOKING_YOU, TIGHT])
def test_zero_duration_words_reach_the_caption_words(words):
    transcript = {"words": words}
    start, end = words[0]["start"] / 1000, words[-1]["end"] / 1000

    absolute = get_absolute_words_in_range(transcript, start, end)
    relative = get_words_in_range(transcript, start, end)
    projected = get_words_for_keep_ranges(transcript, [(start, end)])

    texts = [word["text"] for word in words]
    for result in (absolute, relative, projected):
        assert [word["text"] for word in result] == texts
        assert all(word["end"] > word["start"] for word in result)
        assert all(a["end"] <= b["start"] + 1e-9 for a, b in zip(result, result[1:]))


def test_zero_duration_run_borrows_a_readable_slot_from_its_neighbours():
    repaired = repair_word_timings(CLIX_ZERO)

    run = repaired[1:5]
    assert all(word["end"] - word["start"] >= 90 for word in run)
    # Neighbours lend time but keep their own word on screen.
    assert repaired[0]["end"] - repaired[0]["start"] >= 150
    assert repaired[5]["end"] - repaired[5]["start"] >= 150
    assert repaired[0]["start"] == 580850 and repaired[5]["end"] == 581690
    # The cached transcript itself is not modified.
    assert CLIX_ZERO[1]["end"] == CLIX_ZERO[1]["start"]


def test_repair_leaves_well_timed_transcripts_untouched():
    words = _words(("hello", 0, 300), ("there", 300, 600))
    assert repair_word_timings(words) is words
