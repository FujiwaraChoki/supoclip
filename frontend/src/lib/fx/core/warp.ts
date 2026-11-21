// Time warp (speed ramps, freezes): mirror of backend/src/edit_fx/warp.py (§10). Keep the two in lockstep.
// E = edit seconds, F = final seconds. Pieces cover [0, editDuration] in play order:
// - a linear piece maps [e0, e1] to [o0, o0 + (e1 - e0) / rate] (rate = edit seconds per final second, 1 = normal);
// - a hold piece (rate 0, e0 == e1) repeats the frame at e0 for `hold` final seconds.
// Quantisation (G4): q(x) = floor(x*30 + 0.5)/30 for freeze instants, holds, speed edges and speed piece lengths.
// Outside [0, editDuration] both maps extrapolate with rate 1.
import { FPS, FRAME, type FxCue, type WarpPieceJson } from "./types";

export interface Warp {
  readonly pieces: readonly WarpPieceJson[];   // identity = one rate-1 piece (none for a zero-length edit)
  readonly editDuration: number;
  readonly outDuration: number;
}

const EPS = 1e-9;

/** Snap seconds to the 30 fps grid, rounding half up: floor(x*30 + 0.5)/30 (== warp.quantize, never banker's). */
export const quantize = (x: number): number => Math.floor(x * FPS + 0.5) / FPS;

/** Final (F) seconds a piece occupies. */
export const pieceLength = (p: WarpPieceJson): number => (p.rate === 0 ? p.hold : (p.e1 - p.e0) / p.rate);

export function identityWarp(editDuration: number): Warp {
  const d = Math.max(0, editDuration);
  return { pieces: d > 0 ? [{ e0: 0, e1: d, o0: 0, rate: 1, hold: 0 }] : [], editDuration: d, outDuration: d };
}

export const isNontrivial = (warp: Warp): boolean => warp.pieces.some((p) => p.rate !== 1 || p.hold > 0);

const clamp = (v: number, lo: number, hi: number) => Math.min(Math.max(v, lo), hi);
const byStartThenId = (a: FxCue, b: FxCue) => a.start - b.start || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

/**
 * The warp for the speed and freeze cues among `cues` (E time); other kinds are ignored.
 * - Speed cues are clipped to [0, editDuration] and their edges quantised. Taken in (start, id) order, an overlapping
 *   later cue starts where the earlier one ends (earlier start wins); rate 1 or empty spans are no-ops.
 * - Freezes: instant and hold quantised; a freeze strictly inside a speed span is dropped; a second freeze at the
 *   same instant is dropped.
 * - A speed piece lasts max(1 frame, q(len / rate)) and its rate is adjusted to fit.
 */
export function buildWarp(cues: readonly FxCue[], editDuration: number): Warp {
  const duration = Math.max(0, editDuration);

  const spans: [number, number, number][] = [];
  let lastEnd = 0;
  for (const cue of cues.filter((c) => c.kind === "speed").sort(byStartThenId)) {
    if (cue.kind !== "speed") continue;
    let a = quantize(clamp(cue.start, 0, duration));
    const b = Math.min(quantize(clamp(cue.end, 0, duration)), duration);
    a = Math.max(a, lastEnd);
    if (cue.rate === 1 || b - a <= EPS) continue;
    spans.push([a, b, cue.rate]);
    lastEnd = b;
  }

  const freezes: [number, number][] = [];
  for (const cue of cues.filter((c) => c.kind === "freeze").sort(byStartThenId)) {
    if (cue.kind !== "freeze") continue;
    const e = Math.min(quantize(clamp(cue.start, 0, duration)), duration);
    const hold = quantize(cue.hold);
    if (hold <= EPS) continue;
    if (spans.some(([a, b]) => a + EPS < e && e < b - EPS)) continue;
    if (freezes.some(([other]) => Math.abs(e - other) <= EPS)) continue;
    freezes.push([e, hold]);
  }

  // Freezes sort before a speed span starting at the same instant (the hold shows the span's first frame).
  type Event = { t: number; kind: 0; e: number; hold: number } | { t: number; kind: 1; a: number; b: number; rate: number };
  const events: Event[] = [
    ...freezes.map(([e, hold]) => ({ t: e, kind: 0 as const, e, hold })),
    ...spans.map(([a, b, rate]) => ({ t: a, kind: 1 as const, a, b, rate })),
  ].sort((x, y) => x.t - y.t || x.kind - y.kind);

  const pieces: WarpPieceJson[] = [];
  let o = 0;
  const linear = (e0: number, e1: number, rate: number) => {
    if (e1 - e0 <= EPS) return;
    let length: number;
    let actual: number;
    if (rate === 1) {
      length = e1 - e0;
      actual = 1;
    } else {
      length = Math.max(FRAME, quantize((e1 - e0) / rate));
      actual = (e1 - e0) / length;
    }
    pieces.push({ e0, e1, o0: o, rate: actual, hold: 0 });
    o += length;
  };

  let cursor = 0;
  for (const ev of events) {
    if (ev.kind === 0) {
      linear(cursor, ev.e, 1);
      pieces.push({ e0: ev.e, e1: ev.e, o0: o, rate: 0, hold: ev.hold });
      o += ev.hold;
      cursor = Math.max(cursor, ev.e);
    } else {
      linear(cursor, ev.a, 1);
      linear(ev.a, ev.b, ev.rate);
      cursor = ev.b;
    }
  }
  linear(cursor, duration, 1);
  return { pieces, editDuration: duration, outDuration: o };
}

