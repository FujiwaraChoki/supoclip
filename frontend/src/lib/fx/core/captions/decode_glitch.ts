// `decode_glitch` (§11.1, WP8): TikTokSans-Bold caps where every caption line enters with a decode — each letter
// scrambles through A-Z0-9#%&@ (Latin) or the word's own letters shuffled (other scripts) and locks left → right over
// 180 ms — with an RGB split on the text (three `screen` draws, ±4u) for the first 120 ms and 2u scanlines at 8 % over
// the line box. The `decode` accent re-runs that treatment on its words at 1.3× (the line re-flows around them) and
// tears the line for 2 frames: horizontal slices of the text jump sideways by different amounts (clipped text ops),
// with a pair of thin glitch bars.
//
// Without an on-screen decode_glitch line to live in (another template, captions off or hidden), a decode accent draws
// its text on its own above the caption band.
import type { DrawOp, Shadow } from "../host";
import { BASE_LOOKS, cssColor } from "./base";
import { registerAccent, registerCaptionStyle, type CaptionChunk, type DrawerContext } from "../registry";
import type { AccentCue } from "../types";
import { clamp01, easeOutBack, easeOutQuad, keyframes, progress } from "../easing";
import { gradientOp, mul, rectOp, textOp, translation } from "../layout";
import type { PreparedScene } from "../scene";
import { captionsHidden, cueWords } from "./highlighter_swipe";
import { captionSafe } from "../constants";

/** All letters are locked this long after the line (or the accent) starts. */
export const DECODE_LOCK = 0.18;
/** RGB split on the text for the first 120 ms, ±4u. */
export const DECODE_SPLIT = 0.12;
export const DECODE_SPLIT_PX = 4;
/** Accent words grow to 1.3× (80 ms, slight overshoot) and settle back over the last 120 ms. */
export const DECODE_ACCENT_SCALE = 1.3;
/** The accent's line jolt lasts 2 frames. */
export const DECODE_JOLT_FRAMES = 2;
/** Scanlines: 2u dark lines every 4u at 8 % over the line box. */
export const SCANLINE_ALPHA = 0.08;
export const SCRAMBLE = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789#%&@";

const LOOK = BASE_LOOKS.decode_glitch;
const LATIN = /^[\p{Script=Latin}\p{N}\p{P}\p{S}\p{Zs}]*$/u;
const SCRAMBLABLE = /[\p{L}\p{N}]/u;

/** The character shown in letter slot `index` (of `total` in the line) `local` seconds into a decode. */
export function decodeChar(chars: readonly string[], j: number, index: number, total: number, local: number,
  hash: (n: number) => number, latin: boolean): { char: string; locked: boolean } {
  const actual = chars[j];
  if (!SCRAMBLABLE.test(actual) || local >= (DECODE_LOCK * (index + 1)) / Math.max(1, total)) return { char: actual, locked: true };
  const step = Math.max(0, Math.floor(local * 30));
  const pick = hash((step * 977 + index * 31 + 7) >>> 0);
  if (latin) return { char: SCRAMBLE[Math.floor(pick * SCRAMBLE.length)], locked: false };
  const pool = chars.filter((c) => SCRAMBLABLE.test(c));
  return { char: pool[Math.floor(pick * pool.length)] ?? actual, locked: false };
}

/** Lock time of letter `index` of `total` (s after the decode starts): left → right, the last at 180 ms. */
export const lockTime = (index: number, total: number): number => (DECODE_LOCK * (index + 1)) / Math.max(1, total);

interface Piece {
  text: string; x: number; y: number; px: number; w: number; line: number;
  decode: number;          // seconds into this word's decode (≥ DECODE_LOCK: settled)
  first: number; total: number;   // letter index of the word's first letter and letters in its decode group
  accent: boolean;         // word drawn in the accent colour (emphasis)
}

interface Look {
  family: string; color: string; accent: string; stroke: string | null; strokePx: number; shadow: Shadow | null;
  box: string | null; tracking: number; u: number; hash: (n: number) => number; alpha: number;
  measure: (text: string, family: string, px: number) => number;
}

