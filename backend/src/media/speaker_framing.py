"""Active-speaker framing for multi-person shots, driven by diarization.

On a single-camera wide shot of a podcast or interview, the default vertical
crop can only show one person at a time. This module uses the transcript's
speaker labels to decide *who* is talking, matches each diarized speaker to a
face position on screen by correlating mouth-region motion with their speech,
and returns a crop that hard-cuts to the active speaker on each turn (the way a
human editor cuts a podcast short).

Scope, deliberately conservative so a wrong guess never ships:
- Only shots where two or more faces are visible together (wide shots) get
  speaker cuts. Single-face shots of a multi-camera edit keep a per-shot face
  crop.
- Clips with content shots (no faces: slides, tweets, screen recordings) are
  left to the scene-aware compositor in ``reframing``.
- When the speaker-to-face mapping is not clearly better than the alternatives,
  the caller falls back to dominant-face tracking.
"""

from itertools import permutations
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import subprocess

import cv2
import numpy as np

from .common import FACE_PRESENCE_MIN_AREA, logger
from .ffmpeg import clamp_even, round_to_even
from .timeline import get_words_for_keep_ranges
from .transcription import load_cached_transcript_data

SPEAKER_MIN_TURN_SECONDS = 1.2
SPEAKER_CUT_LEAD_SECONDS = 0.15
SPEAKER_SCENE_SNAP_SECONDS = 0.3
SPEAKER_ANALYSIS_FPS = 10.0
SPEAKER_DETECT_EVERY = 3
SPEAKER_PROC_WIDTH = 480
SPEAKER_MOTION_CELL = 8
WIDE_SHOT_MIN_RATE = 0.4
WIDE_SHOT_MIN_SECONDS = 1.0
CLUSTER_MIN_SUPPORT = 0.25
MIN_SPEAKER_SHARE = 0.12
MAPPING_MIN_MARGIN = 0.1
MIN_SPEAKER_SAMPLE_FRAMES = 8
MIN_SEGMENT_SECONDS = 0.4


