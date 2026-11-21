// @vitest-environment node
// Intensity caption looks (captions/looks.ts): phrase groups, word-by-word reveal with the look's lead, 0-frame
// entrances (a 2-frame pop at max), keyword boxes, and the look emoji / behind words. Spec: docs/dynamic-edits.md
// "Reference edits".
import { describe, expect, it } from "vitest";
import type { DrawOp } from "../host";
import type { AccentCue, FxCaptionStyle, FxScene, FxWord } from "../types";
import { accent, opsAt, prep } from "./wp8.testkit";
import { LOOK_GEOMETRY, stripEdgePunct, visibleLayout } from "./looks";
import { LOOK_LEAD } from "./groups";
import { LOOK_EMOJI, lookEmojiPlacement } from "./emoji_drop";
import { lookBehindText } from "./behind_head";
import { NUDGE_SECONDS, nudgeCurve, zoomFactor } from "../picture/zoom";
import { drawerErrors } from "../frame";

const FRAME = 1 / 30;

/** "Most people never | quit." style words: 0.3 s apart, 0.25 s long; a comma ends the first phrase. */
function words(texts: string[], start = 0.5, step = 0.3): FxWord[] {
  return texts.map((text, i) => ({ id: `w${i}`, start: start + i * step, end: start + i * step + 0.25, text, highlight: false,
    run: "clip" }));
}

const WORDS = words(["Most", "people", "never,", "really", "quit", "their", "jobs."]);

function style(look: "subtle" | "bold" | "max", template: string, extra: Partial<FxCaptionStyle> = {}): FxCaptionStyle {
  return {
    template, font: "Poppins-ExtraBold", size_px: look === "subtle" ? 52 : look === "bold" ? 72 : 84, color: "#FFFFFF",
    accent: "#34FD87", stroke_color: look === "max" ? "#000000" : null, stroke_px: look === "max" ? 7.56 : 0,
    shadow: true, background: template === "highlighter_swipe" && look === "bold" ? "#170904D9" : null, y: 0.55,
    words_per_line: look === "subtle" ? 4 : 3, uppercase: look === "max", enabled: true, placements: [], look, ...extra,
  };
}

function scene(look: "subtle" | "bold" | "max", template = "highlighter_swipe", cues: AccentCue[] = [],
  extra: Partial<FxScene> = {}): FxScene {
  return {
    version: 1, width: 1080, height: 1920, edit_duration: 4, out_duration: 4, warp: [], template, cues, words: WORDS,
    caption: style(look, template), grade: "none", grain: 0, progress_bar: false, progress_color: "#FF7A1A",
    loop_ending: "none", hook_window: null, hook_title_suppressed: false, face_track: null, broll_windows: [],
    matte_windows: [], seed: 7, ...extra,
  };
}

const texts = (ops: DrawOp[]) => ops.filter((o): o is Extract<DrawOp, { op: "text" }> => o.op === "text" && o.fill !== null);
const rects = (ops: DrawOp[]) => ops.filter((o): o is Extract<DrawOp, { op: "rect" }> => o.op === "rect");
const KEY = accent("w1.kw", "highlight", WORDS[1].start, 1.4, { text: "people" });

