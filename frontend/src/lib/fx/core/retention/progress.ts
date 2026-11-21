// Progress bar (§11.2), on the F clock: a 10u px bar along the bottom edge in progress_color whose width is
// o / out_duration, over a faint dark track so it reads on bright footage. Drawn above everything but the loop
// crossfade, so it is never covered by an overlay.
import type { DrawOp } from "../host";
import { clamp01 } from "../easing";
import { rectOp, round3 } from "../layout";
import { registerGlobal } from "../registry";

export const PROGRESS_PX = 10;   // u px

export const progressFraction = (o: number, outDuration: number): number => (outDuration > 0 ? clamp01(o / outDuration) : 0);

registerGlobal({
  id: "progress",
  layer: "retention",
  active: (p) => p.scene.progress_bar,
  ops: (dc): DrawOp[] => {
    const p = dc.scene, W = p.width, H = p.height;
    const h = round3(PROGRESS_PX * dc.u);
    const w = round3(W * progressFraction(dc.clock.o, p.scene.out_duration));
    const ops: DrawOp[] = [rectOp({ x: 0, y: round3(H - h), w: W, h, fill: "#000000", alpha: 0.28 })];
    if (w > 0) ops.push(rectOp({ x: 0, y: round3(H - h), w, h, fill: p.scene.progress_color || "#FF7A1A" }));
    return ops;
  },
});
