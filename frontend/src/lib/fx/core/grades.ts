// STUB (WP3b) for grades, owned by WP9 (§11.4): replace the bodies, keep the exports and the registration.
// gradeOps(id) → the CSS filter for the picture op; gradeMatrix(id) → the same as a 4×5 colour matrix (ImageData path
// when the host has no ctx.filter, and the Plan B ffmpeg colorchannelmixer mapping).
import type { Blend } from "./host";
import { registerPicture } from "./registry";
import type { GradeId } from "./types";

export type GradeOp = ["brightness" | "contrast" | "saturate" | "hue-rotate" | "sepia" | "grayscale", number];
export interface GradeSpec {
  ops: GradeOp[]; tint: { color: string; blend: Blend; alpha: number }[]; vignette: number; scanlines: number; glow: number;
}

const NONE: GradeSpec = { ops: [], tint: [], vignette: 0, scanlines: 0, glow: 0 };
export const GRADE_SPECS: Record<GradeId, GradeSpec> = {
  none: NONE, punchy: NONE, warm: NONE, teal_orange: NONE, noir: NONE, vhs: NONE, dream: NONE,
};

export function gradeOps(id: GradeId): string {
  void id;
  return "";
}

export function gradeMatrix(id: GradeId): number[] {
  void id;
  return [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0];
}

registerPicture({
  kind: "grade",
  apply: (state) => state,
});
