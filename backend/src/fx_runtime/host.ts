// The deno FxHost (FINAL §4.3, §4.4): @napi-rs/canvas (Skia, like Chrome), job fonts registered as "FX-<stem>",
// the emoji atlas, and the base picture drawn from the current input frame.
//
// AR-B1: the input frame's alpha is the person matte, never the picture's alpha. Skia canvases are premultiplied, so
// the host copies the alpha into its own matte buffer and forces the frame opaque before any putImageData. The
// `cutout` picture op is rebuilt from that matte (only inside behind windows, where fx-core asks for it).

import { createCanvas, GlobalFonts, ImageData, loadImage } from "@napi-rs/canvas";
import type { Canvas, Image, SKRSContext2D } from "@napi-rs/canvas";
import { extractAlpha, forceOpaque } from "./protocol.ts";

export type Mat2D = [number, number, number, number, number, number];
export type Blend =
  | "source-over"
  | "multiply"
  | "screen"
  | "lighter"
  | "overlay"
  | "soft-light"
  | "destination-in"
  | "difference"
  | "color";

/** §4.3 `DrawOp` with `op: "picture"`. */
export interface PictureOp {
  op: "picture";
  transform: Mat2D;
  filter: string;
  alpha: number;
  blend: Blend;
  channel: "rgb" | "r" | "g" | "b";
  dx: number;
  dy: number;
  cutout: boolean;
  clip: [number, number, number, number] | null;
}

export interface EmojiAtlas {
  image: Image | Canvas;
  cell: number;
  glyphs: Record<string, { x: number; y: number }>;
}

/** The context type fx-core draws into on the server. */
export type Ctx = SKRSContext2D;

const CHANNEL_COLOR = { r: "#ff0000", g: "#00ff00", b: "#0000ff" } as const;

export function fontFamilyFor(stem: string): string {
  return `FX-${stem}`;
}

/**
 * Mirror of fx-core's `FONT_FALLBACK` (frontend/src/lib/fx/core/layout.ts). rasterize() draws text with
 * `fontCss(family, px)`, so the host's `measure` must use the identical string or caption layout and drawing disagree
 * for any glyph the job font lacks. main_test.ts checks this copy against the TypeScript source.
 */
export const FONT_FALLBACK =
  '"Noto Sans", "Noto Sans CJK SC", "Noto Sans Arabic", "Noto Sans Devanagari", "Noto Sans Thai", sans-serif';

/** Mirror of fx-core's `fontCss(family, px)`: the canvas font string rasterize() draws text with. */
export function fxFontCss(family: string, px: number): string {
  return `${Math.round(px * 1000) / 1000}px "${family}", ${FONT_FALLBACK}`;
}

/** A CSS font shorthand for a family name, quoting it when needed. */
export function cssFont(family: string, px: number): string {
  const quoted = /^[A-Za-z][A-Za-z0-9-]*$/.test(family) ? family : JSON.stringify(family);
  return `${px}px ${quoted}`;
}

export interface HostWarning {
  (message: string): void;
}

export class DenoFxHost {
  readonly width: number;
  readonly height: number;
  readonly supportsFilter = true;
  readonly hasMatte: boolean;

  #atlas: EmojiAtlas | null = null;
  #frame0: Canvas | null = null;
  #measureCtx: SKRSContext2D;

  // Current input frame and the canvases derived from it, rebuilt lazily when the frame changes.
  #frame: Uint8Array | null = null;
  #frameIndex = -1;
  #matte: Uint8Array | null;
  #matteReady = false;
  #opaque = false;
  #picture: Canvas;
  #pictureIndex = -1;
  #cutout: Canvas | null = null;
  #cutoutRgba: Uint8ClampedArray | null = null;
  #cutoutIndex = -1;
  #channels = new Map<string, { index: number; canvas: Canvas }>();