/** Text ops for pieces: outlines first (they carry the shadow), then fills; per-letter while decoding or splitting. */
function pieceOps(pieces: readonly Piece[], look: Look, dx: number): DrawOp[] {
  const strokes: DrawOp[] = [], fills: DrawOp[] = [];
  const u = look.u;
  for (const piece of pieces) {
    const chars = Array.from(piece.text);
    const latin = LATIN.test(piece.text);
    const settled = piece.decode >= DECODE_LOCK;
    const split = piece.decode >= 0 && piece.decode < DECODE_SPLIT;
    const spacing = look.tracking * piece.px;
    const fill = piece.accent ? look.accent : look.color;
    const glyphs: { text: string; x: number; align: "left" | "center"; locked: boolean; spacing: number }[] = [];
    if (settled) {
      glyphs.push({ text: piece.text, x: piece.x + dx, align: "left", locked: true, spacing });
    } else {
      let x = piece.x + dx;
      chars.forEach((c, j) => {
        const width = look.measure(c, look.family, piece.px);
        const shown = decodeChar(chars, j, piece.first + j, piece.total, piece.decode, look.hash, latin);
        glyphs.push({ text: shown.char, x: x + width / 2, align: "center", locked: shown.locked, spacing: 0 });
        x += width + spacing;
      });
    }
    for (const g of glyphs) {
      if (g.text.trim() === "") continue;
      const base = { text: g.text, font: look.family, px: piece.px, x: g.x, y: piece.y, align: g.align, letterSpacing: g.spacing, alpha: look.alpha };
      const shadow = look.shadow;   // every outline casts it, like the base captions
      if (look.stroke && look.strokePx > 0) strokes.push(textOp({ ...base, fill: null, stroke: look.stroke, strokePx: look.strokePx, shadow }));
      const colour = g.locked ? fill : look.accent;
      if (split) {
        const off = DECODE_SPLIT_PX * u;
        fills.push(textOp({ ...base, x: g.x - off, fill: "#FF0000", blend: "screen" }));
        fills.push(textOp({ ...base, fill: "#00FF00", blend: "screen" }));
        fills.push(textOp({ ...base, x: g.x + off, fill: "#0000FF", blend: "screen" }));
      } else {
        fills.push(textOp({ ...base, fill: colour, shadow: look.stroke && look.strokePx > 0 ? null : shadow }));
      }
    }
  }
  return [...strokes, ...fills];
}

/** 2u lines every 4u at 8 % over a box: one gradient op with hard stops (any height, one draw). */
export function scanlineOps(x: number, y: number, w: number, h: number, u: number, alpha = 1): DrawOp[] {
  if (w <= 0 || h <= 0 || alpha <= 0) return [];
  const line = Math.max(1, 2 * u), period = Math.max(2, 4 * u);
  const stops: [number, string][] = [];
  for (let k = 0; k * period < h; k++) {
    const a = (k * period) / h, b = Math.min(1, (k * period + line) / h), c = Math.min(1, ((k + 1) * period) / h);
    stops.push([a, "rgba(0,0,0,1)"], [b, "rgba(0,0,0,1)"], [b, "rgba(0,0,0,0)"], [c, "rgba(0,0,0,0)"]);
  }
  return [gradientOp({ kind: "linear", x0: 0, y0: y, x1: 0, y1: y + h, stops, rect: [x, y, w, h], alpha: SCANLINE_ALPHA * alpha })];
}

/** Glitch slices for the jolt frames: two thin bars across the line in the accent and cyan, offset opposite ways. */
function sliceOps(x: number, y: number, w: number, h: number, frame: number, look: Look, dx: number): DrawOp[] {
  const u = look.u;
  return [0, 1].map((i) => {
    const bar = (4 + 5 * look.hash(800 + frame * 2 + i)) * u;
    const by = y + h * (0.2 + 0.6 * look.hash(810 + frame * 2 + i));
    return rectOp({ x: x + (i ? -dx : dx) * 0.8, y: by, w, h: bar, fill: i ? "#00E5FF" : look.accent, alpha: 0.75 * look.alpha, blend: "screen" });
  });
}

