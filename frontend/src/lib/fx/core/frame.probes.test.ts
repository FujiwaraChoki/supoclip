// Probe drawers registered here stay inside this test file (vitest isolates module state per file).
import { describe, expect, it } from "vitest";
import { clockAt, drawerErrors, frameOps, inWindow, PICTURE_ORDER } from "./frame";
import { buildWarp } from "./warp";
import type { DrawOp } from "./host";
import { applyMat, pictureMatrix, rectOp, transformRect } from "./layout";
import {
  registerAccent, registerCue, registerGlobal, registerPicture, type DrawerContext, type Layer, type PictureState,
} from "./registry";
import { prepareScene } from "./scene";
import type { FxCue } from "./types";
import { fakeMeasureHost, makeScene } from "./test-utils/fake-host";

const tag = (name: string): DrawOp => rectOp({ x: 0, y: 0, w: 1, h: 1, fill: name });
const tags = (ops: DrawOp[]) => ops.map((o) => (o.op === "rect" ? o.fill : o.op === "text" ? "text" : o.op));
const cueBase = { seed: 3, beat: "b0", origin: "auto" as const };

describe("picture state and the transformed face (AR-M6)", () => {
  it("dc.face is the face box carried through this frame's picture matrix", () => {
    let seen: DrawerContext | null = null;
    registerPicture({
      kind: "zoom",
      apply: (s, cue, dc) => (cue ? { ...s, scale: 1.4, ax: dc.face ? (dc.face.x + dc.face.w / 2) / 1080 : 0.5, ay: dc.face ? (dc.face.y + dc.face.h / 2) / 1920 : 0.38 } : s),
    });
    registerPicture({ kind: "shake", apply: (s, cue) => (cue ? { ...s, dx: s.dx + 12, dy: s.dy - 7, rot: 0.03 } : s) });
    registerCue({ kind: "sfx", layer: "overlay", window: (c) => [c.start - 1, c.start + 1], ops: (_c, dc) => { seen = dc; return []; } });
    const row = [0.1, 0.12, 0.3, 0.16, 0.95, 0.15, 0.17, 0.3, 0.17, 0.22, 0.21, 0.17, 0.25, 0.28, 0.25];
    const cues: FxCue[] = [
      { ...cueBase, id: "z", kind: "zoom", start: 0.5, end: 2, mode: "punch", scale: 1.4, anchor: "face" },
      { ...cueBase, id: "s", kind: "shake", start: 0.5, end: 2, amp: 18, freq: 18 },
      { ...cueBase, id: "x", kind: "sfx", start: 1, end: 1, sound: "pop", variant: 0, gain_db: 0 },
    ];
    const p = prepareScene(makeScene({ caption: null, cues, face_track: { version: 1, space: "output", fps: 6, samples: [0, 0.25, 0.5, 0.75, 1, 1.25, 1.5].map((t) => [t, [row]] as [number, number[][]]) } }), fakeMeasureHost);
    frameOps(p, clockAt(p, 1));
    const dc = seen as DrawerContext | null;
    expect(dc).not.toBeNull();
    const raw = p.faceAt(1)!;
    const rawState: PictureState = { ...dc!.picture };
    const m = pictureMatrix(rawState, 1080, 1920);
    const box = transformRect(m, raw);
    expect(dc!.face!.x).toBeCloseTo(box.x, 6);
    expect(dc!.face!.y).toBeCloseTo(box.y, 6);
    expect(dc!.face!.w).toBeCloseTo(box.w, 6);
    expect(dc!.face!.h).toBeCloseTo(box.h, 6);
    const [lx, ly] = applyMat(m, raw.landmarks![0], raw.landmarks![1]);
    expect(dc!.face!.landmarks![0]).toBeCloseTo(lx, 6);
    expect(dc!.face!.landmarks![1]).toBeCloseTo(ly, 6);
    expect(dc!.picture.scale).toBeGreaterThanOrEqual(1.4);
    expect(dc!.u).toBe(1);
  });

  it("modifiers run in the fixed order grade → desaturate → zoom → shake → transition → glitch", () => {
    for (const kind of PICTURE_ORDER) registerPicture({ kind, apply: (s) => ({ ...s, filter: [...s.filter, kind] }) });
    const cues: FxCue[] = [
      { ...cueBase, id: "g", kind: "glitch", start: 0, end: 2, amount: 0.5 },
      { ...cueBase, id: "t", kind: "transition", start: 1, end: 1, style: "whip" },
      { ...cueBase, id: "s", kind: "shake", start: 0, end: 2, amp: 4, freq: 18 },
      { ...cueBase, id: "z", kind: "zoom", start: 0, end: 2, mode: "push", scale: 1.1, anchor: "center" },
      { ...cueBase, id: "d", kind: "desaturate", start: 0, end: 2, amount: 0.5, dim: 0 },
    ];
    const p = prepareScene(makeScene({ caption: null, cues, grade: "noir" }), fakeMeasureHost);
    const [pic] = frameOps(p, clockAt(p, 1));
    expect(pic.op === "picture" && pic.filter).toBe("grade desaturate zoom shake transition glitch");
    const outside = frameOps(p, clockAt(p, 2.5))[0];
    expect(outside.op === "picture" && outside.filter).toBe("grade");
  });
});

