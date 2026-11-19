// @vitest-environment node
// The hook title (hook.ts): tokens and markers, balanced lines, the two styles, the fixed position, motion,
// and the drawn frame.
import { describe, expect, it } from "vitest";
import { clockAt, drawerErrors, frameOps } from "./frame";
import type { DrawOp } from "./host";
import { HOOK, HOOK_PLACE, HOOK_STACK, HOOK_VARIANTS, hookBlock, hookVariant, stackOutline, hookBounds, hookLayout, hookMarkColor, hookMarks, hookMotion, hookOps, hookTokens } from "./hook";
import { prepareScene } from "./scene";
import { captionStyle, fakeMeasure, fakeMeasureHost, makeScene, sampleWords } from "./test-utils/fake-host";
import { expectGolden, renderSceneToPng } from "./test-utils/golden";
import { bareScene, cue, snap } from "./picture/fixtures";
import type { FaceTrack, FxHookTitle, FxScene } from "./types";

type RectOp = Extract<DrawOp, { op: "rect" }>;
type PathOp = Extract<DrawOp, { op: "path" }>;
const V = HOOK_STACK;
type TextOp = Extract<DrawOp, { op: "text" }>;

const W = 1080, H = 1920;
const hook = (overrides: Partial<FxHookTitle> = {}): FxHookTitle => ({
  text: "I deleted a mega star's episode", font: "Poppins-ExtraBold", accent: "#34FD87", look: "bold", start: 0.12, end: 4,
  x: 0, y: 0, scale: 1, ...overrides,
});
const prep = (scene: FxScene, detachHook = false) => prepareScene(scene, fakeMeasureHost, { detachHook });
const measure = (text: string, px: number) => fakeMeasure(text, "", px);

/** One face (normalised output box with landmarks) for the whole clip. */
function face(x: number, y: number, w: number, h: number, duration = 6): FaceTrack {
  const row = [x, y, w, h, 0.97, x + 0.3 * w, y + 0.38 * h, x + 0.7 * w, y + 0.38 * h, x + 0.5 * w, y + 0.55 * h,
    x + 0.35 * w, y + 0.75 * h, x + 0.65 * w, y + 0.75 * h];
  const samples: [number, number[][]][] = [];
  for (let i = 0; i <= duration * 6; i++) samples.push([i / 6, [row]]);
  return { version: 1, space: "output", fps: 6, samples };
}
const lookCaptions = (y: number, extra: Partial<FxScene["caption"] & object> = {}) => captionStyle({
  template: "highlighter_swipe", look: "bold", font: "Poppins-ExtraBold", size_px: 72, stroke_color: null, stroke_px: 0,
  background: "#170904D9", accent: "#34FD87", y, ...extra,
});

describe("tokens and markers", () => {
  it("splits at spaces; text without spaces breaks between characters, never before closing punctuation", () => {
    expect(hookTokens("  3am,  a duvet ")).toEqual([{ text: "3am,", glue: false }, { text: "a", glue: true }, { text: "duvet", glue: true }]);
    const cjk = hookTokens("凌晨三点，没有计划");
    expect(cjk.map((t) => t.text)).toEqual(["凌", "晨", "三", "点，", "没", "有", "计", "划"]);
    expect(cjk.every((t) => !t.glue)).toBe(true);
    expect(hookTokens("赚了100万 in a day").map((t) => [t.text, t.glue])).toEqual(
      [["赚", false], ["了", false], ["100", false], ["万", false], ["in", true], ["a", true], ["day", true]]);
  });

  it("marks numbers first, then power words, at most two, in text order", () => {
    const marks = (title: string) => hookMarks(hookTokens(title)).map((i) => hookTokens(title)[i].text);
    expect(marks("90% of podcast guests become real friends")).toEqual(["90%"]);
    expect(marks("Nobody saw the $40k mistake in 2019 coming")).toEqual(["$40k", "2019"]);
    expect(marks("The secret nobody tells you")).toEqual(["secret", "nobody"]);
    expect(marks("Never trust the first 3 offers")).toEqual(["Never", "3"]);
    expect(marks("It just tastes like texture")).toEqual([]);
  });

  it("keeps a bright accent and replaces one that would not read behind dark type", () => {
    expect(hookMarkColor("#34fd87")).toBe("#34FD87");
    expect(hookMarkColor("#FFFFFF")).toBe(HOOK.mark);
    expect(hookMarkColor("#102040")).toBe(HOOK.mark);
    expect(hookMarkColor("nope")).toBe(HOOK.mark);
  });
});

