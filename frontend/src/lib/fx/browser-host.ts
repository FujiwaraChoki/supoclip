// The browser FxHost: Chrome/Firefox/Safari canvas for the studio preview, browser export and create-screen tiles.
// fx-core stays host-agnostic; this file is the only place it meets the DOM. The caller supplies how the base picture
// is drawn (source video via frameRect + B-roll + reaction PiP, in OUTPUT px) and, for behind cues, the person cutout.
import type { DrawOp, EmojiAtlas, FxCanvas, FxContext2D, FxHost, FxImage } from "./core/host";
import { fontCss, fxFamily } from "./core/layout";

type PictureOp = Extract<DrawOp, { op: "picture" }>;

/**
 * ctx.filter works when the property exists on the context (Safari < 18 has none) and keeps an assigned value.
 * Without it, rasterize applies the grade's colour matrix through ImageData instead.
 */
export function detectFilterSupport(ctx: unknown): boolean {
  if (!ctx || typeof ctx !== "object" || !("filter" in ctx)) return false;
  const c = ctx as { filter: string };
  try {
    const previous = c.filter;
    c.filter = "saturate(0)";
    const ok = c.filter === "saturate(0)";
    c.filter = previous;
    return ok;
  } catch {
    return false;
  }
}

/** An offscreen canvas: OffscreenCanvas where available, else a detached <canvas>. */
export function createFxCanvas(width: number, height: number): FxCanvas {
  const w = Math.max(1, Math.round(width)), h = Math.max(1, Math.round(height));
  if (typeof OffscreenCanvas !== "undefined") return new OffscreenCanvas(w, h) as unknown as FxCanvas;
  const canvas = document.createElement("canvas");
  canvas.width = w;
  canvas.height = h;
  return canvas as unknown as FxCanvas;
}

export interface BrowserHostOptions {
  /** Draw the base picture in output px; the host has already applied the op's transform and filter. */
  drawBase(ctx: FxContext2D, op: PictureOp): void;
  /** Draw only the person (matte-keyed) in output px; omitted ⇒ cutout draws nothing (text stays in front). */
  drawCutout?(ctx: FxContext2D, op: PictureOp): void;
  atlas?(): EmojiAtlas | null;
  frame0?(): FxImage | null;
  /** Force the filter path (tests); detected from a probe canvas by default. */
  supportsFilter?: boolean;
}

export interface BrowserHost extends FxHost {
  /** Text advance in px, with the same font string rasterize uses (prepareScene's `measure`). */
  measure(text: string, family: string, px: number): number;
}

let probe: FxContext2D | null = null;
function probeContext(): FxContext2D | null {
  if (!probe) {
    try {
      probe = createFxCanvas(8, 8).getContext("2d");
    } catch {
      probe = null;
    }
  }
  return probe;
}

export function createBrowserHost(options: BrowserHostOptions): BrowserHost {
  const supportsFilter = options.supportsFilter ?? detectFilterSupport(probeContext());
  return {
    supportsFilter,
    createCanvas: createFxCanvas,
    atlas: () => options.atlas?.() ?? null,
    frame0: () => options.frame0?.() ?? null,
    fontFamily: fxFamily,
    measure(text, family, px) {
      const ctx = probeContext();
      if (!ctx) return Array.from(text).length * px * 0.55;
      ctx.font = fontCss(family, px);
      return ctx.measureText(text).width;
    },
    drawPicture(ctx, op) {
      ctx.save();
      const m = op.transform;
      ctx.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]);
      if (supportsFilter) ctx.filter = op.filter || "none";
      if (op.cutout) options.drawCutout?.(ctx, op);
      else options.drawBase(ctx, op);
      ctx.restore();
    },
  };
}

const loadedFonts = new Map<string, Promise<void>>();
/** Registers a bundled font as "FX-<stem>" (served by /api/fonts/<stem>, like the editor's fonts). */
export function loadFxFont(stem: string, url = `/api/fonts/${encodeURIComponent(stem)}`): Promise<void> {
  let pending = loadedFonts.get(stem);
  if (!pending) {
    pending = (async () => {
      const face = new FontFace(fxFamily(stem), `url(${url})`);
      await face.load();
      document.fonts.add(face);
    })().catch((error) => {
      loadedFonts.delete(stem);
      throw error;
    });
    loadedFonts.set(stem, pending);
  }
  return pending;
}

/** Loads the emoji atlas (atlas.png + atlas.json: {cell, cols, glyphs}) into an EmojiAtlas. */
export async function loadEmojiAtlas(pngUrl: string, jsonUrl: string): Promise<EmojiAtlas> {
  const [map, image] = await Promise.all([
    fetch(jsonUrl).then((r) => {
      if (!r.ok) throw new Error(`emoji atlas map: ${r.status}`);
      return r.json() as Promise<{ cell: number; glyphs: Record<string, { x: number; y: number }> }>;
    }),
    fetch(pngUrl).then((r) => {
      if (!r.ok) throw new Error(`emoji atlas image: ${r.status}`);
      return r.blob();
    }).then((blob) => createImageBitmap(blob)),
  ]);
  return { image, cell: map.cell, glyphs: map.glyphs };
}
