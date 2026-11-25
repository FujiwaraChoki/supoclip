"""Where a highlight opens and closes: always on a sentence, in any language.

The analysis picks a moment with whole-second labels, read off transcript
lines that are not sentences (a long speaker turn is cut into 24-word lines).
Rendering those labels as they are opens clips on "de feria es muy común
que..." and closes them wherever the second ran out. This module turns the
labels into a first and a last *sentence unit* (``workflows.units``: split on
punctuation of every script, on speaker changes and on real pauses, with
over-long run-ons divided at a breath), so a cut can only land between two
units, never inside a word or a clause.

On top of the mechanical guarantee it applies the rules an editor would:

- **Opening.** The sentence that opens in the labelled second, else the
  sentence the label falls into (its beginning if that is close, the next
  sentence otherwise). A bare agreement ("Yeah, exactly.") is skipped; an
  opening that leans on the line before it (a conjunction, "he", "as I
  said") takes that line in when the same voice said it just before.
- **Closing.** The sentence the label falls into is finished, not cut. A
  clip does not close on a question whose short answer follows, nor on a
  line that only announces the next thought ("But then...").
- **Length.** The cap moves the end back to the last sentence that fits; it
  never cuts one. When taking in context is what broke the cap, the context
  goes first and the payoff stays.

The rules read ``workflows.language``'s lists (English and German are the most
complete) plus the conjunction and agreement lists here for the languages the
service sees most. A language without lists still gets the mechanical part.
"""

from __future__ import annotations

import os
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .common import logger

_WORD_CHARS = re.compile(r"[\W_]+", re.UNICODE)

# The labelled second holds the intended first word; a sentence opening within this much after the label wins.
LABEL_SECONDS = 1.0
# A label inside a sentence goes back to its beginning when that is this close, else on to the next sentence.
START_BACK_SECONDS = 6.0
START_FORWARD_SECONDS = 4.0
# How far the opening may move back in all (to a sentence start, then for context).
START_REWIND_LIMIT_SECONDS = 12.0
# Context is the line just before, by the same voice: at most this long, after at most this much silence.
CONTEXT_MAX_SECONDS = 9.0
CONTEXT_MAX_GAP_SECONDS = 1.0
CONTEXT_MAX_STEPS = 2
# A bare agreement or a scrap this short at the opening is skipped rather than explained.
SKIP_MAX_SECONDS = 3.0
# A sentence the end label falls into is finished when that takes at most this long, else it is left out.
END_EXTEND_SECONDS = 7.0
# A question at the end takes its answer in when the answer starts this soon and is this short.
ANSWER_MAX_GAP_SECONDS = 1.2
ANSWER_MAX_SECONDS = 7.0
# A cut after a unit at least this strong is one a viewer does not notice (``workflows.boundaries``).
STRONG_END = 0.75
# Silence this long between a label and the nearest sentence means the pick's edge is action, not speech.
SILENT_EDGE_SECONDS = 1.5
# A speaker change inside a written sentence with less silence than this is a label a word off, not a new sentence.
MISLABELLED_GAP_SECONDS = 0.3
# A pause shorter than this before a lower-case word is a breath inside a sentence (as ``workflows.units``).
CONTINUATION_PAUSE_SECONDS = 2.5
# With no sentence start near the label (speech without punctuation), a silence at least this long is the opening.
OPENING_PAUSE_SECONDS = 0.4
# A reply this short after a question (within REPLY_MAX_GAP_SECONDS) needs the question.
REPLY_MAX_WORDS = 6
REPLY_MAX_GAP_SECONDS = 2.0
# Speech that runs straight from one sentence into the next leaves no silence to cut in: with less than this
# between two words, the cut goes to the quietest instant around the word boundary the transcript gives
# (its timings are good to a few hundredths of a second, which is the length of a consonant).
TIGHT_GAP_SECONDS = 0.2
QUIET_SEARCH_AFTER = 0.05
# At the opening the search barely enters the first word: a softly spoken first syllable is the quietest
# thing around, and a cut there takes it off (a "Pero" became "ro").
QUIET_SEARCH_INTO_WORD = 0.03
QUIET_WINDOW_SECONDS = 0.02
QUIET_HOP_SECONDS = 0.005
QUIET_SAMPLE_RATE = 16000
# The padding kept before the first word and after the last is silence by the transcript's times only. A
# transcript drops repeated words and times a word spoken over another voice late, so that padding can hold
# speech: sound that rises this much above the dip after the last word (before the first), near the level of
# the speech around it, for this long. The cut then goes to the dip. A word's own last consonant is shorter
# and quieter than that.
UNTIMED_RISE_DB = 10.0
UNTIMED_NEAR_SPEECH_DB = 12.0
UNTIMED_SUSTAIN_SECONDS = 0.15
UNTIMED_SUSTAIN_SHARE = 0.7
UNTIMED_SPEECH_SECONDS = 1.5      # the speech level is measured over this much of the clip at that edge
UNTIMED_LOOK_PAST_SECONDS = 0.2   # read this far past the cut, to tell a sustained sound from a click
# A sentence the speaker starts, drops and starts again ("As for the... As for the oven, we have...") is a
# false start: the next sentence opens with the same words, at least this many.
RESTART_MIN_WORDS = 2
# Edge review (``ai.review_clip_edges``): sentences shown beyond the clip's edge and inside it, how far an
# edge may move, and how closely stored edges must still match the transcript.
EDGE_CONTEXT_UNITS = 6
EDGE_INSIDE_UNITS = 4
EDGE_MAX_SHIFT_SECONDS = 20.0
REVIEWED_EDGE_TOLERANCE = 0.3
# What the rules may shorten a pick to, as a share of what was picked.
MIN_KEPT_SHARE = 0.6

