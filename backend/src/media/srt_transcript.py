"""Turn a user-supplied ``.srt`` file into the transcript cache the pipeline reads.

The pipeline is built around the word-timed transcript sidecar written by the
speech providers (``<video-stem>.transcript_cache.json``). An SRT carries only
cue-level timings, so this module spreads each cue's words across the cue's own
``[start, end]`` window. Every consumer — clip snapping, burned-in captions,
workflows, the editor — then works unchanged, at cue resolution instead of word
resolution.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional
import re
import unicodedata

import srt

from .common import TRANSCRIPT_CACHE_SCHEMA_VERSION, logger

SRT_REFERENCE_PREFIX = "srt://"

_TAG_RE = re.compile(r"<[^>]*>")
_ASS_OVERRIDE_RE = re.compile(r"\{\\[^}]*\}")


class SrtSubtitleError(ValueError):
    """An uploaded subtitle file could not be used."""


def _decode(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise SrtSubtitleError("The subtitle file is not readable text.")


def parse_srt_bytes(raw: bytes) -> List[srt.Subtitle]:
    """Parse uploaded subtitle bytes, rejecting anything with no usable cue."""
    text = _decode(raw)
    try:
        cues = list(srt.parse(text, ignore_errors=True))
    except (srt.SRTParseError, ValueError) as error:
        raise SrtSubtitleError("The subtitle file is not a valid .srt file.") from error
    if not any(_clean_text(cue.content) for cue in cues):
        raise SrtSubtitleError("The subtitle file has no subtitle lines.")
    return cues


def _clean_text(content: str) -> str:
    """Join a cue's lines and strip markup an SRT viewer would not show."""
    lines = []
    for line in (content or "").splitlines():
        line = _ASS_OVERRIDE_RE.sub("", _TAG_RE.sub("", line)).strip()
        if line:
            lines.append(line)
    return " ".join(lines)


def _ms(seconds: float) -> int:
    return max(0, int(round(seconds * 1000)))


def _tokenize(text: str, language_code: Optional[str]) -> List[str]:
    """Split cue text into caption words, one character at a time without spaces."""
    tokens = text.split()
    if not tokens:
        return []
    from ..workflows.language import is_no_space

    if not is_no_space(language_code, "".join(tokens)):
        return tokens
    characters: List[str] = []
    for character in "".join(tokens):
        # Combining marks (Thai vowels and tones) stay on their base character.
        if characters and unicodedata.category(character).startswith("M"):
            characters[-1] += character
        else:
            characters.append(character)
    return characters


def _normalize_cues(
    subtitles: Iterable[srt.Subtitle], duration_ms: Optional[int]
) -> List[List[Any]]:
    cues: List[List[Any]] = []
    for cue in subtitles:
        text = _clean_text(cue.content)
        if not text:
            continue
        start = _ms(cue.start.total_seconds())
        end = max(_ms(cue.end.total_seconds()), start + 1)
        cues.append([start, end, text])
    cues.sort(key=lambda cue: (cue[0], cue[1]))
    if duration_ms is not None and duration_ms > 0:
        clamped: List[List[Any]] = []
        for start, end, text in cues:
            if start >= duration_ms:
                continue
            clamped.append([start, max(min(end, duration_ms), start + 1), text])
        cues = clamped
    return cues


def srt_to_transcript_cache(
    subtitles: Iterable[srt.Subtitle],
    *,
    duration_seconds: Optional[float] = None,
    language_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the word-timed transcript cache from parsed subtitle cues.

    Words inside a cue are distributed proportionally and never overlap, so the
    clip-boundary helpers in ``media.timeline`` see an ordered word stream.
    """
    duration_ms = _ms(duration_seconds) if duration_seconds else None
    cues = _normalize_cues(subtitles, duration_ms)

    words: List[Dict[str, Any]] = []
    utterances: List[Dict[str, Any]] = []
    previous_end = 0
    for index, (start, end, text) in enumerate(cues):
        next_start = cues[index + 1][0] if index + 1 < len(cues) else None
        start = max(start, previous_end)
        if next_start is not None and start >= next_start:
            # A fully swallowed cue still gets a 1 ms sliver before the next one.
            start = max(previous_end, next_start - 1)
            end = max(start + 1, next_start)
        else:
            end = max(end, start + 1)
            if next_start is not None and end > next_start:
                end = next_start
            end = max(end, start + 1)

        tokens = _tokenize(text, language_code)
        if not tokens:
            continue

        count = len(tokens)
        span = end - start
        if span <= count:
            starts = [start + offset for offset in range(count)]
            ends = [start + offset + 1 for offset in range(count)]
        else:
            step = span / count
            starts = [start + int(round(offset * step)) for offset in range(count)]
            ends = starts[1:] + [end]

        cue_words = [
            {
                "text": token,
                "start": starts[offset],
                "end": ends[offset],
                "confidence": 1.0,
                "speaker": None,
            }
            for offset, token in enumerate(tokens)
        ]
        utterances.append(
            {
                "text": text,
                "start": start,
                "end": cue_words[-1]["end"],
                "speaker": None,
                "words": cue_words,
            }
        )
        words.extend(cue_words)
        previous_end = cue_words[-1]["end"]

    return {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words,
        "utterances": utterances,
        "text": "\n".join(utterance["text"] for utterance in utterances),
        "language_code": language_code,
    }


def load_srt_transcript_cache(
    srt_path: Path,
    *,
    duration_seconds: Optional[float] = None,
    language_code: Optional[str] = None,
) -> Dict[str, Any]:
    """Read an ``.srt`` file from disk and convert it to transcript cache data."""
    try:
        raw = Path(srt_path).read_bytes()
    except OSError as error:
        raise SrtSubtitleError(
            "The subtitle file could not be read. Upload it again or remove it."
        ) from error
    cache_data = srt_to_transcript_cache(
        parse_srt_bytes(raw),
        duration_seconds=duration_seconds,
        language_code=language_code,
    )
    if not cache_data["words"]:
        raise SrtSubtitleError("The subtitle file has no subtitle lines.")
    logger.info(
        "Loaded %d subtitle cues (%d words) from %s",
        len(cache_data["utterances"]),
        len(cache_data["words"]),
        srt_path,
    )
    return cache_data


def format_srt_for_analysis(cache_data: Dict[str, Any]) -> List[str]:
    """Render the analysis transcript exactly like a speech-provider transcript.

    ``format_transcript_for_analysis`` reads attribute-style words and
    utterances, so the cache shape is wrapped in lightweight shims rather than
    duplicating its segmenting rules here.
    """
    from .transcription import format_transcript_for_analysis

    utterances = [
        SimpleNamespace(
            text=utterance.get("text", ""),
            start=int(utterance.get("start", 0)),
            end=int(utterance.get("end", 0)),
            speaker=utterance.get("speaker"),
            words=[
                SimpleNamespace(
                    text=word.get("text", ""),
                    start=int(word.get("start", 0)),
                    end=int(word.get("end", 0)),
                )
                for word in utterance.get("words") or []
            ],
        )
        for utterance in cache_data.get("utterances") or []
    ]
    return format_transcript_for_analysis(SimpleNamespace(utterances=utterances, words=[]))