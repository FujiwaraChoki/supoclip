// `kinetic_stack` (§11.1, WP8): THEBOLDFONT two-line captions, and the `kinetic` accent — the hook sentence as a stack
// of big type. The sentence splits into rows of 1–3 words; every row is sized to fill 88 % of the width (mixed sizes:
// emphasis words are 1.6× their row-mates), rows stack down from y = 0.14 and start a fresh screen when the stack
// would pass 80 % of the height. Each row slides in on its first word (60 ms, from 30u left, alpha 0 → 1), rows
// alternate white and the accent colour, and normal captions hide while it plays (`hidesCaptions`). The desaturated
// picture behind it is a separate resolver cue. With approximate word timing a row appears with its caption line.
// The stack keeps off the speaker's head: when a head seen during the cue reaches into the stack's band, the stack
// moves to the larger free band beneath the lowest chin or above the highest head (`kineticBand`).
import type { DrawOp } from "../host";
import { BASE_LOOKS, baseCaptionOps, cssColor } from "./base";
import { registerAccent, registerCaptionStyle, type DrawerContext } from "../registry";
import type { AccentCue, FxWord } from "../types";
import { captionSafe } from "../constants";
import { clamp01, easeOutCubic, progress } from "../easing";
import { pictureMatrix, textOp, transformRect } from "../layout";
import type { PreparedScene } from "../scene";
import { pictureStateAt, type FxClock } from "../frame";
import { approxTiming, cueWords, runStart, spokenWords } from "./highlighter_swipe";

/** Every row's width as a fraction of the frame width. */
export const KINETIC_FILL = 0.88;
/** The stack starts this far down (fraction of H). */
export const KINETIC_TOP = 0.14;
/** A screen's stack never grows taller than this fraction of H (and never into the bottom safe margin). */
export const KINETIC_MAX_STACK = 0.8;
/** Emphasis words are this much bigger than their row-mates. */
export const KINETIC_EMPHASIS = 1.6;
/** A single row's letters never exceed this fraction of H (a lone short word would otherwise be a wall of type). */
export const KINETIC_MAX_ROW = 0.24;
/** Slide-in: 60 ms from 30u left. */
export const KINETIC_SLIDE = 0.06;
export const KINETIC_SLIDE_FROM = 30;
/** Fade out over the last 120 ms of the cue. */
export const KINETIC_OUT = 0.12;
export const KINETIC_FONT = "THEBOLDFONT";
/** Faces smaller than this share of H are background people (== HOOK_PLACE.minFace). */
export const KINETIC_MIN_FACE = 0.05;
/** Space kept between a head and the stack (fraction of H). */
export const KINETIC_HEAD_GAP = 0.02;
/** A free band beside the head must be at least this tall (fraction of H); otherwise the stack keeps its home. */
export const KINETIC_MIN_BAND = 0.2;
/** Rows of 1–3 words, filled up to ~12 weighted characters (emphasis counts 1.6×). */
const ROW_WORDS = 3;
const ROW_CHARS = 12;
const ROW_MIN_CHARS = 6;
/** Caps-font metrics: a row's visual height and where the baseline sits below the row top (fractions of px). */
const CAP_BOX = 0.8;
const CENTER_ABOVE_BASELINE = 0.35;
const BASELINE = 0.74;

export interface KineticWord { text: string; x: number; y: number; px: number; w: number; emphasis: boolean }
export interface KineticRow {
  words: KineticWord[];
  start: number;       // when the row slides in (E seconds)
  top: number; height: number; width: number; x: number;
  screen: number;
  filled: boolean;     // false when a height cap kept the row narrower than 88 % of the width
  color: "white" | "accent";
}
export interface KineticLayout { rows: KineticRow[]; screens: number[] /* screen start times */ }

const isEmphasis = (w: Pick<FxWord, "text" | "highlight">) => w.highlight || /\d/.test(w.text);

/** The sentence's words with their reveal times: scene words when timed, else cue.text spread over the cue. */
function sentence(p: PreparedScene, cue: AccentCue): { text: string; start: number; emphasis: boolean }[] {
  const tokens = cue.text.trim().split(/\s+/).filter(Boolean);
  const words = tokens.length
    ? cueWords(p, cue)
    : spokenWords(p).filter((w) => w.start >= cue.start - 0.06 && w.start < cue.end);
  if (words.length) {
    const approx = approxTiming(p);
    return words.map((w) => ({ text: w.text, start: approx ? runStart(p, w) : w.start, emphasis: isEmphasis(w) }));
  }
  const span = Math.max(0.01, cue.end - cue.start - 0.3);
  return tokens.map((text, i) => ({ text, start: cue.start + (span * i) / Math.max(1, tokens.length), emphasis: /\d/.test(text) }));
}

