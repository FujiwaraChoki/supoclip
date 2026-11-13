"""Moving cues, beats and words between the studio's source clock S and edit clock E (§1, §5.5).

``segments`` are ``(id, s_start, s_end)`` in play order, already continuous-merged (adjacent segments that touch in
S joined, ``editor_document.continuous_segments``). A segment occupies ``[cursor, cursor + s_end - s_start)`` of E,
where ``cursor`` is the summed length of the segments before it. Generation passes ``[("clip", 0.0, duration)]``,
which is the identity.

Forward mapping (``mapped_cues``, mirrored by fx-core ``mapping.ts``) follows ``editor_document.mapped_broll``:

- a span cue is intersected with each segment; a piece shorter than ``min_piece`` is dropped; the piece starts at
  ``cursor + (max(start, seg.start) - seg.start)``;
- an instant cue (``sfx``, ``freeze``, ``transition``) is emitted once per segment with
  ``seg.start <= start < seg.end``, or ``seg.end <= start <= seg.end + 1e-6`` when the next play-order segment does
  not start at ``seg.end`` (an instant at the very end of a segment belongs to the segment it ends, AR-m4). An
  instant that no segment plays this way (a cut removed it) is still emitted by every segment with
  ``seg.start - 0.05 <= start < seg.start``, so a sound just before a cut is not lost. The lead never applies to an
  instant some segment already plays: two segments separated by a gap under 50 ms must not double an SFX or a
  freeze. Emissions are clamped into the segment;
- every emission of the same cue after the first gets id ``f"{id}@{k}"`` (k = 1, 2, ...), truncating the base id so
  the result fits the 32-character id limit;
- output sorted by ``(start, id)``.
"""

from __future__ import annotations

from typing import Sequence, TypeVar

from .contracts import INSTANT_KINDS, Beat, FxCue

INSTANT_LEAD = 0.05       # an instant this close before a segment start still belongs to it
JUNCTION_EPS = 1e-6       # segments closer than this in S are contiguous
CUE_ID_MAX = 32
BEAT_ID_MAX = 24

Segment = tuple[str, float, float]
_Item = TypeVar("_Item", FxCue, Beat)


def suffixed_id(base: str, suffix: str, limit: int) -> str:
    """``base + suffix``, truncating ``base`` so the result is at most ``limit`` characters."""
    if len(base) + len(suffix) <= limit:
        return base + suffix
    return base[: max(0, limit - len(suffix))] + suffix


def _instant_owned(start: float, index: int, segments: Sequence[Segment]) -> bool:
    """The instant plays inside segment ``index``, or is the instant that ends it (AR-m4)."""
    _, seg_start, seg_end = segments[index]
    if seg_start <= start < seg_end:
        return True
    following = segments[index + 1] if index + 1 < len(segments) else None
    contiguous = following is not None and abs(following[1] - seg_end) < JUNCTION_EPS
    return not contiguous and seg_end <= start <= seg_end + JUNCTION_EPS


def _instant_in_segment(start: float, index: int, segments: Sequence[Segment]) -> bool:
    if _instant_owned(start, index, segments):
        return True
    # The lead only rescues an instant that a cut removed (it plays in no segment): one inside, or ending, another
    # segment already sounds there, so a small gap between two segments never doubles an SFX or a freeze.
    seg_start = segments[index][1]
    if not (seg_start - INSTANT_LEAD <= start < seg_start):
        return False
    return not any(_instant_owned(start, other, segments) for other in range(len(segments)))


def _map_items(segments: Sequence[Segment], items: Sequence[_Item], *, instant, min_piece: float,
               id_limit: int) -> list[_Item]:
    out: list[_Item] = []
    emissions: dict[str, int] = {}
    cursor = 0.0
    for index, (_, seg_start, seg_end) in enumerate(segments):
        for item in items:
            if instant(item):
                if not _instant_in_segment(item.start, index, segments):
                    continue
                at = cursor + (min(max(item.start, seg_start), seg_end) - seg_start)
                update = {"start": at, "end": at}
            else:
                start, end = max(item.start, seg_start), min(item.end, seg_end)
                if end - start < min_piece - 1e-9:
                    continue
                update = {"start": cursor + (start - seg_start), "end": cursor + (end - seg_start)}
            count = emissions.get(item.id, 0)
            emissions[item.id] = count + 1
            if count:
                update["id"] = suffixed_id(item.id, f"@{count}", id_limit)
            out.append(item.model_copy(update=update))
        cursor += seg_end - seg_start
    out.sort(key=lambda item: (item.start, item.id))
    return out


def mapped_cues(segments: Sequence[tuple[str, float, float]], cues: Sequence[FxCue], *,
                min_piece: float = 0.04) -> list[FxCue]:
    """S cues → E cues (see the module docstring)."""
    return _map_items(segments, cues, instant=lambda cue: cue.kind in INSTANT_KINDS, min_piece=min_piece,
                      id_limit=CUE_ID_MAX)


