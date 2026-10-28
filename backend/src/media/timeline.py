"""Timeline helpers for the video pipeline."""

from typing import Any
from ..clip_cleanup import DEFAULT_PAUSE_THRESHOLD_MS, default_filtered_words
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from typing import Tuple
from ..clip_cleanup import clip_cleanup_enabled
from ..clip_source_map import normalize_source_ranges
import re
from .common import (
    CLIP_CAP_SENTENCE_BACKOFF_FRACTION,
    CLIP_END_NEXT_WORD_GUARD_SECONDS,
    CLIP_END_PADDING_SECONDS,
    CLIP_END_SENTENCE_EXTENSION_SECONDS,
    MAX_RENDERED_CLIP_SECONDS,
    CLIP_START_LEAD_IN_SECONDS,
    CLIP_START_SNAP_WINDOW_SECONDS,
    HOOK_TEASER_MAX_SECONDS,
    HOOK_TEASER_MIN_OFFSET_SECONDS,
    HOOK_TEASER_MIN_SECONDS,
    PAUSE_EDGE_PADDING_SECONDS,
    PAUSE_LEAD_IN_SECONDS,
    PAUSE_TAIL_SECONDS,
    SENTENCE_END_RE,
    SENTENCE_PAUSE_BOUNDARY_SECONDS,
    get_config,
    logger,
)
from .ffmpeg import (
    ffprobe_duration,
    output_offsets_for_ranges,
    run_ffmpeg_command,
    select_audio_stream_index,
)
from .gap_activity import active_gap_ranges
from .transcription import (
    _join_transcript_tokens,
    load_cached_transcript_data,
)


def word_ends_sentence(text: str) -> bool:
    return bool(SENTENCE_END_RE.search((text or "").strip()))


def _next_word_start(transcript_data: Optional[Dict], word_start: float) -> Optional[float]:
    """Source time where the first word after the one starting at ``word_start`` begins."""
    word_start_ms = int(round(word_start * 1000))
    starts = [
        int(word["start"])
        for word in (transcript_data or {}).get("words") or []
        if int(word["start"]) > word_start_ms
    ]
    return min(starts) / 1000.0 if starts else None


def _padded_clip_end(
    word_end: float,
    next_word_start: Optional[float],
    padding_seconds: float = CLIP_END_PADDING_SECONDS,
) -> float:
    """End a clip a little after its last word, but never inside the next one.

    Word end timings land before the final consonant decays, so a short tail
    is kept. When the next sentence starts sooner than that ("...won't. All
    right, it's"), the tail stops just before it instead of leaking a fragment.
    """
    end = word_end + max(0.0, padding_seconds)
    if next_word_start is not None:
        end = min(end, max(word_end, next_word_start - CLIP_END_NEXT_WORD_GUARD_SECONDS))
    return end


def extend_keep_ranges_to_sentence_boundary(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    max_extension_seconds: float = CLIP_END_SENTENCE_EXTENSION_SECONDS,
    padding_seconds: float = CLIP_END_PADDING_SECONDS,
) -> List[Tuple[float, float]]:
    """Extend the final source range when a clip end lands mid-sentence."""
    normalized = normalize_source_ranges(keep_ranges)
    if not normalized:
        return []

    last_start, last_end = normalized[-1]
    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data or not transcript_data.get("words"):
        return normalized

    try:
        source_duration = ffprobe_duration(video_path)
    except Exception:
        source_duration = None

    cap_end = last_end + max(0.0, max_extension_seconds)
    if source_duration is not None:
        cap_end = min(cap_end, source_duration)
    if cap_end <= last_end:
        return normalized

    nearby_words = get_absolute_words_in_range(
        transcript_data,
        max(0.0, last_end - 6.0),
        cap_end,
    )
    if not nearby_words:
        return normalized

    boundary_words = [
        word for word in nearby_words if float(word["start"]) <= last_end + 0.05
    ]
    last_boundary_word = boundary_words[-1] if boundary_words else None
    if (
        last_boundary_word
        and float(last_boundary_word["end"]) <= last_end + 0.05
        and word_ends_sentence(str(last_boundary_word.get("text", "")))
    ):
        return normalized

    extended_end = last_end
    completed = False
    for word in nearby_words:
        word_end = float(word["end"])
        if word_end <= last_end + 0.05:
            continue
        extended_end = max(extended_end, word_end)
        if word_ends_sentence(str(word.get("text", ""))):
            extended_end = _padded_clip_end(
                extended_end,
                _next_word_start(transcript_data, float(word["start"])),
                padding_seconds,
            )
            completed = True
            break

    if not completed:
        # The sentence we ran into does not finish inside the window. If it is
        # only a short tail after a sentence that already landed, drop the tail
        # ("...payoff. Before we started recording") instead of keeping a fragment.
        trimmed = _drop_dangling_tail(normalized, transcript_data)
        if trimmed[-1][1] < last_end - 0.05:
            return trimmed

    if extended_end <= last_end:
        return normalized
    if source_duration is not None:
        extended_end = min(extended_end, source_duration)
    extended_end = min(extended_end, cap_end + max(0.0, padding_seconds))

    if extended_end - last_start <= 0.05:
        return normalized

    return [*normalized[:-1], (last_start, extended_end)]


