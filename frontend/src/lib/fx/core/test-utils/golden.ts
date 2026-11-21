// Raster goldens (§16): render a scene (or raw ops) with the napi host and compare to shared/fx/golden/<name>.png
// with pixelmatch (≤ 0.5 % differing pixels at threshold 0.1). UPDATE_GOLDENS=1 writes the file instead.
// Frames render at full size (layout, strokes and blur are exact) and are stored downscaled so the short side is
// GOLDEN_SHORT_SIDE px (1080×1920 → 360×640, 1920×1080 → 640×360), colour quantised to GOLDEN_STEP levels (≤ 4 code
// values off, far inside pixelmatch's 0.1 threshold) and written as RGB with zlib level 9: the whole set stays under
// 5 MB (D10) while JSON op snapshots keep full-resolution coordinates.
// The deno host renders the same scenes against the same PNGs (fx_runtime tests), proving browser == server.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createCanvas, ImageData as NapiImageData } from "@napi-rs/canvas";
import pixelmatch from "pixelmatch";
import { PNG } from "pngjs";
import type { DrawOp, FxContext2D } from "../host";
import type { FxScene } from "../types";
import { prepareScene } from "../scene";
import { clockAt } from "../frame";
import { renderFrame } from "../render";
import { rasterize } from "../raster";
import { napiHost, REPO_ROOT, type NapiHostOptions } from "./napi-host";

export const GOLDEN_DIR = join(REPO_ROOT, "shared", "fx", "golden");

/** The frame at final time `o` (seconds) of `scene`, as PNG bytes. */
export function renderSceneToPng(scene: FxScene, o: number, options: NapiHostOptions = {}): Buffer {
  const host = napiHost({ width: scene.width, height: scene.height, ...options });
  const canvas = createCanvas(scene.width, scene.height);
  const ctx = canvas.getContext("2d") as unknown as FxContext2D;
  const prepared = prepareScene(scene, host);
  renderFrame(ctx, prepared, clockAt(prepared, o), host);
  return canvas.toBuffer("image/png");
}

/** Raw ops on a w×h canvas (over the test picture unless `background` is false), as PNG bytes. */
export function renderOpsToPng(ops: DrawOp[], w: number, h: number, options: NapiHostOptions & { background?: boolean } = {}): Buffer {
  const host = napiHost({ width: w, height: h, ...options });
  const canvas = createCanvas(w, h);
  const ctx = canvas.getContext("2d") as unknown as FxContext2D;
  if (options.background !== false) host.drawPicture(ctx, { op: "picture", transform: [1, 0, 0, 1, 0, 0], filter: "", alpha: 1, blend: "source-over", channel: "rgb", dx: 0, dy: 0, cutout: false, clip: null });
  rasterize(ctx, ops, host);
  return canvas.toBuffer("image/png");
}

/** Short side of a stored golden (px). */
export const GOLDEN_SHORT_SIDE = 360;

/** A rendered PNG as stored in shared/fx/golden: downscaled (high-quality smoothing) to GOLDEN_SHORT_SIDE, zlib 9. */
export function goldenImage(png: Buffer): PNG {
  const src = PNG.sync.read(png);
  const k = Math.min(1, GOLDEN_SHORT_SIDE / Math.min(src.width, src.height));
  const w = Math.max(1, Math.round(src.width * k)), h = Math.max(1, Math.round(src.height * k));
  if (w === src.width && h === src.height) return quantize(src);
  const full = createCanvas(src.width, src.height);
  full.getContext("2d").putImageData(new NapiImageData(new Uint8ClampedArray(src.data.buffer, src.data.byteOffset, src.data.length), src.width, src.height), 0, 0);
  const small = createCanvas(w, h);
  const ctx = small.getContext("2d");
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(full, 0, 0, w, h);
  const out = new PNG({ width: w, height: h });
  out.data = Buffer.from(ctx.getImageData(0, 0, w, h).data);
  return quantize(out);
}

/** Colour step of stored goldens (RGB rounded to multiples of 8: smooth gradients compress to runs). */
export const GOLDEN_STEP = 8;
function quantize(png: PNG): PNG {
  const d = png.data;
  for (let i = 0; i < d.length; i++) if ((i & 3) !== 3) d[i] = Math.min(255, Math.round(d[i] / GOLDEN_STEP) * GOLDEN_STEP);
  return png;
}

/** PNG bytes for a golden: RGB when opaque (alpha adds nothing), RGBA otherwise; zlib level 9. */
export function encodeGolden(png: PNG): Buffer {
  let opaque = true;
  for (let i = 3; i < png.data.length && opaque; i += 4) opaque = png.data[i] === 255;
  return PNG.sync.write(png, { deflateLevel: 9, colorType: opaque ? 2 : 6 });
}

export interface GoldenResult { name: string; diffRatio: number; written: boolean }

/** Compares `png` with shared/fx/golden/<name>.png; throws (and writes <tmp>/fx-golden/<name>.actual.png) on drift. */
export function expectGolden(name: string, png: Buffer, options: { maxDiffRatio?: number; threshold?: number } = {}): GoldenResult {
  const file = join(GOLDEN_DIR, `${name}.png`);
  const actual = goldenImage(png);
  const encoded = encodeGolden(actual);
  if (process.env.UPDATE_GOLDENS === "1") {
    mkdirSync(GOLDEN_DIR, { recursive: true });
    writeFileSync(file, encoded);
    return { name, diffRatio: 0, written: true };
  }
  if (!existsSync(file)) throw new Error(`missing golden ${file}; run with UPDATE_GOLDENS=1 to create it`);
  const expected = PNG.sync.read(readFileSync(file));
  if (expected.width !== actual.width || expected.height !== actual.height) {
    throw new Error(`golden ${name}: size ${actual.width}x${actual.height} != ${expected.width}x${expected.height}`);
  }
  const diff = new PNG({ width: actual.width, height: actual.height });
  const count = pixelmatch(expected.data, actual.data, diff.data, actual.width, actual.height, { threshold: options.threshold ?? 0.1 });
  const ratio = count / (actual.width * actual.height);
  if (ratio > (options.maxDiffRatio ?? 0.005)) {
    const dir = join(tmpdir(), "fx-golden");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, `${name}.actual.png`), encoded);
    writeFileSync(join(dir, `${name}.diff.png`), PNG.sync.write(diff));
    throw new Error(`golden ${name}: ${(ratio * 100).toFixed(3)} % pixels differ (see ${dir})`);
  }
  return { name, diffRatio: ratio, written: false };
}