# First words that join a sentence to the one before it. Matched on the first token only (accents kept:
# Italian "e" is "and", "è" is "is").
_CONNECTIVES: Dict[str, frozenset] = {
    "en": frozenset({"and", "but", "so", "because", "or", "then", "also", "plus", "which", "although", "though",
                     "anyway", "anyways", "meanwhile", "otherwise", "instead", "therefore", "hence", "cause", "cuz"}),
    "es": frozenset({"y", "e", "pero", "porque", "pues", "entonces", "además", "aunque", "sino", "ni", "luego",
                     "así", "tampoco", "también"}),
    "pt": frozenset({"e", "mas", "porque", "pois", "então", "ou", "aí", "também", "porém", "portanto", "daí"}),
    "it": frozenset({"e", "ed", "ma", "perché", "quindi", "allora", "oppure", "però", "anche", "poi", "cioè",
                     "dunque", "infatti", "inoltre"}),
    "de": frozenset({"und", "aber", "also", "denn", "weil", "oder", "dann", "sondern", "außerdem", "deshalb",
                     "deswegen", "doch", "trotzdem", "danach"}),
    "fr": frozenset({"et", "mais", "donc", "car", "parce", "ou", "puis", "alors", "aussi", "ensuite", "pourtant"}),
    "pl": frozenset({"i", "a", "ale", "bo", "więc", "lub", "albo", "czyli", "potem", "oraz", "jednak", "ponieważ",
                     "dlatego", "też"}),
    "nl": frozenset({"en", "maar", "dus", "want", "of", "omdat", "toen", "ook", "daarna"}),
    "tr": frozenset({"ve", "ama", "fakat", "çünkü", "yani", "sonra", "ayrıca"}),
    "ru": frozenset({"и", "а", "но", "потому", "или", "потом", "также", "поэтому", "значит", "зато"}),
}
# Words a line consists of when it only agrees or reacts.
_AGREEMENTS: Dict[str, frozenset] = {
    "en": frozenset({"yeah", "yes", "yep", "yup", "right", "okay", "ok", "exactly", "totally", "absolutely", "sure",
                     "true", "indeed", "mhm", "mm", "hmm", "oh", "ah", "wow", "nice", "cool", "no", "nah", "well",
                     "alright", "all", "definitely", "correct", "great", "good", "thanks", "thank", "you"}),
    "es": frozenset({"sí", "si", "claro", "vale", "exacto", "exactamente", "bueno", "ya", "no", "ok", "okay",
                     "total", "eso", "ajá", "pues", "gracias"}),
    "pt": frozenset({"sim", "claro", "exato", "exatamente", "pois", "é", "tá", "ok", "não", "isso", "obrigado"}),
    "it": frozenset({"sì", "si", "esatto", "esattamente", "certo", "ok", "okay", "va", "bene", "no", "già",
                     "ecco", "grazie"}),
    "de": frozenset({"ja", "genau", "okay", "ok", "richtig", "stimmt", "nein", "gut", "klar", "danke", "ne"}),
    "fr": frozenset({"oui", "ouais", "voilà", "exactement", "d'accord", "non", "bon", "ok", "okay", "merci"}),
    "pl": frozenset({"tak", "no", "dokładnie", "właśnie", "nie", "dobra", "okej", "ok", "jasne", "dzięki"}),
    "nl": frozenset({"ja", "precies", "oké", "ok", "nee", "klopt", "goed", "juist"}),
}
# English openers that need the line before them (ported from the first highlights snap). "I", "we" and
# "this" are left out on purpose: they often start a real claim.
_WEAK_OPENERS_EN = frozenset({"like", "dude", "yeah", "yes", "yep", "yup", "well", "right", "okay", "ok", "exactly",
                              "totally", "absolutely", "he", "she", "they", "them", "him", "her",
                              "he's", "she's", "they're", "they've", "he'd", "she'd", "they'd"})
_BACK_REFERENCES_EN = ("we mentioned", "i mentioned", "you mentioned", "as i said", "like i said", "i said",
                       "we said", "before we started", "that point", "that sentiment", "he made", "she made",
                       "they made", "that's why", "which is why", "this is why")
_HESITATIONS = frozenset({"um", "umm", "uh", "uhh", "uhm", "erm", "hmm", "äh", "ähm", "öh", "eh", "euh", "ehm", "mm"})


def _opening_tokens(text: str) -> list:
    """Lower-case words of a unit, punctuation removed (inner apostrophes kept), leading hesitations dropped."""
    lowered = unicodedata.normalize("NFC", text or "").casefold().replace("’", "'").replace("‘", "'")
    spaced = "".join(" " if unicodedata.category(char)[0] in "PS" and char != "'" else char for char in lowered)
    tokens = [token.strip("'") for token in spaced.split()]
    tokens = [token for token in tokens if token]
    while tokens and tokens[0] in _HESITATIONS:
        tokens.pop(0)
    return tokens


def is_agreement(text: str, language: Optional[str]) -> bool:
    """A line that only agrees or reacts ("Yeah, exactly.", "Sí, claro.", "Ja, genau.")."""
    words = _AGREEMENTS.get(language or "")
    tokens = _opening_tokens(text)
    return bool(words) and bool(tokens) and len(tokens) <= 4 and all(token in words for token in tokens)


