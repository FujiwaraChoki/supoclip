"""Render translated, captioned, and dubbed clip variants."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from openai import AsyncOpenAI

from ..config import get_config
from ..content_ai import translate_transcript
from ..utils.async_helpers import run_in_thread
from ..video_utils import ffmpeg_escape_filter_path


def _sentences(text: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"(?<=[.!?؟。！？])\s+", text) if part.strip()]
    return parts or [text.strip()]


def _srt_timestamp(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def _write_even_srt(text: str, duration: float, path: Path) -> None:
    sentences = _sentences(text)
    weights = [max(1, len(item.split())) for item in sentences]
    total = sum(weights)
    cursor = 0.0
    cues = []
    for index, (sentence, weight) in enumerate(zip(sentences, weights), start=1):
        cue_duration = duration * weight / total
        end = duration if index == len(sentences) else cursor + cue_duration
        cues.append(
            f"{index}\n{_srt_timestamp(cursor)} --> {_srt_timestamp(end)}\n{sentence}\n"
        )
        cursor = end
    path.write_text("\n".join(cues), encoding="utf-8")


def _burn_subtitles(source: Path, srt: Path, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(source), "-vf",
            f"subtitles=filename={ffmpeg_escape_filter_path(srt)}",
            "-c:v", "libx264", "-preset", "medium", "-crf", "18",
            "-c:a", "copy", "-movflags", "+faststart", str(output),
        ],
        check=True,
        capture_output=True,
    )


def _mux_dub(source: Path, audio: Path, output: Path, duration: float) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg", "-y", "-i", str(source), "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
            "-af", f"apad=whole_dur={duration:.3f}", "-t", f"{duration:.3f}",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(output),
        ],
        check=True,
        capture_output=True,
    )


async def render_localized_variant(
    *,
    source_path: Path,
    transcript: str,
    duration: float,
    output_path: Path,
    target_language: str,
    voice: str = "alloy",
    dub: bool = False,
) -> tuple[str, dict]:
    localized = await translate_transcript(transcript, target_language)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    srt_path = output_path.with_suffix(".srt")
    await run_in_thread(_write_even_srt, localized.translated_text, duration, srt_path)

    if dub:
        runtime_config = get_config()
        if not runtime_config.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for AI dubbing")
        audio_path = output_path.with_suffix(".mp3")
        speech = await AsyncOpenAI(api_key=runtime_config.openai_api_key).audio.speech.create(
            model="tts-1",
            voice=voice,
            input=localized.translated_text,
            response_format="mp3",
        )
        audio_path.write_bytes(speech.read())
        await run_in_thread(_mux_dub, source_path, audio_path, output_path, duration)
        # The generated voice is intentionally disclosed in persisted metadata/UI.
        metadata = {"ai_voice": True, "caption_path": str(srt_path), "audio_path": str(audio_path)}
    else:
        await run_in_thread(_burn_subtitles, source_path, srt_path, output_path)
        metadata = {"ai_voice": False, "caption_path": str(srt_path)}
    return localized.translated_text, metadata
