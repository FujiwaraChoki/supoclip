// renderFrame = rasterize(frameOps): one output frame of a prepared scene onto a canvas, on either host.
import type { FxContext2D, FxHost, Mat2D } from "./host";
import { frameOps, type FxClock } from "./frame";
import { rasterize } from "./raster";
import type { PreparedScene } from "./scene";

/** Draws the frame at `clock`. When the canvas is not scene-sized (a scaled preview), ops are scaled to fit it. */
export function renderFrame(ctx: FxContext2D, p: PreparedScene, clock: FxClock, host: FxHost): void {
  const canvas = (ctx as unknown as { canvas?: { width: number; height: number } }).canvas;
  const base: Mat2D | undefined = canvas && (canvas.width !== p.width || canvas.height !== p.height) && canvas.width > 0
    ? [canvas.width / p.width, 0, 0, canvas.height / p.height, 0, 0]
    : undefined;
  rasterize(ctx, frameOps(p, clock), host, base);
}
