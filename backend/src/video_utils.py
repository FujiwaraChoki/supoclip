"""
Utility functions for video-related operations.
Optimized for ffmpeg, AssemblyAI integration, and high-quality output.
"""

from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import logging
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import json
import re
import uuid
import shutil
import subprocess
import tempfile
import time

import cv2

import assemblyai as aai
import httpx
import srt
from datetime import timedelta

from .config import get_config
from .clip_cleanup import DEFAULT_FILTERED_WORDS, clip_cleanup_enabled
from .clip_source_map import (
    normalize_source_ranges,
    save_clip_source_ranges,
)
from .caption_templates import get_template, CAPTION_TEMPLATES
from .emoji_captions import annotate_caption_words
from .font_registry import FONTS_DIR, find_font_path, get_font_family_name

logger = logging.getLogger(__name__)
TRANSCRIPT_CACHE_SCHEMA_VERSION = 2
VALID_OUTPUT_FORMATS = {"vertical", "vertical_pan", "vertical_split", "original"}
# Family name of the bundled colour-emoji font (fonts/NotoColorEmoji.ttf). We
# force it explicitly per-emoji via an ASS \fn override so libass renders colour
# emojis reliably instead of depending on automatic Unicode font fallback.
EMOJI_FONT_NAME = "Noto Color Emoji"
CLIP_END_SENTENCE_EXTENSION_SECONDS = 3.0
CLIP_END_PADDING_SECONDS = 0.35
SENTENCE_END_RE = re.compile(r"""[.!?]["')\]}]*$""")


class VideoProcessor:
    """Handles video processing operations with optimized settings."""

    def __init__(
        self,
        font_family: str = "THEBOLDFONT",
        font_size: int = 24,
        font_color: str = "#FFFFFF",
    ):
        self.font_family = font_family
        self.font_size = font_size
        self.font_color = font_color
        resolved_font = find_font_path(font_family, allow_all_user_fonts=True)
        if not resolved_font:
            resolved_font = find_font_path("TikTokSans-Regular")
        if not resolved_font:
            resolved_font = find_font_path("THEBOLDFONT")
        self.font_path = str(resolved_font) if resolved_font else ""

    def get_optimal_encoding_settings(
        self, target_quality: str = "high"
    ) -> Dict[str, Any]:
        """Get optimal encoding settings for different quality levels."""
        settings = {
            "high": {
                "codec": "libx264",
                "audio_codec": "aac",
                "audio_bitrate": "256k",
                "preset": "slow",
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
            },
            "medium": {
                "codec": "libx264",
                "audio_codec": "aac",
                "bitrate": "4000k",
                "audio_bitrate": "192k",
                "preset": "fast",
                "ffmpeg_params": ["-crf", "23", "-pix_fmt", "yuv420p"],
            },
        }
        return settings.get(target_quality, settings["high"])


def _prepare_audio_for_transcription(video_path: Path) -> Path:
    """Extract a compact audio-only file before uploading to AssemblyAI."""
    audio_path = video_path.with_name(f"{video_path.stem}.assemblyai.mp3")
    if audio_path.exists() and audio_path.stat().st_size > 0:
        return audio_path

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    try:
        result = run_ffmpeg_command(command, timeout=900)
    except FileNotFoundError:
        logger.warning(
            "ffmpeg is not available; falling back to source video for transcription"
        )
        return video_path

    if result.returncode != 0 or not audio_path.exists() or audio_path.stat().st_size == 0:
        logger.warning(
            "Failed to extract transcription audio with ffmpeg; falling back to source video"
        )
        return video_path

    logger.info(
        "Prepared transcription audio: %s (%.2f MB)",
        audio_path,
        audio_path.stat().st_size / (1024 * 1024),
    )
    return audio_path


def _submit_and_wait_for_assemblyai_transcript(
    transcriber,
    media_path: Path,
    config_obj,
    timeout_seconds: int,
):
    """Submit a transcript job and poll with a total timeout."""
    submitted = transcriber.submit(str(media_path), config=config_obj)
    if not submitted.id:
        raise RuntimeError("AssemblyAI did not return a transcript ID")

    logger.info("AssemblyAI transcript submitted: %s", submitted.id)
    deadline = time.monotonic() + timeout_seconds
    next_log_at = 0.0

    while True:
        response = aai.api.get_transcript(
            submitted._client.http_client,  # noqa: SLF001 - AssemblyAI exposes no timeout-aware poller.
            submitted.id,
        )
        transcript = aai.Transcript.from_response(
            client=submitted._client,  # noqa: SLF001
            response=response,
        )

        if transcript.status in (
            aai.TranscriptStatus.completed,
            aai.TranscriptStatus.error,
        ):
            return transcript

        now = time.monotonic()
        if now >= deadline:
            raise TimeoutError(
                f"AssemblyAI transcript {submitted.id} did not complete within {timeout_seconds}s"
            )

        if now >= next_log_at:
            logger.info(
                "AssemblyAI transcript %s still %s",
                submitted.id,
                transcript.status,
            )
            next_log_at = now + 30

        time.sleep(aai.settings.polling_interval)


def get_video_transcript(video_path: Path, speech_model: str = "best") -> str:
    """Get transcript using AssemblyAI with word-level timing for precise subtitles."""
    logger.info(f"Getting transcript for: {video_path}")

    # Configure AssemblyAI
    runtime_config = get_config()
    aai.settings.api_key = runtime_config.assembly_ai_api_key
    aai.settings.http_timeout = runtime_config.assembly_ai_http_timeout_seconds
    transcriber = aai.Transcriber()

    # Request word-level timestamps for precise subtitle sync
    speech_model_value = aai.SpeechModel.best
    if speech_model == "nano":
        speech_model_value = aai.SpeechModel.nano

    config_obj = aai.TranscriptionConfig(
        speaker_labels=True,
        punctuate=True,
        format_text=True,
        speech_model=speech_model_value,
    )

    try:
        logger.info("Starting AssemblyAI transcription")
        transcription_media_path = _prepare_audio_for_transcription(video_path)
        transcript = None
        for attempt in range(1, 4):
            try:
                transcript = _submit_and_wait_for_assemblyai_transcript(
                    transcriber,
                    transcription_media_path,
                    config_obj,
                    runtime_config.assembly_ai_http_timeout_seconds,
                )
                break
            except (httpx.TimeoutException, TimeoutError):
                logger.warning(
                    "AssemblyAI transcription timed out on attempt %s/3",
                    attempt,
                )
                if attempt == 3:
                    raise

        if transcript is None:
            raise RuntimeError("AssemblyAI transcription did not return a transcript")

        if transcript.status == aai.TranscriptStatus.error:
            logger.error(f"AssemblyAI transcription failed: {transcript.error}")
            raise Exception(f"Transcription failed: {transcript.error}")

        formatted_lines = format_transcript_for_analysis(transcript)

        # Cache the raw transcript for subtitle generation
        cache_transcript_data(video_path, transcript)

        result = "\n".join(formatted_lines)
        logger.info(
            f"Transcript formatted: {len(formatted_lines)} segments, {len(result)} chars"
        )
        return result

    except Exception as e:
        logger.error(f"Error in transcription: {e}")
        raise


def cache_transcript_data(video_path: Path, transcript) -> None:
    """Cache AssemblyAI transcript data for subtitle generation."""
    cache_path = video_path.with_suffix(".transcript_cache.json")

    words_data = []
    if transcript.words:
        words_data = [_serialize_transcript_word(word) for word in transcript.words]

    utterances_data = []
    if getattr(transcript, "utterances", None):
        utterances_data = [
            {
                "text": utterance.text,
                "start": utterance.start,
                "end": utterance.end,
                "speaker": getattr(utterance, "speaker", None),
                "words": [
                    _serialize_transcript_word(word)
                    for word in getattr(utterance, "words", []) or []
                ],
            }
            for utterance in transcript.utterances
        ]

    cache_data = {
        "version": TRANSCRIPT_CACHE_SCHEMA_VERSION,
        "words": words_data,
        "utterances": utterances_data,
        "text": transcript.text,
    }

    with open(cache_path, "w") as f:
        json.dump(cache_data, f)

    logger.info(f"Cached {len(words_data)} words to {cache_path}")


def load_cached_transcript_data(video_path: Path) -> Optional[Dict]:
    """Load cached AssemblyAI transcript data."""
    cache_path = video_path.with_suffix(".transcript_cache.json")

    if not cache_path.exists():
        return None

    try:
        with open(cache_path, "r") as f:
            payload = json.load(f)
            if "version" not in payload:
                payload["version"] = TRANSCRIPT_CACHE_SCHEMA_VERSION
                payload.setdefault("utterances", [])
            return payload
    except Exception as e:
        logger.warning(f"Failed to load transcript cache: {e}")
        return None


def _serialize_transcript_word(word) -> Dict[str, Any]:
    return {
        "text": word.text,
        "start": word.start,
        "end": word.end,
        "confidence": word.confidence if hasattr(word, "confidence") else 1.0,
        "speaker": getattr(word, "speaker", None),
    }


def format_transcript_for_analysis(transcript) -> List[str]:
    """Format transcripts into readable timestamped segments for AI analysis."""
    utterances = getattr(transcript, "utterances", None) or []
    if utterances:
        formatted_lines = []
        for utterance in utterances:
            start_time = format_ms_to_timestamp(utterance.start)
            end_time = format_ms_to_timestamp(utterance.end)
            speaker = getattr(utterance, "speaker", None)
            speaker_prefix = f"Speaker {speaker}: " if speaker else ""
            formatted_lines.append(
                f"[{start_time} - {end_time}] {speaker_prefix}{utterance.text}"
            )
        return formatted_lines

    formatted_lines = []
    words = getattr(transcript, "words", None) or []
    if not words:
        return formatted_lines

    logger.info(f"Processing {len(words)} words with precise timing")

    current_segment = []
    current_start = None
    segment_word_count = 0
    max_words_per_segment = 8

    for word in words:
        if current_start is None:
            current_start = word.start

        current_segment.append(word.text)
        segment_word_count += 1

        if (
            segment_word_count >= max_words_per_segment
            or word.text.endswith(".")
            or word.text.endswith("!")
            or word.text.endswith("?")
        ):
            if current_segment:
                start_time = format_ms_to_timestamp(current_start)
                end_time = format_ms_to_timestamp(word.end)
                text = " ".join(current_segment)
                formatted_lines.append(f"[{start_time} - {end_time}] {text}")

            current_segment = []
            current_start = None
            segment_word_count = 0

    if current_segment and current_start is not None:
        start_time = format_ms_to_timestamp(current_start)
        end_time = format_ms_to_timestamp(words[-1].end)
        text = " ".join(current_segment)
        formatted_lines.append(f"[{start_time} - {end_time}] {text}")

    return formatted_lines


def format_ms_to_timestamp(ms: int) -> str:
    """Format milliseconds to MM:SS format."""
    seconds = ms // 1000
    minutes = seconds // 60
    seconds = seconds % 60
    return f"{minutes:02d}:{seconds:02d}"


def round_to_even(value: int) -> int:
    """Round integer to nearest even number for H.264 compatibility."""
    return value - (value % 2)


def clamp_even(value: int, minimum: int, maximum: int) -> int:
    """Clamp an integer to an even value within inclusive bounds."""
    if maximum < minimum:
        return round_to_even(minimum)
    return round_to_even(max(minimum, min(value, maximum)))


def get_scaled_font_size(base_font_size: int, video_width: int) -> int:
    """Scale caption font size by output width with punchy, sensible bounds.

    Tuned so even the small UI default (24) renders as a bold, readable caption
    on a 1080-wide vertical clip, matching the larger short-form caption sizing
    used by tools like OpusClip.
    """
    scaled_size = int(base_font_size * (video_width / 560.0))
    return max(42, min(82, scaled_size))


def get_subtitle_max_width(video_width: int) -> int:
    """Return max subtitle text width with horizontal safe margins."""
    horizontal_padding = max(40, int(video_width * 0.06))
    return max(200, video_width - (horizontal_padding * 2))


