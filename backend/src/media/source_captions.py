"""Captions already burned into a source video, and what the render does about them.

Shorts, reposts and subtitled uploads carry their own text. A second caption
layer on top of it makes both unreadable, and a 9:16 crop of a 16:9 picture
cuts a subtitle line off at both ends. ``analyze_source_captions`` finds the
band the source writes in and reads it (local OCR), and ``plan_caption_action``
decides, per clip:

- ``suppress``: the source already captions the speech and the layout shows
  those captions whole. Ours would say everything twice, so they are left off.
- ``mask``: the layout crops the picture, so the source's lines are cut. They
  are painted out of the cropped shots (``mask_caption_band``) and ours are
  drawn as usual: one clean caption layer.
- ``avoid``: text that is not the speech (a translation, a label, a title), or
  captions that only cover part of the clip: ours move clear of the band.

Detection is local. Two readers vote on each sampled frame: the OCR (any text
style it can read: small lowercase subtitles, coloured or boxed text) and a
shape test for bright outlined glyphs (bold creator captions the OCR cannot
read). Frames are sampled while somebody speaks, which is when captions show.
A band needs text at the same height in a good share of them.
"""

import math
import re
import subprocess
import unicodedata
from difflib import SequenceMatcher
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import cv2
import numpy as np

from .common import KENBURNS_FOCUS_Y, KENBURNS_ZOOM_DELTA, logger

Band = Tuple[float, float]  # (top, bottom) as fractions of the frame height

ANALYSIS_WIDTH = 540
# Only the lower part of the frame matters: that is where captions are drawn.
SEARCH_TOP = 0.40
SEARCH_BOTTOM = 0.97
SAMPLE_COUNT = 12
# Share of sampled frames that must show a caption line at the same height.
MIN_FRAME_SHARE = 0.4
BAND_TOLERANCE = 0.06
# Bold word-by-word creator captions are a tenth of the frame tall; subtitles a third of that.
MAX_GLYPH_HEIGHT = 0.12
MAX_LINE_HEIGHT = 0.16


def caption_lines_in_frame(frame: np.ndarray) -> List[Band]:
    """Return centred, outlined text lines in the lower part of ``frame``.

    Burned captions are bright glyphs edged by a dark outline or shadow, many
    glyphs side by side on one line around the horizontal centre. Natural
    edges (a shirt print, a window) rarely produce that combination.
    """
    if frame is None or frame.size == 0:
        return []
    height, width = frame.shape[:2]
    scale = ANALYSIS_WIDTH / float(width)
    if scale != 1.0:
        frame = cv2.resize(
            frame, (ANALYSIS_WIDTH, max(1, int(round(height * scale)))),
            interpolation=cv2.INTER_AREA,
        )
    gray = frame if frame.ndim == 2 else cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    # Lime, yellow and pink captions are bright in their strongest channel, not in grey.
    value = gray if frame.ndim == 2 else frame.max(axis=2)
    return _caption_lines_polarity(gray, value) + _caption_lines_polarity(255 - gray)


def _caption_lines_polarity(gray: np.ndarray, value: Optional[np.ndarray] = None) -> List[Band]:
    h, w = gray.shape
    top, bottom = int(h * SEARCH_TOP), int(h * SEARCH_BOTTOM)
    region = gray[top:bottom]

    bright = (gray if value is None else value)[top:bottom] >= 195
    dark = cv2.dilate((region <= 70).astype(np.uint8), np.ones((5, 5), np.uint8))
    strokes = (bright & (dark > 0)).astype(np.uint8)

    # Glyph-sized pieces of outlined bright stroke.
    count, _, stats, centers = cv2.connectedComponentsWithStats(strokes)
    glyph_mask = np.zeros_like(strokes)
    glyphs: List[Tuple[float, float]] = []
    min_glyph, max_glyph = h * 0.008, h * MAX_GLYPH_HEIGHT
    for index in range(1, count):
        x, y, cw, ch, area = stats[index]
        if not (min_glyph <= ch <= max_glyph and cw <= ch * 3 and area >= 6):
            continue
        glyph_mask[y:y + ch, x:x + cw] = 1
        glyphs.append((float(centers[index][0]), float(centers[index][1])))
    if len(glyphs) < 5:
        return []

    # Join glyphs into lines.
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (max(3, int(w * 0.05)), max(3, int(h * 0.006)))
    )
    lines = cv2.morphologyEx(glyph_mask, cv2.MORPH_CLOSE, kernel)
    count, _, stats, _ = cv2.connectedComponentsWithStats(lines)
    found: List[Band] = []
    for index in range(1, count):
        x, y, cw, ch, _area = stats[index]
        if not (h * 0.015 <= ch <= h * MAX_LINE_HEIGHT and cw >= w * 0.25):
            continue
        if abs((x + cw / 2) - w / 2) > w * 0.15:
            continue
        inside = [
            gx for gx, gy in glyphs
            if x <= gx <= x + cw and y <= gy <= y + ch
        ]
        # Several glyphs spread across the line, not one long edge.
        if len(inside) < 6 or (max(inside) - min(inside)) < cw * 0.6:
            continue
        found.append(((top + y) / h, (top + y + ch) / h))
    return found


def aggregate_caption_bands(
    frame_lines: Sequence[Sequence[Band]], min_share: float = MIN_FRAME_SHARE
) -> Optional[Band]:
    """Return the band holding caption lines in enough frames, else None."""
    sampled = len(frame_lines)
    if not sampled:
        return None
    lines = [line for frame in frame_lines for line in frame]
    if not lines:
        return None
    centre = float(np.median([(a + b) / 2 for a, b in lines]))
    hits = [
        [line for line in frame if abs((line[0] + line[1]) / 2 - centre) <= BAND_TOLERANCE]
        for frame in frame_lines
    ]
    hit_frames = sum(1 for frame in hits if frame)
    if hit_frames < max(2, int(np.ceil(sampled * min_share))):
        return None
    tops = [line[0] for frame in hits for line in frame]
    bottoms = [line[1] for frame in hits for line in frame]
    return float(np.percentile(tops, 10)), float(np.percentile(bottoms, 90))


def band_through_full_height_crop(band: Band, zoom_delta: float = KENBURNS_ZOOM_DELTA) -> Band:
    """Where ``band`` can appear after a full-height crop with a slow punch-in.

    A crop that keeps the whole source height leaves vertical positions alone;
    the Ken Burns zoom (up to ``1 + zoom_delta``, toward ``KENBURNS_FOCUS_Y``)
    then moves a source row ``y`` to ``zoom * y - KENBURNS_FOCUS_Y * (zoom - 1)``.
    The band covers both ends of the zoom.
    """
    zoom = 1 + zoom_delta

    def zoomed(y: float) -> float:
        return zoom * y - KENBURNS_FOCUS_Y * (zoom - 1)

    top, bottom = band
    return max(0.0, min(top, zoomed(top))), min(1.0, max(bottom, zoomed(bottom)))