def mapped_beats(segments, beats, *, min_piece: float = 0.04) -> list[Beat]:
    """S beats → E beats, with the span rules of ``mapped_cues``. ``src_start``/``src_end`` (A) are untouched."""
    return _map_items(segments, beats, instant=lambda beat: False, min_piece=min_piece, id_limit=BEAT_ID_MAX)


def mapped_words_e(segments, words) -> list[dict]:
    """Words ``{id, start, end, text, ...}`` (S) → E pieces with ``run`` = the id of the segment they play in.

    Like ``editor_document.mapped_words``: a word cut by a segment edge keeps the part inside, empty text is skipped.
    A word played twice (repeated segment) gets ``f"{id}@{k}"`` on later emissions. Other keys (A times, speaker)
    are copied unchanged. Sorted by ``(start, id)``; this is the studio resolve input.
    """
    ordered = sorted(words, key=lambda word: float(word["start"]))
    out: list[dict] = []
    emissions: dict[str, int] = {}
    cursor = 0.0
    for segment_id, seg_start, seg_end in segments:
        for word in ordered:
            if not str(word.get("text", "")).strip():
                continue
            start, end = max(seg_start, float(word["start"])), min(seg_end, float(word["end"]))
            if end <= start:
                continue
            word_id = str(word.get("id", ""))
            count = emissions.get(word_id, 0)
            emissions[word_id] = count + 1
            out.append({
                **word,
                "id": f"{word_id}@{count}" if count else word_id,
                "start": cursor + (start - seg_start),
                "end": cursor + (end - seg_start),
                "run": segment_id,
            })
        cursor += seg_end - seg_start
    out.sort(key=lambda word: (word["start"], word["id"]))
    return out


def _segment_at(e: float, starts: Sequence[float], segments: Sequence[Segment]) -> int | None:
    for index, (_, seg_start, seg_end) in enumerate(segments):
        if starts[index] - 1e-9 <= e < starts[index] + (seg_end - seg_start):
            return index
    total = starts[-1] + segments[-1][2] - segments[-1][1] if segments else 0.0
    if segments and abs(e - total) <= 1e-6:
        return len(segments) - 1
    return None


def _played_before(index: int, s: float, segments: Sequence[Segment]) -> bool:
    return any(seg_start - 1e-9 <= s < seg_end - 1e-9 for _, seg_start, seg_end in segments[:index])


def inverse_mapped_cues(segments, cues_e: Sequence[FxCue], *, min_piece: float = 0.04) -> list[FxCue]:
    """E cues (studio resolve output) → S cues (AP-H9). Python only.

    - An instant (or a zero-length cue) goes back through the segment containing its E time (the end of the timeline belongs to the last
      segment).
    - A span is split at every junction it crosses, one cue per segment; the first piece keeps the id and later ones
      get ``f"{id}~{k}"``. Pieces shorter than ``min_piece`` are dropped (unless the cue itself is shorter and fits
      inside one segment).
    - A repeated segment plays S already covered earlier in play order: anything landing there (judged at the
      instant, or at a span piece's S midpoint) is dropped, so each S moment carries its cues once (first occurrence).

    Output sorted by ``(start, id)``.
    """
    segments = list(segments)
    if not segments:
        return []
    starts: list[float] = []
    cursor = 0.0
    for _, seg_start, seg_end in segments:
        starts.append(cursor)
        cursor += seg_end - seg_start

    out: list[FxCue] = []
    for cue in cues_e:
        if cue.kind in INSTANT_KINDS or cue.end - cue.start <= 1e-9:
            index = _segment_at(cue.start, starts, segments)
            if index is None:
                continue
            _, seg_start, seg_end = segments[index]
            s = min(max(seg_start + (cue.start - starts[index]), seg_start), seg_end)
            if _played_before(index, s, segments):
                continue
            out.append(cue.model_copy(update={"start": s, "end": s}))
            continue
        pieces = []
        for index, (_, seg_start, seg_end) in enumerate(segments):
            e_start = max(cue.start, starts[index])
            e_end = min(cue.end, starts[index] + (seg_end - seg_start))
            if e_end - e_start <= 1e-9:
                continue
            piece_len = e_end - e_start
            whole = piece_len >= (cue.end - cue.start) - 1e-9
            if piece_len < min_piece - 1e-9 and not whole:
                continue
            s_start = seg_start + (e_start - starts[index])
            s_end = seg_start + (e_end - starts[index])
            if _played_before(index, (s_start + s_end) / 2, segments):
                continue
            pieces.append((s_start, s_end))
        for k, (s_start, s_end) in enumerate(pieces):
            update: dict = {"start": s_start, "end": s_end}
            if k:
                update["id"] = suffixed_id(cue.id, f"~{k}", CUE_ID_MAX)
            out.append(cue.model_copy(update=update))
    out.sort(key=lambda cue: (cue.start, cue.id))
    return out
