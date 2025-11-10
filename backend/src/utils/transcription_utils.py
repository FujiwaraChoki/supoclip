"""
Transcription utilities with MLX Whisper primary and AssemblyAI fallback.
"""
import logging
import os
from pathlib import Path
from typing import Dict, Any, Optional
import aiohttp
import asyncio

logger = logging.getLogger(__name__)


async def transcribe_with_mlx(
    video_path: str,
    mlx_url: str = None,
    timeout: int = 1200
) -> Optional[Dict[str, Any]]:
    """
    Transcribe video using MLX Whisper server.

    Args:
        video_path: Path to video file
        mlx_url: MLX server URL (defaults to env var or localhost:5001)
        timeout: Request timeout in seconds (default 20 minutes)

    Returns:
        Transcript dict with word-level timing, or None if failed
    """
    if mlx_url is None:
        mlx_url = os.getenv('MLX_TRANSCRIPTION_URL', 'http://localhost:5001')

    transcribe_url = f"{mlx_url}/transcribe"

    logger.info(f"🎤 Attempting MLX transcription: {video_path}")
    logger.info(f"MLX URL: {transcribe_url}")

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                transcribe_url,
                json={'video_path': video_path},
                timeout=aiohttp.ClientTimeout(total=timeout)
            ) as response:
                if response.status == 200:
                    result = await response.json()
                    logger.info(f"✅ MLX transcription successful")
                    logger.info(f"Transcript: {len(result.get('text', ''))} chars, "
                              f"{len(result.get('words', []))} words")
                    return result
                else:
                    error_text = await response.text()
                    logger.warning(f"MLX server returned {response.status}: {error_text}")
                    return None

    except aiohttp.ClientConnectorError as e:
        logger.warning(f"Cannot connect to MLX server at {mlx_url}: {e}")
        logger.info("Is MLX transcription server running? Start with: python3 mlx_transcription_server.py")
        return None

    except asyncio.TimeoutError:
        logger.error(f"MLX transcription timed out after {timeout}s")
        return None

    except Exception as e:
        logger.error(f"MLX transcription error: {e}", exc_info=True)
        return None


async def transcribe_with_assemblyai(
    video_path: str,
    api_key: str = None
) -> Optional[Dict[str, Any]]:
    """
    Transcribe video using AssemblyAI (fallback method).

    Args:
        video_path: Path to video file
        api_key: AssemblyAI API key (defaults to env var)

    Returns:
        Transcript dict with word-level timing, or None if failed
    """
    if api_key is None:
        api_key = os.getenv('ASSEMBLY_AI_API_KEY')

    if not api_key:
        logger.error("AssemblyAI API key not found")
        return None

    logger.info(f"🎤 Starting AssemblyAI transcription (fallback): {video_path}")

    try:
        import assemblyai as aai

        aai.settings.api_key = api_key
        transcriber = aai.Transcriber()

        config = aai.TranscriptionConfig(
            speaker_labels=False,
            punctuate=True,
            format_text=True,
            speech_model=aai.SpeechModel.best
        )

        # Run synchronous transcription in executor
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None,
            lambda: transcriber.transcribe(str(video_path), config=config)
        )

        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AssemblyAI transcription failed: {transcript.error}")
            return None

        # Convert to standard format
        words_data = []
        if transcript.words:
            for word in transcript.words:
                words_data.append({
                    'text': word.text,
                    'start': word.start,
                    'end': word.end,
                    'confidence': word.confidence if hasattr(word, 'confidence') else 1.0
                })

        result = {
            'text': transcript.text,
            'words': words_data,
            'source': 'assemblyai'
        }

        logger.info(f"✅ AssemblyAI transcription successful")
        logger.info(f"Transcript: {len(result['text'])} chars, {len(words_data)} words")

        return result

    except ImportError:
        logger.error("assemblyai package not installed. Run: pip install assemblyai")
        return None

    except Exception as e:
        logger.error(f"AssemblyAI transcription error: {e}", exc_info=True)
        return None


