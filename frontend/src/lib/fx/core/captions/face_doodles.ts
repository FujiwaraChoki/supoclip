// `face_doodles` (§11.1, WP8): TikTokSans-Bold karaoke captions, and the `doodle` accent — hand-drawn marks around
// the speaker's head (doodles.ts) that follow the face box as it moves and as the picture zooms or shakes (dc.face is
// the interpolated face AFTER this frame's picture transform). With no face at t the beat still lands: the cue's text
// is slapped on as a die-cut sticker instead (looks/sticker.ts, placed by sticker_place.ts). Doodles are hidden during
// B-roll windows (the face on screen is not the speaker's). When the face drops out mid-cue the doodle crossfades
// into the sticker over 250 ms (the sticker slaps in at the drop), and back again when the face returns.
import type { DrawOp } from "../host";
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle, type DrawerContext } from "../registry";
import type { AccentCue, DoodleKind } from "../types";
import type { PreparedScene } from "../scene";
import { doodleOps } from "../doodles";
import { IDENTITY, pictureMatrix, transformOps, transformRect } from "../layout";
import { placedStickerOps, placeSticker, type StickerPlacement } from "./sticker_place";

/** Sticker text when a doodle with no label has no face to draw on. */
export const DOODLE_FALLBACK_TEXT: Record<DoodleKind, string> = {
  sparkles: "WOW", arrows: "LOOK", sweat: "YIKES", horns: "EVIL", bang: "WHAT?!", heart: "AWW", anger: "UGH",
};

const fallbacks = new WeakMap<PreparedScene, Map<string, StickerPlacement | null>>();

/** The no-face sticker for a doodle cue (placed once per prepared scene). */
export function doodleFallback(p: PreparedScene, cue: AccentCue): StickerPlacement | null {
  let map = fallbacks.get(p);
  if (!map) { map = new Map(); fallbacks.set(p, map); }
  if (!map.has(cue.id)) {
    const text = cue.text.trim() || DOODLE_FALLBACK_TEXT[cue.doodle] || DOODLE_FALLBACK_TEXT.sparkles;
    map.set(cue.id, placeSticker(p, { ...cue, text }, [], null));
  }
  return map.get(cue.id) ?? null;
}

/** Crossfade between the doodle and its no-face sticker when the face drops out (or comes back) mid-cue (s). */
export const DOODLE_SWAP = 0.25;

const hasFace = (p: PreparedScene, t: number): boolean => {
  const f = p.faceAt(t);
  return !!f && f.w > 0 && f.h > 0;
};

/** The last moment in (t − DOODLE_SWAP, t] (and not before `from`) where face presence differed from now, at the
 *  30 fps grid: the time of the swap and how far into the crossfade t is; null when nothing changed recently. */
export function faceSwapAt(p: PreparedScene, t: number, from: number): { at: number; k: number } | null {
  const now = hasFace(p, t);
  for (let i = 1; i * (1 / 30) <= DOODLE_SWAP + 1e-9; i++) {
    const tb = t - i / 30;
    if (tb < from - 1e-9) return null;
    if (hasFace(p, tb) !== now) {
      const at = tb + 1 / 30;
      return { at, k: Math.min(1, (t - at) / DOODLE_SWAP) };
    }
  }
  return null;
}

/** The face box at t through this frame's picture transform (like dc.face), for a face that has just left. */
function faceThroughPicture(p: PreparedScene, t: number, dc: DrawerContext): DrawerContext["face"] {
  const f = p.faceAt(t);
  if (!f) return null;
  const box = transformRect(pictureMatrix(dc.picture, p.width, p.height), f);
  return { ...box, landmarks: null };
}

export function doodleAccentOps(cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene;
  if (p.inBroll(dc.t)) return [];
  const face = dc.face && dc.face.w > 0 && dc.face.h > 0 ? dc.face : null;
  const swap = faceSwapAt(p, dc.t, cue.start);
  const spot = doodleFallback(p, cue);
  // The sticker slaps in at the moment the face is lost (its own life runs from there to the cue's end).
  const stickerFrom = (start: number) => (spot ? placedStickerOps({ ...spot, start, end: Math.max(spot.end, start) }, dc.t - start, dc.u) : []);
  if (face) {
    const doodle = doodleOps(cue.doodle, face, dc);
    if (!swap || !spot) return doodle;
    // Face came back: the sticker fades out over the doodle as it draws in again.
    const lostAt = lastSwapBefore(p, swap.at, cue.start);
    return [...transformOps(stickerFrom(lostAt), IDENTITY, 1 - swap.k), ...doodle];
  }
  if (!swap) {
    const lostAt = lastSwapBefore(p, dc.t, cue.start);
    return lostAt > cue.start ? stickerFrom(lostAt) : spot ? placedStickerOps(spot, dc.local, dc.u) : [];
  }
  // Face just dropped: the doodle (on the face's last box) fades out while the sticker slaps in.
  const last = faceThroughPicture(p, swap.at - 1 / 30, dc);
  const fading = last ? transformOps(doodleOps(cue.doodle, last, dc), IDENTITY, 1 - swap.k) : [];
  return [...fading, ...stickerFrom(swap.at)];
}

/** When the face was last lost before t within the cue (the cue start when it was never seen in the cue). */
function lastSwapBefore(p: PreparedScene, t: number, start: number): number {
  for (let tb = t - 1 / 30; tb >= start - 1e-9; tb -= 1 / 30) if (hasFace(p, tb)) return tb + 1 / 30;
  return start;
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
