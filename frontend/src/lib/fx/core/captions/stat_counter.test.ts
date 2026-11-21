// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { DrawOp } from "../host";
import { accentDrawerFor } from "../registry";
import type { AccentCue } from "../types";
import { COUNTER, counterDuration, counterValue, tabularAdvances } from "./stat_counter";
import { accent, accentOps, expectGoldens, expectInsideFrame, prep, snap, templateScene } from "./wp7.testkit";

type TextOp = Extract<DrawOp, { op: "text" }>;
const fills = (ops: DrawOp[]) => ops.filter((o): o is TextOp => o.op === "text" && o.fill !== null);
const shown = (ops: DrawOp[]) => fills(ops).map((o) => o.text).join("");

// "300%" is spoken 1.28–1.586 s: count = max(0.6, 0.306 + 0.4) = 0.706 s, lock at 1.986, cue end = lock + 0.9.
const CUE = accent("b2.accent", "counter", 1.28, 2.886, { text: "300%", value: 300, suffix: "%" });
const LOCK = 1.28 + 0.706;
const scene = (w = 1080, h = 1920, cue: AccentCue = CUE) => templateScene("stat_counter", { width: w, height: h, cues: [cue] },
  { font: "Poppins-ExtraBold", stroke_color: null, stroke_px: 0 });