/** Groups words into rows of 1–3, aiming for 6–12 weighted characters per row. */
export function kineticRows<T extends { text: string; emphasis: boolean }>(words: readonly T[]): T[][] {
  const weight = (w: T) => Array.from(w.text).length * (w.emphasis ? KINETIC_EMPHASIS : 1);
  const rows: T[][] = [];
  let row: T[] = [], chars = 0;
  for (const w of words) {
    const add = weight(w);
    if (row.length && (row.length >= ROW_WORDS || (chars >= ROW_MIN_CHARS && chars + add > ROW_CHARS))) {
      rows.push(row); row = []; chars = 0;
    }
    row.push(w);
    chars += add;
  }
  if (row.length) rows.push(row);
  return rows;
}

/**
 * The vertical band the stack may use: its home (from `KINETIC_TOP`, at most `KINETIC_MAX_STACK` tall, clear of the
 * bottom safe margin), unless a head seen while the cue plays reaches into it. Heads are the face-track boxes through
 * the picture transform at each sample (so a punch-in during the cue counts), ignoring background faces. Then the
 * stack takes the larger free band, beneath the lowest chin or above the highest head, when that band is at least
 * `KINETIC_MIN_BAND` tall; with no room either side (a face filling the frame) it stays at home.
 */
export function kineticBand(p: PreparedScene, cue: AccentCue): { top: number; bottom: number } {
  const W = p.width, H = p.height;
  const homeTop = KINETIC_TOP * H;
  const limit = H - captionSafe(W, H).bottom;
  const home = { top: homeTop, bottom: Math.min(homeTop + KINETIC_MAX_STACK * H, limit) };
  let headTop = Infinity, chin = -Infinity;
  for (const [t, rows] of p.scene.face_track?.samples ?? []) {
    if (t < cue.start - 0.2 || t > cue.end + 0.2) continue;
    const e = Math.min(Math.max(t, cue.start), Math.max(cue.start, cue.end - 1e-3));
    const clock: FxClock = { e, o: e, frame: Math.round(e * 30) };
    const m = pictureMatrix(pictureStateAt(p, clock, p.faceAt(e)), W, H);
    for (const row of rows ?? []) {
      if (row.length < 4 || row[3] < KINETIC_MIN_FACE) continue;
      const r = transformRect(m, { x: row[0] * W, y: row[1] * H, w: row[2] * W, h: row[3] * H });
      headTop = Math.min(headTop, r.y);
      chin = Math.max(chin, r.y + r.h);
    }
  }
  if (!(chin > home.top) || !(headTop < home.bottom)) return home;   // no head, or none inside the home band
  const gap = KINETIC_HEAD_GAP * H;
  const below = { top: Math.max(home.top, chin + gap), bottom: limit };
  const above = { top: captionSafe(W, H).top, bottom: Math.min(home.bottom, headTop - gap) };
  const size = (b: { top: number; bottom: number }) => b.bottom - b.top;
  const best = size(below) >= size(above) ? below : above;
  if (size(best) < KINETIC_MIN_BAND * H) return home;
  return { top: best.top, bottom: Math.min(best.bottom, best.top + KINETIC_MAX_STACK * H) };
}

const layouts = new WeakMap<PreparedScene, Map<string, KineticLayout>>();

