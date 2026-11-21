// @vitest-environment node
import { describe, expect, it } from "vitest";
import { clockAt, drawerErrors, frameOps, isPassthrough, pictureStateAt } from "../frame";
import type { DrawOp } from "../host";
import { invert, mul, translation, applyMat } from "../layout";
import { prepareScene } from "../scene";
import { hash01 } from "../rng";
import { TRANSITION_HALF, type FxCue, type TransitionStyle } from "../types";
import { fakeMeasureHost } from "../test-utils/fake-host";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import { ASPECTS, bareScene, cue, faceTrack, snap } from "./fixtures";
import { NO_FACE_ANCHOR, smoothedFaceCenter, zoomFaceAnchor, zoomFactor } from "./zoom";
import { shakeOffset } from "./shake";
import { spinAngle, whipAt, zoomThroughScale } from "./transition";

type PictureOp = Extract<DrawOp, { op: "picture" }>;
const STYLES: TransitionStyle[] = ["whip", "flash", "glitch", "zoom_through", "spin", "light_leak"];

const prep = (cues: FxCue[], w = 1080, h = 1920, extra = {}) => prepareScene(bareScene({ width: w, height: h, cues, ...extra }), fakeMeasureHost);
const opsAt = (p: ReturnType<typeof prep>, o: number) => frameOps(p, clockAt(p, o));
const pictures = (ops: DrawOp[]) => ops.filter((o): o is PictureOp => o.op === "picture" && !o.cutout);

/** Every picture op must cover its whole draw region (the frame, or its clip band) with picture pixels. */
function assertCovered(ops: DrawOp[], w: number, h: number, label: string) {
  for (const op of pictures(ops)) {
    const inv = invert(mul(translation(op.dx, op.dy), op.transform));
    const [x, y, cw, ch] = op.clip ?? [0, 0, w, h];
    for (const [qx, qy] of [[x, y], [x + cw, y], [x, y + ch], [x + cw, y + ch]]) {
      const [sx, sy] = applyMat(inv, qx, qy);
      const ok = sx >= -1e-3 && sx <= w + 1e-3 && sy >= -1e-3 && sy <= h + 1e-3;
      if (!ok) throw new Error(`${label}: corner (${qx},${qy}) maps to picture (${sx.toFixed(2)},${sy.toFixed(2)}) outside ${w}x${h}`);
    }
  }
}

describe("zoom", () => {
  it("punch steps hard at start and end; push/pull ease; zooms compose multiplicatively", () => {
    expect(zoomFactor({ mode: "punch", scale: 1.2 }, 0, 1)).toBe(1.2);
    expect(zoomFactor({ mode: "punch", scale: 1.2 }, 0.999, 1)).toBe(1.2);
    expect(zoomFactor({ mode: "punch", scale: 1.2 }, 1, 1)).toBe(1);
    expect(zoomFactor({ mode: "push", scale: 1.1 }, 0, 2)).toBe(1);
    expect(zoomFactor({ mode: "push", scale: 1.1 }, 1, 2)).toBeCloseTo(1.05, 9);
    expect(zoomFactor({ mode: "pull", scale: 1.1 }, 0, 2)).toBeCloseTo(1.1, 9);
    expect(zoomFactor({ mode: "pull", scale: 1.1 }, 1.5, 2)).toBeCloseTo(1.1 - 0.1 * (1 - Math.cos(0.75 * Math.PI)) / 2, 9);
    const p = prep([
      cue({ id: "kb0", kind: "zoom", start: 0, end: 6, mode: "push", scale: 1.05, anchor: "face" }),
      cue({ id: "z", kind: "zoom", start: 2, end: 3, mode: "punch", scale: 1.2, anchor: "face" }),
    ], 1080, 1920, { face_track: faceTrack() });
    const s = pictureStateAt(p, clockAt(p, 2.5), p.faceAt(2.5));
    expect(s.scale).toBeCloseTo(1.2 * zoomFactor({ mode: "push", scale: 1.05 }, 2.5, 6), 9);
    expect(pictureStateAt(p, clockAt(p, 1.99), null).scale).toBeCloseTo(zoomFactor({ mode: "push", scale: 1.05 }, 1.99, 6), 9);
  });

  it("anchors on the smoothed face centre, the frame centre, or (0.5, 0.38) without a face", () => {
    const z = (anchor: "face" | "center") => cue<FxCue>({ id: "z", kind: "zoom", start: 1, end: 2, mode: "punch", scale: 1.3, anchor });
    const withFace = prep([z("face")], 1080, 1920, { face_track: faceTrack(6, [0.02, 0]) });
    const s = pictureStateAt(withFace, clockAt(withFace, 1.5), withFace.faceAt(1.5));
    const c = smoothedFaceCenter(withFace, 1.5)!;
    expect([s.ax, s.ay]).toEqual(c);
    // Moving face, symmetric window: the smoothed centre equals the centre at t.
    expect(c[0]).toBeCloseTo(0.5 + 0.02 * 1.5, 6);
    expect(c[1]).toBeCloseTo(0.34, 6);
    const noFace = prep([z("face")]);
    const n = pictureStateAt(noFace, clockAt(noFace, 1.5), null);
    expect([n.ax, n.ay]).toEqual([...NO_FACE_ANCHOR]);
    const centre = prep([z("center")], 1080, 1920, { face_track: faceTrack() });
    const m = pictureStateAt(centre, clockAt(centre, 1.5), null);
    expect([m.ax, m.ay]).toEqual([0.5, 0.5]);
  });
});