# Two-word openers that restate the line before ("that is", "in other words").
_RESTATEMENTS: Dict[str, frozenset] = {
    "en": frozenset({"in other", "that is", "meaning that"}),
    "es": frozenset({"es decir", "o sea"}),
    "pt": frozenset({"ou seja", "quer dizer"}),
    "de": frozenset({"das heißt", "sprich also"}),
    "fr": frozenset({"c'est-à-dire", "autrement dit"}),
    "it": frozenset({"vale a", "in altre"}),
}


def leans_back(text: str, language: Optional[str]) -> bool:
    """Whether a sentence opens on something only the line before explains (a conjunction, "he", "as I said")."""
    tokens = _opening_tokens(text)
    if not tokens:
        return False
    if tokens[0] in _CONNECTIVES.get(language or "", ()):
        return True
    if " ".join(tokens[:2]) in _RESTATEMENTS.get(language or "", ()):
        return True
    if language == "en":
        if tokens[0] in _WEAK_OPENERS_EN:
            return True
        phrase = " ".join(tokens[:12])
        return any(reference in phrase for reference in _BACK_REFERENCES_EN)
    return False


def _stands_alone(text: str, language: Optional[str]) -> bool:
    """Whether a line can open a clip: it neither leans on, agrees with, nor wraps up what came before."""
    from ..workflows.language import opens_with_back_reference, resolution_closer

    return not (
        leans_back(text, language)
        or is_agreement(text, language)
        or opens_with_back_reference(text, language)
        or resolution_closer(text, language)
    )


def _language(index: Any) -> str:
    """The transcript's language for the opening rules. Sidecars cached before the language was recorded get
    the English rules, as every transcript did before these were per language."""
    return index.language or "en"


def _strong_end(index: Any, uid: int) -> bool:
    return index.boundary_strength(uid) >= STRONG_END


def _clean_end(index: Any, uid: int) -> bool:
    """The unit ends a written sentence (or the transcript): the cut a viewer hears as an ending."""
    return index.unit(uid).end_kind in ("sentence", "eof")


def _opens_sentence(index: Any, uid: int) -> bool:
    """Whether unit ``uid`` begins a sentence.

    A unit after a speaker change or a pause normally does. When it carries on in lower case right after a
    unit that did not end its sentence ("What | do you think of pineapple?": the speaker labels changed one
    word late), it is the middle of that sentence.
    """
    from ..workflows.language import starts_lowercase

    unit = index.unit(uid)
    if not unit.starts_sentence:
        return False
    if uid <= 1:
        return True
    before = index.unit(uid - 1)
    if before.end_kind == "speaker":
        # The voice changed in the middle of a written sentence. Carrying on in lower case ("Pero si no entra,
        # | también aquí hay tres cartas") or without a breath ("Ach, sind die | Kisten.") it is the same
        # sentence: the speaker labels are a word or a clause off.
        return not (starts_lowercase(unit.text) or before.gap_after < MISLABELLED_GAP_SECONDS)
    if before.end_kind == "pause" and before.gap_after < CONTINUATION_PAUSE_SECONDS:
        return not starts_lowercase(unit.text)
    return True


def _opening_unit(index: Any, label: float) -> int:
    """Id of the unit a clip labelled to start at ``label`` opens on (always a unit's first word)."""
    units = index.units
    in_second = [u for u in units if label - 0.02 <= u.start < label + LABEL_SECONDS]
    for unit in in_second:
        if _opens_sentence(index, unit.id):
            return unit.id
    uid = index.unit_at(label)
    unit = index.unit(uid)
    if unit.start < label - 0.02:
        # The label falls inside this unit.
        following = index.unit(uid + 1) if uid < len(units) else None
        back = label - unit.start
        forward = following.start - label if following else float("inf")
        if back > START_BACK_SECONDS and forward <= START_FORWARD_SECONDS:
            uid = following.id
    # A unit that carries on a sentence (after a comma, a breath, a forced split) is no place to open.
    first = uid
    while not _opens_sentence(index, first) and first > 1:
        if label - index.unit(first - 1).start > START_REWIND_LIMIT_SECONDS:
            break
        first -= 1
    if _opens_sentence(index, first):
        return first
    ahead = uid
    while ahead < len(units) and not _opens_sentence(index, ahead):
        ahead += 1
        if index.unit(ahead).start - label > START_FORWARD_SECONDS:
            break
    if _opens_sentence(index, ahead) and index.unit(ahead).start - label <= START_FORWARD_SECONDS:
        return ahead
    # Speech without punctuation or capitals: no unit near the label opens a sentence. The longest silence
    # near it is where a thought begins.
    near = [
        unit for unit in units
        if label - START_BACK_SECONDS <= unit.start <= label + START_FORWARD_SECONDS and unit.id > 1
    ]
    quiet = max(near, key=lambda unit: (index.unit(unit.id - 1).gap_after, -abs(unit.start - label)), default=None)
    if quiet is not None and index.unit(quiet.id - 1).gap_after >= OPENING_PAUSE_SECONDS:
        return quiet.id
    return uid