def _drop_dangling_tail(
    keep_ranges: List[Tuple[float, float]],
    transcript_data: Dict,
) -> List[Tuple[float, float]]:
    """Cut a short unfinished sentence that trails a completed one.

    Extending forward cannot always reach the next period. A leftover like
    "Before we started recording" after the payoff is the next thought's
    opening, not part of this clip.
    """
    if not keep_ranges:
        return keep_ranges
    last_start, last_end = keep_ranges[-1]
    words = get_absolute_words_in_range(
        transcript_data, max(0.0, last_start), last_end + 0.05
    )
    if not words:
        return keep_ranges
    last_complete = None
    for word in words:
        if float(word["end"]) > last_end + 0.05:
            break
        if word_ends_sentence(str(word.get("text", ""))):
            last_complete = word
    if last_complete is None:
        return keep_ranges
    # Only words that are actually heard count; even a fraction of the next
    # sentence's first word ("...won't. All right, it's") reads as a fragment.
    dangling = [
        word
        for word in words
        if float(word["start"]) >= float(last_complete["end"]) - 0.02
        and float(word["start"]) < last_end - 0.02
    ]
    if not dangling or len(dangling) > 12:
        return keep_ranges
    if last_end - float(dangling[0]["start"]) > 4.0:
        return keep_ranges
    trimmed_end = min(
        last_end,
        _padded_clip_end(float(last_complete["end"]), float(dangling[0]["start"])),
    )
    # A real clip should stay long enough to stand alone. Ranges that were
    # already short (or a unit-test fixture) can still drop the fragment.
    if last_end - last_start >= 8.0 and trimmed_end - last_start < 8.0:
        return keep_ranges
    return [*keep_ranges[:-1], (last_start, trimmed_end)]


def clamp_keep_ranges_to_max_duration(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    max_seconds: float = MAX_RENDERED_CLIP_SECONDS,
) -> List[Tuple[float, float]]:
    """Shorten the end so a rewound or extended clip still plays within the cap.

    Analysis already checks the selected preset's duration range. Pulling the
    start back to a self-contained sentence or finishing the last sentence
    can push the rendered timeline past its cap. The extra time comes off the end,
    on the last sentence boundary inside the cap when one is close enough
    (``CLIP_CAP_SENTENCE_BACKOFF_FRACTION`` of the cap) to keep a full clip.
    """
    ranges = [(float(start), float(end)) for start, end in keep_ranges if end > start]
    played = sum(end - start for start, end in ranges)
    if not ranges or played <= max_seconds + 0.05:
        return ranges

    budget = max_seconds
    trimmed: List[Tuple[float, float]] = []
    for index, (start, end) in enumerate(ranges):
        length = end - start
        if length <= budget + 0.001:
            trimmed.append((start, end))
            budget -= length
            continue
        cut = start + max(0.0, budget)
        # Whichever range the cap lands in, end on a word, not mid-syllable.
        cut = _end_at_cap(
            video_path,
            start,
            cut,
            max(3.0, max_seconds * CLIP_CAP_SENTENCE_BACKOFF_FRACTION),
        )
        if cut - start >= 0.5:
            trimmed.append((start, cut))
        break
    return trimmed or ranges


def _end_at_cap(
    video_path: Path,
    range_start: float,
    cut: float,
    max_backoff_seconds: float = 3.0,
) -> float:
    """Land the cap on the last sentence end before it, otherwise on the last word.

    A 60s cap that falls in "...when you want to be asleep and when you want"
    backs off to the previous full stop when it is within
    ``max_backoff_seconds``; an unfinished thought is worse than a shorter clip.
    """
    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data:
        return cut
    # Read past the cap so a word it splits keeps its real end and is not
    # mistaken for one that fits.
    words = get_absolute_words_in_range(transcript_data, range_start, cut + 5.0)
    fitting = [word for word in words if float(word["end"]) <= cut + 0.05]
    if not fitting:
        return cut
    chosen = fitting[-1]
    for word in reversed(fitting):
        if cut - float(word["end"]) > max_backoff_seconds:
            break
        if word_ends_sentence(str(word.get("text", ""))):
            chosen = word
            break
    padded = min(
        cut,
        _padded_clip_end(
            float(chosen["end"]),
            _next_word_start(transcript_data, float(chosen["start"])),
        ),
    )
    return max(range_start + 0.5, padded)


def _word_starts_sentence(
    words: List[Dict[str, Any]],
    index: int,
    pause_seconds: float = SENTENCE_PAUSE_BOUNDARY_SECONDS,
) -> bool:
    """Whether ``words[index]`` opens a sentence (punctuation or a clear pause)."""
    if index <= 0:
        return True
    previous = words[index - 1]
    return word_ends_sentence(str(previous.get("text", ""))) or (
        float(words[index]["start"]) - float(previous["end"]) >= pause_seconds
    )


