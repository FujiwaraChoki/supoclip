// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createCanvas } from "@napi-rs/canvas";
import { clockAt, drawerErrors, frameOps, isPassthrough } from "../frame";
import type { DrawOp, FxContext2D } from "../host";
import { prepareScene } from "../scene";
import type { FxCue } from "../types";
import { buildWarp, toOutput } from "../warp";
import { fakeMeasureHost } from "../test-utils/fake-host";
import { expectGolden, renderSceneToPng } from "../test-utils/golden";
import { ASPECTS, bareScene, cue, faceTrack, snap } from "../picture/fixtures";
import { FREEZE_LABEL_SCALE, freezeHold } from "./freeze_label";
import { loopAlpha } from "./loop";
import { progressFraction } from "./progress";

type RectOp = Extract<DrawOp, { op: "rect" }>;
type TextOp = Extract<DrawOp, { op: "text" }>;
const nonPicture = (ops: DrawOp[]) => ops.filter((o) => o.op !== "picture");

function freezeScene(w = 1080, h = 1920, label = "WAIT…") {
  const freeze = cue<FxCue>({ id: "f", kind: "freeze", start: 2.017, end: 2.017, hold: 0.6, label });
  const warp = buildWarp([freeze], 5);
  return prepareScene(bareScene({ width: w, height: h, cues: [freeze], edit_duration: 5, out_duration: warp.outDuration, warp: [...warp.pieces], face_track: faceTrack() }), fakeMeasureHost);
}

describe("progress bar", () => {
  it("a 10u bar at the bottom edge, width o / out_duration, on the F clock", () => {
    for (const [w, h] of ASPECTS) {
      const u = Math.min(w, h) / 1080;
      const p = prepareScene(bareScene({ width: w, height: h, progress_bar: true, progress_color: "#22CC88", edit_duration: 4, out_duration: 5 }), fakeMeasureHost);
      for (const o of [0.5, 2.5, 4.9]) {
        const [track, bar] = nonPicture(frameOps(p, clockAt(p, o))) as RectOp[];
        expect(track).toMatchObject({ x: 0, w, fill: "#000000" });
        expect(bar.fill).toBe("#22CC88");
        expect(bar.h).toBeCloseTo(10 * u, 3);
        expect(bar.y + bar.h).toBeCloseTo(h, 3);
        expect(bar.w).toBeCloseTo(w * (o / 5), 2);
      }
      expect(nonPicture(frameOps(p, clockAt(p, 0)))).toHaveLength(1);
    }
    expect(progressFraction(9, 5)).toBe(1);
    expect(progressFraction(1, 0)).toBe(0);
  });
});

