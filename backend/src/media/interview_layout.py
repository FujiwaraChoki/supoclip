"""Per-shot 9:16 composition for sit-down interviews.

A two-person interview edit cuts between a wide two-shot and single
close-ups. No single 9:16 crop shows both people in the two-shot, so the
generic planner kept that shot (and, through its hysteresis, the whole clip)
as a letterboxed full frame. This module composes every camera shot on its
own and switches only at the edit:

- ``pair``: two people too far apart for one crop. Each gets a face-centred
  1080x960 panel, stacked with the left-hand person on top.
- ``single``: one person (or two people close enough to share a crop) in a
  tight, head-and-shoulders 9:16 crop.
- ``fit``: a shot without a usable subject (b-roll, aerials, a crowd) or with
  an on-screen paragraph (a tweet, a document, an explainer graphic) keeps
  the whole picture on a blurred background.

Crops follow their face with the same eased, dead-zoned trajectory as the
tracked crop, so a tripod shot stays still and a handheld one is followed.
Every return to the same camera setup gets the same zoom.

It only takes over clips that contain a real two-shot (see
``plan_interview_layout``); single talking heads, calls, reactions, slides and
sports keep their existing layouts. Diarized speaker cuts are not used here:
both people stay on screen whenever the camera shows both.

The filter decodes the input once: each composition reads only its own time
range (``trim``) and the pieces are joined with ``concat``. It reads no other
file, so the plan can be stored and replayed by the editor.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
import re
import subprocess
import tempfile

import numpy as np

from .common import HOOK_TITLE_SECONDS, UPSCALE_SHARPEN_FILTER, logger
from .ffmpeg import clamp_even, round_to_even

Box = Tuple[float, float, float, float]
FaceFrame = Tuple[float, Sequence[Box]]

OUT_W = 1080
OUT_H = 1920
PANEL_H = OUT_H // 2

# Largest source-to-output enlargement. A 1080p source keeps a sharp picture
# up to about 2.25x with the upscale sharpen.
MAX_UPSCALE = 2.25
# Two people sitting close may need a narrower panel than that allows, so the
# neighbour's face stays out of it; never beyond this.
MAX_PANEL_UPSCALE = 2.6
# Face box height as a share of a single crop's height (head and shoulders).
SINGLE_FACE_FRAC = 0.25
# Face box height as a share of a stacked panel's height.
PANEL_FACE_FRAC = 0.32
# Where the face-box centre sits, as a share of the crop height from the top.
# The eyes sit a little above the box centre, near the upper third.
SINGLE_FACE_Y = 0.36
TOP_PANEL_FACE_Y = 0.42
BOTTOM_PANEL_FACE_Y = 0.38
# Space kept above the face box for hair, caps and headroom, in face heights.
HEADROOM_FACES = 0.6

# A person is a face cluster present in at least this share of a shot's
# samples, at least MIN_FACE_H_FRAC of the frame height tall.
CLUSTER_SUPPORT = 0.5
MIN_FACE_H_FRAC = 0.07
# A lone face needs this share of the frame height for a single crop.
SINGLE_MIN_FACE_H_FRAC = 0.11
# Two people form a pair when the smaller face is at least this share of the
# larger one's height and their face centres are vertically close.
PAIR_MIN_SIZE_RATIO = 0.6
PAIR_MAX_DY_FRAC = 0.25
# Two faces closer than this share of a full-height 9:16 crop share one crop.
SHARED_CROP_SPAN = 0.85
# A graphic (or its absence) must last this many samples before a shot is
# divided at its onset, so one noisy frame never flips the layout.
GRAPHIC_MIN_SAMPLES = 3
# A stretch of a shot this short beside a graphic shows the graphic layout too.
GRAPHIC_ABSORB_SECONDS = 2.0
# Analysis samples this close to a cut are ambiguous and are not classified.
CUT_GUARD_SECONDS = 0.05
# A change in how many people are on screen, without a detected cut, must
# last this many samples before the shot is divided (a missed edit, not a
# guest turning away for a moment).
FACE_STATE_MIN_SAMPLES = 6
# A shot this short where nobody was found keeps its neighbour's composition.
FLASH_SECONDS = 0.5
# The clip needs this much two-shot before the interview layout takes over.
PAIR_MIN_SECONDS = 1.5
# The same camera setup across shots: centres within this share of the width
# and face heights within this ratio share one zoom.
SETUP_CENTRE_TOLERANCE = 0.05
SETUP_SIZE_TOLERANCE = 0.2
# Frame size of the full-rate decode that places cuts on the exact frame.
CUT_PROBE_W = 64
CUT_PROBE_H = 36
# A cut: a change of the picture (mean 8-bit difference) at least this large
# over at most CUT_MAX_FRAMES frames, CUT_SPIKE_RATIO times the local motion
# and CUT_PEAK_RATIO times the frame changes either side (pans and dissolves
# do not count).
CUT_MIN_DIFF = 10.0
CUT_MAX_FRAMES = 3
CUT_SPIKE_RATIO = 2.5
CUT_PEAK_RATIO = 1.8
# Faces that start below this share of the frame height look like webcam
# tiles: a clip that only ever shows such a pair keeps the call stack.
WEBCAM_FACE_TOP = 0.3


# Output y of the seam between the two stacked panels (each 1080x960).
PAIR_SEAM_Y = 0.5

@dataclass
class Segment:
    start: float
    end: float
    kind: str = "fit"
    reason: str = ""
    # Median face boxes of the people framed, left to right; after pooling,
    # the camera setup's boxes (they fix each crop's zoom).
    people: List[Box] = field(default_factory=list)
    # Per person, the face box at each sample time (for tracking).
    tracks: List[Dict[float, Box]] = field(default_factory=list)
    times: List[float] = field(default_factory=list)
    graphic: bool = False


def _median_box(boxes: Sequence[Box]) -> Box:
    arr = np.asarray(boxes, dtype=float)
    return tuple(float(v) for v in np.median(arr, axis=0))  # type: ignore[return-value]


def _centre(box: Box) -> Tuple[float, float]:
    return box[0] + box[2] / 2, box[1] + box[3] / 2


def _clusters(
    samples: Sequence[FaceFrame], width: int, height: int
) -> List[Dict[str, Any]]:
    """Group a shot's face boxes by horizontal position, most supported first."""
    clusters: List[Dict[str, Any]] = []
    min_h = height * MIN_FACE_H_FRAC * 0.7
    for t, faces in samples:
        for box in sorted(faces, key=lambda b: b[2] * b[3], reverse=True):
            if box[3] < min_h:
                continue
            cx = box[0] + box[2] / 2
            best = None
            for cluster in clusters:
                distance = abs(cx - cluster["cx"])
                reach = max(width * 0.06, 0.8 * max(box[2], cluster["w"]))
                if distance <= reach and (best is None or distance < abs(cx - best["cx"])):
                    best = cluster
            if best is None:
                best = {"cx": cx, "w": box[2], "track": {}}
                clusters.append(best)
            if t in best["track"]:
                continue
            best["track"][t] = tuple(float(v) for v in box)
            boxes = list(best["track"].values())
            best["cx"] = float(np.median([b[0] + b[2] / 2 for b in boxes]))
            best["w"] = float(np.median([b[2] for b in boxes]))
    total = max(1, len(samples))
    for cluster in clusters:
        cluster["support"] = len(cluster["track"]) / total
        cluster["box"] = _median_box(list(cluster["track"].values()))
    clusters.sort(key=lambda c: c["support"], reverse=True)
    return clusters


