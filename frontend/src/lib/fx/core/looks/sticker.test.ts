import { describe, expect, it } from "vitest";
import { slapAlpha, slapScale, STICKER_EDGE_EM, STICKER_PALETTES, STICKER_PX, stickerBox, stickerOps, stickerPalette, stickerTilt } from "./sticker";
import type { DrawOp } from "../host";
import { applyMat } from "../layout";
import { fakeMeasure } from "../test-utils/fake-host";

type Text = Extract<DrawOp, { op: "text" }>;
const texts = (ops: DrawOp[]) => ops as Text[];

describe("sticker look", () => {
  it("draws the white die-cut edge (with the hard shadow), then the keyline, then the coloured fill", () => {
    const ops = texts(stickerOps("NO WAY", 300, 400, 0.1, 1, STICKER_PALETTES[0], 1));
    expect(ops.map((o) => [o.fill, o.stroke])).toEqual([["#FFFFFF", "#FFFFFF"], [null, "#2A0710"], ["#FF3B5C", null]]);
    expect(ops[0].strokePx).toBeCloseTo(STICKER_EDGE_EM * STICKER_PX, 9);
    expect(ops[0].shadow).toEqual({ color: "rgba(0,0,0,0.35)", blur: 0, dx: 0, dy: 6 });
    expect(ops.slice(1).every((o) => o.shadow === null)).toBe(true);
    expect(ops.every((o) => o.join === "round" && o.align === "center" && o.font === "FX-Poppins-ExtraBold")).toBe(true);
  });

  it("places, tilts and scales every layer through one transform", () => {
    const ops = texts(stickerOps("HI", 300, 400, Math.PI / 2, 2, "#123456", 1));
    for (const o of ops) expect(o.transform).toEqual(ops[0].transform);
    const [x, y] = applyMat(ops[0].transform, 10, 0);
    expect(x).toBeCloseTo(300, 9);
    expect(y).toBeCloseTo(420, 9);
    expect(ops[0].shadow!.dy).toBe(12);
    expect(ops[2].fill).toBe("#123456");
  });

  it("authors in u units (720p and landscape outputs keep proportions)", () => {
    const small = texts(stickerOps("A", 0, 0, 0, 1, STICKER_PALETTES[1], 720 / 1080));
    expect(small[0].px).toBeCloseTo(STICKER_PX * (720 / 1080), 9);
  });

  it("stacks multi-line text around the centre and skips empty text", () => {
    const ops = texts(stickerOps("plot\ntwist", 0, 0, 0, 1, STICKER_PALETTES[3], 1));
    expect(ops.filter((o) => o.fill === "#7CF25B").map((o) => o.y)).toEqual([-STICKER_PX * 0.56, STICKER_PX * 0.56]);
    expect(stickerOps("  ", 0, 0, 0, 1, STICKER_PALETTES[0], 1)).toEqual([]);
    expect(stickerOps("x", 0, 0, 0, 0, STICKER_PALETTES[0], 1)).toEqual([]);
  });

  it("seeded palette, tilt within ±8° and the 90 ms slap-in", () => {
    expect(stickerPalette(0)).toBe(STICKER_PALETTES[0]);
    expect(stickerPalette(0.999)).toBe(STICKER_PALETTES[3]);
    expect(stickerPalette("#ABCDEF").fill).toBe("#ABCDEF");
    for (const d of [0, 0.25, 0.5, 0.999]) expect(Math.abs(stickerTilt(d))).toBeLessThanOrEqual((8 * Math.PI) / 180 + 1e-12);
    expect(slapScale(0)).toBeCloseTo(1.3, 9);
    expect(slapScale(0.045)).toBeGreaterThan(1);
    expect(slapScale(0.09)).toBe(1);
    expect(slapScale(1)).toBe(1);
    expect(slapAlpha(-0.1)).toBe(0);
    expect(slapAlpha(0.05)).toBe(1);
  });

  it("reports a placement box that covers the edge and grows with scale", () => {
    const one = stickerBox("WAIT FOR IT…", 1, 1, fakeMeasure);
    const two = stickerBox("WAIT FOR IT…", 2, 1, fakeMeasure);
    expect(one.w).toBeGreaterThan(fakeMeasure("WAIT FOR IT…", "", STICKER_PX));
    expect(two.w).toBeCloseTo(one.w * 2, 9);
    expect(stickerBox("a\nb", 1, 1, fakeMeasure).h).toBeGreaterThan(one.h);
  });
});
