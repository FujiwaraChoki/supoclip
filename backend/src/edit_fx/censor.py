"""Masking bleeped words in caption text. Mirrored by ``frontend/src/lib/fx/core/censor.ts``."""

from __future__ import annotations

from typing import Sequence

CENSOR_MIN_OVERLAP = 0.03   # a word is censored when a bleep covers at least 30 ms of it (or all of it)
_EPS = 1e-9


def censor_text(text: str) -> str:
    """Keep the first letter, replace every later letter with ``*``; digits, punctuation and spaces stay.

    Letters are Unicode letters (``str.isalpha``, the same set as JavaScript's ``\\p{L}``), walked by code point:
    ``"fuck!"`` → ``"f***!"``, ``"Scheiße"`` → ``"S******"``, ``"блядь"`` → ``"б****"``.
    """
    seen_letter = False
    out = []
    for char in text:
        if char.isalpha():
            out.append("*" if seen_letter else char)
            seen_letter = True
        else:
            out.append(char)
    return "".join(out)


def _covered(start: float, end: float, spans: Sequence[tuple[float, float]]) -> bool:
    for span_start, span_end in spans:
        overlap = min(end, span_end) - max(start, span_start)
        if overlap >= CENSOR_MIN_OVERLAP - _EPS:
            return True
        if span_start <= start and end <= span_end:
            return True
    return False


def censored_words(words: Sequence[dict], spans: Sequence[tuple[float, float]]) -> list[dict]:
    """Copies of ``words`` (same order) with ``text`` censored where a span overlaps the word by ≥ 30 ms, or covers
    it entirely (a word shorter than 30 ms inside a bleep is still masked)."""
    out = []
    for word in words:
        copy = dict(word)
        if spans and _covered(float(word.get("start", 0.0)), float(word.get("end", 0.0)), spans):
            copy["text"] = censor_text(str(word.get("text", "")))
        out.append(copy)
    return out
