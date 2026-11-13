// Freeze label (§11.2), on the F clock. A freeze is an E instant whose frame the warp holds for `hold` final seconds;
// the frame's E window covers the hold plus the frame that plays on after it, so this drawer narrows itself to the
// hold with clock.o (o0 ≤ o < o0 + hold, o0 = the hold piece's start). During the hold: a white rim flash on the first
// 2 frames (a camera-shutter hit), and the beat label slapped on in the sticker look. Nothing draws when the render's
// warp has no hold at this instant (safe mode, FX_WARP=off, an identity retry).
import type { DrawOp } from "../host";
import { rectOp, round3 } from "../layout";
import { registerCue, type CueDrawer } from "../registry";
import type { PreparedScene } from "../scene";
import { slapAlpha, slapScale, stickerOps, stickerPalette, stickerTilt } from "../looks/sticker";
import { stickerPlacement } from "../overlays/sticker";
import { FPS, type CueOf } from "../types";
import { quantize } from "../warp";

export const RIM_PX = 16;                       // u px
export const RIM_ALPHA: readonly number[] = [1, 0.5];   // per hold frame (2 frames)
export const SHUTTER_ALPHA: readonly number[] = [0.32, 0.12];
/** Resting size of the label (× the sticker look's 72u letters): the hold is the moment, the label reads big. */
export const FREEZE_LABEL_SCALE = 1.2;

/** Where the label may sit (fractions of the frame, tried in order): centred above the face first, then below it,
 *  then the sides, so a close-up that fills the middle still gets a label off the face. */
export const FREEZE_LABEL_SPOTS: readonly [number, number][] = [
  [0.5, 0.3], [0.5, 0.22], [0.5, 0.5], [0.5, 0.6], [0.5, 0.14], [0.27, 0.3], [0.73, 0.3], [0.27, 0.55], [0.73, 0.55],
];
/** The hold piece the render applies at this freeze instant (o0 and hold in F seconds), or null. */
export function freezeHold(p: Pick<PreparedScene, "warp">, cue: Pick<CueOf<"freeze">, "start">): { o0: number; hold: number } | null {
  const e = quantize(cue.start);
  const piece = p.warp.pieces.find((x) => x.rate === 0 && x.hold > 0 && Math.abs(x.e0 - e) <= 1e-6);
  return piece ? { o0: piece.o0, hold: piece.hold } : null;
}

registerCue({
  kind: "freeze",
  layer: "retention",
  window: (cue) => [cue.start, cue.start],
  ops: (cue, dc): DrawOp[] => {
    const p = dc.scene, W = p.width, H = p.height, u = dc.u;
    const hold = freezeHold(p, cue);
    if (!hold) return [];
    const local = dc.clock.o - hold.o0;
    if (local < -1e-6 || local >= hold.hold - 1e-6) return [];
    const ops: DrawOp[] = [];
    const frame = Math.round(Math.max(0, local) * FPS);
    if (frame < RIM_ALPHA.length) {
      const rim = RIM_PX * u;
      ops.push(rectOp({ x: 0, y: 0, w: W, h: H, fill: "#FFFFFF", alpha: SHUTTER_ALPHA[frame] }));
      ops.push(rectOp({
        x: round3(rim / 2), y: round3(rim / 2), w: round3(W - rim), h: round3(H - rim), fill: null, stroke: "#FFFFFF",
        strokePx: round3(rim), alpha: RIM_ALPHA[frame],
      }));
    }
    const label = cue.label.trim();
    if (label) {
      const spots = FREEZE_LABEL_SPOTS.map(([x, y]) => [x * W, y * H] as [number, number]);
      const at = stickerPlacement(p, { start: cue.start, end: cue.start }, label, spots, FREEZE_LABEL_SCALE);
      const k = slapScale(Math.max(0, local)), alpha = slapAlpha(Math.max(0, local));
      for (const op of stickerOps(label, at.x, at.y, stickerTilt(dc.hash(1), 6), at.scale * k, stickerPalette(dc.hash(2)), u)) {
        ops.push(op.op === "text" && alpha < 1 ? { ...op, alpha: round3(op.alpha * alpha) } : op);
      }
    }
    return ops;
  },
} satisfies CueDrawer<"freeze"> as unknown as CueDrawer);