def detect_rendered_caption_band(video_path: Path, video_filter: str, mode: str, duration: float) -> Optional[Band]:
    """Inspect the actual composed layout, so fit/split/cropped sources use output coordinates.

    Only called after source text is detected; ordinary footage avoids this extra pass.
    Sampling follows composition, preserving its crop, zoom and split placement.
    """
    rate = SAMPLE_COUNT / max(1.0, duration)
    tail = f"fps={rate:.9f},scale=540:960,format=gray"
    command = ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video_path), "-an", "-sn"]
    if mode == "complex":
        command += ["-filter_complex", f"{video_filter};[vout]{tail}[caption_probe]", "-map", "[caption_probe]"]
    else:
        command += ["-vf", f"{video_filter},{tail}"]
    command += ["-frames:v", str(SAMPLE_COUNT), "-f", "rawvideo", "-"]
    result = subprocess.run(command, capture_output=True, timeout=max(60, int(duration * 4)))
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", "replace")[-500:])
    data = np.frombuffer(result.stdout, dtype=np.uint8)
    size = 540 * 960
    frames = data[:len(data) // size * size].reshape(-1, 960, 540)
    return aggregate_caption_bands([caption_lines_in_frame(frame) for frame in frames])


# --- reading the band ------------------------------------------------------

ANALYSIS_SAMPLES = 16
# Frames looked at first; a clip showing no text in any of them has no captions.
FIRST_LOOK_SAMPLES = 8
# Captions that come and go (only one of two voices is subtitled, a creator captions only the phrases they
# stress) show in fewer frames than MIN_FRAME_SHARE. This many frames with a line of two words or more at one
# height that says what is being said are captions too; so are MIN_SUBTITLE_FRAMES frames of whole lines centred
# at the bottom that are not the speech (a translation, a line for a voice the transcript missed). They are
# painted only while they are on screen, so a wrong call costs a strip of picture for a second.
MIN_READ_FRAMES = 2
MIN_SUBTITLE_FRAMES = 2
# The pass that looks for outlined glyph rows over the whole clip, and how many of its moments are read.
SCAN_FPS = 2
SCAN_SECOND_READ_SECONDS = 1.0
SCAN_MAX_EXTRA_READS = 10
SPEECH_REACH_SECONDS = 2.0      # a caption may come up this long before its word and stay as long after
MIN_SAMPLE_GAP_SECONDS = 0.4
MATCH_TOKEN_SHARE = 0.5         # of a frame's written words that are being said
SPEECH_MATCH_SHARE = 0.5        # of the frames with readable text whose text is the speech
STATIC_SAME_SHARE = 0.6         # words shared by two frames for the text to count as unchanged
STATIC_PAIR_SHARE = 0.7         # of neighbouring frames with unchanged text: a label, not captions
# Of the moments somebody speaks, the share that must show the source's own captions before ours are left off.
SUPPRESS_MIN_COVERAGE = 0.6
SECOND_LOOK_MARGIN = 0.05       # of the frame height, around the band, for the enlarged second read
READ_TOP = 0.04                 # the OCR reads the frame from here down (above is the player's own chrome)
MAX_BANDS = 5                   # heights the source may write captions at (top, bottom, beside what it shows)
FUZZY_MATCH_RATIO = 0.72        # similarity of a garbled written word to a spoken one
SUBTITLE_MIN_CENTRE = 0.66      # subtitles sit below this height ...
SUBTITLE_MAX_OFFSET = 0.12      # ... and their lines are centred to within this much of the frame width
# Share of the clip a layout must crop before the source's lines count as cut.
MASK_MIN_CROPPED_SHARE = 0.5
MASK_PAD_FRAC = 0.02            # of the frame height, added around the painted-out box
MASK_SOFTEN_SIGMA = 8           # blur of the painted box: no streaks, no glyph shapes
# Painting replaces picture with a soft patch, so it is kept to where captions live and to a few lines.
PAINT_BOTTOM_ZONE = 0.66        # a band centred below this height is in the bottom caption zone
PAINT_TOP_ZONE = 0.24           # ... above this one, in the top caption zone
PAINT_MAX_HEIGHT = 0.16         # of the frame height
PAINT_MAX_BANDS = 3
# Outside the caption strips only a track that is up for nearly all of the speech is captions.
CAPTION_TRACK_MIN_COVERAGE = 0.8
PRESENCE_CELLS = 8              # columns of the caption box read separately
PRESENCE_LEVEL_SHARE = 0.3      # the "text" level sits this far from the empty frames towards the text frames
PRESENCE_FPS = 5                # how often the caption box is checked for text
PRESENCE_EDGE_LEVEL = 80        # horizontal gradient of a letter's edge
PRESENCE_SEPARATION = 1.3       # frames with text must have this many times the edges of frames without
PRESENCE_MARGIN_SECONDS = 0.3   # painted before and after a detected line
PRESENCE_READ_SECONDS = 0.6     # painted either side of a moment text was read, when it cannot be timed
PRESENCE_JOIN_SECONDS = 1.5     # gaps shorter than this between two lines stay painted

_TOKEN = re.compile(r"[^\W_]+", re.UNICODE)


@dataclass(frozen=True)
class SourceCaptions:
    """Text the source burns into its picture. Coordinates are 0-1 fractions of the source frame."""

    band: Band              # rows the text occupies
    left: float             # columns it reaches
    right: float
    kind: str               # "speech" (captions of what is said), "text" (changing, not the speech), "static"
    coverage: float         # share of the sampled speech moments with text in the band
    match: float            # share of the frames with readable text whose text is the speech
    shown: Tuple[float, ...] = ()    # sampled moments (clip seconds) with text in the band ...
    absent: Tuple[float, ...] = ()   # ... and without
    # Lines centred in the lower part of the frame: where subtitles sit, whatever language they are in.
    subtitle_like: bool = True
    # Further bands the source writes in (a creator's captions jump between the top and the bottom).
    more: Tuple["SourceCaptions", ...] = ()
    # The rows also hold text that is not captions (a score bar): ``shown`` are the caption lines only, and
    # only they are painted.
    mixed: bool = False

    @property
    def bands(self) -> Tuple["SourceCaptions", ...]:
        return (self, *self.more)

    @property
    def in_caption_zone(self) -> bool:
        """A strip at the top or the bottom of the frame, where captions are put, a few lines tall."""
        top, bottom = self.band
        centre = (top + bottom) / 2
        return bottom - top <= PAINT_MAX_HEIGHT and (centre >= PAINT_BOTTOM_ZONE or centre <= PAINT_TOP_ZONE)

    @property
    def captions_the_speech(self) -> bool:
        """A few lines that say nearly all of what is said: a caption track wherever the creator put it."""
        top, bottom = self.band
        return self.kind == "speech" and self.coverage >= CAPTION_TRACK_MIN_COVERAGE and bottom - top <= PAINT_MAX_HEIGHT

    @property
    def paintable(self) -> Tuple["SourceCaptions", ...]:
        """Bands a crop slices and the render paints out: captions and subtitles in a caption strip, and a
        caption track that is nearly always up wherever it sits.

        Words set in the middle of the picture now and then (an explainer's key words beside what it shows,
        a label, a chart's title) stay, even when the narrator says them: painting them was tried and blurs
        a third of the frame, which looks worse than a word the crop cuts like the rest of the picture.
        Never a label, a document, a game's prompts, or a block taller than a few lines."""
        found = [
            b for b in self.bands
            if (b.in_caption_zone and (b.kind == "speech" or (b.kind == "text" and b.subtitle_like)))
            or b.captions_the_speech
        ]
        return tuple(found[:PAINT_MAX_BANDS])


def _fold(text: str) -> str:
    """Lower case without accents: the OCR drops them ("Mas" for "Más")."""
    return "".join(char for char in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(char))


def _tokens(text: str) -> List[str]:
    """The words of a line that can be compared: three letters or more, and numbers of two digits or more."""
    return [
        _fold(token) for token in _TOKEN.findall(text or "")
        if len(token) >= 3 or (len(token) == 2 and token.isdigit())
    ]


def _said(token: str, spoken: Sequence[str]) -> bool:
    """Whether a written word is one of the spoken ones, allowing for the OCR's slips: a wrong or missing
    letter, and the garbling heavy display fonts get ("DIEEERENT" for "different")."""
    for other in spoken:
        if token == other:
            return True
        if token.isdigit() and other.isdigit() and (token.startswith(other) or other.startswith(token)):
            return True     # "50?" is read as "507": a number is the number it starts with
        if len(token) >= 5 and len(token) == len(other) and sum(a != b for a, b in zip(token, other)) <= 1:
            return True
        if len(token) >= 4 and abs(len(token) - len(other)) == 1 and (other.startswith(token) or token.startswith(other)):
            return True
        if (len(token) >= 6 and abs(len(token) - len(other)) <= 2
                and SequenceMatcher(None, token, other).ratio() >= FUZZY_MATCH_RATIO):
            return True
    return False


def sample_moments(
    duration: float, words: Optional[Sequence[Dict[str, Any]]] = None, count: int = ANALYSIS_SAMPLES,
) -> List[float]:
    """When to look at the picture: spread over the spoken words (clip seconds), or evenly without them."""
    if duration <= 0:
        return []
    spoken = [
        (float(word["start"]) + float(word["end"])) / 2 for word in words or ()
        if 0 <= float(word["start"]) < duration
    ]
    if len(spoken) >= count:
        picked = [spoken[int(len(spoken) * (step + 0.5) / count)] for step in range(count)]
    else:
        picked = [duration * (step + 0.5) / count for step in range(count)]
    moments: List[float] = []
    for moment in sorted(picked):
        if not moments or moment - moments[-1] >= MIN_SAMPLE_GAP_SECONDS:
            moments.append(min(moment, max(0.0, duration - 0.05)))
    return moments


def _frame_at(capture: "cv2.VideoCapture", seconds: float) -> Optional[np.ndarray]:
    capture.set(cv2.CAP_PROP_POS_MSEC, max(0.0, seconds) * 1000.0)
    ok, frame = capture.read()
    return frame if ok else None


def _frame_lines(
    video_path: Path, capture: Any, moment: float, read: Any, region: Tuple[float, float] = (READ_TOP, 1.0),
) -> List[Dict[str, Any]]:
    """Text lines of one frame: what the OCR reads anywhere in it (a creator's captions sit at the top as
    often as at the bottom), plus outlined glyph rows in the lower part that it cannot read."""
    from .screen_text import text_lines

    lines: List[Dict[str, Any]] = []
    words = read(video_path, moment, region) if read else None
    # A box taller than any caption glyph is a misread; left in, it joins a line and stretches the band.
    for line in text_lines([word for word in words or [] if word.height <= MAX_GLYPH_HEIGHT]):
        tokens = [token for word in line for token in _tokens(word.text)]
        if not tokens:
            continue
        lines.append({
            "top": min(word.top for word in line), "bottom": max(word.bottom for word in line),
            "left": line[0].left, "right": line[-1].right, "tokens": tokens, "words": len(line),
        })
    frame = _frame_at(capture, moment) if capture is not None else None
    if frame is not None:
        for top, bottom in caption_lines_in_frame(frame):
            centre = (top + bottom) / 2
            if any(line["top"] - 0.02 <= centre <= line["bottom"] + 0.02 for line in lines):
                continue
            lines.append({"top": top, "bottom": bottom, "left": 0.25, "right": 0.75, "tokens": [], "words": 0,
                          "shape": True})
    return [line for line in lines if READ_TOP <= (line["top"] + line["bottom"]) / 2 <= SEARCH_BOTTOM]


def _band_from_frames(frames: Sequence[Sequence[Dict[str, Any]]]) -> Optional[Tuple[float, List[List[Dict[str, Any]]]]]:
    """The height most frames write at: (centre, the lines there per frame), or None."""
    centres = [(line["top"] + line["bottom"]) / 2 for frame in frames for line in frame]
    best: Optional[Tuple[Tuple[int, int], float]] = None
    for centre in centres:
        hits = [[line for line in frame if abs((line["top"] + line["bottom"]) / 2 - centre) <= BAND_TOLERANCE]
                for frame in frames]
        key = (sum(1 for frame in hits if frame), sum(len(line["tokens"]) for frame in hits for line in frame))
        if best is None or key > best[0]:
            best = (key, centre)
    if best is None:
        return None
    centre = best[1]
    return centre, [
        [line for line in frame if abs((line["top"] + line["bottom"]) / 2 - centre) <= BAND_TOLERANCE]
        for frame in frames
    ]


def _judge_band(
    hits: Sequence[Sequence[Dict[str, Any]]], spoken: Sequence[Sequence[str]], min_share: float,
    moments: Optional[Sequence[float]],
) -> Optional[SourceCaptions]:
    """What the lines at one height are: captions of the speech, other changing text, a static label, or
    nothing worth acting on (None)."""
    sampled = len(hits)
    hit_frames = [index for index, frame in enumerate(hits) if frame]
    enough = len(hit_frames) >= max(2, math.ceil(sampled * min_share))
    read_frames = [index for index in hit_frames if any(line["words"] >= 2 for line in hits[index])]
    if not enough and len(read_frames) < MIN_READ_FRAMES:
        return None

    def matching(index: int) -> Optional[bool]:
        tokens = [token for line in hits[index] for token in line["tokens"]]
        if not tokens:
            return None
        said = sum(1 for token in tokens if _said(token, spoken[index] if index < len(spoken) else ()))
        return said >= max(1, math.ceil(len(tokens) * MATCH_TOKEN_SHARE))

    verdicts = {index: matching(index) for index in hit_frames}
    readable = [index for index in hit_frames if verdicts[index] is not None]
    matched = [index for index in readable if verdicts[index]]
    # A band is caption-like text, not stray marks: most of its frames show an outlined row, two words or more,
    # or a word that is being said.
    wordy = [
        index for index in hit_frames
        if verdicts[index] or any(line.get("shape") or line["words"] >= 2 for line in hits[index])
    ]
    if len(wordy) * 2 < len(hit_frames):
        return None
    lines = [line for frame in hits for line in frame]
    top = float(np.percentile([line["top"] for line in lines], 5))
    bottom = float(np.percentile([line["bottom"] for line in lines], 95))
    middles = [(line["left"] + line["right"]) / 2 for line in lines]
    subtitle_like = (top + bottom) / 2 >= SUBTITLE_MIN_CENTRE and abs(float(np.median(middles)) - 0.5) <= SUBTITLE_MAX_OFFSET
    match = len(matched) / len(readable) if readable else 0.0
    kind = "text"
    spoken_lines = [index for index in matched if any(line["words"] >= 2 for line in hits[index])]
    mixed = False
    if len(matched) >= 2 and match >= SPEECH_MATCH_SHARE and (enough or len(spoken_lines) >= MIN_READ_FRAMES):
        kind = "speech"
    elif len(readable) >= 2:
        # A label says the same thing in most of the frames. A subtitle line stays up for seconds, so two
        # neighbouring frames often show the same one: what tells them apart is the clip as a whole.
        sets = [set(token for line in hits[index] for token in line["tokens"]) for index in readable]

        def unchanged(a: set, b: set) -> bool:
            return len(a & b) >= STATIC_SAME_SHARE * max(1, max(len(a), len(b)))

        largest = max(sum(1 for other in sets if unchanged(one, other)) for one in sets)
        if largest >= max(2, math.ceil(STATIC_PAIR_SHARE * len(sets))):
            kind = "static"
    if kind != "speech":
        centre = (top + bottom) / 2
        sentences = [index for index in hit_frames if any(line["words"] >= 3 for line in hits[index])]
        # Text in the upper part that is not what is being said is a title, a logo, a document, a game's HUD.
        # Text in a few frames only that is neither the speech nor lines of subtitles is a prompt or a sign.
        dropped = centre < SEARCH_TOP or (not enough and not (subtitle_like and len(sentences) >= MIN_SUBTITLE_FRAMES))
        if not dropped and not enough:
            # In a few frames only, it comes and goes: not a label, whatever two reads of one line look like.
            kind = "text"
        painted = (
            not dropped and kind == "text" and subtitle_like and bottom - top <= PAINT_MAX_HEIGHT
            and (centre >= PAINT_BOTTOM_ZONE or centre <= PAINT_TOP_ZONE)
        )
        if not painted and len(spoken_lines) >= MIN_READ_FRAMES:
            # The rows are shared: a subtitle for a line or two, and a score bar or a title at other times,
            # which read together are "mostly not the speech". The lines that say the speech are captions;
            # the band counts only where they were read. (Subtitles that are painted anyway stay as they
            # are: a game's own lines are painted throughout, whoever speaks them.)
            kind, mixed, hit_frames = "speech", True, list(spoken_lines)
        elif dropped:
            return None
    # How far the lines reach comes from the ones that were read; an outlined row the OCR could not read is
    # only known to be there, and stands in with a nominal width when nothing else is known.
    measured = [line for line in lines if not line.get("shape")] or lines
    return SourceCaptions(
        band=(top, bottom),
        left=float(min(line["left"] for line in measured)),
        right=float(max(line["right"] for line in measured)),
        kind=kind,
        coverage=len(hit_frames) / sampled,
        match=match,
        shown=tuple(float(moments[index]) for index in hit_frames) if moments else (),
        absent=tuple(float(moments[index]) for index in range(sampled) if index not in set(hit_frames))
        if moments else (),
        subtitle_like=subtitle_like,
        mixed=mixed,
    )


STACKED_LINE_GAP = 0.03         # of the frame height between two lines of one caption


def _with_stacked_lines(band: SourceCaptions, frames: Sequence[Sequence[Dict[str, Any]]]) -> SourceCaptions:
    """``band`` grown over the second line its captions sometimes have.

    A subtitle that runs to two lines puts the extra one directly above (or, for captions at the top, below)
    the row it always uses. That row alone is the band most frames agree on; painted alone, the other line
    stays, whole and legible. The band never grows past the few lines a caption strip holds.
    """
    top, bottom = band.band
    left, right = band.left, band.right
    for frame in frames:
        if not any(top - 0.01 <= (line["top"] + line["bottom"]) / 2 <= bottom + 0.01 for line in frame):
            continue
        grew = True
        while grew:
            grew = False
            for line in frame:
                if line.get("words", 0) < 2 or (line["top"] >= top - 0.005 and line["bottom"] <= bottom + 0.005):
                    continue
                # Touching the band, or a line's height away from it at most.
                reach = max(STACKED_LINE_GAP, line["bottom"] - line["top"])
                near = line["bottom"] >= top - reach and line["top"] <= bottom + reach
                if near and max(bottom, line["bottom"]) - min(top, line["top"]) <= PAINT_MAX_HEIGHT:
                    top, bottom = min(top, line["top"]), max(bottom, line["bottom"])
                    left, right = min(left, line["left"]), max(right, line["right"])
                    grew = True
    if (top, bottom) == band.band:
        return band
    return SourceCaptions(**{**band.__dict__, "band": (top, bottom), "left": left, "right": right})


def captions_from_frames(
    frames: Sequence[Sequence[Dict[str, Any]]], spoken: Sequence[Sequence[str]],
    min_share: float = MIN_FRAME_SHARE, moments: Optional[Sequence[float]] = None,
) -> Optional[SourceCaptions]:
    """Judge sampled frames: ``frames[i]`` are the text lines of frame i, ``spoken[i]`` the words said around it.

    Up to MAX_BANDS heights are judged, the most written-at first; the result is the best of them (captions
    of the speech before other text, then the one seen most) with the others in ``more``.
    """
    remaining = [list(frame) for frame in frames]
    found: List[SourceCaptions] = []
    for _ in range(MAX_BANDS * 2):
        if len(found) >= MAX_BANDS or not any(remaining):
            break
        best = _band_from_frames(remaining)
        if best is None:
            break
        _centre, hits = best
        taken = {id(line) for frame in hits for line in frame}
        remaining = [[line for line in frame if id(line) not in taken] for frame in remaining]
        judged = _judge_band(hits, spoken, min_share, moments)
        if judged is not None:
            found.append(_with_stacked_lines(judged, frames))
    if not found:
        return None
    found.sort(key=lambda b: (b.kind != "speech", -b.coverage))
    first, rest = found[0], tuple(found[1:])
    return SourceCaptions(**{**first.__dict__, "more": rest})


def outlined_text_moments(video_path: Path, duration: float) -> List[float]:
    """Moments (clip seconds) at which the lower part of the picture shows an outlined row of glyphs.

    One cheap pass over the whole clip (SCAN_FPS frames a second, small): a subtitle that is up for a second
    in a minute falls between sixteen sampled frames, but not between these. Each run of such frames gives
    its middle, and a second moment when it lasts.
    """
    out_w = ANALYSIS_WIDTH
    probe = cv2.VideoCapture(str(video_path))
    try:
        width = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        probe.release()
    if width <= 0 or height <= 0 or duration <= 0:
        return []
    out_h = max(2, int(round(height * out_w / width)) // 2 * 2)
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video_path), "-an", "-sn",
         "-vf", f"fps={SCAN_FPS},scale={out_w}:{out_h}", "-pix_fmt", "bgr24", "-f", "rawvideo", "-"],
        capture_output=True, timeout=max(120, int(duration * 4)),
    )
    size = out_w * out_h * 3
    count = len(result.stdout) // size if result.returncode == 0 else 0
    if count < 1:
        return []
    frames = np.frombuffer(result.stdout[:count * size], dtype=np.uint8).reshape(count, out_h, out_w, 3)
    hits = [bool(caption_lines_in_frame(frame)) for frame in frames]
    moments: List[float] = []
    index = 0
    while index < count:
        if not hits[index]:
            index += 1
            continue
        stop = index
        while stop + 1 < count and hits[stop + 1]:
            stop += 1
        first, last = (index + 0.5) / SCAN_FPS, (stop + 0.5) / SCAN_FPS
        moments.append((first + last) / 2)
        if last - first >= SCAN_SECOND_READ_SECONDS:
            moments.append(first + (last - first) * 0.15)
        index = stop + 1
    return [min(moment, max(0.0, duration - 0.05)) for moment in moments]


