import { describe, expect, it } from "vitest";
import {
  applyMat, clampBox, cutoutOp, invert, isIdentity, mul, pictureMatrix, pictureOps, placeAt, placeAvoiding, rotateAbout,
  safeRect, scaleAbout, transformOps, transformRect, translation,
} from "./layout";
import { coverScale, IDENTITY_PICTURE, type PictureState } from "./registry";

const ASPECTS = [[1080, 1920], [1080, 1080], [1920, 1080]] as const;
const state = (s: Partial<PictureState>): PictureState => ({ ...IDENTITY_PICTURE, filter: [], slices: [], ...s });

/** Every frame corner, pulled back through each picture draw, must land inside the source picture. */
function uncovered(st: PictureState, w: number, h: number): string | null {
  for (const op of pictureOps(st, w, h)) {
    const inv = invert(mul(translation(op.dx, op.dy), op.transform));
    for (const [qx, qy] of [[0, 0], [w, 0], [0, h], [w, h]]) {
      const [x, y] = applyMat(inv, qx, qy);
      if (x < -1e-6 || x > w + 1e-6 || y < -1e-6 || y > h + 1e-6) return `${op.channel} dx=${op.dx}: (${qx},${qy}) → (${x.toFixed(2)},${y.toFixed(2)})`;
    }
  }
  return null;
}

describe("matrices", () => {
  it("compose, invert and transform", () => {
    const m = mul(translation(10, 20), mul(rotateAbout(0.3, 5, 5), scaleAbout(1.5, 2, 3)));
    const [x, y] = applyMat(m, 7, 9);
    const back = applyMat(invert(m), x, y);
    expect(back[0]).toBeCloseTo(7, 9);
    expect(back[1]).toBeCloseTo(9, 9);
    expect(isIdentity(mul(m, invert(m)))).toBe(true);
    expect(applyMat(placeAt(100, 50, Math.PI / 2, 2), 10, 0)).toEqual([expect.closeTo(100, 9), expect.closeTo(70, 9)]);
    expect(transformRect(scaleAbout(2, 0, 0), { x: 1, y: 1, w: 2, h: 3 })).toEqual({ x: 2, y: 2, w: 4, h: 6 });
  });

  it("transformOps prefixes the outer matrix and multiplies alpha", () => {
    const [op] = transformOps([{ op: "rect", x: 0, y: 0, w: 1, h: 1, radius: 0, fill: "#000", stroke: null, strokePx: 0, transform: translation(1, 0), alpha: 0.5, blend: "source-over" }], translation(0, 2), 0.5);
    expect(op).toMatchObject({ transform: [1, 0, 0, 1, 1, 2], alpha: 0.25 });
  });
});

describe("coverScale (AR-M5)", () => {
  it("is the documented bound or more, and 1 for the identity", () => {
    for (const [w, h] of ASPECTS) {
      expect(coverScale(state({}), w, h)).toBe(1);
      for (const rot of [0.2, 1, 2.5]) {
        const s = state({ rot, dx: 30, dy: -12, blurX: 20 });
        const c = Math.abs(Math.cos(rot)), n = Math.abs(Math.sin(rot));
        const bound = Math.max((w * c + h * n) / w, (w * n + h * c) / h) + 2 * Math.max(30 / w, 12 / h) + 20 / w;
        expect(coverScale(s, w, h)).toBeGreaterThanOrEqual(bound - 1e-12);
      }
    }
    expect(coverScale(state({ rot: Math.PI / 2 }), 1080, 1920)).toBeCloseTo(1920 / 1080, 9);
  });

  it("leaves no frame corner uncovered for rotation 0–360°, translation, blur, RGB split and slices (3 aspects)", () => {
    for (const [w, h] of ASPECTS) {
      for (let deg = 0; deg <= 360; deg += 7.5) {
        for (const [dx, dy] of [[0, 0], [40, 0], [-140, 0], [0, 40], [25, -25], [-140, 60]]) {
          for (const extra of [{}, { blurX: 36 }, { rgbSplit: 14 }, { slices: [{ y: 100, h: 50, dx: 24 }, { y: 600, h: 80, dx: -24 }] }]) {
            const st = state({ rot: (deg * Math.PI) / 180, dx, dy, ...extra });
            expect(uncovered(st, w, h), `${w}x${h} ${deg}° ${dx},${dy} ${JSON.stringify(extra)}`).toBeNull();
          }
        }
      }
    }
  });

  it("clamps a zoom anchored at a frame corner so no edge shows", () => {
    for (const [w, h] of ASPECTS) {
      for (const [ax, ay] of [[0, 0], [1, 1], [0, 1], [0.95, 0.05]]) {
        for (const scale of [1, 1.05, 1.2, 1.6]) {
          expect(uncovered(state({ scale, ax, ay, dx: 18, dy: -18 }), w, h)).toBeNull();
        }
      }
    }
  });

  it("keeps the anchor fixed when there is room", () => {
    const m = pictureMatrix(state({ scale: 1.5, ax: 0.5, ay: 0.38 }), 1080, 1920);
    const [x, y] = applyMat(m, 540, 0.38 * 1920);
    expect(x).toBeCloseTo(540, 6);
    expect(y).toBeCloseTo(0.38 * 1920, 6);
  });
});