def _runs(
    times: Sequence[float], states: Sequence[Any], start: float, end: float, min_samples: int
) -> List[Tuple[float, float, Any]]:
    """Split [start, end) where ``states`` changes for at least ``min_samples``.

    Shorter runs take their neighbours' state, so one noisy sample never
    divides a shot.
    """
    if not times:
        return [(start, end, None)]
    states = list(states)
    while True:
        runs: List[List[int]] = []
        for index, state in enumerate(states):
            if runs and states[runs[-1][0]] == state:
                runs[-1][1] = index + 1
            else:
                runs.append([index, index + 1])
        short = [r for r in runs if r[1] - r[0] < min_samples]
        if len(runs) <= 1 or not short:
            break

        def neighbours(run: List[int]) -> List[List[int]]:
            position = runs.index(run)
            return [runs[j] for j in (position - 1, position + 1) if 0 <= j < len(runs)]

        # The short run beside the longest steady stretch goes first, into it.
        run = min(short, key=lambda r: (-max(b - a for a, b in neighbours(r)), r[1] - r[0]))
        donor = max(neighbours(run), key=lambda r: r[1] - r[0])
        for i in range(run[0], run[1]):
            states[i] = states[donor[0]]
    out: List[Tuple[float, float, Any]] = []
    for index, state in enumerate(states):
        if out and out[-1][2] == state:
            continue
        boundary = start if not out else times[index]
        if out:
            out[-1] = (out[-1][0], boundary, out[-1][2])
        out.append((boundary, end, state))
    return out