# A subtitle that is up for under a second (a single short line in a minute-long clip) is never among the
# sampled frames and, plain white on a busy picture, not an outlined row either. Text arriving in the bottom
# strip is a jump in hard edges there: the strip is watched at STRIP_FPS, and its strongest jumps are read,
# the strip alone and enlarged.
STRIP_FPS = 4
STRIP_ROWS = (0.80, 0.97)        # of the frame height
STRIP_COLUMNS = (0.15, 0.85)     # of the frame width
STRIP_JUMP = 3.0                 # times the strip's usual bright-edge energy
STRIP_MIN_ENERGY = 0.05
STRIP_BRIGHT_LEVEL = 200         # subtitles are light: only hard edges next to pixels this bright count
STRIP_MAX_READS = 9
STRIP_READ_REGION = (0.76, 1.0)


def strip_text_moments(video_path: Path, duration: float) -> List[float]:
    """Moments (clip seconds) at which text seems to come up in the bottom strip, the strongest appearance
    first; up to three per appearance."""
    out_w = ANALYSIS_WIDTH
    probe = cv2.VideoCapture(str(video_path))
    try:
        width = int(probe.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(probe.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
    finally:
        probe.release()
    if width <= 0 or height <= 0 or duration <= 0:
        return []
    out_h = max(2, int(round(height * out_w / width)) // 2 * 2)
    top, bottom = int(STRIP_ROWS[0] * out_h), int(STRIP_ROWS[1] * out_h)
    left, right = int(STRIP_COLUMNS[0] * out_w), int(STRIP_COLUMNS[1] * out_w)
    strip_w, strip_h = (right - left) // 2 * 2, (bottom - top) // 2 * 2
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video_path), "-an", "-sn",
         "-vf", f"fps={STRIP_FPS},scale={out_w}:{out_h},crop={strip_w}:{strip_h}:{left}:{top},format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=max(120, int(duration * 4)),
    )
    size = strip_w * strip_h
    count = len(result.stdout) // size if result.returncode == 0 and size else 0
    if count < 4:
        return []
    frames = np.frombuffer(result.stdout[:count * size], dtype=np.uint8).reshape(count, strip_h, strip_w)
    return strip_jumps([bright_edge_energy(frame) for frame in frames], duration)


def bright_edge_energy(gray: np.ndarray) -> float:
    """How much of a grey strip is hard vertical edges next to bright pixels, in its busiest eighth.

    Edges alone do not tell a line of text from a jacket or a brick wall; light glyphs with dark outlines
    do. A patterned shirt measures a few thousandths, a subtitle over it a tenth and more.
    """
    if gray.size == 0:
        return 0.0
    edges = np.abs(cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)) > PRESENCE_EDGE_LEVEL
    bright = cv2.dilate((gray >= STRIP_BRIGHT_LEVEL).astype(np.uint8), np.ones((3, 3), np.uint8)) > 0
    cells = np.array_split(edges & bright, PRESENCE_CELLS, axis=1)
    return float(max(cell.mean() for cell in cells if cell.size))


