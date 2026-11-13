import { describe, expect, it } from "vitest";
import { captionKey, counterFormat, decimalsOf, faceInterpolator, formatNumber, prepareScene } from "./scene";
import { clockAt } from "./frame";
import type { AccentCue, FxScene } from "./types";
import { captionStyle, fakeMeasureHost, makeScene, sampleWords } from "./test-utils/fake-host";

const prep = (s: Partial<FxScene> = {}) => prepareScene(makeScene(s), fakeMeasureHost);

describe("caption chunks", () => {
  it("groups words like the studio's captionGroups and keys them by run and first word", () => {
    const p = prep();
    expect(p.chunks.map((c) => c.words.map((w) => w.id))).toEqual([
      ["w0", "w1", "w2"], ["w3", "w4", "w5"], ["w6", "w7", "w8"], ["w9", "w10", "w11"], ["w12"],
    ]);
    expect(p.chunks[1].key).toBe(JSON.stringify(["clip", "w3"]));
    expect(captionKey({ run: "seg-2", id: "w9" })).toBe('["seg-2","w9"]');
  });

  it("counts empty words when grouping (studio keys stay aligned) but never lays them out", () => {
    const words = sampleWords("one two three four five six", 0, 0.3);
    words[3] = { ...words[3], text: " " };
    const p = prep({ words, caption: captionStyle({ words_per_line: 3 }) });
    expect(p.chunks.map((c) => c.key)).toEqual(['["clip","w0"]', '["clip","w3"]']);
    expect(p.chunks[1].words.map((w) => w.id)).toEqual(["w4", "w5"]);
    const blank = sampleWords("a b c", 0, 0.3).map((w) => ({ ...w, text: "" }));
    expect(prep({ words: [...blank, ...sampleWords("d", 1, 0.3).map((w) => ({ ...w, id: "d" }))] }).chunks.map((c) => c.key))
      .toEqual(['["clip","d"]']);
  });

  it("widens word boxes by the template's letter spacing", () => {
    const plain = prep({ template: "marker_circle" }).chunks[0].words[1];
    const spaced = prep({ template: "decode_glitch" }).chunks[0].words[1];
    expect(spaced.text).toBe(plain.text);
    expect(spaced.w - plain.w).toBeCloseTo(0.04 * spaced.h * (Array.from(spaced.text).length - 1), 6);
  });

  it("bridges short gaps to the next chunk and never overlaps it", () => {
    const p = prep();
    for (let i = 0; i + 1 < p.chunks.length; i++) expect(p.chunks[i].end).toBeCloseTo(p.chunks[i + 1].start, 9);
    const words = sampleWords("one two three four", 0, 0.3);
    words[3] = { ...words[3], start: 3, end: 3.4 };
    const gap = prep({ words, caption: captionStyle({ words_per_line: 3 }) });
    expect(gap.chunks[0].end).toBeCloseTo(words[2].end, 9);
    expect(gap.chunkAt(2)).toBeNull();
    expect(gap.chunkAt(3.1)?.key).toBe('["clip","w3"]');
  });

  it("lays words out centred on the caption position, uppercased when asked", () => {
    const p = prep({ caption: captionStyle({ uppercase: true, y: 0.5 }) });
    const c = p.chunks[0];
    expect(c.words.map((w) => w.text)).toEqual(["OUR", "REVENUE", "GREW"]);
    const left = c.words[0].x, right = c.words[2].x + c.words[2].w;
    expect((left + right) / 2).toBeCloseTo(540, 6);
    expect(c.words[0].y + c.words[0].h / 2).toBeCloseTo(960, 6);
    expect(c.centerY).toBe(960);
  });

  it("wraps at W − 120u, stacks lines 1.2 px apart and shrinks a chunk whose single word is wider than that", () => {
    const words = sampleWords("supercalifragilistic expialidocious antidisestablishmentarianism", 0, 0.5);
    const p = prep({ words, caption: captionStyle({ size_px: 90 }) });
    const lines = new Set(p.chunks[0].words.map((w) => w.line));
    expect(lines.size).toBe(3);
    const [a, b] = p.chunks[0].words;
    expect(p.chunks[0].px).toBeLessThan(90);
    expect(b.y - a.y).toBeCloseTo(p.chunks[0].px * 1.2, 6);
    for (const w of p.chunks[0].words) expect(w.w).toBeLessThanOrEqual(1080 - 120 + 1e-6);
  });

  it("keeps an unplaced caption inside the safe area and honours studio placements", () => {
    const low = prep({ caption: captionStyle({ y: 0.99 }) });
    const c = low.chunks[0];
    expect(c.words[0].y + c.words[0].h).toBeLessThanOrEqual(1920 - 192 + 1e-6);
    const placed = prep({ caption: captionStyle({ y: 0.7, placements: [{ key: '["clip","w3"]', x: 0.1, y: -0.2, scale: 1.5 }] }) });
    const moved = placed.chunks[1];
    expect(moved.centerX).toBeCloseTo(1080 * 0.6, 6);
    expect(moved.centerY).toBeCloseTo(1920 * 0.5, 6);
    expect(moved.px).toBeCloseTo(78 * 1.5, 6);
    expect(placed.chunks[0].scale).toBe(1);
  });

  it("enforces the 34u minimum text size and draws nothing for legacy templates or disabled captions", () => {
    expect(prep({ caption: captionStyle({ size_px: 12 }) }).chunks[0].px).toBe(34);
    expect(prep({ template: "hormozi" }).chunks).toEqual([]);
    expect(prep({ caption: captionStyle({ enabled: false }) }).chunks).toEqual([]);
    expect(prep({ caption: null }).captionFamily).toBeNull();
    expect(prep().captionFamily).toBe("FX-TikTokSans-Bold");
  });

  it("scales layout with u at landscape sizes", () => {
    const p = prep({ width: 1920, height: 1080, caption: captionStyle({}, 1080) });
    expect(p.u).toBe(1);
    expect(p.chunks[0].centerX).toBe(960);
  });
});