describe("zoom face anchor through a detection gap", () => {
  it("holds the nearest face inside the cue instead of jumping to (0.5, 0.38)", () => {
    const track = faceTrack(6, [0.01, 0]);
    // The face is lost from 1.4 s to 2.4 s (a head turn) in the middle of a held punch.
    const gapped = { ...track, samples: track.samples.map(([t, rows]) => [t, t > 1.4 && t < 2.4 ? [] : rows] as [number, number[][]]) };
    const z = cue<FxCue>({ id: "z", kind: "zoom", start: 1, end: 3, mode: "punch", scale: 1.25, anchor: "face" });
    const p = prep([z], 1080, 1920, { face_track: gapped });
    expect(p.faceAt(1.9)).toBeNull();
    const before = pictureStateAt(p, clockAt(p, 1.3), p.faceAt(1.3));
    const inGap = pictureStateAt(p, clockAt(p, 1.9), null);
    expect([inGap.ax, inGap.ay]).not.toEqual([...NO_FACE_ANCHOR]);
    expect(Math.abs(inGap.ay - before.ay)).toBeLessThan(0.01);
    expect(Math.abs(inGap.ax - before.ax)).toBeLessThan(0.02);
    expect(zoomFaceAnchor(p, 1.9, 1, 3)).toEqual([inGap.ax, inGap.ay]);
    // No face anywhere in the cue: the default anchor.
    expect(zoomFaceAnchor(p, 1.9, 1.5, 2.3)).toEqual(NO_FACE_ANCHOR);
  });
});

describe("shake", () => {
  it("follows the §11.2 formula at 10 sample times", () => {
    const shake = cue<FxCue>({ id: "s", kind: "shake", start: 1, end: 1.5, amp: 18, freq: 12, seed: 77 });
    for (const [w, h] of ASPECTS) {
      const p = prep([shake], w, h);
      const u = Math.min(w, h) / 1080;
      const phi = [1, 2, 3, 4].map((n) => 2 * Math.PI * hash01(77, n));
      for (let i = 0; i < 10; i++) {
        const t = 1 + i * 0.05, local = t - 1, tau = local / 0.5;
        const wave = (a: number, b: number) => (Math.sin(2 * Math.PI * 12 * local + a) + 0.5 * Math.sin(2 * Math.PI * 1.7 * 12 * local + b)) / 1.5;
        const s = pictureStateAt(p, { e: t, o: t, frame: Math.round(t * 30) }, null);
        expect(s.dx).toBeCloseTo(18 * u * (1 - tau) * wave(phi[0], phi[1]), 9);
        expect(s.dy).toBeCloseTo(18 * u * (1 - tau) * wave(phi[2], phi[3]), 9);
      }
      expect(shakeOffset(shake as never, 0.5, 0.5, u, [0, 0, 0, 0])).toEqual([0, 0]);
    }
  });
});