def _graphic_spans(
    times: Sequence[float], flags: Sequence[bool], start: float, end: float
) -> List[Tuple[float, float, Any]]:
    """Runs of a shot with and without an on-screen paragraph.

    Text fades in and out over a few frames before the detector sees whole
    lines, so each graphic run starts one sample earlier and ends one later.
    A stretch without it shorter than GRAPHIC_ABSORB_SECONDS beside a
    graphic joins it: one layout change per shot instead of two in a second.
    """
    runs = [list(r) for r in _runs(times, flags, start, end, GRAPHIC_MIN_SAMPLES)]
    if len(runs) < 2:
        return [tuple(r) for r in runs]

    def earlier(t: float) -> float:
        before = [s for s in times if s < t]
        return before[-1] if before else start

    def later(t: float) -> float:
        after = [s for s in times if s > t]
        return after[0] if after else end

    for index in range(len(runs) - 1):
        boundary = runs[index][1]
        moved = later(boundary) if runs[index][2] else earlier(boundary)
        if runs[index][0] < moved < runs[index + 1][1]:
            runs[index][1] = runs[index + 1][0] = moved
    for run in runs:
        if not run[2] and run[1] - run[0] < GRAPHIC_ABSORB_SECONDS:
            run[2] = True
    merged: List[List[Any]] = []
    for run in runs:
        if merged and merged[-1][2] == run[2]:
            merged[-1][1] = run[1]
        else:
            merged.append(run)
    return [tuple(r) for r in merged]


