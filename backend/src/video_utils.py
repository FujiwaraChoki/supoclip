"""
Utility functions for video-related operations.

Most of the heavy lifting lives in sibling ``_video_*`` modules; this file now
hosts the public entry points (``create_optimized_clip``,
``create_clips_from_segments``) and re-exports the helpers so existing
``patch("src.video_utils.<symbol>")`` tests keep working.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional
import logging

from moviepy import (
    ColorClip,
    CompositeVideoClip,
    TextClip,
    VideoFileClip,
    concatenate_videoclips,
)
from moviepy.video.fx import CrossFadeIn, CrossFadeOut, FadeIn, FadeOut

import assemblyai as aai
import srt  # noqa: F401 — re-exported for backwards-compatible patching
from datetime import timedelta  # noqa: F401

from .config import Config
from .caption_templates import get_template, CAPTION_TEMPLATES  # noqa: F401
from .font_registry import find_font_path  # noqa: F401
from ._video_encoder import VideoProcessor, _resolve_font_with_style  # noqa: F401
from ._video_helpers import (
    _chunk_words_by_speaker,  # noqa: F401
    format_ms_to_timestamp,  # noqa: F401
    get_safe_vertical_position,  # noqa: F401
    get_scaled_font_size,  # noqa: F401
    get_subtitle_max_width,  # noqa: F401
    get_words_in_range,  # noqa: F401
    parse_timestamp_to_seconds,
    round_to_even,
)
from ._video_transitions import (  # noqa: F401
    apply_transition_effect,
    get_available_transitions,
)
from ._video_face import (
    detect_faces_in_clip,  # noqa: F401
    detect_optimal_crop_region,
    filter_face_outliers,  # noqa: F401
)
from ._video_broll import (  # noqa: F401
    apply_broll_to_clip,
    create_9_16_clip,
    insert_broll_into_clip,
    resize_for_916,
)
from ._video_subtitles import (  # noqa: F401
    create_assemblyai_subtitles,
    create_fade_subtitles,
    create_karaoke_subtitles,
    create_pop_subtitles,
    create_static_subtitles,
)
from ._video_transcript import (
    cache_transcript_data,  # noqa: F401
    format_transcript_for_analysis,  # noqa: F401
    get_video_transcript,
    load_cached_transcript_data,  # noqa: F401
)

logger = logging.getLogger(__name__)
config = Config()
TRANSCRIPT_CACHE_SCHEMA_VERSION = 2


# Fraction of the source height to crop away when the user marks the original
# video as having burned-in subtitles on the top or bottom band. Trimming this
# strip before face-tracked crop pushes the original text fully out of frame,
# so our new subtitles can render at their normal position over clean pixels.
ORIGINAL_SUBTITLE_BAND_FRACTION = 0.15


def create_optimized_clip(
    video_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path,
    add_subtitles: bool = True,
    font_family: str = "THEBOLDFONT",
    font_size: int = 24,
    font_color: str = "#FFFFFF",
    caption_template: str = "default",
    output_format: str = "vertical",
    stroke_color_override: Optional[str] = None,
    stroke_width_override: Optional[int] = None,
    bold: bool = False,
    italic: bool = False,
    underline: bool = False,
    avoid_original_subtitle: str = "none",
) -> bool:
    """Create clip with optional subtitles.

    output_format:
      - 'vertical' (default): face-centered 9:16 crop
      - 'fit': letterbox-blur 9:16 (full source centered on blurred background)
      - 'original': keep source aspect ratio, no crop/resize
      - 'capcut': same as 'original' but paired with the CapCut export path —
        subtitles and B-roll are never burned in so the user can edit them as
        real tracks after opening the draft in CapCut.
    """
    try:
        duration = end_time - start_time
        if duration <= 0:
            logger.error(f"Invalid clip duration: {duration:.1f}s")
            return False

        # CapCut edit mode keeps the clip as-is for maximum edit-ability in
        # CapCut; the rendering path is identical to 'original'.
        keep_original = output_format in ("original", "capcut")
        fit_mode = output_format == "fit"
        logger.info(
            f"Creating clip: {start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s) "
            f"subtitles={add_subtitles} template '{caption_template}' format={output_format}"
        )

        # Fast path: no subtitles + original = ffmpeg stream copy (no re-encoding)
        if not add_subtitles and keep_original:
            import subprocess
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss", str(start_time),
                    "-i", str(video_path),
                    "-t", str(duration),
                    "-c", "copy",
                    "-movflags", "+faststart",
                    str(output_path),
                ],
                capture_output=True,
                text=True,
                timeout=300,
            )
            if result.returncode != 0:
                logger.error(f"ffmpeg stream copy failed: {result.stderr}")
                return False
            logger.info(f"Successfully created clip (stream copy): {output_path}")
            return True

        video = VideoFileClip(str(video_path))

        if start_time >= video.duration:
            logger.error(
                f"Start time {start_time}s exceeds video duration {video.duration:.1f}s"
            )
            video.close()
            return False

        # Crop away the top/bottom strip that holds the original burned-in
        # subtitles so they fall outside the frame before the 9:16 crop runs.
        if avoid_original_subtitle in ("bottom", "top"):
            orig_h = video.h
            band_px = int(orig_h * ORIGINAL_SUBTITLE_BAND_FRACTION)
            if band_px > 0 and band_px < orig_h:
                if avoid_original_subtitle == "bottom":
                    video = video.cropped(y1=0, y2=orig_h - band_px)
                else:
                    video = video.cropped(y1=band_px, y2=orig_h)
                logger.info(
                    f"Cropped {avoid_original_subtitle} band ({band_px}px / "
                    f"{ORIGINAL_SUBTITLE_BAND_FRACTION * 100:.0f}%) to hide "
                    f"original subtitles; new size {video.w}x{video.h}"
                )

        end_time = min(end_time, video.duration)
        clip = video.subclipped(start_time, end_time)

        if keep_original:
            processed_clip = clip
            target_width = round_to_even(processed_clip.w)
            target_height = round_to_even(processed_clip.h)
            if (target_width, target_height) != (processed_clip.w, processed_clip.h):
                processed_clip = processed_clip.resized((target_width, target_height))
            cropped_clip = None
        elif fit_mode:
            # Fit-with-blur mode: keep the full source centered, fill the
            # surrounding 9:16 area with a blurred zoomed copy. Face is never
            # clipped. Isolated branch — remove to disable without touching
            # the legacy crop path below.
            from .fit_renderer import apply_fit_with_blur
            composite, target_width, target_height = apply_fit_with_blur(
                clip, target_ratio=9 / 16
            )
            processed_clip = composite
            cropped_clip = None
        else:
            x_offset, y_offset, new_width, new_height = detect_optimal_crop_region(
                video, start_time, end_time, target_ratio=9 / 16
            )
            cropped_clip = clip.cropped(
                x1=x_offset,
                y1=y_offset,
                x2=x_offset + new_width,
                y2=y_offset + new_height,
            )
            target_width, target_height = (
                round_to_even(new_width),
                round_to_even(new_height),
            )
            processed_clip = cropped_clip

        final_clips = [processed_clip]

        if add_subtitles:
            subtitle_clips = create_assemblyai_subtitles(
                video_path,
                start_time,
                end_time,
                target_width,
                target_height,
                font_family,
                font_size,
                font_color,
                caption_template,
                stroke_color_override=stroke_color_override,
                stroke_width_override=stroke_width_override,
                bold=bold,
                italic=italic,
                underline=underline,
                avoid_original_subtitle=avoid_original_subtitle,
            )
            final_clips.extend(subtitle_clips)

        final_clip = (
            CompositeVideoClip(final_clips) if len(final_clips) > 1 else processed_clip
        )
        source_fps = clip.fps if clip.fps and clip.fps > 0 else 30

        processor = VideoProcessor(font_family, font_size, font_color)
        encoding_settings = processor.get_optimal_encoding_settings("high")

        final_clip.write_videofile(
            str(output_path),
            temp_audiofile="temp-audio.m4a",
            remove_temp=True,
            logger=None,
            fps=source_fps,
            **encoding_settings,
        )

        if final_clip is not processed_clip:
            final_clip.close()
        if processed_clip is not cropped_clip:
            processed_clip.close()
        if cropped_clip is not None:
            cropped_clip.close()
        clip.close()
        video.close()

        logger.info(f"Successfully created clip: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Failed to create clip: {e}")
        return False


def create_clips_from_segments(
    video_path: Path,
    segments: List[Dict[str, Any]],
    output_dir: Path,
    font_family: str = "THEBOLDFONT",
    font_size: int = 24,
    font_color: str = "#FFFFFF",
    caption_template: str = "default",
    output_format: str = "vertical",
    add_subtitles: bool = True,
) -> List[Dict[str, Any]]:
    """Create optimized video clips from segments with template support."""
    logger.info(
        f"Creating {len(segments)} clips subtitles={add_subtitles} template '{caption_template}'"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    clips_info = []

    for i, segment in enumerate(segments):
        try:
            logger.info(
                f"Processing segment {i + 1}: start='{segment.get('start_time')}', end='{segment.get('end_time')}'"
            )

            start_seconds = parse_timestamp_to_seconds(segment["start_time"])
            end_seconds = parse_timestamp_to_seconds(segment["end_time"])

            duration = end_seconds - start_seconds
            logger.info(
                f"Segment {i + 1} duration: {duration:.1f}s (start: {start_seconds}s, end: {end_seconds}s)"
            )

            if duration <= 0:
                logger.warning(
                    f"Skipping clip {i + 1}: invalid duration {duration:.1f}s (start: {start_seconds}s, end: {end_seconds}s)"
                )
                continue

            clip_filename = f"clip_{i + 1}_{segment['start_time'].replace(':', '')}-{segment['end_time'].replace(':', '')}.mp4"
            clip_path = output_dir / clip_filename

            success = create_optimized_clip(
                video_path,
                start_seconds,
                end_seconds,
                clip_path,
                add_subtitles,
                font_family,
                font_size,
                font_color,
                caption_template,
                output_format,
            )

            if success:
                clip_info = {
                    "clip_id": i + 1,
                    "filename": clip_filename,
                    "path": str(clip_path),
                    "start_time": segment["start_time"],
                    "end_time": segment["end_time"],
                    "duration": duration,
                    "text": segment["text"],
                    "relevance_score": segment["relevance_score"],
                    "reasoning": segment["reasoning"],
                    "virality_score": segment.get("virality_score", 0),
                    "hook_score": segment.get("hook_score", 0),
                    "engagement_score": segment.get("engagement_score", 0),
                    "value_score": segment.get("value_score", 0),
                    "shareability_score": segment.get("shareability_score", 0),
                    "hook_type": segment.get("hook_type"),
                }
                clips_info.append(clip_info)
                logger.info(f"Created clip {i + 1}: {duration:.1f}s")
            else:
                logger.error(f"Failed to create clip {i + 1}")

        except Exception as e:
            logger.error(f"Error processing clip {i + 1}: {e}")

    logger.info(f"Successfully created {len(clips_info)}/{len(segments)} clips")
    return clips_info


def create_clips_with_transitions(
    video_path: Path,
    segments: List[Dict[str, Any]],
    output_dir: Path,
    font_family: str = "THEBOLDFONT",
    font_size: int = 24,
    font_color: str = "#FFFFFF",
    caption_template: str = "default",
    output_format: str = "vertical",
    add_subtitles: bool = True,
) -> List[Dict[str, Any]]:
    """Create standalone video clips without inter-clip transitions.

    Kept as a backward-compatible wrapper for older call sites.
    """
    logger.info(
        f"Creating {len(segments)} standalone clips subtitles={add_subtitles} template '{caption_template}'"
    )
    logger.info(
        "Inter-clip transitions are disabled for standalone SupoClip exports"
    )
    return create_clips_from_segments(
        video_path,
        segments,
        output_dir,
        font_family,
        font_size,
        font_color,
        caption_template,
        output_format,
        add_subtitles,
    )


def get_video_transcript_with_assemblyai(path: Path) -> str:
    """Backward compatibility wrapper."""
    return get_video_transcript(path)
