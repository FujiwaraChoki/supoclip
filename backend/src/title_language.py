"""Which language a clip's hook title should be in, and whether it is.

Highlights write a burned-in hook title per clip. The title must be in the
language the clip is spoken in: a model given an English prompt drifts into
English titles over Russian or Portuguese speech. These checks are
deterministic and cheap, so every clip can be checked after analysis:

- Script: a title written in a script the clip's own speech barely uses
  (Latin over Cyrillic, Cyrillic over Arabic, ...) is in another language.
  This needs no language code and works for any script.
- English over a Latin-script language: when the transcript's language is
  known and isn't English, a title full of English function words over speech
  that has almost none is an English title.

A segment that itself mixes languages (code-switching, an English quote)
passes, because both checks compare the title with that segment's speech.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any, Mapping, Optional

from .workflows.language import LANGUAGE_NAMES, detect_script_language, normalize_language_code

# Scripts that share one writing system are one group: Japanese mixes kana and kanji.
_SCRIPT_GROUPS = {"HIRAGANA": "CJK", "KATAKANA": "CJK", "CJK": "CJK", "IDEOGRAPHIC": "CJK", "HANGUL": "HANGUL"}
# A title script below this share of the segment's letters is foreign to the clip.
MIN_TITLE_SCRIPT_SHARE = 0.2
# Fewer letters than this in the segment says too little about its script.
MIN_SEGMENT_LETTERS = 20

# Words that mark English and are rare in other Latin-script languages.
ENGLISH_MARKERS = frozenset({
    "the", "and", "is", "are", "was", "were", "of", "to", "this", "that", "these", "those", "what", "why",
    "how", "who", "when", "with", "from", "your", "you", "for", "not", "if", "an", "can", "can't", "don't",
    "won't", "isn't", "doesn't", "after", "it", "it's", "its", "they", "their", "my", "his", "her", "he",
    "she", "we", "our", "will", "be", "been", "have", "has", "had", "do", "does", "did", "or", "at", "on",
    "by", "into", "about", "just", "really", "never", "every", "there", "here", "which", "would", "could",
    "should", "than", "then", "out", "up", "over", "only",
})
# Markers that are ordinary words in a language, so they say nothing there.
_MARKER_COLLISIONS = {
    "nl": {"is", "was", "in", "we", "of", "had", "over", "up"},
    "af": {"is", "in", "was", "we", "of", "het", "on", "over"},
    "de": {"was", "will", "so", "an", "in", "hat", "over"},
    "fr": {"on", "or", "but", "an", "est"},
    "es": {"he", "has", "no", "to"},
    "pt": {"do", "no", "has", "to"},
    "it": {"in", "he", "ho", "do", "an"},
    "ro": {"are", "do", "an"},
    "sv": {"for", "is", "an", "her", "at", "hade"},
    "da": {"for", "at", "is", "her", "to", "har"},
    "no": {"for", "at", "is", "her", "to", "har", "over"},
    "fil": {"at", "can", "may", "is"},
    "id": {"to", "it"},
    "ms": {"to", "it"},
    "sw": {"we", "he"},
}
# The English possessive ("collector's") marks English too, except where "'s" is native.
_POSSESSIVE_NATIVE = {"nl", "af", "de", "lb"}
MIN_TITLE_ENGLISH_MARKERS = 2
MIN_TITLE_ENGLISH_SHARE = 0.25
# Speech with at least this share of English markers is (partly) English itself.
MAX_SEGMENT_ENGLISH_SHARE = 0.08

_WORD_RE = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)?", re.UNICODE)
_TIMESTAMP_RE = re.compile(r"^\[[^\]]*\]\s*(?:Speaker [^:]+:\s*)?", re.MULTILINE)


def _script(char: str) -> Optional[str]:
    try:
        name = unicodedata.name(char)
    except ValueError:
        return None
    first = name.split(" ", 1)[0]
    return _SCRIPT_GROUPS.get(first, first)


def _script_counts(text: str) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    letters = 0
    for char in text or "":
        if not char.isalpha():
            continue
        script = _script(char)
        if not script:
            continue
        letters += 1
        counts[script] = counts.get(script, 0) + 1
    return counts, letters


def dominant_script(text: str) -> Optional[str]:
    """The script most letters of ``text`` are written in ("LATIN", "CYRILLIC", "CJK", ...)."""
    counts, _ = _script_counts(text)
    return max(counts, key=counts.get) if counts else None


def _words(text: str) -> list[str]:
    return [word.casefold().replace("’", "'") for word in _WORD_RE.findall(text or "")]


def _english_share(
    words: list[str], markers: frozenset[str] | set[str], possessive: bool = False
) -> tuple[int, float]:
    hits = sum(1 for word in words if word in markers or (possessive and word.endswith("'s")))
    return hits, (hits / len(words)) if words else 0.0


def transcript_language(
    transcript_data: Optional[Mapping[str, Any]], transcript_text: Optional[str] = ""
) -> Optional[str]:
    """ISO 639-1 code of the spoken language, or None when it isn't known.

    The transcription provider's code wins (AssemblyAI detects it, and an
    operator's TRANSCRIPTION_LANGUAGE forces it). Without one, scripts that
    identify a single language (Korean, Thai, Greek, ...) still tell it.
    Latin and Cyrillic text alone stay unknown.
    """
    raw = (transcript_data or {}).get("language_code")
    code = normalize_language_code(raw if isinstance(raw, str) else None)
    if code:
        return code
    sample = _TIMESTAMP_RE.sub("", (transcript_text or "")[:20000])
    return detect_script_language(sample)


def language_display_name(code: Optional[str]) -> Optional[str]:
    """English name of a known language ("Russian"); None for unknown codes.

    Only names from a fixed table ever reach a prompt, never a raw code.
    """
    return LANGUAGE_NAMES.get(normalize_language_code(code) or "")


def title_language_mismatch(title: Optional[str], segment_text: str, language: Optional[str]) -> bool:
    """True when ``title`` is clearly not in the language ``segment_text`` is spoken in.

    ``language`` is the transcript's language when known. Unclear cases
    return False: a title is only replaced on clear evidence.
    """
    if not title or not segment_text:
        return False
    # Names and brands said in the clip ("GTA", "Godknife") don't count for either language.
    spoken = set(_words(segment_text))
    own_words = " ".join(word for word in _words(title) if word not in spoken)
    title_counts, title_letters = _script_counts(own_words)
    text_counts, text_letters = _script_counts(segment_text)
    if title_letters < 2 or text_letters < MIN_SEGMENT_LETTERS:
        return False
    title_script = max(title_counts, key=title_counts.get)
    if text_counts.get(title_script, 0) / text_letters < MIN_TITLE_SCRIPT_SHARE:
        return True

    code = normalize_language_code(language)
    if not code or code == "en" or title_script != "LATIN" or dominant_script(segment_text) != "LATIN":
        return False
    markers = ENGLISH_MARKERS - _MARKER_COLLISIONS.get(code, set())
    possessive = code not in _POSSESSIVE_NATIVE
    title_hits, title_share = _english_share(_words(title), markers, possessive)
    if title_hits < MIN_TITLE_ENGLISH_MARKERS or title_share < MIN_TITLE_ENGLISH_SHARE:
        return False
    _, text_share = _english_share(_words(segment_text), markers, possessive)
    return text_share < MAX_SEGMENT_ENGLISH_SHARE