def _classify(
    segment: Segment, samples: Sequence[FaceFrame], width: int, height: int, crop_w: int
) -> Segment:
    segment.times = [t for t, _ in samples]
    if segment.graphic:
        segment.kind, segment.reason = "fit", "graphic"
        return segment
    clusters = _clusters(samples, width, height)
    people = [
        c for c in clusters
        if c["support"] >= CLUSTER_SUPPORT and c["box"][3] >= height * MIN_FACE_H_FRAC
    ]
    if not people:
        segment.kind, segment.reason = "fit", "no_subject"
        return segment
    if len(people) >= 3:
        segment.kind, segment.reason = "fit", "group"
        return segment
    if len(people) == 2:
        left, right = sorted(people, key=lambda c: _centre(c["box"])[0])
        lb, rb = left["box"], right["box"]
        span = max(lb[0] + lb[2], rb[0] + rb[2]) - min(lb[0], rb[0])
        small, large = sorted((lb[3], rb[3]))
        dy = abs(_centre(lb)[1] - _centre(rb)[1])
        if span <= crop_w * SHARED_CROP_SPAN:
            def union(a: Box, b: Box) -> Box:
                x0, y0 = min(a[0], b[0]), min(a[1], b[1])
                return (x0, y0, max(a[0] + a[2], b[0] + b[2]) - x0, max(a[1] + a[3], b[1] + b[3]) - y0)

            track = {
                t: union(left["track"][t], right["track"][t])
                for t in left["track"] if t in right["track"]
            }
            segment.kind, segment.reason = "single", "shared_crop"
            segment.people, segment.tracks = [union(lb, rb)], [track]
            return segment
        if small >= large * PAIR_MIN_SIZE_RATIO and dy <= height * PAIR_MAX_DY_FRAC:
            segment.kind, segment.reason = "pair", "two_people"
            segment.people, segment.tracks = [lb, rb], [left["track"], right["track"]]
            return segment
        # Over the shoulder: the near person is a huge, edge-cut blur facing
        # away; the subject is the face looking into the lens.
        def foreground(c: Dict[str, Any]) -> bool:
            x, _, w, h = c["box"]
            return h > height * 0.5 or x < width * 0.04 or x + w > width * 0.96

        facing = [c for c in people if not foreground(c)] or people
        people = [max(facing, key=lambda c: c["box"][3])]
    subject = people[0]
    if subject["box"][3] < height * SINGLE_MIN_FACE_H_FRAC:
        # A distant figure, a game character or a false detection in b-roll:
        # a tight crop would invent a subject, so the frame stays whole.
        segment.kind, segment.reason = "fit", "small_subject"
        return segment
    segment.kind, segment.reason = "single", "one_person"
    segment.people, segment.tracks = [subject["box"]], [subject["track"]]
    return segment


def _face_state(faces: Sequence[Box], height: int) -> int:
    return min(3, sum(1 for box in faces if box[3] >= height * MIN_FACE_H_FRAC))


def _segments(
    duration: float,
    scene_cuts: Sequence[float],
    face_samples: Sequence[FaceFrame],
    context: Sequence[Any],
    width: int,
    height: int,
    crop_w: int,
) -> List[Segment]:
    """Camera shots, divided again where a graphic or the people on screen change.

    The cut detector misses some edits (b-roll cut into a close-up, a cut
    between similar colours). A lasting change in the number of people, or a
    paragraph appearing, divides the shot there too.
    """
    cuts = sorted({float(c) for c in scene_cuts if 0 < c < duration})
    bounds = [0.0, *cuts, duration]
    blocks = {round(float(e.time), 3): getattr(e, "text_block_lines", 0) >= 2 for e in context}
    segments: List[Segment] = []
    for start, end in zip(bounds, bounds[1:]):
        # A sample within a frame or so of a cut may show either shot.
        shot = [
            (t, faces) for t, faces in face_samples
            if start <= t < end and not any(abs(t - c) < CUT_GUARD_SECONDS for c in cuts)
        ]
        times = [t for t, _ in shot]
        graphic = [blocks.get(round(float(t), 3), False) for t, _ in shot]
        for a, b, is_graphic in _graphic_spans(times, graphic, start, end):
            part = [(t, faces) for t, faces in shot if a <= t < b]
            pieces = (
                [(a, b, None)] if is_graphic or not part else
                _runs([t for t, _ in part], [_face_state(f, height) for _, f in part], a, b, FACE_STATE_MIN_SAMPLES)
            )
            for c, d, _ in pieces:
                sub = [(t, faces) for t, faces in part if c <= t < d]
                segment = Segment(c, d, graphic=bool(is_graphic) and bool(sub))
                if sub:
                    segments.append(_classify(segment, sub, width, height, crop_w))
                elif segments:
                    segments[-1].end = d
                else:
                    segment.kind, segment.reason = "fit", "unsampled"
                    segments.append(segment)
    index = 0
    while index < len(segments) and len(segments) > 1:
        segment = segments[index]
        if segment.end - segment.start < FLASH_SECONDS and segment.reason == "no_subject":
            if index > 0:
                segments[index - 1].end = segment.end
            else:
                segments[index + 1].start = segment.start
            del segments[index]
            continue
        index += 1
    return segments