  constructor(width: number, height: number, opts: { hasMatte: boolean }) {
    this.width = width;
    this.height = height;
    this.hasMatte = opts.hasMatte;
    this.#matte = opts.hasMatte ? new Uint8Array(width * height) : null;
    this.#picture = createCanvas(width, height);
    this.#measureCtx = createCanvas(8, 8).getContext("2d");
  }

  // ------------------------------------------------------------------ FxHost (§4.3)

  createCanvas(w: number, h: number): Canvas {
    return createCanvas(Math.max(1, Math.round(w)), Math.max(1, Math.round(h)));
  }

  atlas(): EmojiAtlas | null {
    return this.#atlas;
  }

  fontFamily(stem: string): string {
    return fontFamilyFor(stem);
  }

  /** Text advance in px for prepareScene's caption layout, with the same font string rasterize() draws with. */
  measure(text: string, family: string, px: number): number {
    this.#measureCtx.font = fxFontCss(family, px);
    return this.#measureCtx.measureText(text).width;
  }

  frame0(): Canvas | null {
    return this.#frame0;
  }

  drawPicture(ctx: Ctx, op: PictureOp): void {
    let source = op.cutout ? this.#cutoutCanvas() : this.#pictureCanvas();
    if (op.channel !== "rgb") source = this.#channelCanvas(source, op.channel, op.cutout);
    ctx.save();
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    if (op.clip) {
      ctx.beginPath();
      ctx.rect(op.clip[0], op.clip[1], op.clip[2], op.clip[3]);
      ctx.clip();
    }
    ctx.translate(op.dx, op.dy);
    ctx.transform(...op.transform);
    ctx.globalAlpha = op.alpha;
    ctx.globalCompositeOperation = op.blend;
    ctx.filter = op.filter && op.filter.trim() ? op.filter : "none";
    ctx.drawImage(source, 0, 0);
    ctx.restore();
  }

  // ------------------------------------------------------------------ job resources

  /** Registers each job font as "FX-<stem>". Missing or unreadable fonts are warnings: text falls back. */
  registerFonts(fonts: Record<string, string>, warn: HostWarning): void {
    for (const [stem, path] of Object.entries(fonts)) {
      let ok = false;
      try {
        ok = GlobalFonts.registerFromPath(path, fontFamilyFor(stem));
      } catch (error) {
        warn(`font ${stem}: ${(error as Error).message}`);
        continue;
      }
      if (!ok) warn(`font ${stem} could not be registered from ${path}`);
    }
  }

  /** Loads the emoji atlas. An empty path or a broken atlas leaves `atlas()` null (drawers skip emoji). */
  async loadAtlas(paths: { png: string; json: string }, warn: HostWarning): Promise<void> {
    if (!paths.png || !paths.json) return;
    try {
      const meta = JSON.parse(await Deno.readTextFile(paths.json)) as {
        cell?: number;
        glyphs?: Record<string, { x: number; y: number }>;
      };
      if (typeof meta.cell !== "number" || meta.cell <= 0 || typeof meta.glyphs !== "object" || !meta.glyphs) {
        throw new Error("atlas.json has no cell or glyphs");
      }
      const image = await loadImage(await Deno.readFile(paths.png));
      this.#atlas = { image, cell: meta.cell, glyphs: meta.glyphs };
    } catch (error) {
      warn(`emoji atlas not loaded: ${(error as Error).message}`);
      this.#atlas = null;
    }
  }

  /** For tests and the selftest: an atlas built in memory. */
  setAtlas(atlas: EmojiAtlas | null): void {
    this.#atlas = atlas;
  }

  // ------------------------------------------------------------------ frames

  /**
   * Makes `rgba` (W×H×4, owned by the caller and reused across frames) the current input frame. The matte is copied
   * lazily, right before the frame is forced opaque, so skipped and pass-through frames cost nothing.
   */
  setInputFrame(rgba: Uint8Array, index: number): void {
    this.#frame = rgba;
    this.#frameIndex = index;
    this.#matteReady = false;
    this.#opaque = false;
  }

