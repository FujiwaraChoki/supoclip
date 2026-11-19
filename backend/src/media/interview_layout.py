"""Per-shot 9:16 composition for sit-down interviews.

A two-person interview edit cuts between a wide two-shot and single
close-ups. No single 9:16 crop shows both people in the two-shot, so the
generic planner kept that shot (and, through its hysteresis, the whole clip)
as a letterboxed full frame. This module composes every camera shot on its
own and switches only at the edit:

- ``pair``: two people too far apart for one crop. Each gets a face-centred
  1080x960 panel, stacked with the left-hand person on top. Three or four
  people in a row (a table, a booth, friends walking) are divided at the
  widest gap between them: the left-hand people share the top panel and the
  right-hand people the bottom one, so everyone is on screen once and the
  frame is full (reason ``group``).
- ``single``: one person (or two people close enough to share a crop) in a
  tight, head-and-shoulders 9:16 crop.
- ``cover``: live footage without a close subject (a plate of food, a
  product, a street, a distant figure) fills the frame with a full-height
  9:16 crop on what is in focus, or on the lone small face. Reported as
  ``single`` with reason ``cover``: to captions and the hook title it is a
  frame-filling crop like any other.
- ``fit``: an on-screen paragraph (a tweet, a document, an explainer
  graphic), a still picture without people (a chart, a screenshot), a crowd
  or people who cannot be divided into two panels keep the whole picture on a
  blurred background.

Crops follow their face with the same eased, dead-zoned trajectory as the
tracked crop, so a tripod shot stays still and a handheld one is followed.
Every return to the same camera setup gets the same zoom.

It only takes over clips that contain a real two-shot (see
``plan_interview_layout``); calls, reactions, slides and sports keep their
existing layouts. A lone person whose face would otherwise be staged on a
blurred frame gets the same ``single`` crop (``plan_solo_layout``, chosen by
``talking_head_layout``). Diarized speaker cuts are not used here:
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
from .scene_context import COVER_MAX_TEXT_BLOCK_RATE, COVER_MIN_CAMERA_MOTION, COVER_MIN_MOTION

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
PAIR_MAX_DY_FRAC = 0.3
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

# A row of three or four people is divided into two panels. Their faces sit
# within PAIR_MAX_DY_FRAC of each other (a 2x2 call grid does not), the
# smallest is at least this share of the largest, and each side's faces plus
# this margin (as a share of their span) fit a 9:8 panel.
GROUP_MAX_PEOPLE = 4
GROUP_MIN_SIZE_RATIO = 0.45
GROUP_PANEL_MARGIN = 1.2
# Two clusters that never appear together, this many face widths apart at
# most and of similar size, are one person who moved.
CLUSTER_JOIN_WIDTHS = 1.6
CLUSTER_JOIN_SIZE_RATIO = 0.6
# A third or fourth person seen less than this share of the shot (a passer-by,
# a stray detection) is left out when the row cannot be divided with them.
GROUP_MEMBER_SUPPORT = 0.75
# A face box this close to the left or right frame edge is cut by the frame.
EDGE_CUT_FRAC = 0.015
# A person seen in only one sample of a longer shot is a stray detection.
CLUSTER_MIN_SAMPLES = 2
# The two people of a pair are on screen together in this share of the shot.
PAIR_MIN_TOGETHER = 0.3
# Cover: mean 8-bit change between analysis samples that marks live footage
# even where the motion measurement gave up (a whip pan, a fast handheld move).
COVER_MIN_DIFFERENCE = 10.0
# A face cluster this steady in a shot without a close subject is its subject.
COVER_FACE_SUPPORT = 0.6

# A lone person on camera (``plan_solo_layout``) gets the single crop only when
# its face centre lands at or above this share of the output height; lower,
# the face cannot be lifted without zooming past the upscale cap.
SOLO_MAX_FACE_Y = 0.48
# A solo crop may enlarge at most this much. Any 9:16 crop of a 720p source
# is 1920/720 = 2.67x (the plain tracked crop is too); smaller sources would
# turn to mush, so they keep the staged layout.
SOLO_MAX_UPSCALE = 2.7

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
    # Centre (0-1 of the width) of a ``cover`` crop.
    cover_x: float = 0.5


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
            best, nearest = None, 0.0
            for cluster in clusters:
                if t in cluster["track"]:
                    continue
                # A person is where they were last seen (a handheld camera
                # pans, people lean and walk) or at their usual place.
                distance = min(abs(cx - cluster["cx"]), abs(cx - cluster["last"]))
                reach = max(width * 0.06, 0.8 * max(box[2], cluster["w"]))
                if distance <= reach and (best is None or distance < nearest):
                    best, nearest = cluster, distance
            if best is None:
                best = {"cx": cx, "last": cx, "w": box[2], "track": {}}
                clusters.append(best)
            best["track"][t] = tuple(float(v) for v in box)
            best["last"] = cx
            boxes = list(best["track"].values())
            best["cx"] = float(np.median([b[0] + b[2] / 2 for b in boxes]))
            best["w"] = float(np.median([b[2] for b in boxes]))
    # One person lost for a moment and found a little further along (they
    # leaned over, the camera swung) is two clusters that never share a
    # sample: join them, nearest first.
    while True:
        joins = [
            (abs(a["cx"] - b["cx"]), i, j)
            for i, a in enumerate(clusters) for j, b in enumerate(clusters[i + 1:], i + 1)
            if not set(a["track"]) & set(b["track"])
            and abs(a["cx"] - b["cx"]) <= CLUSTER_JOIN_WIDTHS * max(a["w"], b["w"])
            and min(a["w"], b["w"]) >= CLUSTER_JOIN_SIZE_RATIO * max(a["w"], b["w"])
        ]
        if not joins:
            break
        _, i, j = min(joins)
        clusters[i]["track"].update(clusters.pop(j)["track"])
        boxes = list(clusters[i]["track"].values())
        clusters[i]["cx"] = float(np.median([b[0] + b[2] / 2 for b in boxes]))
        clusters[i]["w"] = float(np.median([b[2] for b in boxes]))
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


def _union(boxes: Sequence[Box]) -> Box:
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    return (x0, y0, max(b[0] + b[2] for b in boxes) - x0, max(b[1] + b[3] for b in boxes) - y0)


def _union_track(members: Sequence[Dict[str, Any]]) -> Dict[float, Box]:
    """The box around ``members`` at every sample that shows all of them."""
    shared = set(members[0]["track"])
    for member in members[1:]:
        shared &= set(member["track"])
    return {t: _union([m["track"][t] for m in members]) for t in sorted(shared)}


def _edge_cut(box: Box, width: int) -> bool:
    """A face the source frame itself cuts off: someone half out of shot beside the subject.

    A panel on them would mostly show the subject a second time.
    """
    return box[0] < width * EDGE_CUT_FRAC or box[0] + box[2] > width * (1 - EDGE_CUT_FRAC)


def _group_panels(
    people: Sequence[Dict[str, Any]], width: int, height: int
) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """Divide a row of people into the left-hand and right-hand panel, or None.

    The row is cut at the widest gap between neighbours whose two sides each
    fit a 9:8 panel. A crowd, people at different heights (a call grid, a
    stage with an audience) and faces of very different size are not a row.
    """
    if not 3 <= len(people) <= GROUP_MAX_PEOPLE:
        return None
    row = sorted(people, key=lambda c: _centre(c["box"])[0])
    heights = [c["box"][3] for c in row]
    centres = [_centre(c["box"])[1] for c in row]
    if min(heights) < max(heights) * GROUP_MIN_SIZE_RATIO:
        return None
    if max(centres) - min(centres) > height * PAIR_MAX_DY_FRAC:
        return None
    panel_w = min(float(width), height * 9 / 8)
    best = None
    for index in range(1, len(row)):
        sides = (row[:index], row[index:])
        if any(_union([c["box"] for c in side])[2] * GROUP_PANEL_MARGIN > panel_w for side in sides):
            continue
        gap = _centre(row[index]["box"])[0] - _centre(row[index - 1]["box"])[0]
        if best is None or gap > best[0]:
            best = (gap, sides)
    return (list(best[1][0]), list(best[1][1])) if best else None


def _cover_centre(
    clusters: Sequence[Dict[str, Any]], evidence: Sequence[Any], width: int
) -> Optional[float]:
    """Centre (0-1) of a full-height crop for a shot without a close subject, or None to keep the whole frame.

    Live footage fills the frame the way a short-form editor cuts it; a still
    picture (a chart, a screenshot, a photo of a document) and a shot showing
    a paragraph keep the whole frame. The crop sits on a steady face, however
    small, and otherwise on the densest detail (``focus_center``).
    """
    if not evidence:
        return None
    moving = (
        float(np.median([e.motion for e in evidence])) >= COVER_MIN_MOTION
        or float(np.median([e.camera_motion for e in evidence])) >= COVER_MIN_CAMERA_MOTION
        or float(np.median([e.difference for e in evidence])) >= COVER_MIN_DIFFERENCE
    )
    if not moving:
        return None
    if sum(e.text_block_lines >= 2 for e in evidence) / len(evidence) > COVER_MAX_TEXT_BLOCK_RATE:
        return None
    steady = [c for c in clusters if c["support"] >= COVER_FACE_SUPPORT]
    if steady:
        subject = max(steady, key=lambda c: c["box"][3])
        return _centre(subject["box"])[0] / max(width, 1)
    # Someone the camera is swinging past: no steady cluster, but a face in
    # most samples. People half out of the frame are not the subject.
    seen: Dict[float, Box] = {}
    for cluster in clusters:
        for t, box in cluster["track"].items():
            if not _edge_cut(box, width) and (t not in seen or box[3] > seen[t][3]):
                seen[t] = box
    if len(seen) >= COVER_FACE_SUPPORT * len(evidence):
        return float(np.median([_centre(b)[0] for b in seen.values()])) / max(width, 1)
    return float(np.median([getattr(e, "focus_center", 0.5) for e in evidence]))


def _classify(
    segment: Segment, samples: Sequence[FaceFrame], width: int, height: int, crop_w: int,
    evidence: Sequence[Any] = (),
) -> Segment:
    segment.times = [t for t, _ in samples]
    if segment.graphic:
        segment.kind, segment.reason = "fit", "graphic"
        return segment
    clusters = _clusters(samples, width, height)
    enough = min(CLUSTER_MIN_SAMPLES, len(samples))
    people = [
        c for c in clusters
        if c["support"] >= CLUSTER_SUPPORT and len(c["track"]) >= enough
        and c["box"][3] >= height * MIN_FACE_H_FRAC
    ]

    def cover(reason: str) -> Segment:
        x = _cover_centre(clusters, evidence, width)
        segment.kind, segment.reason = ("fit" if x is None else "cover"), reason
        if x is not None:
            segment.cover_x = x
        return segment

    if not people:
        return cover("no_subject")
    if len(people) >= 3:
        panels = _group_panels(people, width, height)
        while panels is None and len(people) >= 3:
            weakest = min(people, key=lambda c: (c["support"], c["box"][3]))
            if weakest["support"] >= GROUP_MEMBER_SUPPORT:
                break
            people = [c for c in people if c is not weakest]
            panels = _group_panels(people, width, height)
    if len(people) >= 3:
        if panels is None:
            segment.kind, segment.reason = "fit", "group"
            return segment
        segment.kind, segment.reason = "pair", "group"
        segment.people = [_union([c["box"] for c in side]) for side in panels]
        segment.tracks = [_union_track(side) for side in panels]
        return segment
    if len(people) == 2:
        left, right = sorted(people, key=lambda c: _centre(c["box"])[0])
        lb, rb = left["box"], right["box"]
        span = max(lb[0] + lb[2], rb[0] + rb[2]) - min(lb[0], rb[0])
        small, large = sorted((lb[3], rb[3]))
        dy = abs(_centre(lb)[1] - _centre(rb)[1])
        together = len(set(left["track"]) & set(right["track"])) / max(1, len(samples))
        if together < PAIR_MIN_TOGETHER:
            # One person found in two places (a pan, a turn of the head): frame the steadier one.
            people = [max(people, key=lambda c: (c["support"], c["box"][3]))]
        elif span <= crop_w * SHARED_CROP_SPAN:
            segment.kind, segment.reason = "single", "shared_crop"
            segment.people, segment.tracks = [_union([lb, rb])], [_union_track([left, right])]
            return segment
        elif (small >= large * PAIR_MIN_SIZE_RATIO and dy <= height * PAIR_MAX_DY_FRAC
              and not any(_edge_cut(c["box"], width) for c in people)):
            segment.kind, segment.reason = "pair", "two_people"
            segment.people, segment.tracks = [lb, rb], [left["track"], right["track"]]
            return segment
        else:
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
        # a tight crop would invent a subject. Live footage gets a full-height
        # crop around it; a still picture stays whole.
        return cover("small_subject")
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
    measured = {round(float(e.time), 3): e for e in context}

    def evidence(part: Sequence[FaceFrame]) -> List[Any]:
        return [measured[k] for k in (round(float(t), 3) for t, _ in part) if k in measured]

    def signage(part: Sequence[FaceFrame]) -> bool:
        # A paragraph in a handheld shot of people is a sign or a menu behind
        # them, not a graphic laid over the picture: the people are the shot.
        found = evidence(part)
        if not part or not found:
            return False
        if float(np.median([e.camera_motion for e in found])) < COVER_MIN_CAMERA_MOTION:
            return False
        shot = _classify(Segment(part[0][0], part[-1][0]), part, width, height, crop_w, found)
        return shot.kind in {"single", "pair"}

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
            if is_graphic and signage(part):
                is_graphic = False
            pieces = (
                [(a, b, None)] if is_graphic or not part else
                _runs([t for t, _ in part], [_face_state(f, height) for _, f in part], a, b, FACE_STATE_MIN_SAMPLES)
            )
            for c, d, _ in pieces:
                sub = [(t, faces) for t, faces in part if c <= t < d]
                segment = Segment(c, d, graphic=bool(is_graphic) and bool(sub))
                if sub:
                    segments.append(_classify(segment, sub, width, height, crop_w, evidence(sub)))
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
    # A panel on someone near the frame edge is pushed back into the frame,
    # towards the neighbour: it may only reach as far as the neighbour's face.
    if _centre(box)[0] >= _centre(other)[0]:
        room = width - (_centre(other)[0] + other[2] * 0.6)
    else:
        room = _centre(other)[0] - other[2] * 0.6
    reach = min(gap * 2, room)
    if reach < panel_h * 9 / 8:
        panel_h = max(PANEL_H / MAX_PANEL_UPSCALE, reach * 8 / 9)
    # Several people sharing the panel (a divided group) all stay inside it.
    panel_h = max(panel_h, box[2] * GROUP_PANEL_MARGIN * 8 / 9)
    panel_h = round_to_even(int(min(panel_h, height, width * 8 / 9)))
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


def _single_placement(box: Box, width: int, height: int) -> Tuple[float, float]:
    """(face-centre output y, upscale) of a still single crop on ``box``."""
    w, h = _single_size(box, width, height)
    top = clamp_even(int(_vertical_offset(box, h, SINGLE_FACE_Y)), 0, max(0, height - h))
    return (_centre(box)[1] - top) / max(h, 1), OUT_H / max(h, 1)


def _single_chain(segment: Segment, width: int, height: int) -> Tuple[str, float]:
    size = _single_size(segment.people[0], width, height)
    crop, bottom = _crop(segment, 0, size, SINGLE_FACE_Y, width, height)
    return f"{crop},scale={OUT_W}:{OUT_H}:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1", bottom


def _cover_chain(segment: Segment, width: int, height: int) -> str:
    """Still full-height 9:16 crop centred on ``segment.cover_x``."""
    from .reframing import compute_vertical_crop_dims

    crop_w, crop_h = compute_vertical_crop_dims(width, height)
    x = clamp_even(int(segment.cover_x * width - crop_w / 2), 0, max(0, width - crop_w))
    y = clamp_even(int((height - crop_h) / 2), 0, max(0, height - crop_h))
    return f"crop={crop_w}:{crop_h}:{x}:{y},scale={OUT_W}:{OUT_H}:flags=lanczos,{UPSCALE_SHARPEN_FILTER},setsar=1"


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
        elif segment.kind == "cover":
            chain = _cover_chain(segment, width, height)
        else:
            chain = _fit_chain(f"c{i}")
        parts.append(f"{source}{trim}{chain},format=yuv420p,setpts=PTS-STARTPTS[v{i}]")
    if count > 1:
        parts.append("".join(f"[v{i}]" for i in range(count)) + f"concat=n={count}:v=1:a=0[vout]")
    else:
        parts[-1] = parts[-1][: -len("[v0]")] + "[vout]"
    return ";".join(parts), lowest


def _reported(segment: Segment) -> Dict[str, Any]:
    """A shot as the layout report lists it: ``pair``, ``single`` or ``fit``.

    A cover is a frame-filling crop, so captions and the hook title treat it
    as a ``single``; its reason says ``cover``.
    """
    cover = segment.kind == "cover"
    return {
        "start": round(segment.start, 3), "end": round(segment.end, 3),
        "kind": "single" if cover else segment.kind,
        "reason": "cover" if cover else segment.reason,
    }


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


def _on_exact_cuts(
    build: Any, scene_cuts: Sequence[float], duration: float, input_path: Optional[Path]
) -> Optional[List[Segment]]:
    """``build(cuts)`` on the sampled cuts, then again on frame-accurate ones.

    None when either plan is refused. If the frame search fails, the plan on
    the sampled cuts stands.
    """
    coarse = [c for c in scene_cuts if 0 < c < duration]
    segments = build(coarse)
    if segments is None or input_path is None:
        return segments
    try:
        times, thumbnails = frame_changes(input_path)
        cuts = [c for c in precise_cuts(times, thumbnails, coarse) if 0 < c < duration]
        segments = build(cuts)
    except Exception as exc:
        logger.warning("Frame-accurate cut search failed (%s); switching on sampled cuts", exc)
    return segments


def plan_solo_layout(
    width: int,
    height: int,
    duration: float,
    scene_cuts: Sequence[float],
    face_samples: Sequence[FaceFrame],
    input_path: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """The interview's tight ``single`` crop for one person on camera, or None.

    Every shot must show one person (one_person in ``_classify``: a face at
    least SINGLE_MIN_FACE_H_FRAC of the frame tall, nobody else). The crop
    follows that face, eyes near the upper third, and each return to the same
    camera setup gets the same zoom. Refused (None) when a shot shows nobody,
    a second person or only a small face, when the face sits too low in the
    frame to reach SOLO_MAX_FACE_Y within the upscale cap, or when the source
    is too small to crop sharply (SOLO_MAX_UPSCALE).
    """
    from .reframing import compute_vertical_crop_dims

    if width < 2 or height < 2 or duration <= 0 or not face_samples:
        return None
    if width / height <= 1.05:
        return None
    crop_w, _ = compute_vertical_crop_dims(width, height)

    def solo(cuts: Sequence[float]) -> Optional[List[Segment]]:
        segments = _segments(duration, cuts, face_samples, (), width, height, crop_w)
        if not segments or any(s.kind != "single" or s.reason != "one_person" for s in segments):
            return None
        return segments

    segments = _on_exact_cuts(solo, scene_cuts, duration, input_path)
    if segments is None:
        return None
    _pool_setups(segments, width)
    placements = [_single_placement(s.people[0], width, height) for s in segments]
    face_y = max(y for y, _ in placements)
    upscale = max(scale for _, scale in placements)
    if face_y > SOLO_MAX_FACE_Y or upscale > SOLO_MAX_UPSCALE:
        logger.info("Solo crop refused: face at %.2f, upscale %.2fx", face_y, upscale)
        return None
    video_filter, lowest = build_interview_filter(segments, width, height)
    summary = [
        {"start": round(s.start, 3), "end": round(s.end, 3), "kind": s.kind, "reason": s.reason}
        for s in segments
    ]
    logger.info("Solo layout: %s", summary)
    return {
        "kind": "solo",
        "video_filter": video_filter,
        "segments": summary,
        "face_bottom": lowest,
        "face_y": face_y,
        "upscale": upscale,
    }


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
        # A divided group does not make a clip an interview by itself: a
        # three-host podcast or a call gallery keeps speaker framing and the
        # gallery layouts. Beside a real two-shot it is composed like one.
        pairs = [s for s in segments if s.kind == "pair" and s.reason == "two_people"]
        if sum(s.end - s.start for s in pairs) < PAIR_MIN_SECONDS:
            return None
        singles = [s for s in segments if s.kind == "single"]
        if not singles and all(min(b[1] for b in s.people) > height * WEBCAM_FACE_TOP for s in pairs):
            return None
        return segments

    segments = _on_exact_cuts(interview, scene_cuts, duration, input_path)
    if segments is None:
        return None
    _pool_setups(segments, width)
    segments = _merge_neighbours(segments)
    video_filter, lowest = build_interview_filter(segments, width, height)
    summary = [_reported(s) for s in segments]
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
