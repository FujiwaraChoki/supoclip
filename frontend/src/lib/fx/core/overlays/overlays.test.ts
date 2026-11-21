// @vitest-environment node
import { describe, expect, it } from "vitest";
import { PNG } from "pngjs";
import { clockAt, drawerErrors, frameOps, isPassthrough } from "../frame";
import type { DrawOp, FxContext2D } from "../host";
import { applyMat, overlapArea, safeRect } from "../layout";
import { reactionRect } from "../constants";
import { prepareScene } from "../scene";
import type { FxCue } from "../types";
import { captionStyle, fakeMeasureHost, sampleWords } from "../test-utils/fake-host";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import { drawTestPicture, drawTexturedTestPicture } from "../test-utils/napi-host";
import { ASPECTS, bareScene, cue, faceTrack, snap } from "../picture/fixtures";
import { flashAlpha } from "./flash";
import { mouthBar } from "./bleep_cover";
import { stickerBox } from "../looks/sticker";
import { stickerObstacles, TEXT_STICKER_SCALE, WAIT_FOR_IT_TEXT } from "./sticker";
import { GRAIN_SEED } from "./grain";

type PictureOp = Extract<DrawOp, { op: "picture" }>;
type RectOp = Extract<DrawOp, { op: "rect" }>;
type TextOp = Extract<DrawOp, { op: "text" }>;
const prep = (cues: FxCue[], w = 1080, h = 1920, extra = {}) => prepareScene(bareScene({ width: w, height: h, cues, ...extra }), fakeMeasureHost);
const opsAt = (p: ReturnType<typeof prep>, o: number) => frameOps(p, clockAt(p, o));
const nonPicture = (ops: DrawOp[]) => ops.filter((o) => o.op !== "picture");

describe("flash", () => {
  it("peaks at start, decays linearly, lasts at least 2 frames", () => {
    const c = { start: 1, end: 1.3, peak: 0.8 };
    expect(flashAlpha(c, 0)).toBe(0.8);
    expect(flashAlpha(c, 0.15)).toBe(0.4);
    expect(flashAlpha(c, 0.3)).toBe(0);
    const instant = cue<FxCue>({ id: "f", kind: "flash", start: 1, end: 1, color: "#FF0000", peak: 1 });
    const p = prep([instant]);
    const f0 = nonPicture(opsAt(p, 1)) as RectOp[];
    const f1 = nonPicture(opsAt(p, 1 + 1 / 30)) as RectOp[];
    expect(f0).toHaveLength(1);
    expect(f0[0]).toMatchObject({ fill: "#FF0000", alpha: 1, w: 1080, h: 1920 });
    expect(f1[0].alpha).toBeCloseTo(0.5, 3);
    expect(nonPicture(opsAt(p, 1 + 2 / 30))).toEqual([]);
  });
});

describe("bleep cover", () => {
  const swear = (cover: "bar" | "pixelate" | "none") => cue<FxCue>({ id: "b", kind: "bleep", start: 1, end: 2, tone: true, cover });

  it("the bar stays on the mouth under punch 1.2 + shake 18 (transformed landmarks)", () => {
    for (const [w, h] of ASPECTS) {
      const p = prep([
        swear("bar"),
        cue({ id: "z", kind: "zoom", start: 0.5, end: 2.5, mode: "punch", scale: 1.2, anchor: "face" }),
        cue({ id: "s", kind: "shake", start: 0.9, end: 2.1, amp: 18, freq: 16 }),
      ], w, h, { face_track: faceTrack(6, [0.01, 0.005]) });
      for (let f = 30; f < 60; f++) {
        const ops = opsAt(p, f / 30);
        const pic = ops[0] as PictureOp;
        const raw = p.faceAt(f / 30)!.landmarks!;
        const [ax, ay] = applyMat(pic.transform, raw[6], raw[7]);
        const [bx, by] = applyMat(pic.transform, raw[8], raw[9]);
        const bar = ops.find((o): o is RectOp => o.op === "rect" && o.fill === "#000000")!;
        expect(bar).toBeTruthy();
        // Bar centre (transform origin) on the transformed mouth centre; width = 1.7 × transformed mouth width.
        expect(bar.transform[4]).toBeCloseTo((ax + bx) / 2, 6);
        expect(bar.transform[5]).toBeCloseTo((ay + by) / 2, 6);
        expect(bar.w).toBeCloseTo(1.7 * Math.hypot(bx - ax, by - ay), 2);
        expect(bar.h).toBeCloseTo(0.45 * Math.hypot(bx - ax, by - ay), 2);
      }
    }
  });

  it("pixelate covers the bar's bounds in 24u blocks; nothing without landmarks or with cover none", () => {
    const p = prep([swear("pixelate")], 1080, 1920, { face_track: faceTrack() });
    const [px] = nonPicture(opsAt(p, 1.5));
    const bar = mouthBar(p.faceAt(1.5))!;
    expect(px).toMatchObject({ op: "pixelate", block: 24 });
    if (px.op === "pixelate") {
      expect(px.x).toBeCloseTo(bar.cx - bar.w / 2, 2);
      expect(px.w).toBeCloseTo(bar.w, 2);
    }
    const noLandmarks = prep([swear("bar")], 1080, 1920, { face_track: { version: 1, space: "output", fps: 6, samples: [[1, [[0.3, 0.2, 0.3, 0.2, 0.9]]], [2, [[0.3, 0.2, 0.3, 0.2, 0.9]]]] } });
    expect(nonPicture(opsAt(noLandmarks, 1.5))).toEqual([]);
    expect(nonPicture(opsAt(prep([swear("bar")]), 1.5))).toEqual([]);
    expect(nonPicture(opsAt(prep([swear("none")], 1080, 1920, { face_track: faceTrack() }), 1.5))).toEqual([]);
  });
});