  /** The current input frame as it must be written for a pass-through output frame. */
  passthroughBytes(): Uint8Array {
    if (!this.#frame) throw new Error("no input frame");
    return this.#frame;
  }

  /** Caches the rendered output frame 0 for the `frame0` op (loop crossfade). */
  captureFrame0(from: Canvas | "input"): void {
    const canvas = createCanvas(this.width, this.height);
    const ctx = canvas.getContext("2d");
    ctx.drawImage(from === "input" ? this.#pictureCanvas() : from, 0, 0);
    this.#frame0 = canvas;
  }

  /** The person matte of the current frame (null when the scene has no behind cues). */
  matte(): Uint8Array | null {
    if (!this.#matte) return null;
    this.#prepareMatte();
    return this.#matte;
  }

  #prepareMatte(): void {
    if (!this.#matte || this.#matteReady || !this.#frame) return;
    if (this.#opaque) throw new Error("matte read after the frame was forced opaque");
    extractAlpha(this.#frame, this.#matte);
    this.#matteReady = true;
  }

  #makeOpaque(): void {
    if (!this.#frame || this.#opaque) return;
    this.#prepareMatte(); // AR-B1: save the matte before the alpha is overwritten
    forceOpaque(this.#frame);
    this.#opaque = true;
  }

  #pictureCanvas(): Canvas {
    if (!this.#frame) throw new Error("no input frame");
    if (this.#pictureIndex !== this.#frameIndex) {
      this.#makeOpaque();
      const data = new Uint8ClampedArray(this.#frame.buffer, this.#frame.byteOffset, this.#frame.byteLength);
      this.#picture.getContext("2d").putImageData(new ImageData(data, this.width, this.height), 0, 0);
      this.#pictureIndex = this.#frameIndex;
    }
    return this.#picture;
  }

  #cutoutCanvas(): Canvas {
    if (!this.#frame) throw new Error("no input frame");
    if (!this.#matte) {
      // No matte in this render (the scene has no behind cues): the "person" is the whole opaque picture.
      return this.#pictureCanvas();
    }
    if (this.#cutoutIndex !== this.#frameIndex) {
      this.#prepareMatte();
      const matte = this.#matte;
      // One reusable buffer: a behind window rebuilds the cutout on every new input frame.
      const rgba = (this.#cutoutRgba ??= new Uint8ClampedArray(this.#frame.length));
      rgba.set(this.#frame);
      for (let i = 0, j = 3; i < matte.length; i++, j += 4) rgba[j] = matte[i];
      this.#cutout ??= createCanvas(this.width, this.height);
      const ctx = this.#cutout.getContext("2d");
      ctx.clearRect(0, 0, this.width, this.height);
      ctx.putImageData(new ImageData(rgba, this.width, this.height), 0, 0);
      this.#cutoutIndex = this.#frameIndex;
    }
    return this.#cutout!;
  }

  /** One colour channel of `source` (RGB split): multiply by the channel colour, keep the source alpha. */
  #channelCanvas(source: Canvas, channel: "r" | "g" | "b", cutout: boolean): Canvas {
    const key = `${channel}:${cutout ? 1 : 0}`;
    let entry = this.#channels.get(key);
    if (!entry) {
      entry = { index: -1, canvas: createCanvas(this.width, this.height) };
      this.#channels.set(key, entry);
    }
    if (entry.index !== this.#frameIndex) {
      const ctx = entry.canvas.getContext("2d");
      ctx.save();
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.globalCompositeOperation = "copy";
      ctx.drawImage(source, 0, 0);
      ctx.globalCompositeOperation = "multiply";
      ctx.fillStyle = CHANNEL_COLOR[channel];
      ctx.fillRect(0, 0, this.width, this.height);
      ctx.globalCompositeOperation = "destination-in";
      ctx.drawImage(source, 0, 0);
      ctx.restore();
      entry.index = this.#frameIndex;
    }
    return entry.canvas;
  }
}