/** Horizontal slices on a jolt frame: the band split (seeded per frame) and each band's offset. */
export function sliceBands(y0: number, y1: number, frame: number, amp: number, hash: (n: number) => number):
  { y: number; h: number; dx: number }[] {
  const n = 3 + Math.floor(hash(700 + frame) * 3);           // 3–5 bands
  const cuts = Array.from({ length: n - 1 }, (_, i) => (i + 1) / n + (hash(710 + frame * 8 + i) - 0.5) * (0.6 / n)).sort((a, b) => a - b);
  const edges = [0, ...cuts, 1];
  const strong = Math.floor(hash(730 + frame) * n);          // the band that jumps the full amount
  const counter = (strong + 1 + Math.floor(hash(731 + frame) * (n - 1))) % n;   // one band kicks the other way
  return edges.slice(0, -1).map((e, i) => {
    const k = i === strong ? 1 : i === counter ? -0.6 : (hash(740 + frame * 8 + i) - 0.5) * 0.4;
    return { y: y0 + (y1 - y0) * e, h: (y1 - y0) * (edges[i + 1] - e), dx: Math.round(amp * k * 100) / 100 };
  });
}

/** Text ops cut into the bands: one clipped copy per band, shifted by the band's offset. Other ops unchanged. */
export function sliceTextOps(ops: DrawOp[], bands: readonly { y: number; h: number; dx: number }[], x: number, w: number): DrawOp[] {
  const out: DrawOp[] = [];
  for (const op of ops) {
    if (op.op !== "text") { out.push(op); continue; }
    for (const b of bands) {
      if (b.h <= 0) continue;
      out.push({ ...op, transform: mul(translation(b.dx, 0), op.transform), clip: [x, b.y, w, b.h] });
    }
  }
  return out;
}

/** Active decode accents (t inside the cue). */
function decodeCues(p: PreparedScene, t: number): AccentCue[] {
  return (p.cuesByKind.get("accent") ?? []).filter((c): c is AccentCue =>
    c.kind === "accent" && c.variant === "decode" && t >= c.start - 1e-9 && t < c.end);
}

/** Accent word scale: 1 → 1.3 over 80 ms (slight overshoot), hold, back to 1 over the cue's last 120 ms. */
export function accentScale(local: number, dur: number): number {
  if (local < 0 || local > dur) return 1;
  const grow = keyframes([{ t: 0, v: 1 }, { t: 0.08, v: DECODE_ACCENT_SCALE, ease: (x) => Math.min(1.08, easeOutBack(x)) }], local);
  const back = easeOutQuad(progress(local, dur - 0.12, dur));
  return grow + (1 - grow) * back;
}

/** Horizontal jolt for the accent's first 2 frames (seeded direction), else 0. */
export function joltAt(local: number, u: number, hash: (n: number) => number): number {
  const frame = Math.floor(local * 30 + 1e-6);
  if (local < 0 || frame >= DECODE_JOLT_FRAMES) return 0;
  const dir = hash(900) < 0.5 ? -1 : 1;
  const amp = (14 + 10 * hash(901)) * u;
  return frame === 0 ? dir * amp : -dir * amp * 0.55;
}

function lookFor(p: PreparedScene, dc: Pick<DrawerContext, "u" | "hash">, family: string, px: number, alpha = 1): Look {
  const style = p.scene.caption;
  const strokeColor = style?.stroke_color ?? null;
  const strokePx = strokeColor ? (style!.stroke_px > 0 ? style!.stroke_px : LOOK.strokeEm * px) : 0;
  const boxed = !!style?.background;
  const wantShadow = !!style?.shadow || (!strokePx && !boxed);
  return {
    family, color: style?.color ?? "#FFFFFF", accent: cssColor(style?.accent ?? "#FFE000"), stroke: strokeColor, strokePx,
    shadow: wantShadow ? { color: "rgba(0,0,0,0.6)", blur: 12 * dc.u, dx: 0, dy: 4 * dc.u } : null,
    box: style?.background ? cssColor(style.background) : null, tracking: LOOK.tracking, u: dc.u, hash: dc.hash, alpha,
    measure: p.measure,
  };
}

