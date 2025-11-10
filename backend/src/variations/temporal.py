"""
Temporal variation generation - create multiple duration options from same moment.

Preserves the viral hook (first 3 seconds) while extending the ending.
"""
import logging
import random
from typing import List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class TemporalVariation:
    """A temporal variation of a clip."""
    type: str  # 'base', '+4s', '+35s'
    start_time: float  # In seconds
    end_time: float  # In seconds
    duration: float  # In seconds
    frame_offset: float  # Random offset in seconds


def generate_temporal_variations(
    base_start: float,
    base_end: float,
    video_duration: float,
    include_frame_offset: bool = True
) -> List[TemporalVariation]:
    """
    Generate 3 temporal variations from a base clip.

    Args:
        base_start: Original start time in seconds
        base_end: Original end time in seconds
        video_duration: Total video duration (for boundary checks)
        include_frame_offset: Add random 5-10 frame offset to defeat duplicate detection

    Returns:
        List of 3 TemporalVariation objects
    """
    variations = []

    # Base variation - original length
    base_offset = _generate_frame_offset() if include_frame_offset else 0.0
    variations.append(TemporalVariation(
        type='base',
        start_time=base_start + base_offset,
        end_time=base_end,
        duration=(base_end - base_start),
        frame_offset=base_offset
    ))

    # +4s variation - extend end by 4 seconds
    plus4_offset = _generate_frame_offset() if include_frame_offset else 0.0
    plus4_end = min(base_end + 4.0, video_duration)
    variations.append(TemporalVariation(
        type='+4s',
        start_time=base_start + plus4_offset,
        end_time=plus4_end,
        duration=(plus4_end - base_start),
        frame_offset=plus4_offset
    ))

    # +35s variation - extend end by 35 seconds
    plus35_offset = _generate_frame_offset() if include_frame_offset else 0.0
    plus35_end = min(base_end + 35.0, video_duration)
    variations.append(TemporalVariation(
        type='+35s',
        start_time=base_start + plus35_offset,
        end_time=plus35_end,
        duration=(plus35_end - base_start),
        frame_offset=plus35_offset
    ))

    logger.debug(f"Generated {len(variations)} temporal variations from {base_start:.2f}-{base_end:.2f}")

    return variations


def _generate_frame_offset() -> float:
    """
    Generate random frame offset for duplicate detection defeat.

    Returns random offset between 5-10 frames at 60fps.

    Returns:
        Float in seconds (0.083 - 0.167 seconds)
    """
    frames = random.uniform(5, 10)
    return frames / 60.0  # Convert to seconds at 60fps


def batch_generate_temporal_variations(
    clips: List[Dict[str, Any]],
    video_duration: float
) -> List[Dict[str, Any]]:
    """
    Generate temporal variations for multiple clips.

    Args:
        clips: List of clip dicts with start_time, end_time
        video_duration: Total video duration

    Returns:
        List of all variations (3x the input)
    """
    all_variations = []

    for i, clip in enumerate(clips):
        # Parse timestamps
        start_parts = clip['start_time'].split(':')
        end_parts = clip['end_time'].split(':')

        start_seconds = int(start_parts[0]) * 60 + int(start_parts[1])
        end_seconds = int(end_parts[0]) * 60 + int(end_parts[1])

        # Generate variations
        variations = generate_temporal_variations(
            start_seconds,
            end_seconds,
            video_duration,
            include_frame_offset=True
        )

        # Convert back to dicts with original metadata
        for var in variations:
            var_dict = {
                **clip,  # Copy all original metadata
                'variation_type': var.type,
                'start_time_seconds': var.start_time,
                'end_time_seconds': var.end_time,
                'duration_seconds': var.duration,
                'frame_offset': var.frame_offset,
                'original_clip_index': i
            }
            all_variations.append(var_dict)

    logger.info(f"Generated {len(all_variations)} total variations from {len(clips)} base clips")

    return all_variations
