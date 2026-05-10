"""
Clip editing helpers for trim/split/merge/caption/export workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
import subprocess
import tempfile
import uuid


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
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("caption")
    words = [word for word in caption_text.split() if word.strip()]
    if not words:
        _run(["ffmpeg", "-y", "-i", str(input_path), *_encode_args(), str(output_path)])
        return output_path

    width, height = _ffprobe_size(input_path)
    duration = _ffprobe_duration(input_path)
    y_position = {
        "top": int(height * 0.18),
        "middle": int(height * 0.52),
        "bottom": int(height * 0.78),
    }.get(position, int(height * 0.78))
    highlighted = {word.strip().lower() for word in highlight_words if word.strip()}
    word_duration = max(duration / max(len(words), 1), 0.1)
    ass_path = output_dir / f"captions_{uuid.uuid4().hex[:12]}.ass"

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,64,{_ass_color("#FFFFFF")},&H000000FF,&H00000000,&H99000000,1,0,0,0,100,100,0,0,1,2,0,5,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for idx, word in enumerate(words):
        start = idx * word_duration
        end = min(duration, start + word_duration)
        color = (
            _ass_color("#FFD700")
            if word.lower().strip(".,!?;:") in highlighted
            else _ass_color("#FFFFFF")
        )
        events.append(
            "Dialogue: 0,"
            f"{_ass_timestamp(start)},{_ass_timestamp(end)},Default,,0,0,0,,"
            f"{{\\pos({width // 2},{y_position})\\c{color}}}{_escape_ass_text(word)}"
        )

    try:
        ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
        _run(
            [
                "ffmpeg",
                "-y",
                "-i",
                str(input_path),
                "-vf",
                f"subtitles=filename={_escape_filter_path(ass_path)}",
                *_encode_args(),
                str(output_path),
            ]
        )
    finally:
        ass_path.unlink(missing_ok=True)

    return output_path


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