def strip_jumps(energies: Sequence[float], duration: float) -> List[float]:
    """The moments of ``strip_text_moments`` from the strip's edge energy, one value per 1/STRIP_FPS seconds."""
    if len(energies) < 4:
        return []
    usual = float(np.median(energies))
    level = max(usual * STRIP_JUMP, usual + STRIP_MIN_ENERGY)
    runs: List[Tuple[float, int, int]] = []          # (peak energy, first frame, last frame)
    index = 0
    while index < len(energies):
        if energies[index] < level:
            index += 1
            continue
        stop = index
        while stop + 1 < len(energies) and energies[stop + 1] >= level:
            stop += 1
        runs.append((float(max(energies[index:stop + 1])), index, stop))
        index = stop + 1
    moments: List[float] = []
    for _peak, first, last in sorted(runs, reverse=True):
        start, end = (first + 0.5) / STRIP_FPS, (last + 0.5) / STRIP_FPS
        # Three looks at an appearance when it lasts: a line that builds up word by word, or one the OCR
        # garbles in one frame, still reads the same way twice.
        shares = (0.5,) if end == start else ((0.25, 0.75) if last - first == 1 else (0.2, 0.5, 0.8))
        moments.extend(start + (end - start) * share for share in shares)
    return [min(moment, max(0.0, duration - 0.05)) for moment in moments]