async def get_transcript_with_fallback(
    video_path: str,
    prefer_mlx: bool = True
) -> Dict[str, Any]:
    """
    Get video transcript using MLX (primary) with AssemblyAI fallback.

    This is the main entry point for transcription.

    Args:
        video_path: Path to video file
        prefer_mlx: Try MLX first if True

    Returns:
        Transcript dict with word-level timing

    Raises:
        Exception: If all transcription methods fail
    """
    from .path_utils import validate_video_path

    # Validate and translate path
    accessible_path = validate_video_path(video_path)
    logger.info(f"📝 Transcribing video: {accessible_path}")

    transcript_result = None

    # Try MLX first if preferred
    if prefer_mlx:
        logger.info("Attempting MLX transcription...")
        transcript_result = await transcribe_with_mlx(accessible_path)

        if transcript_result:
            logger.info("✅ Using MLX transcription")
            return transcript_result

        logger.info("MLX transcription not available, falling back to AssemblyAI")

    # Fall back to AssemblyAI
    logger.info("Attempting AssemblyAI transcription...")
    transcript_result = await transcribe_with_assemblyai(accessible_path)

    if transcript_result:
        logger.info("✅ Using AssemblyAI transcription")
        return transcript_result

    # Both methods failed
    raise Exception(
        "All transcription methods failed. "
        "Ensure either MLX server is running or AssemblyAI API key is set."
    )


def format_transcript_for_ai(transcript_data: Dict[str, Any]) -> str:
    """
    Format transcript data for AI analysis.

    Converts word-level timing data into readable timestamped format:
    [00:12] Hello there
    [00:15] How are you doing today

    Args:
        transcript_data: Transcript dict from MLX or AssemblyAI

    Returns:
        Formatted transcript string
    """
    words = transcript_data.get('words', [])
    if not words:
        return transcript_data.get('text', '')

    # Group words into segments (8-10 words or sentence boundaries)
    formatted_lines = []
    current_segment = []
    current_start = None
    segment_word_count = 0
    max_words_per_segment = 8

    for word in words:
        if current_start is None:
            current_start = word['start']

        current_segment.append(word['text'])
        segment_word_count += 1

        # End segment at natural breaks or word limit
        is_sentence_end = word['text'].rstrip().endswith(('.', '!', '?'))
        at_word_limit = segment_word_count >= max_words_per_segment

        if is_sentence_end or at_word_limit:
            if current_segment:
                start_time = format_ms_to_timestamp(current_start)
                end_time = format_ms_to_timestamp(word['end'])
                text = ' '.join(current_segment)
                formatted_lines.append(f"[{start_time} - {end_time}] {text}")

            current_segment = []
            current_start = None
            segment_word_count = 0

    # Handle any remaining words
    if current_segment and current_start is not None:
        start_time = format_ms_to_timestamp(current_start)
        end_time = format_ms_to_timestamp(words[-1]['end'])
        text = ' '.join(current_segment)
        formatted_lines.append(f"[{start_time} - {end_time}] {text}")

    return '\n'.join(formatted_lines)


def format_ms_to_timestamp(ms: int) -> str:
    """
    Format milliseconds to MM:SS format.

    Args:
        ms: Milliseconds

    Returns:
        Formatted timestamp string (MM:SS)
    """
    seconds = ms // 1000
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def cache_transcript_data(video_path: str, transcript_data: Dict[str, Any]) -> None:
    """
    Cache transcript data to disk for reuse.

    Args:
        video_path: Video file path
        transcript_data: Transcript data to cache
    """
    import json

    video_path_obj = Path(video_path)
    cache_path = video_path_obj.with_suffix('.transcript_cache.json')

    try:
        with open(cache_path, 'w') as f:
            json.dump(transcript_data, f)
        logger.info(f"💾 Cached transcript to: {cache_path}")
    except Exception as e:
        logger.warning(f"Failed to cache transcript: {e}")


def load_cached_transcript(video_path: str) -> Optional[Dict[str, Any]]:
    """
    Load cached transcript data from disk.

    Args:
        video_path: Video file path

    Returns:
        Cached transcript data or None if not found
    """
    import json

    video_path_obj = Path(video_path)
    cache_path = video_path_obj.with_suffix('.transcript_cache.json')

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, 'r') as f:
            data = json.load(f)
        logger.info(f"📂 Loaded cached transcript from: {cache_path}")
        return data
    except Exception as e:
        logger.warning(f"Failed to load cached transcript: {e}")
        return None
