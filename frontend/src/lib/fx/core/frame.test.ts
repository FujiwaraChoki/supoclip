import { describe, expect, it } from "vitest";
import { captionHitBoxes, clockAt, drawerErrors, frameOps, isPassthrough } from "./frame";
import type { DrawOp } from "./host";
import { registrySnapshot } from "./registry";
import { prepareScene } from "./scene";
import type { FxScene } from "./types";
import { captionStyle, everyCueKind, fakeMeasureHost, makeScene } from "./test-utils/fake-host";

const ASPECTS = [[1080, 1920], [1080, 1080], [1920, 1080]] as const;
const TIMES = [0.1, 0.25, 0.45, 1.3, 4.9];

/** JSON snapshot form: numbers to 3 dp so float noise never churns the snapshot. */
const snap = (ops: DrawOp[]) => JSON.parse(JSON.stringify(ops, (_k, v) => (typeof v === "number" ? Math.round(v * 1000) / 1000 : v)));
const at = (scene: FxScene, o: number) => {
  const p = prepareScene(scene, fakeMeasureHost);
  return { p, clock: clockAt(p, o), ops: frameOps(p, clockAt(p, o)) };
};

describe("base captions frameOps", () => {
  for (const [w, h] of ASPECTS) {
    it(`snapshots at 5 times, ${w}x${h}`, () => {
      const scene = makeScene({ width: w, height: h, caption: captionStyle({}, Math.min(w, h)) });
      for (const t of TIMES) {
        const { ops } = at(scene, t);
        expect(snap(ops)).toMatchSnapshot(`t=${t}`);
        for (const op of ops) {
          if (op.op !== "text") continue;
          // Inside the frame: every word's anchor (left edge, centre line) and its measured extent.
          expect(op.x).toBeGreaterThanOrEqual(0);
          expect(op.x + op.text.length * op.px * 0.55).toBeLessThanOrEqual(w + 1e-6);
          expect(op.y - op.px / 2).toBeGreaterThanOrEqual(0);
          expect(op.y + op.px / 2).toBeLessThanOrEqual(h);
        }
      }
    });
  }

  it("draws outlines before fills, the spoken word in the accent colour, and the settle entrance", () => {
    const { ops } = at(makeScene(), 0.25);
    const texts = ops.filter((o): o is Extract<DrawOp, { op: "text" }> => o.op === "text");
    expect(texts.slice(0, 3).every((o) => o.stroke === "#000000" && o.fill === null)).toBe(true);
    expect(texts.slice(3).map((o) => o.fill)).toEqual(["#FFD23F", "#FFFFFF", "#FFFFFF"]);
    expect(texts[0].transform[0]).toBeLessThan(1);   // 0.92 → 1 settle, 50 ms into the line
    const later = at(makeScene(), 0.9).ops.filter((o) => o.op === "text") as Extract<DrawOp, { op: "text" }>[];
    expect(later[0].transform[0]).toBe(1);
    expect(later.slice(3).map((o) => o.fill)).toEqual(["#FFFFFF", "#FFD23F", "#FFFFFF"]);
  });

  it("legacy templates draw no captions in fx-core (ASS does)", () => {
    const { ops } = at(makeScene({ template: "hormozi" }), 1);
    expect(ops.map((o) => o.op)).toEqual(["picture"]);
  });
});

describe("passthrough", () => {
  it("is true exactly when nothing draws", () => {
    const quiet = at(makeScene({ caption: null }), 1);
    expect(isPassthrough(quiet.p, quiet.clock)).toBe(true);
    const caption = at(makeScene(), 1);
    expect(isPassthrough(caption.p, caption.clock)).toBe(false);
    const before = at(makeScene(), 0.1);
    expect(isPassthrough(before.p, before.clock)).toBe(true);
  });

  it("degenerate scenes (no words, no timing, zero length, blank words) pass through without errors", () => {
    for (const scene of [
      makeScene({ words: [] }),
      makeScene({ edit_duration: 0, out_duration: 0, words: [] }),
      makeScene({ words: [{ id: "w0", start: 1, end: 1, text: "  ", highlight: false, run: "clip" }] }),
    ]) {
      const p = prepareScene(scene, fakeMeasureHost);
      for (const o of [0, 0.5, 1, 6]) expect(isPassthrough(p, clockAt(p, o))).toBe(true);
      expect(drawerErrors(p)).toEqual([]);
    }
  });

  it("memoises the last frame per prepared scene", () => {
    const { p, clock } = at(makeScene(), 1);
    expect(frameOps(p, clock)).toBe(frameOps(p, { ...clock }));
    expect(frameOps(p, clockAt(p, 1.2))).not.toBe(frameOps(p, clock));
  });
});

describe("every drawer registers", () => {
  it("all stubs are registered", () => {
    const r = registrySnapshot();
    expect([...r.accents].sort()).toEqual(["arrow", "behind", "circle", "counter", "decode", "doodle", "emoji", "emoji_burst",
      "highlight", "kinetic", "slam", "strike", "underline"]);
    expect(r.captionStyles).toHaveLength(9);
    expect([...r.picture].sort()).toEqual(["desaturate", "glitch", "grade", "shake", "transition", "zoom"]);
    expect(r.cues.map(([k]) => k).sort()).toEqual(["bleep", "flash", "freeze", "reaction", "sticker", "transition"]);
    expect([...r.globals].sort()).toEqual(["grade", "grain", "hook", "loop", "progress"]);
  });

  it("frameOps on a scene with every cue kind and every global runs without throwing", () => {
    for (const [w, h] of ASPECTS) {
      const scene = makeScene({
        width: w, height: h, caption: captionStyle({}, Math.min(w, h)), cues: everyCueKind(), grade: "vhs", grain: 0.4,
        progress_bar: true, loop_ending: "crossfade", hook_window: [0.12, 3], edit_duration: 5, out_duration: 5.5,
        warp: [{ e0: 0, e1: 4, o0: 0, rate: 1, hold: 0 }, { e0: 4, e1: 4, o0: 4, rate: 0, hold: 0.5 }, { e0: 4, e1: 5, o0: 4.5, rate: 1, hold: 0 }],
        face_track: { version: 1, space: "output", fps: 6, samples: Array.from({ length: 31 }, (_, i) => [i / 6, [[0.4, 0.2, 0.2, 0.12, 0.9]]] as [number, number[][]]) },
        broll_windows: [[3.5, 3.9]], matte_windows: [{ start: 1, end: 2, person: true }],
      });
      const p = prepareScene(scene, fakeMeasureHost);
      for (let frame = 0; frame < p.outFrames; frame++) {
        const ops = frameOps(p, clockAt(p, frame / 30));
        expect(ops[0].op).toBe("picture");
      }
      expect(drawerErrors(p)).toEqual([]);
    }
  });
});

describe("caption hit boxes", () => {
  it("cover the visible caption, keyed for studio placements", () => {
    const { p, clock, ops } = at(makeScene(), 1);
    const [box] = captionHitBoxes(p, clock);
    expect(box.key).toBe('["clip","w0"]');
    for (const op of ops) {
      if (op.op !== "text") continue;
      expect(op.x).toBeGreaterThanOrEqual(box.x);
      expect(op.y).toBeGreaterThanOrEqual(box.y);
      expect(op.y).toBeLessThanOrEqual(box.y + box.h);
    }
    expect(captionHitBoxes(p, clockAt(p, 0.05))).toEqual([]);
  });
});