describe("reaction frame", () => {
  const reaction = (url: string) => cue<FxCue>({ id: "r", kind: "reaction", start: 1, end: 2.5, query: "shocked", provider: "giphy", asset_id: url ? "abc" : "", url });

  it("frames reactionRect with the picture transform and credits GIPHY; nothing without an asset", () => {
    for (const [w, h] of ASPECTS) {
      const p = prep([reaction("https://media.giphy.com/x.mp4"), cue({ id: "z", kind: "zoom", start: 1.5, end: 2, mode: "punch", scale: 1.2, anchor: "center" })], w, h);
      for (const o of [1.2, 1.7]) {
        const ops = opsAt(p, o);
        const pic = ops[0] as PictureOp;
        const own = nonPicture(ops);
        for (const op of own) expect(op.op === "rect" || op.op === "text" ? op.transform : null).toEqual(pic.transform);
        const border = own.find((x): x is RectOp => x.op === "rect" && x.stroke === "#FFFFFF")!;
        const r = reactionRect(w, h), u = Math.min(w, h) / 1080;
        expect(border.strokePx).toBeCloseTo(10 * u, 3);
        // The 10u band covers [rect − 10u, rect]: its centre line runs 5u outside the PiP.
        expect(border.x).toBeCloseTo(r.x - 5 * u, 3);
        expect(border.w).toBeCloseTo(r.w + 10 * u, 3);
        const credit = own.find((x): x is TextOp => x.op === "text")!;
        expect(credit.text).toBe("via GIPHY");
        expect(credit.px).toBeCloseTo(22 * u, 3);
        expect(credit.y).toBeGreaterThan(r.y + r.h);
      }
      expect(nonPicture(opsAt(prep([reaction("")], w, h), 1.5))).toEqual([]);
    }
  });
});

