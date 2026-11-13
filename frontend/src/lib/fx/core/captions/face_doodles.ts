// `face_doodles` (§11.1, WP8): TikTokSans-Bold karaoke captions, and the `doodle` accent — hand-drawn marks around
// the speaker's head (doodles.ts) that follow the face box as it moves and as the picture zooms or shakes (dc.face is
// the interpolated face AFTER this frame's picture transform). With no face at t the beat still lands: the cue's text
// is slapped on as a die-cut sticker instead (looks/sticker.ts via sticker_slap's placement). Doodles are hidden during
// B-roll windows (the face on screen is not the speaker's).
import type { DrawOp } from "../host";
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle, type DrawerContext } from "../registry";
import type { AccentCue, DoodleKind } from "../types";
import type { PreparedScene } from "../scene";
import { doodleOps } from "../doodles";
import { placedStickerOps, placeSticker, type StickerPlacement } from "./sticker_slap";

/** Sticker text when a doodle with no label has no face to draw on. */
export const DOODLE_FALLBACK_TEXT: Record<DoodleKind, string> = {
  sparkles: "WOW", arrows: "LOOK", sweat: "YIKES", horns: "EVIL", bang: "WHAT?!", heart: "AWW", anger: "UGH",
};

const fallbacks = new WeakMap<PreparedScene, Map<string, StickerPlacement | null>>();

/** The no-face sticker for a doodle cue (placed once per prepared scene, like sticker_slap's stickers). */
export function doodleFallback(p: PreparedScene, cue: AccentCue): StickerPlacement | null {
  let map = fallbacks.get(p);
  if (!map) { map = new Map(); fallbacks.set(p, map); }
  if (!map.has(cue.id)) {
    const text = cue.text.trim() || DOODLE_FALLBACK_TEXT[cue.doodle] || DOODLE_FALLBACK_TEXT.sparkles;
    map.set(cue.id, placeSticker(p, { ...cue, text }, [], null));
  }
  return map.get(cue.id) ?? null;
}

export function doodleAccentOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene;
  if (p.inBroll(dc.t)) return [];
  if (dc.face && dc.face.w > 0 && dc.face.h > 0) return doodleOps(cue.doodle, dc.face, dc);
  const spot = doodleFallback(p, cue);
  return spot ? placedStickerOps(spot, dc.local, dc.u) : [];
}

registerCaptionStyle({
  template: "face_doodles",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.face_doodles),
});

registerAccent({
  variant: "doodle",
  layer: "overlay",
  window: (cue) => [cue.start, cue.end],
  ops: doodleAccentOps,
});
