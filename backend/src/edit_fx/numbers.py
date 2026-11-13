"""Spoken-number parsing for number beats (stat counters).

`parse_spoken_number(tokens)` turns the words of a number beat into `(value, prefix, suffix)`, the three fields a
counter needs to draw "$2.5M" or "40%". It reads digits in any language and English number words; anything else is
None (the beat keeps `value=None` and the counter falls back to plain text).

Display semantics: the value is what the counter counts up to and the suffix is drawn after it, so "$2.5M" is
(2.5, "$", "M"), "two million" is (2.0, "", "M") and "1,200" is (1200.0, "", ""). Thousands stay numeric
("two thousand" -> 2000); million and above become a suffix (M, B, T) when the spoken form uses that scale.

Pure: no I/O. Safe to import from the API, the video worker and the AI worker.
"""

from __future__ import annotations

import math
import re
from typing import Optional, Sequence

_CURRENCY_SYMBOLS = "$€£¥"
_PREFIX_SYMBOLS = _CURRENCY_SYMBOLS + "#"

# Surrounding punctuation that never belongs to a number token ("$" and "%" are kept).
_STRIP_CHARS = " \t\n\r\"'“”‘’«»()[]{}<>,;:!?…*_~"

_DIGIT_TOKEN_RE = re.compile(
    r"""^
    (?P<pre>[$€£¥\#]?)
    (?P<num>
        \d{1,3}(?:,\d{3})+(?:\.\d+)?   # 1,200 / 2,000,000.50
      | \d+(?:\.\d+)?                  # 1200 / 2.5
      | \.\d+                          # .5
      | \d+,\d{1,2}                    # 2,5 (decimal comma)
    )
    (?P<suf>[kKmMbBtTxX%]|bn|BN|mm|MM|s)?
    (?P<cur>[$€£¥]?)                   # 100€ / 5M€ (currency written after the number)
    \+?
    $""",
    re.VERBOSE,
)

_DIGIT_SUFFIX = {
    "k": "k", "K": "k",
    "m": "M", "M": "M", "mm": "M", "MM": "M",
    "b": "B", "B": "B", "bn": "B", "BN": "B",
    "t": "T", "T": "T",
    "x": "x", "X": "x",
    "%": "%",
    "s": "s",   # "1990s"
}

_UNITS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fourty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
    "ninety": 90,
}
_SCALES = {"thousand": 1_000, "million": 1_000_000, "billion": 1_000_000_000, "trillion": 1_000_000_000_000}
_DIGIT_SCALE = {"M": 1_000_000, "B": 1_000_000_000, "T": 1_000_000_000_000}
_SCALE_SUFFIX = {1_000_000: "M", 1_000_000_000: "B", 1_000_000_000_000: "T"}
_SCALE_ALIASES = {"k": "thousand", "grand": "thousand", "mil": "million", "bil": "billion"}
_CURRENCY_WORDS = {
    "dollar": "$", "dollars": "$", "buck": "$", "bucks": "$", "usd": "$",
    "euro": "€", "euros": "€", "eur": "€",
    "pound": "£", "pounds": "£", "quid": "£", "gbp": "£",
    "yen": "¥",
}
_PERCENT_WORDS = {"percent", "percentage", "pct", "%"}
_ARTICLES = {"a", "an"}
_FILLER_INSIDE = {"and"}
_DOZEN = "dozen"
_HALF = "half"

_MAX_TOKENS = 12


def _clean(token: str) -> str:
    # A sentence-final "." is punctuation ("$2,000,000."); a leading one is a decimal (".5").
    return str(token or "").strip(_STRIP_CHARS).rstrip(".").strip(_STRIP_CHARS)


def _word(token: str) -> str:
    return _clean(token).lower()


def _plural_scale(word: str) -> str:
    """'millions' -> 'million' ("two millions" is rare but harmless)."""
    if word.endswith("s") and word[:-1] in _SCALES:
        return word[:-1]
    return _SCALE_ALIASES.get(word, word)


def _parse_digit_token(token: str) -> Optional[tuple[float, str, str]]:
    match = _DIGIT_TOKEN_RE.match(_clean(token))
    if not match:
        return None
    raw = match.group("num")
    if re.fullmatch(r"\d+,\d{1,2}", raw):
        number = float(raw.replace(",", "."))
    else:
        number = float(raw.replace(",", ""))
    if not math.isfinite(number):
        return None
    suffix = _DIGIT_SUFFIX.get(match.group("suf") or "", "")
    pre, cur = match.group("pre") or "", match.group("cur") or ""
    if pre and cur and pre != cur:
        return None
    return number, pre or cur, suffix


