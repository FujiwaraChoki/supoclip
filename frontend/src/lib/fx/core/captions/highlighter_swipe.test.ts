// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { DrawOp } from "../host";
import { drawerErrors } from "../frame";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import type { FxWord } from "../types";
import {
  approxTiming, cueWords, SWIPE_ALPHA, SWIPE_APPROX, SWIPE_MAX, SWIPE_MIN, swipeDuration, swipeProgress, swipeStroke,
} from "./highlighter_swipe";
import { accent, cueOps, expectInsideFrame, opsAt, paths, prep, snap, templateScene } from "./wp8.testkit";

// Words 0.36 s apart from 0.2 s, each 0.306 s long. Chunks of 3: "Our revenue grew" | "300% in one" | ...
const REVENUE = accent("b1.accent", "highlight", 0.56, 1.36, { text: "revenue" });
const NUMBER = accent("b2.accent", "highlight", 1.28, 2.08, { text: "300%" });
const scene = (w = 1080, h = 1920, extra = {}) => templateScene("highlighter_swipe", { width: w, height: h, cues: [REVENUE, NUMBER], ...extra });
const marks = (ops: DrawOp[]) => paths(ops).filter((o) => o.fill !== null && o.closed);
const width = (op: Extract<DrawOp, { op: "path" }>) => {
  const xs = op.points.filter((_, i) => i % 2 === 0);
  return Math.max(...xs) - Math.min(...xs);
};

describe("highlighter_swipe frameOps", () => {
  it("snapshots at ≥5 times (1080x1920), inside the frame", () => {
    for (const t of [0.6, 0.7, 0.8, 0.9, 1.2, 1.4]) {
      const ops = cueOps(scene(), t);
      expect(snap(ops)).toMatchSnapshot(`t=${t}`);
      expectInsideFrame(ops, 1080, 1920);
    }
  });

  it("1920x1080 stays inside the frame", () => {
    expectInsideFrame(cueOps(scene(1920, 1080), 1.0), 1920, 1080);
  });

  it("paints between the light box and the dark text, with multiply at 85 %", () => {
    const ops = opsAt(prep(scene()), 1.0);
    const kinds = ops.map((o) => o.op);
    const box = kinds.indexOf("rect"), mark = ops.findIndex((o) => o.op === "path"), text = kinds.indexOf("text");
    expect(box).toBeGreaterThan(0);
    expect(mark).toBeGreaterThan(box);
    expect(text).toBeGreaterThan(mark);
    const m = marks(ops)[0];
    expect(m.blend).toBe("multiply");
    expect(m.alpha).toBeCloseTo(SWIPE_ALPHA, 9);
    expect(m.fill).toBe("#FFE14D");
  });

  it("other templates get it under the caption, solid (no box to multiply onto)", () => {
    const s = templateScene("kinetic_stack", { cues: [NUMBER] });
    const ops = opsAt(prep(s), 1.6);
    const m = marks(ops)[0];
    expect(m.blend).toBe("source-over");
    expect(ops.indexOf(m)).toBeLessThan(ops.findIndex((o) => o.op === "text"));
  });
});

