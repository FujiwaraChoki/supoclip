"""
Utility functions for video-related operations.
Optimized for MoviePy v2, AssemblyAI integration, and high-quality output.
"""

from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
import os
import logging
import numpy as np
from concurrent.futures import ThreadPoolExecutor
import json

import cv2
from moviepy import VideoFileClip, CompositeVideoClip, TextClip, ColorClip
from moviepy.video.fx import CrossFadeIn, CrossFadeOut, FadeIn, FadeOut

import assemblyai as aai
import srt
from datetime import timedelta

from .config import Config
from .caption_templates import get_template, CAPTION_TEMPLATES
from .font_registry import find_font_path

logger = logging.getLogger(__name__)
config = Config()
TRANSCRIPT_CACHE_SCHEMA_VERSION = 2


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


def get_video_transcript(video_path: Path, speech_model: str = "best") -> str:
    """Get transcript using AssemblyAI with word-level timing for precise subtitles."""
    logger.info(f"Getting transcript for: {video_path}")

    # Configure AssemblyAI
    aai.settings.api_key = config.assembly_ai_api_key

    # The SDK (0.42.x) sends speech_model (singular enum) but AssemblyAI's API now
    # requires speech_models (plural array). We patch api.create_transcript to inject it.
    import assemblyai.api as _aai_api
    import httpx as _httpx

    speech_model_value = "universal-3-pro"
    if speech_model == "nano":
        speech_model_value = "universal-2"

    _original_create_transcript = _aai_api.create_transcript

    def _patched_create_transcript(client, request):
        payload = request.dict(exclude_none=True, by_alias=True)
        payload.pop("speech_model", None)
        payload["speech_models"] = [speech_model_value]
        response = client.post(_aai_api.ENDPOINT_TRANSCRIPT, json=payload)
        if response.status_code != _httpx.codes.OK:
            raise aai.TranscriptError(
                f"failed to transcribe url {request.audio_url}: {response.text}",
                response.status_code,
            )
        return aai.types.TranscriptResponse.parse_obj(response.json())

    _aai_api.create_transcript = _patched_create_transcript

    config_obj = aai.TranscriptionConfig(
        speaker_labels=True,
        punctuate=True,
        format_text=True,
    )

    transcriber = aai.Transcriber()

    try:
        logger.info("Starting AssemblyAI transcription")
        transcript = transcriber.transcribe(str(video_path), config=config_obj)

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
    finally:
        _aai_api.create_transcript = _original_create_transcript


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


