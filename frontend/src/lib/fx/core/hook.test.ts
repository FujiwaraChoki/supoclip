// @vitest-environment node
// The hook title card (hook.ts): tokens and markers, balanced lines, placement clear of faces and captions, motion,
// and the drawn frame.
import { describe, expect, it } from "vitest";
import { clockAt, drawerErrors, frameOps } from "./frame";
import type { DrawOp } from "./host";
import { HOOK, HOOK_PLACE, hookBlock, hookBounds, hookLayout, hookMarkColor, hookMarks, hookMotion, hookOps, hookTokens } from "./hook";
import { prepareScene } from "./scene";
import { captionStyle, fakeMeasure, fakeMeasureHost, makeScene, sampleWords } from "./test-utils/fake-host";
import { expectGolden, renderSceneToPng } from "./test-utils/golden";
import { bareScene, cue, snap } from "./picture/fixtures";
import type { FaceTrack, FxHookTitle, FxScene } from "./types";

type RectOp = Extract<DrawOp, { op: "rect" }>;
type TextOp = Extract<DrawOp, { op: "text" }>;

const W = 1080, H = 1920;
const hook = (overrides: Partial<FxHookTitle> = {}): FxHookTitle => ({
  text: "I deleted a mega star's episode", font: "Poppins-ExtraBold", accent: "#34FD87", look: "bold", start: 0.12, end: 4,
  x: 0, y: 0, scale: 1, trim: false, picture: null, avoid: [], ...overrides,
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
  const block = (title: string, base = 65, min = 48.6) => hookBlock(title, base, min, 0.86 * W, measure)!;

  it("one line for a short title, two balanced lines otherwise, three before the type shrinks", () => {
    expect(block("Why I quit").lines.map((l) => l.text)).toEqual(["Why I quit"]);
    const two = block("I deleted a mega star's episode");
    expect(two.lines.map((l) => l.text)).toEqual(["I deleted a mega", "star's episode"]);
    expect(two.px).toBe(65);
    const three = block("Nobody tells you the truth about what 90% of podcast guests want");
    expect(three.lines).toHaveLength(3);
    expect(three.px).toBeGreaterThanOrEqual(48.6);
    for (const b of [two, three]) {
      expect(b.w).toBeLessThanOrEqual(0.86 * W + 1e-6);
      for (const line of b.lines) expect(line.w).toBeLessThanOrEqual(b.w - 2 * HOOK.padX * b.px + 1e-6);
      const widths = b.lines.map((l) => l.w);
      expect(Math.min(...widths) / Math.max(...widths)).toBeGreaterThan(0.6);   // balanced, no orphan
    }
  });

  it("never goes under the minimum size unless a single word is wider than the card", () => {
    const long = block("Nobody tells you the truth about what 90% of podcast guests want today ok");
    expect(long.px).toBeGreaterThanOrEqual(48.6 - 1e-6);
    const word = block("Pneumonoultramicroscopicsilicovolcanoconiosis");
    expect(word.lines).toHaveLength(1);
    expect(word.px).toBeLessThan(48.6);
    expect(word.w).toBeLessThanOrEqual(0.86 * W + 1e-6);
  });

  it("the card is the text plus 0.5 em / 0.32 em padding, lines 1.18 em apart", () => {
    const b = block("I deleted a mega star's episode");
    expect(b.h).toBeCloseTo(2 * 1.18 * 65 + 2 * 0.32 * 65, 6);
    expect(b.w).toBeCloseTo(Math.max(...b.lines.map((l) => l.w)) + 65, 6);
    expect(b.lines[1].y - b.lines[0].y).toBeCloseTo(1.18 * 65, 6);
  });

  it("the marker covers the word, not its comma", () => {
    const b = block("3am, a duvet, and zero plan");
    const word = b.lines[0].words[0];
    expect(word.text).toBe("3am,");
    expect(word.mark).toBe(0);
    expect(word.mw).toBeCloseTo(measure("3am", 65), 6);
    expect(word.mx).toBe(word.x);
  });
});