describe("lines", () => {
  const block = (title: string, base = 80, min = 58) => hookBlock(title, base, min, 0.88 * W, measure)!;

  it("one line for a short title, two balanced lines otherwise, three before the type shrinks", () => {
    expect(block("Why I quit").lines.map((l) => l.text)).toEqual(["Why I quit"]);
    const two = block("I deleted a mega star's episode");
    expect(two.lines.map((l) => l.text)).toEqual(["I deleted a mega", "star's episode"]);
    expect(two.px).toBe(80);
    const three = block("Nobody tells you the truth about what 90% of podcast guests want");
    expect(three.lines).toHaveLength(3);
    expect(three.px).toBeGreaterThanOrEqual(58);
    for (const b of [two, three]) {
      expect(b.w).toBeLessThanOrEqual(0.88 * W + 1e-6);
      for (const line of b.lines) expect(line.w).toBeLessThanOrEqual(b.w - 2 * V.padX * b.px + 1e-6);
      const widths = b.lines.map((l) => l.w);
      expect(Math.min(...widths) / Math.max(...widths)).toBeGreaterThan(0.6);   // balanced, no orphan
    }
  });

  it("never goes under the minimum size unless a single word is wider than the card", () => {
    const long = block("Nobody tells you the truth about what 90% of podcast guests want today ok");
    expect(long.px).toBeGreaterThanOrEqual(58 - 1e-6);
    const word = block("Pneumonoultramicroscopicsilicovolcanoconiosis");
    expect(word.lines).toHaveLength(1);
    expect(word.px).toBeLessThan(58);
    expect(word.w).toBeLessThanOrEqual(0.88 * W + 1e-6);
  });

  it("a stack is one 1.4 em label per line, 0.42 em wider than the text on each side", () => {
    const b = block("I deleted a mega star's episode");
    expect(b.h).toBeCloseTo(2 * 1.4 * 80, 6);
    expect(b.w).toBeCloseTo(Math.max(...b.lines.map((l) => l.w)) + 2 * 0.42 * 80, 6);
    expect(b.lines[1].y - b.lines[0].y).toBeCloseTo(1.4 * 80, 6);
  });

  it("labels of nearly the same width share one edge; a real step keeps both widths", () => {
    const near = block("I quit my job to cook ramens");
    expect(Math.abs(near.lines[0].w - near.lines[1].w)).toBeGreaterThan(0);
    expect(Math.abs(near.lines[0].w - near.lines[1].w)).toBeLessThan(2 * HOOK.snap * 80);
    expect(near.lines[0].hw).toBe(near.lines[1].hw);
    expect(near.lines[0].hw).toBeCloseTo(Math.max(near.lines[0].w, near.lines[1].w) / 2 + 0.42 * 80, 6);
    const far = block("I deleted a mega star's episode");
    expect(Math.abs(far.lines[0].w - far.lines[1].w)).toBeGreaterThan(2 * HOOK.snap * 80);
    expect(far.lines[0].hw).not.toBe(far.lines[1].hw);
    const step = block("Nobody tells you the truth about what 90% of podcast guests want");
    for (const line of step.lines) expect(line.hw).toBeGreaterThanOrEqual(line.w / 2 + 0.42 * step.px - 1e-6);
  });

  it("outlined type is larger, set tighter and has no label", () => {
    const o = HOOK_VARIANTS.slam;
    const b = hookBlock("I deleted a mega star's episode", o.sizeU, o.minSizeU, 0.88 * W, measure, o)!;
    expect(b.lines[1].y - b.lines[0].y).toBeCloseTo(o.lineHeight * b.px, 6);
    expect(b.h).toBeCloseTo(b.lines.length * o.lineHeight * b.px + 2 * o.padY * b.px, 6);
  });

  it("the outline of a stack: one rounded shape, stepping where the widths differ", () => {
    const pts = stackOutline([100, 100, 60], 50, -75, 10);
    const xs = pts.filter((_, i) => i % 2 === 0), ys = pts.filter((_, i) => i % 2 === 1);
    expect(Math.max(...xs)).toBeCloseTo(100, 6);
    expect(Math.min(...xs)).toBeCloseTo(-100, 6);
    expect(Math.min(...ys)).toBeCloseTo(-75, 6);
    expect(Math.max(...ys)).toBeCloseTo(75, 6);
    // Two equal lines are one edge: 4 corners a side, 9 points each.
    expect(pts.length / 2).toBe(8 * 9);
    // Symmetric about the centre line.
    expect(xs.reduce((a, b) => a + b, 0)).toBeCloseTo(0, 6);
  });

  it("the marker covers the word, not its comma", () => {
    const b = block("3am, a duvet, and zero plan");
    const word = b.lines[0].words[0];
    expect(word.text).toBe("3am,");
    expect(word.mark).toBe(0);
    expect(word.mw).toBeCloseTo(measure("3am", 80), 6);
    expect(word.mx).toBe(word.x);
  });
});

