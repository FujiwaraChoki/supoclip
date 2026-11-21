// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { DrawOp } from "../host";
import { accentDrawerFor } from "../registry";
import { fakeMeasure } from "../test-utils/fake-host";
import { TEST_FACE } from "../test-utils/napi-host";
import type { FxCue } from "../types";
import { BEHIND, behindPx, behindText, behindTop } from "./behind_head";
import { accent, accentOps, expectGoldens, expectInsideFrame, opsAt, prep, snap, templateScene, testFaceTrack } from "./wp7.testkit";

type TextOp = Extract<DrawOp, { op: "text" }>;

const CUE = accent("b1.behind", "behind", 1.0, 3.0, { text: "nobody saw" });
// The matte reports the person from 0.9 to 2.2 s, no person from 2.2 to 3.2 s.
const scene = (w = 1080, h = 1920, cues: FxCue[] = [CUE], face = true) => templateScene("behind_head", {
  width: w, height: h, cues, face_track: face ? testFaceTrack(4) : null,
  matte_windows: [{ start: 0.9, end: 2.2, person: true }, { start: 2.2, end: 3.2, person: false }],
}, { stroke_color: null, stroke_px: 0 });

describe("behind_head frameOps", () => {
  it("snapshots at 1080x1920, inside the frame", () => {
    for (const t of [1.0, 1.05, 1.1, 1.3, 2.0, 2.5, 2.95]) {
      const ops = accentOps(scene(), t);
      expect(snap(ops)).toMatchSnapshot(`t=${t}`);
      expectInsideFrame(ops, 1080, 1920);
    }
  });

  it("1920x1080: inside the frame, ≤ 0.25 H tall", () => {
    const ops = accentOps(scene(1920, 1080), 1.5);
    expect(snap(ops)).toMatchSnapshot("1920x1080 t=1.5");
    expectInsideFrame(ops, 1920, 1080);
    const text = ops.find((o): o is TextOp => o.op === "text")!;
    expect(text.px).toBeLessThanOrEqual(0.25 * 1080 + 1e-9);
  });

  it("draws the text in the behind layer, then the person cutout over it", () => {
    const ops = opsAt(prep(scene()), 1.5);
    const i = ops.findIndex((o) => o.op === "text" && o.text === "NOBODY SAW");
    expect(i).toBe(1);                               // right after the base picture, under the captions
    const cut = ops[i + 1] as Extract<DrawOp, { op: "picture" }>;
    expect(cut.op).toBe("picture");
    expect(cut.cutout).toBe(true);
    expect(ops[0]).toMatchObject({ op: "picture", cutout: false });
    expect(accentDrawerFor("behind")?.layer).toBe("behind");
  });

  it("in front at 0.85 with a shadow when the matte has no person (or there is no matte)", () => {
    const ops = accentOps(scene(), 2.5);
    expect(ops.map((o) => o.op)).toEqual(["text"]);
    const t = ops[0] as TextOp;
    expect(t.alpha).toBeCloseTo(BEHIND.frontAlpha);
    expect(t.shadow).not.toBeNull();
    const noMatte = accentOps({ ...scene(), matte_windows: [] }, 1.5);
    expect(noMatte.map((o) => o.op)).toEqual(["text"]);
  });

  it("fills 92 % of the width, capped at 450u", () => {
    const t = accentOps(scene(), 1.5)[0] as TextOp;
    expect(fakeMeasure(t.text, t.font, t.px)).toBeCloseTo(0.92 * 1080, 1);
    const one = accentOps(scene(1080, 1920, [{ ...CUE, text: "no" }]), 1.5)[0] as TextOp;
    expect(one.px).toBe(450);
    expect(behindPx(1080, 1920, 1, () => 1)).toBe(450);
    expect(behindText("one two three")).toBe("ONE TWO");
  });

  it("top edge at faceTop − 0.2·faceH (≥ 0.08 H), 0.18 H without a face; rises 40u while fading in over 200 ms", () => {
    const face = { x: 0, y: TEST_FACE.y * 1920, w: 1, h: TEST_FACE.h * 1920, landmarks: null };
    expect(behindTop(1920, face)).toBeCloseTo((TEST_FACE.y - 0.2 * TEST_FACE.h) * 1920);
    expect(behindTop(1920, { ...face, y: 50 })).toBeCloseTo(0.08 * 1920);
    expect(behindTop(1920, null)).toBeCloseTo(0.18 * 1920);
    const rest = accentOps(scene(), 1.5)[0] as TextOp;
    expect(rest.y - rest.px / 2).toBeCloseTo((TEST_FACE.y - 0.2 * TEST_FACE.h) * 1920, 3);
    const start = accentOps(scene(), 1.0)[0] as TextOp | undefined;
    expect(start).toBeUndefined();                    // alpha 0 at the very start
    const early = accentOps(scene(), 1.05)[0] as TextOp;
    expect(early.alpha).toBeGreaterThan(0);
    expect(early.alpha).toBeLessThan(1);
    expect(early.y - rest.y).toBeGreaterThan(0);
    expect(early.y - rest.y).toBeLessThan(40);
    const noFace = accentOps(scene(1080, 1920, [CUE], false), 1.5)[0] as TextOp;
    expect(noFace.y - noFace.px / 2).toBeCloseTo(0.18 * 1920, 3);
  });

  it("follows a moving face (transformed by the picture state)", () => {
    const s = { ...scene(), face_track: { ...testFaceTrack(4), samples: testFaceTrack(4).samples.map(([t, f]) => [t, [[f[0][0], f[0][1] + 0.02 * t, f[0][2], f[0][3], 0.9]]] as [number, number[][]]) } };
    const a = accentOps(s, 1.5)[0] as TextOp, b = accentOps(s, 2.0)[0] as TextOp;
    expect(b.y).toBeGreaterThan(a.y);
  });

  it("is deterministic", () => {
    expect(snap(accentOps(scene(), 1.3))).toEqual(snap(accentOps(scene(), 1.3)));
  });
});

describe("behind_head raster goldens", () => {
  const golden = () => templateScene("behind_head", {
    width: 540, height: 960, cues: [CUE], face_track: testFaceTrack(4),
    matte_windows: [{ start: 0.9, end: 2.2, person: true }, { start: 2.2, end: 3.2, person: false }],
  }, { stroke_color: null, stroke_px: 0 });
  it("behind the person", () => expectGoldens("behind_head__behind", golden(), [1.08, 1.5, 2.0]));
  it("in front (no person in the matte)", () => expectGoldens("behind_head__behind_front", golden(), [2.5]));
});
