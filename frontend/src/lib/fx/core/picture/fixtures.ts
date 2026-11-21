// Test fixtures shared by the WP9 drawer tests (picture, overlays, retention, grades). Not imported by fx-core itself.
import type { DrawOp } from "../host";
import type { FaceTrack, FxCue, FxScene } from "../types";
import { makeScene } from "../test-utils/fake-host";

export const ASPECTS = [[1080, 1920], [1080, 1080], [1920, 1080]] as const;
export const cueBase = { seed: 11, beat: "b0", origin: "auto" as const };

/** A normalised YuNet row matching napi-host TEST_FACE (box, score, 5 landmarks), optionally shifted by (dx, dy). */
export function faceRow(dx = 0, dy = 0): number[] {
  const x = 0.36 + dx, y = 0.24 + dy;
  return [x, y, 0.28, 0.2, 0.97,
    x + 0.085, y + 0.075, x + 0.195, y + 0.075,   // eyes
    x + 0.14, y + 0.11,                            // nose
    x + 0.095, y + 0.145, x + 0.185, y + 0.145];   // mouth corners
}

/** A face track sampled at 6 fps over [0, duration], the face drifting by `drift` (normalised) per second. */
export function faceTrack(duration = 6, drift: [number, number] = [0, 0]): FaceTrack {
  const samples: [number, number[][]][] = [];
  for (let i = 0; i <= Math.round(duration * 6); i++) {
    const t = i / 6;
    samples.push([t, [faceRow(drift[0] * t, drift[1] * t)]]);
  }
  return { version: 1, space: "output", fps: 6, samples };
}

/** A scene with no captions (drawer tests look at the drawer's own ops). */
export function bareScene(overrides: Partial<FxScene> = {}): FxScene {
  return makeScene({ caption: null, edit_duration: 6, out_duration: overrides.out_duration ?? overrides.edit_duration ?? 6, ...overrides });
}

/** A cue literal of any kind without the common fields (distributive over the FxCue union). */
export type CueInput = FxCue extends infer C ? (C extends FxCue ? Omit<C, "seed" | "beat" | "origin"> & { seed?: number } : never) : never;
export const cue = <T extends FxCue = FxCue>(c: CueInput): T => ({ ...cueBase, ...c } as T);

/** JSON snapshot form: numbers to 3 dp. */
export const snap = (ops: DrawOp[]) => JSON.parse(JSON.stringify(ops, (_k, v) => (typeof v === "number" ? Math.round(v * 1000) / 1000 : v)));
