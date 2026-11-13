// `behind_head` (WP7, §11.1): clean white captions with a soft shadow, and the behind accent: one or two words in huge
// caps filling 92 % of the width (at most 450u and 25 % of the height tall) set behind the speaker. Its top edge sits
// 0.2 face-heights above the top of the head (never above 0.08 H; at y = 0.18 H with no face) so the head overlaps the
// lower part of the letters, and it follows the moving face. It rises 40u while fading in over 200 ms. The person is
// then redrawn over the text with a `picture` op with `cutout: true`. When the matte window reports no person (or the
// scene has no matte at t) the words are drawn in front instead, at 0.85 opacity with a shadow.
import type { DrawOp } from "../host";
import type { DrawerContext } from "../registry";
import type { AccentCue } from "../types";
import { clamp01, easeOutCubic, progress } from "../easing";
import { cutoutOp, textOp } from "../layout";
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";
import { widthAt } from "./anchor";

/** §11.1 behind constants (px in u). */
export const BEHIND = {
  font: "TikTokSans-Bold",
  widthFrac: 0.92,
  maxPx: 450,
  maxHeightFrac: 0.25,
  faceLift: 0.2,
  minTopFrac: 0.08,
  noFaceTopFrac: 0.18,
  rise: 40,
  fadeIn: 0.2,
  fadeOut: 0.15,
  frontAlpha: 0.85,
  minPx: 34,
} as const;

/** The words as drawn: upper case, at most two words (the resolver sends 1–2; user text is trimmed to fit). */
export function behindText(text: string): string {
  return text.trim().split(/\s+/).filter(Boolean).slice(0, 2).join(" ").toLocaleUpperCase();
}

/** Font size: 92 % of the width, capped at 450u and 25 % of the height. */
export function behindPx(width: number, height: number, u: number, textWidthAtPx: (px: number) => number): number {
  const ref = 100, w100 = textWidthAtPx(ref);
  const byWidth = w100 > 0 ? (BEHIND.widthFrac * width * ref) / w100 : BEHIND.maxPx * u;
  return Math.max(BEHIND.minPx * u, Math.min(byWidth, BEHIND.maxPx * u, BEHIND.maxHeightFrac * height));
}

/** Top edge of the text block (output px) for a face box (already carried through the picture transform). */
export function behindTop(height: number, face: DrawerContext["face"]): number {
  if (!face) return BEHIND.noFaceTopFrac * height;
  return Math.max(BEHIND.minTopFrac * height, face.y - BEHIND.faceLift * face.h);
}

function behindOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene, u = dc.u, W = p.width, H = p.height;
  const text = behindText(cue.text);
  if (!text) return [];
  const family = p.family(p.scene.caption?.font || BEHIND.font);
  const px = behindPx(W, H, u, (size) => widthAt(p, text, family, size));
  const k = easeOutCubic(progress(dc.local, 0, BEHIND.fadeIn));
  const alpha = k * clamp01((dc.dur - dc.local) / BEHIND.fadeOut);
  if (alpha <= 0) return [];
  const top = behindTop(H, dc.face) + (1 - k) * BEHIND.rise * u;
  const at = { text, font: family, px, x: W / 2, y: top + px / 2 };
  const matte = p.matteAt(dc.t);
  if (matte?.person) {
    // Behind the person: a soft shadow lifts the letters off the background; the cutout covers them with the person.
    return [
      textOp({ ...at, fill: "#FFFFFF", alpha, shadow: { color: "rgba(0,0,0,0.35)", blur: 28 * u, dx: 0, dy: 8 * u } }),
      cutoutOp(dc.picture, W, H),
    ];
  }
  return [textOp({ ...at, fill: "#FFFFFF", alpha: alpha * BEHIND.frontAlpha,
    shadow: { color: "rgba(0,0,0,0.5)", blur: 24 * u, dx: 0, dy: 6 * u } })];
}

registerCaptionStyle({
  template: "behind_head",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.behind_head),
});

registerAccent({
  variant: "behind",
  layer: "behind",
  window: (cue) => [cue.start, cue.end],
  ops: behindOps,
});