def _self_contained_opening(index: Any, first: int, last_label: float, label: float) -> int:
    """Skip a bare agreement, then take in the line an opening leans on."""
    language = _language(index)
    units = index.units
    # "Yeah. Exactly. So the thing is..." opens on the thing.
    skipped = first
    while (
        skipped < len(units)
        and is_agreement(index.unit(skipped).text, language)
        and index.unit(skipped).end - index.unit(skipped).start <= SKIP_MAX_SECONDS
        and index.unit(skipped + 1).start < last_label - 5.0
    ):
        skipped += 1
    if skipped != first and _opens_sentence(index, skipped):
        first = skipped
    # "Un melón." answers a question: the question comes with it (and the one it rephrases).
    for _ in range(CONTEXT_MAX_STEPS):
        unit = index.unit(first)
        if first <= 1 or len(_opening_tokens(unit.text)) > REPLY_MAX_WORDS:
            break
        asked = first - 1
        while asked > 1 and not _opens_sentence(index, asked):
            asked -= 1
        question = index.unit(first - 1)
        if (
            not question.ends_question
            or question.gap_after > REPLY_MAX_GAP_SECONDS
            or not _opens_sentence(index, asked)
            or unit.start - index.unit(asked).start > CONTEXT_MAX_SECONDS
            or label - index.unit(asked).start > START_REWIND_LIMIT_SECONDS
        ):
            break
        first = asked
    opening = first
    for _ in range(CONTEXT_MAX_STEPS):
        unit = index.unit(first)
        if not leans_back(unit.text, language) or first <= 1:
            break
        # The whole previous sentence, which may be several units long.
        previous = first - 1
        while previous > 1 and not _opens_sentence(index, previous):
            previous -= 1
        before = index.unit(previous)
        closest = index.unit(first - 1)
        if (
            not _opens_sentence(index, previous)
            or unit.start - closest.end > CONTEXT_MAX_GAP_SECONDS
            or unit.start - before.start > CONTEXT_MAX_SECONDS
            or label - before.start > START_REWIND_LIMIT_SECONDS
            or (unit.speaker is not None and closest.speaker is not None and unit.speaker != closest.speaker
                and not closest.ends_question)
            or is_agreement(before.text, language)
        ):
            break
        first = previous
    if first != opening and not _stands_alone(index.unit(first).text, language):
        # A run of "And... So... Because...", or a line that closes the thought before ("That's it."): the
        # context found needs context itself. The pick stays as made.
        return opening
    return first


def _closing_unit(index: Any, first: int, label: float, minimum_end: float) -> int:
    """Id of the unit a clip labelled to end at ``label`` closes on (always a unit's last word)."""
    units = index.units
    # The labelled second holds the intended last word.
    limit = label + LABEL_SECONDS
    last = first
    for unit in units[first - 1:]:
        if unit.end <= limit:
            last = unit.id
        else:
            break
    following = index.unit(last + 1) if last < len(units) else None
    if following is not None and following.start < label - 0.3 and following.end - label <= END_EXTEND_SECONDS:
        # The label falls inside the next unit: its sentence is finished rather than cut.
        last = following.id
    if _clean_end(index, last):
        return last
    # A unit that stops at a comma, a breath or a change of voice: finish its sentence when that is close,
    # else go back to the last finished one. A transcript without punctuation has no such ends; there a
    # real pause or a change of voice is the ending.
    for good in (_clean_end, _strong_end):
        if good(index, last):
            return last
        ahead = last
        while ahead < len(units) and not good(index, ahead) and index.unit(ahead).end - label <= END_EXTEND_SECONDS:
            ahead += 1
        if good(index, ahead) and index.unit(ahead).end - label <= END_EXTEND_SECONDS:
            return ahead
        behind = last
        while behind > first and not good(index, behind):
            behind -= 1
        if good(index, behind) and index.unit(behind).end >= minimum_end:
            return behind
    return last


def _finished_thought(index: Any, first: int, last: int, minimum_end: float) -> int:
    """A clip does not close on a question whose short answer follows, nor on a line announcing the next thought."""
    from ..workflows.language import forward_hook

    units = index.units
    unit = index.unit(last)
    if unit.ends_question and last < len(units) and unit.gap_after <= ANSWER_MAX_GAP_SECONDS:
        answer = last + 1
        while answer < len(units) and not _clean_end(index, answer) and not _strong_end(index, answer):
            answer += 1
        reply = index.unit(last + 1)
        other_voice = unit.speaker is not None and reply.speaker is not None and reply.speaker != unit.speaker
        # A reply that is itself a question from the same voice is the speaker carrying on asking, not an answer.
        if index.unit(answer).end - unit.end <= ANSWER_MAX_SECONDS and (
                other_voice or not index.unit(answer).ends_question):
            return answer
    if last > first and forward_hook(unit.text, _language(index)) and not unit.ends_question:
        before = last - 1
        if (_clean_end(index, before) or _strong_end(index, before)) and index.unit(before).end >= minimum_end:
            return before
    return last


def _within_cap(index: Any, first: int, last: int, cap: float, minimum_end: float) -> Optional[int]:
    """The last unit at or before ``last`` whose cut keeps the clip inside ``cap``, on the strongest cut that fits."""
    start = index.cut_in[first]
    fitting = [uid for uid in range(first, last + 1) if index.cut_out[uid] - start <= cap + 0.05]
    if not fitting:
        return None
    for good in (_clean_end, _strong_end):
        ends = [uid for uid in fitting if good(index, uid)]
        late = [uid for uid in ends if index.unit(uid).end >= minimum_end]
        if late or ends:
            return (late or ends)[-1]
    return max(fitting, key=lambda uid: (index.boundary_strength(uid) >= 0.45, uid))


def _said_words(index: Any, uid: int) -> List[str]:
    unit = index.unit(uid)
    words = (_WORD_CHARS.sub("", word.text).lower() for word in index.words[unit.w0:unit.w1 + 1])
    return [word for word in words if word]


