// @vitest-environment node
import { describe, expect, it } from "vitest";
import type { DrawOp } from "../host";
import { drawerErrors } from "../frame";
import { hash01 } from "../rng";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import type { FxWord } from "../types";
import { fakeMeasureHost } from "../test-utils/fake-host";
import {
  accentScale, DECODE_ACCENT_SCALE, DECODE_LOCK, DECODE_SPLIT_PX, decodeChar, joltAt, lockTime, SCANLINE_ALPHA, SCRAMBLE,
} from "./decode_glitch";
import { accent, cueOps, expectInsideFrame, opsAt, prep, snap, templateScene, texts } from "./wp8.testkit";

// Chunks: "Our revenue grew" 0.2 | "300% in one" 1.28 | "year and honestly" 2.36 | ...
const ONE = accent("b5.accent", "decode", 2.0, 2.6, { text: "one" });
const scene = (w = 1080, h = 1920, extra = {}) => templateScene("decode_glitch", { width: w, height: h, cues: [ONE], ...extra });
const fills = (ops: DrawOp[]) => texts(ops).filter((o) => o.fill !== null);
const hash = (n: number) => hash01(77, n);

describe("decode letters", () => {
  it("lock left → right, all by 180 ms", () => {
    const n = 11;
    const times = Array.from({ length: n }, (_, i) => lockTime(i, n));
    for (let i = 1; i < n; i++) expect(times[i]).toBeGreaterThan(times[i - 1]);
    expect(times[n - 1]).toBeCloseTo(DECODE_LOCK, 12);
    const chars = Array.from("REVENUE");
    for (let j = 0; j < chars.length; j++) {
      expect(decodeChar(chars, j, j, chars.length, DECODE_LOCK, hash, true)).toEqual({ char: chars[j], locked: true });
      expect(decodeChar(chars, j, j, chars.length, 0, hash, true).locked).toBe(false);
    }
    // At 100 ms the left letters are locked and the right ones still scramble.
    const mid = chars.map((_, j) => decodeChar(chars, j, j, chars.length, 0.1, hash, true).locked);
    expect(mid).toEqual([true, true, true, false, false, false, false]);
  });

  it("scrambles Latin from A-Z0-9#%&@ and other scripts from the word's own letters; punctuation never scrambles", () => {
    const latin = Array.from("ABC");
    for (let f = 0; f < 6; f++) expect(SCRAMBLE).toContain(decodeChar(latin, 2, 2, 3, f / 30, hash, true).char);
    const cyr = Array.from("ПРИВЕТ");
    for (let f = 0; f < 6; f++) expect(cyr).toContain(decodeChar(cyr, 5, 5, 6, f / 30, hash, false).char);
    expect(decodeChar(Array.from("A!"), 1, 1, 2, 0, hash, true)).toEqual({ char: "!", locked: true });
  });

  it("re-scrambles every frame, deterministically", () => {
    const chars = Array.from("WXYZWXYZ");
    const at = (t: number) => chars.map((_, j) => decodeChar(chars, j, j, 8, t, hash, true).char).join("");
    expect(at(0.01)).toBe(at(0.02));
    expect(at(0.01)).not.toBe(at(0.04));
  });
});

describe("decode_glitch lines", () => {
  it("snapshots at ≥5 times (1080x1920), inside the frame", () => {
    const p = prep(scene());
    for (const t of [0.2, 0.25, 0.3, 0.45, 1.3, 2.0, 2.04, 2.2]) {
      const ops = opsAt(p, t);
      expect(snap(ops)).toMatchSnapshot(`t=${t}`);
      expectInsideFrame(ops, 1080, 1920);
    }
    expect(drawerErrors(p)).toEqual([]);
  });

  it("1920x1080 stays inside the frame", () => {
    const p = prep(scene(1920, 1080));
    for (const t of [0.22, 2.05]) expectInsideFrame(opsAt(p, t), 1920, 1080);
  });

  it("every line enters with a decode and is fully locked by 180 ms", () => {
    const p = prep(scene());
    const early = fills(opsAt(p, 0.2 + 0.15));
    expect(early.some((o) => o.text.length === 1)).toBe(true);   // per-letter while decoding
    const settled = fills(opsAt(p, 0.2 + DECODE_LOCK + 0.01));
    expect(settled.map((o) => o.text)).toEqual(["OUR", "REVENUE", "GREW"]);
  });

  it("splits RGB on the text (three screen draws, ±4u) for the first 120 ms only", () => {
    const p = prep(scene());
    const split = fills(opsAt(p, 0.2 + 0.05)).filter((o) => o.blend === "screen");
    expect(new Set(split.map((o) => o.fill))).toEqual(new Set(["#FF0000", "#00FF00", "#0000FF"]));
    const r = split.find((o) => o.fill === "#FF0000")!, g = split.find((o) => o.fill === "#00FF00" && o.text === r.text && o.y === r.y)!;
    expect(g.x - r.x).toBeCloseTo(DECODE_SPLIT_PX, 6);
    expect(fills(opsAt(p, 0.2 + 0.13)).some((o) => o.blend === "screen")).toBe(false);
  });

  it("draws 2u scanlines at 8 % over the line box", () => {
    const ops = opsAt(prep(scene()), 1.0);
    const g = ops.filter((o): o is Extract<DrawOp, { op: "gradient" }> => o.op === "gradient");
    expect(g).toHaveLength(1);
    expect(g[0].alpha).toBeCloseTo(SCANLINE_ALPHA, 9);
    expect(g[0].stops.length % 4).toBe(0);
  });
});

