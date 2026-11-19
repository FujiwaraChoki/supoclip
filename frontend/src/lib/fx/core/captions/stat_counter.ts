// `stat_counter` (WP7, §11.1): clean captions, and the counter accent: the spoken number counts up from 0 to its value
// over max(0.6, wordDur + 0.4) s (easeOutExpo) in big tabular digits (every digit has the same advance, measured once,
// so the number never shuffles sideways while it counts), keeping its prefix, suffix, thousands separators and the
// spoken number's decimals. At lock it stamps: scale 1.4 → 1 in 120 ms, a −6° tilt, the accent colour and a 2-frame
// white rim (14u outline, alpha 1 → 0). Size 2.2 × the caption size, centred at y = 0.36 (moved under the chin or
// above the hair when that would cover the speaker's head), at most 88 % of the width.
// The lock lands on the resolver's lock sound (stamp / cash register at start + max(0.6, beat duration + 0.4)).
import type { DrawOp, Mat2D } from "../host";
import type { DrawerContext } from "../registry";
import type { AccentCue } from "../types";
import { FRAME } from "../types";
import { clamp01, easeOutBack, easeOutExpo, easeOutQuad, keyframes, progress } from "../easing";
import { overlapArea, placeAt, safeRect, textOp } from "../layout";
import { captionBand, headBox } from "./sticker_place";
import { counterFormat, type CounterFormat, type PreparedScene } from "../scene";
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";
import { accentColor, cueTokens, REF_PX } from "./anchor";

const DEG = Math.PI / 180;

/** §11.1 counter constants (px in u). */
export const COUNTER = {
  font: "Poppins-ExtraBold",
  sizeOfCaption: 2.2,
  centerY: 0.36,
  maxWidthFrac: 0.88,
  minCount: 0.6,
  countPad: 0.4,
  stampFrom: 1.4,
  stampDur: 0.12,
  stampDeg: -6,
  rimPx: 14,
  rimFrames: 2,
  enter: 0.12,
  fadeOut: 0.15,
  strokeEm: 0.07,
} as const;

const durations = new WeakMap<PreparedScene, WeakMap<AccentCue, number>>();

/**
 * Seconds from the cue start to the lock: max(0.6, wordDur + 0.4), where wordDur spans the spoken number's words (the
 * cue text's words, starting at the cue). Without those words it is recovered from the resolver's cue length
 * (count + 0.9 s hold). Never past the cue end.
 */
export function counterDuration(p: PreparedScene, cue: AccentCue): number {
  let byCue = durations.get(p);
  if (!byCue) { byCue = new WeakMap(); durations.set(p, byCue); }
  const hit = byCue.get(cue);
  if (hit !== undefined) return hit;
  const dur = Math.max(0, cue.end - cue.start);
  const n = Math.max(1, cueTokens(cue.text).length);
  const words = [...p.scene.words].filter((w) => w.text.trim() !== "").sort((a, b) => a.start - b.start);
  const first = words.findIndex((w) => w.start >= cue.start - 0.05 && w.start < Math.max(cue.end, cue.start + 0.05));
  let wordDur: number;
  if (first >= 0) {
    const last = words[Math.min(words.length - 1, first + n - 1)];
    wordDur = Math.max(0, last.end - words[first].start);
  } else {
    wordDur = Math.max(0, dur - 0.9 - COUNTER.countPad);
  }
  const count = Math.min(Math.max(COUNTER.minCount, wordDur + COUNTER.countPad), Math.max(0.3, dur - 0.2));
  byCue.set(cue, count);
  return count;
}

/** The value shown `local` s into the count: 0 → target, easeOutExpo, exactly the target from the lock on. */
export function counterValue(target: number, local: number, count: number): number {
  if (local >= count) return target;
  return target * easeOutExpo(progress(local, 0, count));
}

const isDigit = (ch: string) => ch >= "0" && ch <= "9";

/** Glyph advances with tabular digits: every digit gets the widest digit's advance (measured once per family). */
export function tabularAdvances(p: PreparedScene, text: string, family: string, px: number): number[] {
  const digit = Math.max(...Array.from("0123456789", (d) => p.measure(d, family, REF_PX)));
  return Array.from(text, (ch) => ((isDigit(ch) ? digit : p.measure(ch, family, REF_PX)) * px) / REF_PX);
}

/** Font size: 2.2 × the caption size, shrunk so the locked number fits 88 % of the width. */
export function counterPx(p: PreparedScene, fmt: CounterFormat, family: string): number {
  const style = p.scene.caption;
  const caption = Math.max(34 * p.u, style?.size_px ?? 78 * p.u);
  let px = COUNTER.sizeOfCaption * caption;
  const full = tabularAdvances(p, fmt.format(fmt.target), family, px).reduce((s, v) => s + v, 0);
  const max = COUNTER.maxWidthFrac * p.width;
  if (full > max) px *= max / full;
  return px;
}