def analyze_source_captions(
    video_path: Path, words: Optional[Sequence[Dict[str, Any]]] = None, read: Any = None,
) -> Optional[SourceCaptions]:
    """Find and read the text burned into ``video_path`` (a cut clip), or None when there is none.

    ``words`` are the clip's spoken words on its own timeline (text, start, end): frames are sampled while
    they are said and what is written is compared with them. A pass over the whole clip adds the moments an
    outlined row of glyphs is on screen (``outlined_text_moments``), so captions that come and go are read
    where they are. ``read`` defaults to the local OCR; without a working OCR only outlined captions are
    found and their kind is "text".
    """
    from .ffmpeg import ffprobe_duration
    from .screen_text import ocr_available, read_frame_words

    if read is None:
        read = read_frame_words if ocr_available() else None
    duration = ffprobe_duration(video_path)
    sampled = sample_moments(duration, words)
    if not sampled:
        return None
    try:
        outlined = outlined_text_moments(video_path, duration)
    except Exception as exc:  # noqa: BLE001 - the sampled frames still stand
        logger.debug("Outlined-text scan unavailable (%s)", exc)
        outlined = []
    extra: List[float] = []
    for moment in outlined:
        if len(extra) >= SCAN_MAX_EXTRA_READS:
            break
        if all(abs(moment - other) >= MIN_SAMPLE_GAP_SECONDS for other in [*sampled, *extra]):
            extra.append(moment)
    strip: List[float] = []
    if read is not None:
        try:
            arriving = strip_text_moments(video_path, duration)
        except Exception as exc:  # noqa: BLE001 - the sampled frames still stand
            logger.debug("Bottom-strip scan unavailable (%s)", exc)
            arriving = []
        for moment in arriving:
            if len(strip) >= STRIP_MAX_READS:
                break
            if all(abs(moment - other) >= 0.1 for other in strip) and moment not in sampled and moment not in extra:
                strip.append(moment)
    frames: Dict[float, List[Dict[str, Any]]] = {}
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            capture = None
        first = sorted({sampled[int(len(sampled) * (step + 0.5) / FIRST_LOOK_SAMPLES)]
                        for step in range(min(FIRST_LOOK_SAMPLES, len(sampled)))})
        for moment in first:
            frames[moment] = _frame_lines(video_path, capture, moment, read)
        # Text that comes up in the bottom strip is read there, enlarged; nothing else in those frames is.
        for moment in strip:
            lines = _frame_lines(video_path, None, moment, read, STRIP_READ_REGION)
            if lines:
                frames[moment] = lines
        if not any(frames.values()) and not extra:
            return None
        for moment in [*sampled, *extra]:
            if moment not in frames:
                frames[moment] = _frame_lines(video_path, capture, moment, read)
    finally:
        if capture is not None:
            capture.release()
            capture = None
    moments = sorted(frames)
    spoken = [
        [token for word in words or ()
         if float(word["start"]) <= moment + SPEECH_REACH_SECONDS and float(word["end"]) >= moment - SPEECH_REACH_SECONDS
         for token in _tokens(str(word.get("text", "")))]
        for moment in moments
    ]
    found = captions_from_frames([frames[moment] for moment in moments], spoken, moments=moments)
    if found and read is not None:
        # Small text over a busy picture is missed in a read of the whole frame. Frames without a line at
        # the main band's height get a second look at the band alone, enlarged.
        region = (max(0.0, found.band[0] - SECOND_LOOK_MARGIN), min(1.0, found.band[1] + SECOND_LOOK_MARGIN))
        centre = (found.band[0] + found.band[1]) / 2
        again = False
        for moment in moments:
            if any(abs((line["top"] + line["bottom"]) / 2 - centre) <= BAND_TOLERANCE for line in frames[moment]):
                continue
            closer = _frame_lines(video_path, None, moment, read, region)
            if closer:
                frames[moment] = frames[moment] + closer
                again = True
        if again:
            found = captions_from_frames(
                [frames[moment] for moment in moments], spoken, moments=moments) or found
    for band in (found.bands if found else ()):
        logger.info(
            "Source text at %.2f-%.2f of the frame height: %s, in %.0f%% of speech moments, %.0f%% of it spoken",
            band.band[0], band.band[1], band.kind, band.coverage * 100, band.match * 100,
        )
    return found


