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
import { activeLook, lookCaptionOps } from "./looks";

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

/** Look behind word (spec §3.4, the Modern Wisdom reference): one heavy lowercase word about 0.60 W wide (≤ 0.16 H
 *  tall; 0.70 W, the top of the reference range, so the letters clear the chin on both sides), its block centred on
 *  the chin/neck (face top + 1.05 face heights), hard cut in and out (0 frames) on the
 *  reframes the resolver puts at both ends. */
export const LOOK_BEHIND = {
  font: "ArchivoBlack-Regular", widthFrac: 0.7, maxHeightFrac: 0.16, neck: 1.05, noFaceY: 0.45,
  // Close-ups (face ≥ 12 % of the height): the block's bottom edge sits 0.15 face heights above the top of the face
  // box (the forehead), in the hair, so the head covers only the foot of its middle letters; its top never above
  // 0.03 H.
  closeUpFaceH: 0.12, headOverlap: -0.15, minTopFrac: 0.03,
} as const;

export function lookBehindText(text: string): string {
  const word = text.trim().split(/\s+/).filter(Boolean)[0] ?? "";
  return word.replace(/^[^\p{L}\p{N}$€£¥#]+|[^\p{L}\p{N}%]+$/gu, "").toLocaleLowerCase();
}

function lookBehindOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene, u = dc.u, W = p.width, H = p.height;
  const text = lookBehindText(cue.text);
  if (!text) return [];
  const family = p.family(LOOK_BEHIND.font);
  const w100 = widthAt(p, text, family, 100);
  const px = Math.min(w100 > 0 ? (LOOK_BEHIND.widthFrac * W * 100) / w100 : LOOK_BEHIND.maxHeightFrac * H,
    LOOK_BEHIND.maxHeightFrac * H);
  // A close-up has torso, not background, below the chin: the word sits across the top of the head instead, the
  // head covering only the lower part of its middle letters. On a small (wide-shot) face it is centred on the
  // chin/neck, its top at least at the mouth line, so the neck, not the whole head, crosses it.
  const closeUp = dc.face !== undefined && dc.face !== null && dc.face.h >= LOOK_BEHIND.closeUpFaceH * H;
  const cy = !dc.face
    ? LOOK_BEHIND.noFaceY * H
    : closeUp
      ? Math.max(LOOK_BEHIND.minTopFrac * H + 0.5 * px, dc.face.y + LOOK_BEHIND.headOverlap * dc.face.h - 0.5 * px)
      : Math.max(dc.face.y + LOOK_BEHIND.neck * dc.face.h, dc.face.y + 0.75 * dc.face.h + 0.5 * px);
  const at = { text, font: family, px, x: W / 2, y: Math.min(cy, H - px) };
  const matte = p.matteAt(dc.t);
  if (matte?.person) {
    return [
      textOp({ ...at, fill: "#FFFFFF", shadow: { color: "rgba(0,0,0,0.3)", blur: 0.1 * px, dx: 0, dy: 0.03 * px } }),
      cutoutOp(dc.picture, W, H),
    ];
  }
  return [textOp({ ...at, fill: "#FFFFFF", alpha: BEHIND.frontAlpha, shadow: { color: "rgba(0,0,0,0.45)", blur: 24 * u, dx: 0, dy: 6 * u } })];
}

function behindOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  if (activeLook(dc.scene)) return lookBehindOps(cue, dc);
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
  line: (chunk, dc) => (activeLook(dc.scene) ? lookCaptionOps(chunk, dc) : baseCaptionOps(chunk, dc, BASE_LOOKS.behind_head)),
});

registerAccent({
  variant: "behind",
  layer: "behind",
  window: (cue) => [cue.start, cue.end],
  ops: behindOps,
  // The behind word IS the caption while it shows (the resolver keeps other accents out of its span).
  hidesCaptions: true,
});