def get_safe_vertical_position(
    video_height: int, text_height: int, position_y: float
) -> int:
    """Return subtitle y position clamped inside a top/bottom safe area."""
    min_top_padding = max(40, int(video_height * 0.05))
    min_bottom_padding = max(120, int(video_height * 0.10))

    desired_y = int(video_height * position_y - text_height // 2)
    max_y = video_height - min_bottom_padding - text_height
    return max(min_top_padding, min(desired_y, max_y))


def detect_optimal_crop_region(
    video_path: Path,
    start_time: float,
    end_time: float,
    target_ratio: float = 9 / 16,
) -> Tuple[int, int, int, int]:
    """Detect optimal crop region using improved face detection."""
    try:
        original_width, original_height = ffprobe_video_size(video_path)

        # Calculate target dimensions and ensure they're even
        if original_width / original_height > target_ratio:
            new_width = round_to_even(int(original_height * target_ratio))
            new_height = round_to_even(original_height)
        else:
            new_width = round_to_even(original_width)
            new_height = round_to_even(int(original_width / target_ratio))

        # Try improved face detection
        face_centers = detect_faces_in_clip(video_path, start_time, end_time)

        # Calculate crop position
        if face_centers:
            # Use weighted average of face centers with temporal consistency
            total_weight = sum(
                area * confidence for _, _, area, confidence in face_centers
            )
            if total_weight > 0:
                weighted_x = (
                    sum(
                        x * area * confidence for x, y, area, confidence in face_centers
                    )
                    / total_weight
                )
                weighted_y = (
                    sum(
                        y * area * confidence for x, y, area, confidence in face_centers
                    )
                    / total_weight
                )

                # Add slight bias towards upper portion for better face framing
                weighted_y = max(0, weighted_y - new_height * 0.1)

                x_offset = max(
                    0, min(int(weighted_x - new_width // 2), original_width - new_width)
                )
                y_offset = max(
                    0,
                    min(
                        int(weighted_y - new_height // 2), original_height - new_height
                    ),
                )

                logger.info(
                    f"Face-centered crop: {len(face_centers)} faces detected with improved algorithm"
                )
            else:
                # Center crop
                x_offset = (
                    (original_width - new_width) // 2
                    if original_width > new_width
                    else 0
                )
                y_offset = (
                    (original_height - new_height) // 2
                    if original_height > new_height
                    else 0
                )
        else:
            # Center crop
            x_offset = (
                (original_width - new_width) // 2 if original_width > new_width else 0
            )
            y_offset = (
                (original_height - new_height) // 2
                if original_height > new_height
                else 0
            )
            logger.info("Using center crop (no faces detected)")

        # Ensure offsets are even too
        x_offset = round_to_even(x_offset)
        y_offset = round_to_even(y_offset)

        logger.info(
            f"Crop dimensions: {new_width}x{new_height} at offset ({x_offset}, {y_offset})"
        )
        return (x_offset, y_offset, new_width, new_height)

    except Exception as e:
        logger.error(f"Error in crop detection: {e}")
        # Fallback to center crop
        original_width, original_height = ffprobe_video_size(video_path)
        if original_width / original_height > target_ratio:
            new_width = round_to_even(int(original_height * target_ratio))
            new_height = round_to_even(original_height)
        else:
            new_width = round_to_even(original_width)
            new_height = round_to_even(int(original_width / target_ratio))

        x_offset = (
            round_to_even((original_width - new_width) // 2)
            if original_width > new_width
            else 0
        )
        y_offset = (
            round_to_even((original_height - new_height) // 2)
            if original_height > new_height
            else 0
        )

        return (x_offset, y_offset, new_width, new_height)


def detect_faces_in_clip(
    video_path: Path, start_time: float, end_time: float
) -> List[Tuple[int, int, int, float]]:
    """
    Improved face detection using multiple methods and temporal consistency.
    Returns list of (x, y, area, confidence) tuples.
    """
    face_centers = []

    try:
        # Try to use MediaPipe (most accurate)
        mp_face_detection = None
        try:
            import mediapipe as mp

            mp_face_detection = mp.solutions.face_detection.FaceDetection(
                model_selection=0,  # 0 for short-range (better for close faces)
                min_detection_confidence=0.5,
            )
            logger.info("Using MediaPipe face detector")
        except ImportError:
            logger.info("MediaPipe not available, falling back to OpenCV")
        except Exception as e:
            logger.warning(f"MediaPipe face detector failed to initialize: {e}")

        # Initialize OpenCV face detectors as fallback
        haar_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

        # Try to load DNN face detector (more accurate than Haar)
        dnn_net = None
        try:
            # Load OpenCV's DNN face detector
            prototxt_path = cv2.data.haarcascades.replace(
                "haarcascades", "opencv_face_detector.pbtxt"
            )
            model_path = cv2.data.haarcascades.replace(
                "haarcascades", "opencv_face_detector_uint8.pb"
            )

            # If DNN model files don't exist, we'll fall back to Haar cascade
            import os

            if os.path.exists(prototxt_path) and os.path.exists(model_path):
                dnn_net = cv2.dnn.readNetFromTensorflow(model_path, prototxt_path)
                logger.info("OpenCV DNN face detector loaded as backup")
            else:
                logger.info("OpenCV DNN face detector not available")
        except Exception:
            logger.info("OpenCV DNN face detector failed to load")

        # Sample more frames for better face detection (every 0.5 seconds)
        duration = end_time - start_time
        sample_interval = min(0.5, duration / 10)  # At least 10 samples, max every 0.5s
        sample_times = []

        current_time = start_time
        while current_time < end_time:
            sample_times.append(current_time)
            current_time += sample_interval

        # Ensure we always sample the middle and end
        if duration > 1.0:
            middle_time = start_time + duration / 2
            if middle_time not in sample_times:
                sample_times.append(middle_time)

        sample_times = [t for t in sample_times if t < end_time]
        logger.info(f"Sampling {len(sample_times)} frames for face detection")

        capture = cv2.VideoCapture(str(video_path))
        if not capture.isOpened():
            logger.warning("Unable to open video for face detection: %s", video_path)
            return []

        for sample_time in sample_times:
            try:
                capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, sample_time) * 1000.0)
                ok, frame_bgr = capture.read()
                if not ok or frame_bgr is None:
                    continue
                frame = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
                height, width = frame.shape[:2]
                detected_faces = []

                # Try MediaPipe first (most accurate)
                if mp_face_detection is not None:
                    try:
                        # MediaPipe expects RGB format
                        results = mp_face_detection.process(frame)

                        if results.detections:
                            for detection in results.detections:
                                bbox = detection.location_data.relative_bounding_box
                                confidence = detection.score[0]

                                # Convert relative coordinates to absolute
                                x = int(bbox.xmin * width)
                                y = int(bbox.ymin * height)
                                w = int(bbox.width * width)
                                h = int(bbox.height * height)

                                if w > 30 and h > 30:  # Minimum face size
                                    detected_faces.append((x, y, w, h, confidence))
                    except Exception as e:
                        logger.warning(
                            f"MediaPipe detection failed for frame at {sample_time}s: {e}"
                        )

                # If MediaPipe didn't find faces, try DNN detector
                if not detected_faces and dnn_net is not None:
                    try:
                        blob = cv2.dnn.blobFromImage(
                            frame_bgr, 1.0, (300, 300), [104, 117, 123]
                        )
                        dnn_net.setInput(blob)
                        detections = dnn_net.forward()

                        for i in range(detections.shape[2]):
                            confidence = detections[0, 0, i, 2]
                            if confidence > 0.5:  # Confidence threshold
                                x1 = int(detections[0, 0, i, 3] * width)
                                y1 = int(detections[0, 0, i, 4] * height)
                                x2 = int(detections[0, 0, i, 5] * width)
                                y2 = int(detections[0, 0, i, 6] * height)

                                w = x2 - x1
                                h = y2 - y1

                                if w > 30 and h > 30:  # Minimum face size
                                    detected_faces.append((x1, y1, w, h, confidence))
                    except Exception as e:
                        logger.warning(
                            f"DNN detection failed for frame at {sample_time}s: {e}"
                        )

                # If still no faces found, use Haar cascade
                if not detected_faces:
                    try:
                        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

                        faces = haar_cascade.detectMultiScale(
                            gray,
                            scaleFactor=1.05,  # More sensitive
                            minNeighbors=3,  # Less strict
                            minSize=(40, 40),  # Smaller minimum size
                            maxSize=(
                                int(width * 0.7),
                                int(height * 0.7),
                            ),  # Maximum size limit
                        )

                        for x, y, w, h in faces:
                            # Estimate confidence based on face size and position
                            face_area = w * h
                            relative_size = face_area / (width * height)
                            confidence = min(
                                0.9, 0.3 + relative_size * 2
                            )  # Rough confidence estimate
                            detected_faces.append((x, y, w, h, confidence))
                    except Exception as e:
                        logger.warning(
                            f"Haar cascade detection failed for frame at {sample_time}s: {e}"
                        )

                # Process detected faces
                for x, y, w, h, confidence in detected_faces:
                    face_center_x = x + w // 2
                    face_center_y = y + h // 2
                    face_area = w * h

                    # Filter out very small or very large faces
                    frame_area = width * height
                    relative_area = face_area / frame_area

                    if (
                        0.005 < relative_area < 0.3
                    ):  # Face should be 0.5% to 30% of frame
                        face_centers.append(
                            (face_center_x, face_center_y, face_area, confidence)
                        )

            except Exception as e:
                logger.warning(f"Error detecting faces in frame at {sample_time}s: {e}")
                continue

        capture.release()

        # Close MediaPipe detector
        if mp_face_detection is not None:
            mp_face_detection.close()

        # Remove outliers (faces that are very far from the median position)
        if len(face_centers) > 2:
            face_centers = filter_face_outliers(face_centers)

        logger.info(f"Detected {len(face_centers)} reliable face centers")
        return face_centers

    except Exception as e:
        logger.error(f"Error in face detection: {e}")
        return []


def filter_face_outliers(
    face_centers: List[Tuple[int, int, int, float]],
) -> List[Tuple[int, int, int, float]]:
    """Remove face detections that are outliers (likely false positives)."""
    if len(face_centers) < 3:
        return face_centers

    try:
        # Calculate median position
        x_positions = [x for x, y, area, conf in face_centers]
        y_positions = [y for x, y, area, conf in face_centers]

        median_x = np.median(x_positions)
        median_y = np.median(y_positions)

        # Calculate standard deviation
        std_x = np.std(x_positions)
        std_y = np.std(y_positions)

        # Filter out faces that are more than 2 standard deviations away
        filtered_faces = []
        for face in face_centers:
            x, y, area, conf = face
            if abs(x - median_x) <= 2 * std_x and abs(y - median_y) <= 2 * std_y:
                filtered_faces.append(face)

        logger.info(
            f"Filtered {len(face_centers)} -> {len(filtered_faces)} faces (removed outliers)"
        )
        return (
            filtered_faces if filtered_faces else face_centers
        )  # Return original if all filtered

    except Exception as e:
        logger.warning(f"Error filtering face outliers: {e}")
        return face_centers


def run_ffmpeg_command(command: List[str], timeout: int = 900) -> subprocess.CompletedProcess:
    """Run ffmpeg/ffprobe and log stderr on failure."""
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        logger.error("Command failed: %s\n%s", " ".join(command), result.stderr[-4000:])
    return result


def ffprobe_has_audio(video_path: Path) -> bool:
    result = run_ffmpeg_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_type",
            "-of",
            "csv=p=0",
            str(video_path),
        ],
        timeout=60,
    )
    return result.returncode == 0 and "audio" in result.stdout


def ffprobe_video_size(video_path: Path) -> Tuple[int, int]:
    result = run_ffmpeg_command(
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
            str(video_path),
        ],
        timeout=60,
    )
    if result.returncode != 0 or "x" not in result.stdout:
        raise RuntimeError(f"Unable to read video size for {video_path}")
    width, height = result.stdout.strip().split("x", 1)
    return int(width), int(height)


def ffprobe_duration(video_path: Path) -> float:
    result = run_ffmpeg_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(video_path),
        ],
        timeout=60,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Unable to read duration for {video_path}")
    try:
        return max(0.0, float(result.stdout.strip()))
    except ValueError as exc:
        raise RuntimeError(f"Invalid duration for {video_path}") from exc


def ffmpeg_escape_filter_path(path: Path) -> str:
    """Escape a path for use inside an ffmpeg filter argument."""
    return (
        str(path)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(" ", "\\ ")
    )


def ffmpeg_escape_filter_value(value: str) -> str:
    """Escape an ffmpeg filter option value."""
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace(" ", "\\ ")
    )


# --- shared encode quality profile -----------------------------------------
# The final render pass determines output quality. We keep a single, slightly
# higher-quality intermediate so the binding constraint is always this profile.
FINAL_VIDEO_CRF = 19
FINAL_VIDEO_PRESET = "medium"
INTERMEDIATE_CRF = 16
OUTPUT_FPS = 30
AUDIO_BITRATE = "192k"
# Normalise perceived loudness to the short-form social target (~ -14 LUFS).
LOUDNORM_FILTER = "loudnorm=I=-14:TP=-1.5:LRA=11"


def build_final_video_encode_args(
    crf: int = FINAL_VIDEO_CRF,
    preset: str = FINAL_VIDEO_PRESET,
    fps: int = OUTPUT_FPS,
) -> List[str]:
    """libx264 args for the quality-determining final pass (CFR, H.264 High)."""
    return [
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-profile:v", "high",
        "-level", "4.1",
        "-r", str(fps),
        "-x264-params", "keyint=120:min-keyint=30:scenecut=40",
    ]


def build_audio_output_args(has_audio: bool, loudnorm: bool = True) -> List[str]:
    """Audio encode args (with optional loudness normalisation) or `-an`."""
    if not has_audio:
        return ["-an"]
    args: List[str] = []
    if loudnorm:
        args += ["-af", LOUDNORM_FILTER]
    args += ["-c:a", "aac", "-b:a", AUDIO_BITRATE, "-ar", "48000"]
    return args


def subtitles_filter_fragment(
    ass_path: Path, fonts_dir: Optional[Path] = None
) -> str:
    """ffmpeg `subtitles` filter fragment burning an ASS file (with fonts dir)."""
    fragment = f"subtitles=filename={ffmpeg_escape_filter_path(ass_path)}"
    if fonts_dir:
        fragment += f":fontsdir={ffmpeg_escape_filter_value(str(fonts_dir))}"
    return fragment


_EMOJI_SUPPORT_CACHE: Optional[bool] = None


