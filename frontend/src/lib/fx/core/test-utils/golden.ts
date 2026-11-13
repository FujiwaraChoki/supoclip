// Raster goldens (§16): render a scene (or raw ops) with the napi host and compare to shared/fx/golden/<name>.png
// with pixelmatch (≤ 0.5 % differing pixels at threshold 0.1). UPDATE_GOLDENS=1 writes the file instead.
// The deno host renders the same scenes against the same PNGs (fx_runtime tests), proving browser == server.
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { createCanvas } from "@napi-rs/canvas";
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

export interface GoldenResult { name: string; diffRatio: number; written: boolean }

/** Compares `png` with shared/fx/golden/<name>.png; throws (and writes <tmp>/fx-golden/<name>.actual.png) on drift. */
export function expectGolden(name: string, png: Buffer, options: { maxDiffRatio?: number; threshold?: number } = {}): GoldenResult {
  const file = join(GOLDEN_DIR, `${name}.png`);
  if (process.env.UPDATE_GOLDENS === "1") {
    mkdirSync(GOLDEN_DIR, { recursive: true });
    writeFileSync(file, png);
    return { name, diffRatio: 0, written: true };
  }
  if (!existsSync(file)) throw new Error(`missing golden ${file}; run with UPDATE_GOLDENS=1 to create it`);
  const expected = PNG.sync.read(readFileSync(file));
  const actual = PNG.sync.read(png);
  if (expected.width !== actual.width || expected.height !== actual.height) {
    throw new Error(`golden ${name}: size ${actual.width}x${actual.height} != ${expected.width}x${expected.height}`);
  }
  const diff = new PNG({ width: actual.width, height: actual.height });
  const count = pixelmatch(expected.data, actual.data, diff.data, actual.width, actual.height, { threshold: options.threshold ?? 0.1 });
  const ratio = count / (actual.width * actual.height);
  if (ratio > (options.maxDiffRatio ?? 0.005)) {
    const dir = join(tmpdir(), "fx-golden");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, `${name}.actual.png`), png);
    writeFileSync(join(dir, `${name}.diff.png`), PNG.sync.write(diff));
    throw new Error(`golden ${name}: ${(ratio * 100).toFixed(3)} % pixels differ (see ${dir})`);
  }
  return { name, diffRatio: ratio, written: false };
}