def build_speaker_turns(
    words: Sequence[Dict[str, Any]],
    duration: Optional[float] = None,
    *,
    min_turn: float = SPEAKER_MIN_TURN_SECONDS,
    lead: float = SPEAKER_CUT_LEAD_SECONDS,
) -> List[Dict[str, Any]]:
    """Turn clip-relative diarized words into a gap-free speaker-turn timeline.

    Consecutive words by the same speaker form a run. Runs shorter than
    ``min_turn`` (a quick "yeah" or "right") are absorbed by the surrounding
    speaker so the frame doesn't flicker. Each cut lands ``lead`` seconds before
    the new speaker's first word, never earlier than the previous speaker's last
    word. Returns [] unless at least two speakers remain.
    """
    runs: List[Dict[str, Any]] = []
    for word in words:
        speaker = word.get("speaker")
        start, end = float(word["start"]), float(word["end"])
        if end <= start:
            continue
        if speaker is None:
            if runs:
                runs[-1]["end"] = max(runs[-1]["end"], end)
            continue
        if runs and runs[-1]["speaker"] == speaker:
            runs[-1]["end"] = max(runs[-1]["end"], end)
            continue
        runs.append({"speaker": str(speaker), "start": start, "end": end})

    def coalesce(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        for item in items:
            if merged and merged[-1]["speaker"] == item["speaker"]:
                merged[-1]["end"] = max(merged[-1]["end"], item["end"])
            else:
                merged.append(dict(item))
        return merged

    runs = coalesce(runs)
    while len(runs) > 1:
        shortest = min(range(len(runs)), key=lambda i: runs[i]["end"] - runs[i]["start"])
        if runs[shortest]["end"] - runs[shortest]["start"] >= min_turn:
            break
        # Absorb into the previous speaker (or the next one for the first run).
        neighbour = shortest - 1 if shortest > 0 else 1
        lo, hi = sorted((shortest, neighbour))
        runs[lo] = {
            "speaker": runs[neighbour]["speaker"],
            "start": runs[lo]["start"],
            "end": max(runs[lo]["end"], runs[hi]["end"]),
        }
        del runs[hi]
        runs = coalesce(runs)

    if len({run["speaker"] for run in runs}) < 2:
        return []

    turns: List[Dict[str, Any]] = []
    for index, run in enumerate(runs):
        start = 0.0 if index == 0 else turns[-1]["end"]
        if index + 1 < len(runs):
            following = runs[index + 1]["start"]
            end = max(following - lead, min(run["end"], following))
        else:
            end = max(run["end"], duration or run["end"])
        turns.append({"speaker": run["speaker"], "start": start, "end": max(start, end)})
    if duration is not None:
        turns = [
            {**turn, "end": min(turn["end"], duration)}
            for turn in turns
            if turn["start"] < duration
        ]
        if turns:
            turns[-1]["end"] = duration
    return turns


def build_clip_speaker_turns(
    video_path: Path, keep_ranges: Sequence[Tuple[float, float]]
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Speaker turns and diarized words for a clip, on the output timeline.

    Returns ([], []) when the transcript has no usable speaker labels.
    """
    transcript_data = load_cached_transcript_data(video_path)
    if not transcript_data:
        return [], []
    words = get_words_for_keep_ranges(transcript_data, list(keep_ranges))
    if not words or not any(word.get("speaker") for word in words):
        return [], []
    return build_speaker_turns(words), words


def _merge_boxes(boxes: List[Tuple[float, float, float, float]]) -> List[Tuple[float, float, float, float]]:
    """Drop boxes whose centre falls inside a larger box (duplicate detections)."""
    kept: List[Tuple[float, float, float, float]] = []
    for box in sorted(boxes, key=lambda b: b[2] * b[3], reverse=True):
        cx, cy = box[0] + box[2] / 2, box[1] + box[3] / 2
        if any(k[0] <= cx <= k[0] + k[2] and k[1] <= cy <= k[1] + k[3] for k in kept):
            continue
        kept.append(box)
    return kept


def _detect_all_faces(frame_bgr, mp_face, cascades) -> List[Tuple[float, float, float, float]]:
    """All face boxes (x, y, w, h) in a frame. Profiles matter here: podcast
    guests usually face each other, which frontal-only detection misses."""
    h, w = frame_bgr.shape[:2]
    boxes: List[Tuple[float, float, float, float]] = []
    if mp_face is not None:
        try:
            results = mp_face.process(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))
            for det in results.detections or []:
                box = det.location_data.relative_bounding_box
                bw, bh = max(0.0, box.width) * w, max(0.0, box.height) * h
                if bw > 10 and bh > 10:
                    boxes.append((box.xmin * w, box.ymin * h, bw, bh))
        except Exception:
            boxes = []
    if not boxes:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        min_side = max(14, int(w * 0.04))
        detect_kwargs = dict(
            scaleFactor=1.15,
            minNeighbors=4,
            minSize=(min_side, min_side),
            maxSize=(int(w * 0.7), int(h * 0.7)),
        )
        frontal, profile = cascades
        for fx, fy, fw, fh in frontal.detectMultiScale(gray, **detect_kwargs):
            boxes.append((float(fx), float(fy), float(fw), float(fh)))
        if profile is not None and not profile.empty():
            for fx, fy, fw, fh in profile.detectMultiScale(gray, **detect_kwargs):
                boxes.append((float(fx), float(fy), float(fw), float(fh)))
            flipped = cv2.flip(gray, 1)
            for fx, fy, fw, fh in profile.detectMultiScale(flipped, **detect_kwargs):
                boxes.append((float(w - fx - fw), float(fy), float(fw), float(fh)))
    frame_area = float(max(1, w * h))
    return _merge_boxes(
        [b for b in boxes if (b[2] * b[3]) / frame_area >= FACE_PRESENCE_MIN_AREA]
    )


def analyze_speaker_clip(input_path: Path, width: int, height: int) -> Optional[Dict[str, Any]]:
    """One low-res decode: all faces (every few frames), a coarse per-cell
    motion grid (every frame) and scene cuts. Coordinates are in source px."""
    from .reframing import _open_face_detectors, _scene_cuts_from_diffs

    if width <= 0 or height <= 0:
        return None
    proc_w = round_to_even(min(SPEAKER_PROC_WIDTH, width))
    proc_h = round_to_even(max(2, int(round(proc_w * height / width))))
    frame_bytes = proc_w * proc_h * 3
    scale = width / float(proc_w)
    grid_h, grid_w = max(1, proc_h // SPEAKER_MOTION_CELL), max(1, proc_w // SPEAKER_MOTION_CELL)

    command = [
        "ffmpeg", "-v", "error", "-an", "-sn",
        "-i", str(input_path),
        "-vf", f"fps={SPEAKER_ANALYSIS_FPS:.3f},scale={proc_w}:{proc_h}",
        "-pix_fmt", "bgr24", "-f", "rawvideo",
        "-threads", "0", "-",
    ]
    try:
        proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except Exception as exc:
        logger.warning("Speaker framing: ffmpeg spawn failed (%s)", exc)
        return None

    mp_face, frontal = _open_face_detectors()
    profile = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_profileface.xml")
    face_samples: List[Tuple[float, List[Tuple[float, float, float, float]]]] = []
    motion: List[np.ndarray] = []
    diffs: List[Tuple[float, float]] = []
    prev_gray = None
    prev_small = None
    index = 0
    try:
        while True:
            raw = proc.stdout.read(frame_bytes)
            if not raw or len(raw) < frame_bytes:
                break
            frame = np.frombuffer(raw, dtype=np.uint8).reshape(proc_h, proc_w, 3)
            t = index / SPEAKER_ANALYSIS_FPS
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.int16)
            if prev_gray is None:
                cells = np.zeros((grid_h, grid_w), dtype=np.float32)
            else:
                delta = np.abs(gray - prev_gray)[: grid_h * SPEAKER_MOTION_CELL, : grid_w * SPEAKER_MOTION_CELL]
                cells = delta.reshape(grid_h, SPEAKER_MOTION_CELL, grid_w, SPEAKER_MOTION_CELL).mean(axis=(1, 3)).astype(np.float32)
            motion.append(cells)
            prev_gray = gray
            small = cv2.resize(frame, (32, 18)).astype(np.int16)
            if prev_small is not None:
                diffs.append((t, float(np.mean(np.abs(small - prev_small)))))
            prev_small = small
            if index % SPEAKER_DETECT_EVERY == 0:
                faces = _detect_all_faces(frame, mp_face, (frontal, profile))
                face_samples.append(
                    (t, [(x * scale, y * scale, bw * scale, bh * scale) for x, y, bw, bh in faces])
                )
            index += 1
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

    if not motion:
        return None
    return {
        "face_samples": face_samples,
        "motion": np.stack(motion),
        "motion_fps": SPEAKER_ANALYSIS_FPS,
        "motion_cell": SPEAKER_MOTION_CELL * scale,
        "scene_cuts": _scene_cuts_from_diffs(diffs),
    }


def split_shots(scene_cuts: Sequence[float], duration: float) -> List[Tuple[float, float]]:
    bounds = [0.0, *sorted(c for c in scene_cuts if 0.0 < c < duration), duration]
    return [(a, b) for a, b in zip(bounds, bounds[1:]) if b > a]


def find_wide_shots(
    face_samples: Sequence[Tuple[float, List[Tuple[float, float, float, float]]]],
    shots: Sequence[Tuple[float, float]],
    crop_w: int,
) -> List[Tuple[float, float]]:
    """Shots where two or more horizontally separated faces are on screen together."""
    wide: List[Tuple[float, float]] = []
    for start, end in shots:
        if end - start < WIDE_SHOT_MIN_SECONDS:
            continue
        samples = [faces for t, faces in face_samples if start <= t < end]
        if not samples:
            continue
        multi = 0
        for faces in samples:
            centers = sorted(x + w / 2 for x, _, w, _ in faces)
            if len(centers) >= 2 and centers[-1] - centers[0] >= crop_w * 0.5:
                multi += 1
        if multi / len(samples) >= WIDE_SHOT_MIN_RATE:
            wide.append((start, end))
    return wide


def cluster_face_positions(
    face_samples: Sequence[Tuple[float, List[Tuple[float, float, float, float]]]],
    wide_shots: Sequence[Tuple[float, float]],
    crop_w: int,
) -> List[Dict[str, float]]:
    """Stable horizontal face positions across the wide shots, left to right.

    Faces are grouped by gaps in their x centres; groups seen in fewer than
    ``CLUSTER_MIN_SUPPORT`` of the wide-shot samples are treated as noise.
    """
    in_wide = [
        (t, faces) for t, faces in face_samples
        if any(a <= t < b for a, b in wide_shots)
    ]
    if not in_wide:
        return []
    points = sorted(
        (x + w / 2, y + h / 2, w, h, sample_index)
        for sample_index, (_, faces) in enumerate(in_wide)
        for x, y, w, h in faces
    )
    groups: List[List[Tuple[float, float, float, float, int]]] = [[points[0]]]
    for point in points[1:]:
        if point[0] - groups[-1][-1][0] > crop_w * 0.25:
            groups.append([point])
        else:
            groups[-1].append(point)

    clusters: List[Dict[str, float]] = []
    for group in groups:
        support = len({p[4] for p in group}) / len(in_wide)
        if support < CLUSTER_MIN_SUPPORT:
            continue
        clusters.append(
            {
                "cx": float(np.median([p[0] for p in group])),
                "cy": float(np.median([p[1] for p in group])),
                "w": float(np.median([p[2] for p in group])),
                "h": float(np.median([p[3] for p in group])),
                "support": support,
            }
        )
    return clusters


def mouth_motion_series(analysis: Dict[str, Any], cluster: Dict[str, float]) -> np.ndarray:
    """Per-frame motion in the lower half of a face (where the mouth moves)."""
    motion = analysis["motion"]
    cell = analysis["motion_cell"]
    _, grid_h, grid_w = motion.shape
    x0 = cluster["cx"] - cluster["w"] * 0.35
    x1 = cluster["cx"] + cluster["w"] * 0.35
    y0 = cluster["cy"] + cluster["h"] * 0.05
    y1 = cluster["cy"] + cluster["h"] * 0.6
    c0 = int(np.clip(np.floor(x0 / cell), 0, grid_w - 1))
    c1 = int(np.clip(np.ceil(x1 / cell), c0 + 1, grid_w))
    r0 = int(np.clip(np.floor(y0 / cell), 0, grid_h - 1))
    r1 = int(np.clip(np.ceil(y1 / cell), r0 + 1, grid_h))
    return motion[:, r0:r1, c0:c1].mean(axis=(1, 2))


def assign_speakers_to_clusters(
    activity: Dict[str, List[float]],
    *,
    min_margin: float = MAPPING_MIN_MARGIN,
) -> Optional[Dict[str, int]]:
    """Pick the speaker→face mapping whose faces move most while they talk.

    ``activity[speaker][cluster]`` is the cluster's mouth motion while that
    speaker talks, relative to its average while anyone talks (1.0 = no
    difference). Every injective mapping is scored; the best must beat the
    runner-up by ``min_margin`` per speaker, and each assigned face must be
    more active than its own average, otherwise the mapping is ambiguous and
    None is returned.
    """
    speakers = list(activity)
    if len(speakers) < 2:
        return None
    n_clusters = len(next(iter(activity.values())))
    if n_clusters < 2:
        return None
    if len(speakers) > n_clusters:
        return None

    scored: List[Tuple[float, Tuple[int, ...]]] = []
    for combo in permutations(range(n_clusters), len(speakers)):
        score = sum(activity[s][c] for s, c in zip(speakers, combo))
        scored.append((score, combo))
    scored.sort(reverse=True)
    best_score, best_combo = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else float("-inf")
    if (best_score - runner_up) / len(speakers) < min_margin:
        return None
    mapping = dict(zip(speakers, best_combo))
    if any(activity[s][c] <= 1.0 for s, c in mapping.items()):
        return None
    return mapping


def build_speaker_activity(
    analysis: Dict[str, Any],
    clusters: Sequence[Dict[str, float]],
    words: Sequence[Dict[str, Any]],
    wide_shots: Sequence[Tuple[float, float]],
    scene_cuts: Sequence[float],
) -> Dict[str, List[float]]:
    """Relative mouth activity per (speaker, face cluster) inside wide shots."""
    fps = analysis["motion_fps"]
    n_frames = analysis["motion"].shape[0]
    frame_times = np.arange(n_frames) / fps
    usable = np.zeros(n_frames, dtype=bool)
    for a, b in wide_shots:
        usable |= (frame_times >= a) & (frame_times < b)
    for cut in scene_cuts:
        usable &= np.abs(frame_times - cut) > 0.25
    usable[0] = False  # the first frame has no predecessor to diff against

    frame_speaker: List[Optional[str]] = [None] * n_frames
    for word in words:
        speaker = word.get("speaker")
        if speaker is None:
            continue
        lo = max(0, int(np.ceil(float(word["start"]) * fps)))
        hi = min(n_frames, int(np.floor(float(word["end"]) * fps)) + 1)
        for frame_index in range(lo, hi):
            frame_speaker[frame_index] = str(speaker)

    speaking = np.array([s is not None for s in frame_speaker]) & usable
    if speaking.sum() < MIN_SPEAKER_SAMPLE_FRAMES:
        return {}
    talk_counts: Dict[str, int] = {}
    for index in np.flatnonzero(speaking):
        talk_counts[frame_speaker[index]] = talk_counts.get(frame_speaker[index], 0) + 1
    total = float(sum(talk_counts.values()))
    speakers = [
        s for s, count in talk_counts.items()
        if count / total >= MIN_SPEAKER_SHARE and count >= MIN_SPEAKER_SAMPLE_FRAMES
    ]

    series = [mouth_motion_series(analysis, cluster) for cluster in clusters]
    activity: Dict[str, List[float]] = {}
    for speaker in sorted(speakers):
        mask = speaking & np.array([s == speaker for s in frame_speaker])
        row: List[float] = []
        for values in series:
            baseline = float(values[speaking].mean())
            row.append(float(values[mask].mean()) / baseline if baseline > 1e-6 else 1.0)
        activity[speaker] = row
    return activity


def _snap(value: float, cuts: Sequence[float], window: float) -> float:
    near = [c for c in cuts if abs(c - value) <= window]
    return min(near, key=lambda c: abs(c - value)) if near else value


def build_speaker_segments(
    turns: Sequence[Dict[str, Any]],
    mapping: Dict[str, int],
    cluster_x: Sequence[int],
    shots: Sequence[Tuple[float, float]],
    wide_shots: Sequence[Tuple[float, float]],
    shot_x: Dict[Tuple[float, float], int],
    scene_cuts: Sequence[float],
    duration: float,
) -> List[Tuple[float, float, int]]:
    """Piecewise-constant crop-x segments covering the clip.

    Inside wide shots the crop follows the speaker turns (cuts snapped onto
    nearby scene cuts); other shots keep their own face-centred crop. Turns by a
    speaker without an on-screen face hold the previous framing.
    """
    boundaries = {0.0, duration}
    for turn in turns[1:]:
        boundaries.add(_snap(float(turn["start"]), scene_cuts, SPEAKER_SCENE_SNAP_SECONDS))
    for start, end in shots:
        boundaries.update((start, end))
    points = sorted(b for b in boundaries if 0.0 <= b <= duration)

    def speaker_at(t: float) -> Optional[str]:
        for turn in turns:
            if turn["start"] <= t < turn["end"]:
                return turn["speaker"]
        return turns[-1]["speaker"] if turns else None

    first_mapped = next((cluster_x[mapping[t["speaker"]]] for t in turns if t["speaker"] in mapping), cluster_x[0])
    segments: List[Tuple[float, float, int]] = []
    held_x = first_mapped
    for a, b in zip(points, points[1:]):
        if b - a <= 1e-6:
            continue
        mid = (a + b) / 2
        if any(s <= mid < e for s, e in wide_shots):
            speaker = speaker_at(mid)
            x = cluster_x[mapping[speaker]] if speaker in mapping else held_x
            held_x = x
        else:
            shot = next(((s, e) for s, e in shots if s <= mid < e), None)
            x = shot_x.get(shot, held_x) if shot else held_x
        if segments and segments[-1][2] == x:
            segments[-1] = (segments[-1][0], b, x)
        else:
            segments.append((a, b, x))

    # Fold slivers (rounding around snapped cuts) into the previous segment.
    cleaned: List[Tuple[float, float, int]] = []
    for seg in segments:
        if cleaned and seg[1] - seg[0] < MIN_SEGMENT_SECONDS:
            cleaned[-1] = (cleaned[-1][0], seg[1], cleaned[-1][2])
        elif cleaned and cleaned[-1][2] == seg[2]:
            cleaned[-1] = (cleaned[-1][0], seg[1], seg[2])
        else:
            cleaned.append(seg)
    return cleaned


def build_step_x_expression(segments: Sequence[Tuple[float, float, int]]) -> str:
    """Sum-of-steps crop-x expression producing hard cuts.

    ffmpeg's expression parser recurses once per chained operator and rejects
    roughly 100 terms in a row, so the sum is grouped as a balanced tree:
    nesting grows with log2(turns) instead of linearly.
    """
    if not segments:
        return "0"
    terms = [str(int(segments[0][2]))]
    for previous, current in zip(segments, segments[1:]):
        delta = int(current[2]) - int(previous[2])
        if delta:
            terms.append(f"{delta}*gte(t\\,{current[0]:.3f})")

    def join(items: List[str]) -> str:
        if len(items) <= 4:
            return "+".join(items)
        middle = len(items) // 2
        return f"({join(items[:middle])})+({join(items[middle:])})"

    return join(terms)


def build_speaker_crop_chain(
    input_path: Path,
    width: int,
    height: int,
    crop_w: int,
    crop_h: int,
    duration: float,
    turns: Sequence[Dict[str, Any]],
    words: Sequence[Dict[str, Any]],
) -> Optional[str]:
    """ffmpeg -vf chain cutting between speakers, or None to use the default."""
    from .reframing import build_layout_plan

    if len(turns) < 2 or crop_w >= width or duration <= 0:
        return None
    analysis = analyze_speaker_clip(input_path, width, height)
    if not analysis:
        return None
    face_samples = analysis["face_samples"]
    scene_cuts = analysis["scene_cuts"]
    shots = split_shots(scene_cuts, duration)

    # Content shots need the blurred-fit compositor; leave those clips alone.
    track = []
    for t, faces in face_samples:
        if faces:
            x, _, w, h = max(faces, key=lambda f: f[2] * f[3])
            track.append((t, x + w / 2, (w * h) / float(width * height)))
        else:
            track.append((t, None, 0.0))
    if any(seg["kind"] == "fit" for seg in build_layout_plan(track, scene_cuts, duration)):
        logger.info("Speaker framing skipped: clip has content shots")
        return None

    wide_shots = find_wide_shots(face_samples, shots, crop_w)
    if not wide_shots:
        return None
    clusters = cluster_face_positions(face_samples, wide_shots, crop_w)
    if len(clusters) < 2:
        return None
    activity = build_speaker_activity(analysis, clusters, words, wide_shots, scene_cuts)
    mapping = assign_speakers_to_clusters(activity)
    if not mapping:
        logger.info("Speaker framing skipped: speaker-to-face mapping ambiguous (%s)", activity)
        return None

    max_x = max(0, width - crop_w)
    cluster_x = [clamp_even(int(c["cx"] - crop_w / 2), 0, max_x) for c in clusters]
    if len(set(cluster_x[i] for i in mapping.values())) < 2:
        return None
    shot_x: Dict[Tuple[float, float], int] = {}
    for shot in shots:
        centres = [
            x + w / 2 for t, faces in face_samples if shot[0] <= t < shot[1]
            for x, _, w, _ in [max(faces, key=lambda f: f[2] * f[3])] if faces
        ]
        if centres:
            shot_x[shot] = clamp_even(int(np.median(centres) - crop_w / 2), 0, max_x)

    segments = build_speaker_segments(
        turns, mapping, cluster_x, shots, wide_shots, shot_x, scene_cuts, duration
    )
    if len(segments) < 2:
        return None
    logger.info(
        "Speaker framing: %d speaker(s) mapped to faces, %d framing segment(s)",
        len(mapping), len(segments),
    )
    return (
        f"crop={crop_w}:{crop_h}:x='{build_step_x_expression(segments)}':y=0,"
        "scale=1080:1920:flags=lanczos,setsar=1"
    )
