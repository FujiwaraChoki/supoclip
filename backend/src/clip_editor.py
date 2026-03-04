"""
Clip editing helpers for trim/split/merge/caption/export workflows.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List
import uuid
import subprocess

from moviepy import VideoFileClip, concatenate_videoclips, CompositeVideoClip, TextClip


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


def _double_bitrate(value: str) -> str:
    normalized = value.strip().lower()
    if normalized.endswith("m"):
        return f"{int(float(normalized[:-1]) * 2)}M"
    if normalized.endswith("k"):
        return f"{int(float(normalized[:-1]) * 2)}k"
    return value


def _source_fps(clip: VideoFileClip) -> float:
    fps = clip.fps if clip.fps and clip.fps > 0 else 30
    return float(fps)


def _high_quality_encode_options(fps: float) -> dict[str, object]:
    return {
        "codec": "libx264",
        "audio_codec": "aac",
        "audio_bitrate": "256k",
        "preset": "slow",
        "logger": None,
        "fps": fps,
        "ffmpeg_params": [
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            "-profile:v",
            "high",
            "-movflags",
            "+faststart",
            "-sws_flags",
            "lanczos",
        ],
    }


def trim_clip_file(
    input_path: Path, output_dir: Path, start_offset: float, end_offset: float
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("trim")

    clip = VideoFileClip(str(input_path))
    try:
        end_time = max(start_offset + 0.1, clip.duration - max(end_offset, 0.0))
        trimmed = clip.subclipped(max(0.0, start_offset), min(end_time, clip.duration))
        trimmed.write_videofile(str(output_path), **_high_quality_encode_options(_source_fps(clip)))
        trimmed.close()
    finally:
        clip.close()

    return output_path


def split_clip_file(
    input_path: Path, output_dir: Path, split_time: float
) -> tuple[Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    first_path = output_dir / _safe_name("split_a")
    second_path = output_dir / _safe_name("split_b")

    clip = VideoFileClip(str(input_path))
    try:
        source_fps = _source_fps(clip)
        s = max(0.2, min(split_time, clip.duration - 0.2))
        part_a = clip.subclipped(0, s)
        part_b = clip.subclipped(s, clip.duration)
        part_a.write_videofile(str(first_path), **_high_quality_encode_options(source_fps))
        part_b.write_videofile(str(second_path), **_high_quality_encode_options(source_fps))
        part_a.close()
        part_b.close()
    finally:
        clip.close()

    return first_path, second_path


def merge_clip_files(paths: Iterable[Path], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / _safe_name("merge")

    clips = [VideoFileClip(str(p)) for p in paths]
    try:
        source_fps = next((_source_fps(clip) for clip in clips if clip.fps and clip.fps > 0), 30.0)
        merged = concatenate_videoclips(clips, method="compose")
        merged.write_videofile(str(output_path), **_high_quality_encode_options(source_fps))
        merged.close()
    finally:
        for clip in clips:
            clip.close()

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

    base_clip = VideoFileClip(str(input_path))
    try:
        words = [w for w in caption_text.split() if w.strip()]
        source_fps = _source_fps(base_clip)
        if not words:
            base_clip.write_videofile(str(output_path), **_high_quality_encode_options(source_fps))
            return output_path

        y_position = {
            "top": int(base_clip.h * 0.18),
            "middle": int(base_clip.h * 0.52),
            "bottom": int(base_clip.h * 0.78),
        }.get(position, int(base_clip.h * 0.78))

        highlighted = {w.strip().lower() for w in highlight_words if w.strip()}
        word_duration = max(base_clip.duration / max(len(words), 1), 0.1)

        caption_layers = []
        for idx, word in enumerate(words):
            color = (
                "#FFD700" if word.lower().strip(".,!?;:") in highlighted else "#FFFFFF"
            )
            text_layer = (
                TextClip(
                    text=word,
                    font_size=64,
                    color=color,
                    stroke_color="black",
                    stroke_width=2,
                    method="label",
                )
                .with_start(idx * word_duration)
                .with_duration(word_duration)
                .with_position(("center", y_position))
            )
            caption_layers.append(text_layer)

        composite = CompositeVideoClip([base_clip] + caption_layers)
        composite.write_videofile(str(output_path), **_high_quality_encode_options(source_fps))
        composite.close()
        for layer in caption_layers:
            layer.close()
    finally:
        base_clip.close()

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
        f"pad={preset.width}:{preset.height}:(ow-iw)/2:(oh-ih)/2"
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
    subprocess.run(command, check=True, capture_output=True)
    return output_path