describe("cues", () => {
  it("sorts by (start, id) and indexes by kind", () => {
    const base = { seed: 1, beat: "", origin: "auto" as const, end: 2 };
    const p = prep({ cues: [
      { ...base, id: "b", kind: "shake", start: 1, amp: 4, freq: 18 },
      { ...base, id: "a", kind: "shake", start: 1, amp: 4, freq: 18 },
      { ...base, id: "c", kind: "flash", start: 0.5, color: "#FFFFFF", peak: 1 },
    ] });
    expect(p.cues.map((c) => c.id)).toEqual(["c", "a", "b"]);
    expect(p.cuesByKind.get("shake")!.map((c) => c.id)).toEqual(["a", "b"]);
  });

  it("formats counters with separators, the spoken decimals, prefix and suffix", () => {
    const cue = (value: number | null, text = "", prefix = "", suffix = ""): AccentCue => ({
      id: "x", kind: "accent", start: 0, end: 1, seed: 0, beat: "", origin: "auto", variant: "counter", text, emoji: "",
      doodle: "sparkles", value, prefix, suffix,
    });
    expect(counterFormat(cue(1250.5, "", "$")).format(1250.5)).toBe("$1,250.5");
    expect(counterFormat(cue(1250.5, "", "$")).format(37.04)).toBe("$37.0");
    expect(counterFormat(cue(300, "", "", "%")).format(300)).toBe("300%");
    expect(counterFormat(cue(null, "2,000,000 users")).target).toBe(2000000);
    expect(counterFormat(cue(2.5, "$2.50", "$")).format(2.5)).toBe("$2.50");
    expect(counterFormat(cue(2.5, "two and a half")).format(2.5)).toBe("2.5");
    expect(decimalsOf(2.25)).toBe(2);
    expect(decimalsOf(3)).toBe(0);
    expect(formatNumber(-1234567.891, 2)).toBe("-1,234,567.89");
    const p = prep({ cues: [cue(42)] });
    expect(p.counters.get("x")!.format(42)).toBe("42");
  });
});

describe("face track", () => {
  const track = (samples: [number, number[][]][]) => faceInterpolator({ width: 1000, height: 2000, face_track: { version: 1, space: "output", fps: 6, samples } });
  const row = (x: number) => [x, 0.2, 0.2, 0.1, 0.9];

  it("interpolates linearly in output px", () => {
    const at = track([[0, [row(0.1)]], [0.4, [row(0.3)]]]);
    expect(at(0.2)).toEqual({ x: expect.closeTo(200, 6), y: 400, w: 200, h: 200, landmarks: null });
  });

  it("holds at the ends for up to 0.5 s, then gives up", () => {
    const at = track([[1, [row(0.1)]], [1.2, [row(0.2)]]]);
    expect(at(0.6)?.x).toBeCloseTo(100, 6);
    expect(at(0.4)).toBeNull();
    expect(at(1.6)?.x).toBeCloseTo(200, 6);
    expect(at(1.8)).toBeNull();
  });

  it("is null across gaps longer than 0.5 s and uses the nearest sample when one side has no face", () => {
    const at = track([[0, [row(0.1)]], [2, [row(0.5)]]]);
    expect(at(1)).toBeNull();
    expect(at(0.05)?.x).toBeCloseTo(100, 6);
    const lost = track([[0, [row(0.1)]], [0.3, []], [0.6, [row(0.3)]]]);
    expect(lost(0.1)?.x).toBeCloseTo(100, 6);
    expect(lost(0.2)).toBeNull();
  });

  it("converts landmarks to output px", () => {
    const lm = [0.1, 0.2, 0.2, 0.3, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9];
    const at = track([[0, [[0.1, 0.1, 0.2, 0.2, 0.9, ...lm.slice(0, 10)]]]]);
    expect(at(0)!.landmarks).toEqual([100, 400, 200, 600, 300, 800, 500, 1200, 700, 1600]);
  });

  it("is null without a track", () => {
    expect(prep().faceAt(1)).toBeNull();
  });
});

describe("clock", () => {
  it("maps F to E through the warp (constant across a freeze hold)", () => {
    const p = prep({ edit_duration: 4, out_duration: 4.5, warp: [
      { e0: 0, e1: 2, o0: 0, rate: 1, hold: 0 },
      { e0: 2, e1: 2, o0: 2, rate: 0, hold: 0.5 },
      { e0: 2, e1: 4, o0: 2.5, rate: 1, hold: 0 },
    ] });
    expect(clockAt(p, 2.2)).toEqual({ e: 2, o: 2.2, frame: 66 });
    expect(clockAt(p, 3).e).toBeCloseTo(2.5, 9);
    expect(p.outFrames).toBe(135);
  });

  it("windows: hook, B-roll and matte lookups", () => {
    const p = prep({ hook_window: [0.12, 3], broll_windows: [[1, 2]], matte_windows: [{ start: 2, end: 3, person: true }] });
    expect(p.hookActive(0.1)).toBe(false);
    expect(p.hookActive(1)).toBe(true);
    expect(p.inBroll(1.5)).toBe(true);
    expect(p.inBroll(2)).toBe(false);
    expect(p.matteAt(2.5)?.person).toBe(true);
    expect(p.matteAt(3)).toBeNull();
  });
});