def _same_setup(a: Sequence[Box], b: Sequence[Box], width: int) -> bool:
    if len(a) != len(b):
        return False
    for p, q in zip(a, b):
        if abs(_centre(p)[0] - _centre(q)[0]) > width * SETUP_CENTRE_TOLERANCE:
            return False
        if abs(p[3] - q[3]) > max(p[3], q[3]) * SETUP_SIZE_TOLERANCE:
            return False
    return True


def _pool_setups(segments: List[Segment], width: int) -> None:
    """Give every shot from the same camera setup the same zoom."""
    groups: List[List[Segment]] = []
    for segment in segments:
        if segment.kind not in {"single", "pair"}:
            continue
        for group in groups:
            first = group[0]
            if first.kind == segment.kind and _same_setup(first.people, segment.people, width):
                group.append(segment)
                break
        else:
            groups.append([segment])
    for group in groups:
        if len(group) < 2:
            continue
        pooled = [
            _median_box([s.people[i] for s in group]) for i in range(len(group[0].people))
        ]
        for segment in group:
            segment.people = list(pooled)


def _merge_neighbours(segments: List[Segment]) -> List[Segment]:
    """Join neighbouring full-frame segments into one piece of the graph.

    Face crops stay separate: across a (jump) cut each restarts its own
    tracking, as the camera framing changed there.
    """
    merged: List[Segment] = []
    for segment in segments:
        last = merged[-1] if merged else None
        if last is not None and last.kind == segment.kind == "fit":
            last.end = segment.end
            last.times = last.times + segment.times
            continue
        merged.append(segment)
    return merged


def _axis(
    segment: Segment, desired: Dict[float, float], size: int, limit: int, still: float
) -> str:
    """Offset expression for one crop axis: eased tracking, or a still value.

    ``desired`` is the ideal crop offset at each sample. The same dead-zoned
    spring as the tracked crop smooths it; small drift stays at ``still``.
    """
    from .reframing import build_crop_trajectory, build_smooth_pan_expression, trajectory_has_movement

    if limit <= 0:
        return "0"
    track = [
        (t, desired[t] + size / 2 if t in desired else None, 0.0) for t in segment.times
    ]
    keys = build_crop_trajectory(track, limit + size, size) if len(track) >= 3 else []
    if not keys or not trajectory_has_movement(keys, size):
        return str(clamp_even(int(still), 0, limit))
    keys = [(segment.start, keys[0][1]),
            *[(t, x) for t, x in keys if segment.start < t < segment.end],
            (segment.end, keys[-1][1])]
    return f"'{build_smooth_pan_expression(keys)}'"


def _vertical_offset(box: Box, size: int, face_y: float) -> float:
    fy, fh = box[1], box[3]
    return min(fy + fh / 2 - face_y * size, fy - HEADROOM_FACES * fh)


def _single_size(box: Box, width: int, height: int) -> Tuple[int, int]:
    """(crop_w, crop_h) of a tight 9:16 crop for a face box."""
    _, _, fw, fh = box
    crop_h = min(float(height), max(OUT_H / MAX_UPSCALE, fh / SINGLE_FACE_FRAC))
    # Two people sharing a crop both stay inside it.
    crop_h = min(float(height), max(crop_h, fw * 1.25 * 16 / 9))
    if crop_h * 9 / 16 > width:
        crop_h = width * 16 / 9
    crop_h = round_to_even(int(crop_h))
    return round_to_even(int(crop_h * 9 / 16)), crop_h