describe("size", () => {
  const sized = (title: string) => hookBlock(title, 80, 58, 0.88 * W, measure, HOOK_STACK, 80 * HOOK.grow)!;

  it("follows the title: a few words grow, two short lines sit in between, a long title shrinks before a third line", () => {
    const short = sized("Why I quit");
    expect(short.lines).toHaveLength(1);
    expect(short.px).toBeCloseTo(96, 6);                       // 1.2 times the base
    const two = sized("I deleted a mega star's episode");
    expect(two.lines).toHaveLength(2);
    expect(two.px).toBeCloseTo(88, 6);
    const long = sized("Nobody tells you what podcast guests want");
    expect(long.lines).toHaveLength(2);
    expect(long.px).toBeLessThan(80);
    expect(long.px).toBeGreaterThanOrEqual(HOOK.twoLineFloor * 80 - 1e-6);
    const longer = sized("Nobody tells you the truth about what 90% of podcast guests want from you");
    expect(longer.lines).toHaveLength(3);
    expect(longer.px).toBeLessThanOrEqual(80);
    expect(longer.px).toBeGreaterThanOrEqual(58 - 1e-6);
    for (const b of [short, two, long, longer]) expect(b.w).toBeLessThanOrEqual(0.88 * W + 1e-6);
  });
});