def emoji_rendering_supported() -> bool:
    """Whether this environment's libass renders COLOUR emojis (cached, one-shot).

    Caption emojis are only injected when this returns True, so we never burn
    ugly ".notdef" tofu boxes if the runtime's libass/FreeType can't rasterise
    the bundled colour-emoji font. The captions still get keyword emphasis either
    way. The probe burns a single emoji and checks the frame for saturated colour.
    """
    global _EMOJI_SUPPORT_CACHE
    if _EMOJI_SUPPORT_CACHE is not None:
        return _EMOJI_SUPPORT_CACHE

    result = False
    try:
        with tempfile.TemporaryDirectory(prefix="supoclip_emojiprobe_") as probe_dir:
            root = Path(probe_dir)
            ass = root / "probe.ass"
            frame = root / "probe.png"
            ass.write_text(
                "[Script Info]\n"
                "ScriptType: v4.00+\nPlayResX: 120\nPlayResY: 120\n\n"
                "[V4+ Styles]\n"
                "Format: Name, Fontname, Fontsize, PrimaryColour, OutlineColour, "
                "BackColour, Bold, BorderStyle, Outline, Shadow, Alignment, Encoding\n"
                f"Style: D,{EMOJI_FONT_NAME},90,&H00FFFFFF,&H00000000,&H00000000,0,1,0,0,5,1\n\n"
                "[Events]\n"
                "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, "
                "Effect, Text\n"
                "Dialogue: 0,0:00:00.00,0:00:01.00,D,,0,0,0,,"
                "{\\pos(60,60)}\U0001F525\n",
                encoding="utf-8",
            )
            fonts = FONTS_DIR if FONTS_DIR.exists() else None
            fragment = subtitles_filter_fragment(ass, fonts)
            command = [
                "ffmpeg", "-y",
                "-f", "lavfi", "-i", "color=c=black:s=120x120:d=1",
                "-vf", fragment,
                "-frames:v", "1",
                str(frame),
            ]
            if run_ffmpeg_command(command, timeout=60).returncode == 0 and frame.exists():
                from PIL import Image

                arr = np.asarray(Image.open(frame).convert("RGB"), dtype=np.int16)
                spread = arr.max(axis=2) - arr.min(axis=2)  # 0 for grey/tofu
                result = int((spread > 40).sum()) > 30
    except Exception as exc:
        logger.info("Emoji support probe failed (%s); disabling caption emojis", exc)
        result = False

    _EMOJI_SUPPORT_CACHE = result
    logger.info("Caption colour-emoji rendering supported: %s", result)
    return result


def crossfade_fade_for_ranges(keep_ranges: List[Tuple[float, float]]) -> float:
    """Crossfade duration render_source_ranges will use, or 0.0 for hard concat.

    A single source of truth so caption timing (which compacts the same ranges)
    stays perfectly in sync with the crossfade-shortened video timeline.
    """
    ranges = normalize_source_ranges(keep_ranges)
    if len(ranges) < 2 or len(ranges) > 8:
        return 0.0
    durations = [end - start for start, end in ranges]
    if min(durations) < 0.45:
        return 0.0
    fade = min(0.22, min(durations) * 0.5)
    return fade if fade >= 0.06 else 0.0


def render_ranges_crossfade_ffmpeg(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    output_path: Path,
    has_audio: bool,
    transition: str = "fade",
) -> bool:
    """Stitch kept ranges together with short crossfades instead of hard cuts.

    Turns the abrupt jump cuts left by pause/filler removal into quick, smooth
    dissolves (video xfade + audio acrossfade), which read as intentional,
    polished transitions.
    """
    keep_ranges = normalize_source_ranges(keep_ranges)
    n = len(keep_ranges)
    if n < 2:
        return False
    durations = [end - start for start, end in keep_ranges]
    fade = crossfade_fade_for_ranges(keep_ranges)
    if fade <= 0:
        return False

    parts: List[str] = []
    for idx, (start, end) in enumerate(keep_ranges):
        parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS,"
            f"fps={OUTPUT_FPS},format=yuv420p,setsar=1[v{idx}]"
        )
        if has_audio:
            parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{idx}]"
            )

    cur_v = "[v0]"
    cumulative = durations[0]
    for i in range(1, n):
        offset = cumulative - fade
        out = f"[vx{i}]"
        parts.append(
            f"{cur_v}[v{i}]xfade=transition={transition}:duration={fade:.3f}:"
            f"offset={offset:.3f}{out}"
        )
        cumulative = cumulative + durations[i] - fade
        cur_v = out

    map_args = ["-map", cur_v]
    if has_audio:
        cur_a = "[a0]"
        for i in range(1, n):
            out = f"[ax{i}]"
            parts.append(f"{cur_a}[a{i}]acrossfade=d={fade:.3f}{out}")
            cur_a = out
        map_args += ["-map", cur_a]

    command = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-filter_complex", ";".join(parts),
        *map_args,
        "-c:v", "libx264", "-preset", "veryfast", "-crf", str(INTERMEDIATE_CRF),
        "-pix_fmt", "yuv420p",
    ]
    if has_audio:
        command += ["-c:a", "aac", "-b:a", "192k"]
    command += ["-movflags", "+faststart", str(output_path)]
    return run_ffmpeg_command(command, timeout=1800).returncode == 0


def render_source_ranges_ffmpeg(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    output_path: Path,
) -> bool:
    """Render source ranges into one intermediate clip using ffmpeg only."""
    keep_ranges = normalize_source_ranges(keep_ranges)
    if not keep_ranges:
        return False

    if len(keep_ranges) == 1:
        start, end = keep_ranges[0]
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{start:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{end - start:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            str(INTERMEDIATE_CRF),
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        return run_ffmpeg_command(command).returncode == 0

    has_audio = ffprobe_has_audio(video_path)

    # Smooth a handful of substantial internal cuts with crossfades; fall back to
    # a hard concat for many tiny fragments (heavy filler edits) or on failure.
    if crossfade_fade_for_ranges(keep_ranges) > 0:
        if render_ranges_crossfade_ffmpeg(
            video_path, keep_ranges, output_path, has_audio
        ):
            return True
        logger.info("Crossfade stitch failed; falling back to hard concat")

    filter_parts: List[str] = []
    concat_inputs: List[str] = []
    for idx, (start, end) in enumerate(keep_ranges):
        filter_parts.append(
            f"[0:v]trim=start={start:.3f}:end={end:.3f},setpts=PTS-STARTPTS[v{idx}]"
        )
        concat_inputs.append(f"[v{idx}]")
        if has_audio:
            filter_parts.append(
                f"[0:a]atrim=start={start:.3f}:end={end:.3f},asetpts=PTS-STARTPTS[a{idx}]"
            )
            concat_inputs.append(f"[a{idx}]")

    if has_audio:
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={len(keep_ranges)}:v=1:a=1[v][a]"
        )
        map_args = ["-map", "[v]", "-map", "[a]"]
    else:
        filter_parts.append(
            f"{''.join(concat_inputs)}concat=n={len(keep_ranges)}:v=1:a=0[v]"
        )
        map_args = ["-map", "[v]"]

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-filter_complex",
        ";".join(filter_parts),
        *map_args,
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        str(INTERMEDIATE_CRF),
        "-pix_fmt",
        "yuv420p",
    ]
    if has_audio:
        command.extend(["-c:a", "aac", "-b:a", "192k"])
    command.extend(["-movflags", "+faststart", str(output_path)])
    return run_ffmpeg_command(command, timeout=1800).returncode == 0


def ass_timestamp(seconds: float) -> str:
    seconds = max(0.0, seconds)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds - (hours * 3600) - (minutes * 60)
    return f"{hours}:{minutes:02d}:{secs:05.2f}"


def hex_to_ass_color(
    value: Optional[str], fallback: str = "#FFFFFF", include_alpha: bool = True
) -> str:
    value = (value or fallback).strip()
    if value.startswith("#"):
        value = value[1:]
    alpha = 0
    if len(value) == 8:
        css_alpha = int(value[6:8], 16)
        alpha = 255 - css_alpha
        value = value[:6]
    if len(value) != 6:
        value = fallback.lstrip("#")
        if len(value) == 8:
            css_alpha = int(value[6:8], 16)
            alpha = 255 - css_alpha
            value = value[:6]
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha_part = f"{alpha:02X}" if include_alpha else "00"
    return f"&H{alpha_part}{blue}{green}{red}&"


def escape_ass_text(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", " ")
        .strip()
    )


def ass_font_name(font_family: str) -> str:
    font_path = find_font_path(font_family, allow_all_user_fonts=True)
    if font_path:
        return get_font_family_name(Path(font_path)) or Path(font_path).stem
    return font_family or "Arial"


def ass_fonts_dir(font_family: str) -> Optional[Path]:
    font_path = find_font_path(font_family, allow_all_user_fonts=True)
    if font_path:
        return font_path.parent
    return FONTS_DIR if FONTS_DIR.exists() else None


def word_ends_sentence(text: str) -> bool:
    return bool(SENTENCE_END_RE.search((text or "").strip()))


def extend_keep_ranges_to_sentence_boundary(
    video_path: Path,
    keep_ranges: List[Tuple[float, float]],
    max_extension_seconds: float = CLIP_END_SENTENCE_EXTENSION_SECONDS,
    padding_seconds: float = CLIP_END_PADDING_SECONDS,
) -> List[Tuple[float, float]]:
    """Extend the final source range when a clip end lands mid-sentence."""
    normalized = normalize_source_ranges(keep_ranges)
    if not normalized:
        return []

    last_start, last_end = normalized[-1]
    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data or not transcript_data.get("words"):
        return normalized

    try:
        source_duration = ffprobe_duration(video_path)
    except Exception:
        source_duration = None

    cap_end = last_end + max(0.0, max_extension_seconds)
    if source_duration is not None:
        cap_end = min(cap_end, source_duration)
    if cap_end <= last_end:
        return normalized

    nearby_words = get_absolute_words_in_range(
        transcript_data,
        max(0.0, last_end - 6.0),
        cap_end,
    )
    if not nearby_words:
        return normalized

    boundary_words = [
        word for word in nearby_words if float(word["start"]) <= last_end + 0.05
    ]
    last_boundary_word = boundary_words[-1] if boundary_words else None
    if (
        last_boundary_word
        and float(last_boundary_word["end"]) <= last_end + 0.05
        and word_ends_sentence(str(last_boundary_word.get("text", "")))
    ):
        return normalized

    extended_end = last_end
    for word in nearby_words:
        word_end = float(word["end"])
        if word_end <= last_end + 0.05:
            continue
        extended_end = max(extended_end, word_end)
        if word_ends_sentence(str(word.get("text", ""))):
            extended_end += max(0.0, padding_seconds)
            break

    if extended_end <= last_end:
        return normalized
    if source_duration is not None:
        extended_end = min(extended_end, source_duration)
    extended_end = min(extended_end, cap_end + max(0.0, padding_seconds))

    if extended_end - last_start <= 0.05:
        return normalized

    return [*normalized[:-1], (last_start, extended_end)]