/** One decode_glitch caption line: entrance decode, accent re-decodes at 1.3× with the jolt, box, scanlines. */
export function decodeLineOps(chunk: CaptionChunk, dc: Omit<DrawerContext, "local" | "dur">): DrawOp[] {
  const p = dc.scene;
  if (!p.captionFamily || !chunk.words.length) return [];
  const t = dc.t, local = t - chunk.start, px = chunk.px;
  const look = lookFor(p, dc, p.captionFamily, px);
  const key = (w: { run: string; id: string }) => `${w.run}\u0000${w.id}`;
  const accented = new Map<string, AccentCue>();
  for (const cue of decodeCues(p, t)) for (const w of cueWords(p, cue)) accented.set(key(w), cue);

  // Letter numbering for the line's entrance decode (left → right over the whole chunk).
  const letters = chunk.words.map((w) => Array.from(w.text).length);
  const totalLetters = letters.reduce((a, b) => a + b, 0);
  let jolt = 0, joltFrame = -1;
  const pieces: Piece[] = [];
  let first = 0;
  chunk.words.forEach((w, i) => {
    const cue = accented.get(key(w));
    let decode = local, start = first, total = totalLetters, scale = 1;
    if (cue) {
      const cl = t - cue.start;
      const group = chunk.words.filter((x) => accented.get(key(x)) === cue);
      const before = group.slice(0, group.indexOf(w)).reduce((s, x) => s + Array.from(x.text).length, 0);
      decode = Math.min(local, cl);
      start = cl < local ? before : first;
      total = cl < local ? group.reduce((s, x) => s + Array.from(x.text).length, 0) : totalLetters;
      scale = accentScale(cl, cue.end - cue.start);
      const j = joltAt(cl, dc.u, dc.hash);
      if (j !== 0) { jolt = j; joltFrame = Math.floor(cl * 30 + 1e-6); }
    }
    const size = px * scale;
    pieces.push({ text: w.text, x: w.x, y: w.y + w.h / 2, px: size, w: w.w * scale, line: w.line, decode, first: start, total,
      accent: LOOK.emphasis && w.highlight });
    first += letters[i];
  });

  // Re-flow lines that hold a grown word: keep the line centred, keep the gaps.
  const lines = new Map<number, Piece[]>();
  for (const piece of pieces) lines.set(piece.line, [...(lines.get(piece.line) ?? []), piece]);
  const boxes: { x: number; y: number; w: number; h: number }[] = [];
  const side = captionSafe(p.width, p.height).side * p.width;
  for (const [li, row] of lines) {
    const words = chunk.words.filter((w) => w.line === li);
    const gaps = words.slice(1).map((w, j) => w.x - (words[j].x + words[j].w));
    const gapSum = gaps.reduce((s, g) => s + g, 0);
    const oldLeft = words[0].x, oldRight = words[words.length - 1].x + words[words.length - 1].w;
    // A grown word must not push the line out of the frame: past the safe width, the growth shrinks to fit.
    const room = Math.max(oldRight - oldLeft, p.width - 2 * side);
    const base = words.reduce((s, w) => s + w.w, 0) + gapSum;
    const grown = row.reduce((s, piece) => s + piece.w, 0) + gapSum;
    if (grown > room && grown > base) {
      const k = (room - base) / (grown - base);
      row.forEach((piece, j) => {
        const scale = 1 + (piece.w / Math.max(1e-9, words[j].w) - 1) * k;
        piece.w = words[j].w * scale;
        piece.px = px * scale;
      });
    }
    const width = row.reduce((s, piece) => s + piece.w, 0) + gapSum;
    let x = (oldLeft + oldRight) / 2 - width / 2;
    // Only a grown line is nudged back inside the safe area (an untouched line stays where the layout put it).
    if (width > base + 1e-6 && width <= p.width - 2 * side) x = Math.min(Math.max(x, side), p.width - side - width);
    row.forEach((piece, j) => { piece.x = x; x += piece.w + (gaps[j] ?? 0); });
    const tallest = Math.max(...row.map((piece) => piece.px));
    boxes.push({ x: row[0].x, y: row[0].y - tallest / 2, w: width, h: tallest });
  }

  const ops: DrawOp[] = [];
  if (look.box) {
    for (const b of boxes) {
      const padX = px * 0.32, padY = px * 0.16;
      ops.push(rectOp({ x: b.x - padX, y: b.y - padY, w: b.w + 2 * padX, h: b.h + 2 * padY, radius: px * 0.22, fill: look.box }));
    }
  }
  let text = pieceOps(pieces, look, 0);
  if (joltFrame >= 0) {
    // The jolt: the line is cut into horizontal slices that jump sideways by different amounts (real displacement,
    // the letters tear), across every line of the chunk.
    const y0 = Math.min(...boxes.map((b) => b.y)) - px * 0.6, y1 = Math.max(...boxes.map((b) => b.y + b.h)) + px * 0.6;
    text = sliceTextOps(text, sliceBands(y0, y1, joltFrame, Math.abs(jolt) * 1.6, dc.hash), 0, p.width);
  }
  ops.push(...text);
  for (const b of boxes) {
    const padY = b.h * 0.12;
    ops.push(...scanlineOps(b.x - px * 0.1, b.y - padY, b.w + px * 0.2, b.h + 2 * padY, dc.u));
    if (joltFrame >= 0) ops.push(...sliceOps(b.x - px * 0.1, b.y, b.w + px * 0.2, b.h, joltFrame, look, jolt));
  }
  return ops;
}