describe("stickers", () => {
  const text = (t = "NO WAY", start = 1, end = 2.2) => cue<FxCue>({ id: `st-${t}`, kind: "sticker", start, end, sticker: "text", text: t });
  const wait = (start = 1, end = 2.6) => cue<FxCue>({ id: "wfi", kind: "sticker", start, end, sticker: "wait_for_it", text: "" });

  it("text stickers sit in the safe area, off the face and the caption, tilted within ±8°", () => {
    for (const [w, h] of ASPECTS) {
      const scene = bareScene({
        width: w, height: h, cues: [text()], face_track: faceTrack(), words: sampleWords(),
        caption: captionStyle({}, Math.min(w, h)),
      });
      for (let seed = 1; seed <= 12; seed++) {
        const p = prepareScene({ ...scene, cues: [{ ...text(), seed }] }, fakeMeasureHost);
        const ops = frameOps(p, clockAt(p, 1.5)).filter((o): o is TextOp => o.op === "text" && o.font === "FX-Poppins-ExtraBold");
        expect(ops.length).toBeGreaterThan(0);
        const m = ops[0].transform;
        const rot = Math.atan2(m[1], m[0]);
        expect(Math.abs(rot)).toBeLessThanOrEqual((8 * Math.PI) / 180 + 1e-9);
        const cx = m[4], cy = m[5];
        const area = safeRect(w, h, false);
        expect(cx).toBeGreaterThanOrEqual(area.x);
        expect(cx).toBeLessThanOrEqual(area.x + area.w);
        expect(cy).toBeGreaterThanOrEqual(area.y);
        expect(cy).toBeLessThanOrEqual(area.y + area.h);
        // The sticker's own box (unrotated, at rest) misses every obstacle (face, caption).
        const scale = Math.hypot(m[0], m[1]);
        const size = stickerBox("NO WAY", scale, Math.min(w, h) / 1080, p.measure);
        const box = { x: cx - size.w / 2, y: cy - size.h / 2, w: size.w, h: size.h };
        for (const r of stickerObstacles(p, 1)) expect(overlapArea(box, r), `${w}x${h} seed ${seed}`).toBe(0);
      }
    }
  });

  it("slap in 1.3 → 1 over 90 ms, pop out over the last 100 ms, never moves while on screen", () => {
    const p = prep([text()], 1080, 1920, { face_track: faceTrack() });
    const first = (o: number) => (nonPicture(opsAt(p, o)) as TextOp[])[0];
    expect(Math.hypot(first(1).transform[0], first(1).transform[1])).toBeCloseTo(1.3 * TEXT_STICKER_SCALE, 3);
    expect(Math.hypot(first(1.1).transform[0], first(1.1).transform[1])).toBeCloseTo(TEXT_STICKER_SCALE, 3);
    expect(first(1.5).transform[4]).toBeCloseTo(first(1.9).transform[4], 6);
    expect(first(2.15).alpha).toBeLessThan(1);
    expect(nonPicture(opsAt(p, 2.21))).toEqual([]);
  });

  it("wait_for_it: the yellow sticker in the top third, bobbing 2u at 2 Hz, out of the hook band", () => {
    for (const [w, h] of ASPECTS) {
      const p = prep([wait(0.2, 2.6)], w, h, { face_track: faceTrack(), hook_window: [0, 1.5] });
      const at = (o: number) => (nonPicture(opsAt(p, o)) as TextOp[]);
      const ops = at(1);
      expect(ops.map((o) => o.text)).toContain(WAIT_FOR_IT_TEXT);
      expect(ops.some((o) => o.fill === "#FFD23F")).toBe(true);
      const y = ops[0].transform[5];
      expect(y).toBeLessThan(h / 3 + 1e-6);
      expect(y - 50 * Math.min(w, h) / 1080).toBeGreaterThanOrEqual(0.2 * h);
      const u = Math.min(w, h) / 1080;
      const bob = (o: number) => 2 * u * Math.sin(2 * Math.PI * 2 * (o - 0.2));
      expect(at(1.125)[0].transform[5] - at(1)[0].transform[5]).toBeCloseTo(bob(1.125) - bob(1), 3);
    }
  });

  it("text and wait_for_it draw in their own layers", () => {
    const p = prep([text("LOL", 1, 2), wait(1, 2)], 1080, 1920);
    const ops = nonPicture(opsAt(p, 1.5)) as TextOp[];
    const lol = ops.findIndex((o) => o.text === "LOL"), wfi = ops.findIndex((o) => o.text === WAIT_FOR_IT_TEXT);
    expect(lol).toBeGreaterThanOrEqual(0);
    expect(wfi).toBeGreaterThan(lol);
  });
});

describe("grain", () => {
  it("one grain op at grain·0.35, seed 4242, the output frame index", () => {
    const p = prep([], 1080, 1920, { grain: 0.5 });
    expect(nonPicture(opsAt(p, 1))).toEqual([{ op: "grain", amount: 0.175, seed: GRAIN_SEED, frame: 30 }]);
    expect(isPassthrough(p, clockAt(p, 1))).toBe(false);
  });
});

describe("frameOps snapshots per overlay cue kind", () => {
  const cases: Record<string, [FxCue[], number[]]> = {
    flash: [[cue({ id: "f", kind: "flash", start: 1, end: 1.2, color: "#FFFFFF", peak: 0.85 })], [1, 1.1, 1.19]],
    bleep_bar: [[cue({ id: "b", kind: "bleep", start: 1, end: 1.5, tone: true, cover: "bar" })], [1, 1.25]],
    bleep_pixelate: [[cue({ id: "b", kind: "bleep", start: 1, end: 1.5, tone: true, cover: "pixelate" })], [1.25]],
    sticker_text: [[cue({ id: "s", kind: "sticker", start: 1, end: 2, sticker: "text", text: "plot twist" })], [1, 1.05, 1.5, 1.95]],
    sticker_wait: [[cue({ id: "w", kind: "sticker", start: 1, end: 2, sticker: "wait_for_it", text: "" })], [1, 1.5]],
    reaction: [[cue({ id: "r", kind: "reaction", start: 1, end: 2, query: "wow", provider: "giphy", asset_id: "a1", url: "u" })], [1.5]],
  };
  for (const [name, [cues, times]] of Object.entries(cases)) {
    it(name, () => {
      for (const [w, h] of ASPECTS) {
        const p = prep(cues, w, h, { face_track: faceTrack() });
        for (const t of times) expect(snap(opsAt(p, t))).toMatchSnapshot(`${w}x${h} t=${t}`);
        expect(drawerErrors(p)).toEqual([]);
      }
    });
  }
});

