// @vitest-environment node
import { describe, expect, it } from "vitest";
import { drawerErrors } from "../frame";
import { accentDrawerFor } from "../registry";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import type { FxWord } from "../types";
import {
  KINETIC_EMPHASIS, KINETIC_FILL, KINETIC_HEAD_GAP, KINETIC_MAX_STACK, KINETIC_SLIDE, KINETIC_SLIDE_FROM, KINETIC_TOP,
  kineticBand, kineticLayout, kineticRows,
} from "./kinetic_stack";
import type { ZoomCue } from "../types";
import { accent, cueOps, expectInsideFrame, faceTrack, opsAt, prep, snap, templateScene, texts } from "./wp8.testkit";

const HOOK = "Our revenue grew 300% in one year and honestly nobody saw that coming";
const CUE = accent("b0.accent", "kinetic", 0.2, 4.6, { text: HOOK });
// No face track unless a test gives one: the layout tests below check the stack at its home band.
const scene = (w = 1080, h = 1920, extra = {}) =>
  templateScene("kinetic_stack", { width: w, height: h, cues: [CUE], face_track: null, ...extra });
const TIMES = [0.21, 0.24, 0.6, 1.4, 2.5, 3.9];

describe("kinetic_stack frameOps", () => {
  it("snapshots at 1080x1920, inside the frame", () => {
    for (const t of TIMES) {
      const ops = cueOps(scene(), t);
      expect(snap(ops)).toMatchSnapshot(`t=${t}`);
      expectInsideFrame(ops, 1080, 1920);
    }
  });

  it("1920x1080 stays inside the frame", () => {
    for (const t of [1.4, 3.9]) expectInsideFrame(cueOps(scene(1920, 1080), t), 1920, 1080);
  });

  it("hides the normal captions while it plays", () => {
    expect(accentDrawerFor("kinetic")!.hidesCaptions).toBe(true);
    const ops = texts(opsAt(prep(scene()), 1.4));
    expect(ops.every((o) => o.font === "FX-THEBOLDFONT" && o.text === o.text.toUpperCase())).toBe(true);
    expect(ops.some((o) => o.text === "Our")).toBe(false);
  });
});

describe("kinetic layout", () => {
  for (const [w, h] of [[1080, 1920], [1080, 1080], [1920, 1080]] as const) {
    it(`rows fill 88 % ± 1 % of the width and every screen's stack stays within 0.8H (${w}x${h})`, () => {
      const p = prep(scene(w, h));
      const { rows } = kineticLayout(p, CUE);
      expect(rows.length).toBeGreaterThan(2);
      expect(rows.filter((r) => r.filled).length).toBeGreaterThan(0);
      for (const row of rows) {
        expect(row.words.length).toBeGreaterThanOrEqual(1);
        expect(row.words.length).toBeLessThanOrEqual(3);
        const capped = !row.filled;
        if (!capped) expect(Math.abs(row.width / w - KINETIC_FILL)).toBeLessThanOrEqual(0.01);
        const left = row.words[0].x, right = row.words[row.words.length - 1].x + row.words[row.words.length - 1].w;
        if (!capped) expect(Math.abs((right - left) / w - KINETIC_FILL)).toBeLessThanOrEqual(0.01);
      }
      for (const screen of new Set(rows.map((r) => r.screen))) {
        const on = rows.filter((r) => r.screen === screen);
        const top = Math.min(...on.map((r) => r.top)), bottom = Math.max(...on.map((r) => r.top + r.height));
        expect(bottom - top).toBeLessThanOrEqual(KINETIC_MAX_STACK * h + 1e-6);
        expect(top).toBeCloseTo(0.14 * h, 6);
      }
    });
  }

  it("emphasis words are 1.6× their row-mates and rows alternate white / accent", () => {
    const { rows } = kineticLayout(prep(scene()), CUE);
    const row = rows.find((r) => r.words.some((x) => x.emphasis) && r.words.some((x) => !x.emphasis))!;
    const big = row.words.find((x) => x.emphasis)!, small = row.words.find((x) => !x.emphasis)!;
    expect(big.px / small.px).toBeCloseTo(KINETIC_EMPHASIS, 9);
    expect(rows.map((r) => r.color).slice(0, 4)).toEqual(["white", "accent", "white", "accent"]);
  });

  it("wraps to a second screen when the stack would pass 80 % of the height", () => {
    const long = accent("b0.accent", "kinetic", 0.2, 4.6, { text: HOOK });
    const words: FxWord[] = HOOK.split(" ").map((t, i) => ({ id: `w${i}`, start: 0.2 + i * 0.36, end: 0.5 + i * 0.36, text: t, highlight: false, run: "clip" }));
    const p = prep(scene(1080, 1080, { words }));
    const { rows, screens } = kineticLayout(p, long);
    expect(screens.length).toBeGreaterThan(1);
    // A later screen replaces the earlier one.
    const second = rows.find((r) => r.screen === 1)!;
    const shown = texts(cueOps(scene(1080, 1080, { words }), second.start + 0.1)).map((o) => o.text);
    expect(shown).toContain(second.words[0].text);
    expect(shown).not.toContain(rows[0].words[0].text);
  });

  it("groups 1–3 words per row", () => {
    const rows = kineticRows("A B C D E F G".split(" ").map((text) => ({ text, emphasis: false })));
    expect(rows.every((r) => r.length >= 1 && r.length <= 3)).toBe(true);
    expect(kineticRows([{ text: "extraordinarily", emphasis: true }, { text: "big", emphasis: false }])).toHaveLength(2);
  });
});

