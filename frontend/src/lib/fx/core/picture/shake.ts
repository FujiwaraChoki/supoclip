// Shake picture modifier (§11.2): a decaying two-sine wobble, phases seeded per cue.
//   dx, dy = amp·u·(1 − τ)·(sin(2πf·t + φ1) + 0.5·sin(2π·1.7f·t + φ2)) / 1.5,   τ = local / dur, t = local
// x and y use independent phase pairs. No fixed overscan: frame.ts covers the frame through coverScale (AR-M5).
import { registerPicture, type DrawerContext } from "../registry";
import type { CueOf } from "../types";

/** The four seeded phases (radians): [φ1x, φ2x, φ1y, φ2y]. */
export const shakePhases = (hash: DrawerContext["hash"]): [number, number, number, number] =>
  [1, 2, 3, 4].map((n) => 2 * Math.PI * hash(n)) as [number, number, number, number];

/** Output-px offset of a shake cue at `local` seconds into it (0 outside the cue). */
export function shakeOffset(cue: Pick<CueOf<"shake">, "amp" | "freq">, local: number, dur: number, u: number,
  phases: readonly [number, number, number, number]): [number, number] {
  if (dur <= 0 || local < 0 || local >= dur) return [0, 0];
  const tau = local / dur, f = cue.freq, a = cue.amp * u * (1 - tau);
  const wave = (p1: number, p2: number) => (Math.sin(2 * Math.PI * f * local + p1) + 0.5 * Math.sin(2 * Math.PI * 1.7 * f * local + p2)) / 1.5;
  return [a * wave(phases[0], phases[1]), a * wave(phases[2], phases[3])];
}

registerPicture({
  kind: "shake",
  apply: (state, cue, dc) => {
    if (!cue || cue.kind !== "shake") return state;
    const [dx, dy] = shakeOffset(cue, dc.local, dc.dur, dc.u, shakePhases(dc.hash));
    if (dx === 0 && dy === 0) return state;
    return { ...state, dx: state.dx + dx, dy: state.dy + dy };
  },
});
