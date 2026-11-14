// `highlighter_swipe` (§11.1, WP8): TikTokSans-Bold dark text (#111) on an optional light box, and the `highlight`
// accent — a rough-edged marker stroke (seeded ±3u edge jitter, skewed −3°) that swipes left → right behind a word at
// the speed it is spoken (min 120 ms, max 600 ms; a fixed 300 ms with approximate word timing) and stays until the
// caption line leaves.
//
// The `highlight` accent is also used by slam, behind_head, stat_counter and kinetic_stack (§8.7). For those (white text,
// usually no box) it is drawn on the overlay layer, under the caption, in solid accent at 85 %. For highlighter_swipe the
// line drawer paints it itself, between the light box and the dark text, with `multiply` (a real highlighter on paper).
//
// This file also exports the word helpers the other WP8 drawers share (cue words, approximate-timing detection).
import type { DrawOp, Mat2D } from "../host";
import { BASE_LOOKS, baseCaptionOps, cssColor, entranceAt, outlineGap } from "./base";
import { accentDrawerFor, registerAccent, registerCaptionStyle, type CaptionChunk, type DrawerContext } from "../registry";
import type { AccentCue, FxCaptionTemplate, FxWord } from "../types";
import { clamp, clamp01 } from "../easing";
import { IDENTITY, mul, pathOp, scaleAbout, translation } from "../layout";
import type { PreparedScene } from "../scene";
import { activeLook, lookCaptionOps } from "./looks";

/** Sweep duration bounds (s): the marker follows the spoken word, never faster than 120 ms or slower than 600 ms. */
export const SWIPE_MIN = 0.12;
export const SWIPE_MAX = 0.6;
/** With approximate word timing (caption lines spread by character count) every sweep takes 300 ms. */
export const SWIPE_APPROX = 0.3;
/** Marker colour opacity. */
export const SWIPE_ALPHA = 0.85;
/** Skew of the marker stroke (degrees; negative leans the top to the left). */
export const SWIPE_SKEW_DEG = -3;
/** Edge roughness (u). */
export const SWIPE_JITTER = 3;
/** How long after the cue ends a highlight may still be on screen (it stays until its caption line leaves). */
export const HIGHLIGHT_LINGER = 6;

// ----------------------------------------------------------------------------------------------- word helpers

const sortedWords = new WeakMap<PreparedScene, FxWord[]>();
/** The scene's words with text, in time order (memoised per prepared scene). */
export function spokenWords(p: PreparedScene): FxWord[] {
  let words = sortedWords.get(p);
  if (!words) {
    words = p.scene.words.filter((w) => w.text.trim() !== "").sort((a, b) => a.start - b.start || (a.id < b.id ? -1 : 1));
    sortedWords.set(p, words);
  }
  return words;
}

/**
 * The words an accent cue marks: the word starting nearest `cue.start` (resolver cues start exactly on their key word),
 * then as many following words as `cue.text` has tokens, all starting before `cue.end`. Empty when no word fits.
 */
export function cueWords(p: PreparedScene, cue: Pick<AccentCue, "start" | "end" | "text">): FxWord[] {
  const words = spokenWords(p);
  const tokens = Math.max(1, cue.text.trim().split(/\s+/).filter(Boolean).length);
  let best = -1, bestGap = Infinity;
  for (let i = 0; i < words.length; i++) {
    const w = words[i];
    if (w.start < cue.start - 0.06 || w.start >= Math.max(cue.end, cue.start + 0.01)) continue;
    const gap = Math.abs(w.start - cue.start);
    if (gap < bestGap) { best = i; bestGap = gap; }
  }
  if (best < 0) return [];
  const out: FxWord[] = [];
  for (let i = best; i < words.length && out.length < tokens; i++) {
    if (i > best && words[i].start >= cue.end) break;
    out.push(words[i]);
  }
  return out;
}

/**
 * Two neighbouring words look like one approximate caption line (G12, edit_fx.beats._approx_words): they abut exactly
 * and share one duration per character (to the millisecond the times are rounded to).
 */
function sameApproxLine(a: FxWord, b: FxWord): boolean {
  if (Math.abs(b.start - a.end) > 0.0015) return false;
  const la = Math.max(1, Array.from(a.text).length), lb = Math.max(1, Array.from(b.text).length);
  return Math.abs((a.end - a.start) * lb - (b.end - b.start) * la) <= 0.0011 * (la + lb);
}

const approxCache = new WeakMap<PreparedScene, boolean>();
/**
 * True when the scene's words carry approximate timing (each caption line's duration spread over its words by
 * character count): most neighbours sit on one such line. Real word timings never look like that, so this needs no
 * extra scene field.
 */
export function approxTiming(p: PreparedScene): boolean {
  const cached = approxCache.get(p);
  if (cached !== undefined) return cached;
  const words = spokenWords(p);
  let pairs = 0, linked = 0;
  for (let i = 0; i + 1 < words.length; i++) {
    pairs++;
    if (sameApproxLine(words[i], words[i + 1])) linked++;
  }
  const result = linked >= 3 && linked >= 0.6 * pairs;
  approxCache.set(p, result);
  return result;
}