def is_false_start(index: Any, uid: int) -> bool:
    """Whether unit ``uid`` is a sentence the speaker drops and starts again: the next one opens with the
    same words and goes on."""
    if uid >= len(index.units):
        return False
    unit, following = index.unit(uid), index.unit(uid + 1)
    if unit.speaker != following.speaker and unit.speaker and following.speaker:
        return False
    said, again = _said_words(index, uid), _said_words(index, uid + 1)
    return RESTART_MIN_WORDS <= len(said) < len(again) and again[:len(said)] == said


def resolve_units(index: Any, start: float, end: float, max_seconds: float) -> Optional[Tuple[int, int]]:
    """First and last unit id of the clip the labels ``start``-``end`` (seconds) stand for, or None."""
    if index.coarse or not len(index) or end <= start:
        return None
    minimum_end = start + MIN_KEPT_SHARE * (end - start)
    labelled = _opening_unit(index, start)
    if index.unit(labelled).start >= end:
        return None
    first = _self_contained_opening(index, labelled, end, start)
    last = _closing_unit(index, first, end, minimum_end)
    last = _finished_thought(index, first, last, minimum_end)
    if last < first:
        return None
    # A false start at either edge is the same words twice: open on the sentence as it is said in full,
    # close before the one that is dropped.
    while first < last and is_false_start(index, first):
        first += 1
    if last > first and is_false_start(index, last):
        kept = index.unit(last - 1).end - index.unit(first).start
        if kept >= MIN_KEPT_SHARE * (index.unit(last).end - index.unit(first).start):
            last -= 1
    if index.cut_out[last] - index.cut_in[first] > max_seconds + 0.05:
        # Context taken in at the opening goes before the payoff does.
        if first != labelled and index.cut_out[last] - index.cut_in[labelled] <= max_seconds + 0.05:
            first = labelled
        else:
            capped = _within_cap(index, first, last, max_seconds, minimum_end)
            if capped is None and first != labelled:
                first = labelled
                capped = _within_cap(index, first, last, max_seconds, minimum_end)
            if capped is None:
                return None
            last = capped
    return first, last


_INDEX_CACHE: Dict[str, Tuple[Tuple[int, int], Any]] = {}
_INDEX_CACHE_SIZE = 8
_INDEX_LOCK = threading.Lock()     # renders run in worker threads


def unit_index_for(video_path: Path) -> Optional[Any]:
    """The sentence units of ``video_path``'s word sidecar (cached per file), or None without word timings."""
    from ..workflows.units import build_units
    from .transcription import load_cached_transcript_data, word_fix_generation

    sidecar = Path(video_path).with_suffix(".transcript_cache.json")
    try:
        stat = os.stat(sidecar)
    except OSError:
        return None
    # Corrections recorded for the transcript (media.edge_listen) make it a different transcript.
    key, stamp = str(sidecar), (stat.st_mtime_ns, stat.st_size, word_fix_generation(video_path))
    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(key)
    if cached and cached[0] == stamp:
        return cached[1]
    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data or not transcript_data.get("words"):
        return None
    try:
        index = build_units(transcript_data, "")
    except Exception as exc:  # noqa: BLE001 - the caller falls back to the word-level snap
        logger.warning("Sentence units unavailable for %s (%s)", video_path, exc)
        return None
    with _INDEX_LOCK:
        if key not in _INDEX_CACHE and len(_INDEX_CACHE) >= _INDEX_CACHE_SIZE:
            _INDEX_CACHE.pop(next(iter(_INDEX_CACHE)))
        _INDEX_CACHE[key] = (stamp, index)
    return index


def units_at(index: Any, first_word_start: float, last_word_end: float) -> Optional[Tuple[int, int]]:
    """The units whose first word starts at ``first_word_start`` and whose last word ends at ``last_word_end``
    (the edges an edge review stored), or None when the transcript no longer has them."""
    first = min(index.units, key=lambda unit: abs(unit.start - first_word_start))
    last = min(index.units, key=lambda unit: abs(unit.end - last_word_end))
    if (abs(first.start - first_word_start) > REVIEWED_EDGE_TOLERANCE
            or abs(last.end - last_word_end) > REVIEWED_EDGE_TOLERANCE or last.id < first.id):
        return None
    return first.id, last.id


def edge_review_request(
    index: Any, first: int, last: int, clip: int, title: Optional[str] = None, about: Optional[str] = None,
) -> Dict[str, Any]:
    """What the edge review (``ai.review_clip_edges``) is shown for one clip: what it is about (its title and
    why it was picked), and the sentences around its opening and around its closing, each a few before and after."""
    def sentence(uid: int) -> Dict[str, Any]:
        unit = index.unit(uid)
        return {"id": uid, "speaker": unit.speaker, "text": unit.text, "in_clip": first <= uid <= last}

    count = len(index)
    opening = range(max(1, first - EDGE_CONTEXT_UNITS), min(count, min(last, first + EDGE_INSIDE_UNITS)) + 1)
    closing = range(max(1, max(first, last - EDGE_INSIDE_UNITS)), min(count, last + EDGE_CONTEXT_UNITS) + 1)
    return {
        "clip": clip, "title": title or "", "about": about or "", "first": first, "last": last,
        "opening": [sentence(uid) for uid in opening], "closing": [sentence(uid) for uid in closing],
    }