describe("layers", () => {
  it("emit in §6.5 order, captions before caption-layer cues, globals after cues", () => {
    const layers: [FxCue["kind"], Layer][] = [["bleep", "cover"], ["flash", "flash"], ["reaction", "overlay"], ["freeze", "retention"]];
    for (const [kind, layer] of layers) registerCue({ kind, layer, window: (c) => [c.start, Math.max(c.end, c.start + 1)], ops: () => [tag(`${kind}:${layer}`)] });
    registerAccent({ variant: "behind", layer: "behind", window: (c) => [c.start, c.end], ops: () => [tag("behind")] });
    registerAccent({ variant: "decode", layer: "caption", window: (c) => [c.start, c.end], ops: () => [tag("decode")] });
    registerGlobal({ id: "probe-pixel", layer: "pixel", active: () => true, ops: () => [tag("pixel")] });
    registerGlobal({ id: "probe-loop", layer: "loop", active: () => true, ops: () => [tag("loop")] });
    registerGlobal({ id: "probe-retention", layer: "retention", active: () => true, ops: () => [tag("retention-global")] });
    const accent = { text: "x", emoji: "", doodle: "sparkles" as const, value: null, prefix: "", suffix: "" };
    const cues: FxCue[] = [
      { ...cueBase, id: "a", kind: "bleep", start: 1, end: 1.5, tone: true, cover: "bar" },
      { ...cueBase, id: "b", kind: "flash", start: 1, end: 1.5, color: "#FFFFFF", peak: 1 },
      { ...cueBase, id: "c", kind: "reaction", start: 1, end: 1.5, query: "x", provider: "giphy", asset_id: "", url: "" },
      { ...cueBase, id: "d", kind: "freeze", start: 1, end: 1, hold: 0.5, label: "" },
      { ...cueBase, id: "e", kind: "accent", variant: "behind", start: 1, end: 1.5, ...accent },
      { ...cueBase, id: "f", kind: "accent", variant: "decode", start: 1, end: 1.5, ...accent },
    ];
    const p = prepareScene(makeScene({ cues }), fakeMeasureHost);
    const order = tags(frameOps(p, clockAt(p, 1.2)));
    const strip = order.filter((x, i) => x !== "text" || order[i - 1] !== "text");
    // "behind" sits under "pixel" so the person cutout keeps the grade and grain (D7).
    expect(strip).toEqual(["picture", "behind", "pixel", "reaction:overlay", "text", "decode", "flash:flash", "bleep:cover",
      "freeze:retention", "retention-global", "loop"]);
  });

  it("an accent with hidesCaptions hides the base captions while active", () => {
    registerAccent({ variant: "slam", layer: "overlay", hidesCaptions: true, window: (c) => [c.start, c.end], ops: () => [tag("slam")] });
    const cues: FxCue[] = [{ ...cueBase, id: "s", kind: "accent", variant: "slam", start: 1, end: 1.4, text: "GREW", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "" }];
    const p = prepareScene(makeScene({ cues }), fakeMeasureHost);
    expect(tags(frameOps(p, clockAt(p, 1.2))).filter((x) => x !== "pixel" && x !== "loop" && x !== "retention-global")).toEqual(["picture", "slam"]);
    expect(tags(frameOps(p, clockAt(p, 1.5)))).toContain("text");
  });

  it("a throwing drawer costs only its own ops and is reported", () => {
    registerAccent({ variant: "kinetic", layer: "overlay", window: (c) => [c.start, c.end], ops: () => { throw new Error("boom"); } });
    const cues: FxCue[] = [{ ...cueBase, id: "k", kind: "accent", variant: "kinetic", start: 0, end: 3, text: "", emoji: "", doodle: "sparkles", value: null, prefix: "", suffix: "" }];
    const p = prepareScene(makeScene({ cues }), fakeMeasureHost);
    const ops = frameOps(p, clockAt(p, 1));
    expect(ops[0].op).toBe("picture");
    expect(tags(ops)).toContain("text");
    expect(drawerErrors(p)).toEqual(["accent:k: boom"]);
  });

  it("drawer contexts are seeded per cue: rng restarts on every call, hash is stable", () => {
    const draws: number[][] = [];
    registerAccent({ variant: "emoji", layer: "overlay", window: (c) => [c.start, c.end], ops: (_c, dc) => { draws.push([dc.rng(), dc.rng(), dc.hash(5)]); return []; } });
    const cues: FxCue[] = [{ ...cueBase, seed: 99, id: "m", kind: "accent", variant: "emoji", start: 0, end: 3, text: "", emoji: "🔥", doodle: "sparkles", value: null, prefix: "", suffix: "" }];
    const p = prepareScene(makeScene({ cues }), fakeMeasureHost);
    frameOps(p, clockAt(p, 1));
    frameOps(p, clockAt(p, 2));
    expect(draws[0]).toEqual(draws[1]);
    expect(draws[0][0]).not.toBe(draws[0][1]);
  });

  it("an instant window covers the whole freeze hold even when the cue time is stored rounded (1.233 vs 37/30)", () => {
    registerCue({ kind: "freeze", layer: "retention", window: (c) => [c.start, c.start], ops: () => [tag("freeze")] });
    const cues: FxCue[] = [{ ...cueBase, id: "f", kind: "freeze", start: 1.233, end: 1.233, hold: 0.5, label: "WAIT" }];
    const warp = buildWarp(cues, 3);
    const p = prepareScene(makeScene({ cues, edit_duration: 3, out_duration: warp.outDuration, warp: [...warp.pieces] }), fakeMeasureHost);
    const shown = (n: number) => tags(frameOps(p, clockAt(p, n / 30))).includes("freeze");
    const hold = warp.pieces.find((x) => x.rate === 0)!;
    const first = Math.round(hold.o0 * 30), frames = Math.round(hold.hold * 30);
    expect(shown(first - 1)).toBe(false);
    for (let n = first; n < first + frames; n++) expect(shown(n)).toBe(true);
    expect(shown(first + frames + 1)).toBe(false);
    expect(inWindow(1.2, [1, 1.2])).toBe(false);
    expect(inWindow(1.1999, [1, 1.2])).toBe(true);
  });
});