def _word_value(word: str) -> Optional[int]:
    if word in _UNITS:
        return _UNITS[word]
    if word in _TENS:
        return _TENS[word]
    if "-" in word:
        head, _, tail = word.partition("-")
        if head in _TENS and tail in _UNITS and 0 < _UNITS[tail] < 10:
            return _TENS[head] + _UNITS[tail]
    return None


def _is_number_start(word: str) -> bool:
    return (
        _parse_digit_token(word) is not None
        or _word_value(word) is not None
        or word in _ARTICLES
        or word in _PREFIX_SYMBOLS
        or word == _DOZEN
        or word == _HALF
    )


def _round(value: float) -> float:
    rounded = round(value, 6)
    return float(int(rounded)) if rounded == int(rounded) else rounded


def _parse_run(words: Sequence[str]) -> Optional[tuple[float, str, str, int]]:
    """Parse a number phrase starting at words[0]. Returns (value, prefix, suffix, consumed) or None.

    `current` is the group below the next scale word ("five hundred" before "thousand"); `last` is what the group
    ended with, which decides whether the next number word may join it ("twenty" + "five", "hundred" + "twenty").
    """
    prefix = ""
    suffix = ""
    total = 0.0
    current: Optional[float] = None
    last = ""                                    # "", "unit", "tens", "hundred", "digit"
    largest_scale = 1
    seen_number = False
    decimals: Optional[list[str]] = None         # digits spoken after "point"
    consumed = 0
    n = min(len(words), _MAX_TOKENS)
    i = 0

    def fold_decimals() -> None:
        nonlocal current, decimals
        if decimals:
            current = (current or 0.0) + float("0." + "".join(decimals))
        decimals = None

    while i < n:
        word = words[i]
        if not word:
            break

        if seen_number and word in _CURRENCY_SYMBOLS and not prefix:
            prefix = word                        # "5 €": a bare currency token after the number
            consumed = i + 1
            break

        if not seen_number and word in _PREFIX_SYMBOLS and not prefix:
            prefix = word                        # a bare "$" token before the number
            i += 1
            continue

        digit = _parse_digit_token(word)
        if digit is not None:
            number, pre, suf = digit
            if decimals is not None and not pre and not suf and number == int(number):
                decimals.extend(str(int(number)))   # "two point 5"
                consumed = i + 1
                i += 1
                continue
            fold_decimals()
            if current is not None:
                break                            # two numbers in a row ("5 10", "two 5")
            if pre:
                if prefix and pre != prefix:
                    break
                prefix = pre
            seen_number = True
            consumed = i + 1
            i += 1
            if suf in _DIGIT_SCALE:
                total += number * _DIGIT_SCALE[suf]
                largest_scale = max(largest_scale, _DIGIT_SCALE[suf])
                current, last = None, ""
                continue
            current, last = number, "digit"
            if suf == "k":
                current = number * 1_000
                suffix = "k"
                continue
            if suf:
                suffix = suf
                break
            continue

        if decimals is not None:
            value = _word_value(word)
            if value is not None and value < 10:
                decimals.append(str(value))
                consumed = i + 1
                i += 1
                continue
            fold_decimals()

        value = _word_value(word)
        if value is not None:
            if current is None or last == "hundred":
                current = (current or 0.0) + value
                last = "tens" if value >= 20 and value % 10 == 0 else "unit"
            elif last == "tens" and 0 < value < 10:
                current += value                 # "twenty five"
                last = "unit"
            else:
                break                            # "two three"
            seen_number = True
            consumed = i + 1
            i += 1
            continue

        scale_word = _plural_scale(word)

        if word in _ARTICLES and not seen_number:
            nxt = _plural_scale(words[i + 1]) if i + 1 < n else ""
            if nxt in _SCALES or nxt == "hundred" or nxt == _DOZEN:
                current, last = 1.0, "unit"      # "a hundred", "a million", "a dozen"
                seen_number = True
                i += 1
                continue
            break

        if word == _HALF and not seen_number:
            j = i + 1 + (1 if i + 1 < n and words[i + 1] in _ARTICLES else 0)
            if j < n and _plural_scale(words[j]) in _SCALES:
                current, last = 0.5, "unit"      # "half a billion", "half a million"
                seen_number = True
                i = j
                continue
            break

        if word == _DOZEN:
            current = (current if current is not None else 1.0) * 12
            seen_number = True
            last = "hundred"
            consumed = i + 1
            i += 1
            continue

        if scale_word == "hundred":
            if current is None or last == "hundred":
                break
            current *= 100
            last = "hundred"
            consumed = i + 1
            i += 1
            continue

        if scale_word in _SCALES:
            if not seen_number:
                break
            scale = _SCALES[scale_word]
            total += (current if current is not None else 1.0) * scale
            current, last = None, ""
            largest_scale = max(largest_scale, scale)
            consumed = i + 1
            i += 1
            continue

        if word == "point" and current is not None and decimals is None:
            decimals = []
            i += 1
            continue

        if word in _FILLER_INSIDE and seen_number:
            if (current is not None and current == int(current) and i + 2 < n
                    and words[i + 1] in _ARTICLES and words[i + 2] == _HALF):
                current += 0.5                   # "one and a half (million)"
                last = "half"                    # no number word may join the group after "a half"
                consumed = i + 3
                i += 3
                continue
            if i + 1 < n and _word_value(words[i + 1]) is not None and last == "hundred":
                i += 1                           # "one hundred and twenty"
                continue
            break

        if seen_number and word in _PERCENT_WORDS:
            suffix = "%"
            consumed = i + 1
            break

        if seen_number and word == "per" and i + 1 < n and words[i + 1] == "cent":
            suffix = "%"
            consumed = i + 2
            break

        break

    fold_decimals()
    if not seen_number or consumed == 0:
        return None

    value = total + (current or 0.0)
    if not math.isfinite(value):
        return None
    if consumed < len(words) and words[consumed] in _CURRENCY_WORDS and suffix not in ("%", "x", "s"):
        if not prefix:
            prefix = _CURRENCY_WORDS[words[consumed]]   # "two million dollars"
        consumed += 1

    if suffix in ("%", "x", "s"):
        return _round(value), prefix, suffix, consumed
    if largest_scale >= 1_000_000 and value >= largest_scale:
        return _round(value / largest_scale), prefix, _SCALE_SUFFIX[largest_scale], consumed
    if suffix == "k" and value >= 1_000:
        return _round(value / 1_000), prefix, "k", consumed
    return _round(value), prefix, "", consumed


