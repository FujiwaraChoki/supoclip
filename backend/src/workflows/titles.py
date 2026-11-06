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
    "is", "are", "was", "were", "will", "never", "not", "your", "his", "her", "their", "my",
    "ist", "sind", "war", "wird", "nicht", "dein", "sein", "ihr", "mein",
}
# Over budget, a title ends at its last clause mark when this many words stay.
CLAUSE_CUT_MIN_WORDS = 3
_CLAUSE_MARKS = (",", ":", ";", "—", "–")
# Words that say nothing about what a clip contains ("How the bank ...").
TITLE_STOPWORDS = {
    "about", "after", "again", "being", "because", "before", "could", "doesn't", "every", "first", "from", "have",
    "here", "just", "like", "makes", "might", "more", "most", "much", "never", "only", "other", "over", "really",
    "should", "some", "still", "than", "that", "their", "them", "then", "there", "these", "they", "thing", "things",
    "this", "those", "through", "under", "very", "want", "were", "what", "when", "where", "which", "while", "will",
    "with", "without", "would", "your", "you're", "aber", "alle", "auch", "dass", "denn", "diese", "dieser", "doch",
    "durch", "eine", "einem", "einen", "einer", "immer", "keine", "nach", "nicht", "noch", "nur", "oder", "schon",
    "sich", "sind", "über", "unter", "warum", "weil", "wenn", "werden", "wieder", "wird", "wurde",
}
TITLE_WORD_MIN_LETTERS = 4
_NON_WORD_EDGES = "\"'`“”‘’«»「」『』()[]{},.;:!?¿¡…—–-"


def title_words(text: str) -> list[str]:
    """The words of a title or sentence worth comparing: casefolded, without edge punctuation or stopwords."""
    words = []
    for token in (text or "").split():
        word = unicodedata.normalize("NFC", token).casefold().strip(_NON_WORD_EDGES)
        if len(word) >= TITLE_WORD_MIN_LETTERS and word not in TITLE_STOPWORDS:
            words.append(word)
    return words


def said_in(word: str, text_words: set[str] | list[str]) -> bool:
    """Whether a word is said, on its first letters: "habits" counts for "habit", "Frontex'" for "Frontex"."""
    # Scripts with vowel signs count more characters per letter and inflect at the end.
    marked = any(unicodedata.category(char) in ("Mn", "Mc") for char in word)
    stem = word[: 4 if marked else 5]
    return any(other.startswith(stem) for other in text_words)


def heard_share(title: str, text: str) -> float:
    """Share of a title's content words that are said in ``text`` (1.0 when it has none to check)."""
    words = title_words(title)
    if not words:
        return 1.0
    heard = title_words(text)
    return sum(1 for word in words if said_in(word, heard)) / len(words)


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
        clause = next(
            (
                position
                for position in range(len(kept) - 1, CLAUSE_CUT_MIN_WORDS - 2, -1)
                if kept[position].endswith(_CLAUSE_MARKS)
            ),
            None,
        )
        if clause is not None:
            kept = kept[: clause + 1]
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
    # The budget applies before the hook-title sanitizer: its own 10-word cut
    # would leave "and greed is a" looking complete to the trim below.
    raw = strip_numbering(" ".join((title or "").split()), list_number=bool(prefix))
    raw = raw.strip("\"'`“”‘’«»「」『』").strip()
    title_no_space = is_no_space(None, raw)
    separator = "：" if prefix and (title_no_space or is_no_space(None, prefix)) else ": "
    # Columns when the title is full-width, so the prefix counts as it shows.
    prefix_width = sum(_width(char) for char in prefix) if title_no_space else len(prefix)
    char_budget = max_chars - prefix_width - (2 if prefix else 0)
    word_budget = max_words - len(prefix.split())
    clean = ai.sanitize_hook_title(_trim(raw, char_budget, word_budget, title_no_space) if raw else "") or ""
    clean = strip_numbering(clean, list_number=bool(prefix))
    clean = clean.strip("\"'`“”‘’«»「」『』").strip()
    if prefix and clean:
        return f"{prefix}{separator}{clean}"
    return prefix or clean or None
