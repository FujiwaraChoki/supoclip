// @vitest-environment node
// WP-POLISH (D7, D10, D11 and the motion fixes): sticker peel-off, doodle → sticker crossfade, decode slices, the
// accent pop that never touches its neighbours, the graded behind-head cutout and the golden size budget.
import { readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import type { DrawOp } from "./host";
import { drawerErrors } from "./frame";
import { applyMat } from "./layout";
import { LAYERS } from "./registry";
import { GOLDEN_DIR, expectGolden, renderSceneToPng } from "./test-utils/golden";
import { TEST_FACE } from "./test-utils/napi-host";
import type { FaceTrack, FxCue } from "./types";
import { ACCENT_POP, activeWordIndex } from "./captions/base";
import { DECODE_JOLT_FRAMES, sliceBands } from "./captions/decode_glitch";
import { DOODLE_SWAP } from "./captions/face_doodles";
import { STICKER_PEEL, stickerEvictions, stickerPlacements } from "./captions/sticker_slap";
import { accent, cueOps, opsAt, paths, prep, templateScene, texts } from "./captions/wp8.testkit";
import { hash01 } from "./rng";
import { createCanvas } from "@napi-rs/canvas";
import pixelmatch from "pixelmatch";
import type { FxContext2D } from "./host";
import { pictureOps } from "./layout";
import { IDENTITY_PICTURE } from "./registry";
import { rasterize } from "./raster";
import { napiHost } from "./test-utils/napi-host";

type TextOp = Extract<DrawOp, { op: "text" }>;

describe("sticker peel-off (a 4th sticker arrives)", () => {
  // b4 starts at 1.45 while b1, b2, b3 are on screen: b1 is the oldest and peels off.
  const CUES = [
    accent("b1.accent", "sticker", 0.56, 1.9, { text: "No way" }),
    accent("b2.accent", "sticker", 1.0, 2.4, { text: "300% growth in one year" }),
    accent("b3.accent", "sticker", 1.3, 2.6, { text: "plot twist" }),
    accent("b4.accent", "sticker", 1.45, 2.8, { text: "wild" }),
  ];
  const scene = templateScene("sticker_slap", { cues: CUES });
  const noWay = (t: number) => texts(cueOps(scene, t)).filter((o) => o.text === "NO WAY");

  it("evicts the oldest at the 4th sticker's start, and only it", () => {
    const out = stickerEvictions(prep(scene));
    expect(out.get("b1.accent")).toBeCloseTo(1.45, 9);
    for (const id of ["b2.accent", "b3.accent", "b4.accent"]) expect(out.get(id)).toBe(Infinity);
  });

  it("lifts, curls and fades over STICKER_PEEL instead of vanishing", () => {
    const rest = noWay(1.44);
    expect(rest).toHaveLength(3);
    expect(rest.every((o) => o.alpha === 1)).toBe(true);
    const mid = noWay(1.45 + STICKER_PEEL / 2);
    expect(mid).toHaveLength(3);
    expect(mid[0].alpha).toBeGreaterThan(0);
    expect(mid[0].alpha).toBeLessThan(1);
    // The sticker has moved up (lift) and its transform is squashed (curl): the centre's y is above the rest pose.
    const yRest = applyMat(rest[0].transform, 0, 0)[1], yMid = applyMat(mid[0].transform, 0, 0)[1];
    expect(yMid).toBeLessThan(yRest);
    const det = (m: number[]) => m[0] * m[3] - m[1] * m[2];
    expect(Math.abs(det(mid[0].transform))).toBeLessThan(Math.abs(det(rest[0].transform)));
    expect(noWay(1.45 + STICKER_PEEL + 0.01)).toEqual([]);
    expect(noWay(1.85)).toEqual([]);   // once peeled it never comes back
    expect(drawerErrors(prep(scene))).toEqual([]);
  });

  it("keeps stickers off the head (face box plus the hair above it)", () => {
    const p = prep(scene);
    const head = { x: TEST_FACE.x * 1080, y: (TEST_FACE.y - 0.45 * TEST_FACE.h) * 1920, w: TEST_FACE.w * 1080, h: TEST_FACE.h * 1.45 * 1920 };
    for (const spot of stickerPlacements(p).values()) {
      if (!spot) continue;
      const overlapX = Math.min(spot.box.x + spot.box.w, head.x + head.w) - Math.max(spot.box.x, head.x);
      const overlapY = Math.min(spot.box.y + spot.box.h, head.y + head.h) - Math.max(spot.box.y, head.y);
      expect(overlapX <= 0 || overlapY <= 0).toBe(true);
    }
  });

  it("golden: mid-peel", () => {
    expectGolden("polish__sticker_peel", renderSceneToPng(templateScene("sticker_slap", { cues: CUES, width: 540, height: 960 }), 1.45 + STICKER_PEEL * 0.45));
  });
});

describe("doodle → sticker crossfade when the face drops", () => {
  // The face is tracked until 1.0 s, then lost (empty samples) until the end.
  const track: FaceTrack = {
    version: 1, space: "output", fps: 6,
    samples: Array.from({ length: 19 }, (_, i) => [i / 6, i / 6 <= 1.0 ? [[TEST_FACE.x, TEST_FACE.y, TEST_FACE.w, TEST_FACE.h, 0.95]] : []] as [number, number[][]]),
  };
  const cue = accent("b1.accent", "doodle", 0.5, 2.6, { doodle: "sparkles", text: "so true" });
  const scene = (w = 1080, h = 1920) => templateScene("face_doodles", { cues: [cue], face_track: track, width: w, height: h });
  const lost = (() => { const p = prep(scene()); let t = 1.0; while (p.faceAt(t)) t += 1 / 30; return t; })();

  it("only the doodle with a face, only the sticker well after the drop", () => {
    const before = cueOps(scene(), 0.9);
    expect(paths(before).length).toBeGreaterThan(0);
    expect(texts(before).filter((o) => o.text === "SO TRUE")).toEqual([]);
    const after = cueOps(scene(), lost + DOODLE_SWAP + 0.1);
    expect(paths(after)).toEqual([]);
    expect(texts(after).filter((o) => o.text === "SO TRUE")).toHaveLength(3);
  });

  it("during the swap the doodle fades out while the sticker slaps in", () => {
    const mid = cueOps(scene(), lost + DOODLE_SWAP / 2);
    const doodle = paths(mid);
    expect(doodle.length).toBeGreaterThan(0);
    for (const op of doodle) expect(op.alpha).toBeLessThan(1);
    const sticker = texts(mid).filter((o) => o.text === "SO TRUE");
    expect(sticker).toHaveLength(3);
    expect(drawerErrors(prep(scene()))).toEqual([]);
  });

  it("golden: mid-swap", () => {
    expectGolden("polish__doodle_swap", renderSceneToPng(scene(540, 960), lost + DOODLE_SWAP * 0.4));
  });
});

describe("decode slices (real horizontal displacement)", () => {
  const ONE = accent("b5.accent", "decode", 2.0, 2.6, { text: "one" });
  const scene = (w = 1080, h = 1920) => templateScene("decode_glitch", { cues: [ONE], width: w, height: h });

  it("bands tile the range, one band takes the full jolt and one kicks back", () => {
    const hash = (n: number) => hash01(91, n);
    for (let frame = 0; frame < DECODE_JOLT_FRAMES; frame++) {
      const bands = sliceBands(100, 300, frame, 40, hash);
      expect(bands.length).toBeGreaterThanOrEqual(3);
      expect(bands.length).toBeLessThanOrEqual(5);
      expect(bands[0].y).toBe(100);
      bands.forEach((b, i) => { if (i) expect(b.y).toBeCloseTo(bands[i - 1].y + bands[i - 1].h, 9); expect(b.h).toBeGreaterThan(0); });
      expect(bands.at(-1)!.y + bands.at(-1)!.h).toBeCloseTo(300, 9);
      expect(bands.some((b) => Math.abs(b.dx) === 40)).toBe(true);
      expect(bands.some((b) => b.dx < 0) && bands.some((b) => b.dx > 0)).toBe(true);
    }
  });

  it("on the jolt frames every caption text op is cut into clipped, shifted copies; after them none is", () => {
    const jolt = texts(opsAt(prep(scene()), 2.0 + 0.5 / 30)).filter((o) => o.font.startsWith("FX-"));
    const clipped = jolt.filter((o: TextOp) => o.clip);
    expect(clipped.length).toBeGreaterThan(0);
    expect(new Set(clipped.map((o) => Math.round(o.transform[4]))).size).toBeGreaterThan(1);
    const later = texts(opsAt(prep(scene()), 2.0 + 3 / 30));
    expect(later.every((o: TextOp) => !o.clip)).toBe(true);
  });

  it("golden: the tear on the accent's first frame", () => {
    expectGolden("polish__decode_slice", renderSceneToPng(scene(540, 960), 2.0 + 0.5 / 30));
  });
});

describe("accent pop (D11)", () => {
  const scene = templateScene("emoji_drop");
  it("the spoken word grows to 1.06 and its neighbours slide apart: no two word boxes touch", () => {
    const p = prep(scene);
    const chunk = p.chunks[0];
    const t = chunk.words[1].start + 0.2;   // middle word spoken, pop settled
    expect(activeWordIndex(chunk, t)).toBe(1);
    const ops = opsAt(p, t).filter((o): o is TextOp => o.op === "text" && o.fill !== null && chunk.words.some((w) => w.text === o.text));
    const boxes = ops.map((o) => {
      const w = chunk.words.find((x) => x.text === o.text)!;
      const [x0] = applyMat(o.transform, w.x, 0), [x1] = applyMat(o.transform, w.x + w.w, 0);
      return { line: w.line, x0, x1 };
    }).sort((a, b) => a.line - b.line || a.x0 - b.x0);
    const popped = ops.find((o) => o.text === chunk.words[1].text)!;
    expect(popped.transform[0]).toBeCloseTo(ACCENT_POP, 6);
    for (let i = 1; i < boxes.length; i++) {
      if (boxes[i].line !== boxes[i - 1].line) continue;
      expect(boxes[i].x0 - boxes[i - 1].x1).toBeGreaterThan(chunk.px * 0.15);
    }
  });
});

describe("behind-head keeps the grade (D7)", () => {
  const cue: FxCue = accent("b0.behind", "behind", 0.5, 2.0, { text: "no way" });
  const scene = (w = 1080, h = 1920) => templateScene("behind_head", {
    cues: [cue], grade: "warm", grain: 0.6, width: w, height: h, matte_windows: [{ start: 0, end: 3, person: true }],
  });

  it("the behind layer sits under the grade extras and grain", () => {
    expect(LAYERS.indexOf("behind")).toBeLessThan(LAYERS.indexOf("pixel"));
    const ops = opsAt(prep(scene()), 1.0);
    const cutout = ops.findIndex((o) => o.op === "picture" && o.cutout);
    const tint = ops.findIndex((o) => o.op === "rect" && o.blend === "soft-light");
    const grain = ops.findIndex((o) => o.op === "grain");
    expect(cutout).toBeGreaterThan(0);
    expect(tint).toBeGreaterThan(cutout);
    expect(grain).toBeGreaterThan(cutout);
  });

  it("golden: graded person over the text", () => {
    expectGolden("polish__behind_graded", renderSceneToPng(scene(540, 960), 1.0));
  });
});

describe("golden budget (D10)", () => {
  it("shared/fx/golden stays under 5 MB and every PNG is stored at ≤ 640 px", () => {
    let total = 0;
    for (const f of readdirSync(GOLDEN_DIR)) {
      if (!f.endsWith(".png")) continue;
      const file = join(GOLDEN_DIR, f);
      total += statSync(file).size;
      const head = readFileSync(file).subarray(16, 24);
      expect(Math.max(head.readUInt32BE(0), head.readUInt32BE(4)), f).toBeLessThanOrEqual(640);
    }
    expect(total).toBeLessThan(5 * 1024 * 1024);
  });
});

describe("raster fast paths draw the same pixels (D9)", () => {
  const W = 360, H = 640;
  /** Pixels of `ops` drawn by rasterize in one call (fast paths) vs one op per call (the plain per-op path). */
  function compare(ops: DrawOp[]): number {
    const render = (batched: boolean) => {
      const host = napiHost({ width: W, height: H });
      const canvas = createCanvas(W, H);
      const ctx = canvas.getContext("2d") as unknown as FxContext2D;
      if (batched) rasterize(ctx, ops, host);
      else for (const op of ops) rasterize(ctx, [op], host);
      return canvas.getContext("2d").getImageData(0, 0, W, H).data;
    };
    const a = render(true), b = render(false);
    return pixelmatch(a, b, undefined, W, H, { threshold: 0.1 }) / (W * H);
  }
  const state = (extra: Partial<typeof IDENTITY_PICTURE>) => ({ ...IDENTITY_PICTURE, filter: ["saturate(1.2)"], slices: [], ...extra });

  it("RGB split in one pixel pass == three channel draws", () => {
    const ops = pictureOps(state({ rgbSplit: 6, scale: 1.1 }), W, H);
    expect(ops.map((o) => (o as { channel: string }).channel)).toEqual(["r", "g", "b"]);
    expect(compare(ops)).toBeLessThan(0.005);
  });

  it("whip ghosts and glitch slices drawn from one picture layer == one draw per op", () => {
    expect(compare(pictureOps(state({ blurX: 30, scale: 1.2 }), W, H))).toBeLessThan(0.005);
    expect(compare(pictureOps(state({ slices: [{ y: 100, h: 40, dx: 24 }, { y: 300, h: 20, dx: -18 }] }), W, H))).toBeLessThan(0.005);
  });
});