/** E → F. At a freeze instant returns the START of the hold. */
export function toOutput(warp: Warp, e: number): number {
  if (e <= 0) return e;
  for (const p of warp.pieces) {
    if (p.rate === 0) {
      if (Math.abs(e - p.e0) <= EPS) return p.o0;
    } else if (p.e0 <= e && e <= p.e1) {
      return p.o0 + (e - p.e0) / p.rate;
    }
  }
  return warp.outDuration + (e - warp.editDuration);
}

/** F → E. Monotone non-decreasing; constant across holds. */
export function toEdit(warp: Warp, o: number): number {
  if (o <= 0) return o;
  for (const p of warp.pieces) {
    if (p.o0 <= o && o < p.o0 + pieceLength(p)) {
      return p.rate === 0 ? p.e0 : p.e0 + (o - p.o0) * p.rate;
    }
  }
  return warp.editDuration + (o - warp.outDuration);
}

/** float(v) as warp_from_json reads it: numbers, booleans and numeric strings; null, "" and objects are invalid. */
function num(v: unknown): number {
  if (typeof v === "number") return v;
  if (typeof v === "boolean") return v ? 1 : 0;
  if (typeof v === "string" && v.trim() !== "") return Number(v);
  return Number.NaN;
}

/**
 * Rebuild a stored warp (sidecar edit_fx_warp / FxScene.warp). [] or null is identity over `editDuration`.
 * Lenient like warp_from_json: malformed pieces (non-finite, negative, bad holds, gaps) read as identity, never throw.
 */
export function warpFromJson(pieces: readonly WarpPieceJson[] | null | undefined, editDuration = 0): Warp {
  if (!Array.isArray(pieces) || !pieces.length) return identityWarp(editDuration);
  const parsed: WarpPieceJson[] = [];
  let eCursor = 0;
  let oCursor = 0;
  for (const raw of pieces as readonly unknown[]) {
    if (!raw || typeof raw !== "object") return identityWarp(editDuration);
    const r = raw as Record<string, unknown>;
    const p = { e0: num(r.e0), e1: num(r.e1), o0: num(r.o0), rate: num(r.rate), hold: "hold" in r ? num(r.hold) : 0 };
    const ok = [p.e0, p.e1, p.o0, p.rate, p.hold].every(Number.isFinite) && p.rate >= 0 && p.hold >= 0
      && !(p.rate === 0 && (Math.abs(p.e1 - p.e0) > 1e-6 || p.hold <= 0))
      && !(p.rate > 0 && p.e1 <= p.e0)
      && Math.abs(p.e0 - eCursor) <= 1e-6 && Math.abs(p.o0 - oCursor) <= 1e-6;
    if (!ok) return identityWarp(editDuration);
    parsed.push(p);
    eCursor = p.e1;
    oCursor = p.o0 + pieceLength(p);
  }
  return { pieces: parsed, editDuration: eCursor, outDuration: oCursor };
}

/** WarpPieceJson[] (== Warp.to_json()): [] for an identity warp. */
export const warpToJson = (warp: Warp): WarpPieceJson[] =>
  isNontrivial(warp) ? warp.pieces.map((p) => ({ e0: p.e0, e1: p.e1, o0: p.o0, rate: p.rate, hold: p.hold })) : [];