describe("desaturate and glitch", () => {
  it("desaturate adds saturate(1 − amount) brightness(1 − dim) after the grade", () => {
    const p = prep([cue({ id: "d", kind: "desaturate", start: 1, end: 2, amount: 0.8, dim: 0.2 })], 1080, 1920, { grade: "punchy" });
    const [pic] = pictures(opsAt(p, 1.5));
    expect(pic.filter).toBe("contrast(1.12) saturate(1.25) saturate(0.2) brightness(0.8)");
    expect(pictures(opsAt(p, 2.1))[0].filter).toBe("contrast(1.12) saturate(1.25)");
  });

  it("glitch: 3–6 slices within ±40·amount·u, RGB split 6–14u, re-seeded every 2 frames", () => {
    for (const [w, h] of ASPECTS) {
      const u = Math.min(w, h) / 1080;
      const p = prep([cue({ id: "g", kind: "glitch", start: 1, end: 2, amount: 0.6 })], w, h);
      const states = [];
      for (let f = 30; f < 60; f++) {
        const s = pictureStateAt(p, { e: f / 30, o: f / 30, frame: f }, null);
        expect(s.slices.length).toBeGreaterThanOrEqual(3);
        expect(s.slices.length).toBeLessThanOrEqual(6);
        for (const sl of s.slices) {
          expect(Math.abs(sl.dx)).toBeLessThanOrEqual(40 * 0.6 * u + 1e-3);
          expect(sl.y).toBeGreaterThanOrEqual(0);
          expect(sl.y + sl.h).toBeLessThanOrEqual(h + 1e-3);
        }
        expect(s.rgbSplit).toBeGreaterThanOrEqual(6 * u - 1e-3);
        expect(s.rgbSplit).toBeLessThanOrEqual(14 * u + 1e-3);
        states.push(JSON.stringify(s));
      }
      for (let i = 0; i < states.length; i += 2) expect(states[i]).toBe(states[i + 1]);
      expect(new Set(states).size).toBe(15);
      const ops = opsAt(p, 1.5);
      expect(ops.slice(0, 3).map((o) => o.op === "picture" && o.channel)).toEqual(["r", "g", "b"]);
      expect(ops.filter((o) => o.op === "picture" && o.clip).length).toBeGreaterThanOrEqual(3);
    }
  });
});

describe("transitions", () => {
  it("whip, zoom_through and spin curves", () => {
    expect(whipAt(-1, 1)).toEqual({ k: 0, dx: -0 });
    expect(whipAt(0, 1)).toEqual({ k: 1, dx: 140 });
    expect(whipAt(-0.0001, 1).dx).toBeCloseTo(-140, 0);
    expect(whipAt(1, -1).k).toBe(0);
    expect(zoomThroughScale(-1)).toBe(1);
    expect(zoomThroughScale(0)).toBeCloseTo(1.35, 9);
    expect(zoomThroughScale(1)).toBe(1);
    expect(spinAngle(-1, 1)).toBe(0);
    expect(spinAngle(0, 1)).toBeCloseTo(Math.PI, 9);
    expect(spinAngle(1, -1)).toBeCloseTo(-2 * Math.PI, 9);
  });

  it("draw only within ±TRANSITION_HALF of the cut", () => {
    for (const style of STYLES) {
      const p = prep([cue({ id: "t", kind: "transition", start: 2, end: 2, style })]);
      const half = TRANSITION_HALF[style];
      const before = 2 - half - 2 / 30, inside = 2 - half / 3;
      expect(isPassthrough(p, clockAt(p, before))).toBe(true);
      expect(isPassthrough(p, clockAt(p, 2 + half + 2 / 30))).toBe(true);
      expect(isPassthrough(p, clockAt(p, inside))).toBe(false);
      expect(isPassthrough(p, clockAt(p, 2))).toBe(false);
    }
  });

  it("frameOps snapshots per transition style through the cut", () => {
    for (const style of STYLES) {
      const p = prep([cue({ id: "t", kind: "transition", start: 2, end: 2, style })], 1080, 1920, { face_track: faceTrack() });
      const half = TRANSITION_HALF[style];
      for (const k of [-0.6, -0.2, 0, 0.3, 0.8]) {
        const t = 2 + k * half;
        expect(snap(frameOps(p, { e: t, o: t, frame: Math.round(t * 30) }))).toMatchSnapshot(`${style} p=${k}`);
      }
      expect(drawerErrors(p)).toEqual([]);
    }
  });
});

