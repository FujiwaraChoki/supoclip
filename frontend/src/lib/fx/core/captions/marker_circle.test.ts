// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { DrawOp } from "../host";
import { drawerErrors } from "../frame";
import { accentDrawerFor } from "../registry";
import { arrowGeometry, circlePoints, drawInFraction, MARKER, polylineLength } from "./marker_circle";
import { accent, accentOps, expectGoldens, expectInsideFrame, opsAt, prep, snap, templateScene } from "./wp7.testkit";

type PathOp = Extract<DrawOp, { op: "path" }>;
const paths = (ops: DrawOp[]) => ops.filter((o): o is PathOp => o.op === "path");

// Sample words: "Our revenue grew | 300% in one | year and honestly | nobody saw that | coming", 0.36 s apart from 0.2 s.
const CUES = {
  circle: accent("b1.accent", "circle", 0.56, 1.36, { text: "revenue" }),
  arrow: accent("b2.accent", "arrow", 1.28, 2.08, { text: "300%" }),
  underline: accent("b3.accent", "underline", 3.08, 3.88, { text: "honestly" }),
  strike: accent("b4.accent", "strike", 3.44, 4.24, { text: "nobody" }),
};
const TIMES: Record<keyof typeof CUES, number[]> = {
  circle: [0.5, 0.6, 0.7, 0.81, 1.0, 1.2],
  arrow: [1.3, 1.4, 1.5, 1.58, 1.9, 2.2],
  underline: [3.1, 3.2, 3.3, 3.34, 3.4],
  strike: [3.46, 3.52, 3.59, 3.8, 4.1],
};
const scene = (w = 1080, h = 1920) => templateScene("marker_circle", { width: w, height: h, cues: Object.values(CUES) });

describe("marker_circle frameOps", () => {
  for (const variant of Object.keys(CUES) as (keyof typeof CUES)[]) {
    it(`${variant}: snapshots at 1080x1920 and inside the frame`, () => {
      const s = scene();
      for (const t of TIMES[variant]) {
        const ops = accentOps(s, t);
        expect(snap(ops)).toMatchSnapshot(`t=${t}`);
        expectInsideFrame(ops, 1080, 1920);
      }
    });

    it(`${variant}: 1920x1080 stays inside the frame`, () => {
      const s = scene(1920, 1080);
      const t = TIMES[variant][3];
      const ops = accentOps(s, t);
      expect(paths(ops).length).toBeGreaterThan(0);
      expect(snap(ops)).toMatchSnapshot(`1920x1080 t=${t}`);
      expectInsideFrame(ops, 1920, 1080);
    });
  }

  it("every mark is a shadow pass then an accent-colour pass, 10u wide with round caps", () => {
    const ops = paths(accentOps(scene(), 0.9));
    expect(ops).toHaveLength(2);
    expect(ops[0].stroke).toBe(MARKER.shadowColor);
    expect(ops[1].stroke).toBe("#FFD23F");
    expect(ops.every((o) => o.strokePx === 10 && o.cap === "round")).toBe(true);
    const landscape = paths(accentOps(scene(1920, 1080), 0.9));
    expect(landscape[1].strokePx).toBe(10);   // u = min(W, H) / 1080 = 1
    const small = paths(accentOps(scene(720, 1280), 0.9));
    expect(small[1].strokePx).toBeCloseTo(10 * (720 / 1080));
  });

  it("is drawn after the caption text (caption layer)", () => {
    const ops = opsAt(prep(scene()), 0.9);
    const lastText = ops.map((o) => o.op).lastIndexOf("text");
    const firstPath = ops.findIndex((o) => o.op === "path");
    expect(firstPath).toBeGreaterThan(lastText);
  });
});