def get_scaled_font_size(base_font_size: int, video_width: int) -> int:
    """Scale caption font size by output width with sensible bounds."""
    scaled_size = int(base_font_size * (video_width / 720))
    return max(24, min(64, scaled_size))


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
    video_clip: VideoFileClip,
    start_time: float,
    end_time: float,
    target_ratio: float = 9 / 16,
) -> Tuple[int, int, int, int]:
    """Detect optimal static crop region (fallback — used when tracking fails)."""
    original_width, original_height = video_clip.size
    if original_width / original_height > target_ratio:
        new_width = round_to_even(int(original_height * target_ratio))
        new_height = round_to_even(original_height)
    else:
        new_width = round_to_even(original_width)
        new_height = round_to_even(int(original_width / target_ratio))

    x_offset = round_to_even((original_width - new_width) // 2 if original_width > new_width else 0)
    y_offset = round_to_even((original_height - new_height) // 2 if original_height > new_height else 0)
    return (x_offset, y_offset, new_width, new_height)


def _estimate_head_center(
    lm,
    PoseLandmark,
    fw: int,
    fh: int,
    face_result: Optional[Tuple[float, float, str]],
) -> Optional[Tuple[float, float, str]]:
    """
    Derive a normalized HEAD-CENTER anchor from MediaPipe Pose landmarks.

    All paths return a point at approximately head-center height so the Kalman
    filter receives geometrically consistent measurements regardless of pose.

    Priority:
      1. face_result set + nose fading → sigmoid-blend face↔shoulder head center
      2. Nose visible (>0.5)           → use nose as head proxy
      3. Ear midpoint visible (>0.3)   → reliable when back-turned
      4. Shoulder midpoint + offset    → head_y = sh_cy - (sh_width/2.2)*0.5
         Ratio 2.2 = shoulder_width / head_height for standing adult
    """
    import math

    nose  = lm[PoseLandmark.NOSE]
    l_ear = lm[PoseLandmark.LEFT_EAR]
    r_ear = lm[PoseLandmark.RIGHT_EAR]
    l_sh  = lm[PoseLandmark.LEFT_SHOULDER]
    r_sh  = lm[PoseLandmark.RIGHT_SHOULDER]
    l_hip = lm[PoseLandmark.LEFT_HIP]
    r_hip = lm[PoseLandmark.RIGHT_HIP]

    sh_vis  = (l_sh.visibility + r_sh.visibility) / 2
    ear_vis = (l_ear.visibility + r_ear.visibility) / 2

    sh_cx = ((l_sh.x + r_sh.x) / 2) * fw
    sh_cy = ((l_sh.y + r_sh.y) / 2) * fh

    # Anthropometric head height from shoulder width (biomechanics standard ratio)
    sh_width = abs(l_sh.x - r_sh.x) * fw
    head_h_est = sh_width / 2.2 if sh_width > 10 else fh * 0.12
    shoulder_head_cy = sh_cy - head_h_est * 0.5  # head center above shoulder line

    # Case 1: face detected — sigmoid-blend toward shoulder head center when fading
    if face_result is not None:
        face_vis = nose.visibility
        if face_vis < 0.7 and sh_vis > 0.25:
            alpha = 1.0 - (face_vis / 0.7)
            # Sigmoid: sharp transition, avoids mushy middle zone
            w_body = 1.0 / (1.0 + math.exp(-10.0 * (alpha - 0.5)))
            w_face = 1.0 - w_body
            bx, by, _ = face_result
            bx2 = w_face * bx + w_body * sh_cx
            by2 = w_face * by + w_body * shoulder_head_cy
            return (bx2, by2, "face+pose")
        return face_result  # face fully visible — no change needed

    # Case 2: nose visible
    if nose.visibility > 0.5:
        return (nose.x * fw, nose.y * fh, "pose-nose")

    # Case 3: ear midpoint — visibility-weighted (works when back-turned)
    if ear_vis > 0.3:
        total_ear_vis = l_ear.visibility + r_ear.visibility
        if total_ear_vis > 0:
            ear_cx = ((l_ear.x * l_ear.visibility + r_ear.x * r_ear.visibility) / total_ear_vis) * fw
            ear_cy = ((l_ear.y * l_ear.visibility + r_ear.y * r_ear.visibility) / total_ear_vis) * fh
        else:
            ear_cx = ((l_ear.x + r_ear.x) / 2) * fw
            ear_cy = ((l_ear.y + r_ear.y) / 2) * fh
        return (ear_cx, ear_cy, "pose-ear")

    # Case 4: torso center (4-point average: both shoulders + both hips).
    # When fully back-turned, hips are more stable than shoulders alone.
    # Weight hips more heavily (they're lower-body anchors less affected by arm motion).
    hip_vis = (l_hip.visibility + r_hip.visibility) / 2
    if sh_vis > 0.25 and hip_vis > 0.25:
        hip_cx = ((l_hip.x + r_hip.x) / 2) * fw
        hip_cy = ((l_hip.y + r_hip.y) / 2) * fh
        # Weighted torso center: hips 60%, shoulders 40% (hips more stable back-view)
        torso_cx = 0.4 * sh_cx + 0.6 * hip_cx
        torso_cy = 0.4 * ((l_sh.y + r_sh.y) / 2 * fh) + 0.6 * hip_cy
        # Head above torso by one head height (biomechanics: torso center ~ navel, head is ~2.5 heads above navel)
        head_cy = torso_cy - head_h_est * 2.5
        return (torso_cx, head_cy, "pose-shoulder")

    if sh_vis > 0.25:
        return (sh_cx, shoulder_head_cy, "pose-shoulder")

    # Hip-only fallback
    if hip_vis > 0.25:
        hip_cx = ((l_hip.x + r_hip.x) / 2) * fw
        hip_cy = ((l_hip.y + r_hip.y) / 2) * fh
        return (hip_cx, hip_cy - head_h_est * 2.0, "pose-hip")

    return None


def _make_kalman(fps: float) -> "cv2.KalmanFilter":
    """4-state constant-velocity Kalman filter: [x, y, vx, vy] → measure [x, y]."""
    dt = 1.0 / fps
    kf = cv2.KalmanFilter(4, 2)
    kf.transitionMatrix = np.array(
        [[1, 0, dt, 0],
         [0, 1,  0, dt],
         [0, 0,  1,  0],
         [0, 0,  0,  1]], dtype=np.float32
    )
    kf.measurementMatrix = np.array(
        [[1, 0, 0, 0],
         [0, 1, 0, 0]], dtype=np.float32
    )
    kf.processNoiseCov   = np.diag([1.0, 1.0, 0.5, 0.5]).astype(np.float32)
    kf.measurementNoiseCov = np.diag([25.0, 25.0]).astype(np.float32)
    kf.errorCovPost = np.eye(4, dtype=np.float32)
    kf.errorCovPost[2, 2] = 1000.0
    kf.errorCovPost[3, 3] = 1000.0
    kf.statePre = np.zeros((4, 1), dtype=np.float32)
    return kf


# Measurement noise (R) per detection source — tighter = more trust
_R_BY_SOURCE = {
    "face":           np.diag([25.0,   25.0]).astype(np.float32),
    "face+pose":      np.diag([200.0,  200.0]).astype(np.float32),
    "pose-nose":      np.diag([150.0,  150.0]).astype(np.float32),
    "pose-ear":       np.diag([300.0,  300.0]).astype(np.float32),
    "pose-shoulder":  np.diag([600.0,  600.0]).astype(np.float32),
    "pose-hip":       np.diag([900.0,  900.0]).astype(np.float32),
    "yolo-pose":      np.diag([400.0,  400.0]).astype(np.float32),
    "hog":            np.diag([1200.0, 1200.0]).astype(np.float32),
    "haar":           np.diag([1200.0, 1200.0]).astype(np.float32),
}
_Q_TRACKING = np.diag([1.0, 1.0, 0.3, 0.3]).astype(np.float32)
_Q_COASTING = np.diag([4.0, 4.0, 2.0, 2.0]).astype(np.float32)
_MAX_COAST_FRAMES = 20   # ~0.8 s at 25 fps before holding last known position

# --- Optical flow parameters for coast frames ---
_OF_WIN_SIZE   = (15, 15)
_OF_MAX_LEVEL  = 2
_OF_CRITERIA   = (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 10, 0.03)


def _detect_roi_features(
    gray: np.ndarray, cx: float, cy: float, roi_w: int = 120, roi_h: int = 180
) -> Optional[np.ndarray]:
    """Return Shi-Tomasi corners (shape N×1×2 float32) in ROI around (cx, cy)."""
    fh, fw = gray.shape[:2]
    x1 = max(0, int(cx) - roi_w // 2)
    y1 = max(0, int(cy) - roi_h // 2)
    x2 = min(fw, int(cx) + roi_w // 2)
    y2 = min(fh, int(cy) + roi_h // 2)
    roi = gray[y1:y2, x1:x2]
    if roi.size == 0:
        return None
    corners = cv2.goodFeaturesToTrack(
        roi, maxCorners=80, qualityLevel=0.01, minDistance=8, blockSize=7
    )
    if corners is None:
        return None
    corners[:, 0, 0] += x1
    corners[:, 0, 1] += y1
    return corners.astype(np.float32)


def _optical_flow_coast(
    old_gray: np.ndarray, new_gray: np.ndarray, p0: Optional[np.ndarray]
) -> Tuple[Optional[np.ndarray], float, float, bool]:
    """
    Estimate (dx, dy) motion via sparse Lucas-Kanade optical flow.
    Returns (updated_p0, dx, dy, success).
    """
    if p0 is None or len(p0) == 0 or old_gray is None:
        return None, 0.0, 0.0, False
    p1, st, _err = cv2.calcOpticalFlowPyrLK(
        old_gray, new_gray, p0, None,
        winSize=_OF_WIN_SIZE, maxLevel=_OF_MAX_LEVEL, criteria=_OF_CRITERIA
    )
    if p1 is None or st is None:
        return None, 0.0, 0.0, False
    good_new = p1[st == 1]
    good_old = p0[st == 1]
    if len(good_new) < max(3, int(len(p0) * 0.3)):
        return None, 0.0, 0.0, False
    disp = good_new[:, 0] - good_old[:, 0]
    dx_med = float(np.median(disp[:, 0]))
    dy_med = float(np.median(disp[:, 1]))
    # Reject gross outliers (> 2σ from median)
    dstd_x = float(np.std(disp[:, 0])) or 1.0
    dstd_y = float(np.std(disp[:, 1])) or 1.0
    valid = (
        (np.abs(disp[:, 0] - dx_med) < 2.0 * dstd_x) &
        (np.abs(disp[:, 1] - dy_med) < 2.0 * dstd_y)
    )
    if valid.sum() < 3:
        return None, 0.0, 0.0, False
    dx = float(np.mean(disp[valid, 0]))
    dy = float(np.mean(disp[valid, 1]))
    return good_new.reshape(-1, 1, 2).astype(np.float32), dx, dy, True


# --- YOLOv8-pose back-view fallback ---
_yolo_model = None  # lazy-loaded once on first use

def _get_yolo_model():
    """Lazy-load yolov8s-pose once per process."""
    global _yolo_model
    if _yolo_model is None:
        from ultralytics import YOLO
        _yolo_model = YOLO("yolov8s-pose.pt")
    return _yolo_model


def _yolo_head_center(
    frame_rgb: np.ndarray, fw: int, fh: int
) -> Optional[Tuple[float, float, str]]:
    """
    Run YOLOv8s-pose on frame_rgb and return (cx, cy, 'yolo-pose') for the
    most-confident person detection. Returns None if no person found.

    Uses COCO keypoint indices:
      5=left_shoulder, 6=right_shoulder, 11=left_hip, 12=right_hip
    Head center is estimated as torso center shifted up by 2 head-heights.
    """
    try:
        model = _get_yolo_model()
        # verbose=False suppresses per-frame YOLO logs
        results = model(frame_rgb, verbose=False)
        if not results or results[0].keypoints is None:
            return None
        kps = results[0].keypoints  # shape: (N_persons, 17, 2 or 3)
        if kps.xy is None or len(kps.xy) == 0:
            return None

        # Pick the person with highest box confidence
        boxes = results[0].boxes
        if boxes is not None and len(boxes.conf) > 0:
            best_idx = int(boxes.conf.argmax())
        else:
            best_idx = 0

        kp = kps.xy[best_idx]  # (17, 2) pixel coords
        # Confidence scores (0-1); available in kps.conf if present
        conf = kps.conf[best_idx] if kps.conf is not None else None

        def vis(idx):
            return float(conf[idx]) if conf is not None else 1.0

        l_sh, r_sh = kp[5], kp[6]
        l_hip, r_hip = kp[11], kp[12]

        sh_vis  = (vis(5) + vis(6)) / 2
        hip_vis = (vis(11) + vis(12)) / 2

        # Need at least one pair visible
        if sh_vis < 0.2 and hip_vis < 0.2:
            return None

        if sh_vis >= 0.2 and hip_vis >= 0.2:
            # Hip-weighted torso center (same logic as MediaPipe Case 4)
            sh_cx  = float((l_sh[0] + r_sh[0]) / 2)
            hip_cx = float((l_hip[0] + r_hip[0]) / 2)
            hip_cy = float((l_hip[1] + r_hip[1]) / 2)
            torso_cx = 0.4 * sh_cx + 0.6 * hip_cx
            torso_cy = 0.4 * float((l_sh[1] + r_sh[1]) / 2) + 0.6 * hip_cy
            sh_width = abs(float(l_sh[0]) - float(r_sh[0]))
            head_h = sh_width / 2.2 if sh_width > 10 else fh * 0.12
            return (torso_cx, torso_cy - head_h * 2.5, "yolo-pose")

        if sh_vis >= 0.2:
            sh_cx = float((l_sh[0] + r_sh[0]) / 2)
            sh_cy = float((l_sh[1] + r_sh[1]) / 2)
            sh_width = abs(float(l_sh[0]) - float(r_sh[0]))
            head_h = sh_width / 2.2 if sh_width > 10 else fh * 0.12
            return (sh_cx, sh_cy - head_h * 0.5, "yolo-pose")

        # Hip-only
        hip_cx = float((l_hip[0] + r_hip[0]) / 2)
        hip_cy = float((l_hip[1] + r_hip[1]) / 2)
        return (hip_cx, hip_cy - fh * 0.30, "yolo-pose")

    except Exception:
        return None


def build_face_tracking_clip(
    clip: VideoFileClip,
    original_width: int,
    original_height: int,
    crop_w: int,
    crop_h: int,
    start_time: float,
    end_time: float,
) -> VideoFileClip:
    """
    Hybrid face+body tracking crop for portrait (9:16) video.

    ALL anchors are normalized to HEAD CENTER before entering the Kalman filter,
    eliminating the Y-jump caused by switching between face-level and body-level
    anchor types.

    Detection priority (all return head-center coordinates):
      1. MediaPipe FaceDetection  — face bbox center
      2. MediaPipe Pose           — nose > ear midpoint > shoulder+anthropometric offset
                                    sigmoid-blended with face when partially occluded
      3. OpenCV HOG               — full-body bbox upper quarter
      4. Haar cascade             — frontal face fallback

    Smoothing: per-frame Kalman filter (constant-velocity, 4-state).
      - Variable R (measurementNoiseCov) by source confidence
      - Coasting (predict-only, inflated Q) up to 20 frames on missed detection
      - After 20 coasted frames: hold last known position
    """
    duration = end_time - start_time
    fps = clip.fps or 25.0
    # Shift crop down so head sits ~15% from top rather than dead-centre
    top_offset = crop_h * 0.15

    centre_x = original_width / 2.0
    centre_y = original_height / 2.0

    # --- Init detectors ---
    mp_face_det = None
    mp_pose_det = None
    PoseLandmark = None
    try:
        import mediapipe as mp
        mp_face_det = mp.solutions.face_detection.FaceDetection(
            model_selection=1, min_detection_confidence=0.4
        )
        mp_pose_det = mp.solutions.pose.Pose(
            static_image_mode=False,
            model_complexity=2,
            smooth_landmarks=True,
            min_detection_confidence=0.4,
            min_tracking_confidence=0.4,
        )
        PoseLandmark = mp.solutions.pose.PoseLandmark
    except Exception:
        pass

    hog = cv2.HOGDescriptor()
    hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    haar = cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )

    # --- Frame index array ---
    total_frames = int(duration * fps) + 2
    frame_times = np.linspace(0.0, duration, total_frames)

    # Sample every Nth frame for detection (~15 detections/sec max)
    sample_interval = max(1, int(fps / 15))

    # detections[fi] = (cx, cy, source) or None
    detections: List[Optional[Tuple[float, float, str]]] = [None] * total_frames

    n_face = n_pose = n_hog = n_haar = 0

    for fi in range(0, total_frames, sample_interval):
        t = float(frame_times[fi])
        try:
            frame = clip.get_frame(t)
            fh, fw = frame.shape[:2]
            rgb = np.ascontiguousarray(frame)
            result = None

            # 1. MediaPipe face detection
            if mp_face_det is not None:
                try:
                    res = mp_face_det.process(rgb)
                    if res.detections:
                        best = max(res.detections, key=lambda d: d.score[0])
                        bbox = best.location_data.relative_bounding_box
                        fx = (bbox.xmin + bbox.width / 2) * fw
                        fy = (bbox.ymin + bbox.height / 2) * fh
                        if 0 <= fx < fw and 0 <= fy < fh:
                            result = (fx, fy, "face")
                            n_face += 1
                except Exception:
                    pass

            # 2. MediaPipe Pose — normalize to head center
            if mp_pose_det is not None and PoseLandmark is not None:
                try:
                    pose_res = mp_pose_det.process(rgb)
                    if pose_res.pose_landmarks:
                        lm = pose_res.pose_landmarks.landmark
                        head = _estimate_head_center(lm, PoseLandmark, fw, fh, result)
                        if head is not None:
                            prev_source = result[2] if result else None
                            if head[2] != prev_source:
                                if result is None:
                                    n_pose += 1
                            result = head
                except Exception:
                    pass

            # 2b. YOLOv8-pose — back-view fallback when MediaPipe has low confidence
            # Triggers when no detection yet, or MediaPipe fell back to noisy shoulder/hip estimate
            _low_conf_sources = {"pose-shoulder", "pose-hip", None}
            if result is None or result[2] in _low_conf_sources:
                try:
                    yolo_head = _yolo_head_center(rgb, fw, fh)
                    if yolo_head is not None:
                        if result is None:
                            n_pose += 1
                        result = yolo_head
                except Exception:
                    pass

            # 3. HOG person detector
            if result is None:
                try:
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    scale = min(1.0, 480 / fh)
                    small = cv2.resize(frame_bgr, (int(fw * scale), int(fh * scale)))
                    boxes, weights = hog.detectMultiScale(
                        small, winStride=(8, 8), padding=(4, 4), scale=1.05
                    )
                    if len(boxes) > 0:
                        best_idx = int(np.argmax(weights))
                        hx, hy, hw, hh = boxes[best_idx]
                        px = (hx + hw / 2) / scale
                        py = (hy + hh * 0.25) / scale
                        result = (px, py, "hog")
                        n_hog += 1
                except Exception:
                    pass

            # 4. Haar fallback
            if result is None:
                try:
                    frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
                    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
                    faces = haar.detectMultiScale(
                        gray, scaleFactor=1.05, minNeighbors=3, minSize=(30, 30)
                    )
                    if len(faces) > 0:
                        x, y, bw, bh = max(faces, key=lambda f: f[2] * f[3])
                        result = (float(x + bw // 2), float(y + bh // 2), "haar")
                        n_haar += 1
                except Exception:
                    pass

            detections[fi] = result

        except Exception:
            detections[fi] = None

    if mp_face_det is not None:
        mp_face_det.close()
    if mp_pose_det is not None:
        mp_pose_det.close()

    n_detected = sum(1 for d in detections if d is not None)
    logger.info(
        f"Detection results: face={n_face} pose={n_pose} hog={n_hog} haar={n_haar} "
        f"total={n_detected}/{total_frames} (sampled every {sample_interval} frames)"
    )

    # --- Kalman filter pass over all frames ---
    kf = _make_kalman(fps)

    first_det = next((d for d in detections if d is not None), None)
    init_x = first_det[0] if first_det else centre_x
    init_y = first_det[1] if first_det else centre_y
    kf.statePre  = np.array([[init_x], [init_y], [0.0], [0.0]], dtype=np.float32)
    kf.statePost = kf.statePre.copy()

    x_offs = np.zeros(total_frames, dtype=np.int32)
    y_offs = np.zeros(total_frames, dtype=np.int32)

    coast_count = 0
    last_cx, last_cy = init_x, init_y
    of_gray_prev: Optional[np.ndarray] = None    # grayscale of prev frame for optical flow
    of_p0: Optional[np.ndarray] = None           # tracked feature points
    of_refresh_every = max(1, int(fps // 3))     # refresh features every ~0.33 s

    for fi in range(total_frames):
        # Fill detection gap: use nearest sampled detection within window
        det = detections[fi]
        if det is None:
            lo = max(0, fi - sample_interval + 1)
            hi = min(total_frames - 1, fi + sample_interval - 1)
            for ni in range(lo, hi + 1):
                if detections[ni] is not None:
                    det = detections[ni]
                    break

        kf.processNoiseCov = _Q_TRACKING if det is not None else _Q_COASTING
        pred = kf.predict()
        pred_x, pred_y = float(pred[0]), float(pred[1])

        if det is not None:
            kf.measurementNoiseCov = _R_BY_SOURCE.get(det[2], _R_BY_SOURCE["pose-shoulder"])
            meas = np.array([[det[0]], [det[1]]], dtype=np.float32)
            corrected = kf.correct(meas)
            cx, cy = float(corrected[0]), float(corrected[1])
            # Clamp per-frame velocity to prevent large jumps on source switch
            dx, dy = cx - last_cx, cy - last_cy
            max_jump = 30  # pixels per frame
            if abs(dx) > max_jump or abs(dy) > max_jump:
                scale = max_jump / max(abs(dx), abs(dy), 1e-6)
                cx = last_cx + dx * scale
                cy = last_cy + dy * scale
            last_cx, last_cy = cx, cy
            coast_count = 0
            # Refresh optical flow features on detection (or every of_refresh_every frames)
            try:
                t = float(frame_times[fi])
                frame = clip.get_frame(t)
                of_gray_prev = cv2.cvtColor(
                    np.ascontiguousarray(frame), cv2.COLOR_RGB2GRAY
                )
                if of_p0 is None or fi % of_refresh_every == 0:
                    of_p0 = _detect_roi_features(of_gray_prev, cx, cy)
            except Exception:
                of_gray_prev = None
                of_p0 = None
        else:
            coast_count += 1
            if coast_count <= _MAX_COAST_FRAMES:
                # Try optical flow to refine predicted position
                try:
                    t = float(frame_times[fi])
                    frame = clip.get_frame(t)
                    new_gray = cv2.cvtColor(
                        np.ascontiguousarray(frame), cv2.COLOR_RGB2GRAY
                    )
                    updated_p0, fdx, fdy, ok = _optical_flow_coast(
                        of_gray_prev, new_gray, of_p0
                    )
                    if ok:
                        # Correct Kalman with optical-flow-derived synthetic measurement
                        kf.measurementNoiseCov = np.diag([400.0, 400.0]).astype(np.float32)
                        synth = np.array([[pred_x + fdx], [pred_y + fdy]], dtype=np.float32)
                        corrected = kf.correct(synth)
                        cx, cy = float(corrected[0]), float(corrected[1])
                        of_p0 = updated_p0
                        # Refresh features if running low
                        if of_p0 is None or len(of_p0) < 10:
                            of_p0 = _detect_roi_features(new_gray, cx, cy)
                    else:
                        # Optical flow failed: use Kalman prediction
                        cx, cy = pred_x, pred_y
                        of_p0 = _detect_roi_features(new_gray, last_cx, last_cy)
                    of_gray_prev = new_gray
                except Exception:
                    cx, cy = pred_x, pred_y
                # Clamp velocity on coast frames too
                dxc, dyc = cx - last_cx, cy - last_cy
                max_coast_jump = 20
                if abs(dxc) > max_coast_jump or abs(dyc) > max_coast_jump:
                    s = max_coast_jump / max(abs(dxc), abs(dyc), 1e-6)
                    cx = last_cx + dxc * s
                    cy = last_cy + dyc * s
            else:
                # Beyond coast limit: hold last known position (no optical flow drift)
                cx, cy = last_cx, last_cy
            last_cx, last_cy = cx, cy

        # Shift crop down so head sits near top of frame
        cy_adj = cy + top_offset

        cx     = float(np.clip(cx,     crop_w / 2, original_width  - crop_w / 2))
        cy_adj = float(np.clip(cy_adj, crop_h / 2, original_height - crop_h / 2))

        xo = int(np.clip(cx     - crop_w / 2, 0, original_width  - crop_w))
        yo = int(np.clip(cy_adj - crop_h / 2, 0, original_height - crop_h))
        x_offs[fi] = (xo // 2) * 2
        y_offs[fi] = (yo // 2) * 2

    # --- Post-Kalman stabilization pass (dead-zone + velocity clamp + EMA) ---
    _dead_zone    = 10    # px: ignore movements smaller than this
    _max_velocity = 8     # px/frame: max horizontal crop shift per frame
    _ema_alpha    = 0.2   # EMA weight for current frame
    prev_x   = float(x_offs[0])
    smooth_x = float(x_offs[0])
    for fi in range(total_frames):
        raw_x = float(x_offs[fi])
        delta = raw_x - prev_x
        # Dead-zone: only move if beyond threshold
        if abs(delta) < _dead_zone:
            clamped_x = prev_x
        else:
            # Velocity clamp
            clamped_delta = max(-_max_velocity, min(_max_velocity, delta))
            clamped_x = prev_x + clamped_delta
        # EMA smoothing
        smooth_x = _ema_alpha * clamped_x + (1.0 - _ema_alpha) * smooth_x
        prev_x = clamped_x
        final_x = int(np.clip(round(smooth_x), 0, original_width - crop_w))
        x_offs[fi] = (final_x // 2) * 2

    def apply_crop(get_frame, t):
        frame = get_frame(t)
        fi = min(int(t * fps + 0.5), total_frames - 1)
        xo, yo = x_offs[fi], y_offs[fi]
        return frame[yo:yo + crop_h, xo:xo + crop_w]

    tracked = clip.transform(apply_crop)
    logger.info(
        f"Kalman tracking complete: {n_detected}/{total_frames} detections, "
        f"coast_max={_MAX_COAST_FRAMES} frames"
    )
    return tracked


def detect_faces_in_clip(
    video_clip: VideoFileClip, start_time: float, end_time: float
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

        for sample_time in sample_times:
            try:
                frame = video_clip.get_frame(sample_time)
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
                        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
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
                        frame_bgr = cv2.cvtColor(frame, cv2.COLOR_RGB2BGR)
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


def create_assemblyai_subtitles(
    video_path: Path,
    clip_start: float,
    clip_end: float,
    video_width: int,
    video_height: int,
    font_family: str = "THEBOLDFONT",
    font_size: int = 24,
    font_color: str = "#FFFFFF",
    caption_template: str = "default",
) -> List[TextClip]:
    """Create subtitles using AssemblyAI's precise word timing with template support."""
    transcript_data = load_cached_transcript_data(video_path)

    if not transcript_data or not transcript_data.get("words"):
        logger.warning("No cached transcript data available for subtitles")
        return []

    # Get template settings
    template = get_template(caption_template)
    animation_type = template.get("animation", "none")

    effective_font_family = font_family or template["font_family"]
    effective_font_size = int(font_size) if font_size else int(template["font_size"])
    effective_font_color = font_color or template["font_color"]
    effective_template = {
        **template,
        "font_size": effective_font_size,
        "font_color": effective_font_color,
        "font_family": effective_font_family,
    }

    logger.info(
        f"Creating subtitles with template '{caption_template}', animation: {animation_type}"
    )

    # Get words in range
    relevant_words = get_words_in_range(transcript_data, clip_start, clip_end)

    if not relevant_words:
        logger.warning("No words found in clip timerange")
        return []

    # Choose subtitle creation method based on animation type
    if animation_type == "karaoke":
        return create_karaoke_subtitles(
            relevant_words,
            video_width,
            video_height,
            effective_template,
            effective_font_family,
        )
    elif animation_type == "pop":
        return create_pop_subtitles(
            relevant_words,
            video_width,
            video_height,
            effective_template,
            effective_font_family,
        )
    elif animation_type == "fade":
        return create_fade_subtitles(
            relevant_words,
            video_width,
            video_height,
            effective_template,
            effective_font_family,
        )
    else:
        # Default static subtitles
        return create_static_subtitles(
            relevant_words,
            video_width,
            video_height,
            effective_template,
            effective_font_family,
        )


def create_static_subtitles(
    relevant_words: List[Dict],
    video_width: int,
    video_height: int,
    template: Dict,
    font_family: str,
) -> List[TextClip]:
    """Create standard static subtitles (original behavior)."""
    subtitle_clips = []
    processor = VideoProcessor(
        font_family, template["font_size"], template["font_color"]
    )

    calculated_font_size = get_scaled_font_size(template["font_size"], video_width)
    position_y = template.get("position_y", 0.75)
    max_text_width = get_subtitle_max_width(video_width)

    words_per_subtitle = 3
    for i in range(0, len(relevant_words), words_per_subtitle):
        word_group = relevant_words[i : i + words_per_subtitle]
        if not word_group:
            continue

        segment_start = word_group[0]["start"]
        segment_end = word_group[-1]["end"]
        segment_duration = segment_end - segment_start

        if segment_duration < 0.1:
            continue

        text = " ".join(word["text"] for word in word_group)

        try:
            stroke_color = template.get("stroke_color", "black")
            stroke_width = template.get("stroke_width", 1)

            text_clip = (
                TextClip(
                    text=text,
                    font=processor.font_path,
                    font_size=calculated_font_size,
                    color=template["font_color"],
                    stroke_color=stroke_color if stroke_color else None,
                    stroke_width=stroke_width if stroke_color else 0,
                    method="caption",
                    size=(max_text_width, None),
                    text_align="center",
                    interline=6,
                )
                .with_duration(segment_duration)
                .with_start(segment_start)
            )

            text_height = text_clip.size[1] if text_clip.size else 40
            vertical_position = get_safe_vertical_position(
                video_height, text_height, position_y
            )
            text_clip = text_clip.with_position(("center", vertical_position))

            subtitle_clips.append(text_clip)

        except Exception as e:
            logger.warning(f"Failed to create subtitle for '{text}': {e}")
            continue

    logger.info(f"Created {len(subtitle_clips)} static subtitle elements")
    return subtitle_clips


def create_karaoke_subtitles(
    relevant_words: List[Dict],
    video_width: int,
    video_height: int,
    template: Dict,
    font_family: str,
) -> List[TextClip]:
    """Create karaoke-style subtitles with word-by-word highlighting."""
    subtitle_clips = []
    processor = VideoProcessor(
        font_family, template["font_size"], template["font_color"]
    )

    calculated_font_size = get_scaled_font_size(template["font_size"], video_width)
    position_y = template.get("position_y", 0.75)
    highlight_color = template.get("highlight_color", "#FFD700")
    normal_color = template["font_color"]
    max_text_width = get_subtitle_max_width(video_width)
    horizontal_padding = max(40, int(video_width * 0.06))

    words_per_group = 3

    def measure_word_group_width(word_group: List[Dict], font_size: int) -> List[int]:
        widths: List[int] = []
        for word in word_group:
            temp_clip = TextClip(
                text=word["text"],
                font=processor.font_path,
                font_size=font_size,
                color=normal_color,
                stroke_color=template.get("stroke_color", "black"),
                stroke_width=template.get("stroke_width", 1),
                method="label",
            )
            widths.append(temp_clip.size[0] if temp_clip.size else 50)
            temp_clip.close()
        return widths

    for group_idx in range(0, len(relevant_words), words_per_group):
        word_group = relevant_words[group_idx : group_idx + words_per_group]
        if not word_group:
            continue

        group_start = word_group[0]["start"]
        group_end = word_group[-1]["end"]

        # For each word in the group, create a highlighted version
        for word_idx, current_word in enumerate(word_group):
            word_start = current_word["start"]
            word_end = current_word["end"]
            word_duration = word_end - word_start

            if word_duration < 0.05:
                continue

            try:
                # Build the text with the current word highlighted
                # We create individual text clips for each word and composite them
                word_clips_for_composite = []
                font_size_for_group = calculated_font_size
                word_widths = measure_word_group_width(word_group, font_size_for_group)
                space_width = font_size_for_group * 0.28
                total_width = sum(word_widths) + space_width * (len(word_group) - 1)

                if total_width > max_text_width and total_width > 0:
                    shrink_ratio = max_text_width / total_width
                    font_size_for_group = max(
                        20, int(font_size_for_group * shrink_ratio)
                    )
                    word_widths = measure_word_group_width(
                        word_group, font_size_for_group
                    )
                    space_width = font_size_for_group * 0.28
                    total_width = sum(word_widths) + space_width * (len(word_group) - 1)

                # Second pass: create positioned clips
                current_x = max(horizontal_padding, (video_width - total_width) / 2)
                text_height = 40

                for w_idx, word in enumerate(word_group):
                    is_current = w_idx == word_idx
                    color = highlight_color if is_current else normal_color
                    # Scale up current word slightly for pop effect
                    size_multiplier = 1.1 if is_current else 1.0

                    word_clip = (
                        TextClip(
                            text=word["text"],
                            font=processor.font_path,
                            font_size=int(font_size_for_group * size_multiplier),
                            color=color,
                            stroke_color=template.get("stroke_color", "black"),
                            stroke_width=template.get("stroke_width", 1),
                            method="label",
                        )
                        .with_duration(word_duration)
                        .with_start(word_start)
                    )

                    text_height = max(
                        text_height, word_clip.size[1] if word_clip.size else 40
                    )
                    vertical_position = get_safe_vertical_position(
                        video_height, text_height, position_y
                    )

                    word_clip = word_clip.with_position(
                        (int(current_x), vertical_position)
                    )
                    word_clips_for_composite.append(word_clip)

                    current_x += word_widths[w_idx] + space_width

                subtitle_clips.extend(word_clips_for_composite)

            except Exception as e:
                logger.warning(
                    f"Failed to create karaoke subtitle for word '{current_word['text']}': {e}"
                )
                continue

    logger.info(f"Created {len(subtitle_clips)} karaoke subtitle elements")
    return subtitle_clips


def create_pop_subtitles(
    relevant_words: List[Dict],
    video_width: int,
    video_height: int,
    template: Dict,
    font_family: str,
) -> List[TextClip]:
    """Create pop-style subtitles where each word pops in."""
    subtitle_clips = []
    processor = VideoProcessor(
        font_family, template["font_size"], template["font_color"]
    )

    calculated_font_size = get_scaled_font_size(template["font_size"], video_width)
    position_y = template.get("position_y", 0.75)
    max_text_width = get_subtitle_max_width(video_width)

    words_per_group = 3

    for group_idx in range(0, len(relevant_words), words_per_group):
        word_group = relevant_words[group_idx : group_idx + words_per_group]
        if not word_group:
            continue

        # Show the full group text
        group_text = " ".join(w["text"] for w in word_group)
        group_start = word_group[0]["start"]
        group_end = word_group[-1]["end"]
        group_duration = group_end - group_start

        if group_duration < 0.1:
            continue

        try:
            # Create main text clip
            text_clip = (
                TextClip(
                    text=group_text,
                    font=processor.font_path,
                    font_size=calculated_font_size,
                    color=template["font_color"],
                    stroke_color=template.get("stroke_color", "black"),
                    stroke_width=template.get("stroke_width", 2),
                    method="caption",
                    size=(max_text_width, None),
                    text_align="center",
                    interline=6,
                )
                .with_duration(group_duration)
                .with_start(group_start)
            )

            text_height = text_clip.size[1] if text_clip.size else 40
            vertical_position = get_safe_vertical_position(
                video_height, text_height, position_y
            )
            text_clip = text_clip.with_position(("center", vertical_position))

            subtitle_clips.append(text_clip)

        except Exception as e:
            logger.warning(f"Failed to create pop subtitle: {e}")
            continue

    logger.info(f"Created {len(subtitle_clips)} pop subtitle elements")
    return subtitle_clips


def create_fade_subtitles(
    relevant_words: List[Dict],
    video_width: int,
    video_height: int,
    template: Dict,
    font_family: str,
) -> List[TextClip]:
    """Create fade-style subtitles with smooth transitions."""
    subtitle_clips = []
    processor = VideoProcessor(
        font_family, template["font_size"], template["font_color"]
    )

    calculated_font_size = get_scaled_font_size(template["font_size"], video_width)
    position_y = template.get("position_y", 0.75)
    has_background = template.get("background", False)
    background_color = template.get("background_color", "#00000080")
    max_text_width = get_subtitle_max_width(video_width)

    words_per_group = 4

    for group_idx in range(0, len(relevant_words), words_per_group):
        word_group = relevant_words[group_idx : group_idx + words_per_group]
        if not word_group:
            continue

        group_text = " ".join(w["text"] for w in word_group)
        group_start = word_group[0]["start"]
        group_end = word_group[-1]["end"]
        group_duration = group_end - group_start

        if group_duration < 0.1:
            continue

        try:
            # Create text clip
            text_clip = TextClip(
                text=group_text,
                font=processor.font_path,
                font_size=calculated_font_size,
                color=template["font_color"],
                stroke_color=template.get("stroke_color")
                if template.get("stroke_color")
                else None,
                stroke_width=template.get("stroke_width", 0),
                method="caption",
                size=(max_text_width, None),
                text_align="center",
                interline=6,
            )

            text_height = text_clip.size[1] if text_clip.size else 40
            text_width = text_clip.size[0] if text_clip.size else 200
            vertical_position = get_safe_vertical_position(
                video_height, text_height, position_y
            )

            # Add background if specified
            if has_background and background_color:
                padding = 10
                # Parse background color (handle alpha)
                bg_color_hex = (
                    background_color[:7]
                    if len(background_color) > 7
                    else background_color
                )

                bg_clip = (
                    ColorClip(
                        size=(text_width + padding * 2, text_height + padding),
                        color=tuple(
                            int(bg_color_hex[i : i + 2], 16) for i in (1, 3, 5)
                        ),
                    )
                    .with_duration(group_duration)
                    .with_start(group_start)
                )

                bg_clip = bg_clip.with_position(
                    ("center", vertical_position - padding // 2)
                )

                # Apply fade to background
                fade_duration = min(0.2, group_duration / 4)
                bg_clip = (
                    bg_clip.with_effects(
                        [CrossFadeIn(fade_duration), CrossFadeOut(fade_duration)]
                    )
                    if group_duration > 0.5
                    else bg_clip
                )

                subtitle_clips.append(bg_clip)

            # Apply timing and position to text
            text_clip = text_clip.with_duration(group_duration).with_start(group_start)
            text_clip = text_clip.with_position(("center", vertical_position))

            subtitle_clips.append(text_clip)

        except Exception as e:
            logger.warning(f"Failed to create fade subtitle: {e}")
            continue

    logger.info(f"Created {len(subtitle_clips)} fade subtitle elements")
    return subtitle_clips


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
) -> bool:
    """Create clip with optional subtitles. output_format: 'vertical' (9:16) or 'original' (keep source size)."""
    try:
        duration = end_time - start_time
        if duration <= 0:
            logger.error(f"Invalid clip duration: {duration:.1f}s")
            return False

        keep_original = output_format == "original"
        logger.info(
            f"Creating clip: {start_time:.1f}s - {end_time:.1f}s ({duration:.1f}s) "
            f"subtitles={add_subtitles} template '{caption_template}' format={'original' if keep_original else 'vertical'}"
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

        # Load and process video
        video = VideoFileClip(str(video_path))

        if start_time >= video.duration:
            logger.error(
                f"Start time {start_time}s exceeds video duration {video.duration:.1f}s"
            )
            video.close()
            return False

        end_time = min(end_time, video.duration)
        clip = video.subclipped(start_time, end_time)

        if keep_original:
            # No face detection, no crop, no resize - use trimmed clip as-is
            processed_clip = clip
            target_width = round_to_even(processed_clip.w)
            target_height = round_to_even(processed_clip.h)
            if (target_width, target_height) != (processed_clip.w, processed_clip.h):
                processed_clip = processed_clip.resized((target_width, target_height))
            cropped_clip = None
        else:
            # Vertical 9:16: dynamic face-tracking crop
            original_width, original_height = video.size
            target_ratio = 9 / 16
            if original_width / original_height > target_ratio:
                crop_w = round_to_even(int(original_height * target_ratio))
                crop_h = round_to_even(original_height)
            else:
                crop_w = round_to_even(original_width)
                crop_h = round_to_even(int(original_width / target_ratio))

            try:
                cropped_clip = build_face_tracking_clip(
                    clip, original_width, original_height,
                    crop_w, crop_h, start_time, end_time,
                )
            except Exception as e:
                logger.warning(f"Face tracking failed, falling back to static crop: {e}")
                x_offset, y_offset, crop_w, crop_h = detect_optimal_crop_region(
                    video, start_time, end_time, target_ratio=9 / 16
                )
                cropped_clip = clip.cropped(
                    x1=x_offset, y1=y_offset,
                    x2=x_offset + crop_w, y2=y_offset + crop_h,
                )

            target_width, target_height = crop_w, crop_h
            processed_clip = cropped_clip

        # Add AssemblyAI subtitles with template support
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
            )
            final_clips.extend(subtitle_clips)

        # Compose and encode
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

        # Cleanup
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
            # Debug log the segment data
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
                    # Include virality data if available
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
    clip1 = None
    clip2 = None
    transition = None
    clip1_tail = None
    clip2_intro = None
    clip2_remainder = None
    intro_segment = None
    final_clip = None

    try:
        from moviepy import VideoFileClip, CompositeVideoClip, concatenate_videoclips

        # Load clips
        clip1 = VideoFileClip(str(clip1_path))
        clip2 = VideoFileClip(str(clip2_path))
        transition = VideoFileClip(str(transition_path))

        # Keep the transition window within both clips so the output still matches
        # the current clip's duration and metadata.
        transition_duration = min(1.5, transition.duration, clip1.duration, clip2.duration)
        if transition_duration <= 0:
            logger.warning("Transition duration is zero, skipping transition effect")
            return False

        transition = transition.subclipped(0, transition_duration)

        # Resize transition to match clip dimensions
        clip_size = clip2.size
        transition = transition.resized(clip_size)

        # Build a transition intro from the previous clip tail over the first
        # part of the current clip so the exported file keeps clip2's duration.
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

        # Write output
        processor = VideoProcessor()
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
        from moviepy import VideoFileClip, CompositeVideoClip, concatenate_videoclips
        from moviepy.video.fx import CrossFadeIn, CrossFadeOut

        # Load clips
        main_clip = VideoFileClip(str(main_clip_path))
        broll_clip = VideoFileClip(str(broll_path))

        # Get main clip dimensions
        target_width, target_height = main_clip.size

        # Resize B-roll to match main clip (9:16 aspect ratio)
        broll_resized = resize_for_916(broll_clip, target_width, target_height)

        # Ensure B-roll doesn't exceed requested duration
        actual_broll_duration = min(broll_duration, broll_resized.duration)
        broll_trimmed = broll_resized.subclipped(0, actual_broll_duration)

        # Ensure insert_time is within clip bounds
        insert_time = max(0, min(insert_time, main_clip.duration - 0.5))

        # Calculate end time for B-roll
        broll_end_time = insert_time + actual_broll_duration

        # Don't let B-roll extend past the main clip
        if broll_end_time > main_clip.duration:
            broll_end_time = main_clip.duration
            actual_broll_duration = broll_end_time - insert_time
            broll_trimmed = broll_resized.subclipped(0, actual_broll_duration)

        # Split main clip into three parts
        part1 = main_clip.subclipped(0, insert_time) if insert_time > 0 else None
        part2_audio = main_clip.subclipped(insert_time, broll_end_time).audio
        part3 = (
            main_clip.subclipped(broll_end_time)
            if broll_end_time < main_clip.duration
            else None
        )

        # Apply crossfade to B-roll
        if transition_duration > 0:
            broll_with_audio = broll_trimmed.with_audio(part2_audio)
            broll_faded = broll_with_audio.with_effects(
                [CrossFadeIn(transition_duration), CrossFadeOut(transition_duration)]
            )
        else:
            broll_faded = broll_trimmed.with_audio(part2_audio)

        # Concatenate parts
        clips_to_concat = []
        if part1:
            clips_to_concat.append(part1)
        clips_to_concat.append(broll_faded)
        if part3:
            clips_to_concat.append(part3)

        if len(clips_to_concat) == 1:
            final_clip = clips_to_concat[0]
        else:
            final_clip = concatenate_videoclips(clips_to_concat, method="compose")

        # Write output
        processor = VideoProcessor()
        encoding_settings = processor.get_optimal_encoding_settings("high")

        final_clip.write_videofile(
            str(output_path),
            temp_audiofile="temp-audio-broll.m4a",
            remove_temp=True,
            logger=None,
            **encoding_settings,
        )

        # Cleanup
        final_clip.close()
        main_clip.close()
        broll_clip.close()
        broll_resized.close()

        logger.info(
            f"Inserted B-roll at {insert_time:.1f}s ({actual_broll_duration:.1f}s duration): {output_path}"
        )
        return True

    except Exception as e:
        logger.error(f"Error inserting B-roll: {e}")
        return False


def resize_for_916(
    clip: VideoFileClip, target_width: int, target_height: int
) -> VideoFileClip:
    """
    Resize a video clip to fit 9:16 aspect ratio with center crop.

    Args:
        clip: Input video clip
        target_width: Target width
        target_height: Target height

    Returns:
        Resized video clip
    """
    clip_width, clip_height = clip.size
    target_aspect = target_width / target_height
    clip_aspect = clip_width / clip_height

    if clip_aspect > target_aspect:
        # Clip is wider - scale to height and crop width
        scale_factor = target_height / clip_height
        new_width = int(clip_width * scale_factor)
        new_height = target_height
        resized = clip.resized((new_width, new_height))

        # Center crop
        x_offset = (new_width - target_width) // 2
        cropped = resized.cropped(x1=x_offset, x2=x_offset + target_width)
    else:
        # Clip is taller - scale to width and crop height
        scale_factor = target_width / clip_width
        new_width = target_width
        new_height = int(clip_height * scale_factor)
        resized = clip.resized((new_width, new_height))

        # Center crop (crop from top for portrait videos)
        y_offset = (new_height - target_height) // 4  # Bias towards top
        cropped = resized.cropped(y1=y_offset, y2=y_offset + target_height)

    return cropped


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