describe("frameOps snapshots per picture cue kind", () => {
  const cases: Record<string, FxCue[]> = {
    punch: [cue({ id: "z", kind: "zoom", start: 1, end: 2, mode: "punch", scale: 1.2, anchor: "face" })],
    push: [cue({ id: "z", kind: "zoom", start: 0, end: 4, mode: "push", scale: 1.08, anchor: "face" })],
    pull: [cue({ id: "z", kind: "zoom", start: 0, end: 4, mode: "pull", scale: 1.15, anchor: "center" })],
    shake: [cue({ id: "s", kind: "shake", start: 1, end: 1.6, amp: 18, freq: 14 })],
    glitch: [cue({ id: "g", kind: "glitch", start: 1, end: 1.3, amount: 0.7 })],
    desaturate: [cue({ id: "d", kind: "desaturate", start: 1, end: 2, amount: 0.85, dim: 0.15 })],
  };
  for (const [name, cues] of Object.entries(cases)) {
    it(name, () => {
      for (const [w, h] of ASPECTS) {
        const p = prep(cues, w, h, { face_track: faceTrack() });
        for (const t of [0.5, 1.0, 1.1, 1.5, 2.5]) expect(snap(opsAt(p, t))).toMatchSnapshot(`${w}x${h} t=${t}`);
      }
    });
  }
});

describe("coverage (AR-M5): no frame corner is ever uncovered", () => {
  for (const [w, h] of ASPECTS) {
    it(`${w}x${h}: shake amp 40, whip, spin at every angle, zoom_through, glitch, punch toward a corner face`, () => {
      const scenes: [string, FxCue[], number, number][] = [
        ["shake", [cue({ id: "s", kind: "shake", start: 0.5, end: 2.5, amp: 40, freq: 18 })], 0.5, 2.5],
        ["whip", [cue({ id: "t", kind: "transition", start: 2, end: 2, style: "whip" })], 1.85, 2.15],
        ["spin", [cue({ id: "t", kind: "transition", start: 2, end: 2, style: "spin" })], 1.78, 2.22],
        ["zoom_through", [cue({ id: "t", kind: "transition", start: 2, end: 2, style: "zoom_through" })], 1.8, 2.2],
        ["glitch", [cue({ id: "t", kind: "transition", start: 2, end: 2, style: "glitch" })], 1.88, 2.12],
        ["punch+shake+whip", [
          cue({ id: "z", kind: "zoom", start: 1, end: 3, mode: "punch", scale: 1.2, anchor: "face" }),
          cue({ id: "s", kind: "shake", start: 1, end: 3, amp: 40, freq: 18 }),
          cue({ id: "t", kind: "transition", start: 2, end: 2, style: "whip" }),
          cue({ id: "g", kind: "glitch", start: 2.5, end: 2.8, amount: 1 }),
        ], 1, 3],
      ];
      for (const [name, cues, a, b] of scenes) {
        // A face in the top-left corner pulls the punch anchor toward the edge; the clamp must still cover.
        const p = prep(cues, w, h, { face_track: faceTrack(6, [-0.05, -0.04]) });
        for (let f = Math.floor(a * 30); f <= Math.ceil(b * 30); f++) assertCovered(opsAt(p, f / 30), w, h, `${name} f=${f}`);
        // Sub-frame sampling, so spin is checked at (nearly) every angle.
        for (let t = a; t <= b; t += 0.002) assertCovered(frameOps(p, { e: t, o: t, frame: Math.round(t * 30) }), w, h, `${name} t=${t.toFixed(3)}`);
      }
    });
  }
});

