"""On-screen titles for planned clips: "Part 2: The bank calls back".

The burned-in hook title shares ``ai.sanitize_hook_title`` with highlights
(unchanged, 64 characters / 10 words). A prefix such as a localized part
label takes its share of that budget, so the title is trimmed again here at
a word boundary (a character boundary for languages without spaces).
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional

from .. import ai
from .language import is_no_space

# A numbering the model added itself ("Part 3:", "Teil 3 –", "#3", "Episode 2 of 5").
_LEADING_NUMBERING_RE = re.compile(
    r"^(?:part|teil|parte|partie|deel|bagian|bölüm|część|часть|частина|osa|část"
    r"|episode|episodio|épisode|folge|ep\.?|#)"
    r"\s*\d+(?:\s*(?:/|of|von|de|sur)\s*\d+)?\s*[:.\-–—|]?\s*",
    re.IGNORECASE,
)
# Scandinavian "Del 2" only with a separator: Spanish "Del 1 al 10" is a title.
_LEADING_DEL_RE = re.compile(r"^del\s*\d+\s*(?:[:.\-–—|]\s*|$)", re.IGNORECASE)
# A bare list number ("1. The beginning"), stripped only under a part label.
_LEADING_LIST_NUMBER_RE = re.compile(r"^\d{1,2}[.)]\s+")
_LEADING_CJK_NUMBERING_RE = re.compile(r"^(?:第\s*\d+\s*(?:部分|集|部|話|话|回)|パート\s*\d+|\d+\s*부)\s*[:：\-–—|]?\s*")
_TRAILING_NUMBERING_RE = re.compile(
    r"\s*[\(\[（【]?\s*(?:part|teil|parte|partie|deel|bagian|bölüm|część|часть|episode|folge|ep\.?)\s*\d+"
    r"(?:\s*(?:/|of|von|de|sur)\s*\d+)?\s*[\)\]）】]?\s*$",
    re.IGNORECASE,
)
_TRAILING_TRIM = ".,;:-–—、，；：|/ "
# Words a budget trim must not leave dangling at the end ("...keeps going and").
_DANGLING_WORDS = {
    "a", "an", "the", "and", "or", "but", "of", "to", "in", "on", "at", "by", "for", "from", "with", "as", "that",
    "und", "oder", "aber", "der", "die", "das", "den", "dem", "ein", "eine", "mit", "für", "von", "zu", "im", "auf",
    "y", "o", "pero", "el", "la", "los", "las", "de", "del", "con", "para", "en", "un", "una", "que",
    "e", "ou", "mas", "os", "da", "do", "com", "em", "um", "uma",
    "et", "mais", "le", "les", "du", "des", "avec", "pour", "une",
}


def strip_numbering(title: str, *, list_number: bool = False) -> str:
    """Drop a part or episode number the model wrote into a title.

    ``list_number`` also drops a leading "1." or "2)" (only safe when a part
    label replaces it; "10. Mai" or "1) Pricing" can be a title of its own).
    """
    stripped = _LEADING_NUMBERING_RE.sub("", title.strip(), count=1)
    stripped = _LEADING_DEL_RE.sub("", stripped, count=1)
    stripped = _LEADING_CJK_NUMBERING_RE.sub("", stripped, count=1)
    if list_number:
        stripped = _LEADING_LIST_NUMBER_RE.sub("", stripped, count=1)
    stripped = _TRAILING_NUMBERING_RE.sub("", stripped, count=1)
    return stripped.strip()


def _width(char: str) -> int:
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def _cut_chars(text: str, limit: int, *, wide: bool = False) -> str:
    """At most ``limit`` columns, never leaving a combining mark without its base.

    With ``wide``, full-width characters (Chinese, Japanese) count as two:
    they take twice the room of a Latin letter on screen.
    """
    used = 0
    cut = len(text)
    for position, char in enumerate(text):
        used += _width(char) if wide else 1
        if used > limit:
            cut = position
            break
    while 0 < cut < len(text) and unicodedata.category(text[cut])[0] == "M":
        cut -= 1
    return text[:cut]


def _trim(text: str, max_chars: int, max_words: int, no_space: bool) -> str:
    if max_chars <= 0:
        return ""
    if no_space:
        cut = _cut_chars(text, max_chars, wide=True)
        if len(cut) < len(text):
            # Prefer ending on the writer's own pause (、。，) late in the cut.
            pause = max(cut.rfind(mark) for mark in "、，。！？；：　 ")
            if pause >= len(cut) * 0.6:
                cut = cut[: pause + 1]
        return cut.rstrip(_TRAILING_TRIM)
    words = text.split()
    if max_words <= 0:
        return ""
    kept = words[:max_words]
    while kept and len(" ".join(kept)) > max_chars:
        kept.pop()
    if not kept:
        # One word longer than the whole budget.
        return _cut_chars(words[0], max_chars).rstrip(_TRAILING_TRIM)
    if len(kept) < len(words):
        while len(kept) > 1 and kept[-1].casefold().strip(_TRAILING_TRIM) in _DANGLING_WORDS:
            kept.pop()
    return " ".join(kept).rstrip(_TRAILING_TRIM)


def compose_title(
    title: str,
    *,
    prefix: str = "",
    max_chars: int = 64,
    max_words: int = 10,
) -> Optional[str]:
    """``"{prefix}: {title}"`` within the hook-title budget, or the part that is left.

    The title is sanitized like a highlights hook title, loses any numbering
    the model added, and is trimmed to the budget left after the prefix.
    Returns the prefix alone when nothing of the title survives, and None when
    there is neither.
    """
    prefix = (prefix or "").strip()
    clean = ai.sanitize_hook_title(title) or ""
    clean = strip_numbering(clean, list_number=bool(prefix))
    clean = clean.strip("\"'`“”‘’«»「」『』").strip()
    title_no_space = is_no_space(None, clean)
    separator = "：" if prefix and (title_no_space or is_no_space(None, prefix)) else ": "
    # Columns when the title is full-width, so the prefix counts as it shows.
    prefix_width = sum(_width(char) for char in prefix) if title_no_space else len(prefix)
    char_budget = max_chars - prefix_width - (2 if prefix else 0)
    word_budget = max_words - len(prefix.split())
    clean = _trim(clean, char_budget, word_budget, title_no_space) if clean else ""
    if prefix and clean:
        return f"{prefix}{separator}{clean}"
    return prefix or clean or None