/** Start of the approximate caption line that contains `word` (the run of words that share its line). */
export function runStart(p: PreparedScene, word: FxWord): number {
  const words = spokenWords(p);
  let i = words.findIndex((w) => w.id === word.id && w.run === word.run);
  if (i < 0) return word.start;
  while (i > 0 && sameApproxLine(words[i - 1], words[i])) i--;
  return words[i].start;
}

// ----------------------------------------------------------------------------------------------- the swipe

type LaidWord = CaptionChunk["words"][number];

/** Sweep length for a word: its spoken duration clamped to [120, 600] ms, or 300 ms with approximate timing. */
export const swipeDuration = (word: Pick<FxWord, "start" | "end">, approx: boolean): number =>
  approx ? SWIPE_APPROX : clamp(word.end - word.start, SWIPE_MIN, SWIPE_MAX);

/** 0 → 1 progress of the sweep across `word` at edit time t (linear: width ∝ progress). */
export const swipeProgress = (word: Pick<FxWord, "start" | "end">, t: number, approx: boolean): number =>
  clamp01((t - word.start) / swipeDuration(word, approx));

export interface SwipeStyle { color: string; alpha: number; blend: "multiply" | "source-over" }

/**
 * The marker strokes for the words of `cues` that sit in `chunk` (one stroke per caption line they cover), already
 * grown to edit time t, under the line transform `outer`. Pure: geometry from the chunk layout, roughness from the
 * cue seed (stable while it grows, so the edge never crawls).
 */
export function swipeOps(p: PreparedScene, chunk: CaptionChunk, t: number, cues: readonly AccentCue[], outer: Mat2D,
  style: SwipeStyle, lineAlpha = 1, grow = 0): DrawOp[] {
  const approx = approxTiming(p);
  const u = p.u;
  const ops: DrawOp[] = [];
  for (const cue of cues) {
    const ids = new Set(cueWords(p, cue).map((w) => `${w.run}\u0000${w.id}`));
    if (!ids.size) continue;
    const marked = chunk.words.filter((w) => ids.has(`${w.run}\u0000${w.id}`));
    const byLine = new Map<number, LaidWord[]>();
    for (const w of marked) byLine.set(w.line, [...(byLine.get(w.line) ?? []), w]);
    for (const [line, group] of byLine) {
      group.sort((a, b) => a.x - b.x);
      const stroke = swipeStroke(group, t, approx, cue.seed + line * 7919, u, chunk.px, grow);
      if (!stroke) continue;
      ops.push(pathOp({
        points: stroke.points, closed: true, smooth: false, fill: style.color, stroke: null, strokePx: 0,
        transform: mul(outer, stroke.skew), alpha: style.alpha * lineAlpha, blend: style.blend,
      }));
    }
  }
  return ops;
}

/** One rough marker stroke across consecutive words of one line, grown to the spoken position at t. */
export function swipeStroke(group: readonly LaidWord[], t: number, approx: boolean, seed: number, u: number, px: number,
  grow = 0): { points: number[]; head: number; x0: number; x1: number; skew: Mat2D } | null {
  if (!group.length) return null;
  const padX = px * 0.14 + grow;
  const x0 = group[0].x - padX;
  const x1 = group[group.length - 1].x + group[group.length - 1].w + padX;
  // Head: the latest started word decides how far the marker has travelled.
  let head = -Infinity;
  group.forEach((w, j) => {
    const k = swipeProgress(w, t, approx);
    if (t < w.start) return;
    const left = j === 0 ? x0 : group[j - 1].x + group[j - 1].w;
    const right = j === group.length - 1 ? x1 : w.x + w.w;
    head = Math.max(head, left + (right - left) * k);
  });
  if (!(head > x0 + 0.5)) return null;
  const yc = group[0].y + group[0].h / 2;
  const top = yc - group[0].h * 0.52 - grow, bottom = yc + group[0].h * 0.5 + grow;
  const amp = SWIPE_JITTER * u;
  const rough = (n: number) => (hashUnit(seed, n) - 0.5) * 2 * amp;
  const step = Math.max(10 * u, 22 * u);
  const pts: number[] = [];
  // Ragged left end, top edge left → right up to the head, ragged head, bottom edge back.
  pts.push(x0 + rough(1), bottom + rough(2), x0 - amp * 0.6 + rough(3), yc + rough(4), x0 + rough(5), top + rough(6));
  let k = 0;
  for (let x = x0 + step; x < head - step * 0.4; x += step) pts.push(x, top + rough(100 + k++));
  pts.push(head, top + rough(50), head + amp * 0.8 + rough(51), yc + rough(52), head, bottom + rough(53));
  const back: number[] = [];
  k = 0;
  for (let x = x0 + step; x < head - step * 0.4; x += step) back.push(x, bottom + rough(300 + k++));
  for (let i = back.length - 2; i >= 0; i -= 2) pts.push(back[i], back[i + 1]);
  const tan = Math.tan((SWIPE_SKEW_DEG * Math.PI) / 180);
  const skew: Mat2D = [1, 0, tan, 1, -tan * yc, 0];
  return { points: pts, head, x0, x1, skew };
}