describe("placement", () => {
  const layoutOf = (scene: FxScene) => hookLayout(prep(scene))!;
  const top = (l: { cy: number; h: number }) => (l.cy - l.h / 2) / H;

  it("sits at one fixed place: top edge at 11 % of the height, centred", () => {
    const l = layoutOf(bareScene({ hook: hook() }));
    expect(top(l)).toBeCloseTo(HOOK_PLACE.top, 6);
    expect(HOOK_PLACE.top).toBe(0.11);
    expect(l.cx).toBe(W / 2);
  });

  it("stays at the top whatever else is on screen: captions, any template, any line count, a face it does not cover", () => {
    const base = layoutOf(bareScene({ hook: hook() }));
    const scenes: FxScene[] = [
      bareScene({ hook: hook(), face_track: face(0.3, 0.3, 0.4, 0.22) }),                     // a face below it
      makeScene({ template: "highlighter_swipe", caption: lookCaptions(0.2), hook: hook() }),  // captions under it
      makeScene({ template: "highlighter_swipe", caption: lookCaptions(0.55, { rows: [{ start: 0, end: 2, y: 0.76 }, { start: 2, end: 6, y: 0.5 }] }), hook: hook() }),
    ];
    for (const scene of scenes) {
      const l = layoutOf(scene);
      expect(l.slot).toBe("top");
      expect(l.cy).toBeCloseTo(base.cy, 6);
      expect([l.start, l.end]).toEqual([0.12, 4]);
    }
    for (const template of ["slam", "mrbeast", "neon", "tiktok"]) {
      for (const text of ["Why I quit", "Nobody tells you the truth about what 90% of podcast guests want"]) {
        expect(top(layoutOf(bareScene({ hook: hook({ template, text }) })))).toBeCloseTo(HOOK_PLACE.top, 6);
      }
    }
  });

  it("goes right beneath a head it would cover at the top", () => {
    // The upper person of a stacked two-shot: head from 6 % to 20 % of the height.
    const l = layoutOf(bareScene({ hook: hook(), face_track: face(0.3, 0.06, 0.4, 0.14) }));
    expect(l.slot).toBe("beneath");
    expect(top(l)).toBeCloseTo(0.06 + 0.14 + HOOK_PLACE.gap, 6);
    expect(l.end).toBe(4);
    // A head it only grazes (under 18 % of the box) does not move it.
    expect(layoutOf(bareScene({ hook: hook(), face_track: face(0.3, 0.22, 0.4, 0.2) })).slot).toBe("top");
    // Background people do not count.
    expect(layoutOf(bareScene({ hook: hook(), face_track: face(0.45, 0.12, 0.08, 0.04) })).slot).toBe("top");
  });

  it("stays at the top when there is no room beneath: a close-up down to the captions, or another head there", () => {
    const closeUp = makeScene({ template: "highlighter_swipe", caption: lookCaptions(0.55), hook: hook(), face_track: face(0.15, 0.04, 0.7, 0.46) });
    expect(layoutOf(closeUp).slot).toBe("top");
    const tall = bareScene({ hook: hook(), face_track: face(0.15, 0.04, 0.7, 0.56) });   // would end under 64 %
    expect(layoutOf(tall).slot).toBe("top");
    const two: FaceTrack = { version: 1, space: "output", fps: 6, samples: [] };
    const row = (y: number, h: number) => face(0.3, y, 0.4, h).samples[0][1][0];
    for (let i = 0; i <= 36; i++) two.samples.push([i / 6, [row(0.06, 0.14), row(0.22, 0.16)]]);
    expect(layoutOf(bareScene({ hook: hook(), face_track: two })).slot).toBe("top");
  });

  it("is judged on its opening shot and leaves at the cut when beneath only works until then", () => {
    // A stacked two-shot until 2.5 s, then a close-up filling the top and middle.
    const track: FaceTrack = { version: 1, space: "output", fps: 6, samples: [] };
    for (let i = 0; i <= 36; i++) {
      const t = i / 6;
      track.samples.push([t, [t < 2.5 ? face(0.3, 0.06, 0.4, 0.14).samples[0][1][0] : face(0.15, 0.1, 0.7, 0.42).samples[0][1][0]]]);
    }
    const p = prep(bareScene({ hook: hook(), face_track: track }));
    const l = hookLayout(p)!;
    expect(l.slot).toBe("beneath");
    expect(top(l)).toBeCloseTo(0.06 + 0.14 + HOOK_PLACE.gap, 6);
    expect(l.end).toBeCloseTo(2.5, 6);
    expect(hookOps(p, 2.4).length).toBeGreaterThan(0);
    expect(hookOps(p, 2.51)).toEqual([]);
    // A cut before 2.2 s of title is not used: the title stays home for its whole window.
    const early: FaceTrack = { ...track, samples: track.samples.map(([t]) => [t, [t < 1.5 ? face(0.3, 0.06, 0.4, 0.14).samples[0][1][0] : face(0.15, 0.1, 0.7, 0.42).samples[0][1][0]]]) };
    const kept = layoutOf(bareScene({ hook: hook(), face_track: early }));
    expect([kept.slot, kept.end]).toEqual(["top", 4]);
  });

  it("adds the studio offset and scale to its position", () => {
    const base = layoutOf(bareScene({ hook: hook() }));
    const moved = layoutOf(bareScene({ hook: hook({ x: 0.1, y: 0.2, scale: 1.5 }) }));
    expect(moved.px).toBeGreaterThan(base.px * 1.2);   // up to 1.5 times; the frame width limits a long line
    expect(moved.px).toBeLessThanOrEqual(base.px * 1.5 + 1e-6);
    expect(moved.cx).toBeCloseTo(W * 0.6, 6);
    expect(moved.cy - moved.h / 2).toBeCloseTo(base.cy - base.h / 2 + 0.2 * H, 3);
    const box = hookBounds(prep(bareScene({ hook: hook({ x: 0.1, y: 0.2, scale: 1.5 }) })))!;
    expect(box).toEqual({ x: moved.cx - moved.w / 2, y: moved.cy - moved.h / 2, w: moved.w, h: moved.h });
  });

  it("scales with the frame: landscape and square use the short side", () => {
    for (const [w, h] of [[1920, 1080], [1080, 1080], [720, 1280]] as const) {
      const l = hookLayout(prep(bareScene({ width: w, height: h, hook: hook() })))!;
      expect(l.px).toBeCloseTo(88 * Math.min(w, h) / 1080, 6);   // two short lines: between the base and the largest size
      expect(l.w).toBeLessThanOrEqual(0.88 * w + 1e-6);
      expect((l.cy - l.h / 2) / h).toBeCloseTo(HOOK_PLACE.top, 6);
    }
  });
});

