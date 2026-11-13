// `sticker_slap` (§11.1, WP8): TikTokSans-Bold white captions with an outline, and the `sticker` accent — the beat's
// label as a die-cut vinyl sticker (looks/sticker.ts, never a copy) slapped onto the frame: scale 1.3 → 1 in 90 ms with
// a seeded tilt within ±8°, coloured in the caption accent or one of the 4 sticker palettes. Stickers sit inside the
// safe area, off the face box, the caption band and the hook band, stack up to 3 deep without overlapping, live at most
// 1.6 s each and peel off with a quick shrink + fade.
//
// Placement is decided once per prepared scene for every sticker (earlier stickers still on screen are obstacles), so a
// sticker never jumps when another one appears. The face_doodles no-face fallback uses the same placement (`placeSticker`).
import type { DrawOp } from "../host";
import { BASE_LOOKS, baseCaptionOps, cssColor } from "./base";
import { registerAccent, registerCaptionStyle, type DrawerContext } from "../registry";
import type { AccentCue } from "../types";
import type { Rect } from "../constants";
import { hash01 } from "../rng";
import { clamp01, easeInQuad, progress } from "../easing";
import { clampBox, inflate, overlapArea, placeAvoiding, safeRect } from "../layout";
import type { PreparedScene } from "../scene";
import { slapAlpha, slapScale, stickerBox, stickerOps, stickerPalette, stickerTilt, type StickerPalette } from "../looks/sticker";

/** A sticker lives at most this long (s). */
export const STICKER_MAX_LIFE = 1.6;
/** At most this many stickers on screen at once (the oldest peels off first). */
export const STICKER_MAX_STACK = 3;
/** Peel-off at the end: shrink to 85 % and fade over this long (s). */
export const STICKER_OUT = 0.12;
/** Largest tilt (degrees). */
export const STICKER_TILT_DEG = 8;
/** Sticker scale before fitting (the look's 72u letters × 1.5 ≈ 108u: a slap you read at a glance). */
export const STICKER_SCALE = 1.5;
/** Longest line before a two-word-or-more label wraps onto two lines. */
const WRAP_AT = 13;

export interface StickerPlacement {
  id: string;
  text: string;        // the label as drawn (uppercase, wrapped)
  cx: number; cy: number;
  rot: number;         // radians, within ±8°
  scale: number;
  palette: StickerPalette;
  box: Rect;           // output-px bounds of the tilted sticker at rest (scale 1 of the slap)
  start: number; end: number;
}

/** Uppercase label, wrapped onto two lines at the space nearest the middle when it is long. */
export function stickerLabel(text: string): string {
  const clean = text.replace(/\s+/g, " ").trim().toLocaleUpperCase();
  if (Array.from(clean).length <= WRAP_AT || !clean.includes(" ")) return clean;
  const mid = clean.length / 2;
  let cut = -1;
  for (let i = 0; i < clean.length; i++) if (clean[i] === " " && (cut < 0 || Math.abs(i - mid) < Math.abs(cut - mid))) cut = i;
  return `${clean.slice(0, cut)}\n${clean.slice(cut + 1)}`;
}

/** Window a sticker draws in: [start, min(end, start + 1.6)). */
export const stickerWindow = (cue: Pick<AccentCue, "start" | "end">): [number, number] =>
  [cue.start, Math.min(cue.end, cue.start + STICKER_MAX_LIFE)];

/** The band captions occupy while [a, b) plays (union of the chunks on screen), padded; null when none show. */
export function captionBand(p: PreparedScene, a: number, b: number): Rect | null {
  let y0 = Infinity, y1 = -Infinity;
  for (const chunk of p.chunks) {
    if (chunk.end <= a || chunk.start >= b) continue;
    for (const w of chunk.words) { y0 = Math.min(y0, w.y); y1 = Math.max(y1, w.y + w.h); }
  }
  const style = p.scene.caption;
  if (y0 === Infinity) {
    if (!style || !style.enabled || !p.captionFamily) return null;
    const half = style.size_px * 1.3;
    y0 = style.y * p.height - half; y1 = style.y * p.height + half;
  }
  const pad = 24 * p.u;
  return { x: 0, y: y0 - pad, w: p.width, h: y1 - y0 + 2 * pad };
}

/** Candidate centres (fractions of W, H), best first: beside and below the head, then the upper corners. */
const CANDIDATES: readonly [number, number][] = [
  [0.5, 0.55], [0.27, 0.5], [0.73, 0.5], [0.27, 0.33], [0.73, 0.33], [0.28, 0.16], [0.72, 0.16], [0.5, 0.16], [0.5, 0.86],
  [0.2, 0.62], [0.8, 0.62],
];
/** Fallback spots: a 7 × 9 grid over the frame. */
const GRID: readonly [number, number][] = Array.from({ length: 63 }, (_, i) => [0.14 + (i % 7) * 0.12, 0.1 + Math.floor(i / 7) * 0.1] as [number, number]);

/**
 * Place one sticker: tilt and palette from the cue seed, size fitted to the safe area, centre at the first candidate
 * that clears the face (at the sticker's start), the caption band, the hook band and `taken` boxes.
 */
