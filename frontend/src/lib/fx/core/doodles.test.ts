// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { DrawOp } from "./host";
import type { DrawerContext } from "./registry";
import { IDENTITY_PICTURE } from "./registry";
import { hash01, mulberry32 } from "./rng";
import { prepareScene } from "./scene";
import { DOODLE_DRAW_IN, DOODLE_FADE, DOODLE_KINDS, doodleOps, opsBounds, polylineLength } from "./doodles";
import { fakeMeasureHost, makeScene } from "./test-utils/fake-host";

const p = prepareScene(makeScene({ template: "face_doodles" }), fakeMeasureHost);
const FACE = { x: 390, y: 460, w: 300, h: 380 };

function dc(local: number, dur = 1.6, seed = 4242): DrawerContext {
  return {
    scene: p, clock: { e: 1 + local, o: 1 + local, frame: Math.round((1 + local) * 30) }, t: 1 + local, local, dur, face: null,
    picture: { ...IDENTITY_PICTURE, filter: [], slices: [] }, u: p.u, rng: mulberry32(seed), hash: (n) => hash01(seed, n),
  };
}
type PathOp = Extract<DrawOp, { op: "path" }>;
const paths = (ops: DrawOp[]) => ops.filter((o): o is PathOp => o.op === "path");

describe("doodles", () => {
  for (const kind of DOODLE_KINDS) {
    it(`${kind}: draws around the face, follows it when it moves and scales with it`, () => {
      const ops = doodleOps(kind, FACE, dc(0.6));
      expect(ops.length).toBeGreaterThan(0);
      const b = opsBounds(ops)!;
      // Near the head: within 1.6 face widths of the face centre.
      const cx = FACE.x + FACE.w / 2, cy = FACE.y + FACE.h / 2;
      expect(Math.abs(b.x + b.w / 2 - cx)).toBeLessThan(FACE.w * 1.6);
      expect(Math.abs(b.y + b.h / 2 - cy)).toBeLessThan(FACE.h * 1.2);
      // Moving the face moves every point by the same amount (same seed, same boil step).
      const moved = opsBounds(doodleOps(kind, { ...FACE, x: FACE.x + 40, y: FACE.y - 30 }, dc(0.6)))!;
      expect(moved.x - b.x).toBeCloseTo(40, 6);
      expect(moved.y - b.y).toBeCloseTo(-30, 6);
      // A 1.4× face (a zoom) gives a ~1.4× doodle.
      const big = opsBounds(doodleOps(kind, { x: FACE.x - FACE.w * 0.2, y: FACE.y - FACE.h * 0.2, w: FACE.w * 1.4, h: FACE.h * 1.4 }, dc(0.6)))!;
      expect(big.w / b.w).toBeGreaterThan(1.2);
      expect(big.w / b.w).toBeLessThan(1.6);
      // Strokes stay at least 6u thick (720p survival).
      for (const o of paths(ops)) if (o.stroke && !o.fill) expect(o.strokePx).toBeGreaterThanOrEqual(6 * p.u - 1e-9);
    });
  }

  it("boils at 8 fps: identical within a step, re-jittered on the next", () => {
    const shaft = (local: number) => paths(doodleOps("arrows", FACE, dc(local)))[1].points;
    expect(shaft(0.5)).toEqual(shaft(0.52));
    expect(shaft(0.5)).not.toEqual(shaft(0.64));
  });

  it("draws in over 300 ms (dash reveal) and fades over the last 200 ms", () => {
    const early = paths(doodleOps("arrows", FACE, dc(0.05)))[1];
    expect(early.dash).not.toBeNull();
    expect(early.dashOffset).toBeGreaterThan(0);
    const done = paths(doodleOps("arrows", FACE, dc(DOODLE_DRAW_IN + 0.01)))[1];
    expect(done.dash).toBeNull();
    const fading = doodleOps("horns", FACE, dc(1.6 - DOODLE_FADE / 2));
    expect(paths(fading)[0].alpha).toBeCloseTo(0.5, 6);
    expect(doodleOps("horns", FACE, dc(1.61))).toEqual([]);
    expect(doodleOps("horns", { ...FACE, w: 0 }, dc(0.5))).toEqual([]);
  });

  it("is deterministic per seed and varies across seeds", () => {
    expect(doodleOps("sparkles", FACE, dc(0.7))).toEqual(doodleOps("sparkles", FACE, dc(0.7)));
    expect(doodleOps("sparkles", FACE, dc(0.7, 1.6, 1))).not.toEqual(doodleOps("sparkles", FACE, dc(0.7, 1.6, 2)));
  });

  it("measures polylines", () => {
    expect(polylineLength([0, 0, 3, 4])).toBe(5);
    expect(polylineLength([0, 0, 3, 0, 3, 4], true)).toBe(12);
  });
});
