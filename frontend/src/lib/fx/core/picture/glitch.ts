// Glitch picture modifier (§11.2): 3–6 seeded horizontal slices displaced ±(40·amount)u px plus an RGB split of
// 6–14u px (three channel draws summed with `lighter`), re-seeded every 2 output frames so it stutters like a bad
// signal instead of crawling. The transition `glitch` style reuses glitchState at amount 0.8.
import { clamp01 } from "../easing";
import { round3 } from "../layout";
import { registerPicture, type PictureState } from "../registry";

/** Frames per glitch pattern (re-seeded every 2 output frames). */
export const GLITCH_HOLD_FRAMES = 2;

/**
 * The glitch for one output frame: `hash` is the cue's seeded hash01, `frame` the final frame index. Slices are
 * bands of 2–11 % of the height anywhere on the frame; the split never goes below 6u px so it reads at 720p.
 */
export function glitchState(state: PictureState, amount: number, hash: (n: number) => number, frame: number,
  u: number, height: number): PictureState {
  const a = clamp01(amount);
  if (a <= 0) return state;
  const bucket = Math.floor(Math.max(0, frame) / GLITCH_HOLD_FRAMES);
  const h = (k: number) => hash(bucket * 64 + k);
  const count = 3 + Math.min(3, Math.floor(h(0) * 4));
  const slices: PictureState["slices"] = [];
  for (let i = 0; i < count; i++) {
    const sh = (0.02 + 0.09 * h(1 + 3 * i)) * height;
    const y = h(2 + 3 * i) * (height - sh);
    const dir = h(3 + 3 * i) < 0.5 ? -1 : 1;
    const dx = dir * 40 * a * u * (0.45 + 0.55 * h(40 + i));
    slices.push({ y: round3(y), h: round3(sh), dx: round3(dx) });
  }
  const split = round3((6 + 8 * h(30)) * u);
  return { ...state, rgbSplit: Math.max(state.rgbSplit, split), slices: [...state.slices, ...slices] };
}

registerPicture({
  kind: "glitch",
  apply: (state, cue, dc) => {
    if (!cue || cue.kind !== "glitch") return state;
    return glitchState(state, cue.amount, dc.hash, dc.clock.frame, dc.u, dc.scene.height);
  },
});
