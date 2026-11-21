// Flash cue (§11.2): a full-frame rect in `color` at alpha `peak` on the cue's first frame, decaying linearly to 0
// over the cue. A flash always lasts at least 2 output frames, so even an instant hit reads.
import { rectOp, round3 } from "../layout";
import { registerCue, type CueDrawer } from "../registry";
import { FRAME, type CueOf } from "../types";

/** The flash's draw length: the cue, but never under 2 frames. */
export const flashLength = (cue: Pick<CueOf<"flash">, "start" | "end">): number => Math.max(cue.end - cue.start, 2 * FRAME);
export function flashAlpha(cue: Pick<CueOf<"flash">, "start" | "end" | "peak">, local: number): number {
  const d = flashLength(cue);
  if (local < 0 || local >= d) return 0;
  return round3(Math.max(0, Math.min(1, cue.peak)) * (1 - local / d));
}

registerCue({
  kind: "flash",
  layer: "flash",
  window: (cue) => [cue.start, cue.start + flashLength(cue)],
  ops: (cue, dc) => {
    const alpha = flashAlpha(cue, dc.local);
    if (alpha <= 0) return [];
    return [rectOp({ x: 0, y: 0, w: dc.scene.width, h: dc.scene.height, fill: cue.color || "#FFFFFF", alpha })];
  },
} satisfies CueDrawer<"flash"> as unknown as CueDrawer);