def _sentence_start_word_index(
    words: List[Dict[str, Any]],
    position: float,
    window_seconds: float,
) -> Optional[int]:
    """Index of the word a clip starting near ``position`` should open on.

    Transcript labels are floored to whole seconds, so the intended first word
    starts within one second after ``position``; a sentence opening in that
    second wins (this drops the previous sentence's tail and leading silence).
    Otherwise the start is mid-sentence, so walk back to the sentence's first
    word within ``window_seconds``, falling back to the first word touched.
    """
    for index, word in enumerate(words):
        word_start = float(word["start"])
        if word_start < position - 0.02:
            continue
        if word_start >= position + 1.0:
            break
        if _word_starts_sentence(words, index):
            return index

    first_index = next(
        (
            index
            for index, word in enumerate(words)
            if float(word["end"]) > position + 0.05
        ),
        None,
    )
    if first_index is None or float(words[first_index]["start"]) > position + window_seconds:
        return None

    index = first_index
    while not _word_starts_sentence(words, index):
        if float(words[index - 1]["start"]) < position - window_seconds:
            return first_index
        index -= 1
    return index


# Disfluencies are removed by filler cleanup. They are not a reason to pull
# the previous sentence in; the first real word after them is the opening.
_FILLER_TOKENS = {"um", "uh", "uhh", "uhm", "umm", "hmm"}

# Openings that need the previous sentence before a new viewer can follow.
# "I" / "we" / "so" are left out on purpose: they often start a real claim.
_WEAK_OPENERS = {
    "like",
    "dude",
    "yeah",
    "yes",
    "yep",
    "yup",
    "well",
    "um",
    "uh",
    "uhh",
    "right",
    "okay",
    "ok",
    "exactly",
    "totally",
    "absolutely",
    "hmm",
    "he",
    "she",
    "they",
    "them",
    "him",
    "her",
}
_BACK_REFERENCES = (
    "we mentioned",
    "i mentioned",
    "you mentioned",
    "as i said",
    "like i said",
    "i said",
    "we said",
    "before we started",
    "that point",
    "that sentiment",
    "he made",
    "she made",
    "they made",
)
WEAK_OPENING_REWIND_SECONDS = 8.0


