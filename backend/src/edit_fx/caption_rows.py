"""Per-shot caption rows for interview layouts (``media.interview_layout``).

An interview clip is composed shot by shot: ``pair`` (two people stacked, seam at y = 0.5), ``single`` (a tight
head-and-shoulders crop) and ``fit`` (graphics or b-roll letterboxed on a blurred fill). One caption height cannot suit
all three, so the intensity-look captions get a row per shot:

- ``pair``: 0.50, on the seam between the panels (both faces stay clear);
- ``single``: 0.65, mid-chest, under the chin (podcast clippers measure 0.65-0.70);
- ``fit``: 0.76, on the blurred fill below the picture band, or lower when the picture is taller (4:3 sources).

A shot shorter than ``FLASH_SHOT_SECONDS`` (a flash, a quick reaction cut) never moves the captions: it takes the
previous shot's row (a clip opening on one takes the next shot's). Rows are resolved here, in Python only. Each
change of row is a camera cut, and a cut ends the caption group on screen (``row_cuts`` → ``phrase_groups(cuts=)``,
mirrored by fx-core ``layoutLookCaptions``): words starting before ``cut - CUT_LEAD`` stay on the old row and leave
at the cut, the rest form the next shot's groups, shown from the cut on its row. Captions therefore switch exactly at
cuts, never inside a group, and never sit on a row meant for another shot.

Rows avoid burned-in source captions (``band``, like the single caption y did). The hook title has its own
place (the top, or beneath a head it would cover; fx-core ``hook.ts``) and never moves for the rows.

Pure: no I/O. Times are in the clock of the segments given (generation: E == stage-1 clip time; the studio stores
them in S and maps them with ``mapped_caption_rows``).
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from .caption_groups import phrase_groups
from .contracts import CaptionRow, caption_row_y

ROW_Y: dict[str, float] = {"pair": 0.50, "single": 0.65, "fit": 0.76}
SHOT_KINDS = frozenset(ROW_Y)
# A shot shorter than this keeps the captions where they were.
FLASH_SHOT_SECONDS = 1.0
# Half the height of a two-line look caption block, in em of the caption size (fx-core lines are 1.25 em apart).
TWO_LINE_HALF_EM = 1.25
# Space kept between a caption block and the picture band, as a share of the frame height
# (== media.captions.caption_center_clear_of_band's gap at 1920).
ROW_GAP_FRAC = 0.015
# The lowest a fit row goes when the picture is tall (keeps the bottom platform UI clear).
FIT_ROW_MAX = 0.84
# Output frame the interview layout composes (media.interview_layout OUT_W x OUT_H).
LAYOUT_W, LAYOUT_H = 1080, 1920
MAX_ROWS = 400


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def clean_shots(segments: Iterable[Any]) -> list[tuple[float, float, str]]:
    """``[(start, end, kind)]`` sorted by start from interview segments (dicts with start/end/kind); anything else
    (unknown kinds, empty or unreadable spans) is dropped."""
    out: list[tuple[float, float, str]] = []
    for segment in segments or ():
        if not isinstance(segment, Mapping):
            continue
        start, end, kind = _num(segment.get("start")), _num(segment.get("end")), segment.get("kind")
        if start is None or end is None or kind not in SHOT_KINDS or end <= start:
            continue
        out.append((max(0.0, start), end, str(kind)))
    out.sort(key=lambda s: s[0])
    return out


def shot_runs(segments: Iterable[Any], *, flash: float = FLASH_SHOT_SECONDS) -> list[tuple[float, float, str]]:
    """Shots with the flashes absorbed: a shot shorter than ``flash`` takes the kind of the shot before it (leading
    flashes the first longer shot's; a clip made only of flashes keeps its first shot's kind), then touching shots of
    one kind join."""
    shots = clean_shots(segments)
    if not shots:
        return []
    long_kinds = [kind for start, end, kind in shots if end - start >= flash]
    current = long_kinds[0] if long_kinds else shots[0][2]
    runs: list[list] = []
    for start, end, kind in shots:
        if end - start >= flash:
            current = kind
        if runs and runs[-1][2] == current:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end, current])
    return [(a, b, k) for a, b, k in runs]


def fit_picture_band(source_size: Sequence[float] | None) -> tuple[float, float] | None:
    """Where a ``fit`` shot's picture lands in the 9:16 frame (top, bottom as height fractions): the source scaled to
    the frame width (``reframing.FIT_CHAIN``), centred. None without a usable size."""
    if not source_size or len(source_size) != 2:
        return None
    sw, sh = _num(source_size[0]), _num(source_size[1])
    if not sw or not sh or sw <= 0 or sh <= 0:
        return None
    height = min(float(LAYOUT_H), LAYOUT_W * sh / sw)
    half = height / LAYOUT_H / 2
    return 0.5 - half, 0.5 + half


def _clear_of_band(y: float, text_px: float, band: Sequence[float], height: int) -> float:
    from ..media.captions import caption_center_clear_of_band

    at = int(round(y * height))
    centre = caption_center_clear_of_band(at, int(text_px * 1.5), (float(band[0]), float(band[1])), height)
    return y if centre == at else centre / height


def _row_y(kind: str, *, half: float, picture: tuple[float, float] | None) -> float:
    """The row of one shot kind before the burned-in caption band. ``half`` is half a two-line block (height
    fraction)."""
    y = ROW_Y[kind]
    if kind == "fit" and picture is not None and picture[1] < 1.0:
        lowest_clear = picture[1] + ROW_GAP_FRAC + half
        y = min(FIT_ROW_MAX, max(y, lowest_clear))
    return max(0.0, min(1.0, y))


def interview_caption_rows(
    segments: Iterable[Any],
    *,
    text_px: float,
    width: int,
    height: int,
    source_size: Sequence[float] | None = None,
    band: Sequence[float] | None = None,
    duration: float | None = None,
) -> tuple[CaptionRow, ...]:
    """Caption rows for an interview clip's shots (``interview_layout`` segments: start, end, kind).

    ``text_px`` is the caption size at ``width``; ``source_size`` the source picture (for the fit band);
    ``band`` the burned-in source captions (top, bottom) to stay clear of. The first row starts at 0 and the last one
    ends at ``duration`` when given. Rows of one height join. () when there is no usable shot.
    """
    runs = shot_runs(segments)
    if not runs or height <= 0 or width <= 0:
        return ()
    half = TWO_LINE_HALF_EM * float(text_px) / float(height)
    picture = fit_picture_band(source_size)
    rows: list[list] = []
    for index, (start, end, kind) in enumerate(runs):
        y = _row_y(kind, half=half, picture=picture)
        if band is not None:
            y = _clear_of_band(y, text_px, band, height)
        y = round(max(0.0, min(1.0, y)), 4)
        if index == 0:
            start = 0.0
        if rows and rows[-1][2] == y:
            rows[-1][1] = end
        else:
            rows.append([start, end, y])
    if duration is not None and duration > 0:
        rows = [r for r in rows if r[0] < duration] or rows[:1]
        rows[-1][1] = float(duration)
    return tuple(CaptionRow(start=round(a, 3), end=round(max(a, b), 3), y=y) for a, b, y in rows[:MAX_ROWS])


def mapped_caption_rows(segments: Sequence[tuple[str, float, float]], rows: Sequence[CaptionRow]) -> list[CaptionRow]:
    """S rows on the E clock (== fx-core ``mappedCaptionRows``): ``segments`` are ``(id, s_start, s_end)`` in play
    order, continuous-merged. Each row is cut by each segment (a reordered or repeated segment brings its rows along)
    and placed at the segment's E cursor; touching pieces of one height join."""
    out: list[CaptionRow] = []
    cursor = 0.0
    ordered = sorted(rows, key=lambda r: r.start)
    for _id, seg_start, seg_end in segments:
        for row in ordered:
            start, end = max(seg_start, row.start), min(seg_end, row.end)
            if end <= start:
                continue
            e0, e1 = cursor + start - seg_start, cursor + end - seg_start
            if out and out[-1].y == row.y and abs(out[-1].end - e0) < 1e-6:
                out[-1] = out[-1].model_copy(update={"end": e1})
            else:
                out.append(CaptionRow(start=e0, end=e1, y=row.y))
        cursor += seg_end - seg_start
    return out[:MAX_ROWS]


def row_cuts(rows: Sequence[CaptionRow]) -> list[float]:
    """== fx-core ``rowCuts``: the camera cuts that end caption groups, where the row height changes."""
    ordered = sorted(rows, key=lambda r: r.start)
    return [b.start for a, b in zip(ordered, ordered[1:]) if b.y != a.y]


def group_rows(words: Sequence[Mapping[str, Any]], max_words: int, rows: Sequence[CaptionRow],
               y: float) -> list[dict]:
    """The look caption groups on rows (fixture helper == fx-core ``layoutLookCaptions``): phrase groups ended by
    the rows' cuts, each on the row where it starts showing (the cut it opens at, else its first word's onset)."""
    return [{"first": g.first, "last": g.last, "start": g.start, "end": g.end, "cut": g.cut, "until": g.until,
             "y": caption_row_y(rows, y, g.cut if g.cut is not None else g.start)}
            for g in phrase_groups(words, max_words, row_cuts(rows))]