describe("placement", () => {
  const layoutOf = (scene: FxScene) => hookLayout(prep(scene))!;
  const top = (l: { cy: number; h: number }) => (l.cy - l.h / 2) / H;
  const bottom = (l: { cy: number; h: number }) => (l.cy + l.h / 2) / H;

  it("sits in the top safe area by default", () => {
    const l = layoutOf(bareScene({ hook: hook() }));
    expect(l.slot).toBe("top");
    expect(top(l)).toBeCloseTo(HOOK_PLACE.top, 6);
    expect(l.cx).toBe(W / 2);
  });

  it("rises to leave a forehead clear, never past the platform bar", () => {
    const l = layoutOf(bareScene({ hook: hook(), face_track: face(0.3, 0.2, 0.4, 0.22) }));
    expect(l.slot).toBe("top");
    expect(top(l)).toBeGreaterThanOrEqual(HOOK_PLACE.topMin - 1e-9);
    expect(bottom(l)).toBeLessThanOrEqual(0.2 - 0.01 + 1e-6);
  });

  it("never covers eyes or mouth: a face at the top of a stacked two-shot sends it above the captions on the seam", () => {
    const scene = makeScene({
      template: "highlighter_swipe", caption: lookCaptions(0.5), hook: hook({ start: 0.12, end: 4 }),
      face_track: face(0.3, 0.06, 0.4, 0.22),
    });
    const p = prep(scene), l = hookLayout(p)!;
    expect(l.slot).toBe("above");
    const chunks = p.chunks.filter((c) => c.start < 4 && c.end > 0.12);
    const captionTop = Math.min(...chunks.flatMap((c) => c.words.map((w) => w.y)));
    expect(l.cy + l.h / 2).toBeLessThan(captionTop);
    expect(l.cy - l.h / 2).toBeGreaterThan((0.06 + 0.22) * H);   // under the upper face
  });

  it("goes under the captions on the chest when a close-up fills the top and middle", () => {
    const scene = makeScene({
      template: "highlighter_swipe", caption: lookCaptions(0.55), hook: hook(), face_track: face(0.15, 0.04, 0.7, 0.46),
    });
    const p = prep(scene), l = hookLayout(p)!;
    expect(l.slot).toBe("below");
    const captionBottom = Math.max(...p.chunks.filter((c) => c.start < 4).flatMap((c) => c.words.map((w) => w.y + w.h)));
    expect(l.cy - l.h / 2).toBeGreaterThan(captionBottom);
    expect(bottom(l)).toBeLessThanOrEqual(HOOK_PLACE.bottomMax);
  });

  it("uses the blurred band above a letterboxed picture", () => {
    const l = layoutOf(bareScene({ hook: hook({ picture: [0.342, 0.658] }) }));
    expect(l.slot).toBe("band");
    expect(top(l)).toBeGreaterThanOrEqual(HOOK_PLACE.top - 1e-9);
    expect(bottom(l)).toBeLessThanOrEqual(0.342 - HOOK_PLACE.bandGap + 1e-9);
    // No room in the band (a tall picture): the top safe area as usual.
    expect(layoutOf(bareScene({ hook: hook({ picture: [0.15, 0.85] }) })).slot).toBe("top");
  });

  it("keeps clear of bands other layers own (classic captions burned by ASS)", () => {
    const l = layoutOf(bareScene({ hook: hook({ avoid: [[0.08, 0.24]] }) }));
    expect(top(l)).toBeGreaterThan(0.24);
  });

  it("has one position for the whole window, wherever the face goes", () => {
    const moving: FaceTrack = { version: 1, space: "output", fps: 6, samples: [] };
    for (let i = 0; i <= 36; i++) moving.samples.push([i / 6, [face(0.3, i < 12 ? 0.3 : 0.08, 0.4, 0.22).samples[0][1][0]]]);
    const p = prep(makeScene({ template: "highlighter_swipe", caption: lookCaptions(0.62), hook: hook(), face_track: moving }));
    const l = hookLayout(p)!;
    const rects = [0.2, 1, 2.5, 3.7].map((t) => (hookOps(p, t)[0] as RectOp).transform.slice(4));
    for (const r of rects) {
      expect(r[0]).toBeCloseTo(l.cx, 3);
      expect(r[1]).toBeCloseTo(l.cy, 3);
    }
    // The spot clears the eyes and mouth of both face positions.
    for (const y of [0.3, 0.08]) {
      const eyes = (y + 0.38 * 0.22 - 0.14 * 0.22) * H, lip = (y + 0.75 * 0.22 + 0.12 * 0.22) * H;
      expect(l.cy + l.h / 2 <= eyes || l.cy - l.h / 2 >= lip).toBe(true);
    }
  });

  it("with automatic timing, leaves at a camera cut when that frees the band above a letterboxed opening", () => {
    // A letterboxed graphic until 3 s, then a close-up whose face fills the top: the title sits above the graphic and
    // goes at the cut, instead of covering the graphic for the whole window.
    const samples: FaceTrack["samples"] = [];
    for (let i = 18; i <= 36; i++) samples.push([i / 6, [face(0.2, 0.05, 0.6, 0.4).samples[0][1][0]]]);
    const rows = [{ start: 0, end: 3, y: 0.76 }, { start: 3, end: 6, y: 0.65 }];
    const scene = (trim: boolean) => makeScene({
      template: "highlighter_swipe", caption: lookCaptions(0.76, { rows }), hook: hook({ trim, picture: [0.342, 0.658] }),
      face_track: { version: 1, space: "output", fps: 6, samples },
    });
    const trimmed = hookLayout(prep(scene(true)))!;
    expect(trimmed.slot).toBe("band");
    expect(trimmed.end).toBe(3);
    expect(hookOps(prep(scene(true)), 3.01)).toEqual([]);
    const kept = hookLayout(prep(scene(false)))!;
    expect(kept.end).toBe(4);
    expect(kept.slot).not.toBe("band");   // the whole window: somewhere clear of the close-up's eyes and mouth
  });

  it("adds the studio offset and scale to the automatic position", () => {
    const base = layoutOf(bareScene({ hook: hook() }));
    const moved = layoutOf(bareScene({ hook: hook({ x: 0.1, y: 0.2, scale: 1.5 }) }));
    expect(moved.px).toBeCloseTo(base.px * 1.5, 6);
    expect(moved.cx).toBeCloseTo(W * 0.6, 6);
    expect(moved.cy - moved.h / 2).toBeCloseTo(base.cy - base.h / 2 + 0.2 * H, 3);
    const box = hookBounds(prep(bareScene({ hook: hook({ x: 0.1, y: 0.2, scale: 1.5 }) })))!;
    expect(box).toEqual({ x: moved.cx - moved.w / 2, y: moved.cy - moved.h / 2, w: moved.w, h: moved.h });
  });

  it("scales with the frame: landscape and square use the short side", () => {
    for (const [w, h] of [[1920, 1080], [1080, 1080], [720, 1280]] as const) {
      const l = hookLayout(prep(bareScene({ width: w, height: h, hook: hook() })))!;
      expect(l.px).toBeCloseTo(65 * Math.min(w, h) / 1080, 6);
      expect(l.w).toBeLessThanOrEqual(0.86 * w + 1e-6);
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
    const marks = (t: number) => (hookOps(p, t).filter((o) => o.op === "rect") as RectOp[]).slice(1);
    expect(marks(0.12 + 0.19)).toHaveLength(0);
    const early = marks(0.12 + 0.26), late = marks(0.12 + 0.6);
    expect(early).toHaveLength(1);                      // the second marker starts 80 ms later
    expect(late).toHaveLength(2);
    expect(early[0].w).toBeLessThan(late[0].w);
    expect(early[0].x).toBe(late[0].x);                 // grows from its left edge
    const card = hookOps(p, 2)[0] as RectOp;
    expect(Math.atan2(card.transform[1], card.transform[0])).toBeCloseTo((-2 * Math.PI) / 180, 9);
    const flat = hookOps(prep(bareScene({ hook: hook() })), 2)[0] as RectOp;
    expect(flat.transform.slice(0, 4)).toEqual([1, 0, -0, 1]);
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
    const card = ops.findIndex((o) => o.op === "rect" && o.fill === HOOK.fill && !!o.shadow);
    const caption = ops.findIndex((o) => o.op === "text" && o.fill === "#FFFFFF");
    const flash = ops.findIndex((o) => o.op === "rect" && o.w === W && o.h === H);
    expect(card).toBeGreaterThan(caption);
    expect(flash).toBeGreaterThan(card);
    const titled = (o: number) => frameOps(p, clockAt(p, o)).some((op) => op.op === "text" && op.fill === HOOK.ink);
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
  });
});
