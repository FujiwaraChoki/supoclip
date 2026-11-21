"""parse_spoken_number: the stat-counter value of a number beat (§7.3)."""

import pytest

from src.edit_fx.numbers import find_number_spans, number_span, parse_spoken_number

CASES = [
    # digits, currency and scale suffixes
    ("$2.5M", (2.5, "$", "M")),
    ("$2,000,000", (2_000_000.0, "$", "")),
    ("1,200", (1200.0, "", "")),
    ("1,200.50", (1200.5, "", "")),
    ("40%", (40.0, "", "%")),
    ("12.5%", (12.5, "", "%")),
    ("€5k", (5.0, "€", "k")),
    ("100k", (100.0, "", "k")),
    ("2.5K", (2.5, "", "k")),
    ("£3bn", (3.0, "£", "B")),
    ("$1.2B", (1.2, "$", "B")),
    ("¥500", (500.0, "¥", "")),
    ("10x", (10.0, "", "x")),
    ("3X", (3.0, "", "x")),
    ("#1", (1.0, "#", "")),
    ("2024", (2024.0, "", "")),
    ("1990s", (1990.0, "", "s")),
    (".5", (0.5, "", "")),
    ("2,5", (2.5, "", "")),
    ("$2,000,000.", (2_000_000.0, "$", "")),
    ("(40%)", (40.0, "", "%")),
    ("$5M+", (5.0, "$", "M")),
    # digits + words
    ("5 million", (5.0, "", "M")),
    ("2.5 million dollars", (2.5, "$", "M")),
    ("1.5 billion dollars", (1.5, "$", "B")),
    ("40 percent", (40.0, "", "%")),
    ("40 per cent", (40.0, "", "%")),
    ("$ 5", (5.0, "$", "")),
    ("300 bucks", (300.0, "$", "")),
    ("20 euros", (20.0, "€", "")),
    ("50 thousand", (50_000.0, "", "")),
    # English number words
    ("two million", (2.0, "", "M")),
    ("a hundred", (100.0, "", "")),
    ("a million", (1.0, "", "M")),
    ("three hundred", (300.0, "", "")),
    ("twelve hundred", (1200.0, "", "")),
    ("three thousand", (3000.0, "", "")),
    ("twenty five", (25.0, "", "")),
    ("seventy-five", (75.0, "", "")),
    ("forty percent", (40.0, "", "%")),
    ("one hundred and twenty three", (123.0, "", "")),
    ("two million five hundred thousand", (2.5, "", "M")),
    ("two point five million", (2.5, "", "M")),
    ("five bucks", (5.0, "$", "")),
    ("a dozen", (12.0, "", "")),
    ("nine", (9.0, "", "")),
    ("Ten", (10.0, "", "")),
    ("half a billion", (500_000_000.0, "", "")),
    ("half a million dollars", (500_000.0, "$", "")),
    ("one and a half million", (1.5, "", "M")),
    ("two and a half percent", (2.5, "", "%")),
    # leading words are skipped
    ("I made $2,000,000", (2_000_000.0, "$", "")),
    ("we hit 40% growth", (40.0, "", "%")),
    ("it costs two million dollars", (2.0, "$", "M")),
]

NOT_NUMBERS = [
    "",
    "hello",
    "oh my god",
    "a lot of people",
    "amazing!",
    "%",
    "$",
    "point",
    "and",
    "hundred",
    "dollars",
]


@pytest.mark.parametrize("text,expected", CASES)
def test_parse_spoken_number_table(text, expected):
    assert parse_spoken_number(text.split()) == expected


@pytest.mark.parametrize("text", NOT_NUMBERS)
def test_not_numbers(text):
    assert parse_spoken_number(text.split()) is None


def test_table_is_large_enough():
    assert len(CASES) >= 40


def test_two_numbers_in_a_row_parse_only_the_first():
    assert parse_spoken_number(["5", "10"]) == (5.0, "", "")
    assert parse_spoken_number(["two", "three"]) == (2.0, "", "")


def test_prefix_and_suffix_lengths_fit_the_beat_contract():
    for text, _ in CASES:
        parsed = parse_spoken_number(text.split())
        assert parsed is not None
        assert len(parsed[1]) <= 4 and len(parsed[2]) <= 8


def test_number_span_reports_consumed_tokens():
    assert number_span(["two", "million", "dollars", "today"], 0) == (3, (2.0, "$", "M"))
    assert number_span(["40%", "growth"], 0) == (1, (40.0, "", "%"))
    assert number_span(["made", "40%"], 0) is None


def test_find_number_spans_trusts_digits_and_scaled_words_only():
    tokens = "one of them made $2.5M and two million people saw 40 percent growth in 2024".split()
    spans = find_number_spans(tokens)
    assert [(start, count) for start, count, _ in spans] == [(4, 1), (6, 2), (10, 2), (14, 1)]
    assert [parsed for _, _, parsed in spans] == [
        (2.5, "$", "M"), (2.0, "", "M"), (40.0, "", "%"), (2024.0, "", ""),
    ]


def test_find_number_spans_skips_lone_number_words():
    assert find_number_spans("one thing I know is two people".split()) == []


def test_currency_written_after_the_number():
    assert parse_spoken_number(["100€"]) == (100.0, "€", "")
    assert parse_spoken_number(["5M€"]) == (5.0, "€", "M")
    assert parse_spoken_number(["5", "€"]) == (5.0, "€", "")
    assert parse_spoken_number(["$5€"]) is None


def test_an_absurdly_long_digit_run_is_not_a_number_and_never_raises():
    assert parse_spoken_number(["1" * 400]) is None
    assert find_number_spans(["made", "1" * 400, "dollars"]) == []