describe("marker motion", () => {
  it("draws in over 250 ms (easeOutCubic) and is complete at 250 ms", () => {
    expect(drawInFraction(0, 0.25)).toBe(0);
    expect(drawInFraction(0.125, 0.25)).toBeCloseTo(0.875);
    expect(drawInFraction(0.25, 0.25)).toBe(1);
    const half = paths(accentOps(scene(), 0.56 + 0.1))[1];
    expect(half.dash).not.toBeNull();
    const len = half.dash![0];
    expect(half.dashOffset).toBeGreaterThan(0);
    expect(half.dashOffset).toBeLessThan(len);
    const done = paths(accentOps(scene(), 0.56 + 0.25))[1];
    expect(done.dash).toBeNull();
    expect(done.dashOffset).toBe(0);
  });

  it("strike draws in over 150 ms, arrow over 300 ms (head last)", () => {
    expect(paths(accentOps(scene(), 3.44 + 0.15))[1].dash).toBeNull();
    expect(paths(accentOps(scene(), 3.44 + 0.1))[1].dash).not.toBeNull();
    expect(paths(accentOps(scene(), 1.28 + 0.05))).toHaveLength(2);   // shaft only (the head grows in over the last 20 %)
    const full = paths(accentOps(scene(), 1.28 + 0.3));
    expect(full).toHaveLength(4);                                        // shaft + head, each with its shadow
    expect(full[3].points).toHaveLength(6);
  });

  it("boils at 8 fps after the draw-in: same within a step, different across steps", () => {
    const at = (t: number) => paths(accentOps(scene(), t))[1].points;
    expect(at(1.0)).toEqual(at(1.01));          // floor(t·8) = 8 for both
    expect(at(1.0)).not.toEqual(at(1.13));      // step 9
    const a = at(1.0), b = at(1.13);
    for (let i = 0; i < a.length; i++) expect(Math.abs(a[i] - b[i])).toBeLessThanOrEqual(2 * MARKER.boilPx + 1e-9);
  });

  it("fades out over the last 120 ms before its caption line changes", () => {
    // "Our revenue grew" leaves at 1.28 (the next line starts); the circle cue would run to 1.36.
    const only = templateScene("marker_circle", { cues: [CUES.circle] });
    const p0 = paths(accentOps(only, 1.1))[1];
    const p1 = paths(accentOps(only, 1.22))[1];
    expect(p0.alpha).toBe(1);
    expect(p1.alpha).toBeCloseTo(0.5, 5);
    expect(paths(accentOps(only, 1.3))).toHaveLength(0);
  });

  it.each([[300, 78], [80, 78], [620, 78], [420, 172]])("circle: 48 points over 1.15 turns clear a %ix%i word box", (bw, bh) => {
    const box = { x: 100, y: 1300, w: bw, h: bh };
    const pts = circlePoints(box, (n) => ((n * 0.618034) % 1));
    expect(pts).toHaveLength(96);
    const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
    let sweep = 0, prev = Math.atan2(pts[1] - cy, pts[0] - cx);
    for (let i = 2; i < pts.length; i += 2) {
      const a = Math.atan2(pts[i + 1] - cy, pts[i] - cx);
      let d = a - prev;
      if (d > Math.PI) d -= 2 * Math.PI;
      if (d < -Math.PI) d += 2 * Math.PI;
      sweep += d;
      prev = a;
      // Every point clears the word box, jitter and spiral included.
      const inside = Math.abs(pts[i] - cx) < box.w / 2 && Math.abs(pts[i + 1] - cy) < box.h / 2;
      expect(inside).toBe(false);
    }
    // More than one full turn (1.15 in the ellipse's own parameter; the screen angle of a long ellipse lags it), and
    // the end overshoots past the start instead of closing on it.
    const turns = Math.abs(sweep) / (2 * Math.PI);
    expect(turns).toBeGreaterThan(1.01);
    expect(turns).toBeLessThan(1.2);
    expect(Math.hypot(pts[0] - pts[pts.length - 2], pts[1] - pts[pts.length - 1])).toBeGreaterThan(bh * 0.1);
  });

  it("arrow comes from the nearer side, ends at the box with a 28u head", () => {
    const right = arrowGeometry({ x: 600, y: 1300, w: 120, h: 78 }, 1080, 1920, 1, () => 0.5);
    expect(right.shaft[0]).toBeGreaterThan(900);
    const tip = [right.head[2], right.head[3]];
    expect(tip[0]).toBeCloseTo(600 + 120 + 18);
    expect(tip[1]).toBeCloseTo(1300 + 39);
    expect(Math.hypot(right.head[0] - tip[0], right.head[1] - tip[1])).toBeCloseTo(28);
    const left = arrowGeometry({ x: 150, y: 1300, w: 160, h: 78 }, 1080, 1920, 1, () => 0.5);
    expect(left.shaft[0]).toBeLessThan(150);
    // A wide number leaves no room at the side: the arrow comes down from above.
    const wide = arrowGeometry({ x: 90, y: 1300, w: 900, h: 78 }, 1080, 1920, 1, () => 0.5);
    expect(wide.head[3]).toBeLessThan(1300);
    expect(polylineLength(wide.shaft)).toBeGreaterThan(150);
  });

  it("is deterministic and seeded per cue", () => {
    expect(snap(accentOps(scene(), 1.0))).toEqual(snap(accentOps(scene(), 1.0)));
    const other = templateScene("marker_circle", { cues: [{ ...CUES.circle, seed: 99 }] });
    expect(paths(accentOps(other, 1.0))[1].points).not.toEqual(paths(accentOps(scene(), 1.0))[1].points);
  });

  it("without FX captions it draws the keyword itself and marks it", () => {
    const s = templateScene("marker_circle", { cues: [CUES.circle] }, { enabled: false });
    const ops = accentOps(s, 0.9);
    const texts = ops.filter((o) => o.op === "text") as Extract<DrawOp, { op: "text" }>[];
    expect(texts.map((t) => t.text)).toEqual(["revenue", "revenue"]);
    expect(paths(ops)).toHaveLength(2);
    expect(drawerErrors(prep(s))).toEqual([]);
    // The keyword fades out with its mark instead of blinking off at the cue end.
    const late = accentOps(s, CUES.circle.end - 0.06).filter((o) => o.op === "text");
    expect(late).toHaveLength(2);
    expect(late.every((o) => o.alpha > 0.4 && o.alpha < 0.6)).toBe(true);
  });

  it("registers the four marker variants in the caption layer", () => {
    for (const v of ["circle", "underline", "strike", "arrow"] as const) expect(accentDrawerFor(v)?.layer).toBe("caption");
  });
});

describe("marker_circle raster goldens", () => {
  // Goldens at 540x960 (u = 0.5) keep the committed PNGs small; the frameOps snapshots cover 1080x1920.
  const full = () => templateScene("marker_circle", { width: 540, height: 960, cues: Object.values(CUES) });
  it("circle", () => expectGoldens("marker_circle__circle", full(), [0.66, 0.9, 1.22]));
  it("arrow", () => expectGoldens("marker_circle__arrow", full(), [1.4, 1.6, 2.0]));
  it("underline", () => expectGoldens("marker_circle__underline", full(), [3.18, 3.3, 3.4]));
  it("strike", () => expectGoldens("marker_circle__strike", full(), [3.5, 3.7, 4.0]));
  it("circle 1920x1080", () => expectGoldens("marker_circle__circle_960x540", templateScene("marker_circle", { width: 960, height: 540, cues: [CUES.circle] }), [0.9]));
});