def _panel_size(box: Box, other: Box, width: int, height: int) -> Tuple[int, int]:
    """(w, h) of a 9:8 source window for one stacked panel."""
    panel_h = min(float(height), max(PANEL_H / MAX_UPSCALE, box[3] / PANEL_FACE_FRAC), width * 8 / 9)
    # Keep the neighbour's face out of the panel, within a looser upscale cap.
    gap = abs(_centre(box)[0] - _centre(other)[0]) - other[2] * 0.6
    if gap * 2 < panel_h * 9 / 8:
        panel_h = max(PANEL_H / MAX_PANEL_UPSCALE, gap * 2 * 8 / 9)
    panel_h = round_to_even(int(min(panel_h, height)))
    return min(round_to_even(width), round_to_even(int(panel_h * 9 / 8))), panel_h


def _crop(segment: Segment, index: int, size: Tuple[int, int], face_y: float, width: int, height: int) -> Tuple[str, float]:
    """``crop`` filter following person ``index``; returns it and the face bottom (0-1)."""
    w, h = size
    track = segment.tracks[index] if index < len(segment.tracks) else {}
    xs = {t: _centre(b)[0] - w / 2 for t, b in track.items()}
    ys = {t: _vertical_offset(b, h, face_y) for t, b in track.items()}
    box = segment.people[index]
    if not xs:
        xs = {segment.start: _centre(box)[0] - w / 2}
        ys = {segment.start: _vertical_offset(box, h, face_y)}
    # A still crop sits on the camera setup's pooled face, so every return
    # to that setup is framed identically.
    still_x = _centre(box)[0] - w / 2
    still_y = _vertical_offset(box, h, face_y)
    x = _axis(segment, xs, w, max(0, width - w), still_x)
    y = _axis(segment, ys, h, max(0, height - h), still_y)
    bottom = (box[1] + box[3] - clamp_even(int(still_y), 0, max(0, height - h))) / h
    return f"crop={w}:{h}:x={x}:y={y}", bottom


def _single_chain(segment: Segment, width: int, height: int) -> Tuple[str, float]:
    size = _single_size(segment.people[0], width, height)
    crop, bottom = _crop(segment, 0, size, SINGLE_FACE_Y, width, height)
    return f"{crop},scale={OUT_W}:{OUT_H}:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1", bottom


def _pair_chain(segment: Segment, width: int, height: int, label: str) -> Tuple[str, float]:
    """Two stacked panels, left-hand person on top; returns the lower face bottom."""
    order = sorted(range(2), key=lambda i: _centre(segment.people[i])[0])
    top, bottom = order
    scale = f"scale={OUT_W}:{PANEL_H}:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1"
    top_size = _panel_size(segment.people[top], segment.people[bottom], width, height)
    bottom_size = _panel_size(segment.people[bottom], segment.people[top], width, height)
    top_crop, _ = _crop(segment, top, top_size, TOP_PANEL_FACE_Y, width, height)
    bottom_crop, face_bottom = _crop(segment, bottom, bottom_size, BOTTOM_PANEL_FACE_Y, width, height)
    chain = (
        f"split=2[{label}t][{label}b];"
        f"[{label}t]{top_crop},{scale}[{label}tp];"
        f"[{label}b]{bottom_crop},{scale}[{label}bp];"
        f"[{label}tp][{label}bp]vstack=inputs=2"
    )
    return chain, 0.5 + face_bottom * 0.5


def _fit_chain(label: str, blur_sigma: int = 14) -> str:
    from .reframing import FIT_CHAIN

    return (
        f"split=2[{label}g][{label}f];"
        f"[{label}g]scale=540:960:force_original_aspect_ratio=increase,crop=540:960,"
        f"gblur=sigma={blur_sigma}:steps=2,scale={OUT_W}:{OUT_H}:flags=bilinear,setsar=1[{label}bg];"
        f"[{label}f]{FIT_CHAIN}[{label}fg];"
        f"[{label}bg][{label}fg]overlay=(W-w)/2:(H-h)/2"
    )


