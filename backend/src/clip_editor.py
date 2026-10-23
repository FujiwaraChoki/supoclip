"""
Clip editing helpers for trim/split/merge/caption/export workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, NamedTuple, Optional, Sequence
import logging
import subprocess
import tempfile
import uuid

from .caption_templates import get_template
from .media.ffmpeg import ffmpeg_escape_filter_path
from .video_utils import (
    ass_fonts_dir,
    build_assemblyai_ass_subtitles,
    get_words_for_keep_ranges,
    get_words_in_range,
    load_cached_transcript_data,
)

logger = logging.getLogger(__name__)


@dataclass
class ExportPreset:
    name: str
    width: int
    height: int
    video_bitrate: str
    audio_bitrate: str


EXPORT_PRESETS = {
    "tiktok": ExportPreset("tiktok", 1080, 1920, "10M", "192k"),
    "reels": ExportPreset("reels", 1080, 1920, "12M", "192k"),
    "shorts": ExportPreset("shorts", 1080, 1920, "10M", "192k"),
}


def _safe_name(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}.mp4"


def _run(command: list[str]) -> None:
    subprocess.run(command, check=True, capture_output=True, text=True)


def _ffprobe_duration(path: Path) -> float:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return max(0.0, float(result.stdout.strip()))


def _ffprobe_size(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height",
            "-of",
            "csv=s=x:p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    width, height = result.stdout.strip().split("x", 1)
    return int(width), int(height)


def _double_bitrate(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.endswith("m"):
        return f"{int(float(normalized[:-1]) * 2)}M"
    if normalized.endswith("k"):
        return f"{int(float(normalized[:-1]) * 2)}k"
    return value


def _encode_args(audio_bitrate: str = "256k") -> list[str]:
    return [
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-movflags",
        "+faststart",
    ]


def _ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - (hours * 3600) - (minutes * 60)
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def _ass_color(hex_color: str) -> str:
    value = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(value) != 6:
        value = "FFFFFF"
    red, green, blue = value[0:2], value[2:4], value[4:6]
    return f"&H00{blue}{green}{red}&"


def _escape_ass_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("{", "\\{").replace("}", "\\}")


def _escape_filter_path(path: Path) -> str:
    return ffmpeg_escape_filter_path(path)


def trim_clip_file(
    input_path: Path, output_dir: Path, start_offset: float, end_offset: float
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("trim")
    duration = _ffprobe_duration(input_path)
    start = max(0.0, start_offset)
    end = max(start + 0.1, duration - max(end_offset, 0.0))
    end = min(end, duration)

    _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(input_path),
            "-t",
            f"{end - start:.3f}",
            *_encode_args(),
            str(output_path),
        ]
    )
    return output_path


def split_clip_file(
    input_path: Path, output_dir: Path, split_time: float
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    duration = _ffprobe_duration(input_path)
    split_at = max(0.2, min(split_time, duration - 0.2))
    first_path = output_dir / _safe_name("split_a")
    second_path = output_dir / _safe_name("split_b")

    _run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-t",
            f"{split_at:.3f}",
            *_encode_args(),
            str(first_path),
        ]
    )
    _run(
        [
            "ffmpeg",
            "-y",
            "-ss",
            f"{split_at:.3f}",
            "-i",
            str(input_path),
            "-t",
            f"{duration - split_at:.3f}",
            *_encode_args(),
            str(second_path),
        ]
    )
    return first_path, second_path


def merge_clip_files(paths: Iterable[Path], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("merge")
    input_paths = list(paths)
    if not input_paths:
        raise ValueError("No clips provided for merge")

    with tempfile.NamedTemporaryFile(
        "w", suffix=".txt", prefix="supoclip_concat_", delete=False
    ) as handle:
        list_path = Path(handle.name)
        for path in input_paths:
            escaped = str(path).replace("'", "'\\''")
            handle.write(f"file '{escaped}'\n")

    try:
        _run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "concat",
                "-safe",
                "0",
                "-i",
                str(list_path),
                *_encode_args(),
                str(output_path),
            ]
        )
    finally:
        list_path.unlink(missing_ok=True)

    return output_path


def overlay_custom_captions(
    input_path: Path,
    output_dir: Path,
    caption_text: str,
    position: str,
    highlight_words: List[str],
    *,
    font_family: Optional[str] = None,
    font_size: Optional[int] = None,
    font_color: Optional[str] = None,
    caption_template: str = "default",
    transcript_video_path: Optional[Path] = None,
    source_ranges: Optional[List[tuple[float, float]]] = None,
    position_y: Optional[float] = None,
    original_ranges: Optional[List[tuple[float, float]]] = None,
    source_url: Optional[str] = None,
    source_type: Optional[str] = None,
    cached_video_path: Optional[str] = None,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("caption")
    words = [word for word in caption_text.split() if word.strip()]
    if not words:
        _run(["ffmpeg", "-y", "-i", str(input_path), *_encode_args(), str(output_path)])
        return output_path

    width, height = _ffprobe_size(input_path)
    duration = _ffprobe_duration(input_path)
    position_y = position_y if position_y is not None else {
        "top": 0.18,
        "middle": 0.52,
        "bottom": 0.78,
    }.get(position, 0.78)
    ass_path = output_dir / f"captions_{uuid.uuid4().hex[:12]}.ass"

    transcript_path = transcript_video_path or input_path
    # source_ranges are already on the file's timeline. original_ranges are the
    # clip's timestamps in the full source, used when the only transcript is
    # the full-video cache.
    speech, _missing = resolve_speech_words(
        source=transcript_path,
        original_ranges=original_ranges or source_ranges or [(0.0, duration)],
        local_ranges=source_ranges,
        source_url=source_url,
        source_type=source_type,
        cached_video_path=cached_video_path,
        segment_path=input_path,
        allow_transcribe=True,
        duration=duration,
    )
    caption_words = _caption_words_with_timings(words, speech, duration)
    if caption_words.estimated:
        logger.warning(
            "Caption edit has no speech timings; %d words were spaced evenly",
            len(caption_words.words),
        )
    if not build_assemblyai_ass_subtitles(
        input_path,
        clip_start=0.0,
        clip_end=duration,
        video_width=width,
        video_height=height,
        output_ass_path=ass_path,
        font_family=font_family,
        font_size=font_size,
        font_color=font_color,
        caption_template=caption_template,
        caption_words=caption_words.words,
        position_y_override=position_y,
        highlight_words=highlight_words,
    ):
        raise RuntimeError("Failed to build edited captions")

    try:
        effective_font_family = (
            font_family or get_template(caption_template)["font_family"]
        )
        fonts_dir = ass_fonts_dir(effective_font_family)
        subtitle_filter = f"subtitles=filename={_escape_filter_path(ass_path)}"
        if fonts_dir:
            subtitle_filter += f":fontsdir={_escape_filter_path(fonts_dir)}"
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                subtitle_filter,
                *_encode_args(),
                str(output_path),
            ]
        )
    finally:
        ass_path.unlink(missing_ok=True)

    return output_path


class TimedCaptions(NamedTuple):
    words: List[Dict[str, Any]]
    # True only when no speech transcript was available and words were spaced
    # evenly. Callers must surface this; it is not a successful timing.
    estimated: bool


def _caption_words_with_timings(
    words: List[str],
    timed_words: List[Dict[str, Any]],
    duration: float,
) -> TimedCaptions:
    """Apply edited text to speech timings.

    An even split across the whole clip is a last resort. ``estimated`` is
    True in that case so the editor can warn instead of pretending the words
    follow the audio.
    """
    if not words:
        return TimedCaptions([], False)
    if timed_words:
        source_count = len(timed_words)
        edited_count = len(words)
        if edited_count == source_count:
            return TimedCaptions(
                [
                    {
                        "text": word,
                        "start": float(timed["start"]),
                        "end": float(timed["end"]),
                        "confidence": timed.get("confidence", 1.0),
                    }
                    for word, timed in zip(words, timed_words)
                ],
                False,
            )

        # Word counts differ: spread the edited words proportionally across the
        # spoken span so timings stay strictly increasing (no stacked words).
        span_start = float(timed_words[0]["start"])
        span_end = max(float(timed_words[-1]["end"]), span_start + 0.05 * edited_count)
        step = (span_end - span_start) / edited_count
        return TimedCaptions(
            [
                {
                    "text": word,
                    "start": span_start + index * step,
                    "end": span_start + (index + 1) * step,
                    "confidence": 1.0,
                }
                for index, word in enumerate(words)
            ],
            False,
        )

    word_duration = max(duration / max(len(words), 1), 0.1)
    logger.warning(
        "Spacing %d caption words evenly across %.2fs; no speech transcript",
        len(words),
        duration,
    )
    return TimedCaptions(
        [
            {
                "text": word,
                "start": index * word_duration,
                "end": min(duration, (index + 1) * word_duration),
                "confidence": 1.0,
            }
            for index, word in enumerate(words)
        ],
        True,
    )


_TIMING_EPSILON = 5e-4


def is_evenly_spread_timings(
    words: Sequence[Dict[str, Any]], duration: Optional[float] = None
) -> bool:
    """True when captions were tiled from the start of the clip in equal steps.

    Real speech timings leave gaps or vary in length. A draft whose every word
    has the same duration, abuts the next word, and covers the clip was built
    by the no-transcript fallback.
    """
    if len(words) < 2:
        return False
    ordered = sorted(words, key=lambda word: (float(word["start"]), float(word["end"])))
    if float(ordered[0]["start"]) > 0.02:
        return False
    durations = [float(word["end"]) - float(word["start"]) for word in ordered]
    body = durations
    if len(durations) > 1 and durations[-1] + _TIMING_EPSILON < max(durations[:-1]):
        body = durations[:-1]
    if not body or max(body) - min(body) > _TIMING_EPSILON:
        return False
    for previous, nxt in zip(ordered, ordered[1:]):
        if abs(float(nxt["start"]) - float(previous["end"])) > _TIMING_EPSILON:
            return False
    if duration is not None and abs(float(ordered[-1]["end"]) - float(duration)) > 0.05:
        return False
    return True


def is_synthetic_caption_timing(
    words: Sequence[Dict[str, Any]], duration: Optional[float] = None
) -> bool:
    """Even-spread detection, including drafts built against a slightly different duration."""
    if is_evenly_spread_timings(words, duration):
        return True
    if not words or duration is None:
        return False
    covered = max(float(word["end"]) for word in words)
    if abs(covered - float(duration)) <= 0.05:
        return False
    return is_evenly_spread_timings(words, covered)


def apply_repaired_words(
    document: Dict[str, Any],
    timed_words: Sequence[Dict[str, Any]],
    duration: float,
) -> Optional[Dict[str, Any]]:
    """Retimed copy of a draft, keeping trims, style, and the edited words.

    Returns None when the draft is not an even-spread fallback, or when speech
    timings are still unavailable. Word ids and highlights stay with the same
    edited text.
    """
    from .editor_document import EditDocument

    words = list(document.get("words") or [])
    if not is_synthetic_caption_timing(words, duration) or not timed_words:
        return None
    rebuilt = _caption_words_with_timings(
        [str(word.get("text") or "") for word in words],
        list(timed_words),
        duration,
    )
    if rebuilt.estimated or len(rebuilt.words) != len(words):
        return None
    duration = float(duration)
    aligned: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for index, (previous, new) in enumerate(zip(words, rebuilt.words)):
        start = max(0.0, min(float(new["start"]), max(duration - 0.01, 0.0)))
        end = max(start + 0.01, min(float(new["end"]), duration))
        if start >= duration:
            return None
        word_id = str(previous.get("id") or f"word-{index}")
        if word_id in seen or len(word_id) > 100:
            word_id = f"word-{index}-{uuid.uuid4().hex[:8]}"
        seen.add(word_id)
        aligned.append(
            {
                "id": word_id,
                "start": start,
                "end": end,
                "text": str(new["text"])[:120],
                "highlight": bool(previous.get("highlight", False)),
            }
        )
    if all(
        abs(float(old["start"]) - float(new["start"])) <= _TIMING_EPSILON
        and abs(float(old["end"]) - float(new["end"])) <= _TIMING_EPSILON
        for old, new in zip(words, aligned)
    ):
        return None
    candidate = {**document, "words": aligned}
    try:
        EditDocument.model_validate(candidate).validate_duration(duration)
    except Exception:
        logger.warning("Repaired caption timings did not fit the clip", exc_info=True)
        return None
    return candidate


def _timeline_kind(payload: Dict[str, Any], time_map) -> str:
    """'local' when word times already match a section file, else 'original'."""
    if time_map is None or not getattr(time_map, "spans", None):
        return "original"
    words = [word for word in (payload.get("words") or []) if isinstance(word, dict)][:40]
    if not words:
        return "original"
    local_end = time_map.spans[-1].local_end
    in_local = 0
    in_original = 0
    for word in words:
        start = float(word.get("start") or 0) / 1000.0
        end = float(word.get("end") or start) / 1000.0
        midpoint = (start + end) / 2.0
        if -0.5 <= midpoint <= local_end + 0.5:
            in_local += 1
        if time_map.contains(midpoint):
            in_original += 1
    if in_original > in_local:
        return "original"
    return "local"


def _metadata_media_path(reference: Optional[str]) -> Optional[Path]:
    """Local path for a stored source, downloading transcript sidecars only."""
    if not reference:
        return None
    from .media_storage import MediaStorage, is_s3

    storage = MediaStorage()
    try:
        if reference.startswith("upload://s3/"):
            return storage.local_path(storage.source_reference(reference), metadata_only=True)
        if is_s3(reference):
            return storage.local_path(reference, metadata_only=True)
    except Exception:
        logger.warning("Could not read stored transcript metadata", exc_info=True)
        return None
    if reference.startswith("upload://"):
        from .services.video_service import VideoService

        try:
            return VideoService.resolve_local_video_path(reference)
        except ValueError:
            return None
    return Path(reference)


def _words_on_ranges(payload: Dict[str, Any], ranges: Sequence[tuple[float, float]]) -> List[Dict[str, Any]]:
    if not ranges:
        return []
    return get_words_for_keep_ranges(payload, list(ranges))


def resolve_speech_words(
    *,
    source: Optional[Path],
    original_ranges: Sequence[tuple[float, float]],
    local_ranges: Optional[Sequence[tuple[float, float]]] = None,
    source_url: Optional[str] = None,
    source_type: Optional[str] = None,
    cached_video_path: Optional[str] = None,
    segment_path: Optional[Path] = None,
    allow_transcribe: bool = False,
    duration: float = 0.0,
) -> tuple[List[Dict[str, Any]], bool]:
    """Speech-timed words on the clip timeline, and whether none could be found.

    Section downloads often have no transcript beside the file. The full video's
    transcript cache (and the task's stored sidecar) still uses source
    timestamps, so those words are offset to the clip start. A clip segment is
    transcribed only when every cache misses.
    """
    from .youtube_source import load_cached_youtube_transcript, load_time_map, local_ranges_for
    from .youtube_utils import get_youtube_video_id

    original = [(float(start), float(end)) for start, end in original_ranges]
    if local_ranges is not None:
        local = [(float(start), float(end)) for start, end in local_ranges]
    elif source is not None:
        local = local_ranges_for(source, original)
    else:
        local = list(original)

    candidates: List[tuple[str, Dict[str, Any]]] = []
    seen: set[str] = set()

    def add_media(path: Optional[Path]) -> None:
        if path is None:
            return
        key = str(path)
        if key in seen:
            return
        seen.add(key)
        payload = load_cached_transcript_data(path)
        if not payload or not payload.get("words"):
            return
        candidates.append((_timeline_kind(payload, load_time_map(path)), payload))

    add_media(source)
    add_media(_metadata_media_path(cached_video_path))
    if source_url and source_url.startswith("upload://"):
        add_media(_metadata_media_path(source_url))
    if source_type == "youtube" and source_url:
        video_id = get_youtube_video_id(source_url)
        if video_id:
            cached = load_cached_youtube_transcript(video_id)
            if cached and cached.get("words"):
                candidates.append(("original", cached))

    for kind, payload in candidates:
        primary = local if kind == "local" else original
        secondary = original if kind == "local" else local
        words = _words_on_ranges(payload, primary)
        if not words and secondary != primary:
            words = _words_on_ranges(payload, secondary)
        if words:
            return words, False

    if segment_path is not None:
        segment_payload = load_cached_transcript_data(segment_path)
        span = duration if duration and duration > 0 else 0.0
        if segment_payload and segment_payload.get("words"):
            if span <= 0:
                last = max(float(word.get("end") or 0) for word in segment_payload["words"]) / 1000.0
                span = last
            segment_words = get_words_in_range(segment_payload, 0.0, span)
            if segment_words:
                return segment_words, False
        if allow_transcribe and segment_path.exists():
            transcribed = transcribe_clip_segment(segment_path, span or duration)
            if transcribed:
                return transcribed, False
    return [], True


def transcribe_clip_segment(video_path: Path, duration: float) -> List[Dict[str, Any]]:
    """Word timings for one clip, cached beside that file. Empty on failure."""
    from .config import get_config
    from .media.transcription import (
        _get_transcript_with_assemblyai,
        _get_transcript_with_whisper,
        get_video_transcript,
    )

    existing = load_cached_transcript_data(video_path)
    span = duration if duration and duration > 0 else 0.0
    if existing and existing.get("words") and span > 0:
        return get_words_in_range(existing, 0.0, span)
    runtime = get_config()
    try:
        if runtime.transcription_provider == "whisper":
            _get_transcript_with_whisper(video_path, runtime)
        elif runtime.transcription_provider == "youtube_captions":
            # YouTube captions have no word timings. Prefer a timed provider.
            if runtime.assembly_ai_api_key:
                _get_transcript_with_assemblyai(video_path, "universal", runtime)
            else:
                _get_transcript_with_whisper(video_path, runtime)
        elif runtime.assembly_ai_api_key or runtime.transcription_provider == "assemblyai":
            get_video_transcript(video_path, "universal", None)
        else:
            return []
    except Exception:
        logger.warning("Clip segment transcription failed for %s", video_path.name, exc_info=True)
        return []
    payload = load_cached_transcript_data(video_path)
    if not payload or not payload.get("words"):
        return []
    if span <= 0:
        span = max(float(word.get("end") or 0) for word in payload["words"]) / 1000.0
    return get_words_in_range(payload, 0.0, span or 0.01)


def export_with_preset(input_path: Path, output_dir: Path, preset_name: str) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    preset = EXPORT_PRESETS.get(preset_name)
    if not preset:
        raise ValueError(f"Unknown export preset: {preset_name}")

    output_path = output_dir / _safe_name(preset.name)
    scale_filter = (
        f"scale={preset.width}:{preset.height}:"
        "force_original_aspect_ratio=decrease:flags=lanczos,"
        f"pad={preset.width}:{preset.height}:(ow-iw)/2:(oh-ih)/2,"
        "setsar=1"
    )
    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        scale_filter,
        "-c:v",
        "libx264",
        "-preset",
        "slow",
        "-crf",
        "18",
        "-maxrate",
        preset.video_bitrate,
        "-bufsize",
        _double_bitrate(preset.video_bitrate),
        "-pix_fmt",
        "yuv420p",
        "-profile:v",
        "high",
        "-c:a",
        "aac",
        "-b:a",
        preset.audio_bitrate,
        "-ar",
        "48000",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    _run(command)
    return output_path