describe("raster goldens", () => {
  const W = 540, H = 960;
  const shot = (name: string, cues: FxCue[], o: number, extra = {}, picture?: (ctx: FxContext2D, cutout: boolean) => void) =>
    expectGolden(`overlay__${name}`, renderSceneToPng(bareScene({ width: W, height: H, cues, face_track: faceTrack(), ...extra }), o, picture ? { picture } : {}));
  it("flash, bleep, stickers, grain", () => {
    shot("flash", [cue({ id: "f", kind: "flash", start: 1, end: 1.2, color: "#FFFFFF", peak: 0.85 })], 1.05);
    shot("bleep_bar", [
      cue({ id: "b", kind: "bleep", start: 1, end: 1.5, tone: true, cover: "bar" }),
      cue({ id: "z", kind: "zoom", start: 1, end: 1.5, mode: "punch", scale: 1.2, anchor: "face" }),
    ], 1.2);
    // A textured face: pixelation must visibly average the checker over the mouth (a flat face would prove nothing).
    shot("bleep_pixelate", [cue({ id: "b", kind: "bleep", start: 1, end: 1.5, tone: true, cover: "pixelate" })], 1.2, {},
      (ctx, cutout) => drawTexturedTestPicture(ctx, W, H, cutout));
    shot("sticker_text", [cue({ id: "s", kind: "sticker", start: 1, end: 2, sticker: "text", text: "plot twist" })], 1.5,
      { words: sampleWords(), caption: captionStyle({}, W) });
    shot("sticker_wait_for_it", [cue({ id: "w", kind: "sticker", start: 1, end: 2, sticker: "wait_for_it", text: "" })], 1.5);
    shot("grain", [], 1, { grain: 1 });
  });
  it("reaction frame over a PiP, moving with a punch-in", () => {
    const r = reactionRect(W, H);
    const pip = (ctx: FxContext2D, cutout: boolean) => {
      drawTestPicture(ctx, W, H, cutout);
      if (cutout) return;
      const g = ctx.createLinearGradient(r.x, r.y, r.x + r.w, r.y + r.h);
      g.addColorStop(0, "#3a7bd5");
      g.addColorStop(1, "#00d2ff");
      ctx.fillStyle = g;
      ctx.fillRect(r.x, r.y, r.w, r.h);
    };
    shot("reaction", [
      cue({ id: "r", kind: "reaction", start: 1, end: 2, query: "wow", provider: "giphy", asset_id: "a1", url: "u" }),
      cue({ id: "z", kind: "zoom", start: 1, end: 2, mode: "punch", scale: 1.15, anchor: "center" }),
    ], 1.5, {}, pip);
  });
  it("a bleep bar rasterises black over the mouth", () => {
    const scene = bareScene({ width: W, height: H, cues: [cue({ id: "b", kind: "bleep", start: 1, end: 1.5, tone: true, cover: "bar" })], face_track: faceTrack() });
    const img = PNG.sync.read(renderSceneToPng(scene, 1.2));
    const bar = mouthBar(prepareScene(scene, fakeMeasureHost).faceAt(1.2))!;
    const at = (x: number, y: number) => Array.from(img.data.subarray((Math.round(y) * W + Math.round(x)) * 4, (Math.round(y) * W + Math.round(x)) * 4 + 3));
    expect(at(bar.cx, bar.cy)).toEqual([0, 0, 0]);
    expect(at(bar.cx, bar.cy + bar.h)).not.toEqual([0, 0, 0]);
  });
});

describe("sticker obstacles on caption rows", () => {
  it("keeps the caption band of the row in effect clear when no caption shows", () => {
    const rows = [{ start: 0, end: 2, y: 0.5 }, { start: 2, end: 9, y: 0.76 }];
    const p = prepareScene(bareScene({ caption: captionStyle({ y: 0.5, rows }), words: [] }), fakeMeasureHost);
    const band = (t: number) => {
      const r = stickerObstacles(p, t).find((o) => o.w === 1080)!;
      return r.y + r.h / 2;
    };
    expect(band(1)).toBeCloseTo(0.5 * 1920, 6);
    expect(band(3)).toBeCloseTo(0.76 * 1920, 6);
  });
});