describe("raster goldens", () => {
  const W = 540, H = 960;
  const shot = (name: string, cues: FxCue[], o: number, extra = {}) =>
    expectGolden(`picture__${name}`, renderSceneToPng(bareScene({ width: W, height: H, cues, face_track: faceTrack(), ...extra }), o));
  it("zoom, shake, desaturate, glitch", () => {
    shot("punch_face", [cue({ id: "z", kind: "zoom", start: 1, end: 2, mode: "punch", scale: 1.25, anchor: "face" })], 1.5);
    shot("shake", [cue({ id: "s", kind: "shake", start: 1, end: 1.6, amp: 30, freq: 14 })], 1.1);
    shot("desaturate", [cue({ id: "d", kind: "desaturate", start: 1, end: 2, amount: 0.85, dim: 0.15 })], 1.5);
    shot("glitch", [cue({ id: "g", kind: "glitch", start: 1, end: 1.3, amount: 0.9 })], 1.1);
  });
  it("transitions at the cut", () => {
    for (const style of STYLES) {
      const at = style === "spin" ? 2 - 0.07 : style === "whip" ? 2 - 0.03 : 2;
      shot(`transition_${style}`, [cue({ id: "t", kind: "transition", start: 2, end: 2, style })], at);
    }
  });
});

describe("isPassthrough is false exactly while a cue is active", () => {
  // Every cue spans [1, 1.5) E (frames 30..44); the frame before and the frame at the end pass through untouched.
  const span = { start: 1, end: 1.5 };
  const kinds: Record<string, FxCue> = {
    punch: cue({ id: "c", kind: "zoom", ...span, mode: "punch", scale: 1.2, anchor: "face" }),
    pull: cue({ id: "c", kind: "zoom", ...span, mode: "pull", scale: 1.1, anchor: "center" }),
    shake: cue({ id: "c", kind: "shake", ...span, amp: 18, freq: 14 }),
    glitch: cue({ id: "c", kind: "glitch", ...span, amount: 0.5 }),
    desaturate: cue({ id: "c", kind: "desaturate", ...span, amount: 0.6, dim: 0.1 }),
    flash: cue({ id: "c", kind: "flash", ...span, color: "#FFFFFF", peak: 0.8 }),
    bleep_bar: cue({ id: "c", kind: "bleep", ...span, tone: true, cover: "bar" }),
    bleep_pixelate: cue({ id: "c", kind: "bleep", ...span, tone: true, cover: "pixelate" }),
    sticker_text: cue({ id: "c", kind: "sticker", ...span, sticker: "text", text: "no way" }),
    wait_for_it: cue({ id: "c", kind: "sticker", ...span, sticker: "wait_for_it", text: "" }),
    reaction: cue({ id: "c", kind: "reaction", ...span, query: "wow", provider: "giphy", asset_id: "a1", url: "" }),
  };
  for (const [name, c] of Object.entries(kinds)) {
    it(name, () => {
      for (const [w, h] of ASPECTS) {
        const p = prep([c], w, h, { face_track: faceTrack() });
        expect(isPassthrough(p, clockAt(p, 29 / 30)), `${name} before`).toBe(true);
        for (let f = 30; f < 45; f++) expect(isPassthrough(p, clockAt(p, f / 30)), `${name} f=${f}`).toBe(false);
        expect(isPassthrough(p, clockAt(p, 45 / 30)), `${name} at end`).toBe(true);
        expect(drawerErrors(p)).toEqual([]);
      }
    });
  }

  it("push is identity on its first frame only, then active until its end", () => {
    const p = prep([cue({ id: "c", kind: "zoom", ...span, mode: "push", scale: 1.1, anchor: "face" })], 1080, 1920, { face_track: faceTrack() });
    expect(isPassthrough(p, clockAt(p, 1))).toBe(true);
    for (let f = 31; f < 45; f++) expect(isPassthrough(p, clockAt(p, f / 30))).toBe(false);
    expect(isPassthrough(p, clockAt(p, 1.5))).toBe(true);
  });

  it("nothing to draw means pass through: no landmarks for a bleep cover, no asset for a reaction, cover none", () => {
    const quiet: FxCue[] = [
      cue({ id: "a", kind: "bleep", ...span, tone: true, cover: "bar" }),
      cue({ id: "b", kind: "reaction", ...span, query: "wow", provider: "giphy", asset_id: "", url: "" }),
      cue({ id: "c", kind: "bleep", ...span, tone: true, cover: "none" }),
    ];
    const p = prep(quiet, 1080, 1920);   // no face track
    for (let f = 30; f < 45; f++) expect(isPassthrough(p, clockAt(p, f / 30))).toBe(true);
  });
});