export function placeSticker(p: PreparedScene, cue: Pick<AccentCue, "id" | "seed" | "start" | "end" | "text">, taken: readonly Rect[],
  accent: string | null): StickerPlacement | null {
  const text = stickerLabel(cue.text);
  if (!text) return null;
  const W = p.width, H = p.height, u = p.u;
  const [start, end] = stickerWindow(cue);
  const area = safeRect(W, H, p.hookActive(start));
  const rot = stickerTilt(hash01(cue.seed >>> 0, 1), STICKER_TILT_DEG);
  const palette = accent && hash01(cue.seed >>> 0, 2) < 0.35 ? stickerPalette(cssColor(accent)) : stickerPalette(hash01(cue.seed >>> 0, 3));
  const at1 = stickerBox(text, 1, u, p.measure);
  const c = Math.abs(Math.cos(rot)), s = Math.abs(Math.sin(rot));
  const tilted = (k: number) => ({ w: (at1.w * c + at1.h * s) * k, h: (at1.w * s + at1.h * c) * k });
  const fit = Math.min(STICKER_SCALE, (area.w * 0.86) / tilted(1).w, (area.h * 0.3) / tilted(1).h);
  const hard: Rect[] = [];   // the face and the caption band: never covered when any spot clears them
  const face = p.faceAt(start);
  if (face) hard.push(inflate(face, 18 * u));
  const band = captionBand(p, start, end);
  if (band) hard.push(band);
  // Seeded variety: start the search at one of the first three preferred spots, then a fine grid.
  const first = Math.floor(hash01(cue.seed >>> 0, 4) * 3);
  const order = [...CANDIDATES.slice(first), ...CANDIDATES.slice(0, first), ...GRID].map(([x, y]) => [x * W, y * H] as [number, number]);
  // Shrink (down to 60 %) before giving up on a clear spot; otherwise the least-covered spot at full size, where
  // covering the face or captions costs four times as much as touching another sticker.
  let box: Rect | null = null, scale = fit;
  for (const k of [1, 0.85, 0.72, 0.6]) {
    const size = tilted(fit * k);
    const spot = placeAvoiding(size.w, size.h, order, [...hard, ...taken], area);
    if ([...hard, ...taken].every((r) => overlapArea(spot, r) === 0)) { box = spot; scale = fit * k; break; }
  }
  if (!box) {
    const size = tilted(fit * 0.6);
    let best = Infinity;
    for (const [x, y] of order) {
      const spot = clampBox(x, y, size.w, size.h, area);
      const cost = hard.reduce((c, r) => c + 4 * overlapArea(spot, r), 0) + taken.reduce((c, r) => c + overlapArea(spot, r), 0);
      if (cost < best) { best = cost; box = spot; }
    }
    scale = fit * 0.6;
  }
  const at = box!;
  return { id: cue.id, text, cx: at.x + at.w / 2, cy: at.y + at.h / 2, rot, scale, palette, box: at, start, end };
}

const placements = new WeakMap<PreparedScene, Map<string, StickerPlacement | null>>();

/** Every sticker accent's placement, decided in start order (earlier stickers still showing are obstacles). */
export function stickerPlacements(p: PreparedScene): Map<string, StickerPlacement | null> {
  let map = placements.get(p);
  if (map) return map;
  map = new Map();
  const stickers = (p.cuesByKind.get("accent") ?? []).filter((c): c is AccentCue => c.kind === "accent" && c.variant === "sticker");
  const placed: StickerPlacement[] = [];
  const accent = p.scene.caption?.accent ?? null;
  for (const cue of stickers) {
    const [start] = stickerWindow(cue);
    const showing = placed.filter((s) => s.start <= start && s.end > start).slice(-(STICKER_MAX_STACK - 1));
    const spot = placeSticker(p, cue, showing.map((s) => inflate(s.box, 10 * p.u)), accent);
    map.set(cue.id, spot);
    if (spot) placed.push(spot);
  }
  placements.set(p, map);
  return map;
}

/** Slap-in, rest, peel-off at local seconds into a `life`-long sticker: [scale factor, alpha]. */
export function stickerMotion(local: number, life: number): [number, number] {
  if (local < 0 || local > life) return [0, 0];
  const out = easeInQuad(progress(local, life - STICKER_OUT, life));
  const breathe = 1 + 0.012 * Math.sin(2 * Math.PI * 1.4 * Math.max(0, local - 0.09));
  return [slapScale(local) * breathe * (1 - 0.15 * out), slapAlpha(local) * clamp01(1 - out)];
}

/** Ops for a placed sticker at local seconds into its life. */
export function placedStickerOps(spot: StickerPlacement, local: number, u: number): DrawOp[] {
  const [k, alpha] = stickerMotion(local, spot.end - spot.start);
  if (k <= 0 || alpha <= 0) return [];
  return stickerOps(spot.text, spot.cx, spot.cy, spot.rot, spot.scale * k, spot.palette, u)
    .map((o) => (o.op === "text" ? { ...o, alpha: o.alpha * alpha } : o));
}

/** Is this sticker among the newest 3 on screen at t? (older ones have peeled off) */
function inStack(p: PreparedScene, cue: AccentCue, t: number): boolean {
  const showing = (p.cuesByKind.get("accent") ?? []).filter((c) => {
    if (c.kind !== "accent" || c.variant !== "sticker") return false;
    const [a, b] = stickerWindow(c);
    return t >= a && t < b;
  });
  const index = showing.findIndex((c) => c.id === cue.id);
  return index >= 0 && index >= showing.length - STICKER_MAX_STACK;
}

export function stickerAccentOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene;
  if (!inStack(p, cue, dc.t)) return [];
  const spot = stickerPlacements(p).get(cue.id);
  return spot ? placedStickerOps(spot, dc.local, dc.u) : [];
}

registerCaptionStyle({
  template: "sticker_slap",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.sticker_slap),
});

registerAccent({
  variant: "sticker",
  layer: "overlay",
  window: stickerWindow,
  ops: stickerAccentOps,
});
