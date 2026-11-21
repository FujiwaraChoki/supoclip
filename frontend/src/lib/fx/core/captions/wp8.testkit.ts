// Test kit for the WP8 drawer tests (kinetic_stack, decode_glitch, highlighter_swipe, face_doodles,
// doodles). Test-only: imported by *.test.ts files, never by fx-core itself (so it is not in the bundle).
import { expect } from "vitest";
import type { DrawOp } from "../host";
import { prepareScene, type PreparedScene } from "../scene";
import { clockAt, frameOps } from "../frame";
import { applyMat } from "../layout";
import type { AccentCue, AccentVariant, FaceTrack, FxCaptionStyle, FxCaptionTemplate, FxScene } from "../types";
import { captionStyle, fakeMeasureHost, makeScene } from "../test-utils/fake-host";
import { TEST_FACE } from "../test-utils/napi-host";
import { fnv1a32 } from "../rng";

/** JSON snapshot form: numbers to 3 dp so float noise never churns a snapshot. */
export const snap = (ops: DrawOp[]): unknown =>
  JSON.parse(JSON.stringify(ops, (_k, v) => (typeof v === "number" ? Math.round(v * 1000) / 1000 : v)));

export function accent(id: string, variant: AccentVariant, start: number, end: number, extra: Partial<AccentCue> = {}): AccentCue {
  return {
    id, kind: "accent", variant, start, end, seed: fnv1a32(`wp8:${id}`), beat: id.split(".")[0], origin: "auto",
    text: "", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "", ...extra,
  };
}

/** The test picture's face as a 6 fps output-space track; `drift(i)` moves / grows it per sample. */
export function faceTrack(duration: number, at: (t: number) => { x: number; y: number; w: number; h: number } = () => TEST_FACE): FaceTrack {
  const n = Math.ceil(duration * 6) + 1;
  return {
    version: 1, space: "output", fps: 6,
    samples: Array.from({ length: n }, (_, i) => {
      const f = at(i / 6);
      return [i / 6, [[f.x, f.y, f.w, f.h, 0.95]]] as [number, number[][]];
    }),
  };
}

/** Per-template caption look used by the WP8 tests (what WP11's FX template dicts resolve to). */
export function wp8Style(template: FxCaptionTemplate, width: number): FxCaptionStyle {
  const base = captionStyle({ template }, width);
  switch (template) {
    case "kinetic_stack": return { ...base, font: "THEBOLDFONT", uppercase: true, words_per_line: 2, accent: "#FFE000" };
    case "decode_glitch": return { ...base, uppercase: true, accent: "#39FF88", stroke_px: 7 * (width / 1080) };
    case "highlighter_swipe": return { ...base, color: "#111111", stroke_color: null, stroke_px: 0, shadow: false, background: "#FFFFFFE6", accent: "#FFE14D" };
    default: return base;
  }
}

/** A scene for `template` with its look; captions on, test face unless `face_track` is given. */
export function templateScene(template: FxCaptionTemplate, overrides: Partial<FxScene> = {}): FxScene {
  const width = overrides.width ?? 1080, height = overrides.height ?? 1920;
  return makeScene({
    template, width, height, caption: wp8Style(template, Math.min(width, height)),
    face_track: faceTrack(overrides.edit_duration ?? 5), ...overrides,
  });
}

export const prep = (scene: FxScene): PreparedScene => prepareScene(scene, fakeMeasureHost);
export const opsAt = (p: PreparedScene, t: number): DrawOp[] => frameOps(p, clockAt(p, t));

/** The ops a set of cues adds to a frame (frame with cues minus the frame without them, first-match multiset). */
export function cueOps(scene: FxScene, t: number): DrawOp[] {
  const withOps = opsAt(prep(scene), t);
  const without = opsAt(prep({ ...scene, cues: [] }), t).map((o) => JSON.stringify(o));
  return withOps.filter((o) => {
    const i = without.indexOf(JSON.stringify(o));
    if (i < 0) return true;
    without.splice(i, 1);
    return false;
  });
}

/** Output-px points an op covers (path points, text anchors, rect corners), through its transform. */
export function opPoints(op: DrawOp): [number, number][] {
  if (op.op === "path") {
    const out: [number, number][] = [];
    for (let i = 0; i + 1 < op.points.length; i += 2) out.push(applyMat(op.transform, op.points[i], op.points[i + 1]));
    return out;
  }
  if (op.op === "text") return [applyMat(op.transform, op.x, op.y)];
  if (op.op === "rect") return [applyMat(op.transform, op.x, op.y), applyMat(op.transform, op.x + op.w, op.y + op.h)];
  return [];
}

/** Every drawable point stays inside the w×h frame. */
export function expectInsideFrame(ops: DrawOp[], w: number, h: number, slack = 1): void {
  for (const op of ops) {
    for (const [x, y] of opPoints(op)) {
      expect(x).toBeGreaterThanOrEqual(-slack);
      expect(x).toBeLessThanOrEqual(w + slack);
      expect(y).toBeGreaterThanOrEqual(-slack);
      expect(y).toBeLessThanOrEqual(h + slack);
    }
  }
}

export type TextOp = Extract<DrawOp, { op: "text" }>;
export type PathOp = Extract<DrawOp, { op: "path" }>;
export const texts = (ops: DrawOp[]): TextOp[] => ops.filter((o): o is TextOp => o.op === "text");
export const paths = (ops: DrawOp[]): PathOp[] => ops.filter((o): o is PathOp => o.op === "path");
