"""Timeline helpers for the video pipeline."""

from typing import Any
from ..clip_cleanup import DEFAULT_FILTERED_WORDS
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from typing import Tuple
from ..clip_cleanup import clip_cleanup_enabled
from ..clip_source_map import normalize_source_ranges
import re
from .common import (
    CLIP_END_PADDING_SECONDS,
    CLIP_END_SENTENCE_EXTENSION_SECONDS,
    SENTENCE_END_RE,
    logger,
)
from .ffmpeg import (
    crossfade_fade_for_ranges,
    ffprobe_duration,
    run_ffmpeg_command,
)
from .transcription import (
    _join_transcript_tokens,
    load_cached_transcript_data,
)


def word_ends_sentence(text: str) -> bool:
    return bool(SENTENCE_END_RE.search((text or "").strip()))


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
    for word in nearby_words:
        word_end = float(word["end"])
        if word_end <= last_end + 0.05:
            continue
        extended_end = max(extended_end, word_end)
        if word_ends_sentence(str(word.get("text", ""))):
            extended_end += max(0.0, padding_seconds)
            break

    if extended_end <= last_end:
        return normalized
    if source_duration is not None:
        extended_end = min(extended_end, source_duration)
    extended_end = min(extended_end, cap_end + max(0.0, padding_seconds))

    if extended_end - last_start <= 0.05:
        return normalized

    return [*normalized[:-1], (last_start, extended_end)]


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
        r"^\[(?P<start>\d{1,3}:\d{2})\s*-\s*(?P<end>\d{1,3}:\d{2})\]\s*(?P<text>.*)$"
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
    result = run_ffmpeg_command(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vn",
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


def get_words_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> List[Dict]:
    """Extract words that fall within a clip timerange."""
    if not transcript_data or not transcript_data.get("words"):
        return []

    clip_start_ms = int(clip_start * 1000)
    clip_end_ms = int(clip_end * 1000)

    relevant_words = []
    for word_data in transcript_data["words"]:
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
    for word_data in transcript_data["words"]:
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
            }
        )

    return relevant_words


def _normalize_cleanup_token(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower())


def _build_cleanup_phrases(
    remove_filler_words: bool, filtered_words: Optional[List[str]]
) -> List[List[str]]:
    raw_phrases: List[str] = []
    if remove_filler_words:
        raw_phrases.extend(DEFAULT_FILTERED_WORDS)
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
        0.25, float(settings.get("pause_threshold_ms", 900)) / 1000.0
    )
    cut_long_pauses = bool(settings.get("cut_long_pauses"))

    if cut_long_pauses:
        leading_gap = relevant_words[0]["start"] - clip_start
        if leading_gap >= pause_threshold_seconds:
            removal_intervals.append((clip_start, relevant_words[0]["start"]))

        for current, nxt in zip(relevant_words, relevant_words[1:]):
            gap = nxt["start"] - current["end"]
            if gap >= pause_threshold_seconds:
                removal_intervals.append((current["end"], nxt["start"]))

        trailing_gap = clip_end - relevant_words[-1]["end"]
        if trailing_gap >= pause_threshold_seconds:
            removal_intervals.append((relevant_words[-1]["end"], clip_end))

    phrase_tokens = _build_cleanup_phrases(
        bool(settings.get("remove_filler_words")),
        settings.get("filtered_words"),
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
                idx += matched_length
                continue

            idx += 1

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
    transcript_data: Dict, keep_ranges: List[Tuple[float, float]]
) -> List[Dict[str, Any]]:
    """Project transcript word timings into the output timeline after cuts.

    When the kept ranges are stitched with crossfades (see
    ``crossfade_fade_for_ranges``) each junction shortens the timeline by the
    fade duration, so word offsets are pulled earlier by the same amount to keep
    captions locked to the spoken audio.
    """
    if not transcript_data or not transcript_data.get("words") or not keep_ranges:
        return []

    fade = crossfade_fade_for_ranges(keep_ranges)
    relevant_words: List[Dict[str, Any]] = []
    timeline_offset = 0.0

    for index, (keep_start, keep_end) in enumerate(keep_ranges):
        if index > 0:
            timeline_offset -= fade  # account for the crossfade overlap
        range_words = get_absolute_words_in_range(transcript_data, keep_start, keep_end)
        for word in range_words:
            relevant_words.append(
                {
                    "text": word["text"],
                    "start": timeline_offset + (word["start"] - keep_start),
                    "end": timeline_offset + (word["end"] - keep_start),
                    "confidence": word.get("confidence", 1.0),
                }
            )
        timeline_offset += keep_end - keep_start

    return relevant_words