/** Integer hash → [0, 1) for edge roughness (fixed per cue, so the stroke never crawls as it grows). */
function hashUnit(seed: number, n: number): number {
  let h = (Math.imul((seed >>> 0) ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(n + 1, 0xc2b2ae35)) >>> 0;
  h ^= h >>> 16; h = Math.imul(h, 0x7feb352d) >>> 0; h ^= h >>> 15; h = Math.imul(h, 0x846ca68b) >>> 0; h ^= h >>> 16;
  return (h >>> 0) / 4294967296;
}

/** Active highlight cues for a chunk on screen at t (a highlight stays until its line leaves). */
export function highlightCues(p: PreparedScene, t: number): AccentCue[] {
  return (p.cuesByKind.get("accent") ?? []).filter((c): c is AccentCue =>
    c.kind === "accent" && c.variant === "highlight" && t >= c.start - 1e-9 && t < c.end + HIGHLIGHT_LINGER);
}

/**
 * True while an accent that hides the captions (slam, kinetic) is drawing: frame.ts then draws no caption line, so
 * marks that live on a caption line (highlights, in-line decodes) must not draw either. Same span rule as frame.ts.
 */
export function captionsHidden(p: PreparedScene, t: number): boolean {
  for (const c of p.cuesByKind.get("accent") ?? []) {
    if (c.kind !== "accent") continue;
    const d = accentDrawerFor(c.variant);
    if (!d?.hidesCaptions) continue;
    let w: [number, number];
    try { w = d.window(c); } catch { continue; }
    if (w[1] - w[0] > 1e-9 && t >= w[0] - 1e-9 && t < w[1]) return true;
  }
  return false;
}

/** The line transform base captions use for this chunk at t (entrance scale + rise), so marks move with the line. */
export function lineTransform(template: string, chunk: CaptionChunk, t: number, u: number): { outer: Mat2D; alpha: number } {
  if (chunk.space !== undefined) return { outer: IDENTITY, alpha: 1 };   // look captions never move as a line
  const look = BASE_LOOKS[template as FxCaptionTemplate];
  if (!look) return { outer: IDENTITY, alpha: 1 };
  const ent = entranceAt(look.entrance, t - chunk.start, u);
  return {
    outer: mul(translation(0, ent.dy), ent.scale === 1 ? IDENTITY : scaleAbout(ent.scale, chunk.centerX, chunk.centerY)),
    alpha: ent.alpha,
  };
}

/** highlighter_swipe paints with `multiply` on its light box; over the bare picture a solid 85 % stroke reads better. */
export function swipeStyle(p: PreparedScene): SwipeStyle {
  const style = p.scene.caption;
  return { color: cssColor(style?.accent ?? "#FFE000"), alpha: SWIPE_ALPHA, blend: style?.background ? "multiply" : "source-over" };
}

registerCaptionStyle({
  template: "highlighter_swipe",
  line: (chunk, dc) => {
    // Intensity looks draw the keyword box inside the line (captions/looks.ts).
    if (activeLook(dc.scene)) return lookCaptionOps(chunk, dc);
    const ops = baseCaptionOps(chunk, dc, BASE_LOOKS.highlighter_swipe);
    const cues = highlightCues(dc.scene, dc.t);
    if (!cues.length || !ops.length) return ops;
    const { outer, alpha } = lineTransform("highlighter_swipe", chunk, dc.t, dc.u);
    const marks = swipeOps(dc.scene, chunk, dc.t, cues, outer, swipeStyle(dc.scene), alpha);
    // Box (rect ops) first, then the marker, then the words.
    const firstText = ops.findIndex((o) => o.op === "text");
    const at = firstText < 0 ? ops.length : firstText;
    return [...ops.slice(0, at), ...marks, ...ops.slice(at)];
  },
});

/** Highlight accents for every other template: under the caption, moving with the line's entrance. */
export function highlightAccentOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene;
  if (p.scene.template === "highlighter_swipe") return [];   // the line drawer paints it between box and text
  if (activeLook(p)) return [];                              // look captions mark their keyword in the line
  if (captionsHidden(p, dc.t)) return [];   // no caption line on screen to mark
  const chunk = p.chunkAt(dc.t);
  if (!chunk) return [];
  const { outer, alpha } = lineTransform(p.scene.template, chunk, dc.t, dc.u);
  // Outlined white captions: the marker must reach past the outline, or the outline hides it and only slivers of
  // colour show at the edges. It becomes a solid block the word sits on.
  const style = p.scene.caption;
  const look = BASE_LOOKS[p.scene.template as FxCaptionTemplate];
  const grow = (style && look ? outlineGap(style, look, chunk.px) : 0) + chunk.px * 0.08;
  return swipeOps(p, chunk, dc.t, [cue], outer, swipeStyle(p), alpha, grow);
}

registerAccent({
  variant: "highlight",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end + HIGHLIGHT_LINGER],
  ops: highlightAccentOps,
});