describe("picture ops", () => {
  it("one plain draw for the identity", () => {
    const ops = pictureOps(state({}), 1080, 1920);
    expect(ops).toHaveLength(1);
    expect(isIdentity(ops[0].transform)).toBe(true);
    expect(ops[0]).toMatchObject({ filter: "", channel: "rgb", alpha: 1, blend: "source-over", cutout: false, clip: null });
  });

  it("whip blur: 7 equal-weight ghosts", () => {
    const ops = pictureOps(state({ blurX: 36 }), 1080, 1920);
    expect(ops.map((o) => o.dx)).toEqual([0, -18, 18, -12, 12, -6, 6]);
    // Drawing i at alpha 1/(i+1) over the previous result gives every ghost weight 1/7.
    let weights: number[] = [];
    ops.forEach((o) => { weights = weights.map((v) => v * (1 - o.alpha)); weights.push(o.alpha); });
    weights.forEach((v) => expect(v).toBeCloseTo(1 / 7, 5));
  });

  it("RGB split: r, g, b with lighter; slices clipped and shifted; filters joined", () => {
    const ops = pictureOps(state({ rgbSplit: 8, filter: ["saturate(0.2)", "brightness(0.8)"], slices: [{ y: 10, h: 20, dx: 30 }] }), 1080, 1920);
    expect(ops.map((o) => [o.channel, o.blend, o.dx, o.clip])).toEqual([
      ["r", "source-over", -8, null], ["g", "lighter", 0, null], ["b", "lighter", 8, null], ["rgb", "source-over", 30, [0, 10, 1080, 20]],
    ]);
    expect(ops[0].filter).toBe("saturate(0.2) brightness(0.8)");
    expect(cutoutOp(state({ scale: 1.2 }), 1080, 1920)).toMatchObject({ cutout: true, transform: ops.length ? pictureMatrix(state({ scale: 1.2 }), 1080, 1920) : [] });
  });
});

describe("safe placement", () => {
  it("safe rect follows CAPTION_SAFE and the hook band", () => {
    expect(safeRect(1080, 1920)).toEqual({ x: 64.8, y: 96, w: 950.4, h: 1920 - 96 - 192 });
    expect(safeRect(1080, 1920, true).y).toBe(384);
    expect(safeRect(1920, 1080).y).toBe(54);
  });

  it("places a box at the first candidate clear of every obstacle, else the least overlapping", () => {
    const area = safeRect(1080, 1920);
    const face = { x: 400, y: 400, w: 280, h: 400 };
    const r = placeAvoiding(200, 100, [[540, 600], [540, 1000], [200, 300]], [face], area);
    expect(r).toEqual({ x: 440, y: 950, w: 200, h: 100 });
    expect(clampBox(0, 0, 100, 100, area)).toEqual({ x: area.x, y: area.y, w: 100, h: 100 });
    const blocked = placeAvoiding(200, 100, [[540, 600]], [face], area);
    expect(blocked.y).toBe(550);
  });
});
