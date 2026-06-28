"""
Pattern-based clip generation service.

Detects visual patterns in a video by comparing frames against a user-provided
reference image using OpenCV template matching and ORB feature matching.
Generates clips around each match (configurable window, default ±60s).
"""

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

UPLOAD_URL_PREFIX = "upload://"


@dataclass
class PatternMatch:
    """Represents a single detected pattern match in the video."""

    timestamp: float  # seconds into the video
    score: float  # combined similarity score 0.0-1.0
    method: str  # "template", "orb", or "combined"


@dataclass
class PatternMatchResult:
    """Result of pattern detection for a video."""

    matches: List[PatternMatch] = field(default_factory=list)
    total_frames_checked: int = 0
    match_count: int = 0


def _resolve_reference_image_path(reference_image_path: str) -> Path:
    """Resolve the reference image path from various formats."""
    if reference_image_path.startswith(UPLOAD_URL_PREFIX):
        filename = Path(reference_image_path.removeprefix(UPLOAD_URL_PREFIX)).name
        from ..config import get_config

        return Path(get_config().temp_dir) / "uploads" / filename
    return Path(reference_image_path)


def extract_video_frames(
    video_path: Path,
    output_dir: Path,
    interval_seconds: int = 2,
) -> List[Tuple[Path, float]]:
    """
    Extract frames from a video at regular intervals using ffmpeg.

    Returns list of (frame_path, timestamp_seconds) tuples.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get video duration first
    duration = _get_video_duration(video_path)
    if duration is None or duration <= 0:
        logger.error(f"Could not determine video duration: {video_path}")
        return []

    logger.info(
        f"Extracting frames every {interval_seconds}s from {duration:.1f}s video"
    )

    # Use ffmpeg to extract frames at interval
    cmd = [
        "ffmpeg",
        "-i",
        str(video_path),
        "-vf",
        f"fps=1/{interval_seconds}",
        "-q:v",
        "2",  # JPEG quality
        "-y",
        str(output_dir / "frame_%08d.jpg"),
    ]

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=600,  # 10 min timeout
        )
        if result.returncode != 0:
            logger.error(f"ffmpeg frame extraction failed: {result.stderr[:500]}")
            return []
    except subprocess.TimeoutExpired:
        logger.error("ffmpeg frame extraction timed out")
        return []

    # Collect extracted frames with their timestamps
    frames = []
    frame_files = sorted(output_dir.glob("frame_*.jpg"))
    for i, frame_path in enumerate(frame_files):
        timestamp = i * interval_seconds
        if timestamp > duration:
            break
        frames.append((frame_path, timestamp))

    logger.info(f"Extracted {len(frames)} frames")
    return frames


def _get_video_duration(video_path: Path) -> Optional[float]:
    """Get video duration in seconds using ffprobe."""
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(video_path),
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return float(result.stdout.strip())
    except Exception as e:
        logger.warning(f"Failed to get video duration: {e}")
        return None


def _load_and_preprocess(image_path: Path) -> Optional[np.ndarray]:
    """Load an image and convert to grayscale. Returns None on failure."""
    if not image_path.exists():
        logger.error(f"Image not found: {image_path}")
        return None

    img = cv2.imread(str(image_path))
    if img is None:
        logger.error(f"Failed to read image: {image_path}")
        return None

    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def multi_scale_template_match(
    reference_gray: np.ndarray,
    frame_gray: np.ndarray,
    scales: Optional[List[float]] = None,
    threshold: float = 0.7,
) -> Tuple[float, Optional[Tuple[int, int]]]:
    """
    Perform template matching at multiple scales.

    Returns (score, top_left_location) of the best match above threshold.
    Returns (0.0, None) if no match found.
    """
    if scales is None:
        scales = [0.5, 0.75, 1.0, 1.25, 1.5]

    best_score = 0.0
    best_location = None
    ref_h, ref_w = reference_gray.shape[:2]
    frame_h, frame_w = frame_gray.shape[:2]

    for scale in scales:
        scaled_w = int(ref_w * scale)
        scaled_h = int(ref_h * scale)

        # Skip if scaled template is larger than frame
        if scaled_w > frame_w or scaled_h > frame_h:
            continue
        # Skip if scaled template is too small to be meaningful
        if scaled_w < 10 or scaled_h < 10:
            continue

        scaled_ref = cv2.resize(reference_gray, (scaled_w, scaled_h))

        result = cv2.matchTemplate(frame_gray, scaled_ref, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)

        if max_val > best_score:
            best_score = max_val
            best_location = max_loc

    if best_score >= threshold:
        return best_score, best_location
    return 0.0, None


def orb_feature_match(
    reference_gray: np.ndarray,
    frame_gray: np.ndarray,
    min_good_matches: int = 10,
) -> float:
    """
    Perform ORB feature matching between reference and frame.

    Returns a score based on the number of good matches.
    Score is normalized to 0.0-1.0 range.
    """
    orb = cv2.ORB_create(nfeatures=1000)

    kp1, des1 = orb.detectAndCompute(reference_gray, None)
    kp2, des2 = orb.detectAndCompute(frame_gray, None)

    if des1 is None or des2 is None or len(kp1) < 2 or len(kp2) < 2:
        return 0.0

    # Use BFMatcher with Hamming distance for ORB
    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=False)

    try:
        matches = bf.knnMatch(des1, des2, k=2)
    except cv2.error:
        return 0.0

    # Apply Lowe's ratio test
    good_matches = []
    for match_pair in matches:
        if len(match_pair) == 2:
            m, n = match_pair
            if m.distance < 0.75 * n.distance:
                good_matches.append(m)

    if len(good_matches) < min_good_matches:
        return 0.0

    # Normalize score: more good matches = higher score, capped at 1.0
    # Typical good match count for a solid match: 20-50+
    score = min(1.0, len(good_matches) / 50.0)
    return score


def compute_frame_similarity(
    reference_gray: np.ndarray,
    frame_gray: np.ndarray,
    threshold: float = 0.7,
) -> Tuple[float, str]:
    """
    Compute similarity between reference image and a video frame
    using both template matching and ORB feature matching.

    Returns (combined_score, method_used).
    """
    # Template matching (primary)
    template_score, _ = multi_scale_template_match(
        reference_gray, frame_gray, threshold=threshold
    )

    # ORB feature matching (secondary)
    orb_score = orb_feature_match(reference_gray, frame_gray)

    # Combine scores: template matching weighted more heavily
    # Template matching is more reliable for UI elements and static patterns
    if template_score > 0 and orb_score > 0:
        combined = 0.7 * template_score + 0.3 * orb_score
        return combined, "combined"
    elif template_score > 0:
        return template_score, "template"
    elif orb_score > 0:
        return orb_score, "orb"
    else:
        return 0.0, "none"


def detect_pattern_matches(
    video_path: Path,
    reference_image_path: str,
    task_id: str,
    interval_seconds: int = 2,
    threshold: float = 0.7,
    temp_base_dir: Optional[str] = None,
) -> PatternMatchResult:
    """
    Detect all occurrences of the reference image pattern in the video.

    Args:
        video_path: Path to the video file
        reference_image_path: Reference image path (may be upload:// URL or absolute)
        task_id: Task ID for organizing temp files
        interval_seconds: Seconds between frame extraction
        threshold: Minimum similarity score (0.0-1.0)
        temp_base_dir: Base directory for temp files

    Returns:
        PatternMatchResult with all detected matches
    """
    result = PatternMatchResult()

    # Resolve reference image
    ref_path = _resolve_reference_image_path(reference_image_path)
    reference_gray = _load_and_preprocess(ref_path)
    if reference_gray is None:
        logger.error("Failed to load reference image")
        return result

    # Get video duration
    duration = _get_video_duration(video_path)
    if duration is None or duration <= 0:
        logger.error("Could not determine video duration")
        return result

    # Create temp directory for frames
    if temp_base_dir:
        frames_dir = Path(temp_base_dir) / f"pattern_frames_{task_id}"
    else:
        frames_dir = Path(tempfile.mkdtemp(prefix="supoclip_pattern_"))

    try:
        # Extract frames
        frames = extract_video_frames(video_path, frames_dir, interval_seconds)
        if not frames:
            logger.warning("No frames extracted from video")
            return result

        result.total_frames_checked = len(frames)
        logger.info(
            f"Comparing {len(frames)} frames against reference image "
            f"(threshold={threshold})"
        )

        # Compare each frame against reference
        for frame_path, timestamp in frames:
            frame_gray = _load_and_preprocess(frame_path)
            if frame_gray is None:
                continue

            score, method = compute_frame_similarity(
                reference_gray, frame_gray, threshold
            )

            if score >= threshold and method != "none":
                match = PatternMatch(
                    timestamp=timestamp,
                    score=score,
                    method=method,
                )
                result.matches.append(match)
                logger.debug(
                    f"Match at {timestamp:.1f}s (score={score:.3f}, method={method})"
                )

        # Sort by timestamp
        result.matches.sort(key=lambda m: m.timestamp)
        result.match_count = len(result.matches)

        logger.info(
            f"Found {result.match_count} matches out of "
            f"{result.total_frames_checked} frames checked"
        )

    finally:
        # Cleanup extracted frames
        if frames_dir.exists():
            try:
                shutil.rmtree(frames_dir)
            except Exception as e:
                logger.warning(f"Failed to cleanup frames dir: {e}")

    return result


def merge_nearby_matches(
    matches: List[PatternMatch],
    min_gap_seconds: float = 10.0,
) -> List[PatternMatch]:
    """
    Merge matches that are closer than min_gap_seconds apart.
    Keeps the match with the highest score in each cluster.
    """
    if not matches:
        return []

    # Sort by timestamp
    sorted_matches = sorted(matches, key=lambda m: m.timestamp)
    merged = [sorted_matches[0]]

    for match in sorted_matches[1:]:
        last = merged[-1]
        if match.timestamp - last.timestamp < min_gap_seconds:
            # Same cluster - keep the better match
            if match.score > last.score:
                merged[-1] = match
        else:
            # New cluster
            merged.append(match)

    return merged


def build_segments_from_matches(
    matches: List[PatternMatch],
    clip_window_seconds: int = 60,
    video_duration: float = 0.0,
) -> List[dict]:
    """
    Convert detected matches into clip segments.

    Each match becomes a segment from (match_time - window) to (match_time + window),
    clamped to video boundaries.

    Returns list of segment dicts compatible with the existing clip rendering pipeline.
    """
    segments = []

    for i, match in enumerate(matches):
        start_seconds = max(0.0, match.timestamp - clip_window_seconds)
        end_seconds = min(video_duration, match.timestamp + clip_window_seconds)

        # Skip if segment is too short
        if end_seconds - start_seconds < 5.0:
            continue

        start_time = _seconds_to_mmss(start_seconds)
        end_time = _seconds_to_mmss(end_seconds)

        segment = {
            "start_time": start_time,
            "end_time": end_time,
            "text": f"Pattern match at {_seconds_to_mmss(match.timestamp)} (score: {match.score:.2f})",
            "relevance_score": match.score,
            "reasoning": f"Visual pattern detected using {match.method} matching",
            "virality_score": 0,
            "hook_score": 0,
            "engagement_score": 0,
            "value_score": 0,
            "shareability_score": 0,
            "hook_type": None,
        }
        segments.append(segment)

    return segments


def _seconds_to_mmss(seconds: float) -> str:
    """Convert seconds to MM:SS format."""
    minutes = int(seconds) // 60
    secs = int(seconds) % 60
    return f"{minutes:02d}:{secs:02d}"