/** A decode accent with no decode_glitch line to live in: its text alone, 1.3× the caption size, above the captions. */
export function standaloneDecodeOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene, W = p.width, H = p.height, u = dc.u;
  const text = cue.text.replace(/\s+/g, " ").trim().toLocaleUpperCase();
  if (!text) return [];
  const style = p.scene.caption;
  const family = p.captionFamily ?? p.family(style?.font || "TikTokSans-Bold");
  let px = Math.max(34 * u, (style?.size_px ?? 78 * u) * DECODE_ACCENT_SCALE);
  const tracking = LOOK.tracking;
  const widthAt = (size: number) => p.measure(text, family, size) + tracking * size * Math.max(0, Array.from(text).length - 1);
  if (widthAt(px) > 0.88 * W) px *= (0.88 * W) / widthAt(px);
  const width = widthAt(px);
  const chunk = p.chunkAt(dc.t);
  const yFrac = style ? (p.captionYAt(cue.start) ?? style.y) - (chunk ? 0.13 : 0) : 0.62;
  const y = Math.min(H - px, Math.max(px, yFrac * H));
  const out = clamp01((dc.dur - dc.local) / 0.12);
  const look = lookFor(p, dc, family, px, out);
  const jolt = joltAt(dc.local, u, dc.hash);
  const piece: Piece = { text, x: (W - width) / 2, y, px, w: width, line: 0, decode: dc.local, first: 0, total: Array.from(text).length, accent: false };
  const frame = Math.floor(dc.local * 30 + 1e-6);
  let textOps = pieceOps([piece], look, 0);
  if (jolt !== 0) textOps = sliceTextOps(textOps, sliceBands(y - px * 1.1, y + px * 1.1, frame, Math.abs(jolt) * 1.6, dc.hash), 0, W);
  const ops = [...textOps, ...scanlineOps(piece.x - px * 0.1, y - px * 0.62, width + px * 0.2, px * 1.24, u, out)];
  if (dc.local >= 0 && frame < DECODE_JOLT_FRAMES) ops.push(...sliceOps(piece.x - px * 0.1, y - px / 2, width + px * 0.2, px, frame, look, jolt));
  return ops;
}

/** Is the accent's text living in an on-screen decode_glitch caption line right now? */
function inLine(cue: AccentCue, dc: DrawerContext): boolean {
  const p = dc.scene;
  if (p.scene.template !== "decode_glitch" || !p.captionFamily) return false;
  if (captionsHidden(p, dc.t)) return false;   // a slam / kinetic hides the caption line
  const chunk = p.chunkAt(dc.t);
  if (!chunk) return false;
  const ids = new Set(cueWords(p, cue).map((w) => `${w.run}\u0000${w.id}`));
  return chunk.words.some((w) => ids.has(`${w.run}\u0000${w.id}`));
}

registerCaptionStyle({
  template: "decode_glitch",
  line: decodeLineOps,
});

registerAccent({
  variant: "decode",
  layer: "caption",
  window: (cue) => [cue.start, cue.end],
  ops: (cue, dc) => (inLine(cue, dc) ? [] : standaloneDecodeOps(cue, dc)),
});