def _normalize_opening_token(text: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", (text or "").lower())


def _opening_phrase(words: List[Dict[str, Any]], index: int, limit: int = 12) -> str:
    tokens: List[str] = []
    cursor = index
    while cursor < len(words) and len(tokens) < limit:
        tokens.append(_normalize_opening_token(str(words[cursor].get("text", ""))))
        if word_ends_sentence(str(words[cursor].get("text", ""))):
            break
        cursor += 1
    return " ".join(token for token in tokens if token)


def _content_word_index(words: List[Dict[str, Any]], index: int) -> int:
    """Skip leading um/uh so the opening is judged on the first real word."""
    cursor = index
    while cursor < len(words):
        token = _normalize_opening_token(str(words[cursor].get("text", "")))
        if token not in _FILLER_TOKENS or word_ends_sentence(str(words[cursor].get("text", ""))):
            return cursor
        cursor += 1
    return index


def _opening_is_weak(words: List[Dict[str, Any]], index: int) -> bool:
    """Whether this sentence starts on an agreement, a pronoun, or a back-reference.

    A leading "um" is ignored: filler cleanup removes it, and the word after
    it is the opening a viewer actually hears.
    """
    if index < 0 or index >= len(words):
        return False
    content = _content_word_index(words, index)
    first = _normalize_opening_token(str(words[content].get("text", "")))
    if first in _WEAK_OPENERS:
        return True
    phrase = _opening_phrase(words, content)
    return any(reference in phrase for reference in _BACK_REFERENCES)


def _previous_sentence_index(words: List[Dict[str, Any]], index: int) -> Optional[int]:
    if index <= 0:
        return None
    cursor = index - 1
    while cursor > 0 and not _word_starts_sentence(words, cursor):
        cursor -= 1
    return cursor


def _prefer_self_contained_start(
    words: List[Dict[str, Any]],
    index: int,
    position: float,
    max_rewind: float = WEAK_OPENING_REWIND_SECONDS,
) -> int:
    """Step back one or two sentences when the opening depends on missing context."""
    for _ in range(2):
        if not _opening_is_weak(words, index):
            break
        previous = _previous_sentence_index(words, index)
        if previous is None:
            break
        if float(words[index]["start"]) - float(words[previous]["start"]) > max_rewind:
            break
        if float(words[previous]["start"]) < position - max_rewind:
            break
        index = previous
    return index


def _start_before_word(
    words: List[Dict[str, Any]], index: int, lead_in_seconds: float
) -> float:
    start = float(words[index]["start"]) - lead_in_seconds
    if index > 0:
        start = max(start, float(words[index - 1]["end"]))
    return max(0.0, start)


def _words_near(
    transcript_data: Dict, start: float, end: float
) -> List[Dict[str, Any]]:
    return sorted(
        get_absolute_words_in_range(transcript_data, max(0.0, start), end),
        key=lambda word: float(word["start"]),
    )


def snap_keep_ranges_to_sentence_start(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    window_seconds: float = CLIP_START_SNAP_WINDOW_SECONDS,
    lead_in_seconds: float = CLIP_START_LEAD_IN_SECONDS,
) -> List[Tuple[float, float]]:
    """Snap the clip's opening range to the first word of its sentence.

    The range with the earliest source start is the clip body's opening (a
    hook teaser may be played before it), so only that range moves.
    """
    normalized = normalize_source_ranges(keep_ranges)
    if not normalized:
        return []

    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data or not transcript_data.get("words"):
        return normalized

    body_index = min(range(len(normalized)), key=lambda i: normalized[i][0])
    range_start, range_end = normalized[body_index]
    # Load extra context so sentence boundaries before the window are visible.
    words = _words_near(
        transcript_data, range_start - window_seconds - 10.0, range_start + window_seconds + 1.0
    )
    if not words:
        return normalized

    index = _sentence_start_word_index(words, range_start, window_seconds)
    if index is None:
        return normalized
    index = _prefer_self_contained_start(words, index, range_start)
    snapped_start = _start_before_word(words, index, lead_in_seconds)
    if abs(snapped_start - range_start) < 0.01 or range_end - snapped_start < 0.5:
        return normalized

    snapped = list(normalized)
    snapped[body_index] = (snapped_start, range_end)
    return snapped


def snap_clip_start_to_sentence(
    video_path: Path, clip_start: float, clip_end: float
) -> float:
    """Sentence-aligned start for raw AI clip bounds (snap before cleanup)."""
    snapped = snap_keep_ranges_to_sentence_start(video_path, [(clip_start, clip_end)])
    return snapped[0][0] if snapped else clip_start


def keep_ranges_transcript_text(
    video_path: Path, keep_ranges: List[Tuple[float, float]]
) -> str:
    """Spoken text of the rendered timeline, in play order."""
    transcript_data = load_cached_transcript_data(video_path)
    words = get_words_for_keep_ranges(transcript_data, keep_ranges)
    return _join_transcript_tokens([str(word["text"]) for word in words])


def build_hook_teaser_range(
    transcript_data: Optional[Dict],
    body_start: float,
    body_end: float,
    hook_start: float,
    hook_end: float,
    min_seconds: float = HOOK_TEASER_MIN_SECONDS,
    max_seconds: float = HOOK_TEASER_MAX_SECONDS,
    min_offset_seconds: float = HOOK_TEASER_MIN_OFFSET_SECONDS,
) -> Optional[Tuple[float, float]]:
    """Word-aligned source range for a cold-open hook teaser, or None.

    ``hook_start``/``hook_end`` are second-floored labels from the analysis.
    The teaser must sit inside the clip body, start at least
    ``min_offset_seconds`` after the body opens (otherwise it is already the
    opening line) and its spoken words must last ``min_seconds``-``max_seconds``.
    """
    if not transcript_data or not transcript_data.get("words"):
        return None
    if hook_end <= hook_start or hook_start < body_start + min_offset_seconds:
        return None
    if hook_end > body_end + 1.0:
        return None

    words = _words_near(transcript_data, body_start, body_end)
    if not words:
        return None

    first = _sentence_start_word_index(words, hook_start, 1.0)
    if first is None:
        return None

    # The labelled end is floored too: the last word ends within a second of it.
    candidates = [
        index
        for index in range(first, len(words))
        if float(words[index]["end"]) <= hook_end + 1.0
    ]
    if not candidates:
        return None

    teaser_start = _start_before_word(words, first, 0.08)
    if teaser_start < body_start + min_offset_seconds:
        return None

    def spoken(index: int) -> float:
        return float(words[index]["end"]) - float(words[first]["start"])

    fitting = [index for index in candidates if spoken(index) <= max_seconds]
    if not fitting:
        return None
    sentence_ends = [
        index
        for index in fitting
        if word_ends_sentence(str(words[index].get("text", "")))
        or (
            index + 1 < len(words)
            and float(words[index + 1]["start"]) - float(words[index]["end"]) >= 0.4
        )
    ]
    last = (sentence_ends or fitting)[-1]
    if spoken(last) < min_seconds:
        return None

    teaser_end = float(words[last]["end"]) + 0.15
    if last + 1 < len(words):
        teaser_end = min(teaser_end, float(words[last + 1]["start"]))
    teaser_end = min(teaser_end, body_end)
    if teaser_end - teaser_start < min_seconds:
        return None
    return (teaser_start, teaser_end)


def add_hook_teaser_to_keep_ranges(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    hook_start: Optional[float],
    hook_end: Optional[float],
) -> List[Tuple[float, float]]:
    """Prepend a cold-open teaser of the hook line before the clip body.

    The result is intentionally non-monotonic ([teaser, body...]); everything
    downstream (rendering, captions, source maps) walks ranges in list order.
    """
    normalized = normalize_source_ranges(keep_ranges)
    if not normalized or hook_start is None or hook_end is None:
        return normalized
    if any(
        normalized[i][0] > normalized[i + 1][0] for i in range(len(normalized) - 1)
    ):
        return normalized  # already carries a teaser

    teaser = build_hook_teaser_range(
        load_cached_transcript_data(video_path),
        normalized[0][0],
        normalized[-1][1],
        float(hook_start),
        float(hook_end),
    )
    if not teaser:
        return normalized

    # Keep cleanup cuts (pauses, filler words) inside the teaser too.
    teaser_ranges = [
        (max(teaser[0], start), min(teaser[1], end))
        for start, end in normalized
        if min(teaser[1], end) - max(teaser[0], start) >= 0.12
    ]
    teaser_duration = sum(end - start for start, end in teaser_ranges)
    if teaser_duration < HOOK_TEASER_MIN_SECONDS:
        return normalized
    logger.info("Adding %.1fs hook teaser from %.2fs", teaser_duration, teaser[0])
    return [*teaser_ranges, *normalized]


def _parse_hook_label(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        parts = [int(part) for part in str(value).strip().split(":")]
    except ValueError:
        return None
    if len(parts) == 2:
        return float(parts[0] * 60 + parts[1])
    if len(parts) == 3:
        return float(parts[0] * 3600 + parts[1] * 60 + parts[2])
    return None


def apply_segment_hook_teaser(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    segment: Dict[str, Any],
) -> List[Tuple[float, float]]:
    """Add the segment's AI-picked hook teaser when HOOK_FIRST_TEASER is on."""
    if not get_config().hook_first_teaser:
        return normalize_source_ranges(keep_ranges)
    return add_hook_teaser_to_keep_ranges(
        video_path,
        keep_ranges,
        _parse_hook_label(segment.get("hook_start_time")),
        _parse_hook_label(segment.get("hook_end_time")),
    )


def parse_timestamp_to_seconds(timestamp_str: str) -> float:
    """Parse timestamp string to seconds."""
    try:
        timestamp_str = timestamp_str.strip()
        logger.info(f"Parsing timestamp: '{timestamp_str}'")  # Debug logging

        if ":" in timestamp_str:
            parts = timestamp_str.split(":")
            if len(parts) == 2:
                minutes, seconds = map(int, parts)
                result = minutes * 60 + seconds
                logger.info(f"Parsed '{timestamp_str}' -> {result}s")
                return result
            elif len(parts) == 3:  # HH:MM:SS format
                hours, minutes, seconds = map(int, parts)
                result = hours * 3600 + minutes * 60 + seconds
                logger.info(f"Parsed '{timestamp_str}' -> {result}s")
                return result

        # Try parsing as pure seconds
        result = float(timestamp_str)
        logger.info(f"Parsed '{timestamp_str}' as seconds -> {result}s")
        return result

    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse timestamp '{timestamp_str}': {e}")
        return 0.0


def seconds_to_mmss(seconds: float) -> str:
    """Format seconds as MM:SS with integer-second precision."""
    total = max(0, int(round(seconds)))
    minutes = total // 60
    secs = total % 60
    return f"{minutes:02d}:{secs:02d}"


def parse_transcript_lines(transcript: str) -> List[Dict[str, Any]]:
    """Parse formatted transcript lines into timestamped records."""
    lines: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"^\[(?P<start>\d{1,3}:\d{2}(?::\d{2})?)\s*-\s*"
        r"(?P<end>\d{1,3}:\d{2}(?::\d{2})?)\]\s*(?P<text>.*)$"
    )
    for raw_line in transcript.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        text = match.group("text").strip()
        speaker = None
        speaker_match = re.match(r"Speaker\s+([^:]+):\s*(.*)$", text)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            text = speaker_match.group(2).strip()
        lines.append(
            {
                "start": parse_timestamp_to_seconds(match.group("start")),
                "end": parse_timestamp_to_seconds(match.group("end")),
                "start_label": match.group("start"),
                "end_label": match.group("end"),
                "speaker": speaker,
                "text": text,
            }
        )
    return lines


def detect_audio_peak_times(video_path: Path, max_peaks: int = 8) -> List[float]:
    """Find approximate one-second audio energy peaks with ffmpeg astats."""
    audio_stream = select_audio_stream_index(video_path)
    if audio_stream is None:
        return []
    result = run_ffmpeg_command(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-map",
            f"0:a:{audio_stream}",
            "-af",
            "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f",
            "null",
            "-",
        ],
        timeout=600,
    )
    if result.returncode != 0:
        return []

    current_time: Optional[float] = None
    samples: List[Tuple[float, float]] = []
    for line in result.stderr.splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        rms_match = re.search(r"lavfi\.astats\.Overall\.RMS_level=([-0-9.]+)", line)
        if rms_match and current_time is not None:
            try:
                samples.append((current_time, float(rms_match.group(1))))
            except ValueError:
                pass
            current_time = None

    if not samples:
        return []
    samples.sort(key=lambda item: item[1], reverse=True)
    peaks: List[float] = []
    for timestamp, _ in samples:
        if all(abs(timestamp - existing) >= 4.0 for existing in peaks):
            peaks.append(timestamp)
        if len(peaks) >= max_peaks:
            break
    return sorted(peaks)