describe("motion", () => {
  it("pops in over 260 ms with a small overshoot and fades out over the last 200 ms", () => {
    expect(hookMotion("bold", 0, 3.88)).toEqual({ scale: 0.86, alpha: 0 });
    expect(hookMotion("bold", 0.06, 3.88).alpha).toBeCloseTo(0.5, 6);
    const peak = hookMotion("bold", 0.26 * 0.62, 3.88);
    expect(peak.scale).toBeCloseTo(1.035, 6);
    expect(peak.alpha).toBe(1);
    expect(hookMotion("bold", 0.26, 3.88)).toEqual({ scale: 1, alpha: 1 });
    expect(hookMotion("bold", 2, 3.88)).toEqual({ scale: 1, alpha: 1 });
    const out = hookMotion("bold", 3.78, 3.88);
    expect(out.alpha).toBeCloseTo(0.5, 6);
    expect(out.scale).toBeCloseTo(0.98, 6);
    // Subtle: no overshoot.
    for (const t of [0.05, 0.1, 0.2]) expect(hookMotion("subtle", t, 3.88).scale).toBeLessThanOrEqual(1);
    // A very short window still fits its entrance and exit.
    expect(hookMotion("bold", 0.15, 0.3).alpha).toBe(1);
  });

  it("wipes the markers in left to right just after the card lands; max tilts the card 2 degrees", () => {
    const p = prep(bareScene({ hook: hook({ text: "Never trust the first 3 offers", look: "max" }) }));
    const marks = (t: number) => hookOps(p, t).filter((o) => o.op === "rect") as RectOp[];
    expect(marks(0.12 + 0.19)).toHaveLength(0);
    const early = marks(0.12 + 0.26), late = marks(0.12 + 0.6);
    expect(early).toHaveLength(1);                      // the second marker starts 80 ms later
    expect(late).toHaveLength(2);
    expect(early[0].w).toBeLessThan(late[0].w);
    expect(early[0].x).toBe(late[0].x);                 // grows from its left edge
    const card = hookOps(p, 2)[0] as PathOp;
    expect(Math.atan2(card.transform[1], card.transform[0])).toBeCloseTo((-2 * Math.PI) / 180, 9);
    const flat = hookOps(prep(bareScene({ hook: hook() })), 2)[0] as PathOp;
    expect(flat.transform.slice(0, 4)).toEqual([1, 0, -0, 1]);
  });
});

describe("styles", () => {
  it("each caption template has a title style; unknown templates get the stack", () => {
    expect(hookVariant("emoji_drop").style).toBe("stack");
    expect(hookVariant("tiktok").style).toBe("stack");
    expect(hookVariant("slam").style).toBe("outline");
    expect(hookVariant("mrbeast").fill).toBe("#FFE600");
    expect(hookVariant("nope")).toBe(HOOK_STACK);
    expect(hookVariant(undefined)).toBe(HOOK_STACK);
  });

  it("outline: every word's outline is drawn before any fill, key words in the accent, no label", () => {
    const p = prep(bareScene({ hook: hook({ text: "Never trust the first 3 offers", template: "slam", accent: "#FFE000" }) }));
    const ops = hookOps(p, 2) as TextOp[];
    expect(ops.every((o) => o.op === "text")).toBe(true);
    expect(ops).toHaveLength(12);
    expect(ops.slice(0, 6).every((o) => o.fill === null && o.stroke === "#000000" && o.strokePx > 0 && !!o.shadow)).toBe(true);
    expect(ops.slice(6).map((o) => [o.text, o.fill])).toEqual([["Never", "#FFE000"], ["trust", "#FFFFFF"], ["the", "#FFFFFF"],
      ["first", "#FFFFFF"], ["3", "#FFE000"], ["offers", "#FFFFFF"]]);
  });

  it("a template's own accent wins where the caption accent would not read; a boxed key word keeps its outline off", () => {
    const beast = prep(bareScene({ hook: hook({ text: "Never trust it", template: "mrbeast", accent: "#34FD87" }) }));
    expect(hookLayout(beast)!.mark).toBe("#FF3B30");
    const boxed = prep(bareScene({ hook: hook({ text: "Never trust it", template: "hormozi" }) }));
    const ops = hookOps(boxed, 2);
    expect(ops.filter((o) => o.op === "rect")).toHaveLength(1);
    expect((ops.filter((o) => o.op === "text") as TextOp[]).filter((o) => o.stroke).map((o) => o.text)).toEqual(["trust", "it"]);
  });
});

