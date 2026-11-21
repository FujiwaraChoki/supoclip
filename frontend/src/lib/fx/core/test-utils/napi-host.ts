// The @napi-rs/canvas FxHost for raster goldens under vitest (node environment). Same Skia as Chrome and as the deno
// host (backend/src/fx_runtime/host.ts), so a golden made here also checks the server. Test-only.
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { createCanvas, GlobalFonts, loadImage } from "@napi-rs/canvas";
import type { DrawOp, EmojiAtlas, FxCanvas, FxContext2D, FxHost, FxImage } from "../host";
import { fontCss, fxFamily } from "../layout";

export const REPO_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "../../../../../..");
export const FONTS_DIR = join(REPO_ROOT, "backend", "fonts");
export const ATLAS_DIR = join(REPO_ROOT, "backend", "src", "fx_assets", "emoji");

let fontsRegistered = false;
/** Registers every bundled backend/fonts/*.ttf as "FX-<stem>" (what fx_runtime does with job.fonts). */
export function registerFxFonts(): void {
  if (fontsRegistered) return;
  for (const file of readdirSync(FONTS_DIR)) {
    if (!/\.(ttf|otf)$/i.test(file)) continue;
    GlobalFonts.registerFromPath(join(FONTS_DIR, file), fxFamily(file.replace(/\.(ttf|otf)$/i, "")));
  }
  fontsRegistered = true;
}

/** The committed emoji atlas (WP5), or null while it does not exist yet. */
// Decoded once at import: `image.src = buffer` leaves a napi Image undecoded (width 0) when drawn right away, so
// every sprite would draw nothing. Top-level await keeps `loadTestAtlas()` synchronous for the callers.
const ATLAS_PNG = join(ATLAS_DIR, "atlas.png"), ATLAS_JSON = join(ATLAS_DIR, "atlas.json");
/** The committed atlas map. Read at run time: importing the JSON would pull a backend file into the app's type
 *  check, and the frontend image is built without the backend directory. */
export const readAtlasMap = (): { cell: number; glyphs: EmojiAtlas["glyphs"] } => JSON.parse(readFileSync(ATLAS_JSON, "utf8"));
const TEST_ATLAS: EmojiAtlas | null = existsSync(ATLAS_PNG) && existsSync(ATLAS_JSON)
  ? await (async () => {
    const map = JSON.parse(readFileSync(ATLAS_JSON, "utf8")) as { cell: number; glyphs: Record<string, { x: number; y: number }> };
    return { image: await loadImage(ATLAS_PNG), cell: map.cell, glyphs: map.glyphs };
  })()
  : null;

export function loadTestAtlas(): EmojiAtlas | null {
  return TEST_ATLAS;
}

/**
 * A deterministic stand-in for the video frame: a warm studio backdrop with a soft key light, a striped wall (so zoom,
 * shake and blur are visible in goldens) and a person — shoulders and a head whose box matches `TEST_FACE`
 * (normalised). `cutout` draws only the person, as the matte would.
 */