# --- what the render does --------------------------------------------------

# Layout kinds that show the source frame whole. Every other kind crops its width somewhere: a full-height
# crop, the panels of a split screen, the game panel under a streamer's camera, the source panel under a reactor.
_WHOLE_FRAME_KINDS = {"fit"}


def cropped_intervals(
    output_format: str, width: int, height: int, duration: float, layout: Optional[Dict[str, Any]] = None,
) -> Optional[List[Tuple[float, float]]]:
    """When the layout crops the source's width (clip seconds); [] when never, None when it cannot be told.

    ``layout`` is the vertical plan's report (``kind`` and, for clips mixing crops and fitted shots, its
    ``interview_segments``). A crop cuts a subtitle line at both ends; a fitted or untouched frame keeps it.
    """
    if output_format in ("original", "vertical_fit") or width <= 0 or height <= 0:
        return []
    if output_format in ("vertical_pan", "vertical_split"):
        return [(0.0, duration)]
    if height * 9 / 16 >= width - 2:
        return []  # a portrait source is not cropped sideways
    kind = (layout or {}).get("kind")
    if kind in _WHOLE_FRAME_KINDS:
        return []
    if kind is None:
        return None
    segments = (layout or {}).get("interview_segments")
    if segments:
        return [(float(s["start"]), float(s["end"])) for s in segments if s.get("kind") != "fit"]
    return [(0.0, duration)]


CROP_EDGE_TOLERANCE = 0.01      # of the frame width a line may reach past a crop's edge and still read whole