describe("look captions: grouping and timing", () => {
  it("groups by phrase (punctuation breaks) up to words_per_line and shows words one by one with the lead", () => {
    const p = prep(scene("bold"));
    expect(p.chunks.map((c) => c.words.map((w) => w.text).join(" "))).toEqual(["Most people never", "really quit their", "jobs"]);
    const first = p.chunks[0];
    expect(first.start).toBeCloseTo(WORDS[0].start + LOOK_LEAD.bold, 9);
    expect(first.words.map((w) => w.shown)).toEqual(WORDS.slice(0, 3).map((w) => w.start + LOOK_LEAD.bold));
    // Bold strips edge punctuation ("never," → "never", "jobs." → "jobs").
    expect(stripEdgePunct("“yes,”")).toBe("yes");
    expect(stripEdgePunct("$40,000")).toBe("$40,000");
    // A censored word keeps its stars.
    expect(stripEdgePunct("n****.")).toBe("n****");
    expect(stripEdgePunct("“f***,”")).toBe("f***");
  });

  it("re-centres the line as words appear and never animates (0-frame entrance)", () => {
    const p = prep(scene("bold"));
    const chunk = p.chunks[0];
    const one = visibleLayout(chunk, chunk.words[0].shown!);
    const two = visibleLayout(chunk, chunk.words[1].shown!);
    expect(one).toHaveLength(1);
    expect(two).toHaveLength(2);
    expect(one[0].x + one[0].w / 2).toBeCloseTo(chunk.centerX, 6);
    const span = two[1].x + two[1].w - two[0].x;
    expect(two[0].x + span / 2).toBeCloseTo(chunk.centerX, 6);
    const ops = opsAt(p, chunk.words[1].shown! + 1e-4);
    expect(texts(ops).map((o) => o.text)).toEqual(["Most", "people"]);
    expect(texts(ops).every((o) => o.alpha === 1)).toBe(true);
  });

  it("subtle shows the whole group at once, ahead of the first word (LOOK_LEAD.subtle)", () => {
    const p = prep(scene("subtle"));
    const chunk = p.chunks[0];
    expect(chunk.start).toBeCloseTo(WORDS[0].start + LOOK_LEAD.subtle, 9);
    expect(texts(opsAt(p, chunk.start + 1e-4)).map((o) => o.text)).toEqual(["Most", "people", "never,"]);
    expect(texts(opsAt(p, chunk.start - 0.01))).toEqual([]);
  });

  it("max pops each new word for exactly one frame: 0.85 scale at 60 %, then 1.0", () => {
    const p = prep(scene("max"));
    const w = p.chunks[0].words[1];
    const first = texts(opsAt(p, w.shown! + 1e-4)).filter((o) => o.text === "PEOPLE");
    expect(first.length).toBeGreaterThan(0);
    expect(first.every((o) => o.alpha === LOOK_GEOMETRY.popAlpha && o.transform[0] === LOOK_GEOMETRY.popScale)).toBe(true);
    const next = texts(opsAt(p, w.shown! + FRAME + 1e-4)).filter((o) => o.text === "PEOPLE");
    expect(next.every((o) => o.alpha === 1 && o.transform[0] === 1)).toBe(true);
  });
});

describe("keyword box", () => {
  it("is an opaque rounded box under a black CAPS keyword from its first frame, gone with its group", () => {
    const p = prep(scene("bold", "highlighter_swipe", [KEY]));
    const at = WORDS[1].start + LOOK_LEAD.bold + 1e-4;
    expect(rects(opsAt(p, at - FRAME))).toHaveLength(1);                 // the pill only
    const ops = opsAt(p, at);
    const [pill, box] = rects(ops);
    expect(pill.fill).toBe("rgba(23,9,4,0.851)");
    expect(box.fill).toBe("#34FD87");
    expect(box.alpha).toBe(1);
    expect(box.radius).toBeCloseTo(LOOK_GEOMETRY.boxRadius * box.h, 6);
    const word = texts(ops).find((o) => o.text === "PEOPLE")!;
    expect(word.fill).toBe("#000000");
    expect(ops.indexOf(box)).toBeLessThan(ops.indexOf(word));
    const end = p.chunks[0].end;
    expect(rects(opsAt(p, end + 1e-3)).filter((r) => r.fill === "#34FD87")).toHaveLength(0);
  });

  it("other look templates colour the keyword instead of boxing it", () => {
    const p = prep(scene("bold", "emoji_drop", [KEY]));
    const ops = opsAt(p, 1.3);
    expect(rects(ops)).toHaveLength(0);
    expect(texts(ops).find((o) => o.text === "PEOPLE")?.fill).toBe("#34FD87");
  });
});

describe("look emoji", () => {
  const emoji = accent("b0.accent", "emoji", WORDS[1].start - 2 * FRAME, 1.4, { text: "people", emoji: "💰" });
  it("is 0.26 W under the caption group, 2 frames in (0.85 → 1, alpha .5 → 1), then held without motion", () => {
    const p = prep(scene("bold", "emoji_drop", [emoji]));
    const at = lookEmojiPlacement(p, emoji)!;
    expect(at.size).toBeCloseTo(LOOK_EMOJI.sizeW * 1080, 6);
    expect(at.y).toBeGreaterThan(0.55 * 1920);
    const sprite = (t: number) => opsAt(p, t).find((o): o is Extract<DrawOp, { op: "sprite" }> => o.op === "sprite");
    expect(sprite(emoji.start + 1e-4)?.alpha).toBeCloseTo(0.5, 2);
    expect(sprite(emoji.start + FRAME + 1e-4)?.alpha).toBe(1);
    const a = sprite(emoji.start + 3 * FRAME)!, b = sprite(emoji.start + 12 * FRAME)!;
    expect(a.transform).toEqual(b.transform);                             // no spring, no wobble
    expect(sprite(emoji.end + 1e-3)).toBeUndefined();                     // cut out with its group
    expect(drawerErrors(p)).toEqual([]);
  });
});

