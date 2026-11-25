"""A second opinion on the words around a clip's two cuts, from the local speech recogniser.

A clip is cut on the word times and the punctuation of the transcript. Both are right nearly everywhere and
wrong in a few places per video: a word spoken over another voice is timed a few tenths of a second late, a
repeated or interrupted line is dropped, a full stop lands one word off ("...which does matter if you're reading
this aloud. There is..."). Where that happens at an edge the clip ends on the first word of the next sentence,
loses the start of its first word, or stops in the middle of a sentence.

``retime_around`` listens to a few seconds on either side of each cut (the bundled Whisper, a small model, on
the CPU in a couple of seconds), lines what it hears up with the transcript's words, and records where the two
clearly disagree:

- word times of the words next to the cut (``transcription.register_word_fixes``), so the cut and the captions
  both follow the audio;
- a sentence end the transcript wrote a few words late at the clip's end (the sentence is heard to end
  before the clip's last words and to go on after them);
- speech the transcript has no words for right before the first word or right after the last (``speech_limits``),
  which the cut must not include.

Nothing here decides where the cut goes: ``sentence_bounds`` does, on the corrected words. The transcript on
disk is never changed; the corrections live in this process and are recomputed when another one needs them.
Unavailable (no recogniser, no model file, a failed read) means no corrections.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 4.0           # listened to on either side of an edge word
SAMPLE_RATE = 16000
# The two timings must differ by this much before the heard one is taken: below it they agree to within what
# either is good for. Above the upper bound the match is a different occurrence of the word, not a correction.
RETIME_MIN_SECONDS = 0.08
RETIME_MAX_SECONDS = 1.0
EDGE_REACH_WORDS = 2           # words on either side of the edge word whose times are corrected
PUNCTUATION_REACH_WORDS = 8    # words before a clip's last word in which a misplaced sentence end is looked for
MISPLACED_END_PAUSE_SECONDS = 0.2   # the pause at a sentence end the transcript wrote too late
MAX_WORD_SECONDS = 0.9         # a heard word longer than this is the recogniser stretching over a gap
FUZZY_RATIO = 0.7              # likeness of a heard word to a transcript word in a one-for-one replacement
HEARD_CACHE_SIZE = 64

_SENTENCE_END = re.compile(r"[.!?…。！？]+[\"'”’)\]]*$")
_TRAILING_MARKS = re.compile(r"[.!?…。！？,;:]+(?=[\"'”’)\]]*$)")
_LETTERS = re.compile(r"[^\W_]+", re.UNICODE)

_LOCK = threading.Lock()            # the recogniser runs one window at a time
_STATE_LOCK = threading.Lock()      # renders run in worker threads: the two caches below are shared
SPEECH_LIMITS_SIZE = 512
_HEARD: Dict[Tuple[str, int, Optional[str], str], List["Heard"]] = {}
# (sidecar path, edge word identity) -> seconds the cut must not cross: untimed speech next to that word.
_SPEECH_LIMITS: Dict[Tuple[str, str, str, float], float] = {}


@dataclass(frozen=True)
class Heard:
    """One word the recogniser heard, in source seconds."""

    text: str
    start: float
    end: float


def _setting() -> str:
    from ..config import get_config

    return str(getattr(get_config(), "clip_edge_listen", "auto") or "auto").strip().lower()


def _model_name() -> str:
    from ..config import get_config

    return str(getattr(get_config(), "clip_edge_listen_model", "small") or "small")


def model_file(name: str) -> Path:
    """Where the recogniser keeps the weights of model ``name``."""
    root = os.getenv("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(root) / "whisper" / f"{name}.pt"


def available() -> bool:
    """Whether edges are listened to: off, on, or (the default) on when the model is already on this machine.

    ``auto`` never downloads: a render must not wait on a few hundred megabytes, or hang on a network that
    is not there. The image bakes the model in (see the Dockerfile).
    """
    from .common import _WHISPER_AVAILABLE

    setting = _setting()
    if setting in ("off", "false", "0", "no") or not _WHISPER_AVAILABLE:
        return False
    if setting in ("on", "true", "1", "yes"):
        return True
    return model_file(_model_name()).exists()


def _core(text: str) -> str:
    return "".join(_LETTERS.findall(text or "")).lower()


def hear(video_path: Path, centre: float, language: Optional[str]) -> Optional[List[Heard]]:
    """The words spoken within WINDOW_SECONDS of ``centre`` (source seconds), or None when they cannot be heard."""
    import numpy as np

    model_name = _model_name()
    key = (str(video_path), int(round(centre * 100)), language, model_name)
    with _STATE_LOCK:
        if key in _HEARD:
            return _HEARD[key]
    start = max(0.0, centre - WINDOW_SECONDS)
    heard: Optional[List[Heard]] = None
    try:
        from .ffmpeg import select_audio_stream_index
        from .transcription import _get_whisper_model

        stream = select_audio_stream_index(video_path)
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-nostdin", "-ss", f"{start:.3f}", "-t", f"{centre + WINDOW_SECONDS - start:.3f}",
             "-i", str(video_path), "-map", f"0:a:{stream or 0}", "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE),
             "-f", "s16le", "-"],
            capture_output=True, timeout=60,
        )
        audio = np.frombuffer(raw.stdout, dtype=np.int16).astype(np.float32) / 32768.0
        if raw.returncode == 0 and len(audio) >= SAMPLE_RATE:
            with _LOCK:
                model = _get_whisper_model(model_name)
                result = model.transcribe(
                    audio, language=(language or None), word_timestamps=True, condition_on_previous_text=False,
                    temperature=0.0, fp16=str(getattr(model, "device", "cpu")).startswith("cuda"), verbose=None,
                )
            heard = [
                Heard(str(word["word"]).strip(), start + float(word["start"]), start + float(word["end"]))
                for segment in result.get("segments") or () for word in segment.get("words") or ()
                if str(word.get("word", "")).strip()
            ]
    except Exception as exc:  # noqa: BLE001 - no second opinion; the transcript stands
        logger.debug("Edge audio not heard at %.2fs of %s (%s)", centre, video_path, exc)
        heard = None
    if heard is not None:           # a failed read is tried again next time
        with _STATE_LOCK:
            if key not in _HEARD and len(_HEARD) >= HEARD_CACHE_SIZE:
                _HEARD.pop(next(iter(_HEARD)))
            _HEARD[key] = heard
    return heard


def align(words: Sequence[str], heard: Sequence[str]) -> Dict[int, int]:
    """Which heard word each transcript word is: {position in ``words``: position in ``heard``}.

    Equal runs match; a run replaced by one of the same length matches word for word where the words are
    alike (a different spelling, an ending the recogniser dropped).
    """
    a, b = [_core(word) for word in words], [_core(word) for word in heard]
    pairs: Dict[int, int] = {}
    for tag, a0, a1, b0, b1 in SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if tag == "equal":
            pairs.update({a0 + step: b0 + step for step in range(a1 - a0) if a[a0 + step]})
        elif tag == "replace" and a1 - a0 == b1 - b0:
            for step in range(a1 - a0):
                one, other = a[a0 + step], b[b0 + step]
                if one and other and SequenceMatcher(None, one, other).ratio() >= FUZZY_RATIO:
                    pairs[a0 + step] = b0 + step
    return pairs


def _ends_sentence(text: str) -> bool:
    return bool(_SENTENCE_END.search((text or "").strip()))


def _starts_lower(text: str) -> bool:
    letters = _LETTERS.findall(text or "")
    return bool(letters) and letters[0][0].islower()


def _starts_upper(text: str) -> bool:
    letters = _LETTERS.findall(text or "")
    return bool(letters) and (letters[0][0].isupper() or letters[0][0].isdigit())


def _with_first_letter(text: str, upper: bool) -> str:
    for position, char in enumerate(text):
        if char.isalpha():
            return text[:position] + (char.upper() if upper else char.lower()) + text[position + 1:]
    return text


def corrections(
    words: Sequence[Dict[str, Any]], heard: Sequence[Heard], edge: int, closing: bool = False,
) -> Tuple[Dict[int, Dict[str, Any]], Optional[float], Optional[float]]:
    """What the recogniser corrects around the edge word ``words[edge]`` (``closing``: it is a clip's last word).

    ``words`` are transcript words of the window (text, start, end in seconds). Returns the fixes by position
    ({"start", "end", "text"}, only what changes), the end of untimed speech right before the edge word, and
    the start of untimed speech right after it (None when the neighbouring heard word is the transcript's own).
    """
    pairs = align([str(word["text"]) for word in words], [item.text for item in heard])
    fixes: Dict[int, Dict[str, Any]] = {}

    def fix(position: int) -> Dict[str, Any]:
        return fixes.setdefault(position, {})

    def measured(position: int) -> Optional[Heard]:
        """The heard word for ``words[position]`` when its timing can be trusted: the words on both sides
        are heard next to it too (where the two transcripts differ about a neighbour, the recogniser's
        times drift onto the wrong word), it is not cut by the window, and it is not stretched over a pause."""
        at = pairs.get(position)
        if at is None or at in (0, len(heard) - 1) or position in (0, len(words) - 1):
            return None
        if pairs.get(position - 1) != at - 1 or pairs.get(position + 1) != at + 1:
            return None
        said = heard[at]
        written = float(words[position]["end"]) - float(words[position]["start"])
        if not 0.02 <= said.end - said.start <= min(MAX_WORD_SECONDS, 2.0 * written + 0.2):
            return None
        return said

    # Times: the edge word and its neighbours, where the two clearly disagree.
    for position in range(max(0, edge - EDGE_REACH_WORDS), min(len(words), edge + EDGE_REACH_WORDS + 1)):
        said = measured(position)
        if said is None:
            continue
        start, end = float(words[position]["start"]), float(words[position]["end"])
        if RETIME_MIN_SECONDS <= abs(said.start - start) <= RETIME_MAX_SECONDS:
            fix(position)["start"] = said.start
        if RETIME_MIN_SECONDS <= abs(said.end - end) <= RETIME_MAX_SECONDS:
            fix(position)["end"] = said.end
    if edge not in pairs:
        # The edge word was heard as something else ("buddy." / "pal."). Between the nearest words both
        # transcripts have, each has a run of words: the transcript's run is where the heard one is.
        low = next((at for at in range(edge - 1, max(-1, edge - 4), -1) if at in pairs), None)
        high = next((at for at in range(edge + 1, min(len(words), edge + 4)) if at in pairs), None)
        if low is not None and high is not None and pairs[high] - pairs[low] >= 2:
            run = list(range(low + 1, high))
            span_start, span_end = heard[pairs[low] + 1].start, heard[pairs[high] - 1].end
            sizes = [max(1, len(_core(str(words[at]["text"])))) for at in run]
            if 0.02 * len(run) <= span_end - span_start <= MAX_WORD_SECONDS * len(run):
                moment = span_start
                for at, size in zip(run, sizes):
                    length = (span_end - span_start) * size / sum(sizes)
                    start, end = float(words[at]["start"]), float(words[at]["end"])
                    if (RETIME_MIN_SECONDS <= max(abs(moment - start), abs(moment + length - end))
                            <= RETIME_MAX_SECONDS):
                        fixes[at] = {**fixes.get(at, {}), "start": moment, "end": moment + length}
                    moment += length
                # The words on either side of the run bound it: their near edges are measured too.
                fix(low).setdefault("end", heard[pairs[low]].end)
                fix(high).setdefault("start", heard[pairs[high]].start)
        return {position: change for position, change in fixes.items() if change}, None, None
    # A full stop written a few words late at the end of a clip ("...which does matter if you're reading this
    # aloud. There is so much more...", heard as "...which does matter. If you're reading this aloud, there
    # is..."): the sentence is heard to end before the clip's last words, and to go on after them. Both must
    # be heard. A recogniser that merely runs two sentences together is not evidence against the transcript.
    if closing and edge + 1 < len(words):
        at, following = pairs[edge], pairs.get(edge + 1)
        written = str(words[edge]["text"])
        goes_on = (
            following == at + 1 and _ends_sentence(written) and not _ends_sentence(heard[at].text)
            and _starts_lower(heard[at + 1].text)
        )
        for back in range(1, PUNCTUATION_REACH_WORDS + 1):
            position, here = edge - back, at - back
            if not goes_on or position < 0 or here < 0:
                break
            # The clip's last words, up to the edge word, are heard one for one. The word before them need
            # not be: it is often the one the two transcripts spell differently.
            if any(pairs.get(edge - step) != at - step for step in range(back)):
                break
            before_text = str(words[position]["text"])
            if _ends_sentence(before_text):
                break           # the transcript already ends a sentence here: nothing was misplaced
            if _ends_sentence(heard[here].text) and _starts_upper(heard[here + 1].text):
                # ... and the speaker pauses there, longer than after the clip's last word.
                pause_here = heard[here + 1].start - heard[here].end
                pause_after = heard[at + 1].start - heard[at].end
                if pause_here < MISPLACED_END_PAUSE_SECONDS or pause_here < pause_after:
                    break
                mark = _SENTENCE_END.search(heard[here].text.strip()).group(0)
                fix(position)["text"] = _TRAILING_MARKS.sub("", before_text) + mark
                fix(position + 1)["text"] = _with_first_letter(str(words[position + 1]["text"]), True)
                comma = "," if heard[at].text.strip().endswith(",") else ""
                edge_text = str(fixes.get(edge, {}).get("text", written))
                fix(edge)["text"] = _TRAILING_MARKS.sub("", edge_text) + comma
                following_text = str(words[edge + 1]["text"])
                if _core(following_text) not in ("i", "im", "ill", "ive", "id"):
                    fix(edge + 1)["text"] = _with_first_letter(following_text, False)
                break
    # Speech with no words in the transcript, right next to the edge word.
    at = pairs[edge]
    matched = set(pairs.values())
    before = heard[at - 1].end if at >= 1 and (at - 1) not in matched else None
    after = heard[at + 1].start if at + 1 < len(heard) and (at + 1) not in matched else None
    return {position: change for position, change in fixes.items() if change}, before, after


TRANSITION_BEFORE_SECONDS = 0.1    # a word's real edge is looked for from this long before the heard time ...
TRANSITION_AFTER_SECONDS = 0.3     # ... to this long after it (the recogniser stamps starts and ends early)
# Quiet is well below the loud level around it. A soft first syllable can be that quiet too, which is why a
# found edge must also be supported by what it adds to or removes from the word (``_supported``).
TRANSITION_BELOW_LOUD_DB = 12.0
ONSET_QUIET_FRAMES = 4             # 5 ms steps of quiet before a word starts
OFFSET_QUIET_FRAMES = 12           # ... and after it ends: the gaps inside a word (a stop, a spelled-out "A-B-C") are shorter
TRANSITION_LOUD_SECONDS = 0.15     # the word reaches the loud level within this long of its edge


def sound_edge(levels: Sequence[float], hop: float, near: float, onset: bool) -> Optional[float]:
    """Seconds into ``levels`` (dB, one per ``hop`` seconds) where a word starts (``onset``) or ends, closest
    to ``near`` within TRANSITION_BEFORE/AFTER_SECONDS of it; None when the audio shows no such edge there.

    This is the measurement. The recogniser only says which word is roughly where; a time is corrected only
    when the audio itself shows the word begin after quiet, or end into it.
    """
    if len(levels) < 4 * OFFSET_QUIET_FRAMES:
        return None
    ordered = sorted(levels)
    loud = ordered[int(0.9 * (len(ordered) - 1))]
    quiet_below = loud - TRANSITION_BELOW_LOUD_DB
    quiet = [level <= quiet_below for level in levels]
    reach = max(1, int(round(TRANSITION_LOUD_SECONDS / hop)))
    frames = ONSET_QUIET_FRAMES if onset else OFFSET_QUIET_FRAMES
    low = max(frames, int((near - TRANSITION_BEFORE_SECONDS) / hop))
    high = min(len(levels) - frames, int((near + TRANSITION_AFTER_SECONDS) / hop))
    best: Optional[float] = None
    for at in range(low, high + 1):
        if onset:
            found = (not quiet[at] and all(quiet[at - frames:at])
                     and max(levels[at:at + reach]) >= loud - TRANSITION_BELOW_LOUD_DB)
        else:
            found = (not quiet[at - 1] and all(quiet[at:at + frames])
                     and max(levels[max(0, at - reach):at]) >= loud - TRANSITION_BELOW_LOUD_DB)
        if found and (best is None or abs(at * hop - near) < abs(best - near)):
            best = at * hop
    return best


RUN_ON_GAP_SECONDS = 0.2           # words closer than this run together: there is no silence to cut in
RUN_ON_HEARD_GAP_SECONDS = 0.25    # ... and the recogniser hears them close together too
RUN_ON_MIN_SECONDS = 0.05          # the heard boundary must differ this much from the transcript's to be taken
RUN_ON_MAX_SECONDS = 0.3


def run_on_boundary(
    words: Sequence[Dict[str, Any]], heard: Sequence[Heard], edge: int, closing: bool,
) -> Optional[Tuple[float, float]]:
    """Where the recogniser hears the boundary between the edge word and its neighbour outside the clip, when
    the two run together: (end of the earlier word, start of the later one), or None.

    Between two words spoken without a breath the audio shows no edge to measure, and the quietest instant
    is as often inside a softly spoken word as between the two. The recogniser's boundary is the better
    guide there; the cut is put at the quietest instant inside it.
    """
    earlier, later = (edge, edge + 1) if closing else (edge - 1, edge)
    if earlier < 0 or later >= len(words):
        return None
    if float(words[later]["start"]) - float(words[earlier]["end"]) >= RUN_ON_GAP_SECONDS:
        return None
    pairs = align([str(word["text"]) for word in words], [item.text for item in heard])
    at, following = pairs.get(earlier), pairs.get(later)
    if at is None or following != at + 1 or at == 0 or following == len(heard) - 1:
        return None
    low, high = heard[at].end, heard[following].start
    if not 0.0 <= high - low <= RUN_ON_HEARD_GAP_SECONDS:
        return None
    return low, high


def _levels_around(video_path: Path, start: float, end: float) -> Tuple[Optional[Any], float]:
    from .sentence_bounds import _audio_levels

    read_from = max(0.0, start - 0.6)
    return _audio_levels(video_path, read_from, end + 0.8), read_from


def _measured(video_path: Path, kind: str, heard_time: float) -> Optional[float]:
    """The source second at which sound starts ("start") or stops ("end") next to ``heard_time``."""
    from .sentence_bounds import QUIET_HOP_SECONDS, QUIET_WINDOW_SECONDS

    levels, read_from = _levels_around(video_path, heard_time, heard_time)
    if levels is None:
        return None
    found = sound_edge(list(levels), QUIET_HOP_SECONDS, heard_time - read_from, onset=(kind == "start"))
    return None if found is None else read_from + found + QUIET_WINDOW_SECONDS / 2


def loud_share(levels: Sequence[float], hop: float, start: float, end: float) -> Optional[float]:
    """Share of ``levels`` between ``start`` and ``end`` (seconds into them) that is at the loud level of the
    whole stretch; None for a span too short to tell."""
    low, high = int(round(start / hop)), int(round(end / hop))
    span = list(levels[max(0, low):max(0, high)])
    if len(span) < 4:
        return None
    ordered = sorted(levels)
    loud = ordered[int(0.9 * (len(ordered) - 1))] - TRANSITION_BELOW_LOUD_DB
    return sum(1 for level in span if level >= loud) / len(span)


def _supported(video_path: Path, kind: str, old: float, new: float, limit: Optional[float] = None) -> bool:
    """Whether the audio agrees with moving a word's ``kind`` ("start" or "end") from ``old`` to ``new``.

    A move that takes audio out of the word (a later start, an earlier end) needs that audio to be quiet, or
    to be the next word's (``limit``, where that one was found to start). A move that takes audio in needs
    sound there. Without this a dip inside a long word reads as its start, and a noise as its end.
    """
    from .sentence_bounds import QUIET_HOP_SECONDS

    low, high = min(old, new), max(old, new)
    levels, read_from = _levels_around(video_path, low, high)
    if levels is None:
        return False
    removes = (kind == "start" and new > old) or (kind == "end" and new < old)
    if removes and kind == "end" and limit is not None:
        high = min(high, limit)
    share = loud_share(list(levels), QUIET_HOP_SECONDS, low + 0.02 - read_from, high - 0.02 - read_from)
    if share is None:
        return True
    return share <= 0.1 if removes else share >= 0.5


def measure_fixes(
    video_path: Path, words: Sequence[Dict[str, Any]], fixes: Dict[int, Dict[str, Any]],
) -> Dict[int, Dict[str, Any]]:
    """``fixes`` with every proposed time replaced by where the audio shows the word start or end, and dropped
    where it shows no such thing, contradicts the move, or agrees with the transcript after all. Text fixes
    pass through."""
    kept: Dict[int, Dict[str, Any]] = {
        position: {"text": change["text"]} for position, change in fixes.items() if "text" in change
    }
    # Starts first: where the next word really starts says what an earlier end may leave out.
    for kind in ("start", "end"):
        for position in sorted(fixes):
            if kind not in fixes[position]:
                continue
            old = float(words[position][kind])
            found = _measured(video_path, kind, float(fixes[position][kind]))
            if found is None or not RETIME_MIN_SECONDS <= abs(found - old) <= RETIME_MAX_SECONDS:
                continue
            limit = kept.get(position + 1, {}).get("start") if kind == "end" else None
            if _supported(video_path, kind, old, found, limit):
                kept.setdefault(position, {})[kind] = found
    for position in list(kept):
        result = kept[position]
        start = float(result.get("start", words[position]["start"]))
        end = float(result.get("end", words[position]["end"]))
        if end - start < 0.03:
            # Only one end of the word was found: the word keeps its length next to it.
            length = max(0.05, float(words[position]["end"]) - float(words[position]["start"]))
            if "end" in result and "start" not in result:
                result["start"] = end - length
            elif "start" in result and "end" not in result:
                result["end"] = start + length
            else:
                result.pop("start", None)
                result.pop("end", None)
        if not result:
            kept.pop(position)
    return kept


def _identity(word: Any) -> Tuple[int, int, str]:
    return int(round(float(word.start) * 1000)), int(round(float(word.end) * 1000)), str(word.text)


LIMIT_MATCH_SECONDS = 0.15     # a limit belongs to the edge word whose time is this close to the one it was filed under


def speech_limits(video_path: Path, first_word: Any, last_word: Any) -> Tuple[Optional[float], Optional[float]]:
    """Where untimed speech ends before ``first_word`` and starts after ``last_word`` (None: nothing heard)."""
    sidecar = str(Path(video_path).with_suffix(".transcript_cache.json"))

    with _STATE_LOCK:
        filed_limits = list(_SPEECH_LIMITS.items())

    def find(side: str, word: Any, moment: float) -> Optional[float]:
        for (path, which, text, filed), limit in filed_limits:
            if (path == sidecar and which == side and text == _core(str(word.text))
                    and abs(filed - moment) <= LIMIT_MATCH_SECONDS):
                return limit
        return None

    return find("before", first_word, float(first_word.start)), find("after", last_word, float(last_word.end))


def retime_around(video_path: Path, index: Any, first: int, last: int) -> bool:
    """Listen at both edges of the clip that runs from unit ``first`` to unit ``last`` and record corrections.

    True when the transcript's words changed (the caller rebuilds its index). Never raises.
    """
    if not available():
        return False
    try:
        from .transcription import register_word_fixes

        sidecar = str(Path(video_path).with_suffix(".transcript_cache.json"))
        words = index.words
        changed = False
        for side, edge in (("before", index.unit(first).w0), ("after", index.unit(last).w1)):
            if not 0 <= edge < len(words):
                continue
            centre = words[edge].start if side == "before" else words[edge].end
            heard = hear(Path(video_path), float(centre), index.language)
            if not heard:
                continue
            low = edge
            while low > 0 and words[low - 1].end >= centre - WINDOW_SECONDS:
                low -= 1
            high = edge
            while high + 1 < len(words) and words[high + 1].start <= centre + WINDOW_SECONDS:
                high += 1
            window = [{"text": word.text, "start": word.start, "end": word.end} for word in words[low:high + 1]]
            fixes, before, after = corrections(window, heard, edge - low, closing=(side == "after"))
            fixes = measure_fixes(Path(video_path), window, fixes)
            position = edge - low
            # The word just outside the clip is corrected only towards the clip: a correction away from it
            # gives the cut room it must not have (the start of that word).
            for at in list(fixes):
                outside = at > position if side == "after" else at < position
                if not outside:
                    continue
                keep = {"text": fixes[at]["text"]} if "text" in fixes[at] else {}
                if side == "after" and fixes[at].get("start", float("inf")) < window[at]["start"]:
                    keep["start"] = fixes[at]["start"]
                if side == "before" and fixes[at].get("end", float("-inf")) > window[at]["end"]:
                    keep["end"] = fixes[at]["end"]
                if keep:
                    fixes[at] = keep
                else:
                    fixes.pop(at)
            gap = run_on_boundary(window, heard, position, closing=(side == "after"))
            if gap is not None:
                from .sentence_bounds import quietest_instant

                quiet = quietest_instant(Path(video_path), max(0.0, gap[0] - 0.02), gap[1] + 0.02)
                boundary = quiet if quiet is not None else (gap[0] + gap[1]) / 2
                earlier, later = (position, position + 1) if side == "after" else (position - 1, position)
                written = window[later]["start"] if side == "before" else window[earlier]["end"]
                if (RUN_ON_MIN_SECONDS <= abs(boundary - written) <= RUN_ON_MAX_SECONDS
                        and window[earlier]["start"] + 0.03 < boundary < window[later]["end"] - 0.03):
                    fixes[earlier] = {**{k: v for k, v in fixes.get(earlier, {}).items() if k != "end"}, "end": boundary}
                    fixes[later] = {**{k: v for k, v in fixes.get(later, {}).items() if k != "start"}, "start": boundary}
            limit = before if side == "before" else after
            # A fix moves the edge word; its limit is filed under the times the rebuilt index will show.
            moved = fixes.get(edge - low, {})
            edge_word = Heard(
                str(moved.get("text", words[edge].text)), float(moved.get("start", words[edge].start)),
                float(moved.get("end", words[edge].end)))
            if limit is not None:
                moment = edge_word.start if side == "before" else edge_word.end
                with _STATE_LOCK:
                    if len(_SPEECH_LIMITS) >= SPEECH_LIMITS_SIZE:
                        _SPEECH_LIMITS.pop(next(iter(_SPEECH_LIMITS)))
                    _SPEECH_LIMITS[(sidecar, side, _core(edge_word.text), round(float(moment), 3))] = float(limit)
            if fixes:
                changed = register_word_fixes(Path(video_path), {
                    _identity(words[low + position]): change for position, change in fixes.items()
                }) or changed
        return changed
    except Exception as exc:  # noqa: BLE001 - the transcript stands
        logger.debug("Edge listening failed for %s (%s)", video_path, exc)
        return False