def cut_intervals(
    captions: SourceCaptions, cropped: Optional[Sequence[Tuple[float, float]]],
    windows: Optional[Sequence[Tuple[float, float, float, float]]],
) -> Optional[List[Tuple[float, float]]]:
    """The cropped stretches whose crop cuts the source's lines (clip seconds).

    ``windows`` are the layout's full-height crops as (start, end, left, right), the columns in 0-1 fractions
    of the source width, when the layout knows them. A crop that holds the widest line read keeps the captions
    whole: a portrait picture set in a 16:9 frame, captions narrower than the crop. Without windows every
    cropped stretch cuts, which is what a crop does to a line written across a 16:9 frame.
    """
    if cropped is None or not windows:
        return list(cropped) if cropped is not None else None
    # The lines that matter: the main band and whatever else would be painted. A label elsewhere is picture.
    lines = [captions, *(band for band in captions.paintable if band is not captions)]
    left, right = min(band.left for band in lines), max(band.right for band in lines)
    whole = [
        (float(start), float(end)) for start, end, crop_left, crop_right in windows
        if left >= crop_left - CROP_EDGE_TOLERANCE and right <= crop_right + CROP_EDGE_TOLERANCE
    ]
    cut: List[Tuple[float, float]] = []
    for start, end in cropped:
        pieces = [(float(start), float(end))]
        for a, b in whole:
            pieces = [
                piece for lo, hi in pieces
                for piece in ((lo, min(hi, a)), (max(lo, b), hi)) if piece[1] - piece[0] > 0.02
            ]
        cut.extend(pieces)
    return sorted(cut)


def plan_caption_action(
    captions: Optional[SourceCaptions], cropped: Optional[Sequence[Tuple[float, float]]], duration: float,
    cut: Optional[Sequence[Tuple[float, float]]] = None,
) -> str:
    """"none", "suppress", "mask" or "avoid" (see the module text) for a clip with ``captions``.

    ``cropped`` are the stretches the layout crops; ``cut`` (``cut_intervals``) those whose crop cuts the
    lines, when that is known. Captions of the speech that the crops keep whole carry the clip and ours are
    left off. Anything else in a cropped clip is painted out when it is captions: lines that are whole but
    only there now and then would sit under ours as a second layer.
    """
    if captions is None:
        return "none"
    if cropped is None:
        return "avoid"

    def most(spans: Sequence[Tuple[float, float]]) -> bool:
        return sum(end - start for start, end in spans) >= MASK_MIN_CROPPED_SHARE * max(duration, 1e-6)

    if (cut is not None and not most(cut) and not captions.mixed
            and captions.kind == "speech" and captions.coverage >= SUPPRESS_MIN_COVERAGE):
        return "suppress"
    if most(cropped):
        # Captions and subtitles the crop slices are painted out; a label, a document or a game's prompts
        # are part of the picture and stay.
        return "mask" if captions.paintable else "avoid"
    if captions.kind == "speech" and captions.coverage >= SUPPRESS_MIN_COVERAGE and not captions.mixed:
        return "suppress"
    return "avoid"


def mask_box(captions: SourceCaptions, width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    """Pixels (x, y, w, h) of the box holding the source's caption lines, with room around them."""
    pad = MASK_PAD_FRAC * height
    top = int(max(1, captions.band[0] * height - pad))
    bottom = int(min(height - 1, captions.band[1] * height + pad))
    # The whole width: the longest line may not be among the frames read, and the end of a line left
    # outside the box is a legible, cut-off remnant. Only cropped shots are painted, and they show a part.
    left, right = 1, width - 1
    if bottom - top < 4 or right - left < 4:
        return None
    return left, top, right - left, bottom - top


def _intersect(
    first: Sequence[Tuple[float, float]], second: Sequence[Tuple[float, float]]
) -> List[Tuple[float, float]]:
    return sorted(
        (max(a, c), min(b, d)) for a, b in first for c, d in second if min(b, d) - max(a, c) > 0.02
    )


PRESENCE_MIN_HALF_WIDTH = 0.3   # of the frame width each side of the centre that is watched for text


def text_box(captions: SourceCaptions, width: int, height: int) -> Optional[Tuple[int, int, int, int]]:
    """Pixels (x, y, w, h) of where the source's lines were read, with room: the box watched for text.

    Narrower than the painted box: the picture left and right of the lines (a sleeve, a window frame) has
    edges of its own, and watching it too hides a short line among them.
    """
    box = mask_box(captions, width, height)
    if box is None:
        return None
    _x, top, _w, tall = box
    half = max(0.5 - captions.left, captions.right - 0.5, PRESENCE_MIN_HALF_WIDTH) + 0.04
    left = int(max(1, (0.5 - half) * width))
    right = int(min(width - 1, (0.5 + half) * width))
    return (left, top, right - left, tall) if right - left >= 4 else None


def text_energy(gray: np.ndarray) -> float:
    """How much of a grey box is hard vertical edges: letters are, skin and cloth are not.

    The box is read in PRESENCE_CELLS columns and the busiest one counts: one short word in a wide box is
    as present as a full line.
    """
    if gray.size == 0:
        return 0.0
    edges = np.abs(cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)) > PRESENCE_EDGE_LEVEL
    cells = np.array_split(edges, PRESENCE_CELLS, axis=1)
    return float(max(cell.mean() for cell in cells if cell.size))


def presence_intervals(
    times: Sequence[float], energies: Sequence[float], shown: Sequence[float], absent: Sequence[float],
    duration: float,
) -> Optional[List[Tuple[float, float]]]:
    """When the caption box holds text, from its edge energy over time; None when that cannot be told.

    The frames the OCR read are the labels: ``shown`` moments had a line in the band, ``absent`` ones had
    not. When the two sets separate, every sample above a level just over the empty frames is text: a line
    left in costs more than a moment painted for nothing. Intervals get a margin on both sides and close
    over short gaps, so a line never flickers back in.
    """
    if not len(times) or not shown or not absent:
        return None
    grid = np.asarray(times, dtype=np.float64)
    values = np.asarray(energies, dtype=np.float64)

    def at(moments: Sequence[float]) -> np.ndarray:
        return values[[int(np.argmin(np.abs(grid - moment))) for moment in moments]]

    with_text, without = at(shown), at(absent)
    low, high = float(np.percentile(without, 90)), float(np.percentile(with_text, 25))
    if high < max(low * PRESENCE_SEPARATION, low + 0.005):
        return None
    level = low + PRESENCE_LEVEL_SHARE * (high - low)
    # The moments a line was read count whatever their energy says: a short line over a busy picture can
    # measure below the level, and it is the one thing known to be text.
    present = sorted({float(moment) for moment, value in zip(grid, values) if value >= level} | {float(m) for m in shown})
    intervals: List[List[float]] = []
    for moment in present:
        start, end = max(0.0, moment - PRESENCE_MARGIN_SECONDS), min(duration, moment + PRESENCE_MARGIN_SECONDS)
        if intervals and start - intervals[-1][1] <= PRESENCE_JOIN_SECONDS + 1e-6:
            intervals[-1][1] = max(intervals[-1][1], end)
        else:
            intervals.append([start, end])
    return [(a, b) for a, b in intervals]