def build_interview_filter(
    segments: Sequence[Segment], width: int, height: int
) -> Tuple[str, float]:
    """One-decode filter_complex producing [vout], and the lowest face bottom."""
    count = len(segments)
    parts = [f"[0:v]split={count}" + "".join(f"[s{i}]" for i in range(count))] if count > 1 else []
    lowest = 0.0
    for i, segment in enumerate(segments):
        source = f"[s{i}]" if count > 1 else "[0:v]"
        if count == 1:
            trim = ""
        elif i == count - 1:
            # No end: a stream running past the probed duration still renders.
            trim = f"trim=start={segment.start:.3f},"
        elif i == 0:
            trim = f"trim=end={segment.end:.3f},"
        else:
            trim = f"trim=start={segment.start:.3f}:end={segment.end:.3f},"
        if segment.kind == "single":
            chain, bottom = _single_chain(segment, width, height)
            lowest = max(lowest, bottom)
        elif segment.kind == "pair":
            chain, bottom = _pair_chain(segment, width, height, f"c{i}")
            lowest = max(lowest, bottom)
        else:
            chain = _fit_chain(f"c{i}")
        parts.append(f"{source}{trim}{chain},format=yuv420p,setpts=PTS-STARTPTS[v{i}]")
    if count > 1:
        parts.append("".join(f"[v{i}]" for i in range(count)) + f"concat=n={count}:v=1:a=0[vout]")
    else:
        parts[-1] = parts[-1][: -len("[v0]")] + "[vout]"
    return ";".join(parts), lowest


