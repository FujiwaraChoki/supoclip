"""edit_fx.censor: masked caption text for bleeped words."""

import pytest

from src.edit_fx.censor import censor_text, censored_words


@pytest.mark.parametrize("text,expected", [
    ("fuck", "f***"),
    ("Fuck!", "F***!"),
    ("shit's", "s***'*"),
    ("Scheiße", "S******"),
    ("блядь", "б****"),
    ("co-ño", "c*-**"),
    ("f*ck", "f***"),
    ("123abc", "123a**"),
    ("'damn'", "'d***'"),
    ("💩shit", "💩s***"),
    ("ÉCOLE", "É****"),
    ("你好", "你*"),
    ("", ""),
    ("  ", "  "),
])
def test_censor_text(text, expected):
    assert censor_text(text) == expected


def test_censor_keeps_length_in_code_points():
    for text in ("Scheiße", "блядь", "mierda!", "😤merde"):
        assert len(censor_text(text)) == len(text)


WORDS = [
    {"id": "w0", "start": 0.0, "end": 0.4, "text": "what"},
    {"id": "w1", "start": 0.4, "end": 0.8, "text": "the"},
    {"id": "w2", "start": 0.8, "end": 1.2, "text": "fuck,", "speaker": "A"},
    {"id": "w3", "start": 1.2, "end": 1.22, "text": "uh"},
]


def test_censored_words_marks_overlapping_words_only():
    out = censored_words(WORDS, [(0.8, 1.2)])
    assert [w["text"] for w in out] == ["what", "the", "f***,", "uh"]
    assert out[2]["speaker"] == "A" and out is not WORDS and WORDS[2]["text"] == "fuck,"


def test_overlap_threshold_is_30_ms():
    assert [w["text"] for w in censored_words(WORDS, [(0.37, 0.43)])] == ["w***", "t**", "fuck,", "uh"]
    assert [w["text"] for w in censored_words(WORDS, [(0.38, 0.42)])] == ["what", "the", "fuck,", "uh"]


def test_a_short_word_fully_inside_a_bleep_is_masked():
    assert censored_words(WORDS, [(1.19, 1.25)])[3]["text"] == "u*"


def test_no_spans_copies_words_unchanged():
    out = censored_words(WORDS, [])
    assert out == WORDS and out[0] is not WORDS[0]
