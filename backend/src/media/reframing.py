"""Reframing helpers for the video pipeline."""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from pathlib import Path
from typing import Tuple
import cv2
import numpy as np
import re
import shutil
import subprocess
import tempfile
from .common import (
    FACE_PRESENCE_MIN_AREA,
    FACE_PRESENCE_RATE,
    FACE_RATE_WINDOW,
    KENBURNS_MIN_SECONDS,
    KENBURNS_SUPERSAMPLE_H,
    KENBURNS_SUPERSAMPLE_W,
    KENBURNS_ZOOM_DELTA,
    LAYOUT_SNAP_WINDOW,
    MIN_LAYOUT_SECONDS,
    OUTPUT_FPS,
    logger,
)
from .ffmpeg import (
    build_audio_output_args,
    build_final_video_encode_args,
    clamp_even,
    ffmpeg_escape_filter_path,
    ffprobe_duration,
    ffprobe_has_audio,
    ffprobe_video_size,
    round_to_even,
    run_ffmpeg_command,
    subtitles_filter_fragment,
)


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


def kenburns_zoom_fragment(duration: float) -> Optional[str]:
    """Slow linear punch-in fragment producing a 1080x1920 [setsar-ed] stream.

    Zooms through a 1.5x supersampled frame so zoompan's integer crop offsets
    stay sub-pixel in the output (no visible stepping). The frame rate is
    normalised to OUTPUT_FPS *before* zoompan and re-declared on it, because
    zoompan re-times its output at its own fps — matching the two keeps the
    video duration identical and the audio in sync.
    """
    if duration < KENBURNS_MIN_SECONDS:
        return None
    z_expr = (
        f"if(isnan(it)\\,1\\,1+{KENBURNS_ZOOM_DELTA}*min(it/{duration:.3f}\\,1))"
    )
    return (
        f"scale={KENBURNS_SUPERSAMPLE_W}:{KENBURNS_SUPERSAMPLE_H}:flags=lanczos,"
        f"fps={OUTPUT_FPS},"
        f"zoompan=z='{z_expr}':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)*0.35'"
        f":d=1:s=1080x1920:fps={OUTPUT_FPS},setsar=1"
    )


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

    # Narrow/portrait source: no horizontal room to crop — static fit, with a
    # slow punch-in so the frame still breathes.
    if crop_w >= width:
        sx, sy, sw, sh = detect_optimal_crop_region(input_path, 0, min(duration, 12.0))
        tail = kenburns_zoom_fragment(duration) or "scale=1080:1920:flags=lanczos,setsar=1"
        return (f"crop={sw}:{sh}:{sx}:{sy},{tail}", "vf")

    # One fast decode pass yields both the face track and the scene cuts.
    try:
        track, scene_cuts = analyze_vertical_clip(input_path)
    except Exception as exc:
        logger.warning("Clip analysis failed (%s); using static crop", exc)
        track, scene_cuts = [], []

    keys = build_crop_trajectory(track, width, crop_w) if track else []
    moving = bool(keys and trajectory_has_movement(keys, crop_w))
    static_x = 0
    if moving:
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
        # Static, all-face clip: add the slow Ken Burns punch-in. (The moving
        # tracked crop and the compositor path keep the plain chain — they
        # already have motion, and zoompan's re-timing would fight overlays.)
        if not moving:
            zoom = kenburns_zoom_fragment(duration)
            if zoom:
                return (f"crop={crop_w}:{crop_h}:{static_x}:0,{zoom}", "vf")
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