export const TEST_FACE = { x: 0.36, y: 0.24, w: 0.28, h: 0.2 };
export function drawTestPicture(ctx: FxContext2D, w: number, h: number, cutout = false): void {
  if (!cutout) {
    const bg = ctx.createLinearGradient(0, 0, 0, h);
    bg.addColorStop(0, "#22304a");
    bg.addColorStop(0.55, "#3c4c6b");
    bg.addColorStop(1, "#8a5a4a");
    ctx.fillStyle = bg;
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "rgba(255,255,255,0.06)";
    const stripe = Math.min(w, h) / 18;
    for (let x = -h; x < w; x += stripe * 2) {
      ctx.beginPath();
      ctx.moveTo(x, 0); ctx.lineTo(x + stripe, 0); ctx.lineTo(x + stripe + h * 0.6, h); ctx.lineTo(x + h * 0.6, h);
      ctx.closePath();
      ctx.fill();
    }
    const glow = ctx.createRadialGradient(w * 0.7, h * 0.2, 0, w * 0.7, h * 0.2, Math.max(w, h) * 0.5);
    glow.addColorStop(0, "rgba(255,200,140,0.35)");
    glow.addColorStop(1, "rgba(255,200,140,0)");
    ctx.fillStyle = glow;
    ctx.fillRect(0, 0, w, h);
  }
  const fx = TEST_FACE.x * w, fy = TEST_FACE.y * h, fw = TEST_FACE.w * w, fh = TEST_FACE.h * h;
  ctx.fillStyle = "#1d1f26";
  ctx.beginPath();
  ctx.ellipse(fx + fw / 2, fy + fh * 2.25, fw * 1.25, fh * 1.05, 0, Math.PI, 0);
  ctx.lineTo(fx + fw / 2 + fw * 1.25, h);
  ctx.lineTo(fx + fw / 2 - fw * 1.25, h);
  ctx.closePath();
  ctx.fill();
  ctx.fillStyle = "#c98f6f";
  ctx.beginPath();
  ctx.ellipse(fx + fw / 2, fy + fh / 2, fw / 2, fh / 2, 0, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#2a1c16";
  ctx.beginPath();
  ctx.ellipse(fx + fw / 2, fy + fh * 0.18, fw * 0.52, fh * 0.26, 0, Math.PI, 0);
  ctx.fill();
  // Features where the fixture landmarks put them (picture/fixtures faceRow): eyes, brows, nose, mouth.
  const u = Math.min(w, h) / 1080;
  for (const ex of [0.3, 0.7]) {
    ctx.fillStyle = "#f4efe9";
    ctx.beginPath(); ctx.ellipse(fx + fw * ex, fy + fh * 0.375, fw * 0.085, fh * 0.04, 0, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = "#2b2420";
    ctx.beginPath(); ctx.arc(fx + fw * ex, fy + fh * 0.378, fh * 0.034, 0, Math.PI * 2); ctx.fill();
    ctx.strokeStyle = "#2a1c16"; ctx.lineWidth = 7 * u; ctx.lineCap = "round";
    ctx.beginPath(); ctx.moveTo(fx + fw * (ex - 0.1), fy + fh * 0.3); ctx.quadraticCurveTo(fx + fw * ex, fy + fh * 0.27, fx + fw * (ex + 0.1), fy + fh * 0.3); ctx.stroke();
  }
  ctx.strokeStyle = "rgba(120,60,40,0.55)"; ctx.lineWidth = 5 * u;
  ctx.beginPath(); ctx.moveTo(fx + fw * 0.5, fy + fh * 0.45); ctx.quadraticCurveTo(fx + fw * 0.45, fy + fh * 0.57, fx + fw * 0.52, fy + fh * 0.6); ctx.stroke();
  ctx.fillStyle = "#6e2a2a";
  ctx.beginPath(); ctx.ellipse(fx + fw * 0.5, fy + fh * 0.725, fw * 0.16, fh * 0.055, 0, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#f2ede4";
  ctx.fillRect(fx + fw * 0.4, fy + fh * 0.69, fw * 0.2, fh * 0.022);
}

/** drawTestPicture plus fine detail (a 6u checker over the lower face) so pixelation and blur show in goldens. */
export function drawTexturedTestPicture(ctx: FxContext2D, w: number, h: number, cutout = false): void {
  drawTestPicture(ctx, w, h, cutout);
  const fx = TEST_FACE.x * w, fy = TEST_FACE.y * h, fw = TEST_FACE.w * w, fh = TEST_FACE.h * h;
  const cell = Math.max(2, Math.round((6 * Math.min(w, h)) / 1080));
  ctx.save();
  ctx.beginPath();
  ctx.ellipse(fx + fw / 2, fy + fh / 2, fw / 2, fh / 2, 0, 0, Math.PI * 2);
  ctx.clip();
  for (let y = Math.floor(fy + fh * 0.55); y < fy + fh; y += cell) {
    for (let x = Math.floor(fx), k = 0; x < fx + fw; x += cell, k++) {
      ctx.fillStyle = (k + Math.floor(y / cell)) % 2 ? "rgba(255,255,255,0.45)" : "rgba(40,20,90,0.45)";
      ctx.fillRect(x, y, cell, cell);
    }
  }
  ctx.restore();
}

export interface NapiHostOptions {
  /** Base picture in output px (defaults to drawTestPicture at the scene size). */
  picture?: (ctx: FxContext2D, cutout: boolean) => void;
  width?: number;
  height?: number;
  atlas?: EmojiAtlas | null;
  supportsFilter?: boolean;
  frame0?: FxImage | null;
}

export interface NapiHost extends FxHost { measure(text: string, family: string, px: number): number }

export function napiHost(options: NapiHostOptions = {}): NapiHost {
  registerFxFonts();
  const measureCtx = createCanvas(8, 8).getContext("2d");
  const w = options.width ?? 1080, h = options.height ?? 1920;
  const picture = options.picture ?? ((ctx: FxContext2D, cutout: boolean) => drawTestPicture(ctx, w, h, cutout));
  const atlas = options.atlas === undefined ? loadTestAtlas() : options.atlas;
  return {
    supportsFilter: options.supportsFilter ?? true,
    createCanvas: (cw, ch) => createCanvas(Math.max(1, Math.round(cw)), Math.max(1, Math.round(ch))) as unknown as FxCanvas,
    atlas: () => atlas,
    frame0: () => options.frame0 ?? null,
    fontFamily: fxFamily,
    measure(text, family, px) {
      measureCtx.font = fontCss(family, px);
      return measureCtx.measureText(text).width;
    },
    drawPicture(ctx, op: Extract<DrawOp, { op: "picture" }>) {
      ctx.save();
      const m = op.transform;
      ctx.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]);
      if (op.filter) ctx.filter = op.filter;
      picture(ctx, op.cutout);
      ctx.restore();
    },
  };
}
