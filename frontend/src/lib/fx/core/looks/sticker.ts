// The die-cut sticker look (AP-H4), shared by the face_doodles no-face fallback, the
// "WAIT FOR IT…" retention sticker, sticker `text` cues and the freeze label. One implementation: never copy it.
//
// Anatomy, bottom → top: a hard drop shadow (0 6u 0 rgba(0,0,0,.35)) cast by a thick white die-cut border (0.22 em
// outside the glyphs, round join, filled white so counters close up like cut vinyl), a thin dark keyline that separates
// the fill from the border, then the coloured letters. Authored around the sticker's centre; placement, seeded
// tilt and slap-in scale all arrive through one transform so every layer moves together.
import type { DrawOp } from "../host";
import { easeOutQuad, keyframes } from "../easing";
import { fxFamily, placeAt, textOp } from "../layout";

export interface StickerPalette { fill: string; keyline: string; edge: string }

/** The four seeded sticker colours: hot red, sun yellow, electric cyan, lime. White die-cut edge on all. */
export const STICKER_PALETTES: readonly StickerPalette[] = [
  { fill: "#FF3B5C", keyline: "#2A0710", edge: "#FFFFFF" },
  { fill: "#FFD23F", keyline: "#2B2100", edge: "#FFFFFF" },
  { fill: "#2EC5FF", keyline: "#03202E", edge: "#FFFFFF" },
  { fill: "#7CF25B", keyline: "#0D2A06", edge: "#FFFFFF" },
];

/** Bundled stem the sticker look sets its type in. */
export const STICKER_FONT = "Poppins-ExtraBold";
/** Letter size at scale 1, in u. */
export const STICKER_PX = 72;
/** Die-cut border: the visible white edge outside the glyphs, in em (thick enough that the word reads as one
 *  cut-out shape; letters' borders merge). */
export const STICKER_EDGE_EM = 0.22;
const KEYLINE_EM = 0.04;
const LINE_GAP = 1.12;
const TRACKING_EM = 0.02;

/** A palette from a seeded draw in [0, 1); or a custom fill colour (accent) with the standard edge and keyline. */
export function stickerPalette(pick: number | string): StickerPalette {
  if (typeof pick === "string") return { fill: pick, keyline: "#16161A", edge: "#FFFFFF" };
  const i = Math.min(STICKER_PALETTES.length - 1, Math.max(0, Math.floor(pick * STICKER_PALETTES.length)));
  return STICKER_PALETTES[i];
}

/** Seeded tilt in radians within ±maxDeg (placed stickers use ±8°) from a draw in [0, 1). */
export const stickerTilt = (draw: number, maxDeg = 8): number => ((draw * 2 - 1) * maxDeg * Math.PI) / 180;

/** Slap-in: scale 1.3 → 1 over 90 ms (easeOutQuad) after `local` seconds; 1 afterwards. */
export const slapScale = (local: number, from = 1.3, dur = 0.09): number =>
  keyframes([{ t: 0, v: from }, { t: dur, v: 1, ease: easeOutQuad }], local);
/** Slap-in opacity: on within the first 2 frames so the slap reads as a hit, not a fade. */
export const slapAlpha = (local: number): number => (local < 0 ? 0 : Math.min(1, 0.35 + local / 0.05));

/**
 * Size of a sticker (incl. its border and shadow) for placement: max line width + border, lines × gap.
 * `measure` is the prepared scene's measure (family, px), so the box matches what is drawn.
 */
export function stickerBox(text: string, scale: number, u: number, measure: (text: string, family: string, px: number) => number): { w: number; h: number } {
  const px = STICKER_PX * u;
  const lines = text.split("\n");
  const family = fxFamily(STICKER_FONT);
  const widest = Math.max(...lines.map((l) => measure(l, family, px) + Math.max(0, l.length - 1) * TRACKING_EM * px));
  const edge = 2 * STICKER_EDGE_EM * px;
  return { w: (widest + edge) * scale, h: (lines.length * px * LINE_GAP + edge + 8 * u) * scale };
}

/**
 * The sticker's draw ops, centred on (x, y) in output px, rotated by `rot` radians and scaled by `scale`.
 * `palette` is a StickerPalette or a fill colour. `u` = min(W, H) / 1080. Multi-line text splits on "\n".
 */
export function stickerOps(text: string, x: number, y: number, rot: number, scale: number,
  palette: StickerPalette | string, u: number): DrawOp[] {
  const pal = typeof palette === "string" ? stickerPalette(palette) : palette;
  const lines = text.split("\n").filter((l, i, all) => l.trim() !== "" || all.length === 1);
  if (!text.trim() || scale <= 0) return [];
  const px = STICKER_PX * u;
  const font = fxFamily(STICKER_FONT);
  const transform = placeAt(x, y, rot, scale);
  const letterSpacing = TRACKING_EM * px;
  const ops: DrawOp[] = [];
  const at = (i: number) => ({ text: lines[i], font, px, x: 0, y: (i - (lines.length - 1) / 2) * px * LINE_GAP, letterSpacing, transform });
  // Shadow offsets are not transformed by the canvas, so scale them with the sticker.
  const shadow = { color: "rgba(0,0,0,0.35)", blur: 0, dx: 0, dy: 6 * u * scale };
  lines.forEach((_, i) => ops.push(textOp({ ...at(i), fill: pal.edge, stroke: pal.edge, strokePx: STICKER_EDGE_EM * px, shadow })));
  lines.forEach((_, i) => ops.push(textOp({ ...at(i), fill: null, stroke: pal.keyline, strokePx: KEYLINE_EM * px })));
  lines.forEach((_, i) => ops.push(textOp({ ...at(i), fill: pal.fill })));
  return ops;
}
