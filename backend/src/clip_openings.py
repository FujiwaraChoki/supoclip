"""Opening review for selected clips.

The first spoken seconds decide whether a viewer stays, independently of the
headline burned over them. A selection can open on lead-in banter ("Where's—
Tyler? No. No? Ah!"), on a lone fragment that answers an unseen question
("Catharsis."), or on a conjunction that points back at missing context ("But
the main reason..."). These helpers are pure so the rules can be tested on
real transcript fragments: they move the start to the first self-contained
sentence when that stays within limits, and otherwise ask for a ranking
penalty.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .media.common import CLIP_START_SNAP_WINDOW_SECONDS, SENTENCE_END_RE
from .media.timeline import _FILLER_TOKENS, _opening_is_weak, _sentence_start_word_index

# A sentence this short is a fragment when it stands alone at the opening
# ("Catharsis.") or sits in a run of short lines ("No. No? Ah!"). One short
# sentence followed by a full one ("Here's why. ...") is kept.
OPENING_FRAGMENT_MAX_WORDS = 2
# A dangling conjunction is only trimmed away with its whole sentence when that
# sentence is short; a longer one carries the clip's point, so it is penalised.
OPENING_DANGLING_TRIM_MAX_WORDS = 6
# Source seconds the start may move. Banter often contains long pauses that
# pause cleanup later compresses, so this is wider than the audible lead-in.
OPENING_MAX_TRIM_SECONDS = 15.0
OPENING_MAX_TRIM_WORDS = 12
OPENING_MAX_TRIM_SENTENCES = 5
# Hook points (of 25) removed from a clip whose opening still depends on
# missing context: a "grabs attention" hook drops to "decent but weak".
OPENING_HOOK_PENALTY = 10

_DANGLING_CONJUNCTIONS = {
    "but",
    "and",
    "or",
    "nor",
    "because",
    "cause",
    "plus",
    "also",
    "aber",
    "und",
    "oder",
    "denn",
}
# "So" usually opens a real claim ("So never season eggs..."); only these
# continuations of an earlier argument count as dangling.
_DANGLING_PHRASES = (
    "so the main reason",
    "so the reason",
    "so that's why",
    "so that is why",
    "that's why",
    "which is why",
    "which means",
)


@dataclass(frozen=True)
class OpeningReview:
    """What to do about a clip's opening.

    ``start`` is the whole-second label to store as the new start when the
    opening is trimmed, ``first_word_start`` is where the kept speech begins,
    ``penalise`` asks for a lower hook score, and ``issue`` names the problem
    for logs ("fragment" or "dangling").
    """

    issue: Optional[str] = None
    start: Optional[int] = None
    first_word_start: Optional[float] = None
    skipped_text: str = ""
    penalise: bool = False


def normalize_token(text: str) -> str:
    return re.sub(r"[^\w']+", "", (text or "").lower())


def _content_tokens(words: Sequence[str]) -> List[str]:
    tokens = [normalize_token(word) for word in words]
    return [token for token in tokens if token and token not in _FILLER_TOKENS]


def ends_sentence(text: str) -> bool:
    return bool(SENTENCE_END_RE.search((text or "").strip()))


def split_sentences(texts: Sequence[str]) -> List[List[int]]:
    """Group word indices into sentences on terminal punctuation."""
    sentences: List[List[int]] = []
    current: List[int] = []
    for index, text in enumerate(texts):
        current.append(index)
        if ends_sentence(text):
            sentences.append(current)
            current = []
    if current:
        sentences.append(current)
    return sentences


def is_dangling(tokens: Sequence[str]) -> bool:
    """Whether a sentence opens by continuing something the viewer did not hear."""
    if not tokens:
        return False
    if tokens[0] in _DANGLING_CONJUNCTIONS:
        return True
    phrase = " ".join(tokens[:6])
    return any(phrase.startswith(prefix) for prefix in _DANGLING_PHRASES)


def _leading_fragment_count(sentence_tokens: Sequence[Sequence[str]]) -> int:
    """How many opening sentences are banter or a lone fragment."""
    short = 0
    for tokens in sentence_tokens:
        if len(tokens) > OPENING_FRAGMENT_MAX_WORDS:
            break
        short += 1
    if short == 0 or short == len(sentence_tokens):
        return 0
    if short == 1 and len(sentence_tokens[0]) > 1:
        # "Here's why." before a full sentence is a hook, not banter.
        return 0
    return short


def _classify(sentence_tokens: Sequence[Sequence[str]]) -> tuple[int, Optional[str], bool]:
    """(sentences to skip, issue, whether a long dangling sentence remains)."""
    skip = _leading_fragment_count(sentence_tokens)
    issue = "fragment" if skip else None
    dangling_long = False
    if skip < len(sentence_tokens) and is_dangling(sentence_tokens[skip]):
        issue = issue or "dangling"
        if len(sentence_tokens[skip]) <= OPENING_DANGLING_TRIM_MAX_WORDS:
            skip += 1
        else:
            dangling_long = True
    return skip, issue, dangling_long


def opening_text_issue(text: str) -> Optional[str]:
    """Classify an opening from text alone (no word timings available)."""
    texts = (text or "").split()
    sentences = split_sentences(texts)
    if not sentences:
        return None
    tokens = [_content_tokens([texts[i] for i in sentence]) for sentence in sentences]
    return _classify(tokens)[1]


def opening_label_seconds(
    words: Sequence[Dict[str, Any]], target_index: int
) -> Optional[int]:
    """Whole-second label the renderer snaps onto ``words[target_index]``.

    Segment bounds are stored as MM:SS and the renderer snaps a label to a
    sentence start (``media.timeline._sentence_start_word_index``). Try the
    target's own second, then the next one; None when neither lands on it.
    """
    floor = math.floor(float(words[target_index]["start"]))
    for label in (floor, floor + 1):
        if _sentence_start_word_index(list(words), label, CLIP_START_SNAP_WINDOW_SECONDS) == target_index:
            return label
    return None


def review_opening(
    words: Sequence[Dict[str, Any]],
    clip_start: float,
    clip_end: float,
    min_duration: float,
    max_trim_seconds: float = OPENING_MAX_TRIM_SECONDS,
) -> OpeningReview:
    """Decide how to repair the opening of a clip.

    ``words`` are transcript words in source seconds (``text``/``start``/``end``)
    around the clip, including some before ``clip_start`` so sentence
    boundaries are visible. The opening is the sentence the renderer snaps
    ``clip_start`` to. Lead-in fragments are skipped, along with a short
    dangling sentence after them, when the new opening is one the renderer
    keeps as-is and the clip still meets ``min_duration``. Anything that cannot
    be trimmed within those limits is penalised instead.
    """
    words = list(words)
    opening = _sentence_start_word_index(words, clip_start, CLIP_START_SNAP_WINDOW_SECONDS)
    if opening is None:
        return OpeningReview()
    body = [index for index in range(opening, len(words)) if float(words[index]["start"]) < clip_end]
    if not body:
        return OpeningReview()
    sentences = [
        [body[i] for i in sentence]
        for sentence in split_sentences([str(words[index].get("text", "")) for index in body])
    ]
    tokens = [
        _content_tokens([str(words[index].get("text", "")) for index in sentence])
        for sentence in sentences
    ]

    skip, issue, dangling_long = _classify(tokens)
    if issue is None:
        return OpeningReview()
    if skip == 0:
        return OpeningReview(issue=issue, penalise=True)

    skipped = [index for sentence in sentences[:skip] for index in sentence]
    skipped_text = " ".join(str(words[index].get("text", "")) for index in skipped)
    label = None
    if skip < len(sentences) and skip <= OPENING_MAX_TRIM_SENTENCES:
        target = sentences[skip][0]
        target_start = float(words[target]["start"])
        if (
            len(skipped) <= OPENING_MAX_TRIM_WORDS
            and target_start - float(words[opening]["start"]) <= max_trim_seconds
            and clip_end - target_start >= min_duration
            # The renderer would rewind a pronoun or "yeah" opening straight
            # back into what was skipped.
            and not _opening_is_weak(words, target)
            and (dangling_long or not is_dangling(tokens[skip]))
        ):
            label = opening_label_seconds(words, target)
    if label is None:
        return OpeningReview(issue=issue, skipped_text=skipped_text, penalise=True)
    return OpeningReview(
        issue=issue,
        start=label,
        first_word_start=float(words[sentences[skip][0]]["start"]),
        skipped_text=skipped_text,
        penalise=dangling_long,
    )