describe("look emoji on caption rows (interview shots)", () => {
  const emoji = accent("b0.accent", "emoji", WORDS[1].start - 2 * FRAME, 1.4, { text: "people", emoji: "💰" });
  const rowScene = (rows: { start: number; end: number; y: number }[], words?: FxWord[]) => {
    const base = scene("bold", "emoji_drop", [emoji], words ? { words } : {});
    return { ...base, caption: { ...base.caption!, y: 0.5, rows } };
  };
  it("sits under its group on the row that group was placed on", () => {
    const high = lookEmojiPlacement(prep(rowScene([{ start: 0, end: 9, y: 0.5 }])), emoji)!;
    const low = lookEmojiPlacement(prep(rowScene([{ start: 0, end: 9, y: 0.65 }])), emoji)!;
    expect(low.y - high.y).toBeCloseTo(0.15 * 1920, 6);
  });
  it("leaves with its caption group at a camera cut", () => {
    const cut = 1.2;   // "people" (0.8) is on screen until the cut ends its group
    const p = prep(rowScene([{ start: 0, end: cut, y: 0.65 }, { start: cut, end: 9, y: 0.5 }]));
    const sprite = (t: number) => opsAt(p, t).find((o) => o.op === "sprite");
    expect(sprite(cut - 2 * FRAME)).toBeDefined();
    expect(sprite(cut + 1e-3)).toBeUndefined();
  });
  it("falls back to the row in effect at the emoji when its word is not on screen", () => {
    const lone = words(["unrelated"], 3.5);
    const at = (rows: { start: number; end: number; y: number }[]) => lookEmojiPlacement(prep(rowScene(rows, lone)), emoji)!;
    const pair = at([{ start: 0, end: 9, y: 0.5 }]), single = at([{ start: 0, end: 0.5, y: 0.5 }, { start: 0.5, end: 9, y: 0.65 }]);
    expect(single.y - pair.y).toBeCloseTo(0.15 * 1920, 6);
  });
});

describe("look behind word", () => {
  it("is one lowercase word, about 0.6 W wide, and hides the captions while it shows", () => {
    expect(lookBehindText("Works, ")).toBe("works");
    const behind = accent("b0.behind", "behind", 1.0, 2.5, { text: "Works" });
    const p = prep(scene("bold", "behind_head", [behind], { matte_windows: [{ start: 1, end: 2.5, person: true }] }));
    const ops = opsAt(p, 1.5);
    const word = texts(ops).find((o) => o.text === "works")!;
    expect(word.px).toBeGreaterThan(0);
    expect(ops.some((o) => o.op === "picture" && o.cutout)).toBe(true);
    expect(texts(ops).filter((o) => o.text !== "works")).toEqual([]);   // captions hidden
    expect(texts(opsAt(p, 0.9)).length).toBeGreaterThan(0);
  });
});

describe("nudge zoom", () => {
  it("ramps over 11 frames near-linearly with a soft landing, then holds", () => {
    expect(NUDGE_SECONDS).toBeCloseTo(11 / 30, 9);
    expect(nudgeCurve(0)).toBe(0);
    expect(nudgeCurve(0.5)).toBeCloseTo(0.55, 6);
    expect(nudgeCurve(1)).toBeCloseTo(1, 9);
    const cue = { mode: "nudge" as const, scale: 1.08 };
    expect(zoomFactor(cue, 0, 3)).toBe(1);
    expect(zoomFactor(cue, NUDGE_SECONDS, 3)).toBeCloseTo(1.08, 9);
    expect(zoomFactor(cue, 2.5, 3)).toBeCloseTo(1.08, 9);
    expect(zoomFactor(cue, 3, 3)).toBe(1);
  });
});
