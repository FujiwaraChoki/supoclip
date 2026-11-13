// `slam` (WP7, §11.1): heavy caps captions with a pop entrance, and the slam accent: the keyword alone, huge, sized to
// span 60 % of the frame width (and never taller than 35 % of the height), centred at y = 0.42. It hits the screen
// at 1.6× and lands through 0.95 to 1.0 (keyframes 0 / 110 / 180 ms, easeOutQuad) with a seeded ±3° tilt: white fill,
// black outline 0.1 em, hard drop shadow 8u down. It holds until the cue ends and fades over the last 120 ms. Captions
// are hidden while it shows; flash, shake and glitch come as their own cues.
import type { DrawOp } from "../host";
import type { DrawerContext } from "../registry";
import type { AccentCue } from "../types";
import { clamp01, easeOutQuad, keyframes } from "../easing";
import { placeAt, textOp } from "../layout";
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";
import { widthAt } from "./anchor";

const DEG = Math.PI / 180;

/** §11.1 slam constants. */
export const SLAM = {
  font: "THEBOLDFONT",
  widthFrac: 0.6,
  maxHeightFrac: 0.35,
  centerY: 0.42,
  strokeEm: 0.1,
  shadowPx: 8,
  shadowColor: "rgba(0,0,0,0.6)",
  tiltDeg: 3,
  fadeOut: 0.12,
  minPx: 34,
} as const;

/** Scale keyframes: 1.6 at 0 ms → 0.95 at 110 ms → 1.0 at 180 ms (easeOutQuad). */
export const slamScale = (local: number): number =>
  keyframes([{ t: 0, v: 1.6 }, { t: 0.11, v: 0.95, ease: easeOutQuad }, { t: 0.18, v: 1, ease: easeOutQuad }], local);

/** The keyword as drawn: upper case, whitespace collapsed. */
export const slamText = (text: string): string => text.trim().replace(/\s+/g, " ").toLocaleUpperCase();

/** Font size (px) for the keyword: 60 % of the width at rest, capped at 35 % of the height. */
export function slamPx(width: number, height: number, u: number, textWidthAtPx: (px: number) => number): number {
  const ref = 100, w100 = textWidthAtPx(ref);
  const byWidth = w100 > 0 ? (SLAM.widthFrac * width * ref) / w100 : SLAM.maxHeightFrac * height;
  return Math.max(SLAM.minPx * u, Math.min(byWidth, SLAM.maxHeightFrac * height));
}

function slamOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene, u = dc.u;
  const text = slamText(cue.text);
  if (!text) return [];
  const family = p.family(SLAM.font);
  const px = slamPx(p.width, p.height, u, (size) => widthAt(p, text, family, size));
  const scale = slamScale(dc.local);
  const rot = (dc.hash(1) * 2 - 1) * SLAM.tiltDeg * DEG;
  const alpha = clamp01((dc.dur - dc.local) / SLAM.fadeOut);
  if (alpha <= 0) return [];
  const transform = placeAt(p.width / 2, p.height * SLAM.centerY, rot, scale);
  const at = { text, font: family, px, x: 0, y: 0, transform, alpha };
  return [
    // Shadow offsets are not transformed by the canvas: scale them with the slam so the drop stays proportional.
    textOp({ ...at, fill: null, stroke: "#000000", strokePx: SLAM.strokeEm * px,
      shadow: { color: SLAM.shadowColor, blur: 0, dx: 0, dy: SLAM.shadowPx * u * scale } }),
    textOp({ ...at, fill: "#FFFFFF" }),
  ];
}

registerCaptionStyle({
  template: "slam",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.slam),
});

registerAccent({
  variant: "slam",
  layer: "overlay",
  hidesCaptions: true,
  window: (cue) => [cue.start, cue.end],
  ops: slamOps,
});
