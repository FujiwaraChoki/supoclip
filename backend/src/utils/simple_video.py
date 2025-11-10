"""
Simple video utilities - just cut clips, no effects.
"""
import logging
import asyncio
import os
from pathlib import Path
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


async def cut_clip_ffmpeg(
    input_path: str,
    output_path: str,
    start_time: float,
    duration: float
) -> bool:
    """
    Cut a clip from video using FFmpeg - NO EFFECTS, just timing.

    Preserves:
    - Original resolution (likely 16:9 horizontal)
    - Original aspect ratio
    - Original codec (re-encodes with H.264 for compatibility)

    No captions, no crop, no effects.

    Args:
        input_path: Source video file
        output_path: Output clip file
        start_time: Start time in seconds
        duration: Duration in seconds

    Returns:
        True if successful, False otherwise
    """
    # Ensure output directory exists
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # FFmpeg command - simple cut with re-encode
    cmd = [
        'ffmpeg',
        '-ss', str(start_time),      # Seek to start time
        '-i', input_path,             # Input file
        '-t', str(duration),          # Duration
        '-c:v', 'libx264',            # H.264 video codec
        '-preset', 'medium',          # Encoding speed/quality balance
        '-crf', '23',                 # Quality (18-28, lower=better)
        '-c:a', 'aac',                # AAC audio codec
        '-b:a', '128k',               # Audio bitrate
        '-movflags', '+faststart',    # Enable web streaming
        '-y',                         # Overwrite output
        output_path
    ]

    logger.debug(f"FFmpeg command: {' '.join(cmd)}")

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode != 0:
            logger.error(f"FFmpeg failed with code {process.returncode}")
            logger.error(f"stderr: {stderr.decode()[:500]}")
            return False

        logger.debug(f"✅ Created clip: {output_path}")
        return True

    except Exception as e:
        logger.error(f"Error running FFmpeg: {e}", exc_info=True)
        return False


async def get_video_duration(video_path: str) -> float:
    """
    Get video duration in seconds using FFprobe.

    Args:
        video_path: Path to video file

    Returns:
        Duration in seconds
    """
    cmd = [
        'ffprobe',
        '-v', 'error',
        '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1',
        video_path
    ]

    try:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )

        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            duration = float(stdout.decode().strip())
            logger.info(f"Video duration: {duration / 60:.1f} minutes")
            return duration
        else:
            logger.error(f"FFprobe failed: {stderr.decode()}")
            return 0.0

    except Exception as e:
        logger.error(f"Error getting video duration: {e}")
        return 0.0


async def batch_cut_clips(
    input_path: str,
    output_dir: str,
    clips: List[Dict[str, Any]],
    progress_callback=None
) -> List[str]:
    """
    Cut multiple clips from video in batch.

    Args:
        input_path: Source video file
        output_dir: Output directory for clips
        clips: List of clip dicts with start_time, end_time, title
        progress_callback: Optional async function(current, total)

    Returns:
        List of generated clip file paths
    """
    os.makedirs(output_dir, exist_ok=True)

    generated_clips = []
    total = len(clips)

    for i, clip in enumerate(clips):
        # Parse timestamps
        start_parts = clip['start_time'].split(':')
        end_parts = clip['end_time'].split(':')

        start_seconds = int(start_parts[0]) * 60 + int(start_parts[1])
        end_seconds = int(end_parts[0]) * 60 + int(end_parts[1])

        duration = end_seconds - start_seconds

        # Generate output filename
        safe_title = "".join(c for c in clip.get('title', f'clip_{i}') if c.isalnum() or c in (' ', '-', '_'))
        safe_title = safe_title[:50]  # Limit length
        output_filename = f"{i:04d}_{safe_title}.mp4"
        output_path = os.path.join(output_dir, output_filename)

        # Cut clip
        success = await cut_clip_ffmpeg(
            input_path,
            output_path,
            start_seconds,
            duration
        )

        if success:
            generated_clips.append(output_path)

        # Progress callback
        if progress_callback:
            await progress_callback(i + 1, total)

        # Log progress
        if (i + 1) % 10 == 0 or i == total - 1:
            logger.info(f"Generated {i + 1}/{total} clips")

    logger.info(f"✅ Batch generation complete: {len(generated_clips)}/{total} clips created")

    return generated_clips


def timestamp_to_seconds(timestamp: str) -> float:
    """
    Convert MM:SS timestamp to seconds.

    Args:
        timestamp: Timestamp string in MM:SS format

    Returns:
        Time in seconds
    """
    parts = timestamp.split(':')
    if len(parts) != 2:
        raise ValueError(f"Invalid timestamp format: {timestamp}")

    minutes = int(parts[0])
    seconds = int(parts[1])

    return minutes * 60 + seconds


def seconds_to_timestamp(seconds: float) -> str:
    """
    Convert seconds to MM:SS timestamp.

    Args:
        seconds: Time in seconds

    Returns:
        Timestamp string in MM:SS format
    """
    minutes = int(seconds // 60)
    secs = int(seconds % 60)

    return f"{minutes:02d}:{secs:02d}"
