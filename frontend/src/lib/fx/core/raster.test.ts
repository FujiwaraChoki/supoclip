// @vitest-environment node
import { describe, expect, it } from "vitest";
import { createCanvas } from "@napi-rs/canvas";
import type { DrawOp, FxContext2D } from "./host";
import { applyColorMatrix, composeMatrix, filterMatrix, IDENTITY_MATRIX, rasterize, tracePath } from "./raster";
import { pictureOps } from "./layout";
import { IDENTITY_PICTURE } from "./registry";
import { stickerOps, STICKER_PALETTES } from "./looks/sticker";
import { expectGolden, renderOpsToPng, renderSceneToPng } from "./test-utils/golden";
import { napiHost } from "./test-utils/napi-host";
import { fakeHost, makeScene, recordingContext } from "./test-utils/fake-host";

const flat = (color: string) => (ctx: FxContext2D) => { ctx.fillStyle = color; ctx.fillRect(0, 0, 64, 64); };
function pixelAfter(ops: DrawOp[], supportsFilter: boolean, color: string, at = [32, 32]) {
  const host = napiHost({ width: 64, height: 64, supportsFilter, picture: flat(color), atlas: null });
  const canvas = createCanvas(64, 64);
  const ctx = canvas.getContext("2d") as unknown as FxContext2D;
  rasterize(ctx, ops, host);
  return Array.from(ctx.getImageData(at[0], at[1], 1, 1).data);
}
type PictureOp = Extract<DrawOp, { op: "picture" }>;
const picture = (filter: string): PictureOp => ({ ...pictureOps(IDENTITY_PICTURE as never, 64, 64)[0], filter });

describe("colour matrices", () => {
  it("saturate(0) on rgb(200,50,50) gives the Rec.709 luma 82", () => {
    const px = new Uint8ClampedArray([200, 50, 50, 255]);
    applyColorMatrix(px, filterMatrix("saturate(0)")!);
    expect(Array.from(px)).toEqual([82, 82, 82, 255]);
  });

  it("parses units and composes in order", () => {
    expect(filterMatrix("blur(4px)")).toBeNull();
    expect(filterMatrix("saturate(100%)")).toEqual(filterMatrix("saturate(1)"));
    const a = filterMatrix("brightness(0.5) contrast(2)")!;
    const b = composeMatrix(filterMatrix("contrast(2)")!, filterMatrix("brightness(0.5)")!);
    expect(a).toEqual(b);
    expect(composeMatrix(IDENTITY_MATRIX, a)).toEqual(a);
  });

  it.each(["saturate(0)", "brightness(1.2) contrast(1.1)", "contrast(1.12) saturate(1.25)", "sepia(0.18) saturate(1.1) brightness(1.03)",
    "grayscale(1) contrast(1.3) brightness(0.95)", "hue-rotate(40deg)", "saturate(0.2) brightness(0.8)"])(
    "the ImageData fallback matches ctx.filter within 3/255: %s", (filter) => {
      for (const color of ["#c83232", "#3a7bd5", "#e0c080", "#202020"]) {
        const native = pixelAfter([picture(filter)], true, color);
        const fallback = pixelAfter([picture(filter)], false, color);
        native.forEach((v, i) => expect(Math.abs(v - fallback[i])).toBeLessThanOrEqual(3));
      }
    });
});

describe("picture channel draws", () => {
  it("r + g + b with lighter re-assemble the picture", () => {
    const ops = pictureOps({ ...IDENTITY_PICTURE, rgbSplit: 0.0001, filter: [], slices: [] }, 64, 64)
      .map((op) => ({ ...op, dx: 0 }));
    expect(ops.map((o) => o.channel)).toEqual(["r", "g", "b"]);
    expect(pixelAfter(ops, true, "#c86432")).toEqual([200, 100, 50, 255]);
  });

  it("isolates one channel", () => {
    const [r] = pictureOps({ ...IDENTITY_PICTURE, rgbSplit: 1, filter: [], slices: [] }, 64, 64);
    expect(pixelAfter([{ ...r, dx: 0 }], true, "#c86432")).toEqual([200, 0, 0, 255]);
  });

  it("clips slices to their band and shifts them", () => {
    const host = fakeHost();
    const { ctx, calls } = recordingContext(64, 64);
    rasterize(ctx, [{ ...picture(""), clip: [0, 10, 64, 5], dx: 7 }], host);
    expect(calls.find((c) => c[0] === "rect")).toEqual(["rect", 0, 10, 64, 5]);
    expect(host.pictures[0].transform[4]).toBe(7);
    expect(host.pictures[0].dx).toBe(0);
  });
});

describe("text", () => {
  it("strokes before it fills, centres on y and casts the shadow once", () => {
    const { ctx, calls } = recordingContext();
    rasterize(ctx, [{
      op: "text", text: "Hi", font: "FX-TikTokSans-Bold", px: 50, x: 10, y: 20, align: "center", fill: "#fff", stroke: "#000",
      strokePx: 4, join: "round", shadow: { color: "#000", blur: 3, dx: 0, dy: 2 }, letterSpacing: 0,
      transform: [1, 0, 0, 1, 0, 0], alpha: 1, blend: "source-over",
    }], fakeHost());
    const names = calls.map((c) => c[0]);
    expect(names.indexOf("strokeText")).toBeLessThan(names.indexOf("fillText"));
    expect(calls).toContainEqual(["=textBaseline", "middle"]);
    expect(calls).toContainEqual(["=lineWidth", 8]);
    expect(calls).toContainEqual(["=font", '50px "FX-TikTokSans-Bold", "Noto Sans", "Noto Sans CJK SC", "Noto Sans Arabic", "Noto Sans Devanagari", "Noto Sans Thai", sans-serif']);
    const blurs = calls.filter((c) => c[0] === "=shadowBlur").map((c) => c[1]);
    expect(blurs).toEqual([3, 0]);
  });
});

