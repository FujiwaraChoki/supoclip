import { sequence, uid, type EditDocument, type Segment } from "./document";

export interface SurgicalEdits {
  new_start: string;
  new_end: string;
  internal_cuts?: { cut_start: string; cut_end: string }[];
  cold_open_tease_timestamp?: string | null;
  total_seconds_shaved?: number;
}
export interface Range { start: number; end: number }

export const MIN_KEEP_SECONDS = 0.15;
const round = (n: number) => Math.round(n * 1000) / 1000;

/** "MM:SS.mmm", "MM:SS", "H:MM:SS.mmm" or plain seconds -> seconds (NaN if unusable). */
export function parseClock(value: unknown): number {
  if (typeof value === "number") return Number.isFinite(value) && value >= 0 ? value : NaN;
  if (typeof value !== "string") return NaN;
  const parts = value.trim().split(":");
  if (parts.length > 3 || parts.some((p) => !/^\d+(\.\d+)?$/.test(p))) return NaN;
  return parts.reduce((total, p) => total * 60 + Number(p), 0);
}

/** Clip-relative ranges to keep: [new_start, new_end] minus every internal cut. [] when unusable or a no-op. */
export function packageToKeepRanges(edits: SurgicalEdits | null | undefined, clipDurationSec: number): Range[] {
  if (!edits || !(clipDurationSec > 0)) return [];
  const clamp = (n: number) => Math.min(Math.max(n, 0), clipDurationSec);
  const from = clamp(parseClock(edits.new_start)), to = clamp(parseClock(edits.new_end));
  if (!(to - from >= MIN_KEEP_SECONDS)) return [];
  const cuts = (edits.internal_cuts ?? [])
    .map((c) => ({ start: Math.max(parseClock(c.cut_start), from), end: Math.min(parseClock(c.cut_end), to) }))
    .filter((c) => c.end > c.start)
    .sort((a, b) => a.start - b.start);
  const merged: Range[] = [];
  for (const c of cuts) {
    const last = merged[merged.length - 1];
    if (last && c.start <= last.end) last.end = Math.max(last.end, c.end);
    else merged.push({ ...c });
  }
  const kept: Range[] = [];
  let cursor = from;
  for (const c of [...merged, { start: to, end: to }]) {
    if (c.start - cursor >= MIN_KEEP_SECONDS) kept.push({ start: round(cursor), end: round(c.start) });
    cursor = Math.max(cursor, c.end);
  }
  if (kept.length === 1 && kept[0].start <= 0.001 && kept[0].end >= clipDurationSec - 0.001) return [];
  return kept;
}

/** Keep ranges for a stored package, or [] when it is a no-op or a REJECTED clip left untouched. */
export function rescueKeepRanges(
  pkg: { clip_status: string; surgical_edits: SurgicalEdits } | null | undefined,
  clipDurationSec: number,
): Range[] {
  if (!pkg?.surgical_edits) return [];
  const edits = pkg.surgical_edits;
  if (pkg.clip_status === "REJECTED" && !edits.internal_cuts?.length && !(edits.total_seconds_shaved ?? 0)) return [];
  return packageToKeepRanges(edits, clipDurationSec);
}

/** Map clip-relative ranges through the original draft's edit timeline into source-time segments. */
export function rangesToSegments(original: EditDocument, ranges: Range[]): Segment[] {
  const items = sequence(original);
  return ranges.flatMap((r) =>
    items.flatMap((item) => {
      const a = Math.max(r.start, item.start), b = Math.min(r.end, item.end);
      if (b - a < 0.04) return [];
      const start = item.segment.start + a - item.start;
      return [{ id: uid(), start: round(start), end: round(start + b - a) }];
    }),
  );
}

/** Replace the draft's cuts with the rescue ranges (same semantics as the AI edit's keep_ranges). */
export function applyKeepRanges(doc: EditDocument, original: EditDocument, ranges: Range[]): EditDocument {
  const segments = rangesToSegments(original, ranges);
  return segments.length ? { ...doc, segments } : doc;
}