/** Row sizes, positions and screens for a kinetic cue (memoised per prepared scene). */
export function kineticLayout(p: PreparedScene, cue: AccentCue): KineticLayout {
  let map = layouts.get(p);
  if (!map) { map = new Map(); layouts.set(p, map); }
  const hit = map.get(cue.id);
  if (hit) return hit;
  const W = p.width, H = p.height, u = p.u;
  const family = p.family(KINETIC_FONT);
  const words = sentence(p, cue).map((w) => ({ ...w, text: w.text.toLocaleUpperCase() }));
  const { top, bottom } = kineticBand(p, cue);
  const gap = 14 * u;
  const rows: KineticRow[] = [];
  const screens: number[] = [];
  let y = top, screen = -1;
  kineticRows(words).forEach((group, index) => {
    // Width at 100 px per unit of size, then the size that fills 88 % of the width.
    const ref = 100;
    const space = p.measure(" ", family, ref) / ref;
    const units = group.reduce((sum, w) => sum + (p.measure(w.text, family, ref) / ref) * (w.emphasis ? KINETIC_EMPHASIS : 1), 0)
      + space * (group.length - 1);
    const maxMul = Math.max(...group.map((w) => (w.emphasis ? KINETIC_EMPHASIS : 1)));
    const fillPx = (KINETIC_FILL * W) / Math.max(units, 1e-6);
    const px = Math.min(fillPx, (KINETIC_MAX_ROW * H) / maxMul, (bottom - top) / (CAP_BOX * maxMul));
    const height = CAP_BOX * px * maxMul;
    if (screen < 0 || y + height > bottom) { screen++; y = top; screens.push(group[0].start); }
    const width = units * px;
    let x = (W - width) / 2;
    const baseline = y + BASELINE * px * maxMul;
    const laid = group.map((w): KineticWord => {
      const size = px * (w.emphasis ? KINETIC_EMPHASIS : 1);
      const ww = (p.measure(w.text, family, ref) / ref) * size;
      const word = { text: w.text, x, y: baseline - CENTER_ABOVE_BASELINE * size, px: size, w: ww, emphasis: w.emphasis };
      x += ww + space * px;
      return word;
    });
    rows.push({ words: laid, start: group[0].start, top: y, height, width, x: (W - width) / 2, screen, filled: px >= fillPx - 1e-9, color: index % 2 === 0 ? "white" : "accent" });
    y += height + gap;
  });
  // Every screen gets its turn: with approximate timing (or tightly spoken rows) a screen could start as early as the
  // previous one and that one would never show, so each holds for at least min(its share of the cue, 0.8 s).
  const hold = Math.min(0.8, (cue.end - cue.start) / Math.max(1, screens.length));
  if (screens.length) screens[0] = Math.max(screens[0], cue.start);
  for (let i = 1; i < screens.length; i++) screens[i] = Math.max(screens[i], screens[i - 1] + hold);
  const layout = { rows, screens };
  map.set(cue.id, layout);
  return layout;
}

export function kineticAccentOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene, t = dc.t, u = dc.u;
  const { rows, screens } = kineticLayout(p, cue);
  if (!rows.length) return [];
  // The screen on show: the last one whose first row has started (the first screen from the cue start).
  let screen = 0;
  screens.forEach((s, i) => { if (i > 0 && t >= s) screen = i; });
  const out = clamp01((cue.end - t) / KINETIC_OUT);
  if (out <= 0) return [];
  const family = p.family(KINETIC_FONT);
  const accent = cssColor(p.scene.caption?.accent ?? "#FFE000");
  const ops: DrawOp[] = [];
  const strokes: DrawOp[] = [];
  for (const row of rows) {
    if (row.screen !== screen) continue;
    // A row never shows before its screen (the first screen: before the cue).
    const reveal = Math.max(row.start, screens[row.screen]);
    if (t < reveal) continue;
    const k = easeOutCubic(progress(t, reveal, reveal + KINETIC_SLIDE));
    const dx = -(1 - k) * KINETIC_SLIDE_FROM * u;
    const alpha = k * out;
    const fill = row.color === "white" ? "#FFFFFF" : accent;
    for (const w of row.words) {
      const common = { text: w.text, font: family, px: w.px, x: w.x + dx, y: w.y, align: "left" as const, alpha };
      strokes.push(textOp({ ...common, fill: null, stroke: "#000000", strokePx: Math.max(6 * u, w.px * 0.07),
        shadow: { color: "rgba(0,0,0,0.55)", blur: 0, dx: 0, dy: Math.max(6 * u, w.px * 0.05) } }));
      ops.push(textOp({ ...common, fill }));
    }
  }
  return [...strokes, ...ops];
}

registerCaptionStyle({
  template: "kinetic_stack",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.kinetic_stack),
});

registerAccent({
  variant: "kinetic",
  layer: "overlay",
  hidesCaptions: true,
  window: (cue) => [cue.start, cue.end],
  ops: kineticAccentOps,
});