def apply_edge_review(
    index: Any, first: int, last: int, opening_id: int, closing_id: int, min_seconds: float, max_seconds: float,
) -> Tuple[int, int]:
    """The clip's units after an edge review asked for ``opening_id``-``closing_id``.

    The model proposes; this decides. An opening must be the start of a sentence and a closing the end of
    one, each within a few sentences and seconds of where it was, and the clip must stay inside its length
    range. A proposal that fails is dropped for that edge only, and the resolved edge stays.
    """
    count = len(index)
    new_first, new_last = first, last
    if 1 <= opening_id <= count and first - EDGE_CONTEXT_UNITS <= opening_id <= first + EDGE_INSIDE_UNITS:
        candidate = opening_id
        while candidate > 1 and not _opens_sentence(index, candidate) and candidate > first - EDGE_CONTEXT_UNITS:
            candidate -= 1
        if (_opens_sentence(index, candidate)
                and abs(index.unit(candidate).start - index.unit(first).start) <= EDGE_MAX_SHIFT_SECONDS):
            new_first = candidate
    if 1 <= closing_id <= count and last - EDGE_INSIDE_UNITS <= closing_id <= last + EDGE_CONTEXT_UNITS:
        candidate = closing_id
        while (candidate < count and candidate < last + EDGE_CONTEXT_UNITS
               and not (_clean_end(index, candidate) or _strong_end(index, candidate))):
            candidate += 1
        if ((_clean_end(index, candidate) or _strong_end(index, candidate))
                and abs(index.unit(candidate).end - index.unit(last).end) <= EDGE_MAX_SHIFT_SECONDS):
            new_last = candidate

    def fits(a: int, b: int) -> bool:
        length = index.cut_out[b] - index.cut_in[a]
        return a <= b and min_seconds - 0.05 <= length <= max_seconds + 0.05

    # Both changes, else the closing alone (the payoff), else the opening alone, else none.
    for a, b in ((new_first, new_last), (first, new_last), (new_first, last)):
        if fits(a, b):
            return a, b
    return first, last


def sentence_bounds(
    video_path: Path, start: float, end: float, max_seconds: float,
    edges: Optional[Tuple[float, float]] = None,
) -> Optional[Tuple[float, float]]:
    """Source seconds a highlight labelled ``start``-``end`` plays: from a sentence's first word to a sentence's last.

    ``edges`` are a reviewed clip's stored edges (first word start, last word end): they are used as they are
    when the transcript still has them, and the labels are resolved otherwise.

    None when the transcript has no word timings (the caller keeps its word-level snap), or when the labels
    do not hold a sentence.
    """
    index = unit_index_for(video_path)
    if index is None:
        return None
    from .transcription import corrected_word_time, word_fix_generation

    def resolve(index: Any) -> Optional[Tuple[int, int]]:
        units = None
        if edges:
            # Reviewed edges name two words by their times in the transcript as it was read. Where those
            # times were corrected since (media.edge_listen), the words are where the corrections put them.
            if word_fix_generation(video_path):
                first_start = corrected_word_time(video_path, float(edges[0]), "start")
                last_end = corrected_word_time(video_path, float(edges[1]), "end")
                units = _units_for_edge_words(index, first_start, last_end)
            else:
                units = units_at(index, float(edges[0]), float(edges[1]))
        if units is not None and index.cut_out[units[1]] - index.cut_in[units[0]] > float(max_seconds) + 0.05:
            capped = _within_cap(index, units[0], units[1], float(max_seconds), index.unit(units[0]).start)
            units = (units[0], capped) if capped is not None else None
        if units is None:
            units = resolve_units(index, float(start), float(end), float(max_seconds))
        return units

    try:
        units = resolve(index)
        if units is not None:
            # Listen to the audio around the two cuts. Where the transcript is corrected, the clip is resolved
            # again on the corrected one, which is what every later call for this clip sees.
            from .edge_listen import retime_around

            if retime_around(video_path, index, units[0], units[1]):
                fresh = unit_index_for(video_path)
                again = resolve(fresh) if fresh is not None else None
                if again is not None:
                    index, units = fresh, again
    except Exception as exc:  # noqa: BLE001 - never lose a clip over its edges
        logger.warning("Sentence bounds failed for %.1f-%.1f (%s); using the word-level snap", start, end, exc)
        return None
    if units is None:
        return None
    first, last = units
    cut_in, cut_out = index.cut_in[first], index.cut_out[last]
    cut_in, cut_out = _settle_on_quiet(video_path, index, first, last, cut_in, cut_out)
    try:
        cut_in, cut_out = _outside_untimed_speech(video_path, index, first, last, cut_in, cut_out)
    except Exception as exc:  # noqa: BLE001 - the cuts stand as they are
        logger.debug("Untimed speech not checked (%s)", exc)
    # A pick may open or close on action instead of speech (gameplay, a reaction, music): where the label
    # sits in silence, with nothing said between it and the first or last sentence, the label is the edge.
    opening, closing = index.unit(first), index.unit(last)
    # (A reviewed clip keeps this: the review moves an edge onto a sentence and the labels with it, so a
    # label that still sits in silence is one the review left alone.)
    if (opening.start - start > LABEL_SECONDS + SILENT_EDGE_SECONDS
            and not _speech_between(index, start, opening.start)):
        cut_in = float(start)
    if end - closing.end > SILENT_EDGE_SECONDS and not _speech_between(index, closing.end, end):
        cut_out = min(float(end), cut_in + float(max_seconds))
    if cut_out - cut_in < 0.5:
        return None
    return cut_in, cut_out