def build_clip_signal_summary(video_path: Path, transcript: str) -> str:
    """Build deterministic clipping hints for the LLM ranking step."""
    transcript_lines = parse_transcript_lines(transcript)
    if not transcript_lines:
        return ""

    trigger_pattern = re.compile(
        r"\b(wait|what|no way|seriously|actually|but|however|because|mistake|secret|"
        r"wild|crazy|insane|never|always|nobody|everybody|why|how|haha|laugh|lol|damn|"
        r"shit|fuck)\b",
        re.IGNORECASE,
    )
    candidates: List[Tuple[float, Dict[str, Any], str]] = []
    audio_peaks = detect_audio_peak_times(video_path)

    for idx, line in enumerate(transcript_lines):
        text = line["text"]
        score = 0.0
        reasons: List[str] = []
        if trigger_pattern.search(text):
            score += 2.0
            reasons.append("trigger phrase")
        if "?" in text:
            score += 1.5
            reasons.append("question/hook")
        if "!" in text:
            score += 1.0
            reasons.append("emphatic delivery")
        if re.search(r"\b(I|we)\s+(thought|realized|found|learned|made|lost|won)\b", text, re.I):
            score += 1.0
            reasons.append("story turn")
        if len(text.split()) <= 8:
            score += 0.5
            reasons.append("short punchy line")

        previous_line = transcript_lines[idx - 1] if idx > 0 else None
        next_line = transcript_lines[idx + 1] if idx + 1 < len(transcript_lines) else None
        if previous_line and line["start"] - previous_line["end"] >= 1.0:
            score += 1.0
            reasons.append("pause before line")
        if previous_line and previous_line.get("speaker") and line.get("speaker"):
            if previous_line["speaker"] != line["speaker"] and line["end"] - line["start"] <= 6:
                score += 1.25
                reasons.append("rapid speaker turn")
        if next_line and next_line.get("speaker") and line.get("speaker"):
            if next_line["speaker"] != line["speaker"] and next_line["end"] - line["start"] <= 10:
                score += 1.0
                reasons.append("back-and-forth")
        if any(line["start"] <= peak <= line["end"] for peak in audio_peaks):
            score += 1.25
            reasons.append("audio energy peak")

        if score > 0:
            candidates.append((score, line, ", ".join(reasons)))

    candidates.sort(key=lambda item: item[0], reverse=True)
    summary_lines = [
        "Deterministic clip-worthiness signals to consider before ranking:",
    ]
    for score, line, reason in candidates[:12]:
        summary_lines.append(
            f"- [{line['start_label']} - {line['end_label']}] score={score:.1f}: {reason}; {line['text']}"
        )
    return "\n".join(summary_lines)


