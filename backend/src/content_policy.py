"""Content policy detection: regex word-list engine + asterisk rewrite.

Regex is the primary, free, always-on detector. An optional Ollama-backed
pass (`ollama_borderline_check`, off by default) supplements it for
euphemisms/indirect references regex can't catch by design, batched as one
call per video (see `ai.py::run_with_llm_fallback`).

Categories: sex, drugs, violence (default off), profanity (default off).
Word lists are user-editable per category (`ContentPolicyRepository`); the
lists below are only a starting seed, deliberately short since they're meant
to be curated, not exhaustive.
"""

import re
from typing import Dict, List, Literal, TypedDict

Category = Literal["sex", "drugs", "violence", "profanity"]
Sensitivity = Literal["off", "low", "medium", "high"]

CATEGORIES: List[Category] = ["sex", "drugs", "violence", "profanity"]

# Categories on by default vs. opt-in, per spec ("VIOLENCE (default off),
# PROFANITY (default off)").
DEFAULT_CATEGORIES_ENABLED: Dict[Category, bool] = {
    "sex": True,
    "drugs": True,
    "violence": False,
    "profanity": False,
}

# Each category splits into a "severe" tier (unambiguous terms, included at
# every sensitivity above OFF) and a "borderline" tier (mild euphemisms/
# innuendo, included only at HIGH sensitivity).
DEFAULT_WORD_LISTS: Dict[Category, Dict[str, List[str]]] = {
    "sex": {
        "severe": ["porn", "pornography", "explicit", "nsfw", "nude", "naked", "sex tape"],
        "borderline": ["sexy", "seductive", "thirst trap", "onlyfans"],
    },
    "drugs": {
        "severe": [
            "cocaine", "heroin", "meth", "methamphetamine", "fentanyl",
            "crack", "weed", "marijuana", "ecstasy", "molly",
        ],
        "borderline": ["high af", "blazed", "stoned", "trip balls"],
    },
    "violence": {
        "severe": ["kill", "murder", "stabbing", "shooting", "massacre", "torture"],
        "borderline": ["beat up", "destroy him", "smash his face"],
    },
    "profanity": {
        "severe": ["fuck", "shit", "cunt", "bitch", "asshole"],
        "borderline": ["damn", "hell", "crap", "piss"],
    },
}


class PolicyFlag(TypedDict):
    word: str
    category: str
    start: int
    end: int
    severity: Literal["severe", "borderline"]
    source: Literal["regex", "llm"]


def build_word_boundary_pattern(words: List[str]) -> "re.Pattern[str]":
    """Case-insensitive, word-boundary-aware pattern for a flat word list.

    `\\b` boundaries on both sides mean a substring embedded inside a larger
    word never matches (the Scunthorpe problem) — e.g. "cunt" never matches
    inside "Scunthorpe" since there's no word boundary between "s" and "c".
    Longer phrases are tried first so multi-word entries aren't shadowed by
    a shorter word contained within them.
    """
    if not words:
        return re.compile(r"(?!)")  # never matches
    escaped = sorted((re.escape(w) for w in words if w.strip()), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)


def _tiers_for_sensitivity(sensitivity: Sensitivity) -> List[str]:
    if sensitivity == "off":
        return []
    if sensitivity in ("low", "medium"):
        # No distinct third tier exists between LOW and MEDIUM in the spec;
        # MEDIUM (the default) behaves the same as LOW for regex purposes —
        # both scan the severe tier only. HIGH additionally includes borderline.
        return ["severe"]
    return ["severe", "borderline"]


def scan_text(
    text: str,
    word_lists: Dict[Category, Dict[str, List[str]]],
    sensitivity: Sensitivity,
    categories_enabled: Dict[str, bool],
) -> List[PolicyFlag]:
    """Scan `text` and return every flagged span, sorted by start offset."""
    tiers = _tiers_for_sensitivity(sensitivity)
    if not tiers or not text:
        return []

    flags: List[PolicyFlag] = []
    for category in CATEGORIES:
        if not categories_enabled.get(category, DEFAULT_CATEGORIES_ENABLED[category]):
            continue
        lists = word_lists.get(category, DEFAULT_WORD_LISTS[category])
        for tier in tiers:
            words = lists.get(tier, [])
            pattern = build_word_boundary_pattern(words)
            for match in pattern.finditer(text):
                flags.append(
                    PolicyFlag(
                        word=match.group(0),
                        category=category,
                        start=match.start(),
                        end=match.end(),
                        severity=tier,  # type: ignore[arg-type]
                        source="regex",
                    )
                )
    flags.sort(key=lambda f: f["start"])
    return flags


def rewrite_flagged(text: str, flags: List[PolicyFlag]) -> str:
    """Asterisk every flagged span, preserving the first and last letter.

    "cocaine" -> "c*****e" per spec's literal example (7 chars: first "c",
    5 asterisks, last "e") — note this preserves BOTH ends, not just the
    first letter as the spec's prose alone suggests; the worked example
    takes precedence. Words of length <= 2 are left untouched (asterisking
    would either do nothing or erase the whole word). Processed
    right-to-left by offset so earlier spans' offsets stay valid while later
    ones are rewritten.
    """
    if not flags:
        return text
    result = text
    for flag in sorted(flags, key=lambda f: f["start"], reverse=True):
        start, end = flag["start"], flag["end"]
        original = result[start:end]
        if len(original) <= 2:
            continue
        rewritten = original[0] + "*" * (len(original) - 2) + original[-1]
        result = result[:start] + rewritten + result[end:]
    return result


async def ollama_borderline_check(
    transcript_text: str,
    *,
    allow_gemini: bool = False,
) -> List[str]:
    """Ask the LLM if any phrases in `transcript_text` are borderline (euphemisms,
    indirect references) that the regex engine wouldn't catch.

    One call per video, batched over the full transcript. If Ollama is
    unavailable (and Gemini isn't opted into / configured), returns `[]`
    silently per spec — this is an optional supplement, not a hard requirement.
    """
    from .ai import run_with_llm_fallback  # local import: avoid a circular import at module load

    prompt = (
        "You are screening a video transcript for content-policy purposes. "
        "List any words or short phrases that are borderline references to "
        "sexual content, drugs, violence, or profanity, using euphemisms or "
        "indirect language a simple keyword filter would miss. "
        'Respond with ONLY a JSON array of strings, e.g. ["phrase one", "phrase two"]. '
        "If nothing is borderline, respond with an empty array [].\n\n"
        f"Transcript:\n{transcript_text[:6000]}"
    )
    result, provider = await run_with_llm_fallback(
        prompt=prompt,
        output_type=List[str],
        allow_gemini=allow_gemini,
    )
    if provider == "unavailable" or result is None:
        return []
    return [phrase for phrase in result if isinstance(phrase, str) and phrase.strip()]
