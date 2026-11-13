// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { DrawOp } from "../host";
import { accentDrawerFor } from "../registry";
import { fakeMeasure } from "../test-utils/fake-host";
import { SLAM, slamPx, slamScale } from "./slam";
import { accent, accentOps, expectGoldens, expectInsideFrame, opsAt, prep, snap, templateScene } from "./wp7.testkit";

type TextOp = Extract<DrawOp, { op: "text" }>;
const texts = (ops: DrawOp[]) => ops.filter((o): o is TextOp => o.op === "text");
const scaleOf = (o: TextOp) => Math.hypot(o.transform[0], o.transform[1]);

const CUE = accent("b1.slam", "slam", 0.56, 1.3, { text: "revenue" });
const scene = (w = 1080, h = 1920, cue = CUE) =>
  templateScene("slam", { width: w, height: h, cues: [cue] }, { font: "THEBOLDFONT", uppercase: true });

describe("slam frameOps", () => {
  it("snapshots at 1080x1920, inside the frame at rest", () => {
    for (const t of [0.56, 0.6, 0.67, 0.74, 1.0, 1.25]) {
      const ops = accentOps(scene(), t);
      expect(snap(ops)).toMatchSnapshot(`t=${t}`);
      if (t >= 0.74) expectInsideFrame(ops, 1080, 1920);
    }
  });

  it("1920x1080: inside the frame and height-capped", () => {
    const ops = accentOps(scene(1920, 1080), 1.0);
    expect(snap(ops)).toMatchSnapshot("1920x1080 t=1.0");
    expectInsideFrame(ops, 1920, 1080);
  });

  it("the keyword alone, upper case, outline then white fill, hard shadow 8u down, centred at y = 0.42", () => {
    const ops = opsAt(prep(scene()), 1.0);
    expect(ops.map((o) => o.op)).toEqual(["picture", "text", "text"]);   // captions hidden while it shows
    const [stroke, fill] = texts(ops);
    expect(stroke.text).toBe("REVENUE");
    expect(stroke.fill).toBeNull();
    expect(stroke.stroke).toBe("#000000");
    expect(stroke.strokePx).toBeCloseTo(0.1 * stroke.px);
    expect(stroke.shadow).toEqual({ color: SLAM.shadowColor, blur: 0, dx: 0, dy: 8 });
    expect(fill.fill).toBe("#FFFFFF");
    expect(fill.transform[5]).toBeCloseTo(0.42 * 1920);
    expect(fill.transform[4]).toBeCloseTo(540);
    expect(accentDrawerFor("slam")?.hidesCaptions).toBe(true);
    expect(opsAt(prep(scene()), 1.31).some((o) => o.op === "text" && o.text !== "REVENUE")).toBe(true);
  });

  it("spans 60 % of the width at rest", () => {
    const [, fill] = texts(accentOps(scene(), 1.0));
    expect(fakeMeasure(fill.text, fill.font, fill.px) * scaleOf(fill)).toBeCloseTo(0.6 * 1080, 1);
  });

  it("is capped at 35 % of the height", () => {
    const short = accent("b1.slam", "slam", 0.56, 1.3, { text: "wow" });
    const [, fill] = texts(accentOps(scene(1920, 1080, short), 1.0));
    expect(fill.px).toBeCloseTo(0.35 * 1080);
    expect(fakeMeasure(fill.text, fill.font, fill.px)).toBeLessThan(0.6 * 1920);
    expect(slamPx(1080, 1920, 1, (px) => px * 0.01)).toBeCloseTo(0.35 * 1920);
  });

  it("scale keyframes 1.6 / 0.95 / 1.0 at 0 / 110 / 180 ms; seeded tilt within ±3°", () => {
    expect(slamScale(0)).toBe(1.6);
    expect(slamScale(0.11)).toBeCloseTo(0.95);
    expect(slamScale(0.18)).toBe(1);
    expect(slamScale(0.5)).toBe(1);
    expect(slamScale(0.05)).toBeLessThan(1.6);
    expect(slamScale(0.05)).toBeGreaterThan(0.95);
    expect(slamScale(0.15)).toBeGreaterThan(0.95);
    expect(slamScale(0.15)).toBeLessThan(1);
    const at = (t: number) => scaleOf(texts(accentOps(scene(), t))[1]);
    expect(at(0.56)).toBeCloseTo(1.6);
    expect(at(0.67)).toBeCloseTo(0.95);
    expect(at(0.74)).toBeCloseTo(1);
    for (const seed of [1, 2, 3, 99, 4242]) {
      const [, fill] = texts(accentOps(scene(1080, 1920, { ...CUE, seed }), 1.0));
      const deg = Math.abs(Math.atan2(fill.transform[1], fill.transform[0]) * 180 / Math.PI);
      expect(deg).toBeLessThanOrEqual(3);
    }
  });

  it("holds, then fades over the last 120 ms", () => {
    expect(texts(accentOps(scene(), 1.1))[1].alpha).toBe(1);
    expect(texts(accentOps(scene(), 1.24))[1].alpha).toBeCloseTo(0.5);
  });

  it("is deterministic", () => {
    expect(snap(accentOps(scene(), 0.62))).toEqual(snap(accentOps(scene(), 0.62)));
  });
});

describe("slam raster goldens", () => {
  it("slam", () => expectGoldens("slam__slam", templateScene("slam", { width: 540, height: 960, cues: [CUE] },
    { font: "THEBOLDFONT", uppercase: true }), [0.6, 0.67, 1.0]));
});