def _unit_holding(index: Any, word: int) -> Optional[int]:
    """Id of the unit that word number ``word`` belongs to."""
    for unit in index.units:
        if unit.w0 <= word <= unit.w1:
            return unit.id
    return None


OPENING_SENTENCE_REACH_SECONDS = 4.0   # a clip may open this much earlier to take in the start of its first sentence
EDGE_WORD_REACH_SECONDS = 0.35    # how far from its recorded time a reviewed edge word may sit


def _units_for_edge_words(index: Any, first_start: float, last_end: float) -> Optional[Tuple[int, int]]:
    """The units of a clip named by its first word's start and its last word's end, when no sentence starts
    or ends exactly there any more: the times were corrected, or a sentence end next to the last word was.

    The clip opens on the sentence its first word is in (on the next one when that sentence began long
    before). It closes on the sentence that ends at its last word; when that word now sits inside a
    sentence, its last words open that sentence, and the clip closes before them.
    """
    words = index.words
    if not len(words):
        return None
    first_word = min(range(len(words)), key=lambda at: abs(words[at].start - first_start))
    last_word = min(range(len(words)), key=lambda at: abs(words[at].end - last_end))
    if (abs(words[first_word].start - first_start) > EDGE_WORD_REACH_SECONDS
            or abs(words[last_word].end - last_end) > EDGE_WORD_REACH_SECONDS or last_word < first_word):
        return None
    first, last = _unit_holding(index, first_word), _unit_holding(index, last_word)
    if first is None or last is None:
        return None
    if (index.unit(first).w0 != first_word and first < last
            and words[first_word].start - index.unit(first).start > OPENING_SENTENCE_REACH_SECONDS):
        first += 1              # the sentence it now sits in began long before: open on the next one
    if index.unit(last).w1 != last_word and last - 1 >= first:
        kept = index.unit(last - 1).end - index.unit(first).start
        if kept >= MIN_KEPT_SHARE * (words[last_word].end - index.unit(first).start):
            last -= 1
    return (first, last) if last >= first else None


def _outside_untimed_speech(
    video_path: Path, index: Any, first: int, last: int, cut_in: float, cut_out: float,
) -> Tuple[float, float]:
    """Keep the cuts clear of speech the transcript has no words for, right before the first word or right
    after the last (heard by ``media.edge_listen``): a line by another voice that runs up to the opening, a
    reply that starts on the closing word's heels."""
    from .edge_listen import speech_limits

    first_word, last_word = index.words[index.unit(first).w0], index.words[index.unit(last).w1]
    before, after = speech_limits(video_path, first_word, last_word)
    if before is not None and cut_in < before <= first_word.start + QUIET_SEARCH_AFTER:
        quiet = None
        if first_word.start - before < TIGHT_GAP_SECONDS:
            quiet = quietest_instant(video_path, before - 0.03, first_word.start + 0.03)
        cut_in = max(cut_in, quiet if quiet is not None else min(before, first_word.start))
    if after is not None and cut_out > after >= last_word.end - QUIET_SEARCH_AFTER:
        quiet = None
        if after - last_word.end < TIGHT_GAP_SECONDS:
            quiet = quietest_instant(video_path, last_word.end - 0.03, after + 0.03)
        cut_out = min(cut_out, quiet if quiet is not None else max(after - 0.02, last_word.end))
    return cut_in, cut_out


def _audio_levels(video_path: Path, start: float, end: float) -> Optional[Any]:
    """RMS level (dB below full scale) of QUIET_WINDOW_SECONDS windows every QUIET_HOP_SECONDS between
    ``start`` and ``end`` (source seconds), or None when the audio cannot be read."""
    import subprocess

    import numpy as np

    from .ffmpeg import select_audio_stream_index

    span = end - start
    if span < QUIET_WINDOW_SECONDS * 1.5 or start < 0 or not Path(video_path).exists():
        return None
    try:
        stream = select_audio_stream_index(video_path)
        if stream is None:
            return None
        result = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{span:.3f}", "-i", str(video_path),
             "-map", f"0:a:{stream}", "-vn", "-ac", "1", "-ar", str(QUIET_SAMPLE_RATE), "-f", "s16le", "-"],
            capture_output=True, timeout=30,
        )
    except Exception:  # noqa: BLE001 - the cut stays where the transcript put it
        return None
    if result.returncode != 0:
        return None
    samples = np.frombuffer(result.stdout, dtype=np.int16).astype(np.float32)
    window = int(QUIET_WINDOW_SECONDS * QUIET_SAMPLE_RATE)
    hop = int(QUIET_HOP_SECONDS * QUIET_SAMPLE_RATE)
    if len(samples) < window + hop:
        return None
    rms = np.array([
        float(np.sqrt(np.mean(samples[i:i + window] ** 2))) for i in range(0, len(samples) - window + 1, hop)
    ])
    return 20.0 * np.log10(rms / 32768.0 + 1e-7)


def _level_time(start: float, position: int) -> float:
    """Source second of the middle of window ``position`` of levels read from ``start``."""
    return start + position * QUIET_HOP_SECONDS + QUIET_WINDOW_SECONDS / 2


def quietest_instant(video_path: Path, start: float, end: float) -> Optional[float]:
    """The moment between ``start`` and ``end`` (source seconds) where the audio is quietest, or None when the
    audio cannot be read. Measured as the RMS of QUIET_WINDOW_SECONDS windows."""
    levels = _audio_levels(video_path, start, end)
    if levels is None:
        return None
    return _level_time(start, int(levels.argmin()))


