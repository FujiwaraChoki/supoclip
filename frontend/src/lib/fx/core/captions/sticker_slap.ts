// `sticker_slap` (§11.1, WP8): TikTokSans-Bold white captions with an outline, and the `sticker` accent — the beat's
// label as a die-cut vinyl sticker (looks/sticker.ts, never a copy) slapped onto the frame: scale 1.3 → 1 in 90 ms with
// a seeded tilt within ±8°, coloured in the caption accent or one of the 4 sticker palettes. Stickers sit inside the
// safe area, off the face box, the caption band and the hook band, stack up to 3 deep without overlapping, live at most
// 1.6 s each and peel off with a quick shrink + fade; a 4th sticker makes the oldest peel away (lift at a corner,
// curl and fly off) instead of vanishing.
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
import { clampBox, inflate, overlapArea, placeAvoiding, safeRect, transformOps } from "../layout";
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

/** The head a sticker must not cover: the detector's face box (brow to chin) grown by the hair above it (≈ 0.45 of
 *  the box height) and a margin around, so stickers never sit on the top of the head. */
export function headBox(face: Rect, u: number): Rect {
  const m = 18 * u, hair = 0.45 * face.h;
  return { x: face.x - m - 0.08 * face.w, y: face.y - hair - m, w: face.w * 1.16 + 2 * m, h: face.h + hair + 2 * m };
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
  if (face) hard.push(headBox(face, u));
  const band = captionBand(p, start, end);
  if (band) hard.push(band);
  // Seeded variety: start the search at one of the first three preferred spots, then a fine grid.
  const first = Math.floor(hash01(cue.seed >>> 0, 4) * 3);
  const order = [...CANDIDATES.slice(first), ...CANDIDATES.slice(0, first), ...GRID].map(([x, y]) => [x * W, y * H] as [number, number]);
  // Shrink (down to 60 %) before giving up on a clear spot; otherwise the least-covered spot at full size, where
  // covering the head or captions costs four times as much as touching another sticker, the face itself far more.
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
      // The face itself (eyes, mouth) costs most, then captions and the hair, then other stickers.
      const cost = hard.reduce((c, r) => c + 4 * overlapArea(spot, r), 0) + taken.reduce((c, r) => c + overlapArea(spot, r), 0)
        + (face ? 12 * overlapArea(spot, inflate(face, 18 * u)) : 0);
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

/** A sticker pushed out of the stack by a 4th one peels off over this long (s). */
export const STICKER_PEEL = 0.22;

/**
 * Peel-off of an evicted sticker, `k` = 0 → 1 through STICKER_PEEL: its higher side lifts while it stays hinged at
 * the lower top corner, curls up (squashed toward the hinge, turning further) and flies off up and out while fading.
 * Ops are the placed sticker's at rest, carried by one transform about the hinge.
 */
export function peelOps(spot: StickerPlacement, k: number, u: number): DrawOp[] {
  if (k >= 1) return [];
  const e = easeInQuad(clamp01(k));
  const dir = spot.rot >= 0 ? 1 : -1;                 // tilted clockwise: the left corner sits higher and lifts
  const ax = spot.cx + dir * spot.box.w * 0.42, ay = spot.cy - spot.box.h * 0.4;   // hinge: the lower top corner
  const turn = dir * (0.35 * e);                      // +20° curl
  const squash = 1 - 0.45 * e;                         // the far edge folds over the hinge
  const lift = -90 * u * e * e, drift = dir * 50 * u * e;
  const c = Math.cos(turn), s = Math.sin(turn);
  // translate(ax+drift, ay+lift) · rotate(turn) · scale(1, squash) · translate(-ax, -ay)
  const m: [number, number, number, number, number, number] = [c, s, -s * squash, c * squash, 0, 0];
  m[4] = ax + drift - (m[0] * ax + m[2] * ay);
  m[5] = ay + lift - (m[1] * ax + m[3] * ay);
  const alpha = clamp01(1 - e * 1.1);
  if (alpha <= 0) return [];
  return transformOps(placedStickerOps(spot, Math.min(0.5, spot.end - spot.start - STICKER_OUT), u), m, alpha);
}

const evictions = new WeakMap<PreparedScene, Map<string, number>>();

/**
 * When each sticker is pushed out of the stack: the start of the newer sticker that makes it the 4th on screen
 * (Infinity when it never is). Once out it stays out, even if a newer one ends first.
 */
export function stickerEvictions(p: PreparedScene): Map<string, number> {
  let map = evictions.get(p);
  if (map) return map;
  map = new Map();
  const stickers = (p.cuesByKind.get("accent") ?? []).filter((c): c is AccentCue => c.kind === "accent" && c.variant === "sticker");
  stickers.forEach((cue, i) => {
    const [, end] = stickerWindow(cue);
    let out = Infinity;
    const newer = stickers.slice(i + 1).map(stickerWindow).filter(([a]) => a < end);
    for (const [a] of newer) {
      const showing = newer.filter(([s, e]) => s <= a && e > a).length;
      if (showing >= STICKER_MAX_STACK) { out = a; break; }
    }
    map!.set(cue.id, out);
  });
  evictions.set(p, map);
  return map;
}

export function stickerAccentOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene;
  const spot = stickerPlacements(p).get(cue.id);
  if (!spot) return [];
  const out = stickerEvictions(p).get(cue.id) ?? Infinity;
  if (dc.t >= out) return peelOps(spot, (dc.t - out) / STICKER_PEEL, dc.u);
  return placedStickerOps(spot, dc.local, dc.u);
}

registerCaptionStyle({
  template: "sticker_slap",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.sticker_slap),
});

registerAccent({
  variant: "sticker",
  layer: "overlay",
  // A sticker evicted just before its own end still finishes its peel.
  window: (cue) => { const [a, b] = stickerWindow(cue); return [a, b + STICKER_PEEL]; },
  ops: stickerAccentOps,
});