describe("the swipe", () => {
  it("grows left → right with width ∝ progress over the spoken duration clamped to [120, 600] ms", () => {
    expect(swipeDuration({ start: 0, end: 0.05 }, false)).toBe(SWIPE_MIN);
    expect(swipeDuration({ start: 0, end: 2 }, false)).toBe(SWIPE_MAX);
    expect(swipeDuration({ start: 0, end: 0.3 }, false)).toBeCloseTo(0.3, 12);
    expect(swipeDuration({ start: 0, end: 2 }, true)).toBe(SWIPE_APPROX);
    const word = { id: "w1", run: "clip", text: "revenue", highlight: false, start: 1, end: 1.4, x: 100, y: 500, w: 300, h: 80, line: 0 };
    const full = swipeStroke([word], 2, false, 7, 1, 80)!;
    const span = full.x1 - full.x0;
    for (const k of [0.25, 0.5, 0.75, 1]) {
      const s = swipeStroke([word], 1 + k * 0.4, false, 7, 1, 80)!;
      expect((s.head - s.x0) / span).toBeCloseTo(k, 9);
      expect(swipeProgress(word, 1 + k * 0.4, false)).toBeCloseTo(k, 9);
    }
    expect(swipeStroke([word], 0.99, false, 7, 1, 80)).toBeNull();
    // The rough edge does not crawl while it grows: the same seed gives the same top edge.
    const a = swipeStroke([word], 1.2, false, 7, 1, 80)!, b = swipeStroke([word], 1.3, false, 7, 1, 80)!;
    expect(a.points.slice(0, 6)).toEqual(b.points.slice(0, 6));
  });

  it("in frames: narrower early, full by the end of the word, stays until the line ends", () => {
    const p = prep(scene());
    const at = (t: number) => marks(opsAt(p, t));
    const w0 = width(at(0.62)[0]), w1 = width(at(0.75)[0]), w2 = width(at(0.95)[0]);
    expect(w0).toBeLessThan(w1);
    expect(w1).toBeLessThan(w2);
    expect(width(at(1.2)[0])).toBeCloseTo(w2, 6);
    expect(at(1.2)).toHaveLength(1);             // line 1 still on screen after the word
    expect(at(1.29).every((m) => width(m) < w2)).toBe(true);   // next line: only the growing 300% swipe
  });

  it("is skewed −3° and rough-edged (seeded ±3u)", () => {
    const m = marks(opsAt(prep(scene()), 1.0))[0];
    expect(m.transform[2]).toBeCloseTo(Math.tan((-3 * Math.PI) / 180), 9);
    expect(m.points.length).toBeGreaterThan(12);
  });

  it("uses a fixed 300 ms sweep with approximate timing", () => {
    // Approximate words: two caption lines spread by character count.
    const line = (text: string, start: number, end: number, offset: number): FxWord[] => {
      const tokens = text.split(" "), total = tokens.reduce((s, x) => s + x.length, 0);
      let c = 0;
      return tokens.map((t, i) => {
        const a = start + ((end - start) * c) / total; c += t.length;
        return { id: `w${offset + i}`, start: Math.round(a * 1000) / 1000, end: Math.round((start + ((end - start) * c) / total) * 1000) / 1000, text: t, highlight: false, run: "clip" };
      });
    };
    const words = [...line("Our extraordinary revenue", 0.2, 3.2, 0), ...line("grew a lot", 3.2, 4.2, 3)];
    const cue = accent("b1.accent", "highlight", words[1].start, words[1].start + 2, { text: "extraordinary" });
    const p = prep(scene(1080, 1920, { words, cues: [cue] }));
    expect(approxTiming(p)).toBe(true);
    expect(approxTiming(prep(scene()))).toBe(false);
    const grow = (t: number) => marks(opsAt(p, t)).map(width)[0] ?? 0;
    expect(grow(words[1].start + 0.15)).toBeLessThan(grow(words[1].start + 0.3));
    expect(grow(words[1].start + 0.3)).toBeCloseTo(grow(words[1].start + 0.9), 6);
  });

  it("finds the cue's words by start time and token count", () => {
    const p = prep(scene());
    expect(cueWords(p, REVENUE).map((w) => w.text)).toEqual(["revenue"]);
    expect(cueWords(p, accent("x", "highlight", 0.56, 1.5, { text: "revenue grew" })).map((w) => w.text)).toEqual(["revenue", "grew"]);
    expect(cueWords(p, accent("x", "highlight", 9, 9.5, { text: "nothing" }))).toEqual([]);
  });

  it("is not drawn while a slam or kinetic hides the caption line it marks", () => {
    // stat_counter-style white captions: a highlight that lingers on its line, then a slam hides the captions.
    const slam = accent("b9.slam", "slam", 1.0, 1.6, { text: "grew" });
    const s = templateScene("slam", { cues: [REVENUE, slam] });
    const p = prep(s);
    expect(marks(opsAt(prep(templateScene("slam", { cues: [REVENUE] })), 1.1))).toHaveLength(1);
    expect(marks(opsAt(p, 0.9))).toHaveLength(1);
    expect(marks(opsAt(p, 1.1))).toHaveLength(0);
    expect(drawerErrors(p)).toEqual([]);
  });

  it("is deterministic and never throws", () => {
    const a = prep(scene()), b = prep(scene());
    for (const t of [0.7, 1.0, 1.5]) expect(snap(opsAt(a, t))).toEqual(snap(opsAt(b, t)));
    expect(drawerErrors(a)).toEqual([]);
  });
});

describe("highlighter_swipe raster goldens", () => {
  it("renders at 3 times (540x960)", () => {
    for (const t of [0.7, 1.0, 1.5]) expectGolden(`highlighter_swipe__highlight_t${t}`, renderSceneToPng(scene(540, 960), t));
  });
  it("renders the highlight accent on a white-text template", () => {
    const s = templateScene("kinetic_stack", { width: 540, height: 960, cues: [NUMBER] });
    expectGolden("highlighter_swipe__highlight_kinetic_t1.6", renderSceneToPng(s, 1.6));
  });
});