def sound_onset(levels: Sequence[float], speech_level: float) -> Optional[Tuple[int, int]]:
    """Where a sound starts in what should be silence: (position of the dip before it, position it starts at).

    ``levels`` (dB, one per QUIET_HOP_SECONDS) run away from the clip's edge word: forwards from the last
    word's end, or backwards from the first word's start. A sound counts when it rises UNTIMED_RISE_DB above
    the quietest level so far, to within UNTIMED_NEAR_SPEECH_DB of ``speech_level``, and stays for
    UNTIMED_SUSTAIN_SECONDS: another word, not a click or the edge word's own last consonant.
    """
    need = int(round(UNTIMED_SUSTAIN_SECONDS / QUIET_HOP_SECONDS))
    floor = speech_level - UNTIMED_NEAR_SPEECH_DB
    low, low_at = float("inf"), 0
    for position, level in enumerate(levels):
        if level < low:
            low, low_at = float(level), position
        if level < low + UNTIMED_RISE_DB or level < floor or position + need > len(levels):
            continue
        ahead = levels[position:position + need]
        if sum(1 for value in ahead if value >= floor - 3.0) >= UNTIMED_SUSTAIN_SHARE * need:
            return low_at, position
    return None


def _clear_of_untimed_sound(
    video_path: Path, first_word: Any, last_word: Any, cut_in: float, cut_out: float,
) -> Tuple[float, float]:
    """Pull a cut's padding back to the dip next to its word when the padding holds sound the transcript
    does not time (see UNTIMED_RISE_DB)."""
    import numpy as np

    hop = QUIET_HOP_SECONDS
    if cut_out - last_word.end > 3 * QUIET_WINDOW_SECONDS:
        # One read: the speech before the last word's end (for its level), the padding, and a little more.
        read_from = max(cut_in, last_word.end - UNTIMED_SPEECH_SECONDS)
        levels = _audio_levels(video_path, read_from, cut_out + UNTIMED_LOOK_PAST_SECONDS)
        pad_from = int(round((last_word.end - QUIET_SEARCH_AFTER - read_from) / hop))
        if levels is not None and 0 < pad_from < len(levels):
            found = sound_onset(levels[pad_from:], float(np.percentile(levels[:pad_from], 90)))
            if found is not None and _level_time(read_from, pad_from + found[1]) < cut_out:
                quiet = _level_time(read_from, pad_from + found[0])
                if quiet > cut_in + 0.5:
                    cut_out = min(cut_out, quiet)
    if first_word.start - cut_in > 3 * QUIET_WINDOW_SECONDS:
        read_from = max(0.0, cut_in - UNTIMED_LOOK_PAST_SECONDS)
        read_to = min(cut_out, first_word.start + UNTIMED_SPEECH_SECONDS)
        levels = _audio_levels(video_path, read_from, read_to)
        pad_to = int(round((first_word.start - read_from) / hop)) if levels is not None else 0
        if levels is not None and 0 < pad_to < len(levels):
            # Backwards from the first word: the same test, mirrored.
            found = sound_onset(levels[:pad_to][::-1], float(np.percentile(levels[pad_to:], 90)))
            if found is not None and _level_time(read_from, pad_to - 1 - found[1]) > cut_in:
                quiet = _level_time(read_from, pad_to - 1 - found[0])
                if quiet < cut_out - 0.5:
                    cut_in = max(cut_in, min(quiet, first_word.start))
    return cut_in, cut_out


def _settle_on_quiet(
    video_path: Path, index: Any, first: int, last: int, cut_in: float, cut_out: float
) -> Tuple[float, float]:
    """Move a cut that has no silence to sit in to the quietest instant around its word boundary.

    Between two sentences spoken without a breath the lead-in and the tail padding are both zero, and the
    cut lands exactly on the transcript's word time: a few hundredths early and the previous word's last
    consonant is in the clip, a few hundredths late and this word's first one is gone.
    """
    words = index.words
    opening, closing = index.unit(first), index.unit(last)
    # The search never reaches into the neighbouring word as the transcript times it: a cut inside it would
    # put that word (the last one of the sentence before, the first of the one after) into the clip's text
    # and captions.
    if opening.w0 > 0:
        before = words[opening.w0 - 1]
        if words[opening.w0].start - before.end < TIGHT_GAP_SECONDS:
            quiet = quietest_instant(video_path, before.end, words[opening.w0].start + QUIET_SEARCH_INTO_WORD)
            if quiet is not None:
                cut_in = max(quiet, before.end + 0.0005)
    if closing.w1 + 1 < len(words):
        after = words[closing.w1 + 1]
        if after.start - words[closing.w1].end < TIGHT_GAP_SECONDS:
            quiet = quietest_instant(video_path, words[closing.w1].end - QUIET_SEARCH_AFTER, after.start)
            if quiet is not None and quiet > cut_in + 0.5:
                cut_out = min(quiet, after.start - 0.0005)
    try:
        cut_in, cut_out = _clear_of_untimed_sound(
            video_path, words[opening.w0], words[closing.w1], cut_in, cut_out)
    except Exception as exc:  # noqa: BLE001 - the padding stays as the transcript gives it
        logger.debug("Edge padding not checked for untimed sound (%s)", exc)
    return cut_in, cut_out


def _speech_between(index: Any, start: float, end: float) -> bool:
    """Whether a word is spoken strictly inside ``start``-``end``."""
    return any(word.start < end - 0.05 and word.end > start + 0.05 for word in index.words)