describe("paths, pixelate, grain", () => {
  it("smooth closed paths curve through midpoints", () => {
    const { ctx, calls } = recordingContext();
    tracePath(ctx, [0, 0, 10, 0, 10, 10, 0, 10], true, true);
    expect(calls.filter((c) => c[0] === "quadraticCurveTo")).toHaveLength(4);
    expect(calls[1]).toEqual(["moveTo", 0, 5]);
  });

  it("pixelates into flat blocks", () => {
    const host = napiHost({ width: 64, height: 64, atlas: null, picture: (ctx) => {
      const g = ctx.createLinearGradient(0, 0, 64, 0);
      g.addColorStop(0, "#000"); g.addColorStop(1, "#fff");
      ctx.fillStyle = g; ctx.fillRect(0, 0, 64, 64);
    } });
    const canvas = createCanvas(64, 64);
    const ctx = canvas.getContext("2d") as unknown as FxContext2D;
    rasterize(ctx, [picture(""), { op: "pixelate", x: 0, y: 0, w: 32, h: 32, block: 16 }], host);
    const a = Array.from(ctx.getImageData(1, 1, 1, 1).data), b = Array.from(ctx.getImageData(14, 14, 1, 1).data);
    expect(a).toEqual(b);
    expect(Array.from(ctx.getImageData(40, 1, 1, 1).data)).not.toEqual(a);
  });

  it("grain is deterministic per frame and changes between frames", () => {
    const render = (frame: number) => {
      const host = napiHost({ width: 64, height: 64, atlas: null, picture: flat("#808080") });
      const canvas = createCanvas(64, 64);
      const ctx = canvas.getContext("2d") as unknown as FxContext2D;
      rasterize(ctx, [picture(""), { op: "grain", amount: 0.5, seed: 4242, frame }], host);
      return Buffer.from(ctx.getImageData(0, 0, 64, 64).data).toString("base64");
    };
    expect(render(3)).toBe(render(3));
    expect(render(3)).not.toBe(render(4));
  });
});

describe("raster goldens", () => {
  const times = [0.3, 1.05, 2.3];
  for (const [w, h] of [[1080, 1920], [1080, 1080], [1920, 1080]] as const) {
    it(`base captions ${w}x${h}`, () => {
      for (const t of times) {
        const png = renderSceneToPng(makeScene({ width: w, height: h }), t);
        expectGolden(`base__marker_circle_${w}x${h}_t${t}`, png);
      }
    });
  }

  it("base captions per template look", () => {
    for (const template of ["slam", "behind_head", "highlighter_swipe", "emoji_drop"] as const) {
      const scene = makeScene({ template });
      const style = template === "highlighter_swipe"
        ? { ...scene.caption!, color: "#111111", stroke_color: null, stroke_px: 0, shadow: false, background: "#FFFFFFE6" }
        : template === "slam" ? { ...scene.caption!, font: "THEBOLDFONT", uppercase: true }
          : template === "behind_head" ? { ...scene.caption!, stroke_color: null, stroke_px: 0 } : { ...scene.caption!, font: "Poppins-ExtraBold" };
      expectGolden(`base__${template}_t1.05`, renderSceneToPng({ ...scene, caption: { ...style, template } }, 1.05));
    }
  }, 30_000); // four full-size frames; a shared CI runner needs more than the default 5 s

  it("picture ops: punch toward the face, whip blur, spin, RGB split + slices", () => {
    const states = {
      punch: { scale: 1.35, ax: 0.5, ay: 0.29 },
      whip: { dx: 90, blurX: 36 },
      spin: { rot: 0.6 },
      glitch: { rgbSplit: 12, slices: [{ y: 520, h: 90, dx: 60 }, { y: 1200, h: 40, dx: -45 }] },
      grade: { filter: ["contrast(1.3)", "saturate(0)"] },
    };
    for (const [name, st] of Object.entries(states)) {
      const ops = pictureOps({ ...IDENTITY_PICTURE, filter: [], slices: [], ...st }, 1080, 1920);
      expectGolden(`base__picture_${name}`, renderOpsToPng(ops, 1080, 1920, { background: false }));
    }
  }, 30_000); // five full-size frames; a shared CI runner needs more than the default 5 s

  it("sticker looks", () => {
    const ops: DrawOp[] = [
      ...stickerOps("WAIT FOR IT…", 540, 560, -0.08, 1.1, STICKER_PALETTES[1], 1),
      ...stickerOps("NO WAY", 300, 900, 0.12, 1, STICKER_PALETTES[0], 1),
      ...stickerOps("$1,000,000", 760, 1160, -0.05, 0.9, STICKER_PALETTES[2], 1),
      ...stickerOps("plot\ntwist", 520, 1480, 0.1, 1.2, STICKER_PALETTES[3], 1),
    ];
    expectGolden("looks__sticker_palettes", renderOpsToPng(ops, 1080, 1920));
    expectGolden("looks__sticker_landscape", renderOpsToPng(stickerOps("WAIT FOR IT…", 960, 300, 0.06, 1, "#FF7A1A", 1), 1920, 1080));
  });
});
