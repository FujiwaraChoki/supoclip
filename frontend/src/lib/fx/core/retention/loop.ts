// Loop crossfade (§11.2): over the last 0.4 s of F the first output frame (cached by the host) fades in on top of
// everything, so the clip's end dissolves into its own start and a replay loops seamlessly. The audio side of the
// seam is the bus's job (WP12b).
import { round3 } from "../layout";
import { registerGlobal } from "../registry";

export const LOOP_CROSSFADE = 0.4;

/** frame0 alpha at final time o: 0 → 1 over [out − 0.4, out]. */
export function loopAlpha(o: number, outDuration: number): number {
  if (outDuration <= 0) return 0;
  const start = Math.max(0, outDuration - LOOP_CROSSFADE);
  if (o < start) return 0;
  return round3(Math.min(1, (o - start) / (outDuration - start || 1)));
}

registerGlobal({
  id: "loop",
  layer: "loop",
  active: (p, clock) => p.scene.loop_ending === "crossfade" && loopAlpha(clock.o, p.scene.out_duration) > 0,
  ops: (dc) => {
    const alpha = loopAlpha(dc.clock.o, dc.scene.scene.out_duration);
    return alpha > 0 ? [{ op: "frame0", alpha }] : [];
  },
});