def number_span(tokens: Sequence[str], start: int) -> Optional[tuple[int, tuple[float, str, str]]]:
    """Parse the number phrase that starts at tokens[start]. Returns (token count, (value, prefix, suffix)) or None."""
    words = [_word(token) for token in tokens[start:]]
    # Keep the original casing for digit tokens ("2.5M" vs "2.5m" both map to M; "$" stays).
    raw = [_clean(token) for token in tokens[start:]]
    words = [raw[i] if _parse_digit_token(raw[i]) is not None else words[i] for i in range(len(words))]
    if not words or not _is_number_start(words[0]):
        return None
    parsed = _parse_run(words)
    if parsed is None:
        return None
    value, prefix, suffix, consumed = parsed
    if consumed <= 0:
        return None
    return consumed, (value, prefix[:4], suffix[:8])


def parse_spoken_number(tokens: Sequence[str]) -> tuple[float, str, str] | None:
    """(value, prefix, suffix) for the first number phrase in `tokens`, or None.

    Leading words that are not part of a number are skipped ("made $2,000,000" -> 2000000, "$").
    """
    for index in range(len(tokens)):
        span = number_span(tokens, index)
        if span is not None:
            return span[1]
    return None


def find_number_spans(tokens: Sequence[str]) -> list[tuple[int, int, tuple[float, str, str]]]:
    """Number phrases a heuristic can trust: `[(start, count, (value, prefix, suffix))]`, left to right.

    A phrase must start at a token with a digit ("40%", "$2.5M", "5 million") or be English number words that end
    in a scale or percent ("two million", "forty percent"). Lone number words ("one of them") are not numbers here.
    """
    spans: list[tuple[int, int, tuple[float, str, str]]] = []
    index = 0
    while index < len(tokens):
        token = _clean(tokens[index])
        has_digit = any(ch.isdigit() for ch in token)
        span = number_span(tokens, index) if (has_digit or _word_value(token.lower()) is not None
                                              or token.lower() in _ARTICLES or token.lower() == _HALF) else None
        if span is not None:
            count, parsed = span
            words = [_word(t) for t in tokens[index:index + count]]
            scaled = any(_plural_scale(w) in _SCALES or w == "hundred" for w in words)
            if has_digit or scaled or parsed[2] == "%":
                spans.append((index, count, parsed))
                index += count
                continue
        index += 1
    return spans