describe("decode accent", () => {
  it("grows its word to 1.3× (line re-centred), re-decodes it, then settles back", () => {
    expect(accentScale(0, 0.6)).toBe(1);
    expect(accentScale(0.2, 0.6)).toBeCloseTo(DECODE_ACCENT_SCALE, 9);
    expect(accentScale(0.6, 0.6)).toBeCloseTo(1, 9);
    const p = prep(scene());
    const plain = fills(opsAt(prep(scene(1080, 1920, { cues: [] })), 2.3));
    const grown = fills(opsAt(p, 2.3));
    const one = grown.find((o) => o.text === "ONE")!, base = plain.find((o) => o.text === "ONE")!;
    expect(one.px / base.px).toBeCloseTo(DECODE_ACCENT_SCALE, 6);
    const first = grown.find((o) => o.text === "300%")!, firstBase = plain.find((o) => o.text === "300%")!;
    expect(first.x).toBeLessThan(firstBase.x);   // the line re-flowed around the bigger word
    // Re-decode: at 2.02 the accent word is per-letter again while the rest of its line stays settled.
    const redo = fills(opsAt(p, 2.02));
    expect(redo.some((o) => o.text === "300%")).toBe(true);
    expect(redo.some((o) => o.text === "ONE")).toBe(false);
  });

  it("jolts the line with glitch slices for exactly 2 frames", () => {
    const slices = (t: number) => opsAt(prep(scene()), t).filter((o) => o.op === "rect" && o.blend === "screen");
    expect(slices(2.0).length).toBe(2);
    expect(slices(2.0 + 1 / 30 + 0.001).length).toBe(2);
    expect(slices(2.0 + 2 / 30 + 0.001).length).toBe(0);
    expect(joltAt(0, 1, hash)).not.toBe(0);
    expect(joltAt(2.5 / 30, 1, hash)).toBe(0);
  });

  it("draws standalone, 1.3× the caption size, without a decode_glitch line", () => {
    const cue = accent("b1.accent", "decode", 1.0, 1.8, { text: "revenue" });
    const s = templateScene("marker_circle", { cues: [cue] });
    const ops = fills(cueOps(s, 1.5));
    const word = ops.find((o) => o.text === "REVENUE")!;
    expect(word.px).toBeCloseTo(s.caption!.size_px * DECODE_ACCENT_SCALE, 6);
    expectInsideFrame(cueOps(s, 1.5), 1080, 1920);
  });

  it("non-Latin lines scramble with their own letters", () => {
    const words: FxWord[] = ["ПРИВЕТ", "МИР", "ДРУЗЬЯ"].map((t, i) => ({ id: `w${i}`, start: 0.2 + i * 0.3, end: 0.45 + i * 0.3, text: t, highlight: false, run: "clip" }));
    const ops = fills(opsAt(prep(scene(1080, 1920, { words, cues: [] })), 0.21));
    const pool = new Set(Array.from("ПРИВЕТМИРДРУЗЬЯ"));
    for (const o of ops) for (const c of Array.from(o.text)) expect(pool.has(c)).toBe(true);
  });

  it("is deterministic", () => {
    const a = prep(scene()), b = prep(scene());
    for (const t of [0.22, 2.01, 2.3]) expect(snap(opsAt(a, t))).toEqual(snap(opsAt(b, t)));
  });
});

describe("decode_glitch raster goldens", () => {
  it("renders at 3 times (540x960)", () => {
    for (const t of [0.23, 2.02, 2.3]) expectGolden(`decode_glitch__decode_t${t}`, renderSceneToPng(scene(540, 960), t));
  });
});

describe("decode accent growth", () => {
  it("never pushes a long line out of the frame: the growth shrinks to the safe width", () => {
    const words: FxWord[] = ["So", "incomprehensible", "wow"].map((t, i) => (
      { id: `w${i}`, start: 0.2 + i * 0.4, end: 0.55 + i * 0.4, text: t, highlight: false, run: "clip" }));
    const cue = accent("b1.accent", "decode", 0.6, 1.4, { text: "incomprehensible" });
    for (const [w, h] of [[1080, 1920], [1080, 1080], [1920, 1080]] as const) {
      const p = prep(templateScene("decode_glitch", { width: w, height: h, words, cues: [cue] }));
      const chunk = p.chunkAt(1.0)!;
      const ops = fills(opsAt(p, 1.0));
      const grown = ops.find((o) => o.text === "INCOMPREHENSIBLE")!;
      expect(grown.px).toBeGreaterThanOrEqual(chunk.px);
      // Inside the safe sides, or no wider than the layout already placed the line (a word that fills the frame).
      const left = Math.min(0.06 * w, ...chunk.words.map((x) => x.x));
      const right = Math.max(w - 0.06 * w, ...chunk.words.map((x) => x.x + x.w));
      for (const o of ops) {
        expect(o.x).toBeGreaterThanOrEqual(left - 1);
        expect(o.x + fakeMeasureHost.measure(o.text, o.font, o.px)).toBeLessThanOrEqual(right + 1);
      }
      expect(drawerErrors(p)).toEqual([]);
    }
  });
});