def frame_changes(input_path: Path) -> Tuple[List[float], np.ndarray]:
    """Presentation time and a 64x36 grey thumbnail of every frame.

    One tiny full-rate decode; ``precise_cuts`` reads the edits from it.
    """
    with tempfile.NamedTemporaryFile(suffix=".log") as log:
        command = [
            "ffmpeg", "-v", "info", "-nostdin", "-an", "-sn", "-i", str(input_path),
            "-vf", f"scale={CUT_PROBE_W}:{CUT_PROBE_H},format=gray,showinfo",
            # One output frame per decoded frame, so frames and times pair up.
            "-fps_mode", "passthrough", "-f", "rawvideo", "-",
        ]
        with open(log.name, "wb") as errors:
            proc = subprocess.run(command, stdout=subprocess.PIPE, stderr=errors, timeout=300)
        if proc.returncode != 0:
            raise RuntimeError("cut probe decode failed")
        times = [float(m) for m in re.findall(rb"pts_time:\s*(-?[\d.]+)", Path(log.name).read_bytes())]
    size = CUT_PROBE_W * CUT_PROBE_H
    count = min(len(times), len(proc.stdout) // size)
    frames = np.frombuffer(proc.stdout[: count * size], np.uint8).reshape(count, size)
    return times[:count], frames


def precise_cuts(
    times: Sequence[float], frames: np.ndarray, coarse: Sequence[float], sample_period: float = 1 / 3
) -> List[float]:
    """Frame-accurate camera cuts: hard cuts found at full rate, plus soft ones.

    A cut is a picture change over one to CUT_MAX_FRAMES frames (a straight
    cut, or a short whip or flash transition) that is much larger than the
    frame changes just before and after it and than the clip's local motion.
    The frames either side must differ, so a camera flash that returns to the
    same picture is not a cut. Its time sits halfway through the transition,
    so ``trim`` splits on the edit. A coarse (sampled) cut with no cut found
    in the third of a second before it is a dissolve or a slow change; it
    keeps its time.
    """
    count = min(len(times), len(frames))
    hard: List[float] = []
    if count >= 2:
        pix = frames[:count].astype(np.int16)
        steps = np.abs(np.diff(pix, axis=0)).mean(axis=1)
        n = len(steps)
        i = 0
        while i < n:
            found = None
            for k in range(1, CUT_MAX_FRAMES + 1):
                if i + k > n or float(steps[i:i + k].min()) < CUT_MIN_DIFF * (0.5 if k > 1 else 1):
                    break
                span = float(np.abs(pix[i + k] - pix[i]).mean())
                outside = max(float(steps[i - 1]) if i > 0 else 0.0,
                              float(steps[i + k]) if i + k < n else 0.0)
                lo, hi = max(0, i - 15), min(n, i + k + 15)
                local = np.concatenate([steps[lo:i], steps[i + k:hi]])
                level = float(np.median(local)) if len(local) else 0.0
                if (span >= CUT_MIN_DIFF and float(steps[i:i + k].min()) >= CUT_PEAK_RATIO * outside
                        and span >= CUT_SPIKE_RATIO * max(level, 1.5)):
                    found = k
                    break
            if found:
                hard.append((times[i] + times[i + found]) / 2)
                i += found
            else:
                i += 1
    soft = [
        c for c in coarse
        if not any(c - sample_period - 0.07 <= h <= c + 0.04 for h in hard)
    ]
    return sorted(hard + soft)


def plan_interview_layout(
    width: int,
    height: int,
    duration: float,
    scene_cuts: Sequence[float],
    face_samples: Sequence[FaceFrame],
    context: Sequence[Any],
    input_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Per-shot single/pair/fit plan, or None when the clip is not an interview.

    The clip needs at least PAIR_MIN_SECONDS of a two-shot. Two low faces
    side by side and nothing else are webcam tiles, which keep the call
    stack (``choose_talking_head_layout``) even across a jump cut. With
    ``input_path`` the switches land on the exact frame of each camera cut
    (``precise_cuts``), including cuts the sampled analysis missed.
    """
    from .reframing import compute_vertical_crop_dims

    if width < 2 or height < 2 or duration <= 0 or not face_samples or not context:
        return None
    if width / height <= 1.05:
        return None
    crop_w, _ = compute_vertical_crop_dims(width, height)

    def interview(cuts: Sequence[float]) -> Optional[List[Segment]]:
        segments = _segments(duration, cuts, face_samples, context, width, height, crop_w)
        pairs = [s for s in segments if s.kind == "pair"]
        if sum(s.end - s.start for s in pairs) < PAIR_MIN_SECONDS:
            return None
        singles = [s for s in segments if s.kind == "single"]
        if not singles and all(min(b[1] for b in s.people) > height * WEBCAM_FACE_TOP for s in pairs):
            return None
        return segments

    coarse = [c for c in scene_cuts if 0 < c < duration]
    segments = interview(coarse)
    if segments is None:
        return None
    if input_path is not None:
        try:
            times, thumbnails = frame_changes(input_path)
            cuts = [c for c in precise_cuts(times, thumbnails, coarse) if 0 < c < duration]
            segments = interview(cuts)
        except Exception as exc:
            logger.warning("Frame-accurate cut search failed (%s); switching on sampled cuts", exc)
        if segments is None:
            return None
    _pool_setups(segments, width)
    segments = _merge_neighbours(segments)
    video_filter, lowest = build_interview_filter(segments, width, height)
    summary = [
        {"start": round(s.start, 3), "end": round(s.end, 3), "kind": s.kind, "reason": s.reason}
        for s in segments
    ]
    logger.info("Interview layout: %s", summary)
    result = {
        "kind": "interview",
        "video_filter": video_filter,
        "segments": summary,
        "face_bottom": lowest,
    }
    # The hook title sits in the top safe area, which in a stacked two-shot is
    # the top person's face. Opening on a pair, put it on the panel seam.
    if any(s.kind == "pair" and s.start < HOOK_TITLE_SECONDS for s in segments):
        result["hook_center_y"] = PAIR_SEAM_Y
    return result