# AssemblyAI stamps some words it could not align with start == end ("game" at
# 541460-541460, or "And I did zero" all at 581250). They are real, usually
# high-confidence text, so they get a short slot instead of being dropped by
# the overlap checks below.
REPAIRED_WORD_MS = 120
# A neighbour lends at most half its length and normally keeps this much.
MIN_LENDER_WORD_MS = 60


def _is_degenerate_word(word: Dict[str, Any]) -> bool:
    return int(word["end"]) <= int(word["start"])


def repair_word_timings(words: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Give zero-duration ASR words a short slot, keeping every word in order.

    Each run of words stamped at one instant first takes free time from the
    gaps around it, then borrows from the neighbouring words. Returns the input
    list unchanged when no word needs repair.
    """
    if not any(_is_degenerate_word(word) for word in words):
        return words

    repaired = [dict(word) for word in words]
    count = len(repaired)
    index = 0
    while index < count:
        if not _is_degenerate_word(repaired[index]):
            index += 1
            continue
        anchor = int(repaired[index]["start"])
        run_end = index
        while (
            run_end + 1 < count
            and _is_degenerate_word(repaired[run_end + 1])
            and int(repaired[run_end + 1]["start"]) == anchor
        ):
            run_end += 1
        run_length = run_end - index + 1
        wanted = run_length * REPAIRED_WORD_MS
        previous = repaired[index - 1] if index > 0 else None
        following = repaired[run_end + 1] if run_end + 1 < count else None

        # Free time in the gaps on either side.
        right_limit = int(following["start"]) if following else anchor + wanted
        left_limit = int(previous["end"]) if previous else 0
        high = max(anchor, min(right_limit, anchor + wanted))
        low = min(anchor, max(left_limit, anchor - (wanted - (high - anchor))))

        def spare(word: Optional[Dict[str, Any]], edge: int, from_start: bool) -> int:
            if word is None:
                return 0
            start, end = int(word["start"]), int(word["end"])
            remaining = end - edge if from_start else edge - start
            if remaining <= 0:
                return 0
            # Very short neighbours still lend a third so the slot is visible.
            return max(min(remaining - MIN_LENDER_WORD_MS, remaining // 2), remaining // 3)

        # Then borrow from the neighbouring words, split between both sides.
        missing = wanted - (high - low)
        if missing > 0:
            right_spare = spare(following, high, True)
            left_spare = spare(previous, low, False)
            take_right = min(right_spare, missing - min(left_spare, missing // 2))
            take_left = min(left_spare, missing - take_right)
            high += take_right
            low -= take_left
        # No room at all: a millisecond each still keeps the text in range.
        if high - low < run_length:
            high = low + run_length
        if following is not None and int(following["start"]) < high < int(following["end"]):
            following["start"] = high
        if previous is not None and int(previous["start"]) < low < int(previous["end"]):
            previous["end"] = low

        step = (high - low) / run_length
        for offset in range(run_length):
            word = repaired[index + offset]
            word["start"] = low + round(step * offset)
            word["end"] = low + round(step * (offset + 1))
        index = run_end + 1

    return repaired


def get_words_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> List[Dict]:
    """Extract words that fall within a clip timerange."""
    if not transcript_data or not transcript_data.get("words"):
        return []

    clip_start_ms = int(clip_start * 1000)
    clip_end_ms = int(clip_end * 1000)

    relevant_words = []
    for word_data in repair_word_timings(transcript_data["words"]):
        word_start = word_data["start"]
        word_end = word_data["end"]

        if word_start < clip_end_ms and word_end > clip_start_ms:
            relative_start = max(0, (word_start - clip_start_ms) / 1000.0)
            relative_end = min(
                (clip_end_ms - clip_start_ms) / 1000.0,
                (word_end - clip_start_ms) / 1000.0,
            )

            if relative_end > relative_start:
                relevant_words.append(
                    {
                        "text": word_data["text"],
                        "start": relative_start,
                        "end": relative_end,
                        "confidence": word_data.get("confidence", 1.0),
                    }
                )

    return relevant_words


def get_absolute_words_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> List[Dict[str, Any]]:
    """Extract absolute-timing words that overlap a clip timerange."""
    if not transcript_data or not transcript_data.get("words"):
        return []

    clip_start_ms = int(clip_start * 1000)
    clip_end_ms = int(clip_end * 1000)

    relevant_words: List[Dict[str, Any]] = []
    for word_data in repair_word_timings(transcript_data["words"]):
        word_start = int(word_data["start"])
        word_end = int(word_data["end"])
        overlap_start = max(word_start, clip_start_ms)
        overlap_end = min(word_end, clip_end_ms)

        if overlap_end <= overlap_start:
            continue

        relevant_words.append(
            {
                "text": word_data["text"],
                "start": overlap_start / 1000.0,
                "end": overlap_end / 1000.0,
                "confidence": word_data.get("confidence", 1.0),
                "speaker": word_data.get("speaker"),
            }
        )

    return relevant_words


def _normalize_cleanup_token(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower())


def _build_cleanup_phrases(
    remove_filler_words: bool,
    filtered_words: Optional[List[str]],
    language_code: Optional[str] = "en",
) -> List[List[str]]:
    raw_phrases: List[str] = []
    if remove_filler_words:
        raw_phrases.extend(default_filtered_words(language_code))
    raw_phrases.extend(filtered_words or [])

    normalized_phrases: List[List[str]] = []
    seen: set[tuple[str, ...]] = set()
    for phrase in raw_phrases:
        tokens = [
            _normalize_cleanup_token(part)
            for part in phrase.split()
            if _normalize_cleanup_token(part)
        ]
        if not tokens:
            continue
        key = tuple(tokens)
        if key in seen:
            continue
        seen.add(key)
        normalized_phrases.append(tokens)

    normalized_phrases.sort(key=len, reverse=True)
    return normalized_phrases


def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not intervals:
        return []

    merged: List[Tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def get_transcript_text_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> str:
    """Return transcript text reconstructed from exact cached word timings."""
    relevant_words = get_words_in_range(transcript_data, clip_start, clip_end)
    if not relevant_words:
        return ""
    return _join_transcript_tokens([word["text"] for word in relevant_words])


def build_clip_keep_ranges(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    cleanup_settings: Optional[Dict[str, Any]] = None,
) -> List[Tuple[float, float]]:
    """Build source-video keep ranges after removing pauses and filtered words."""
    if clip_end <= clip_start:
        return []

    settings = cleanup_settings or {}
    if not clip_cleanup_enabled(settings):
        return [(clip_start, clip_end)]

    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data or not transcript_data.get("words"):
        return [(clip_start, clip_end)]

    relevant_words = get_absolute_words_in_range(transcript_data, clip_start, clip_end)
    if not relevant_words:
        return [(clip_start, clip_end)]

    removal_intervals: List[Tuple[float, float]] = []
    pause_threshold_seconds = max(
        0.25, float(settings.get("pause_threshold_ms", DEFAULT_PAUSE_THRESHOLD_MS)) / 1000.0
    )
    cut_long_pauses = bool(settings.get("cut_long_pauses"))

    filler_indices: set[int] = set()
    phrase_tokens = _build_cleanup_phrases(
        bool(settings.get("remove_filler_words")),
        settings.get("filtered_words"),
        transcript_data.get("language_code"),
    )
    if phrase_tokens:
        normalized_words = [
            _normalize_cleanup_token(word["text"]) for word in relevant_words
        ]
        idx = 0
        while idx < len(relevant_words):
            matched_length = 0
            for phrase in phrase_tokens:
                end_idx = idx + len(phrase)
                if end_idx > len(normalized_words):
                    continue
                if normalized_words[idx:end_idx] == phrase:
                    matched_length = len(phrase)
                    break

            if matched_length:
                removal_intervals.append(
                    (
                        relevant_words[idx]["start"],
                        relevant_words[idx + matched_length - 1]["end"],
                    )
                )
                filler_indices.update(range(idx, idx + matched_length))
                idx += matched_length
                continue

            idx += 1

    # Removed fillers count as silence, so "point. um... next" compresses as one
    # pause rather than leaving dead air on both sides of the cut filler.
    spoken_words = [
        word for index, word in enumerate(relevant_words) if index not in filler_indices
    ]
    if not spoken_words:
        # Nothing but filtered words: cutting them would leave only dead air.
        return [(clip_start, clip_end)]
    if cut_long_pauses:
        # Compress long pauses instead of cutting them out entirely: keep a
        # short breath of silence on each side so the edit doesn't sound
        # clipped and the junction crossfade only blends silence.
        def compress_pause(
            silence_start: float, silence_end: float, keep_before: float, keep_after: float
        ) -> None:
            removal_start = silence_start + keep_before
            removal_end = silence_end - keep_after
            if removal_end - removal_start >= 0.05:
                removal_intervals.append((removal_start, removal_end))

        leading_gap = spoken_words[0]["start"] - clip_start
        if leading_gap >= pause_threshold_seconds:
            compress_pause(clip_start, spoken_words[0]["start"], 0.0, PAUSE_LEAD_IN_SECONDS)

        internal_gaps = [
            (current["end"], nxt["start"])
            for current, nxt in zip(spoken_words, spoken_words[1:])
            if nxt["start"] - current["end"] >= pause_threshold_seconds
        ]
        protected = active_gap_ranges(video_path, internal_gaps, [(word["start"], word["end"]) for word in spoken_words])
        for (gap_start, gap_end), action in zip(internal_gaps, protected):
            cursor = gap_start
            # Keep detected action, compressing only the quiet windows between it.
            for action_start, action_end in action:
                if action_start - cursor >= pause_threshold_seconds:
                    compress_pause(cursor, action_start, PAUSE_EDGE_PADDING_SECONDS, PAUSE_EDGE_PADDING_SECONDS)
                cursor = max(cursor, action_end)
            if gap_end - cursor >= pause_threshold_seconds:
                compress_pause(cursor, gap_end, PAUSE_EDGE_PADDING_SECONDS, PAUSE_EDGE_PADDING_SECONDS)

        trailing_gap = clip_end - spoken_words[-1]["end"]
        if trailing_gap >= pause_threshold_seconds:
            compress_pause(spoken_words[-1]["end"], clip_end, PAUSE_TAIL_SECONDS, 0.0)

    merged_removals = _merge_intervals(removal_intervals)
    if not merged_removals:
        return [(clip_start, clip_end)]

    keep_ranges: List[Tuple[float, float]] = []
    cursor = clip_start
    for removal_start, removal_end in merged_removals:
        if removal_start - cursor >= 0.12:
            keep_ranges.append((cursor, removal_start))
        cursor = max(cursor, removal_end)

    if clip_end - cursor >= 0.12:
        keep_ranges.append((cursor, clip_end))

    total_kept = sum(max(0.0, end - start) for start, end in keep_ranges)
    if not keep_ranges or total_kept < 0.5:
        return [(clip_start, clip_end)]

    return keep_ranges


def build_keep_ranges_from_source_ranges(
    video_path: Path,
    source_ranges: List[Tuple[float, float]],
    cleanup_settings: Optional[Dict[str, Any]] = None,
) -> List[Tuple[float, float]]:
    """Apply cleanup to a list of source ranges while preserving their ordering."""
    normalized_ranges = normalize_source_ranges(source_ranges)
    if not normalized_ranges:
        return []

    keep_ranges: List[Tuple[float, float]] = []
    for range_start, range_end in normalized_ranges:
        keep_ranges.extend(
            build_clip_keep_ranges(
                video_path,
                range_start,
                range_end,
                cleanup_settings,
            )
        )
    return normalize_source_ranges(keep_ranges)


def get_words_for_keep_ranges(
    transcript_data: Dict, keep_ranges: List[Tuple[float, float]], output_offsets: Optional[List[float]] = None
) -> List[Dict[str, Any]]:
    """Project transcript word timings into the output timeline after cuts.

    When the kept ranges are stitched with crossfades each junction shortens
    the timeline by the fade duration; ``output_offsets_for_ranges`` applies
    the same shift the renderer does, keeping captions locked to the audio.
    """
    if not transcript_data or not transcript_data.get("words") or not keep_ranges:
        return []

    keep_ranges = normalize_source_ranges(keep_ranges)
    relevant_words: List[Dict[str, Any]] = []

    for (keep_start, keep_end), timeline_offset in zip(
        keep_ranges, output_offsets if output_offsets is not None else output_offsets_for_ranges(keep_ranges)
    ):
        range_words = get_absolute_words_in_range(transcript_data, keep_start, keep_end)
        for word in range_words:
            relevant_words.append(
                {
                    "text": word["text"],
                    "start": timeline_offset + (word["start"] - keep_start),
                    "end": timeline_offset + (word["end"] - keep_start),
                    "confidence": word.get("confidence", 1.0),
                    "speaker": word.get("speaker"),
                }
            )

    return relevant_words