describe("kinetic motion", () => {
  it("each row slides in on its first word: 60 ms from 30u left, alpha 0 → 1", () => {
    const p = prep(scene());
    const row = kineticLayout(p, CUE).rows[1];
    const at = (t: number) => texts(cueOps(scene(), t)).filter((o) => o.text === row.words[0].text && o.fill !== null);
    expect(at(row.start - 0.01)).toHaveLength(0);
    const first = at(row.start)[0];
    expect(first.alpha).toBe(0);
    expect(first.x).toBeCloseTo(row.words[0].x - KINETIC_SLIDE_FROM, 6);
    const done = at(row.start + KINETIC_SLIDE)[0];
    expect(done.alpha).toBe(1);
    expect(done.x).toBeCloseTo(row.words[0].x, 6);
  });

  it("with approximate timing a row appears with its caption line", () => {
    // Two "lines" spread by character count (contiguous, one duration per character).
    const line = (text: string, start: number, end: number, offset: number): FxWord[] => {
      const tokens = text.split(" "), total = tokens.reduce((s, x) => s + x.length, 0);
      let c = 0;
      return tokens.map((t, i) => {
        const a = start + ((end - start) * c) / total; c += t.length;
        return { id: `w${offset + i}`, start: Math.round(a * 1000) / 1000, end: Math.round((start + ((end - start) * c) / total) * 1000) / 1000, text: t, highlight: false, run: "clip" };
      });
    };
    const words = [...line("Our revenue grew 300% in", 0.2, 2.0, 0), ...line("one year and honestly nobody", 2.0, 4.0, 5)];
    const p = prep(scene(1080, 1920, { words }));
    const { rows } = kineticLayout(p, CUE);
    expect(new Set(rows.map((r) => r.start))).toEqual(new Set([0.2, 2.0]));
  });

  it("with approximate timing every screen still gets its turn (a later screen never starts with the earlier one)", () => {
    // One approximate caption line holding the whole sentence: every row shares one start.
    const tokens = HOOK.split(" "), total = tokens.reduce((n, x) => n + x.length, 0);
    let c = 0;
    const words: FxWord[] = tokens.map((t, i) => {
      const a = 0.2 + (4.0 * c) / total; c += t.length;
      return { id: `w${i}`, start: Math.round(a * 1000) / 1000, end: Math.round((0.2 + (4.0 * c) / total) * 1000) / 1000, text: t, highlight: false, run: "clip" };
    });
    const s = scene(1080, 1080, { words });
    const { rows, screens } = kineticLayout(prep(s), CUE);
    expect(screens.length).toBeGreaterThan(1);
    for (let i = 1; i < screens.length; i++) expect(screens[i]).toBeGreaterThan(screens[i - 1] + 0.3);
    const first = texts(cueOps(s, 0.4)).map((o) => o.text);
    expect(first).toContain(rows[0].words[0].text);
    const second = rows.find((r) => r.screen === 1)!;
    expect(first).not.toContain(second.words[0].text);
    expect(texts(cueOps(s, screens[1] + 0.1)).map((o) => o.text)).toContain(second.words[0].text);
  });

  it("fades out over the last 120 ms and is deterministic", () => {
    expect(texts(cueOps(scene(), 4.55)).every((o) => o.alpha < 1)).toBe(true);
    const a = prep(scene()), b = prep(scene());
    for (const t of TIMES) expect(snap(opsAt(a, t))).toEqual(snap(opsAt(b, t)));
    expect(drawerErrors(a)).toEqual([]);
  });
});

