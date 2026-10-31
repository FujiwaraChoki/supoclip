"""The sentence-indexed transcript: numbered units every planner cuts on.

Planners never address the video by timestamp. They pick unit ids from a
listing, and this module turns ids into word-exact cut points in seconds. A
unit is a sentence (or, for trailers, a clause) split from the word sidecar,
so every cut lands between words by construction.

Sidecar words are integer milliseconds. They are repaired with
``timeline.repair_word_timings`` first (which works in ms) and only then
converted to seconds.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Optional, Sequence

from .. import ai
from ..media.common import CLIP_START_LEAD_IN_SECONDS, SENTENCE_PAUSE_BOUNDARY_SECONDS

# Private timeline helpers: planned cuts must start and end exactly where the
# highlights renderer would, so they share its lead-in and end padding.
from ..media.timeline import _padded_clip_end, _start_before_word, repair_word_timings
from ..media.transcription import _join_transcript_tokens
from .boundaries import SpeechClock, boundary_strength, cleanup_key, kept_runs, unit_index_at
from .errors import WorkflowError
from .language import (
    CLAUSE_MARK_RE,
    detect_script_language,
    ends_question,
    ends_sentence,
    is_no_space,
    normalize_language_code,
    starts_lowercase,
)

# Part of the fingerprint: bump it whenever the splitting rules change, since
# cached outlines and plans store unit ids.
UNITS_VERSION = "units-v1"

Granularity = Literal["sentence", "clause"]
EndKind = Literal["sentence", "speaker", "pause", "clause", "forced", "line", "eof"]

# Sentence units longer than this are split (at a breath, a clause mark, or by force).
SENTENCE_MAX_WORDS = 40
SENTENCE_MAX_SECONDS = 20.0
# Clause units (trailers) are short enough to stop at a comma or a breath.
CLAUSE_MAX_WORDS = 14
CLAUSE_MAX_SECONDS = 7.0
CLAUSE_MARK_MIN_GAP = 0.25
CLAUSE_PAUSE_GAP = 0.5
CLAUSE_MIN_WORDS = 3
# A silence this long ends a unit even when the sentence carries on after it.
CONTINUATION_PAUSE_SECONDS = 2.5
# A forced split prefers the widest breath at least this long.
FORCE_SPLIT_MIN_GAP = 0.3
# Fragments ("So the", "And") join the next unit when it follows this closely.
FRAGMENT_MAX_WORDS = 2
FRAGMENT_MIN_CHARS_NO_SPACE = 4
FRAGMENT_MERGE_MAX_GAP = 0.3
# Analysis transcript lines are stamped in whole seconds (MM:SS).
COARSE_LINE_RESOLUTION_SECONDS = 1.0
# The listing marks silences at least this long after a unit.
LISTING_PAUSE_SECONDS = 1.5

_STARTS_SENTENCE_AFTER = {"sentence", "speaker", "eof"}
_SPEAKER_PREFIX_RE = re.compile(r"^Speaker (?P<speaker>[^:]{1,40}):\s*")
_MS_SAFE_DECIMALS = 4


@dataclass(frozen=True, slots=True)
class Word:
    """One sidecar word, in seconds, after repair_word_timings."""

    text: str
    start: float
    end: float
    speaker: Optional[str]


@dataclass(frozen=True, slots=True)
class Unit:
    id: int  # 1-based, stable for a given sidecar and granularity
    w0: int  # inclusive indexes into UnitIndex.words; -1 for coarse units
    w1: int
    start: float  # first word start, last word end (seconds)
    end: float
    speaker: Optional[str]
    text: str
    end_kind: EndKind
    gap_after: float  # silence before the next unit's first word (0 for the last)
    starts_sentence: bool
    ends_question: bool


@dataclass
class UnitIndex:
    units: list[Unit]
    words: list[Word]
    granularity: Granularity
    language: Optional[str]  # ISO 639-1 from the sidecar, else detected from the script, else None
    coarse: bool
    fingerprint: str
    cleanup_language: Optional[str] = None  # the sidecar's raw language_code, as cleanup reads it
    no_space: bool = False  # words are written without spaces (Chinese, Japanese, Thai, ...)
    # 1-based: where unit u's source range starts and ends (index 0 unused).
    cut_in: list[float] = field(default_factory=list, init=False, repr=False)
    cut_out: list[float] = field(default_factory=list, init=False, repr=False)
    _strengths: list[float] = field(default_factory=list, init=False, repr=False)
    _starts: list[float] = field(default_factory=list, init=False, repr=False)
    _word_dicts: list[dict[str, float]] = field(default_factory=list, init=False, repr=False)
    _clocks: dict[Any, SpeechClock] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.units:
            raise ValueError("A unit index needs at least one unit")
        self._starts = [unit.start for unit in self.units]
        self._word_dicts = [{"start": word.start, "end": word.end} for word in self.words]
        self._strengths = [0.0] + [
            boundary_strength(unit, self.units[position + 1] if position + 1 < len(self.units) else None)
            for position, unit in enumerate(self.units)
        ]
        self.cut_in = [0.0] + [self._cut_in(unit) for unit in self.units]
        self.cut_out = [0.0] + [self._cut_out(unit) for unit in self.units]

    # -- ids -------------------------------------------------------------

    def __len__(self) -> int:
        return len(self.units)

    def unit(self, uid: int) -> Unit:
        """The unit with this 1-based id; KeyError when out of range."""
        if not isinstance(uid, int) or isinstance(uid, bool) or not 1 <= uid <= len(self.units):
            raise KeyError(uid)
        return self.units[uid - 1]

    def clamp_id(self, uid: int) -> int:
        return min(max(int(uid), 1), len(self.units))

    def unit_at(self, seconds: float) -> int:
        """Id of the unit playing at ``seconds``, else the next one to start (the last after the end)."""
        position = unit_index_at(self._starts, seconds)
        unit = self.units[position]
        if seconds < unit.start or seconds <= unit.end or position + 1 >= len(self.units):
            return unit.id
        return unit.id + 1

    # -- cut points ------------------------------------------------------

    def _cut_in(self, unit: Unit) -> float:
        if self.coarse:
            return float(unit.start)
        start_ms = round(_start_before_word(self._word_dicts, unit.w0, CLIP_START_LEAD_IN_SECONDS) * 1000)
        if unit.w0 > 0 and start_ms <= round(self.words[unit.w0 - 1].end * 1000):
            # Cut in right where the previous word ends: half a millisecond
            # later, so code that truncates seconds to whole ms
            # (get_absolute_words_in_range) never counts that word again.
            return round((start_ms + 0.5) / 1000, _MS_SAFE_DECIMALS)
        return start_ms / 1000

    def _cut_out(self, unit: Unit) -> float:
        if self.coarse:
            # Line times are whole seconds rounded down, so the last word may
            # run up to a second past ``end``. Keep that second when the next
            # line starts later; contiguous lines share the stamped second.
            following = self.units[unit.id] if unit.id < len(self.units) else None
            if following is not None and following.start > unit.end:
                return float(min(unit.end + COARSE_LINE_RESOLUTION_SECONDS, following.start))
            return float(unit.end)
        last = self.words[unit.w1]
        following = self.words[unit.w1 + 1].start if unit.w1 + 1 < len(self.words) else None
        end = _padded_clip_end(last.end, following)
        if following is not None:
            # ASR words can overlap; the cut never reaches into the next word.
            end = min(end, following)
        # Never before the cut-in (only possible when one word swallows the next).
        end_ms = max(round(end * 1000), round(self.cut_in[unit.id] * 1000))
        return round(end_ms / 1000, _MS_SAFE_DECIMALS)

    def range_seconds(self, first: int, last: int) -> tuple[float, float]:
        """Cut-in of ``first`` and cut-out of ``last``, in seconds."""
        self.unit(first), self.unit(last)
        if last < first:
            raise ValueError(f"Unit range #{first}–#{last} is reversed")
        return self.cut_in[first], self.cut_out[last]

    def ranges_for(
        self, first: int, last: int, skips: Iterable[tuple[int, int]] = ()
    ) -> list[tuple[float, float]]:
        """Source ranges for ``first..last`` without the skipped units, one per kept run."""
        return [
            self.range_seconds(run_first, run_last) for run_first, run_last in kept_runs(first, last, skips)
        ]

    # -- text and length -------------------------------------------------

    def text(self, first: int, last: int, skips: Iterable[tuple[int, int]] = ()) -> str:
        """What is heard in ``first..last`` without the skipped units."""
        joiner = "" if self.no_space else " "
        return joiner.join(
            self.units[uid - 1].text
            for run_first, run_last in kept_runs(first, last, skips)
            for uid in range(run_first, run_last + 1)
        ).strip()

    def clock(self, cleanup: Optional[dict[str, Any]] = None) -> SpeechClock:
        key = cleanup_key(cleanup)
        clock = self._clocks.get(key)
        if clock is None:
            clock = self._clocks[key] = SpeechClock(self, cleanup)
        return clock

    def speech_seconds(
        self,
        first: int,
        last: int,
        skips: Iterable[tuple[int, int]] = (),
        cleanup: Optional[dict[str, Any]] = None,
    ) -> float:
        """Estimated rendered seconds of ``first..last`` without the skips, after cleanup.

        This is the length every workflow budget is measured in.
        """
        clock = self.clock(cleanup)
        return sum(
            clock.run_seconds(run_first, run_last) for run_first, run_last in kept_runs(first, last, skips)
        )

    def boundary_strength(self, uid: int) -> float:
        """Strength of the cut AFTER unit ``uid`` (0..1)."""
        self.unit(uid)
        return self._strengths[uid]

    # -- what the model sees --------------------------------------------

    def listing(self, ids: Optional[Iterable[int]] = None, *, with_durations: bool = True) -> str:
        """Numbered lines for a prompt: ``#412 41:20 +3.2s B: text ⏸1.8s``.

        The speaker label appears when it changes, on the first line and after
        a gap marker. A subset gets ``··· 4:12 not shown ···`` between ids that
        are not adjacent.
        """
        selected = (
            sorted({self.clamp_id(uid) for uid in ids}) if ids is not None else [u.id for u in self.units]
        )
        lines: list[str] = []
        previous: Optional[Unit] = None
        for uid in selected:
            unit = self.units[uid - 1]
            show_speaker = previous is None or unit.speaker != previous.speaker
            if previous is not None and uid != previous.id + 1:
                hidden = max(0.0, unit.start - previous.end)
                lines.append(f"··· {format_clock(hidden)} not shown ···")
                show_speaker = True
            parts = [f"#{uid}", format_clock(unit.start)]
            if with_durations:
                parts.append(f"+{max(0.0, unit.end - unit.start):.1f}s")
            speaker = f"{unit.speaker}: " if show_speaker and unit.speaker else ""
            line = " ".join(parts) + " " + speaker + unit.text
            if unit.gap_after >= LISTING_PAUSE_SECONDS:
                line += f" ⏸{unit.gap_after:.1f}s"
            lines.append(line)
            previous = unit
        return "\n".join(lines)


def format_clock(seconds: float) -> str:
    """``M:SS``, or ``H:MM:SS`` from one hour."""
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


# -- building ---------------------------------------------------------------


def _prepare_words(transcript_data: Optional[dict]) -> list[Word]:
    raw = (transcript_data or {}).get("words") or []
    timed = [
        {**word, "start": int(word["start"]), "end": int(word["end"])}
        for word in raw
        if isinstance(word, dict)
        and isinstance(word.get("start"), (int, float))
        and isinstance(word.get("end"), (int, float))
    ]
    # Repair the same list the caption code repairs (blank words included), so
    # both see identical timings; blank words are dropped afterwards.
    words: list[Word] = []
    for word in repair_word_timings(timed):
        if not str(word.get("text") or "").strip():
            continue
        speaker = word.get("speaker")
        words.append(
            Word(
                text=str(word["text"]).strip(),
                start=int(word["start"]) / 1000,
                end=max(int(word["start"]), int(word["end"])) / 1000,
                speaker=str(speaker) if speaker not in (None, "") else None,
            )
        )
    return words


def _coarse_lines(transcript_text: str) -> list[dict[str, Any]]:
    lines = []
    for span in ai._parse_transcript_spans(transcript_text or ""):
        text = span["text"]
        match = _SPEAKER_PREFIX_RE.match(text)
        speaker = match.group("speaker").strip() if match else None
        text = text[match.end() :].strip() if match else text
        if text:
            lines.append(
                {"start": float(span["start"]), "end": float(span["end"]), "speaker": speaker, "text": text}
            )
    return lines


def units_fingerprint(transcript_data: Optional[dict], transcript_text: str) -> str:
    """Stable id of the unit source (words, else timed lines), the same for both granularities."""
    words = _prepare_words(transcript_data)
    return _fingerprint(words, [] if words else _coarse_lines(transcript_text))


def _fingerprint(words: list[Word], lines: list[dict[str, Any]]) -> str:
    if words:
        body = "|".join(f"{word.text}:{word.start:.2f}:{word.end:.2f}" for word in words)
    else:
        body = "|".join(f"{line['text']}:{line['start']:.2f}:{line['end']:.2f}" for line in lines)
    return hashlib.sha256(f"{UNITS_VERSION}\n{body}".encode("utf-8")).hexdigest()


def _gap(words: Sequence[Word], position: int) -> float:
    """Silence between word ``position`` and the next one (0 at the end or on overlap)."""
    if position + 1 >= len(words):
        return 0.0
    return max(0.0, words[position + 1].start - words[position].end)


Piece = tuple[int, int, str]  # (w0, w1, end_kind)


def _ends_on_pause(words: Sequence[Word], position: int) -> bool:
    """A silence long enough to end a unit.

    A sentence that carries on in lowercase after the silence ("its
    disruption ⏸ of sleep.") stays one unit unless the silence is very long:
    the speaker took a breath, and the scrap after it is no place to cut.
    """
    gap = _gap(words, position)
    if gap < SENTENCE_PAUSE_BOUNDARY_SECONDS:
        return False
    return gap >= CONTINUATION_PAUSE_SECONDS or not starts_lowercase(words[position + 1].text)


def _sentence_pieces(words: Sequence[Word], language: Optional[str]) -> list[Piece]:
    pieces: list[Piece] = []
    start = 0
    for position, word in enumerate(words):
        following = words[position + 1] if position + 1 < len(words) else None
        if following is None:
            kind = "eof"
        elif ends_sentence(word.text, following.text, _gap(words, position), language):
            kind = "sentence"
        elif following.speaker is not None and following.speaker != word.speaker:
            kind = "speaker"
        elif _ends_on_pause(words, position):
            kind = "pause"
        else:
            continue
        pieces.append((start, position, kind))
        start = position + 1
    return pieces


def _too_long(words: Sequence[Word], piece: Piece, max_words: int, max_seconds: float) -> bool:
    w0, w1, _ = piece
    return w1 - w0 + 1 > max_words or words[w1].end - words[w0].start > max_seconds


def _force_split_point(words: Sequence[Word], w0: int, w1: int, max_words: int, max_seconds: float) -> int:
    """Index of the word after which an over-long piece splits."""
    count = w1 - w0 + 1
    edge = CLAUSE_MIN_WORDS if count >= 2 * CLAUSE_MIN_WORDS else 1
    candidates = range(w0 + edge - 1, w1 - edge + 1)
    if not candidates:
        candidates = range(w0, w1)
    widest = max(candidates, key=lambda position: (_gap(words, position), -position))
    if _gap(words, widest) >= FORCE_SPLIT_MIN_GAP:
        return widest
    early = w0 + max(1, (count * 3) // 4) - 1
    clause = [
        position
        for position in candidates
        if position <= early and CLAUSE_MARK_RE.search(words[position].text)
    ]
    if clause:
        return clause[-1]
    # Fill the first piece up to the limits, leaving the rest at least
    # ``edge`` words ("...its disruption" + "of sleep." would strand a scrap).
    position = w0
    while (
        position + 1 <= w1 - edge
        and position + 1 - w0 + 1 <= max_words
        and words[position + 1].end - words[w0].start <= max_seconds
    ):
        position += 1
    return position


def _force_split(words: Sequence[Word], piece: Piece, max_words: int, max_seconds: float) -> list[Piece]:
    pending = [piece]
    done: list[Piece] = []
    while pending:
        current = pending.pop()
        w0, w1, kind = current
        if w0 == w1 or not _too_long(words, current, max_words, max_seconds):
            done.append(current)
            continue
        split = _force_split_point(words, w0, w1, max_words, max_seconds)
        left_kind = "clause" if CLAUSE_MARK_RE.search(words[split].text) else "forced"
        pending.append((split + 1, w1, kind))
        pending.append((w0, split, left_kind))
    return done


def _clause_pieces(words: Sequence[Word], piece: Piece) -> list[Piece]:
    """Split a sentence piece at commas with a breath, or at any clear pause."""
    w0, w1, kind = piece
    pieces: list[Piece] = []
    start = w0
    for position in range(w0, w1):
        if position - start + 1 < CLAUSE_MIN_WORDS or w1 - position < CLAUSE_MIN_WORDS:
            continue
        gap = _gap(words, position)
        if (
            CLAUSE_MARK_RE.search(words[position].text) and gap >= CLAUSE_MARK_MIN_GAP
        ) or gap >= CLAUSE_PAUSE_GAP:
            pieces.append((start, position, "clause"))
            start = position + 1
    pieces.append((start, w1, kind))
    return pieces


def _is_fragment(words: Sequence[Word], piece: Piece, no_space: bool) -> bool:
    w0, w1, _ = piece
    if no_space:
        return sum(len(words[position].text) for position in range(w0, w1 + 1)) < FRAGMENT_MIN_CHARS_NO_SPACE
    return w1 - w0 + 1 <= FRAGMENT_MAX_WORDS


def _merge_fragments(
    words: Sequence[Word], pieces: list[Piece], no_space: bool, max_words: int, max_seconds: float
) -> list[Piece]:
    merged: list[Piece] = []
    position = 0
    while position < len(pieces):
        current = pieces[position]
        while position + 1 < len(pieces):
            w0, w1, kind = current
            following = pieces[position + 1]
            candidate = (w0, following[1], following[2])
            if (
                kind == "sentence"
                or not _is_fragment(words, current, no_space)
                or _speaker(words, current) != _speaker(words, following)
                or _gap(words, w1) >= FRAGMENT_MERGE_MAX_GAP
                or _too_long(words, candidate, max_words, max_seconds)
            ):
                break
            current = candidate
            position += 1
        merged.append(current)
        position += 1
    return merged


def _speaker(words: Sequence[Word], piece: Piece) -> Optional[str]:
    return next(
        (words[position].speaker for position in range(piece[0], piece[1] + 1) if words[position].speaker),
        None,
    )


def _join(texts: list[str], no_space: bool) -> str:
    if no_space:
        return "".join(text.strip() for text in texts).strip()
    return _join_transcript_tokens(texts)


def _starts_sentence(words: Sequence[Word], pieces: list[Piece], position: int) -> bool:
    """Whether piece ``position`` opens a sentence.

    After a pause it does unless it carries on in lowercase ("I was thinking
    ⏸ about it", "Hier ⏸ ist das Erste"): the speaker paused mid-sentence.
    """
    if position == 0:
        return True
    previous_kind = pieces[position - 1][2]
    if previous_kind == "pause":
        return not starts_lowercase(words[pieces[position][0]].text)
    return previous_kind in _STARTS_SENTENCE_AFTER


def _units_from_words(
    words: list[Word], granularity: Granularity, language: Optional[str], no_space: bool
) -> list[Unit]:
    pieces: list[Piece] = []
    for piece in _sentence_pieces(words, language):
        pieces.extend(_force_split(words, piece, SENTENCE_MAX_WORDS, SENTENCE_MAX_SECONDS))
    max_words, max_seconds = SENTENCE_MAX_WORDS, SENTENCE_MAX_SECONDS
    if granularity == "clause":
        max_words, max_seconds = CLAUSE_MAX_WORDS, CLAUSE_MAX_SECONDS
        pieces = [
            split
            for piece in pieces
            for clause in _clause_pieces(words, piece)
            for split in _force_split(words, clause, max_words, max_seconds)
        ]
    pieces = _merge_fragments(words, pieces, no_space, max_words, max_seconds)

    units: list[Unit] = []
    for position, (w0, w1, kind) in enumerate(pieces):
        following = pieces[position + 1] if position + 1 < len(pieces) else None
        text = _join([words[index].text for index in range(w0, w1 + 1)], no_space)
        units.append(
            Unit(
                id=position + 1,
                w0=w0,
                w1=w1,
                start=words[w0].start,
                end=words[w1].end,
                speaker=_speaker(words, (w0, w1, kind)),
                text=text,
                end_kind="eof" if following is None else kind,  # type: ignore[arg-type]
                gap_after=max(0.0, words[following[0]].start - words[w1].end) if following else 0.0,
                starts_sentence=_starts_sentence(words, pieces, position),
                ends_question=ends_question(words[w1].text, language),
            )
        )
    return units


def _units_from_lines(lines: list[dict[str, Any]], language: Optional[str]) -> list[Unit]:
    units: list[Unit] = []
    for position, line in enumerate(lines):
        previous = lines[position - 1] if position else None
        following = lines[position + 1] if position + 1 < len(lines) else None
        starts_sentence = (
            previous is None
            or ends_sentence(previous["text"], language=language)
            or (line["speaker"] is not None and line["speaker"] != previous["speaker"])
            or line["start"] - previous["end"] >= SENTENCE_PAUSE_BOUNDARY_SECONDS
        )
        units.append(
            Unit(
                id=position + 1,
                w0=-1,
                w1=-1,
                start=line["start"],
                end=line["end"],
                speaker=line["speaker"],
                text=line["text"],
                end_kind="line" if following else "eof",
                gap_after=max(0.0, following["start"] - line["end"]) if following else 0.0,
                starts_sentence=starts_sentence,
                ends_question=ends_question(line["text"], language),
            )
        )
    return units


def build_units(
    transcript_data: Optional[dict],
    transcript_text: str,
    *,
    granularity: Granularity = "sentence",
) -> UnitIndex:
    """Number the transcript into sentence (or clause) units.

    Without a word sidecar (for example YouTube captions) every timed line of
    the analysis transcript becomes one unit and the index is ``coarse``:
    cuts land on line bounds and clause granularity is not available. With
    no timed lines either, the transcript can't be cut and this raises
    ``transcript_untimed``.
    """
    if granularity not in ("sentence", "clause"):
        raise ValueError(f"Unknown unit granularity: {granularity}")
    raw_language = (transcript_data or {}).get("language_code")
    cleanup_language = raw_language if isinstance(raw_language, str) else None
    words = _prepare_words(transcript_data)
    if words:
        fingerprint = _fingerprint(words, [])
        sample = " ".join(word.text for word in words[:2000])
        language = normalize_language_code(cleanup_language) or detect_script_language(sample)
        no_space = is_no_space(language, sample)
        units = _units_from_words(words, granularity, language, no_space)
        return UnitIndex(
            units=units,
            words=words,
            granularity=granularity,
            language=language,
            coarse=False,
            fingerprint=fingerprint,
            cleanup_language=cleanup_language,
            no_space=no_space,
        )

    lines = _coarse_lines(transcript_text)
    fingerprint = _fingerprint([], lines)
    if not lines:
        raise WorkflowError(
            "transcript_untimed",
            "This video's transcript has no timings, so it can't be cut precisely. Try uploading the file instead.",
        )
    sample = " ".join(line["text"] for line in lines[:400])
    language = normalize_language_code(cleanup_language) or detect_script_language(sample)
    return UnitIndex(
        units=_units_from_lines(lines, language),
        words=[],
        granularity="sentence",
        language=language,
        coarse=True,
        fingerprint=fingerprint,
        cleanup_language=cleanup_language,
        no_space=is_no_space(language, sample),
    )