describe("the frame", () => {
  it("draws the card above the captions and under a flash, only inside its window", () => {
    const scene = makeScene({
      template: "highlighter_swipe", caption: lookCaptions(0.55), hook: hook(),
      cues: [cue({ id: "f", kind: "flash", start: 1, end: 1.2, color: "#FFFFFF", peak: 0.9 })],
    });
    const p = prep(scene);
    const ops = frameOps(p, clockAt(p, 1.05));
    const card = ops.findIndex((o) => o.op === "path" && o.fill === V.fill && !!o.shadow);
    const caption = ops.findIndex((o) => o.op === "text" && o.fill === "#FFFFFF");
    const flash = ops.findIndex((o) => o.op === "rect" && o.w === W && o.h === H);
    expect(card).toBeGreaterThan(caption);
    expect(flash).toBeGreaterThan(card);
    const titled = (o: number) => frameOps(p, clockAt(p, o)).some((op) => op.op === "text" && op.fill === V.ink);
    expect(titled(0.1)).toBe(false);
    expect(titled(0.13)).toBe(true);
    expect(titled(3.99)).toBe(true);
    expect(titled(4)).toBe(false);
    expect(drawerErrors(p)).toEqual([]);
  });

  it("is left out of the frame when detached (the studio draws it as its own layer), and still laid out", () => {
    const scene = bareScene({ hook: hook() });
    const p = prep(scene, true);
    expect(frameOps(p, clockAt(p, 1)).filter((o) => o.op !== "picture")).toEqual([]);
    expect(hookOps(p, 1).length).toBeGreaterThan(0);
    // The field being typed in replaces the title; an emptied field draws nothing.
    const texts = (hookOps(p, 1, "Brand new words") as TextOp[]).filter((o) => o.op === "text").map((o) => o.text);
    expect(texts).toEqual(["Brand", "new", "words"]);
    expect(hookOps(p, 1, "  ")).toEqual([]);
    expect(hookBounds(p, "")).toBeNull();
  });

  it("no hook, no ops", () => {
    const p = prep(bareScene({ hook: null, hook_window: [0.12, 4] }));
    expect(frameOps(p, clockAt(p, 1)).filter((o) => o.op !== "picture")).toEqual([]);
    expect(hookLayout(p)).toBeNull();
  });

  it("ops snapshot: entrance, held with markers, exit", () => {
    const p = prep(bareScene({ hook: hook({ text: "90% of podcast guests become real friends" }) }));
    expect(snap(hookOps(p, 0.12 + 2 / 30))).toMatchSnapshot("entrance");
    expect(snap(hookOps(p, 1.2))).toMatchSnapshot("held");
    expect(snap(hookOps(p, 3.9))).toMatchSnapshot("exit");
  });

  it("golden: the card over a close-up with look captions, and tilted at max", () => {
    const words = sampleWords();
    const scene = (look: FxHookTitle["look"], text: string) => makeScene({
      template: "highlighter_swipe", words, caption: lookCaptions(0.55), hook: hook({ look, text }),
      face_track: face(0.36, 0.24, 0.28, 0.2),
    });
    expectGolden("hook_card_bold", renderSceneToPng(scene("bold", "90% of podcast guests become real friends"), 1.2));
    expectGolden("hook_card_max_long", renderSceneToPng(scene("max", "Nobody tells you the truth about what 90% of podcast guests want"), 1.2));
    const outlined = (template: string, text: string) => makeScene({
      words, caption: lookCaptions(0.55), hook: hook({ template, text, accent: "#FFE000" }), face_track: face(0.36, 0.24, 0.28, 0.2),
    });
    expectGolden("hook_outline_slam", renderSceneToPng(outlined("slam", "Never trust the first 3 offers"), 1.2));
    expectGolden("hook_outline_neon", renderSceneToPng(outlined("neon", "90% of podcast guests become real friends"), 1.2));
  });
});
