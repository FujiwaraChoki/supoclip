// S → E cue mapping through play-order segments: mirror of backend/src/edit_fx/mapping.py (mapped_cues/mapped_beats).
// Follows mapped_broll (editor_document.py) for spans; instants map by their start only (INSTANT_KINDS).
import { INSTANT_KINDS, type Beat, type CaptionRow, type FxCue } from "./types";

/** A play-order segment (already continuous-merged): id, S start, S end. Tuples mirror the Python signature. */
export type SegmentSpan = readonly [string, number, number] | { readonly id: string; readonly start: number; readonly end: number };

const INSTANT_LEAD = 0.05;    // an instant this close before a segment start still belongs to it
const JUNCTION_EPS = 1e-6;    // segments closer than this in S are contiguous
export const CUE_ID_MAX = 32;
export const BEAT_ID_MAX = 24;

const normalise = (segments: readonly SegmentSpan[]): [string, number, number][] =>
  segments.map((s) => {
    if (Array.isArray(s)) return [s[0], s[1], s[2]] as [string, number, number];
    const o = s as { id: string; start: number; end: number };
    return [o.id, o.start, o.end] as [string, number, number];
  });

const byStartThenId = <T extends { start: number; id: string }>(a: T, b: T) =>
  a.start - b.start || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/** base + suffix, cutting base so the result is at most `limit` characters (== mapping.suffixed_id). */
export function suffixedId(base: string, suffix: string, limit: number): string {
  if (base.length + suffix.length <= limit) return base + suffix;
  return base.slice(0, Math.max(0, limit - suffix.length)) + suffix;
}

function instantOwned(start: number, index: number, segs: readonly [string, number, number][]): boolean {
  const [, segStart, segEnd] = segs[index];
  if (segStart <= start && start < segEnd) return true;
  // An instant at the very end of a segment belongs to the segment it ends, unless play continues seamlessly (AR-m4).
  const following = segs[index + 1];
  const contiguous = following !== undefined && Math.abs(following[1] - segEnd) < JUNCTION_EPS;
  return !contiguous && segEnd <= start && start <= segEnd + JUNCTION_EPS;
}

function instantInSegment(start: number, index: number, segs: readonly [string, number, number][]): boolean {
  if (instantOwned(start, index, segs)) return true;
  // The lead only rescues an instant a cut removed; one another segment already plays must not be emitted twice.
  const segStart = segs[index][1];
  if (!(segStart - INSTANT_LEAD <= start && start < segStart)) return false;
  return !segs.some((_, other) => instantOwned(start, other, segs));
}

interface Timed { id: string; start: number; end: number }

function mapTimed<T extends Timed>(segments: readonly SegmentSpan[], items: readonly T[], isInstant: (item: T) => boolean,
  minPiece: number, idLimit: number): T[] {
  const segs = normalise(segments);
  const out: T[] = [];
  const emissions = new Map<string, number>();
  let cursor = 0;
  segs.forEach(([, segStart, segEnd], index) => {
    for (const item of items) {
      let start: number;
      let end: number;
      if (isInstant(item)) {
        if (!instantInSegment(item.start, index, segs)) continue;
        start = end = cursor + (Math.min(Math.max(item.start, segStart), segEnd) - segStart);
      } else {
        const a = Math.max(item.start, segStart);
        const b = Math.min(item.end, segEnd);
        if (b - a < minPiece - 1e-9) continue;
        start = cursor + (a - segStart);
        end = cursor + (b - segStart);
      }
      const count = emissions.get(item.id) ?? 0;
      emissions.set(item.id, count + 1);
      out.push({ ...item, id: count ? suffixedId(item.id, `@${count}`, idLimit) : item.id, start, end });
    }
    cursor += segEnd - segStart;
  });
  return out.sort(byStartThenId);
}

/**
 * Cues (S) → E. Span cues are cut by each segment and placed at cursor + (max(start, seg.start) - seg.start); pieces
 * shorter than minPiece are dropped. Instants (sfx, freeze, transition) are emitted once per segment that plays them
 * (seg.start <= start < seg.end, or seg.end <= start <= seg.end + 1e-6 when the next segment does not start at
 * seg.end). An instant no segment plays (a cut removed it) is still emitted by each segment with
 * seg.start - 0.05 <= start < seg.start, so a small gap never doubles it. Emissions are clamped into the segment. Every emission after the first gets `${id}@${k}` (cut to 32 chars). Sorted by (start, id).
 * Generation passes [["clip", 0, duration]] (identity).
 */
export function mappedCues(segments: readonly SegmentSpan[], cues: readonly FxCue[], minPiece = 0.04): FxCue[] {
  return mapTimed(segments, cues, (cue) => INSTANT_KINDS.has(cue.kind), minPiece, CUE_ID_MAX);
}

/** Beats (S) → E with the span rules of mappedCues (ids cut to 24 chars). src_start/src_end (A) are untouched. */
export function mappedBeats(segments: readonly SegmentSpan[], beats: readonly Beat[], minPiece = 0.04): Beat[] {
  return mapTimed(segments, beats, () => false, minPiece, BEAT_ID_MAX);
}

/**
 * S caption rows on the E clock (== caption_rows.mapped_caption_rows): each row cut by each play-order segment (a
 * reordered or repeated segment brings its rows along) and placed at the segment's E cursor; touching pieces of one
 * height join.
 */
export function mappedCaptionRows(segments: readonly SegmentSpan[], rows: readonly CaptionRow[]): CaptionRow[] {
  const out: CaptionRow[] = [];
  const ordered = [...rows].sort((a, b) => a.start - b.start);
  let cursor = 0;
  for (const [, segStart, segEnd] of normalise(segments)) {
    for (const row of ordered) {
      const start = Math.max(segStart, row.start), end = Math.min(segEnd, row.end);
      if (end <= start) continue;
      const e0 = cursor + start - segStart, e1 = cursor + end - segStart;
      const last = out[out.length - 1];
      if (last && last.y === row.y && Math.abs(last.end - e0) < 1e-6) last.end = e1;
      else out.push({ start: e0, end: e1, y: row.y });
    }
    cursor += segEnd - segStart;
  }
  return out.slice(0, 400);
}
