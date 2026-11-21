// Bleep cover (§11.2): hides the mouth while a swear is bleeped. The mouth comes from dc.face landmarks, which frame.ts
// has already carried through this frame's picture transform (zoom × shake × transition), so the cover rides the
// mouth through punch-ins and shakes (AR-M6). Landmarks (YuNet order): [rex,rey, lex,ley, nx,ny, rmx,rmy, lmx,lmy].
//   bar      a black rounded bar along the mouth line: the corners extended by 35 % of the mouth width each side,
//            0.45 × the mouth width tall, rotated with the head tilt;
//   pixelate the bar's bounding box, mosaic blocks of 24u px.
// Nothing draws when there are no landmarks at t (no face, or a face without landmarks).
import type { DrawOp } from "../host";
import { placeAt, rectOp, round3 } from "../layout";
import { registerCue, type CueDrawer, type DrawerContext } from "../registry";

export const MOUTH_EXTEND = 0.35;
export const BAR_HEIGHT = 0.45;
export const PIXELATE_BLOCK = 24;

export interface MouthBar { cx: number; cy: number; w: number; h: number; angle: number }

/** The cover rect for a (transformed) face, or null without landmarks. */
export function mouthBar(face: DrawerContext["face"]): MouthBar | null {
  const lm = face?.landmarks;
  if (!lm || lm.length < 10 || lm.slice(6, 10).some((v) => !Number.isFinite(v))) return null;
  let [ax, ay, bx, by] = [lm[6], lm[7], lm[8], lm[9]];
  if (bx < ax) [ax, ay, bx, by] = [bx, by, ax, ay];   // left → right in the image, so the bar is never upside down
  const mw = Math.hypot(bx - ax, by - ay);
  if (mw < 1) return null;
  return { cx: (ax + bx) / 2, cy: (ay + by) / 2, w: mw * (1 + 2 * MOUTH_EXTEND), h: mw * BAR_HEIGHT, angle: Math.atan2(by - ay, bx - ax) };
}

/** Axis-aligned bounds of a mouth bar (the pixelate region). */
export function mouthBounds(bar: MouthBar): { x: number; y: number; w: number; h: number } {
  const c = Math.abs(Math.cos(bar.angle)), s = Math.abs(Math.sin(bar.angle));
  const w = bar.w * c + bar.h * s, h = bar.w * s + bar.h * c;
  return { x: bar.cx - w / 2, y: bar.cy - h / 2, w, h };
}

registerCue({
  kind: "bleep",
  layer: "cover",
  window: (cue) => [cue.start, cue.end],
  ops: (cue, dc): DrawOp[] => {
    if (cue.cover === "none") return [];
    const bar = mouthBar(dc.face);
    if (!bar) return [];
    if (cue.cover === "pixelate") {
      const b = mouthBounds(bar);
      return [{ op: "pixelate", x: round3(b.x), y: round3(b.y), w: round3(b.w), h: round3(b.h), block: round3(PIXELATE_BLOCK * dc.u) }];
    }
    return [rectOp({
      x: round3(-bar.w / 2), y: round3(-bar.h / 2), w: round3(bar.w), h: round3(bar.h), radius: round3(bar.h * 0.28),
      fill: "#000000", transform: placeAt(bar.cx, bar.cy, bar.angle),
    })];
  },
} satisfies CueDrawer<"bleep"> as unknown as CueDrawer);