describe("kinetic_stack raster goldens", () => {
  it("renders at 3 times (540x960)", () => {
    for (const t of [0.6, 1.4, 3.9]) expectGolden(`kinetic_stack__kinetic_t${t}`, renderSceneToPng(scene(540, 960), t));
  });
  it("renders landscape (960x540)", () => {
    expectGolden("kinetic_stack__kinetic_landscape_t2.5", renderSceneToPng(scene(960, 540), 2.5));
  });
});

describe("kinetic stack keeps off the head", () => {
  // A podcast close-up: the head fills the upper middle of a 9:16 frame, where the stack's home band starts.
  const HEAD = { x: 0.3, y: 0.18, w: 0.4, h: 0.31 };
  const withFace = (face: typeof HEAD, extra = {}) => scene(1080, 1920, { face_track: faceTrack(5, () => face), ...extra });
  const rowsOverlap = (rows: { top: number; height: number }[], top: number, bottom: number) =>
    rows.some((r) => r.top < bottom && r.top + r.height > top);

  it("without a face track the stack starts at its home", () => {
    expect(kineticBand(prep(scene()), CUE).top).toBeCloseTo(KINETIC_TOP * 1920, 6);
  });

  it("moves beneath the chin when the head is in its band, and never covers the head", () => {
    const p = prep(withFace(HEAD));
    const band = kineticBand(p, CUE);
    const chin = (HEAD.y + HEAD.h) * 1920;
    expect(band.top).toBeCloseTo(chin + KINETIC_HEAD_GAP * 1920, 6);
    const { rows } = kineticLayout(p, CUE);
    expect(rows.length).toBeGreaterThan(0);
    expect(rowsOverlap(rows, HEAD.y * 1920, chin)).toBe(false);
    for (const t of TIMES) expectInsideFrame(cueOps(withFace(HEAD), t), 1080, 1920);
  });

  it("goes above a head low in the frame", () => {
    const low = { x: 0.3, y: 0.6, w: 0.4, h: 0.2 };
    const p = prep(withFace(low));
    const band = kineticBand(p, CUE);
    expect(band.bottom).toBeCloseTo((low.y - KINETIC_HEAD_GAP) * 1920, 6);
    expect(rowsOverlap(kineticLayout(p, CUE).rows, low.y * 1920, (low.y + low.h) * 1920)).toBe(false);
  });

  it("keeps its home when no band beside the head is tall enough, or the face is a background face", () => {
    expect(kineticBand(prep(withFace({ x: 0.1, y: 0.1, w: 0.8, h: 0.75 })), CUE).top).toBeCloseTo(KINETIC_TOP * 1920, 6);
    expect(kineticBand(prep(withFace({ x: 0.45, y: 0.3, w: 0.04, h: 0.03 })), CUE).top).toBeCloseTo(KINETIC_TOP * 1920, 6);
  });

  it("counts the head as a punch-in during the cue shows it", () => {
    const zoom: ZoomCue = { id: "z54.zoom", kind: "zoom", start: 1.8, end: 4.6, seed: 1, beat: "b0", origin: "auto",
      mode: "punch", scale: 1.2, anchor: "face" };
    const still = kineticBand(prep(withFace(HEAD)), CUE);
    const zoomed = kineticBand(prep(withFace(HEAD, { cues: [CUE, zoom] })), CUE);
    // Scaled about the face centre, the chin drops by 0.2 × half the face height.
    expect(zoomed.top - still.top).toBeCloseTo(0.2 * (HEAD.h / 2) * 1920, 0);
  });
});
