"""Language helpers for workflow planning: punctuation, scripts and labels.

Sentence punctuation here covers CJK, Arabic, Devanagari, Armenian and
Ethiopic marks. ``media.common.SENTENCE_END_RE`` stays ASCII-only on purpose:
highlights must not change.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Optional, Sequence

_CLOSERS = r"""["'”’»)\]}」』）]*"""
SENTENCE_END_RE = re.compile(r"""(?:[.!?。！？…‼⁇⁈⁉؟۔।॥։።፧\u037e]|\.{3})""" + _CLOSERS + "$")
CLAUSE_MARK_RE = re.compile(r"""[,;:—–、，；：،؛፣፤]""" + _CLOSERS + "$")
QUESTION_END_RE = re.compile(r"""[?？؟፧\u037e]""" + _CLOSERS + "$")
# Greek writes its question mark as ";" (U+037E, or often the ASCII semicolon).
_GREEK_QUESTION_RE = re.compile(r"""[;\u037e]""" + _CLOSERS + "$")
_ELLIPSIS_END_RE = re.compile(r"""(?:…|\.{3})""" + _CLOSERS + "$")

NO_SPACE_LANGS = {"zh", "ja", "th", "lo", "km", "my", "yue"}

# Old or regional codes some ASR providers report, mapped to ISO 639-1.
_LANGUAGE_ALIASES = {"iw": "he", "in": "id", "ji": "yi", "jw": "jv", "nb": "no", "nn": "no", "tl": "fil", "cmn": "zh"}

LANGUAGE_NAMES = {
    "en": "English", "de": "German", "es": "Spanish", "pt": "Portuguese", "fr": "French", "it": "Italian",
    "nl": "Dutch", "id": "Indonesian", "tr": "Turkish", "pl": "Polish", "ru": "Russian", "hi": "Hindi",
    "ja": "Japanese", "zh": "Chinese", "ko": "Korean", "ar": "Arabic", "uk": "Ukrainian", "sv": "Swedish",
    "da": "Danish", "no": "Norwegian", "fi": "Finnish", "cs": "Czech", "sk": "Slovak", "ro": "Romanian",
    "hu": "Hungarian", "el": "Greek", "he": "Hebrew", "th": "Thai", "vi": "Vietnamese", "ms": "Malay",
    "fa": "Persian", "ur": "Urdu", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
    "gu": "Gujarati", "pa": "Punjabi", "ca": "Catalan", "hr": "Croatian", "sr": "Serbian", "bg": "Bulgarian",
    "sl": "Slovenian", "lt": "Lithuanian", "lv": "Latvian", "et": "Estonian", "fil": "Filipino",
    "sw": "Swahili", "yue": "Cantonese", "lo": "Lao", "km": "Khmer", "my": "Burmese",
}

PART_LABEL = {
    "en": "Part {n}", "de": "Teil {n}", "es": "Parte {n}", "pt": "Parte {n}", "it": "Parte {n}",
    "fr": "Partie {n}", "nl": "Deel {n}", "id": "Bagian {n}", "tr": "Bölüm {n}", "pl": "Część {n}",
    "ru": "Часть {n}", "hi": "भाग {n}", "ja": "パート{n}", "zh": "第{n}部分", "ko": "{n}부", "ar": "الجزء {n}",
    "uk": "Частина {n}", "sv": "Del {n}", "da": "Del {n}", "no": "Del {n}", "fi": "Osa {n}",
    "cs": "Část {n}", "ro": "Partea {n}", "hu": "{n}. rész", "el": "Μέρος {n}", "he": "חלק {n}",
    "vi": "Phần {n}", "th": "ตอนที่ {n}", "ms": "Bahagian {n}", "yue": "第{n}部分",
}

# Lowercase openers that lean on something said earlier ("he", "that's why").
# Matched at the start of a unit after punctuation and hesitations are removed,
# then again after leading connectives ("and", "so", "but") are removed, so
# "And so he said" counts like "he said".
BACK_REFERENCE_OPENERS = {
    "en": ("as i said", "like i said", "that's why", "and that", "so that", "which is why", "this is why",
           "he ", "she ", "they ", "it ", "that ", "this ", "these ", "those ", "at least", "so yeah",
           "after that", "and then", "then ", "also ", "and the ", "but the ", "because ", "i say it because",
           "which ", "how does that", "what does that", "is that", "like this", "like that", "him ", "them ",
           "it's ", "that's ", "they're ", "he's ", "she's ", "and here ", "and instead", "we then ",
           "it amounts to", "what i mean by that", "and really", "and the thing is", "at the risk of"),
    "de": ("wie gesagt", "deshalb", "darum", "und das", "er ", "sie ", "es ", "das ", "dies ",
           "diese ", "dieser ", "dieses ", "diesen ", "diesem ", "vielen dank", "danke", "solche", "solchen",
           "danach", "außerdem", "dann ", "zweitens", "und hier ", "und stattdessen", "wir dann ",
           "das heißt", "was ich damit meine", "und wirklich", "die sache ist", "auf die gefahr hin"),
    "es": ("como dije", "por eso", "y eso", "él ", "ella ", "eso ", "esto "),
    "pt": ("como eu disse", "por isso", "e isso", "ele ", "ela ", "isso ", "isto "),
    "fr": ("comme je disais", "c'est pour ça", "et ça", "il ", "elle ", "ça ", "cela "),
    "hi": ("इसलिए", "वो ", "वह ", "उसने", "उसे ", "उसको", "यह ", "ये ", "तो ", "इसमें"),
}
# Openers that only make sense after a list was announced ("The second tip...").
# A standalone clip may not open on one; a series part may (the list is in the previous part).
LIST_ITEM_OPENERS = {
    "en": ("the first", "the second", "the third", "the next", "the other", "another "),
    "de": ("der erste", "die erste", "das erste", "der zweite", "die zweite", "das zweite", "zweitens", "drittens"),
}
# A list position after a lead-in ("I think the 4th sort of little tip"), and
# lines that only make sense as a reply or a resumption ("Okay, like", "Dude,").
# Strict only: a series part may open on these, a standalone clip may not. A
# repair never prepends for these (the line before is the previous thought).
LIST_POSITION_RE = {
    "en": re.compile(r"^(i think |so )?(the )?(\d+(st|nd|rd|th)|first|second|third|fourth|fifth|next|other)\b"),
    "de": re.compile(r"^(ich denke |also )?(der |die |das )?(erste\w*|zweite\w*|dritte\w*|vierte\w*|nächste\w*|andere\w*)\b"),
}
DISCOURSE_OPENERS = {
    "en": ("now ", "okay ", "ok ", "dude ", "looking good"),
    "de": ("nun ", "okay ", "also gut", "sieht gut aus"),
}
# Words that only join a unit to the one before it; removed before matching.
_CONNECTIVES = {
    "en": ("and so", "and", "but", "so", "then", "also", "because", "or"),
    "de": ("und", "aber", "also", "dann", "auch"),
}
# A part that ends on one of these points forward ("Number six is feedback.").
FORWARD_HOOKS = {
    "en": (
        r"^(all right |alright |okay |ok )?(number|step|habit|tip|rule|lesson) \w+",
        r"^(but )?here's", r"\bthe question is\b", r"^but then\b", r"^and that's when\b",
        r"\bwhat happens\b", r"\bit turns out\b", r"\blet's (see|find out|dig)\b",
        r"\bthe problem is\b", r"\byou know (who|what|why)\b", r"\bdoesn't feel like\b",
    ),
    "de": (r"\bdie frage ist\b", r"^doch dann\b", r"^und dann\b", r"^warum\b"),
}
# A part that ends on one of these closes its own loop ("So that's rest.").
RESOLUTION_CLOSERS = {
    "en": (r"^so that's\b", r"^that's (it|why|how)\b", r"^in short\b"),
    "de": (r"^also das\b", r"^das war'?s\b"),
}
# A unit that only acknowledges ("Thanks.", "Vielen Dank, Björn.") never opens a part.
_ACK_PREFIXES = {"en": ("thank you", "thanks"), "de": ("vielen dank", "danke")}
_ACK_WORDS = {
    "en": {"yeah", "yes", "right", "all", "alright", "okay", "ok", "exactly", "sure", "good", "great", "thank", "you", "thanks"},
    "de": {"ja", "genau", "danke", "gut", "okay", "richtig", "stimmt"},
}
_HESITATIONS = {"um", "umm", "uh", "uhh", "uhm", "erm", "hmm", "äh", "ähm", "öh", "eh", "euh", "ehm"}

# Tokens ending in "." that rarely end a sentence. Lowercase, without closers.
_ABBREVIATIONS = {
    "mr.", "mrs.", "ms.", "dr.", "prof.", "st.", "jr.", "sr.", "vs.", "e.g.", "i.e.", "approx.",
    "z.b.", "bzw.", "ca.", "d.h.", "u.a.", "evtl.", "ggf.", "inkl.", "sog.", "vgl.", "hr.", "fr.",
    "sra.", "srta.", "dra.", "ud.", "uds.", "p.ej.", "mme.", "mlle.", "env.", "sig.", "dott.",
}
# "No." and "Nr." abbreviate "number" only before one ("No. 5"); otherwise
# "No." is the answer that ends a sentence.
_NUMBER_ABBREVIATIONS = {"no.", "nr.", "nos."}
# Initials are capitals ("J.", "U.S."). A lone "I." is the pronoun ending a sentence.
_INITIALS_RE = re.compile(r"^(?:[^\W\d_]\.)+$")
_ORDINAL_RE = re.compile(r"^\d{1,2}\.$")
_GERMAN_MONTHS = {
    "januar", "jänner", "februar", "märz", "april", "mai", "juni", "juli", "august", "september",
    "oktober", "november", "dezember",
}
# A word ending in "." directly before a lowercase word, with less silence than
# this, continues the sentence (an abbreviation or a trailing ellipsis).
_CONTINUATION_MAX_GAP = 0.3


def normalize_language_code(code: Optional[str]) -> Optional[str]:
    """ISO 639-1 (or 639-3 where there is no 2-letter code) from an ASR code such as en_us."""
    if not isinstance(code, str) or not code.strip():
        return None
    base = re.split(r"[_\-]", code.strip().lower(), maxsplit=1)[0]
    if not base.isalpha():
        return None
    return _LANGUAGE_ALIASES.get(base, base)


def language_name(code: Optional[str]) -> str:
    """English name of a language for prompts; unknown codes get a neutral phrase."""
    return LANGUAGE_NAMES.get(normalize_language_code(code) or "", "the language the speakers mainly use")


def part_label(code: Optional[str], n: int) -> str:
    """Localized "Part {n}" label. Unknown languages get English."""
    template = PART_LABEL.get(normalize_language_code(code) or "", PART_LABEL["en"])
    return template.format(n=n)


def _script(char: str) -> Optional[str]:
    """Coarse script of one character, for the scripts that identify a language."""
    cp = ord(char)
    if 0x3040 <= cp <= 0x30FF or 0x31F0 <= cp <= 0x31FF or 0xFF66 <= cp <= 0xFF9F:
        return "kana"
    if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF or 0x20000 <= cp <= 0x2FA1F or 0xF900 <= cp <= 0xFAFF:
        return "han"
    if 0xAC00 <= cp <= 0xD7AF or 0x1100 <= cp <= 0x11FF or 0x3130 <= cp <= 0x318F:
        return "hangul"
    for low, high, name in _SCRIPT_RANGES:
        if low <= cp <= high:
            return name
    return None


_SCRIPT_RANGES = (
    (0x0E00, 0x0E7F, "thai"), (0x0E80, 0x0EFF, "lao"), (0x1780, 0x17FF, "khmer"), (0x1000, 0x109F, "myanmar"),
    (0x0900, 0x097F, "devanagari"), (0x0980, 0x09FF, "bengali"), (0x0A00, 0x0A7F, "gurmukhi"),
    (0x0A80, 0x0AFF, "gujarati"), (0x0B80, 0x0BFF, "tamil"), (0x0C00, 0x0C7F, "telugu"),
    (0x0600, 0x06FF, "arabic"), (0x0750, 0x077F, "arabic"), (0x0590, 0x05FF, "hebrew"),
    (0x0370, 0x03FF, "greek"),
)
_SCRIPT_LANGUAGE = {
    "hangul": "ko", "thai": "th", "lao": "lo", "khmer": "km", "myanmar": "my", "devanagari": "hi",
    "bengali": "bn", "gurmukhi": "pa", "gujarati": "gu", "tamil": "ta", "telugu": "te", "hebrew": "he",
    "greek": "el",
}
_NO_SPACE_SCRIPTS = {"kana", "han", "thai", "lao", "khmer", "myanmar"}
# Languages that inflect by appending case suffixes or particles to the word
# (투자를, yatırımlar, talossa): a search term is found at the start of a word.
SUFFIXING_LANGS = {
    "ko", "tr", "az", "kk", "ky", "uz", "tk", "tt", "ba", "fi", "et", "hu", "mn", "eu", "ka",
    "ta", "te", "kn", "ml",
}
_URDU_LETTERS = set("ٹڈڑںےۓھ")
_PERSIAN_LETTERS = set("پچژگ")


def _script_counts(text: str, limit: int = 6000) -> tuple[dict[str, int], int]:
    counts: dict[str, int] = {}
    letters = 0
    for char in text[:limit]:
        if not char.isalpha():
            continue
        letters += 1
        script = _script(char)
        if script:
            counts[script] = counts.get(script, 0) + 1
    return counts, letters


def detect_script_language(text: str) -> Optional[str]:
    """A language code when one script that identifies it makes up most letters.

    Latin and Cyrillic text return None: the script alone doesn't say which
    language it is. Arabic script tells Urdu and Persian apart by their letters.
    """
    counts, letters = _script_counts(text or "")
    if not letters:
        return None
    cjk = counts.get("kana", 0) + counts.get("han", 0)
    if cjk * 2 > letters:
        return "ja" if counts.get("kana", 0) * 20 >= cjk else "zh"
    script, count = max(counts.items(), key=lambda item: item[1], default=(None, 0))
    if not script or count * 2 <= letters:
        return None
    if script == "arabic":
        sample = set(text[:6000])
        if sample & _URDU_LETTERS:
            return "ur"
        return "fa" if sample & _PERSIAN_LETTERS else "ar"
    return _SCRIPT_LANGUAGE.get(script)


def is_no_space(language: Optional[str], sample_text: str) -> bool:
    """Whether words are written without spaces: the language, or > 50% CJK/Thai letters."""
    if normalize_language_code(language) in NO_SPACE_LANGS:
        return True
    counts, letters = _script_counts(sample_text or "")
    if not letters:
        return False
    return sum(counts.get(script, 0) for script in _NO_SPACE_SCRIPTS) * 2 > letters


def inflects_by_suffix(language: Optional[str], sample_text: str) -> bool:
    """Whether words take suffixes or particles: the language, or mostly Hangul text."""
    code = normalize_language_code(language)
    if code:
        return code in SUFFIXING_LANGS
    return detect_script_language(sample_text or "") in SUFFIXING_LANGS


def _bare_token(text: str) -> str:
    return re.sub(_CLOSERS + "$", "", (text or "").strip())


def starts_lowercase(text: Optional[str]) -> bool:
    """Whether the first letter is lowercase. Scripts without case never are."""
    for char in (text or "").strip():
        if char.isalpha():
            return char.islower()
        if char.isdigit():
            return False
    return False


def _greek_question(text: str, language: Optional[str]) -> bool:
    return normalize_language_code(language) == "el" and bool(_GREEK_QUESTION_RE.search(text))


def ends_question(text: str, language: Optional[str] = None) -> bool:
    """Whether a word or line ends on a question mark (Greek ";" included)."""
    stripped = (text or "").strip()
    return bool(QUESTION_END_RE.search(stripped)) or _greek_question(stripped, language)


def _starts_with_digit(text: Optional[str]) -> bool:
    stripped = (text or "").strip().lstrip("#(")
    return bool(stripped) and stripped[0].isdigit()


def ends_sentence(
    text: str,
    next_text: Optional[str] = None,
    gap: float = 0.0,
    language: Optional[str] = None,
) -> bool:
    """Whether a word ends a sentence.

    ``SENTENCE_END_RE`` decides, except for tokens that only look final:
    abbreviations ("Dr.", "z.B.", "No. 5"), initials ("J.", "U.S.") and a
    trailing ellipsis ("so...") when the next word follows quickly in
    lowercase, and German ordinals before a month ("3. Oktober").
    """
    if not SENTENCE_END_RE.search((text or "").strip()) and not _greek_question((text or "").strip(), language):
        return False
    if next_text is None:
        return True
    bare = _bare_token(text)
    lowered = bare.casefold()
    quick = gap < _CONTINUATION_MAX_GAP
    if lowered in _ABBREVIATIONS and quick:
        return False
    if lowered in _NUMBER_ABBREVIATIONS and quick and _starts_with_digit(next_text):
        return False
    if _INITIALS_RE.match(bare) and bare == bare.upper() and bare != "I." and quick:
        return False
    if _ELLIPSIS_END_RE.search(text.strip()) and quick and starts_lowercase(next_text):
        return False
    if (
        normalize_language_code(language) == "de"
        and _ORDINAL_RE.match(bare)
        and _bare_token(next_text).casefold().strip(".,") in _GERMAN_MONTHS
    ):
        return False
    return True


def _opening_text(text: str) -> str:
    """Casefolded words with punctuation (not inner apostrophes) and leading hesitations removed.

    Combining marks stay: Devanagari vowel signs are part of the word.
    """
    lowered = unicodedata.normalize("NFC", text or "").casefold().replace("’", "'").replace("‘", "'")
    spaced = "".join(" " if unicodedata.category(char)[0] in "PS" and char != "'" else char for char in lowered)
    tokens = [token.strip("'") for token in spaced.split()]
    tokens = [token for token in tokens if token]
    while tokens and tokens[0] in _HESITATIONS:
        tokens.pop(0)
    return " ".join(tokens) + " "


def _without_connectives(opening: str, code: str) -> str:
    connectives = _CONNECTIVES.get(code, ())
    changed = True
    while changed:
        changed = False
        for connective in connectives:
            if opening.startswith(connective + " ") and len(opening) > len(connective) + 1:
                opening = opening[len(connective) + 1 :]
                changed = True
                break
    return opening


def opens_with_back_reference(text: str, code: Optional[str], *, strict: bool = False) -> bool:
    """Whether a unit opens by pointing back at something unheard ("That's why...").

    Only languages with a list; False for others. Openers ending in a space
    match a whole word ("he " never matches "hello"). Leading connectives
    ("And so he...") are looked through. ``strict`` (standalone clips) also
    counts list-item openers ("The second tip is...").
    """
    language = normalize_language_code(code) or ""
    openers = BACK_REFERENCE_OPENERS.get(language)
    if not openers:
        return False
    if strict:
        openers = openers + LIST_ITEM_OPENERS.get(language, ())
    opening = _opening_text(text)
    bare = _without_connectives(opening, language)
    if any(opening.startswith(opener) or bare.startswith(opener) for opener in openers):
        return True
    return strict and leans_on_a_list_or_reply(text, code)


def leans_on_a_list_or_reply(text: str, code: Optional[str]) -> bool:
    """A list position ("I think the 4th tip") or a bare reply ("Okay, like"): only a later start fixes these."""
    language = normalize_language_code(code) or ""
    opening = _opening_text(text)
    bare = _without_connectives(opening, language)
    position = LIST_POSITION_RE.get(language)
    if position is not None and (position.search(opening) or position.search(bare)):
        return True
    discourse = DISCOURSE_OPENERS.get(language, ())
    return any(opening.startswith(item) or bare.startswith(item) for item in discourse)


_PRONOUNS = {
    "en": {"he", "she", "they", "him", "her", "them", "his", "their", "it", "it's", "that's", "they're", "he's", "she's"},
    "de": {"er", "ihn", "ihm"},
}
_DEMONSTRATIVES = {"en": {"that", "this", "those", "these"}, "de": {"diese", "dieser", "dieses", "diesen", "diesem", "solche", "solchen"}}
# After "that" these words mean a conjunction ("I think that we..."), not a reference.
_CONJUNCTION_FOLLOWERS = {
    "i", "you", "we", "they", "he", "she", "it", "the", "a", "an", "there", "if", "when", "our", "your", "my",
    "his", "her", "their", "people", "everyone", "nobody", "someone",
}
_TIME_NOUNS = {"video", "episode", "week", "year", "month", "morning", "evening", "time", "day", "point", "way"}
REFERENCE_SCAN_TOKENS = 8
PRONOUN_SCAN_TOKENS = 3


def unresolved_reference(text: str, code: Optional[str]) -> bool:
    """Whether a beat's opening leans on something unheard, anywhere in its first words.

    ``opens_with_back_reference`` only reads the very start; this also finds
    "And so I asked him", "If you operate in that frame" and "a model like
    this": a pronoun among the first three words, or a demonstrative
    (that/this/those/these + noun) among the first eight.
    """
    if opens_with_back_reference(text, code):
        return True
    language = normalize_language_code(code) or ""
    pronouns, demonstratives = _PRONOUNS.get(language), _DEMONSTRATIVES.get(language)
    if not pronouns:
        return False
    opening = _without_connectives(_opening_text(text), language)
    tokens = opening.split()
    if any(token in pronouns for token in tokens[:PRONOUN_SCAN_TOKENS]):
        return True
    if language == "de" and " wie hier " in f" {opening} ":
        return True  # "in solchen Zwischenlagern wie hier": a place only the picture shows
    for position, token in enumerate(tokens[:REFERENCE_SCAN_TOKENS]):
        if token not in demonstratives:
            continue
        following = tokens[position + 1] if position + 1 < len(tokens) else ""
        if following in _TIME_NOUNS:
            continue
        if language == "en" and token == "that" and following in _CONJUNCTION_FOLLOWERS:
            continue
        return True
    return False


def _matches(patterns: Sequence[str], text: str) -> bool:
    return any(re.search(pattern, text) for pattern in patterns)


def forward_hook(text: str, code: Optional[str]) -> bool:
    """Whether a unit points ahead ("Number six is feedback.", "But then...")."""
    patterns = FORWARD_HOOKS.get(normalize_language_code(code) or "")
    return bool(patterns) and _matches(patterns, _opening_text(text).strip())


def resolution_closer(text: str, code: Optional[str]) -> bool:
    """Whether a unit wraps up what came before ("So that's rest.")."""
    patterns = RESOLUTION_CLOSERS.get(normalize_language_code(code) or "")
    return bool(patterns) and _matches(patterns, _opening_text(text).strip())


def is_acknowledgement(text: str, code: Optional[str]) -> bool:
    """A unit that only thanks or agrees ("Thank you.", "Vielen Dank, Björn.", "Yeah, right.")."""
    language = normalize_language_code(code) or ""
    opening = _opening_text(text).strip()
    if not opening:
        return False
    if any(opening.startswith(prefix) for prefix in _ACK_PREFIXES.get(language, ())):
        return True
    words = _ACK_WORDS.get(language)
    return bool(words) and all(token in words for token in opening.split())