/** Stamp start scale: 1.4, lowered (never below 1) so a long number at its 1.4× peak still fits 96 % of the width
 *  instead of clipping at the frame edges for the stamp's first frames. */
export const stampPeak = (textWidth: number, frameWidth: number): number =>
  textWidth > 0 ? Math.max(1, Math.min(COUNTER.stampFrom, (0.96 * frameWidth) / textWidth)) : COUNTER.stampFrom;

const counterRows = new WeakMap<PreparedScene, Map<string, number>>();

/**
 * Centre y (px) of a counter: y = 0.36 H when that keeps the number off the speaker's head; otherwise just under the
 * chin when it fits above the captions, else just above the hair, else whichever of the three covers the head least.
 * Decided once per cue from the face at its start, so the number never jumps.
 */
export function counterCenterY(p: PreparedScene, cue: AccentCue, px: number): number {
  let rows = counterRows.get(p);
  if (!rows) { rows = new Map(); counterRows.set(p, rows); }
  const hit = rows.get(cue.id);
  if (hit !== undefined) return hit;
  const H = p.height, u = p.u, half = px * 0.62, gap = 24 * u;
  const face = p.faceAt(cue.start);
  const preferred = H * COUNTER.centerY;
  let y = preferred;
  if (face) {
    const head = headBox(face, u);
    const band = captionBand(p, cue.start, cue.end);
    const safe = safeRect(p.width, H, p.hookActive(cue.start));
    const bottomLimit = band ? band.y : safe.y + safe.h;
    const options = [
      preferred,
      head.y + head.h + gap + half,          // under the chin
      head.y - gap - half,                   // above the hair
    ];
    const cost = (cy: number) => {
      const box = { x: 0, y: cy - half, w: p.width, h: 2 * half };
      const outside = Math.max(0, safe.y - box.y) + Math.max(0, box.y + box.h - bottomLimit);
      return overlapArea(box, head) / p.width + outside * 4;
    };
    y = options.reduce((best, cy) => (cost(cy) < cost(best) - 1e-6 ? cy : best), preferred);
  }
  rows.set(cue.id, y);
  return y;
}

function counterOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene, u = dc.u, local = dc.local;
  const fmt = p.counters.get(cue.id) ?? counterFormat(cue);
  const family = p.family(COUNTER.font);
  const px = counterPx(p, fmt, family);
  const count = counterDuration(p, cue);
  const text = fmt.format(counterValue(fmt.target, local, count));
  const locked = local >= count - 1e-9;
  const sinceLock = local - count;

  const fade = clamp01((dc.dur - local) / COUNTER.fadeOut);
  const enterK = progress(local, 0, COUNTER.enter);
  const alpha = Math.min(1, enterK * 4) * fade;
  if (alpha <= 0) return [];
  const advances = tabularAdvances(p, text, family, px);
  const total = advances.reduce((s, v) => s + v, 0);
  const scale = locked
    ? keyframes([{ t: 0, v: stampPeak(total, p.width) }, { t: COUNTER.stampDur, v: 1, ease: easeOutQuad }], sinceLock)
    : 0.7 + 0.3 * easeOutBack(enterK);
  const rot = locked ? COUNTER.stampDeg * DEG : 0;
  const transform: Mat2D = placeAt(p.width / 2, counterCenterY(p, cue, px), rot, scale);

  const chars = Array.from(text);
  const xs: number[] = [];
  let x = -total / 2;
  for (const a of advances) { xs.push(x + a / 2); x += a; }

  const fill = locked ? accentColor(p) : "#FFFFFF";
  const glyph = (i: number) => ({ text: chars[i], font: family, px, x: xs[i], y: 0, transform, alpha });
  const ops: DrawOp[] = [];
  // The stamp's white rim: two frames, alpha 1 → 0, under the outline.
  const outline = COUNTER.strokeEm * px;
  if (locked && sinceLock < COUNTER.rimFrames * FRAME - 1e-9) {
    // The rim is a 14u stroke around the outlined glyph (outline + 14u wide, so 7u of white shows past the black
    // outline on each side); a plain 14u stroke would hide under the ~12u black outline.
    const rimAlpha = alpha * (1 - Math.floor(sinceLock / FRAME + 1e-6) / COUNTER.rimFrames);
    chars.forEach((_, i) => ops.push(textOp({ ...glyph(i), alpha: rimAlpha, fill: null, stroke: "#FFFFFF", strokePx: outline + COUNTER.rimPx * u })));
  }
  const shadow = { color: "rgba(0,0,0,0.5)", blur: 18 * u, dx: 0, dy: 6 * u * scale };
  chars.forEach((_, i) => ops.push(textOp({ ...glyph(i), fill: null, stroke: "#000000", strokePx: outline, shadow })));
  chars.forEach((_, i) => ops.push(textOp({ ...glyph(i), fill })));
  return ops;
}

registerCaptionStyle({
  template: "stat_counter",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.stat_counter),
});

registerAccent({
  variant: "counter",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: counterOps,
});
