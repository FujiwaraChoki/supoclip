"""
Clip editing helpers for trim/split/merge/caption/export workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import subprocess
import tempfile
import uuid

from .caption_templates import get_template
from .video_utils import (
    ass_fonts_dir,
    build_assemblyai_ass_subtitles,
    get_words_for_keep_ranges,
    get_words_in_range,
    load_cached_transcript_data,
)


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
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(" ", "\\ ")
    )


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
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("caption")
    words = [word for word in caption_text.split() if word.strip()]
    if not words:
        _run(["ffmpeg", "-y", "-i", str(input_path), *_encode_args(), str(output_path)])
        return output_path

    width, height = _ffprobe_size(input_path)
    duration = _ffprobe_duration(input_path)
    position_y = {
        "top": 0.18,
        "middle": 0.52,
        "bottom": 0.78,
    }.get(position, 0.78)
    ass_path = output_dir / f"captions_{uuid.uuid4().hex[:12]}.ass"

    transcript_path = transcript_video_path or input_path
    transcript_data = load_cached_transcript_data(transcript_path)
    timed_words: List[Dict[str, Any]] = []
    if transcript_data and transcript_data.get("words"):
        if source_ranges:
            timed_words = get_words_for_keep_ranges(transcript_data, source_ranges)
        else:
            timed_words = get_words_in_range(transcript_data, 0.0, duration)

    caption_words = _caption_words_with_timings(words, timed_words, duration)
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
        caption_words=caption_words,
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


def _caption_words_with_timings(
    words: List[str],
    timed_words: List[Dict[str, Any]],
    duration: float,
) -> List[Dict[str, Any]]:
    """Apply edited text to source timings, or evenly split when none exist."""
    if timed_words:
        source_count = len(timed_words)
        edited_count = len(words)
        if edited_count == source_count:
            return [
                {
                    "text": word,
                    "start": float(timed["start"]),
                    "end": float(timed["end"]),
                    "confidence": timed.get("confidence", 1.0),
                }
                for word, timed in zip(words, timed_words)
            ]

        # Word counts differ: spread the edited words proportionally across the
        # spoken span so timings stay strictly increasing (no stacked words).
        span_start = float(timed_words[0]["start"])
        span_end = max(float(timed_words[-1]["end"]), span_start + 0.05 * edited_count)
        step = (span_end - span_start) / edited_count
        return [
            {
                "text": word,
                "start": span_start + index * step,
                "end": span_start + (index + 1) * step,
                "confidence": 1.0,
            }
            for index, word in enumerate(words)
        ]

    word_duration = max(duration / max(len(words), 1), 0.1)
    return [
        {
            "text": word,
            "start": index * word_duration,
            "end": min(duration, (index + 1) * word_duration),
            "confidence": 1.0,
        }
        for index, word in enumerate(words)
    ]


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
