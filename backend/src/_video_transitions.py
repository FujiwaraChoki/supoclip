"""Transition helpers extracted from ``video_utils.py``.

``apply_transition_effect`` re-imports the MoviePy symbols inside the function
body on purpose: tests patch them via ``moviepy.VideoFileClip`` etc.
``VideoProcessor`` is routed through ``video_utils`` so
``patch("src.video_utils.VideoProcessor")`` keeps working.
"""

from pathlib import Path
from typing import List
import logging

logger = logging.getLogger(__name__)


def get_available_transitions() -> List[str]:
    """Get list of available transition video files."""
    transitions_dir = Path(__file__).parent.parent / "transitions"
    if not transitions_dir.exists():
        logger.warning("Transitions directory not found")
        return []

    transition_files = []
    for file_path in transitions_dir.glob("*.mp4"):
        transition_files.append(str(file_path))

    logger.info(f"Found {len(transition_files)} transition files")
    return transition_files


def apply_transition_effect(
    clip1_path: Path, clip2_path: Path, transition_path: Path, output_path: Path
) -> bool:
    """Apply transition effect between two clips using a transition video."""
    from moviepy import VideoFileClip, CompositeVideoClip, concatenate_videoclips
    from moviepy.video.fx import FadeIn, FadeOut
    from . import video_utils as _vu

    clip1 = None
    clip2 = None
    transition = None
    clip1_tail = None
    clip2_intro = None
    clip2_remainder = None
    intro_segment = None
    final_clip = None

    try:
        clip1 = VideoFileClip(str(clip1_path))
        clip2 = VideoFileClip(str(clip2_path))
        transition = VideoFileClip(str(transition_path))

        transition_duration = min(1.5, transition.duration, clip1.duration, clip2.duration)
        if transition_duration <= 0:
            logger.warning("Transition duration is zero, skipping transition effect")
            return False

        transition = transition.subclipped(0, transition_duration)

        clip_size = clip2.size
        transition = transition.resized(clip_size)

        clip1_tail_start = max(0, clip1.duration - transition_duration)
        clip1_tail = clip1.subclipped(clip1_tail_start, clip1.duration).with_effects(
            [FadeOut(transition_duration)]
        )
        clip2_intro = clip2.subclipped(0, transition_duration).with_effects(
            [FadeIn(transition_duration)]
        )

        intro_segment = CompositeVideoClip(
            [clip1_tail, clip2_intro, transition], size=clip_size
        ).with_duration(transition_duration)
        if clip2_intro.audio is not None:
            intro_segment = intro_segment.with_audio(clip2_intro.audio)

        final_segments = [intro_segment]
        if clip2.duration > transition_duration:
            clip2_remainder = clip2.subclipped(transition_duration, clip2.duration)
            final_segments.append(clip2_remainder)

        final_clip = (
            concatenate_videoclips(final_segments, method="compose")
            if len(final_segments) > 1
            else intro_segment
        )

        processor = _vu.VideoProcessor()
        encoding_settings = processor.get_optimal_encoding_settings("high")

        final_clip.write_videofile(
            str(output_path),
            temp_audiofile="temp-audio.m4a",
            remove_temp=True,
            logger=None,
            **encoding_settings,
        )

        logger.info(f"Applied transition effect: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error applying transition effect: {e}")
        return False
    finally:
        for clip in (
            final_clip,
            intro_segment,
            clip2_remainder,
            clip2_intro,
            clip1_tail,
            transition,
            clip2,
            clip1,
        ):
            if clip is not None:
                try:
                    clip.close()
                except Exception:
                    pass