def caption_presence(video_path: Path, captions: SourceCaptions) -> Optional[List[Tuple[float, float]]]:
    """When ``video_path`` shows text in the caption box (clip seconds), or None for "assume always"."""
    from .ffmpeg import ffprobe_duration, ffprobe_video_size

    width, height = ffprobe_video_size(video_path)
    box = text_box(captions, width, height)
    if box is None or not captions.shown or not captions.absent:
        return None
    x, y, w, h = box
    out_w = max(2, min(w, 640) // 2 * 2)
    out_h = max(2, int(round(h * out_w / w)) // 2 * 2)
    result = subprocess.run(
        ["ffmpeg", "-v", "error", "-nostdin", "-i", str(video_path), "-an", "-sn",
         "-vf", f"crop={w}:{h}:{x}:{y},fps={PRESENCE_FPS},scale={out_w}:{out_h},format=gray",
         "-f", "rawvideo", "-"],
        capture_output=True, timeout=300,
    )
    size = out_w * out_h
    count = len(result.stdout) // size if result.returncode == 0 else 0
    if count < 2:
        return None
    frames = np.frombuffer(result.stdout[:count * size], dtype=np.uint8).reshape(count, out_h, out_w)
    times = [(index + 0.5) / PRESENCE_FPS for index in range(count)]
    return presence_intervals(
        times, [text_energy(frame) for frame in frames], captions.shown, captions.absent,
        ffprobe_duration(video_path),
    )


def _enable(intervals: Sequence[Tuple[float, float]], duration: float) -> Optional[str]:
    """ffmpeg ``enable`` option for ``intervals`` ("" for the whole clip), or None when there is nothing to do."""
    spans = [(start, end) for start, end in intervals if end > start]
    if not spans:
        return None
    if len(spans) == 1 and spans[0][0] <= 0.01 and spans[0][1] >= duration - 0.01:
        return ""
    return ":enable='" + "+".join(f"between(t,{start:.3f},{end:.3f})" for start, end in spans) + "'"


def mask_filter(
    captions: SourceCaptions, width: int, height: int, intervals: Sequence[Tuple[float, float]], duration: float,
) -> Optional[str]:
    """ffmpeg filter that paints one caption box out of ``intervals``.

    The box is filled from its edges (``delogo``) and then softened, so what is left is a smooth patch of
    the surrounding picture instead of streaks with glyph shapes in them.
    """
    found = mask_box(captions, width, height)
    enable = _enable(intervals, duration)
    if found is None or enable is None:
        return None
    left, top, box_w, box_h = found
    return (
        f"delogo=x={left}:y={top}:w={box_w}:h={box_h}{enable},"
        f"split[whole][patch];[patch]crop={box_w}:{box_h}:{left}:{top},gblur=sigma={MASK_SOFTEN_SIGMA}[soft];"
        f"[whole][soft]overlay={left}:{top}{enable}"
    )


def mask_caption_band(
    video_path: Path, captions: SourceCaptions, intervals: Sequence[Tuple[float, float]],
) -> bool:
    """Paint the source's captions out of ``video_path`` in place (audio untouched). False when it could not.

    ``intervals`` are the cropped stretches of the clip. Every band that is captions or subtitles
    (``SourceCaptions.paintable``) is painted. A band that is there most of the time is painted throughout:
    a line the presence test missed would show cut off. A band that comes and goes (one of two voices is
    subtitled, a creator's captions jump between the top and the bottom) is painted only while it holds
    text, when that can be told (``caption_presence``).
    """
    from .common import INTERMEDIATE_CRF
    from .ffmpeg import ffprobe_duration, ffprobe_video_size, run_ffmpeg_command

    width, height = ffprobe_video_size(video_path)
    duration = ffprobe_duration(video_path)
    stages: List[str] = []
    for band in captions.paintable:
        spans = list(intervals)
        if band.coverage < SUPPRESS_MIN_COVERAGE or not band.in_caption_zone or band.mixed:
            try:
                present = None if band.mixed else caption_presence(video_path, band)
            except Exception as exc:  # noqa: BLE001 - paint the whole stretch instead
                logger.debug("Caption presence unavailable (%s)", exc)
                present = None
            if present is None and (band.mixed or not band.in_caption_zone):
                # In the middle of the picture a patch that is always there costs more than a word left in:
                # when the text cannot be timed, only the moments it was read are painted.
                present = []
                for moment in sorted(band.shown):
                    start, end = max(0.0, moment - PRESENCE_READ_SECONDS), moment + PRESENCE_READ_SECONDS
                    if present and start <= present[-1][1]:
                        present[-1] = (present[-1][0], end)
                    else:
                        present.append((start, end))
            if present is not None:
                spans = _intersect(spans, present)
        stage = mask_filter(band, width, height, spans, duration)
        if stage:
            stages.append(stage)
    if not stages:
        return bool(captions.paintable)
    graph = "[0:v]" + stages[0]
    for index, stage in enumerate(stages[1:], start=1):
        graph += f"[m{index}];[m{index}]" + stage
    graph += "[painted]"
    masked = video_path.with_name(video_path.stem + ".masked" + video_path.suffix)
    result = run_ffmpeg_command([
        "ffmpeg", "-y", "-i", str(video_path), "-filter_complex", graph,
        "-map", "[painted]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast", "-crf", str(INTERMEDIATE_CRF),
        "-pix_fmt", "yuv420p", "-c:a", "copy", str(masked),
    ])
    if result.returncode != 0 or not masked.exists():
        masked.unlink(missing_ok=True)
        return False
    masked.replace(video_path)
    return True


def band_in_output(
    captions: SourceCaptions, output_format: str, width: int, height: int, layout_kind: Optional[str],
) -> Optional[Band]:
    """Where the source's band lands in the output frame when that follows from the layout alone, else None.

    An untouched frame keeps it; a full-height crop (and a portrait source, with its slow punch-in) keeps its
    rows; a fitted frame is the source scaled to the output width and centred. Split and stacked layouts move
    it in ways only a look at the composed picture can tell (``detect_rendered_caption_band``).
    """
    if output_format == "original":
        return captions.band
    if width <= 0 or height <= 0:
        return None
    if output_format == "vertical_fit" or layout_kind in _WHOLE_FRAME_KINDS:
        scale = min(1.0, (1080 * height / width) / 1920)
        return 0.5 + (captions.band[0] - 0.5) * scale, 0.5 + (captions.band[1] - 0.5) * scale
    if height * 9 / 16 >= width - 2 or layout_kind in {"crop", "speaker", "compositor"}:
        return band_through_full_height_crop(captions.band)
    return None