def build_assemblyai_ass_subtitles(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    video_width: int,
    video_height: int,
    output_ass_path: Path,
    font_family: str = "THEBOLDFONT",
    font_size: int = 24,
    font_color: str = "#FFFFFF",
    caption_template: str = "default",
    keep_ranges: Optional[List[Tuple[float, float]]] = None,
    caption_cues: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    """Generate animated word-synced ASS subtitles from cached AssemblyAI words.

    Renders OpusClip-style captions: a per-word active highlight that pops, an
    accent colour on emphasised power/keyword words, contextual emojis, a thick
    scaled outline + drop shadow, and an optional pill behind the active word.
    """
    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data or not transcript_data.get("words"):
        logger.warning("No cached transcript data available for ASS subtitles")
        return False

    template = get_template(caption_template)
    effective_font_family = font_family or template["font_family"]
    effective_font_size = int(font_size) if font_size else int(template["font_size"])
    effective_font_color = font_color or template["font_color"]
    animation = template.get("animation", "karaoke")

    if keep_ranges:
        relevant_words = get_words_for_keep_ranges(transcript_data, keep_ranges)
    else:
        relevant_words = get_words_in_range(transcript_data, clip_start, clip_end)
    if not relevant_words:
        logger.warning("No words found in clip timerange for ASS subtitles")
        return False

    # --- styling knobs (new template fields, all optional) ---
    uppercase = bool(template.get("uppercase"))
    # Only inject emojis when the runtime can actually render them in colour.
    enable_emoji = bool(template.get("emoji", True)) and emoji_rendering_supported()
    word_pop = bool(template.get("word_pop", True))
    word_box = bool(template.get("word_box"))
    glow = bool(template.get("glow"))
    has_outline = template.get("stroke_color") is not None
    # Emphasis colouring only makes sense when something distinguishes words.
    enable_emphasis = animation != "none"

    primary = hex_to_ass_color(effective_font_color)
    highlight = hex_to_ass_color(template.get("highlight_color"), "#FFE000")
    emphasis_color = hex_to_ass_color(
        template.get("emphasis_color") or template.get("highlight_color"), "#FFE000"
    )
    outline = hex_to_ass_color(template.get("stroke_color") or "#000000", "#000000")
    back_color = hex_to_ass_color(template.get("background_color"), "#00000080")
    box_color = hex_to_ass_color(
        template.get("word_box_color") or template.get("highlight_color"), "#00BF49"
    )

    font_px = get_scaled_font_size(effective_font_size, video_width)
    base_stroke = int(template.get("stroke_width", 3) or 0)
    # Scale the outline with the font so big captions keep a chunky, readable edge.
    outline_px = max(base_stroke, round(font_px * base_stroke / 26)) if (has_outline and base_stroke) else 0
    shadow_px = max(2, font_px // 20) if template.get("shadow") else 0
    box_bord = max(outline_px + 2, font_px // 5)
    pos_y = float(template.get("position_y", 0.80))
    est_text_height = int(font_px * 1.5)
    y_pos = get_safe_vertical_position(video_height, est_text_height, pos_y)
    font_name = ass_font_name(effective_font_family)
    border_style = 3 if template.get("background") and template.get("background_color") else 1

    # Contextual emoji + emphasis annotations over the whole clip word list.
    emoji_by_idx, emphasis_idx = annotate_caption_words(
        relevant_words,
        caption_cues,
        enable_emoji=enable_emoji,
        enable_emphasis=enable_emphasis,
    )

    max_words = max(1, int(template.get("max_words_per_line", 4) or 4))
    chunk_size = max_words

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {video_width}
PlayResY: {video_height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_px},{primary},&H000000FF,{outline},{back_color},1,0,0,0,100,100,0,0,{border_style},{outline_px},{shadow_px},5,60,60,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    line_prefix = f"{{\\pos({video_width // 2},{y_pos})" + ("\\blur4" if glow else "") + "}"

    # Every word span re-declares the caption font, so an emoji's \fn override
    # can never leak into the following word.
    font_tag = f"\\fn{font_name}"

    def render_text(global_idx: int, word: Dict[str, Any]) -> str:
        text = str(word.get("text", ""))
        if uppercase:
            text = text.upper()
        disp = escape_ass_text(text)
        emoji = emoji_by_idx.get(global_idx)
        if emoji:
            # Force the colour-emoji font for the glyph; the next word's span
            # re-declares the caption font, so no explicit restore is needed.
            disp = f"{disp} {{\\fn{EMOJI_FONT_NAME}}}{emoji}"
        return disp

    # The active word is distinguished by COLOUR only (and an optional box). We
    # deliberately do NOT scale individual words: scaling a word changes its
    # advance width, which reflows the centre-anchored line and makes the whole
    # caption visibly vibrate as each word pops. The "pop" lives as a one-shot
    # line entrance instead (see below).
    def active_span(disp: str) -> str:
        tags = f"{font_tag}\\c{highlight}"
        if word_box:
            tags += f"\\3c{box_color}\\bord{box_bord}\\shad0"
        return f"{{{tags}}}{disp}"

    def idle_span(global_idx: int, disp: str) -> str:
        color = emphasis_color if (enable_emphasis and global_idx in emphasis_idx) else primary
        tags = f"{font_tag}\\c{color}"
        if word_box:
            tags += f"\\3c{outline}\\bord{outline_px}\\shad{shadow_px}"
        return f"{{{tags}}}{disp}"

    # Subtle one-shot entrance for the whole line (uniform scale, centred), shown
    # only as the first word of a chunk appears — gives a pop without any
    # per-word reflow/vibration.
    line_entrance = "\\fscx92\\fscy92\\t(0,140,\\fscx100\\fscy100)" if word_pop else ""

    events: List[str] = []
    total = len(relevant_words)
    for chunk_start in range(0, total, chunk_size):
        chunk = relevant_words[chunk_start : chunk_start + chunk_size]
        indices = list(range(chunk_start, chunk_start + len(chunk)))
        chunk_end = float(chunk[-1]["end"])

        if animation == "karaoke":
            for local_i, word in enumerate(chunk):
                start = float(word["start"])
                end = (
                    float(chunk[local_i + 1]["start"])
                    if local_i + 1 < len(chunk)
                    else chunk_end
                )
                if end <= start:
                    end = start + 0.05
                parts = []
                for local_j, other in enumerate(chunk):
                    gj = indices[local_j]
                    disp = render_text(gj, other)
                    parts.append(active_span(disp) if local_j == local_i else idle_span(gj, disp))
                line = " ".join(parts)
                # Entrance only on the first word's event so it plays once, not
                # once per word.
                entrance = f"{{{line_entrance}}}" if (line_entrance and local_i == 0) else ""
                events.append(
                    f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{line_prefix}{entrance}{line}"
                )
        else:
            start = float(chunk[0]["start"])
            end = chunk_end
            if end <= start:
                end = start + 0.05
            spans = []
            for local_j, word in enumerate(chunk):
                gj = indices[local_j]
                disp = render_text(gj, word)
                color = emphasis_color if (enable_emphasis and gj in emphasis_idx) else primary
                spans.append(f"{{{font_tag}\\c{color}}}{disp}")
            chunk_text = " ".join(spans)

            effect = ""
            if animation == "fade":
                effect = "{\\fad(120,120)}"
            elif animation == "pop":
                effect = (
                    "{\\fscx88\\fscy88\\t(0,130,\\fscx106\\fscy106)"
                    "\\t(130,250,\\fscx100\\fscy100)}"
                )
            elif animation == "bounce":
                effect = (
                    "{\\fscx70\\fscy70\\t(0,120,\\fscx112\\fscy112)"
                    "\\t(120,240,\\fscx100\\fscy100)}"
                )
            events.append(
                f"Dialogue: 0,{ass_timestamp(start)},{ass_timestamp(end)},Default,,0,0,0,,{line_prefix}{effect}{chunk_text}"
            )

    output_ass_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    logger.info("Wrote ASS subtitles: %s (%d events)", output_ass_path, len(events))
    return True


def count_scene_cuts(video_path: Path, threshold: float = 0.35) -> int:
    """Count likely scene cuts in a clip using ffmpeg's scene score."""
    result = run_ffmpeg_command(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-filter:v",
            f"select='gt(scene,{threshold})',showinfo",
            "-f",
            "null",
            "-",
        ],
        timeout=300,
    )
    if result.returncode != 0:
        return 0
    return len(re.findall(r"pts_time:", result.stderr))


def parse_motion_metadata(path: Path) -> Tuple[List[float], List[float]]:
    times: List[float] = []
    values: List[float] = []
    current_time: Optional[float] = None
    for line in path.read_text(errors="ignore").splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        value_match = re.search(r"lavfi\.signalstats\.YAVG=([0-9.]+)", line)
        if value_match and current_time is not None:
            times.append(current_time)
            values.append(float(value_match.group(1)))
            current_time = None
    return times, values


def smooth_values(values: List[float], window: int = 15) -> List[float]:
    if not values:
        return []
    smoothed: List[float] = []
    half = window // 2
    for idx in range(len(values)):
        start = max(0, idx - half)
        end = min(len(values), idx + half + 1)
        smoothed.append(sum(values[start:end]) / (end - start))
    return smoothed


def build_speaker_timeline_from_motion(
    times: List[float],
    left_values: List[float],
    right_values: List[float],
    min_duration: float = 1.0,
) -> List[Dict[str, Any]]:
    if not times or len(left_values) != len(right_values):
        return []

    def normalize(values: List[float]) -> List[float]:
        mean_value = sum(values) / max(len(values), 1)
        return [value / mean_value if mean_value > 0 else 0.0 for value in values]

    left = smooth_values(normalize(left_values))
    right = smooth_values(normalize(right_values))
    if not left or not right:
        return []

    margin = 1.15
    current = 0 if left[0] >= right[0] else 1
    speakers: List[int] = []
    for left_value, right_value in zip(left, right):
        if current == 0 and right_value > left_value * margin:
            current = 1
        elif current == 1 and left_value > right_value * margin:
            current = 0
        speakers.append(current)

    segments: List[Dict[str, Any]] = []
    idx = 0
    while idx < len(speakers):
        end_idx = idx
        while end_idx + 1 < len(speakers) and speakers[end_idx + 1] == speakers[idx]:
            end_idx += 1
        seg_start = times[idx]
        seg_end = times[min(end_idx + 1, len(times) - 1)]
        if seg_end <= seg_start:
            seg_end = seg_start + 0.05
        segments.append(
            {
                "start": seg_start,
                "end": seg_end,
                "speaker": "left" if speakers[idx] == 0 else "right",
            }
        )
        idx = end_idx + 1

    merged: List[Dict[str, Any]] = []
    for segment in segments:
        if merged and segment["end"] - segment["start"] < min_duration:
            merged[-1]["end"] = segment["end"]
            continue
        if merged and merged[-1]["speaker"] == segment["speaker"]:
            merged[-1]["end"] = segment["end"]
            continue
        merged.append(segment)
    return merged


def cluster_two_face_regions(
    face_centers: List[Tuple[int, int, int, float]],
    width: int,
    height: int,
) -> Optional[Dict[str, Dict[str, int]]]:
    """Approximate left/right face regions from sampled face centers."""
    if len(face_centers) < 2:
        return None

    median_x = float(np.median([face[0] for face in face_centers]))
    left_faces = [face for face in face_centers if face[0] <= median_x]
    right_faces = [face for face in face_centers if face[0] > median_x]
    if not left_faces or not right_faces:
        return None

    def region(faces: List[Tuple[int, int, int, float]]) -> Dict[str, int]:
        center_x = int(np.median([face[0] for face in faces]))
        center_y = int(np.median([face[1] for face in faces]))
        face_size = int(np.sqrt(max(1, float(np.median([face[2] for face in faces])))))
        roi_w = max(80, int(face_size * 1.4))
        roi_h = max(70, int(face_size * 0.9))
        roi_x = clamp_even(center_x - roi_w // 2, 0, max(0, width - roi_w))
        roi_y = clamp_even(center_y, 0, max(0, height - roi_h))
        tile_w = min(width, max(160, int(face_size * 2.8)))
        tile_h = min(height, max(160, int(face_size * 2.4)))
        tile_x = clamp_even(center_x - tile_w // 2, 0, max(0, width - tile_w))
        tile_y = clamp_even(center_y - int(tile_h * 0.42), 0, max(0, height - tile_h))
        return {
            "center_x": center_x,
            "center_y": center_y,
            "roi_x": roi_x,
            "roi_y": roi_y,
            "roi_w": round_to_even(min(roi_w, width - roi_x)),
            "roi_h": round_to_even(min(roi_h, height - roi_y)),
            "tile_x": tile_x,
            "tile_y": tile_y,
            "tile_w": round_to_even(min(tile_w, width - tile_x)),
            "tile_h": round_to_even(min(tile_h, height - tile_y)),
        }

    left = region(left_faces)
    right = region(right_faces)
    if abs(right["center_x"] - left["center_x"]) < width * 0.15:
        return None
    return {"left": left, "right": right}


def build_pan_expression(
    timeline: List[Dict[str, Any]], left_x: int, right_x: int, ramp: float = 0.45
) -> str:
    """Eased crop-x expression that glides between two speaker framings.

    Instead of snapping the crop instantly at each speaker change, this ramps
    smoothly over ``ramp`` seconds, giving a natural camera-pan feel.
    """
    if not timeline:
        return str(left_x)

    def x_for(speaker: str) -> int:
        return left_x if speaker == "left" else right_x

    keys: List[Tuple[float, float]] = [(0.0, float(x_for(timeline[0]["speaker"])))]
    for segment in timeline:
        switch_t = max(0.0, float(segment["start"]))
        target = float(x_for(segment["speaker"]))
        if abs(target - keys[-1][1]) < 1.0:
            continue
        keys.append((switch_t, keys[-1][1]))  # hold previous framing until switch
        keys.append((switch_t + ramp, target))  # then ease into the new framing

    cleaned: List[Tuple[float, int]] = []
    for t, x in keys:
        if cleaned and t <= cleaned[-1][0]:
            t = cleaned[-1][0] + 0.01
        cleaned.append((t, int(round(x))))

    if len(cleaned) < 2:
        return str(int(cleaned[0][1]) if cleaned else left_x)
    return build_smooth_pan_expression(cleaned)


def detect_speaker_reframe_plan(
    clip_path: Path,
    output_format: str,
) -> Optional[Dict[str, Any]]:
    """Build a speaker-aware pan or split-screen plan for a trimmed clip."""
    try:
        width, height = ffprobe_video_size(clip_path)
        if width / max(height, 1) <= 1.2:
            return None

        scene_cuts = count_scene_cuts(clip_path)
        if scene_cuts > 2:
            logger.info("Skipping speaker reframe: %d scene cuts detected", scene_cuts)
            return None

        duration = ffprobe_duration(clip_path)
        face_centers = detect_faces_in_clip(clip_path, 0, min(duration, 12.0))
        regions = cluster_two_face_regions(face_centers, width, height)
        if not regions:
            return None

        crop_w = round_to_even(min(width, int(height * 9 / 16)))
        left_x = clamp_even(
            regions["left"]["center_x"] - crop_w // 2,
            0,
            max(0, width - crop_w),
        )
        right_x = clamp_even(
            regions["right"]["center_x"] - crop_w // 2,
            0,
            max(0, width - crop_w),
        )

        if output_format == "vertical_split":
            return {
                "mode": "split",
                "width": width,
                "height": height,
                "regions": regions,
            }

        with tempfile.TemporaryDirectory(prefix="supoclip_motion_") as motion_dir:
            left_motion = Path(motion_dir) / "left.txt"
            right_motion = Path(motion_dir) / "right.txt"
            left = regions["left"]
            right = regions["right"]
            filter_complex = (
                f"[0:v]split=2[l][r];"
                f"[l]crop={left['roi_w']}:{left['roi_h']}:{left['roi_x']}:{left['roi_y']},"
                f"format=gray,tblend=all_mode=difference,signalstats,"
                f"metadata=mode=print:key=lavfi.signalstats.YAVG:file={ffmpeg_escape_filter_path(left_motion)}[lo];"
                f"[r]crop={right['roi_w']}:{right['roi_h']}:{right['roi_x']}:{right['roi_y']},"
                f"format=gray,tblend=all_mode=difference,signalstats,"
                f"metadata=mode=print:key=lavfi.signalstats.YAVG:file={ffmpeg_escape_filter_path(right_motion)}[ro]"
            )
            result = run_ffmpeg_command(
                [
                    "ffmpeg",
                    "-y",
                    "-i",
                    str(clip_path),
                    "-filter_complex",
                    filter_complex,
                    "-map",
                    "[lo]",
                    "-f",
                    "null",
                    "-",
                    "-map",
                    "[ro]",
                    "-f",
                    "null",
                    "-",
                ],
                timeout=300,
            )
            if result.returncode != 0:
                return None
            times, left_values = parse_motion_metadata(left_motion)
            _, right_values = parse_motion_metadata(right_motion)
            timeline = build_speaker_timeline_from_motion(
                times,
                left_values,
                right_values,
            )
            if len(timeline) < 2:
                return None

        return {
            "mode": "pan",
            "width": width,
            "height": height,
            "crop_w": crop_w,
            "crop_h": height,
            "x_expression": build_pan_expression(timeline, left_x, right_x),
            "timeline": timeline,
        }
    except Exception as exc:
        logger.warning("Speaker reframe planning failed: %s", exc)
        return None


def compute_vertical_crop_dims(
    width: int, height: int, target_ratio: float = 9 / 16
) -> Tuple[int, int]:
    """Even-dimensioned 9:16 crop box that fits inside a source frame."""
    if width <= 0 or height <= 0:
        return width, height
    if width / height > target_ratio:
        crop_w = round_to_even(int(height * target_ratio))
        crop_h = round_to_even(height)
    else:
        crop_w = round_to_even(width)
        crop_h = round_to_even(int(width / target_ratio))
    return (
        max(2, min(crop_w, round_to_even(width))),
        max(2, min(crop_h, round_to_even(height))),
    )



def _open_face_detectors():
    """Initialise the MediaPipe (preferred) + Haar (fallback) face detectors."""
    mp_face = None
    try:
        import mediapipe as mp

        mp_face = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.5
        )
    except Exception as exc:
        logger.info("MediaPipe unavailable (%s); using Haar", exc)
    haar = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )
    return mp_face, haar


def _detect_dominant_face(frame_bgr, mp_face, haar) -> Optional[Tuple[float, float]]:
    """Return (center_x_fraction, area_fraction) of the dominant face, or None."""
    h, w = frame_bgr.shape[:2]
    frame_area = float(max(1, w * h))
    best: Optional[Tuple[float, float, float]] = None  # (score, cx, area_frac)

    if mp_face is not None:
        try:
            results = mp_face.process(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
            if results.detections:
                for det in results.detections:
                    box = det.location_data.relative_bounding_box
                    bw = max(0.0, box.width) * w
                    bh = max(0.0, box.height) * h
                    conf = float(det.score[0]) if det.score else 0.5
                    cx = (box.xmin + box.width / 2) * w
                    score = bw * bh * conf
                    if bw > 10 and bh > 10 and (best is None or score > best[0]):
                        best = (score, cx, (bw * bh) / frame_area)
        except Exception:
            pass

    if best is None:
        try:
            gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
            min_side = max(14, int(w * 0.04))
            faces = haar.detectMultiScale(
                gray,
                scaleFactor=1.2,  # coarser scale steps -> ~2x faster
                minNeighbors=3,
                minSize=(min_side, min_side),
                maxSize=(int(w * 0.7), int(h * 0.7)),
            )
            for (fx, fy, fw, fh) in faces:
                score = float(fw * fh)
                if best is None or score > best[0]:
                    best = (score, fx + fw / 2.0, (fw * fh) / frame_area)
        except Exception:
            pass

    if best is None:
        return None
    return best[1] / w, best[2]


def _scene_cuts_from_diffs(diffs: List[Tuple[float, float]]) -> List[float]:
    """Derive scene-cut timestamps from per-frame difference spikes."""
    if len(diffs) < 3:
        return []
    vals = [d for _, d in diffs]
    mean = sum(vals) / len(vals)
    std = (sum((v - mean) ** 2 for v in vals) / len(vals)) ** 0.5
    threshold = max(14.0, mean + 3.5 * std)
    cuts: List[float] = []
    for i, (t, d) in enumerate(diffs):
        if d <= threshold:
            continue
        if (i == 0 or d >= diffs[i - 1][1]) and (
            i == len(diffs) - 1 or d >= diffs[i + 1][1]
        ):
            cuts.append(t)
    return cuts


def analyze_vertical_clip(
    input_path: Path,
    *,
    sample_fps: float = 3.0,
    proc_width: int = 480,
) -> Tuple[List[Tuple[float, Optional[float], float]], List[float]]:
    """Fast single-pass clip analysis: face track + scene cuts in one decode.

    Replaces slow per-sample random seeks (and a separate scene-detect pass) with
    a single sequential ffmpeg decode at low fps/resolution, piped straight into
    lightweight face detection. Scene cuts come from frame differences computed
    in the same pass. Returns (track, scene_cuts) where track entries are
    (t, center_x_in_source_px or None, area_frac).
    """
    width, height = ffprobe_video_size(input_path)
    if width <= 0 or height <= 0:
        return [], []
    proc_w = round_to_even(min(proc_width, width))
    proc_h = round_to_even(max(2, int(round(proc_w * height / width))))
    frame_bytes = proc_w * proc_h * 3

    command = [
        "ffmpeg", "-v", "error", "-an", "-sn",
        "-i", str(input_path),
        "-vf", f"fps={sample_fps:.3f},scale={proc_w}:{proc_h}",
        "-pix_fmt", "bgr24", "-f", "rawvideo",
        "-threads", "0", "-",
    ]
    try:
        proc = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
    except Exception as exc:
        logger.warning("analyze_vertical_clip: ffmpeg spawn failed (%s)", exc)
        return [], []

    mp_face, haar = _open_face_detectors()
    track: List[Tuple[float, Optional[float], float]] = []
    diffs: List[Tuple[float, float]] = []
    prev_small = None
    idx = 0
    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if not raw or len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(proc_h, proc_w, 3)
            t = idx / sample_fps
            face = _detect_dominant_face(frame, mp_face, haar)
            if face is None:
                track.append((t, None, 0.0))
            else:
                cx_frac, area = face
                track.append((t, cx_frac * width, area))
            small = cv2.resize(frame, (32, 18)).astype(np.int16)
            if prev_small is not None:
                diffs.append((t, float(np.mean(np.abs(small - prev_small)))))
            prev_small = small
            idx += 1
    finally:
        try:
            proc.stdout.close()
        except Exception:
            pass
        proc.wait()
        if mp_face is not None:
            try:
                mp_face.close()
            except Exception:
                pass

    return track, _scene_cuts_from_diffs(diffs)


def _median_filter(values: List[float], window: int = 3) -> List[float]:
    """Small median filter to remove single-frame detection spikes."""
    if window <= 1 or len(values) < window:
        return list(values)
    half = window // 2
    out: List[float] = []
    for i in range(len(values)):
        lo = max(0, i - half)
        hi = min(len(values), i + half + 1)
        seg = sorted(values[lo:hi])
        out.append(seg[len(seg) // 2])
    return out


def build_crop_trajectory(
    track: List[Tuple[float, Optional[float], float]],
    width: int,
    crop_w: int,
    *,
    deadzone_frac: float = 0.05,
    smooth_time: float = 0.9,
    max_pan_speed_frac: float = 0.4,
) -> List[Tuple[float, int]]:
    """Turn a raw face-centre track into a smooth, eased crop-x trajectory.

    Returns keyframes [(t, x)] for the crop's left edge. The motion is produced
    by a critically-damped spring (Unity-style SmoothDamp) easing toward a
    comfort-zone target, which gives natural ease-in/ease-out with no overshoot
    and no mechanical ramp-then-stop feel. A deadzone keeps the frame still for
    small head movements; a median pre-filter removes detection spikes. Returns
    [] when there isn't enough signal to track.
    """
    if not track:
        return []
    max_x = max(0, width - crop_w)
    if max_x <= 0:
        return []

    centers: List[Optional[float]] = [c for _, c, _ in track]
    times = [t for t, _, _ in track]
    detected = sum(1 for c in centers if c is not None)
    if detected < max(3, len(centers) // 5):
        return []  # too sparse to trust — caller falls back to a static crop

    # Gap-fill missing detections: forward fill, then back fill.
    last: Optional[float] = None
    for i in range(len(centers)):
        if centers[i] is None:
            centers[i] = last
        else:
            last = centers[i]
    last = None
    for i in range(len(centers) - 1, -1, -1):
        if centers[i] is None:
            centers[i] = last
        else:
            last = centers[i]
    if any(c is None for c in centers):
        return []

    desired = [min(max(c - crop_w / 2.0, 0.0), float(max_x)) for c in centers]
    desired = _median_filter(desired, window=3)

    deadzone = max(2.0, crop_w * deadzone_frac)
    max_speed = max(1.0, width * max_pan_speed_frac)
    smooth_time = max(0.05, smooth_time)
    omega = 2.0 / smooth_time

    # Comfort-zone target: a stable goal that only moves once the subject drifts
    # past the deadzone, so the spring isn't chasing sub-deadzone jitter.
    targets: List[float] = []
    anchor = desired[0]
    for d in desired:
        if d - anchor > deadzone:
            anchor = d - deadzone
        elif anchor - d > deadzone:
            anchor = d + deadzone
        targets.append(anchor)

    # Critically-damped spring toward the comfort-zone target.
    eased: List[float] = []
    cur = float(targets[0])
    vel = 0.0
    for i, tgt in enumerate(targets):
        dt = (times[i] - times[i - 1]) if i > 0 else 0.0
        if dt <= 0:
            eased.append(cur)
            continue
        x = omega * dt
        exp_factor = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
        change = cur - tgt
        max_change = max_speed * smooth_time
        change = max(-max_change, min(change, max_change))
        adj_target = cur - change
        temp = (vel + omega * change) * dt
        vel = (vel - omega * temp) * exp_factor
        out = adj_target + (change + temp) * exp_factor
        # Prevent overshoot past the target.
        if (tgt - cur > 0) == (out > tgt):
            out = tgt
            vel = (out - tgt) / dt
        cur = min(max(out, 0.0), float(max_x))
        eased.append(cur)

    # Final low-pass pass: removes residual velocity steps so the piecewise-
    # linear keyframes read as continuous, fluid motion.
    eased = smooth_values(eased, window=5)

    # Keep keyframes fine enough that linear interpolation tracks the smooth
    # curve without visible faceting.
    def simplify(tol: float) -> List[Tuple[float, int]]:
        keys: List[Tuple[float, int]] = [(0.0, int(round(eased[0])))]
        for i in range(1, len(eased)):
            if abs(eased[i] - keys[-1][1]) >= tol:
                keys.append((times[i], int(round(eased[i]))))
        if keys[-1][0] < times[-1]:
            keys.append((times[-1], int(round(eased[-1]))))
        return keys

    tol = max(1.5, crop_w * 0.006)
    keys = simplify(tol)
    while len(keys) > 90:
        tol *= 1.5
        keys = simplify(tol)

    if keys and keys[0][0] > 0.0:
        keys[0] = (0.0, keys[0][1])
    return keys


def trajectory_has_movement(keys: List[Tuple[float, int]], crop_w: int) -> bool:
    """Whether a trajectory pans enough to be worth a moving crop."""
    if len(keys) < 2:
        return False
    xs = [x for _, x in keys]
    return (max(xs) - min(xs)) >= max(8, crop_w * 0.04)


def build_smooth_pan_expression(keys: List[Tuple[float, int]]) -> str:
    """Piecewise-linear ffmpeg crop-x expression interpolating the keyframes.

    Commas are escaped for use inside a quoted filtergraph expression. The
    result is rounded to an even integer for clean chroma subsampling.
    """
    if not keys:
        return "0"
    if len(keys) == 1:
        return str(int(keys[0][1]))

    expr = str(int(keys[-1][1]))
    for i in range(len(keys) - 2, -1, -1):
        t0, x0 = keys[i]
        t1, x1 = keys[i + 1]
        span = max(1e-3, t1 - t0)
        lerp = f"({int(x0)}+({int(x1) - int(x0)})*(t-{t0:.3f})/{span:.3f})"
        expr = f"if(lt(t\\,{t1:.3f})\\,{lerp}\\,{expr})"
    return f"trunc(({expr})/2)*2"


# Scene-aware vertical layout tuning. A shot is a "face shot" (tracked crop) if a
# face is detected in at least FACE_PRESENCE_RATE of frames over a short window —
# this keeps far-away/small talking-head faces as crops while only flagging true
# content (tweets/graphs with NO face) as full-frame fit. A tiny area floor
# rejects single-pixel false positives. Short layout islands are merged so the
# layout doesn't flicker, and switch points snap to nearby scene cuts.
FACE_PRESENCE_MIN_AREA = 0.002
FACE_RATE_WINDOW = 2.0
FACE_PRESENCE_RATE = 0.25
# Only switch to the full-frame fit for genuinely sustained content shots, so a
# brief detection drop while talking never causes a jarring zoom-out.
MIN_LAYOUT_SECONDS = 1.5
LAYOUT_SNAP_WINDOW = 0.6


def build_layout_plan(
    track: List[Tuple[float, Optional[float], float]],
    scene_cuts: List[float],
    duration: float,
) -> List[Dict[str, Any]]:
    """Classify a clip into 'face' (tracked crop) and 'fit' (full-frame) shots.

    Talking-head shots become a tracked crop; content shots (tweets, graphs,
    code — no real face) become a full-frame blurred-background fit so nothing
    is cropped off. Boundaries are debounced and snapped to scene cuts.
    """
    if duration <= 0 or not track:
        return [{"start": 0.0, "end": max(0.0, duration), "kind": "face"}]

    times = [t for t, _, _ in track]
    present = [
        1 if (c is not None and a >= FACE_PRESENCE_MIN_AREA) else 0
        for _, c, a in track
    ]

    # Classify by face-presence RATE over a window: a real talking shot has a
    # face in many frames (even if small/spotty); a content shot has ~none.
    diffs = [times[i] - times[i - 1] for i in range(1, len(times))]
    dt = sorted(diffs)[len(diffs) // 2] if diffs else 0.25
    half = max(1, int(round(FACE_RATE_WINDOW / 2.0 / max(dt, 0.05))))
    smoothed: List[int] = []
    for i in range(len(present)):
        seg = present[max(0, i - half) : min(len(present), i + half + 1)]
        rate = sum(seg) / len(seg)
        smoothed.append(1 if rate >= FACE_PRESENCE_RATE else 0)

    # Build runs of constant layout value.
    runs: List[List[float]] = []
    run_start, run_val = 0.0, smoothed[0]
    for i in range(1, len(times)):
        if smoothed[i] != run_val:
            runs.append([run_start, times[i], run_val])
            run_start, run_val = times[i], smoothed[i]
    runs.append([run_start, duration, run_val])

    def coalesce(rs: List[List[float]]) -> List[List[float]]:
        out = [rs[0][:]]
        for r in rs[1:]:
            if r[2] == out[-1][2]:
                out[-1][1] = r[1]
            else:
                out.append(r[:])
        return out

    # Merge any run shorter than the minimum layout duration (flip + coalesce).
    runs = coalesce(runs)
    changed = True
    while changed and len(runs) > 1:
        changed = False
        for r in runs:
            if r[1] - r[0] < MIN_LAYOUT_SECONDS:
                r[2] = 1 - r[2]
                changed = True
                break
        if changed:
            runs = coalesce(runs)

    # Snap internal boundaries to nearby scene cuts for clean switches.
    cuts = sorted(c for c in (scene_cuts or []) if 0.05 < c < duration - 0.05)
    for i in range(len(runs) - 1):
        boundary = runs[i][1]
        near = [c for c in cuts if abs(c - boundary) <= LAYOUT_SNAP_WINDOW]
        if not near:
            continue
        snapped = min(near, key=lambda c: abs(c - boundary))
        if runs[i][0] + 0.1 < snapped < runs[i + 1][1] - 0.1:
            runs[i][1] = snapped
            runs[i + 1][0] = snapped

    return [
        {"start": r[0], "end": r[1], "kind": "face" if r[2] == 1 else "fit"}
        for r in runs
    ]


def build_vertical_compositor_filter(
    crop_chain: str,
    face_intervals: List[Tuple[float, float]],
    fit_intervals: List[Tuple[float, float]],
    blur_sigma: int = 14,
) -> str:
    """filter_complex switching between a tracked face crop and a blurred-
    background full-frame fit over time. Produces a labelled [vout] stream.

    Layers: a blurred fill background (always), the face crop on top during face
    shots (covers the frame), and the centred full-frame fit during content
    shots (background shows around it).
    """
    def enable_expr(intervals: List[Tuple[float, float]]) -> str:
        if not intervals:
            return "0"
        return "+".join(
            f"between(t\\,{a:.3f}\\,{b:.3f})" for a, b in intervals
        )

    face_en = enable_expr(face_intervals)
    fit_en = enable_expr(fit_intervals)
    # Smooth Gaussian background: blur at half resolution (plenty of detail for a
    # heavy blur) with multiple passes for a true Gaussian falloff, then upscale
    # 2x with bilinear so there's no lanczos ringing/blockiness — a creamy blur.
    return (
        "[0:v]split=3[bgsrc][crsrc][ftsrc];"
        "[bgsrc]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,"
        f"gblur=sigma={blur_sigma}:steps=2,scale=1080:1920:flags=bilinear,setsar=1[bg];"
        f"[crsrc]{crop_chain}[face];"
        "[ftsrc]scale=1080:1920:force_original_aspect_ratio=decrease:flags=lanczos,"
        "scale=trunc(iw/2)*2:trunc(ih/2)*2,setsar=1[fit];"
        f"[bg][face]overlay=0:0:enable='{face_en}'[t1];"
        f"[t1][fit]overlay=(W-w)/2:(H-h)/2:enable='{fit_en}'[vout]"
    )


def build_vertical_filter_plan(
    input_path: Path, width: int, height: int
) -> Tuple[str, str]:
    """Build the 9:16 reframing filter for the default vertical mode.

    Returns (filter, mode): mode 'vf' is a simple crop chain; mode 'complex' is a
    filter_complex producing [vout]. Talking-head-only clips use the cheap
    tracked crop; clips containing content shots use the scene-aware compositor
    so tweets / graphs / slides are shown in full instead of being cropped.
    """
    crop_w, crop_h = compute_vertical_crop_dims(width, height)
    duration = ffprobe_duration(input_path)

    # Narrow/portrait source: no horizontal room to crop — static fit.
    if crop_w >= width:
        sx, sy, sw, sh = detect_optimal_crop_region(input_path, 0, min(duration, 12.0))
        return (
            f"crop={sw}:{sh}:{sx}:{sy},scale=1080:1920:flags=lanczos,setsar=1",
            "vf",
        )

    # One fast decode pass yields both the face track and the scene cuts.
    try:
        track, scene_cuts = analyze_vertical_clip(input_path)
    except Exception as exc:
        logger.warning("Clip analysis failed (%s); using static crop", exc)
        track, scene_cuts = [], []

    keys = build_crop_trajectory(track, width, crop_w) if track else []
    if keys and trajectory_has_movement(keys, crop_w):
        x_expr = build_smooth_pan_expression(keys)
        crop_chain = (
            f"crop={crop_w}:{crop_h}:x='{x_expr}':y=0,"
            "scale=1080:1920:flags=lanczos,setsar=1"
        )
    else:
        if keys:
            static_x = clamp_even(
                int(np.median([x for _, x in keys])), 0, max(0, width - crop_w)
            )
        else:
            sx, _, _, _ = detect_optimal_crop_region(input_path, 0, min(duration, 12.0))
            static_x = clamp_even(sx, 0, max(0, width - crop_w))
        crop_chain = (
            f"crop={crop_w}:{crop_h}:{static_x}:0,scale=1080:1920:flags=lanczos,setsar=1"
        )

    # Decide the layout over time. All-face clips skip the compositor (cheaper).
    plan = build_layout_plan(track, scene_cuts, duration)
    fit_intervals = [(s["start"], s["end"]) for s in plan if s["kind"] == "fit"]
    if not fit_intervals:
        return (crop_chain, "vf")

    face_intervals = [(s["start"], s["end"]) for s in plan if s["kind"] == "face"]
    logger.info(
        "Scene-aware vertical layout: %d face shot(s), %d content shot(s)",
        len(face_intervals), len(fit_intervals),
    )
    return (
        build_vertical_compositor_filter(crop_chain, face_intervals, fit_intervals),
        "complex",
    )


def render_reframed_clip_ffmpeg(
    input_path: Path,
    output_path: Path,
    output_format: str,
    subtitle_ass_path: Optional[Path] = None,
    fonts_dir: Optional[Path] = None,
) -> Tuple[bool, int, int]:
    """Render the final framed clip and (optionally) burn subtitles in one pass.

    Collapsing reframing + subtitle burn into a single encode avoids a whole
    generation of re-encode loss. The pass uses the high-quality profile, CFR
    output and loudness-normalised audio.
    """
    width, height = ffprobe_video_size(input_path)
    has_audio = ffprobe_has_audio(input_path)
    subs = (
        subtitles_filter_fragment(subtitle_ass_path, fonts_dir)
        if subtitle_ass_path
        else None
    )
    audio_args = build_audio_output_args(has_audio)

    if output_format == "original":
        out_w, out_h = round_to_even(width), round_to_even(height)
        if not subs:
            shutil.copyfile(input_path, output_path)
            return True, out_w, out_h
        command = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", f"{subs},setsar=1",
            *build_final_video_encode_args(),
            *audio_args,
            "-movflags", "+faststart",
            str(output_path),
        ]
        return run_ffmpeg_command(command).returncode == 0, out_w, out_h

    plan = (
        detect_speaker_reframe_plan(input_path, output_format)
        if output_format in {"vertical_pan", "vertical_split"}
        else None
    )

    if plan and plan["mode"] == "split":
        left = plan["regions"]["left"]
        right = plan["regions"]["right"]
        vstack_tail = f",{subs}" if subs else ""
        video_filter = (
            f"[0:v]split=2[l][r];"
            f"[l]crop={left['tile_w']}:{left['tile_h']}:{left['tile_x']}:{left['tile_y']},"
            f"scale=1080:960:flags=lanczos,setsar=1[lv];"
            f"[r]crop={right['tile_w']}:{right['tile_h']}:{right['tile_x']}:{right['tile_y']},"
            f"scale=1080:960:flags=lanczos,setsar=1[rv];"
            f"[lv][rv]vstack,setsar=1{vstack_tail}[v]"
        )
        command = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-filter_complex", video_filter,
            "-map", "[v]", "-map", "0:a?",
            *build_final_video_encode_args(),
            *audio_args,
            "-movflags", "+faststart",
            str(output_path),
        ]
        return run_ffmpeg_command(command).returncode == 0, 1080, 1920

    if plan and plan["mode"] == "pan":
        video_filter = (
            f"crop={plan['crop_w']}:{plan['crop_h']}:x='{plan['x_expression']}':y=0,"
            "scale=1080:1920:flags=lanczos,setsar=1"
        )
        if subs:
            video_filter = f"{video_filter},{subs}"
        command = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-vf", video_filter,
            *build_final_video_encode_args(),
            *audio_args,
            "-movflags", "+faststart",
            str(output_path),
        ]
        return run_ffmpeg_command(command).returncode == 0, 1080, 1920

    # Default "vertical": scene-aware — tracked crop for face shots, blurred-
    # background full-frame fit for content shots (tweets/graphs/slides).
    video_filter, mode = build_vertical_filter_plan(input_path, width, height)
    if mode == "complex":
        if subs:
            graph = f"{video_filter};[vout]{subs}[v]"
            map_label = "[v]"
        else:
            graph = video_filter
            map_label = "[vout]"
        command = [
            "ffmpeg", "-y", "-i", str(input_path),
            "-filter_complex", graph,
            "-map", map_label, "-map", "0:a?",
            *build_final_video_encode_args(),
            *audio_args,
            "-movflags", "+faststart",
            str(output_path),
        ]
        return run_ffmpeg_command(command).returncode == 0, 1080, 1920

    if subs:
        video_filter = f"{video_filter},{subs}"
    command = [
        "ffmpeg", "-y", "-i", str(input_path),
        "-vf", video_filter,
        *build_final_video_encode_args(),
        *audio_args,
        "-movflags", "+faststart",
        str(output_path),
    ]
    return run_ffmpeg_command(command).returncode == 0, 1080, 1920


def burn_ass_subtitles_ffmpeg(
    input_path: Path,
    ass_path: Path,
    output_path: Path,
    fonts_dir: Optional[Path] = None,
) -> bool:
    subtitles_filter = f"subtitles=filename={ffmpeg_escape_filter_path(ass_path)}"
    if fonts_dir:
        subtitles_filter += f":fontsdir={ffmpeg_escape_filter_value(str(fonts_dir))}"
    video_filter = f"{subtitles_filter},setsar=1"

    command = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vf",
        video_filter,
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "20",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    return run_ffmpeg_command(command).returncode == 0


def parse_timestamp_to_seconds(timestamp_str: str) -> float:
    """Parse timestamp string to seconds."""
    try:
        timestamp_str = timestamp_str.strip()
        logger.info(f"Parsing timestamp: '{timestamp_str}'")  # Debug logging

        if ":" in timestamp_str:
            parts = timestamp_str.split(":")
            if len(parts) == 2:
                minutes, seconds = map(int, parts)
                result = minutes * 60 + seconds
                logger.info(f"Parsed '{timestamp_str}' -> {result}s")
                return result
            elif len(parts) == 3:  # HH:MM:SS format
                hours, minutes, seconds = map(int, parts)
                result = hours * 3600 + minutes * 60 + seconds
                logger.info(f"Parsed '{timestamp_str}' -> {result}s")
                return result

        # Try parsing as pure seconds
        result = float(timestamp_str)
        logger.info(f"Parsed '{timestamp_str}' as seconds -> {result}s")
        return result

    except (ValueError, IndexError) as e:
        logger.error(f"Failed to parse timestamp '{timestamp_str}': {e}")
        return 0.0


def seconds_to_mmss(seconds: float) -> str:
    """Format seconds as MM:SS with integer-second precision."""
    total = max(0, int(round(seconds)))
    minutes = total // 60
    secs = total % 60
    return f"{minutes:02d}:{secs:02d}"


def parse_transcript_lines(transcript: str) -> List[Dict[str, Any]]:
    """Parse formatted transcript lines into timestamped records."""
    lines: List[Dict[str, Any]] = []
    pattern = re.compile(
        r"^\[(?P<start>\d{1,3}:\d{2})\s*-\s*(?P<end>\d{1,3}:\d{2})\]\s*(?P<text>.*)$"
    )
    for raw_line in transcript.splitlines():
        match = pattern.match(raw_line.strip())
        if not match:
            continue
        text = match.group("text").strip()
        speaker = None
        speaker_match = re.match(r"Speaker\s+([^:]+):\s*(.*)$", text)
        if speaker_match:
            speaker = speaker_match.group(1).strip()
            text = speaker_match.group(2).strip()
        lines.append(
            {
                "start": parse_timestamp_to_seconds(match.group("start")),
                "end": parse_timestamp_to_seconds(match.group("end")),
                "start_label": match.group("start"),
                "end_label": match.group("end"),
                "speaker": speaker,
                "text": text,
            }
        )
    return lines


def detect_audio_peak_times(video_path: Path, max_peaks: int = 8) -> List[float]:
    """Find approximate one-second audio energy peaks with ffmpeg astats."""
    result = run_ffmpeg_command(
        [
            "ffmpeg",
            "-i",
            str(video_path),
            "-vn",
            "-af",
            "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level",
            "-f",
            "null",
            "-",
        ],
        timeout=600,
    )
    if result.returncode != 0:
        return []

    current_time: Optional[float] = None
    samples: List[Tuple[float, float]] = []
    for line in result.stderr.splitlines():
        time_match = re.search(r"pts_time:([0-9.]+)", line)
        if time_match:
            current_time = float(time_match.group(1))
            continue
        rms_match = re.search(r"lavfi\.astats\.Overall\.RMS_level=([-0-9.]+)", line)
        if rms_match and current_time is not None:
            try:
                samples.append((current_time, float(rms_match.group(1))))
            except ValueError:
                pass
            current_time = None

    if not samples:
        return []
    samples.sort(key=lambda item: item[1], reverse=True)
    peaks: List[float] = []
    for timestamp, _ in samples:
        if all(abs(timestamp - existing) >= 4.0 for existing in peaks):
            peaks.append(timestamp)
        if len(peaks) >= max_peaks:
            break
    return sorted(peaks)


def build_clip_signal_summary(video_path: Path, transcript: str) -> str:
    """Build deterministic clipping hints for the LLM ranking step."""
    transcript_lines = parse_transcript_lines(transcript)
    if not transcript_lines:
        return ""

    trigger_pattern = re.compile(
        r"\b(wait|what|no way|seriously|actually|but|however|because|mistake|secret|"
        r"wild|crazy|insane|never|always|nobody|everybody|why|how|haha|laugh|lol|damn|"
        r"shit|fuck)\b",
        re.IGNORECASE,
    )
    candidates: List[Tuple[float, Dict[str, Any], str]] = []
    audio_peaks = detect_audio_peak_times(video_path)

    for idx, line in enumerate(transcript_lines):
        text = line["text"]
        score = 0.0
        reasons: List[str] = []
        if trigger_pattern.search(text):
            score += 2.0
            reasons.append("trigger phrase")
        if "?" in text:
            score += 1.5
            reasons.append("question/hook")
        if "!" in text:
            score += 1.0
            reasons.append("emphatic delivery")
        if re.search(r"\b(I|we)\s+(thought|realized|found|learned|made|lost|won)\b", text, re.I):
            score += 1.0
            reasons.append("story turn")
        if len(text.split()) <= 8:
            score += 0.5
            reasons.append("short punchy line")

        previous_line = transcript_lines[idx - 1] if idx > 0 else None
        next_line = transcript_lines[idx + 1] if idx + 1 < len(transcript_lines) else None
        if previous_line and line["start"] - previous_line["end"] >= 1.0:
            score += 1.0
            reasons.append("pause before line")
        if previous_line and previous_line.get("speaker") and line.get("speaker"):
            if previous_line["speaker"] != line["speaker"] and line["end"] - line["start"] <= 6:
                score += 1.25
                reasons.append("rapid speaker turn")
        if next_line and next_line.get("speaker") and line.get("speaker"):
            if next_line["speaker"] != line["speaker"] and next_line["end"] - line["start"] <= 10:
                score += 1.0
                reasons.append("back-and-forth")
        if any(line["start"] <= peak <= line["end"] for peak in audio_peaks):
            score += 1.25
            reasons.append("audio energy peak")

        if score > 0:
            candidates.append((score, line, ", ".join(reasons)))

    candidates.sort(key=lambda item: item[0], reverse=True)
    summary_lines = [
        "Deterministic clip-worthiness signals to consider before ranking:",
    ]
    for score, line, reason in candidates[:12]:
        summary_lines.append(
            f"- [{line['start_label']} - {line['end_label']}] score={score:.1f}: {reason}; {line['text']}"
        )
    return "\n".join(summary_lines)


def get_words_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> List[Dict]:
    """Extract words that fall within a clip timerange."""
    if not transcript_data or not transcript_data.get("words"):
        return []

    clip_start_ms = int(clip_start * 1000)
    clip_end_ms = int(clip_end * 1000)

    relevant_words = []
    for word_data in transcript_data["words"]:
        word_start = word_data["start"]
        word_end = word_data["end"]

        if word_start < clip_end_ms and word_end > clip_start_ms:
            relative_start = max(0, (word_start - clip_start_ms) / 1000.0)
            relative_end = min(
                (clip_end_ms - clip_start_ms) / 1000.0,
                (word_end - clip_start_ms) / 1000.0,
            )

            if relative_end > relative_start:
                relevant_words.append(
                    {
                        "text": word_data["text"],
                        "start": relative_start,
                        "end": relative_end,
                        "confidence": word_data.get("confidence", 1.0),
                    }
                )

    return relevant_words


def get_absolute_words_in_range(
    transcript_data: Dict, clip_start: float, clip_end: float
) -> List[Dict[str, Any]]:
    """Extract absolute-timing words that overlap a clip timerange."""
    if not transcript_data or not transcript_data.get("words"):
        return []

    clip_start_ms = int(clip_start * 1000)
    clip_end_ms = int(clip_end * 1000)

    relevant_words: List[Dict[str, Any]] = []
    for word_data in transcript_data["words"]:
        word_start = int(word_data["start"])
        word_end = int(word_data["end"])
        overlap_start = max(word_start, clip_start_ms)
        overlap_end = min(word_end, clip_end_ms)

        if overlap_end <= overlap_start:
            continue

        relevant_words.append(
            {
                "text": word_data["text"],
                "start": overlap_start / 1000.0,
                "end": overlap_end / 1000.0,
                "confidence": word_data.get("confidence", 1.0),
            }
        )

    return relevant_words


def _normalize_cleanup_token(value: str) -> str:
    return re.sub(r"[^a-z0-9']+", "", value.lower())


def _build_cleanup_phrases(
    remove_filler_words: bool, filtered_words: Optional[List[str]]
) -> List[List[str]]:
    raw_phrases: List[str] = []
    if remove_filler_words:
        raw_phrases.extend(DEFAULT_FILTERED_WORDS)
    raw_phrases.extend(filtered_words or [])

    normalized_phrases: List[List[str]] = []
    seen: set[tuple[str, ...]] = set()
    for phrase in raw_phrases:
        tokens = [
            _normalize_cleanup_token(part)
            for part in phrase.split()
            if _normalize_cleanup_token(part)
        ]
        if not tokens:
            continue
        key = tuple(tokens)
        if key in seen:
            continue
        seen.add(key)
        normalized_phrases.append(tokens)

    normalized_phrases.sort(key=len, reverse=True)
    return normalized_phrases


def _merge_intervals(intervals: List[Tuple[float, float]]) -> List[Tuple[float, float]]:
    if not intervals:
        return []

    merged: List[Tuple[float, float]] = []
    for start, end in sorted(intervals):
        if end <= start:
            continue
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        merged[-1] = (merged[-1][0], max(merged[-1][1], end))
    return merged


def build_clip_keep_ranges(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    cleanup_settings: Optional[Dict[str, Any]] = None,
) -> List[Tuple[float, float]]:
    """Build source-video keep ranges after removing pauses and filtered words."""
    if clip_end <= clip_start:
        return []

    settings = cleanup_settings or {}
    if not clip_cleanup_enabled(settings):
        return [(clip_start, clip_end)]

    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data or not transcript_data.get("words"):
        return [(clip_start, clip_end)]

    relevant_words = get_absolute_words_in_range(transcript_data, clip_start, clip_end)
    if not relevant_words:
        return [(clip_start, clip_end)]

    removal_intervals: List[Tuple[float, float]] = []
    pause_threshold_seconds = max(
        0.25, float(settings.get("pause_threshold_ms", 900)) / 1000.0
    )
    cut_long_pauses = bool(settings.get("cut_long_pauses"))

    if cut_long_pauses:
        leading_gap = relevant_words[0]["start"] - clip_start
        if leading_gap >= pause_threshold_seconds:
            removal_intervals.append((clip_start, relevant_words[0]["start"]))

        for current, nxt in zip(relevant_words, relevant_words[1:]):
            gap = nxt["start"] - current["end"]
            if gap >= pause_threshold_seconds:
                removal_intervals.append((current["end"], nxt["start"]))

        trailing_gap = clip_end - relevant_words[-1]["end"]
        if trailing_gap >= pause_threshold_seconds:
            removal_intervals.append((relevant_words[-1]["end"], clip_end))

    phrase_tokens = _build_cleanup_phrases(
        bool(settings.get("remove_filler_words")),
        settings.get("filtered_words"),
    )
    if phrase_tokens:
        normalized_words = [
            _normalize_cleanup_token(word["text"]) for word in relevant_words
        ]
        idx = 0
        while idx < len(relevant_words):
            matched_length = 0
            for phrase in phrase_tokens:
                end_idx = idx + len(phrase)
                if end_idx > len(normalized_words):
                    continue
                if normalized_words[idx:end_idx] == phrase:
                    matched_length = len(phrase)
                    break

            if matched_length:
                removal_intervals.append(
                    (
                        relevant_words[idx]["start"],
                        relevant_words[idx + matched_length - 1]["end"],
                    )
                )
                idx += matched_length
                continue

            idx += 1

    merged_removals = _merge_intervals(removal_intervals)
    if not merged_removals:
        return [(clip_start, clip_end)]

    keep_ranges: List[Tuple[float, float]] = []
    cursor = clip_start
    for removal_start, removal_end in merged_removals:
        if removal_start - cursor >= 0.12:
            keep_ranges.append((cursor, removal_start))
        cursor = max(cursor, removal_end)

    if clip_end - cursor >= 0.12:
        keep_ranges.append((cursor, clip_end))

    total_kept = sum(max(0.0, end - start) for start, end in keep_ranges)
    if not keep_ranges or total_kept < 0.5:
        return [(clip_start, clip_end)]

    return keep_ranges


def build_keep_ranges_from_source_ranges(
    video_path: Path,
    source_ranges: List[Tuple[float, float]],
    cleanup_settings: Optional[Dict[str, Any]] = None,
) -> List[Tuple[float, float]]:
    """Apply cleanup to a list of source ranges while preserving their ordering."""
    normalized_ranges = normalize_source_ranges(source_ranges)
    if not normalized_ranges:
        return []

    keep_ranges: List[Tuple[float, float]] = []
    for range_start, range_end in normalized_ranges:
        keep_ranges.extend(
            build_clip_keep_ranges(
                video_path,
                range_start,
                range_end,
                cleanup_settings,
            )
        )
    return normalize_source_ranges(keep_ranges)


def get_words_for_keep_ranges(
    transcript_data: Dict, keep_ranges: List[Tuple[float, float]]
) -> List[Dict[str, Any]]:
    """Project transcript word timings into the output timeline after cuts.

    When the kept ranges are stitched with crossfades (see
    ``crossfade_fade_for_ranges``) each junction shortens the timeline by the
    fade duration, so word offsets are pulled earlier by the same amount to keep
    captions locked to the spoken audio.
    """
    if not transcript_data or not transcript_data.get("words") or not keep_ranges:
        return []

    fade = crossfade_fade_for_ranges(keep_ranges)
    relevant_words: List[Dict[str, Any]] = []
    timeline_offset = 0.0

    for index, (keep_start, keep_end) in enumerate(keep_ranges):
        if index > 0:
            timeline_offset -= fade  # account for the crossfade overlap
        range_words = get_absolute_words_in_range(transcript_data, keep_start, keep_end)
        for word in range_words:
            relevant_words.append(
                {
                    "text": word["text"],
                    "start": timeline_offset + (word["start"] - keep_start),
                    "end": timeline_offset + (word["end"] - keep_start),
                    "confidence": word.get("confidence", 1.0),
                }
            )
        timeline_offset += keep_end - keep_start

    return relevant_words


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
    keep_ranges: Optional[List[Tuple[float, float]]] = None,
) -> bool:
    """Create clip with optional subtitles. output_format: 'vertical' (9:16) or 'original' (keep source size)."""
    try:
        if keep_ranges:
            effective_keep_ranges = normalize_source_ranges(keep_ranges)
        else:
            effective_keep_ranges = [
                (max(start_time, start), min(end_time, end))
                for start, end in [(start_time, end_time)]
                if min(end_time, end) - max(start_time, start) > 0.05
            ]
        effective_keep_ranges = extend_keep_ranges_to_sentence_boundary(
            video_path,
            effective_keep_ranges,
        )
        duration = sum(end - start for start, end in effective_keep_ranges)
        if duration <= 0:
            logger.error(f"Invalid clip duration: {duration:.1f}s")
            return False

        keep_original = output_format == "original"
        logger.info(
            f"Creating clip: {start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s) "
            f"subtitles={add_subtitles} template '{caption_template}' format={'original' if keep_original else 'vertical'}"
        )

        # Fast path: no subtitles + original = ffmpeg stream copy (no re-encoding)
        if not add_subtitles and keep_original and len(effective_keep_ranges) == 1:
            fast_path_start, fast_path_end = effective_keep_ranges[0]
            result = subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss", str(fast_path_start),
                    "-i", str(video_path),
                    "-t", str(fast_path_end - fast_path_start),
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

        with tempfile.TemporaryDirectory(prefix="supoclip_render_") as temp_dir:
            temp_root = Path(temp_dir)
            source_clip_path = temp_root / "source.mp4"
            final_clip_path = temp_root / "final.mp4"
            ass_path = temp_root / "captions.ass"

            if not render_source_ranges_ffmpeg(
                video_path,
                effective_keep_ranges,
                source_clip_path,
            ):
                raise RuntimeError("ffmpeg source-range render failed")

            reframe_format = (
                output_format if output_format in VALID_OUTPUT_FORMATS else "vertical"
            )

            # Output dimensions are known ahead of the render: vertical modes are
            # always 1080x1920, "original" keeps the (even) source size. Knowing
            # them lets us build the ASS captions up front and burn them in the
            # SAME pass as reframing — one encode instead of two.
            if reframe_format == "original":
                src_w, src_h = ffprobe_video_size(source_clip_path)
                target_width, target_height = round_to_even(src_w), round_to_even(src_h)
            else:
                target_width, target_height = 1080, 1920

            burn_ass_path: Optional[Path] = None
            fonts_dir: Optional[Path] = None
            if add_subtitles and build_assemblyai_ass_subtitles(
                video_path,
                start_time,
                end_time,
                target_width,
                target_height,
                ass_path,
                font_family,
                font_size,
                font_color,
                caption_template,
                effective_keep_ranges,
            ):
                burn_ass_path = ass_path
                fonts_dir = ass_fonts_dir(
                    font_family or get_template(caption_template)["font_family"]
                )

            framed_ok, _, _ = render_reframed_clip_ffmpeg(
                source_clip_path,
                final_clip_path,
                reframe_format,
                subtitle_ass_path=burn_ass_path,
                fonts_dir=fonts_dir,
            )
            if not framed_ok:
                raise RuntimeError("ffmpeg reframe render failed")

            shutil.move(str(final_clip_path), str(output_path))
            logger.info(f"Successfully created clip with ffmpeg: {output_path}")
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
    cleanup_settings: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Create optimized video clips from segments with template support."""
    logger.info(
        f"Creating {len(segments)} clips subtitles={add_subtitles} template '{caption_template}'"
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    clips_info = []

    for i, segment in enumerate(segments):
        try:
            # Debug log the segment data
            logger.info(
                f"Processing segment {i + 1}: start='{segment.get('start_time')}', end='{segment.get('end_time')}'"
            )

            provided_keep_ranges = normalize_source_ranges(segment.get("keep_ranges"))
            provided_source_ranges = normalize_source_ranges(segment.get("source_ranges"))
            if provided_keep_ranges:
                start_seconds = provided_keep_ranges[0][0]
                end_seconds = provided_keep_ranges[-1][1]
            elif provided_source_ranges:
                start_seconds = provided_source_ranges[0][0]
                end_seconds = provided_source_ranges[-1][1]
            else:
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

            clip_filename = (
                f"clip_{i + 1}_{segment['start_time'].replace(':', '')}-"
                f"{segment['end_time'].replace(':', '')}_{uuid.uuid4().hex[:12]}.mp4"
            )
            clip_path = output_dir / clip_filename

            if provided_keep_ranges:
                keep_ranges = provided_keep_ranges
            elif provided_source_ranges:
                keep_ranges = build_keep_ranges_from_source_ranges(
                    video_path,
                    provided_source_ranges,
                    cleanup_settings,
                )
            else:
                keep_ranges = build_clip_keep_ranges(
                    video_path, start_seconds, end_seconds, cleanup_settings
                )
            keep_ranges = extend_keep_ranges_to_sentence_boundary(video_path, keep_ranges)

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
                keep_ranges,
            )

            if success:
                save_clip_source_ranges(clip_path, keep_ranges)
                cleaned_duration = sum(end - start for start, end in keep_ranges)
                clip_info = {
                    "clip_id": i + 1,
                    "filename": clip_filename,
                    "path": str(clip_path),
                    "start_time": segment["start_time"],
                    "end_time": segment["end_time"],
                    "duration": cleaned_duration,
                    "text": segment["text"],
                    "relevance_score": segment["relevance_score"],
                    "reasoning": segment["reasoning"],
                    # Include virality data if available
                    "virality_score": segment.get("virality_score", 0),
                    "hook_score": segment.get("hook_score", 0),
                    "engagement_score": segment.get("engagement_score", 0),
                    "value_score": segment.get("value_score", 0),
                    "shareability_score": segment.get("shareability_score", 0),
                    "hook_type": segment.get("hook_type"),
                    "keep_ranges": keep_ranges,
                }
                clips_info.append(clip_info)
                logger.info(f"Created clip {i + 1}: {cleaned_duration:.1f}s")
            else:
                logger.error(f"Failed to create clip {i + 1}")

        except Exception as e:
            logger.error(f"Error processing clip {i + 1}: {e}")

    logger.info(f"Successfully created {len(clips_info)}/{len(segments)} clips")
    return clips_info


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
    try:
        clip1_duration = ffprobe_duration(clip1_path)
        clip2_duration = ffprobe_duration(clip2_path)
        transition_duration = min(1.5, clip1_duration, clip2_duration)
        if transition_duration <= 0:
            logger.warning("Transition duration is zero, skipping transition effect")
            return False

        width, height = ffprobe_video_size(clip2_path)
        clip1_tail_start = max(0.0, clip1_duration - transition_duration)
        filter_parts = [
            (
                f"[0:v]trim=start={clip1_tail_start:.3f}:end={clip1_duration:.3f},"
                f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos[v0]"
            ),
            (
                f"[1:v]trim=start=0:end={transition_duration:.3f},"
                f"setpts=PTS-STARTPTS,scale={width}:{height}:flags=lanczos[v1]"
            ),
            (
                f"[v0][v1]xfade=transition=fade:duration={transition_duration:.3f}:"
                "offset=0[vintro]"
            ),
        ]
        if clip2_duration - transition_duration > 0.05:
            filter_parts.extend(
                [
                    (
                        f"[1:v]trim=start={transition_duration:.3f}:end={clip2_duration:.3f},"
                        "setpts=PTS-STARTPTS[vrem]"
                    ),
                    "[vintro][vrem]concat=n=2:v=1:a=0[v]",
                ]
            )
            video_label = "[v]"
        else:
            video_label = "[vintro]"

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(clip1_path),
            "-i",
            str(clip2_path),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            video_label,
            "-map",
            "1:a?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        success = run_ffmpeg_command(command).returncode == 0
        if success:
            logger.info("Applied transition effect: %s", output_path)
        return success

    except Exception as e:
        logger.error(f"Error applying transition effect: {e}")
        return False


def resize_for_916_filter(target_width: int, target_height: int) -> str:
    """Return a scale/crop filter that fills a target portrait frame."""
    return (
        f"scale={target_width}:{target_height}:force_original_aspect_ratio=increase:"
        f"flags=lanczos,crop={target_width}:{target_height},setsar=1"
    )


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
    cleanup_settings: Optional[Dict[str, Any]] = None,
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
        cleanup_settings,
    )


# Backward compatibility functions
def get_video_transcript_with_assemblyai(path: Path) -> str:
    """Backward compatibility wrapper."""
    return get_video_transcript(path)


def create_9_16_clip(
    video_path: Path,
    start_time: float,
    end_time: float,
    output_path: Path,
    subtitle_text: str = "",
) -> bool:
    """Backward compatibility wrapper."""
    return create_optimized_clip(
        video_path, start_time, end_time, output_path, add_subtitles=bool(subtitle_text)
    )


# B-Roll compositing functions


def insert_broll_into_clip(
    main_clip_path: Path,
    broll_path: Path,
    insert_time: float,
    broll_duration: float,
    output_path: Path,
    transition_duration: float = 0.3,
) -> bool:
    """
    Insert B-roll footage into a clip at a specified timestamp.

    Args:
        main_clip_path: Path to the main video clip
        broll_path: Path to the B-roll video
        insert_time: When to insert B-roll (seconds from clip start)
        broll_duration: How long to show B-roll (seconds)
        output_path: Where to save the composited clip
        transition_duration: Crossfade duration (seconds)

    Returns:
        True if successful
    """
    try:
        main_duration = ffprobe_duration(main_clip_path)
        source_broll_duration = ffprobe_duration(broll_path)
        target_width, target_height = ffprobe_video_size(main_clip_path)

        insert_time = max(0.0, min(insert_time, max(0.0, main_duration - 0.5)))
        actual_broll_duration = min(
            max(0.0, broll_duration),
            source_broll_duration,
            max(0.0, main_duration - insert_time),
        )
        if actual_broll_duration <= 0.05:
            logger.warning("B-roll duration is too short, skipping insertion")
            return False

        broll_end_time = insert_time + actual_broll_duration
        fade_duration = min(
            max(0.0, transition_duration),
            max(0.0, actual_broll_duration / 3),
        )

        filter_parts: List[str] = []
        concat_labels: List[str] = []
        segment_count = 0
        if insert_time > 0.05:
            filter_parts.append(
                f"[0:v]trim=start=0:end={insert_time:.3f},setpts=PTS-STARTPTS[vpre]"
            )
            concat_labels.append("[vpre]")
            segment_count += 1

        broll_filter = (
            f"[1:v]trim=start=0:end={actual_broll_duration:.3f},setpts=PTS-STARTPTS,"
            f"{resize_for_916_filter(target_width, target_height)}"
        )
        if fade_duration > 0:
            broll_filter += (
                f",fade=t=in:st=0:d={fade_duration:.3f},"
                f"fade=t=out:st={max(0.0, actual_broll_duration - fade_duration):.3f}:"
                f"d={fade_duration:.3f}"
            )
        filter_parts.append(f"{broll_filter}[vbroll]")
        concat_labels.append("[vbroll]")
        segment_count += 1

        if main_duration - broll_end_time > 0.05:
            filter_parts.append(
                f"[0:v]trim=start={broll_end_time:.3f}:end={main_duration:.3f},"
                "setpts=PTS-STARTPTS[vpost]"
            )
            concat_labels.append("[vpost]")
            segment_count += 1

        if segment_count > 1:
            filter_parts.append(
                f"{''.join(concat_labels)}concat=n={segment_count}:v=1:a=0[v]"
            )
            video_label = "[v]"
        else:
            video_label = concat_labels[0]

        command = [
            "ffmpeg",
            "-y",
            "-i",
            str(main_clip_path),
            "-i",
            str(broll_path),
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            video_label,
            "-map",
            "0:a?",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        if run_ffmpeg_command(command).returncode != 0:
            return False

        logger.info(
            f"Inserted B-roll at {insert_time:.1f}s ({actual_broll_duration:.1f}s duration): {output_path}"
        )
        return True

    except Exception as e:
        logger.error(f"Error inserting B-roll: {e}")
        return False


def apply_broll_to_clip(
    clip_path: Path, broll_suggestions: List[Dict[str, Any]], output_path: Path
) -> bool:
    """
    Apply multiple B-roll insertions to a clip.

    Args:
        clip_path: Path to the main clip
        broll_suggestions: List of B-roll suggestions with local_path, timestamp, duration
        output_path: Where to save the final clip

    Returns:
        True if successful
    """
    if not broll_suggestions:
        logger.info("No B-roll suggestions to apply")
        return False

    try:
        # Sort suggestions by timestamp (process from end to start to preserve timing)
        sorted_suggestions = sorted(
            broll_suggestions, key=lambda x: x.get("timestamp", 0), reverse=True
        )

        current_clip_path = clip_path
        temp_paths = []

        for i, suggestion in enumerate(sorted_suggestions):
            broll_path = suggestion.get("local_path")
            if not broll_path or not Path(broll_path).exists():
                logger.warning(f"B-roll file not found: {broll_path}")
                continue

            timestamp = suggestion.get("timestamp", 0)
            duration = suggestion.get("duration", 3.0)

            # Create temp output for intermediate clips
            if i < len(sorted_suggestions) - 1:
                temp_output = output_path.parent / f"temp_broll_{i}.mp4"
                temp_paths.append(temp_output)
            else:
                temp_output = output_path

            success = insert_broll_into_clip(
                current_clip_path, Path(broll_path), timestamp, duration, temp_output
            )

            if success:
                current_clip_path = temp_output
            else:
                logger.warning(f"Failed to insert B-roll at {timestamp}s")

        # Cleanup temp files
        for temp_path in temp_paths:
            if temp_path.exists() and temp_path != output_path:
                try:
                    temp_path.unlink()
                except Exception:
                    pass

        return True

    except Exception as e:
        logger.error(f"Error applying B-roll to clip: {e}")
        return False