describe("stat_counter frameOps", () => {
  it("snapshots at 1080x1920, inside the frame", () => {
    for (const t of [1.3, 1.45, 1.7, LOCK, LOCK + 1 / 30, LOCK + 0.3, 2.85]) {
      const ops = accentOps(scene(), t);
      expect(snap(ops)).toMatchSnapshot(`t=${t.toFixed(3)}`);
      expectInsideFrame(ops, 1080, 1920);
    }
  });

  it("1920x1080: inside the frame", () => {
    const ops = accentOps(scene(1920, 1080), LOCK + 0.3);
    expect(snap(ops)).toMatchSnapshot("1920x1080 lock+0.3");
    expectInsideFrame(ops, 1920, 1080);
  });

  it("counts from 0 and reaches exactly the value at lock, with prefix and suffix", () => {
    expect(counterDuration(prep(scene()), CUE)).toBeCloseTo(0.706, 6);
    expect(shown(accentOps(scene(), 1.28 + 1 / 30))).toMatch(/^\d+%$/);
    expect(Number(shown(accentOps(scene(), 1.3)).replace("%", ""))).toBeLessThan(300);
    expect(shown(accentOps(scene(), LOCK))).toBe("300%");
    expect(shown(accentOps(scene(), 2.5))).toBe("300%");
    const money = accent("b2.accent", "counter", 1.28, 2.886, { text: "2.5 million", value: 2500000, prefix: "$" });
    expect(shown(accentOps(scene(1080, 1920, money), 2.5))).toBe("$2,500,000");
    const decimals = accent("b2.accent", "counter", 1.28, 2.886, { text: "$2.50", value: 2.5, prefix: "$" });
    expect(shown(accentOps(scene(1080, 1920, decimals), 2.5))).toBe("$2.50");
    expect(shown(accentOps(scene(1080, 1920, decimals), 1.4))).toMatch(/^\$\d\.\d\d$/);
  });

  it("counts easeOutExpo and never overshoots", () => {
    let prev = -1;
    for (let t = 0; t <= 0.8; t += 0.02) {
      const v = counterValue(300, t, 0.706);
      expect(v).toBeGreaterThanOrEqual(prev);
      expect(v).toBeLessThanOrEqual(300);
      prev = v;
    }
    expect(counterValue(300, 0.706, 0.706)).toBe(300);
    expect(counterValue(300, 0.353, 0.706)).toBeGreaterThan(290);   // expo: most of the way by mid-count
  });

  it("falls back to the resolver's cue length without the spoken words", () => {
    const words = { ...scene(), words: [] };
    expect(counterDuration(prep(words), CUE)).toBeCloseTo(2.886 - 1.28 - 0.9, 6);
    const short = accent("x", "counter", 0, 0.3, { text: "5", value: 5 });
    expect(counterDuration(prep({ ...scene(), words: [] }), short)).toBeLessThanOrEqual(0.3);
  });

  it("tabular digits: every digit has the same advance", () => {
    const ops = fills(accentOps(scene(1080, 1920, accent("b2.accent", "counter", 1.28, 2.886, { text: "1,118", value: 1118 })), 2.5));
    const digits = ops.filter((o) => /\d/.test(o.text));
    const gaps = digits.slice(1).map((o, i) => o.x - digits[i].x);
    // 1 , 1 1 8: the gaps between neighbouring digits are equal (the comma adds its own advance once).
    expect(gaps[1]).toBeCloseTo(gaps[2], 6);
  });

  it("stamps at lock: 1.4 → 1 in 120 ms, −6°, accent colour, 2-frame white rim fading 1 → 0", () => {
    const pre = accentOps(scene(), LOCK - 0.05);
    expect(pre.some((o) => o.op === "text" && o.stroke === "#FFFFFF")).toBe(false);
    expect(fills(pre)[0].fill).toBe("#FFFFFF");
    const at = accentOps(scene(), LOCK);
    const rim = at.filter((o): o is TextOp => o.op === "text" && o.stroke === "#FFFFFF");
    expect(rim.length).toBe(4);
    expect(rim[0].strokePx).toBeCloseTo(COUNTER.strokeEm * 2.2 * 78 + COUNTER.rimPx);   // 14u past the black outline
    expect(rim[0].alpha).toBe(1);
    const f = fills(at)[0];
    expect(f.fill).toBe("#FFD23F");
    expect(Math.hypot(f.transform[0], f.transform[1])).toBeCloseTo(1.4);
    expect(Math.atan2(f.transform[1], f.transform[0]) * 180 / Math.PI).toBeCloseTo(-6);
    const next = accentOps(scene(), LOCK + 1 / 30).filter((o): o is TextOp => o.op === "text" && o.stroke === "#FFFFFF");
    expect(next[0].alpha).toBeCloseTo(0.5);
    expect(accentOps(scene(), LOCK + 2 / 30).some((o) => o.op === "text" && o.stroke === "#FFFFFF")).toBe(false);
    const settled = fills(accentOps(scene(), LOCK + 0.12))[0];
    expect(Math.hypot(settled.transform[0], settled.transform[1])).toBeCloseTo(1);
  });

  it("2.2 × the caption size, centred at y = 0.36, fitting 88 % of the width", () => {
    const f = fills(accentOps(scene(), 2.5));
    expect(f[0].px).toBeCloseTo(2.2 * 78);
    expect(f[0].transform[5]).toBeCloseTo(0.36 * 1920);
    const huge = accent("b2.accent", "counter", 1.28, 2.886, { text: "x", value: 123456789012, prefix: "$" });
    const h = fills(accentOps(scene(1080, 1920, huge), 2.5));
    expect(h[0].px).toBeLessThan(2.2 * 78);
    expectInsideFrame(h, 1080, 1920);
    expect(accentDrawerFor("counter")?.layer).toBe("overlay");
    // The stamp peak is lowered for a number this wide, so even its first locked frame stays inside the width.
    const stamped = fills(accentOps(scene(1080, 1920, huge), 1.28 + counterDuration(prep(scene(1080, 1920, huge)), huge)));
    const peak = Math.hypot(stamped[0].transform[0], stamped[0].transform[1]);
    expect(peak).toBeGreaterThanOrEqual(1);
    expect(peak).toBeLessThan(COUNTER.stampFrom);
    const widthAtRest = tabularAdvances(prep(scene(1080, 1920, huge)), stamped.map((o) => o.text).join(""), stamped[0].font, stamped[0].px)
      .reduce((a, b) => a + b, 0);
    expect(widthAtRest * peak).toBeLessThanOrEqual(0.96 * 1080 + 1e-6);
  });

  it("is deterministic", () => {
    expect(snap(accentOps(scene(), 1.5))).toEqual(snap(accentOps(scene(), 1.5)));
  });
});

describe("stat_counter raster goldens", () => {
  it("counter", () => expectGoldens("stat_counter__counter", templateScene("stat_counter", { width: 540, height: 960, cues: [CUE] },
    { font: "Poppins-ExtraBold", stroke_color: null, stroke_px: 0 }), [1.4, 2, 2.5]));
});