describe("freeze label", () => {
  it("draws only during the hold (F clock), slaps in at the hold start, rim flash on the first 2 frames", () => {
    const p = freezeScene();
    const hold = freezeHold(p, { start: 2.017 })!;
    expect(hold.o0).toBeCloseTo(toOutput(p.warp, 61 / 30), 9);   // quantize(2.017) = 61/30
    expect(hold.hold).toBeCloseTo(0.6, 9);
    const first = Math.round(hold.o0 * 30), frames = Math.round(hold.hold * 30);
    const at = (n: number) => nonPicture(frameOps(p, clockAt(p, n / 30)));
    expect(at(first - 1)).toEqual([]);
    expect(at(first + frames)).toEqual([]);   // the frame that plays on after the hold
    for (let n = first; n < first + frames; n++) expect(at(n).some((o) => o.op === "text")).toBe(true);
    const rims = (n: number) => (at(n) as RectOp[]).filter((o) => o.op === "rect" && o.stroke === "#FFFFFF");
    expect(rims(first)[0].alpha).toBe(1);
    expect(rims(first + 1)[0].alpha).toBe(0.5);
    expect(rims(first + 2)).toEqual([]);
    const label = (n: number) => (at(n) as TextOp[]).find((o) => o.op === "text" && o.text === "WAIT…")!;
    expect(Math.hypot(label(first).transform[0], label(first).transform[1])).toBeCloseTo(1.3 * FREEZE_LABEL_SCALE, 3);
    expect(Math.hypot(label(first + 5).transform[0], label(first + 5).transform[1])).toBeCloseTo(FREEZE_LABEL_SCALE, 3);
    expect(drawerErrors(p)).toEqual([]);
  });

  it("no hold in the render's warp (safe mode / FX_WARP=off) ⇒ nothing; an empty label draws only the rim", () => {
    const freeze = cue<FxCue>({ id: "f", kind: "freeze", start: 2, end: 2, hold: 0.6, label: "WAIT" });
    const p = prepareScene(bareScene({ cues: [freeze], edit_duration: 5 }), fakeMeasureHost);
    for (let n = 55; n < 70; n++) expect(nonPicture(frameOps(p, clockAt(p, n / 30)))).toEqual([]);
    const bare = freezeScene(1080, 1920, "");
    const hold = freezeHold(bare, { start: 2.017 })!;
    const n = Math.round(hold.o0 * 30);
    expect(nonPicture(frameOps(bare, clockAt(bare, n / 30))).every((o) => o.op === "rect")).toBe(true);
    expect(nonPicture(frameOps(bare, clockAt(bare, (n + 3) / 30)))).toEqual([]);
  });

  it("snapshots", () => {
    for (const [w, h] of ASPECTS) {
      const p = freezeScene(w, h);
      const hold = freezeHold(p, { start: 2.017 })!;
      for (const k of [0, 1, 4, 15]) expect(snap(frameOps(p, clockAt(p, hold.o0 + k / 30)))).toMatchSnapshot(`${w}x${h} hold+${k}`);
    }
  });
});

describe("loop crossfade", () => {
  it("frame0 alpha 0 → 1 over the last 0.4 s of F", () => {
    expect(loopAlpha(4.5, 5)).toBe(0);
    expect(loopAlpha(4.6, 5)).toBe(0);
    expect(loopAlpha(4.8, 5)).toBe(0.5);
    expect(loopAlpha(5, 5)).toBe(1);
    const p = prepareScene(bareScene({ loop_ending: "crossfade", edit_duration: 4, out_duration: 5 }), fakeMeasureHost);
    expect(isPassthrough(p, clockAt(p, 4.5))).toBe(true);
    expect(nonPicture(frameOps(p, clockAt(p, 4.9)))).toEqual([{ op: "frame0", alpha: 0.75 }]);
    const off = prepareScene(bareScene({ loop_ending: "none", edit_duration: 4, out_duration: 5 }), fakeMeasureHost);
    expect(isPassthrough(off, clockAt(off, 4.9))).toBe(true);
  });
});

describe("raster goldens", () => {
  const W = 540, H = 960;
  it("progress bar, freeze label, loop crossfade", () => {
    expectGolden("retention__progress", renderSceneToPng(bareScene({ width: W, height: H, progress_bar: true, progress_color: "#FF7A1A" }), 2.4));
    const freeze = cue<FxCue>({ id: "f", kind: "freeze", start: 2, end: 2, hold: 0.6, label: "WAIT FOR IT" });
    const warp = buildWarp([freeze], 5);
    const scene = bareScene({ width: W, height: H, cues: [freeze], edit_duration: 5, out_duration: warp.outDuration, warp: [...warp.pieces], face_track: faceTrack() });
    expectGolden("retention__freeze_rim", renderSceneToPng(scene, 2));
    expectGolden("retention__freeze_label", renderSceneToPng(scene, 2 + 6 / 30));
    // frame0: a warm graded still standing in for the clip's first frame.
    const still = createCanvas(W, H);
    const sctx = still.getContext("2d") as unknown as FxContext2D;
    sctx.fillStyle = "#F2C14E";
    sctx.fillRect(0, 0, W, H);
    expectGolden("retention__loop", renderSceneToPng(bareScene({ width: W, height: H, loop_ending: "crossfade" }), 5.85, { frame0: still as never }));
  });
});
