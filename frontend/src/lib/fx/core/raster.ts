// rasterize(): the only fx-core code that touches a canvas. Draws a DrawOp[] (output px) onto a 2D context with the
// same calls in Chrome and in @napi-rs/canvas (both Skia), so preview, browser export and the server agree.
//
// Conventions every drawer can rely on:
// - text is vertically centred on y: the middle of the font's bounding box (ascent/descent), drawn on the
//   alphabetic baseline. Never textBaseline "middle": Chrome puts it at the middle of the em square and Skia/napi at
//   the middle of the font box, 0.1 em apart for Poppins (e2e/fx-parity.spec.ts). `strokePx` is the outline width outside the glyph (the
//   canvas line is 2·strokePx); the stroke is drawn before the fill; the shadow is cast by the first draw only.
// - `picture` ops: dx/dy are output-px offsets applied after `transform`; `clip` is an output-px rect; channel draws
//   isolate one colour channel (summed back with `lighter`); without ctx.filter support the filter's colour part is
//   applied as a 4×5 matrix through ImageData (W3C filter-effects definitions; blur is dropped). A picture with
//   alpha < 1, another blend, a channel or the matrix fallback is drawn into a scratch layer first, then composited.
// - `grain` draws a 256² mono noise tile from mulberry32(seed), offset per frame by hash01(seed, frame), at
//   alpha = amount with `overlay`; `scanlines` darkens a 1/2-spacing band every `spacing` px.
// - a text op's optional `clip` is an output-px rect (not moved by the op's transform): glitch slices.
// - `base` (optional) maps output px to canvas px when the canvas is not output-sized (e.g. a scaled preview).
import type { DrawOp, FxCanvas, FxContext2D, FxHost, Mat2D } from "./host";
import { IDENTITY, fontCss, mul, round3, translation } from "./layout";
import { hash01, mulberry32 } from "./rng";

type PictureOp = Extract<DrawOp, { op: "picture" }>;
type TextOp = Extract<DrawOp, { op: "text" }>;

const setMat = (ctx: FxContext2D, m: Mat2D) => ctx.setTransform(m[0], m[1], m[2], m[3], m[4], m[5]);
const canvasSize = (ctx: FxContext2D): { width: number; height: number } | null => {
  const c = (ctx as unknown as { canvas?: { width: number; height: number } }).canvas;
  return c && c.width > 0 && c.height > 0 ? { width: c.width, height: c.height } : null;
};

export function rasterize(ctx: FxContext2D, ops: DrawOp[], host: FxHost, base: Mat2D = IDENTITY): void {
  for (let i = 0; i < ops.length; i++) {
    const op = ops[i];
    ctx.save();
    try {
      // An RGB split (three channel draws of one picture) is done in one pass over the pixels (D9).
      if (op.op === "picture" && op.channel === "r" && splitTrio(ops, i, host)) {
        drawSplit(ctx, ops[i] as PictureOp, ops[i + 1] as PictureOp, ops[i + 2] as PictureOp, host, base);
        i += 2;
        continue;
      }
      // Several draws of the same picture (whip ghosts, glitch slices): draw it once, composite the copies (D9).
      if (op.op === "picture") {
        const run = pictureRun(ops, i, host);
        if (run > 1) {
          drawRun(ctx, ops.slice(i, i + run) as PictureOp[], host, base);
          i += run - 1;
          continue;
        }
      }
      draw(ctx, op, host, base);
    } finally {
      ctx.restore();
    }
  }
}

function prepare(ctx: FxContext2D, alpha: number, blend: string) {
  ctx.globalAlpha = Math.max(0, Math.min(1, alpha));
  ctx.globalCompositeOperation = blend;
}

function draw(ctx: FxContext2D, op: DrawOp, host: FxHost, base: Mat2D) {
  switch (op.op) {
    case "picture": return drawPicture(ctx, op, host, base);
    case "text": return drawText(ctx, op, base);
    case "path": {
      if (op.points.length < 4 || op.alpha <= 0) return;
      prepare(ctx, op.alpha, op.blend);
      setMat(ctx, mul(base, op.transform));
      tracePath(ctx, op.points, op.closed, op.smooth);
      if (op.fill) {
        if (op.shadow) {
          ctx.shadowColor = op.shadow.color;
          ctx.shadowBlur = op.shadow.blur * Math.abs(base[0] || 1);
          ctx.shadowOffsetX = op.shadow.dx * Math.abs(base[0] || 1);
          ctx.shadowOffsetY = op.shadow.dy * Math.abs(base[3] || 1);
        }
        ctx.fillStyle = op.fill;
        ctx.fill();
        if (op.shadow) { ctx.shadowColor = "rgba(0,0,0,0)"; ctx.shadowBlur = 0; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0; }
      }
      if (op.stroke && op.strokePx > 0) {
        ctx.strokeStyle = op.stroke;
        ctx.lineWidth = op.strokePx;
        ctx.lineCap = op.cap;
        ctx.lineJoin = "round";
        if (op.dash) { ctx.setLineDash(op.dash); ctx.lineDashOffset = op.dashOffset; }
        ctx.stroke();
      }
      return;
    }
    case "rect": {
      if (op.alpha <= 0 || op.w <= 0 || op.h <= 0) return;
      prepare(ctx, op.alpha, op.blend);
      setMat(ctx, mul(base, op.transform));
      ctx.beginPath();
      roundRectPath(ctx, op.x, op.y, op.w, op.h, op.radius);
      if (op.fill) {
        if (op.shadow) {
          ctx.shadowColor = op.shadow.color;
          ctx.shadowBlur = op.shadow.blur * Math.abs(base[0] || 1);
          ctx.shadowOffsetX = op.shadow.dx * Math.abs(base[0] || 1);
          ctx.shadowOffsetY = op.shadow.dy * Math.abs(base[3] || 1);
        }
        ctx.fillStyle = op.fill;
        ctx.fill();
        if (op.shadow) { ctx.shadowColor = "rgba(0,0,0,0)"; ctx.shadowBlur = 0; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0; }
      }
      if (op.stroke && op.strokePx > 0) { ctx.strokeStyle = op.stroke; ctx.lineWidth = op.strokePx; ctx.lineJoin = "round"; ctx.stroke(); }
      return;
    }
    case "sprite": {
      const atlas = host.atlas();
      const glyph = atlas?.glyphs[op.key];
      if (!atlas || !glyph || op.alpha <= 0 || op.w <= 0 || op.h <= 0) return;
      prepare(ctx, op.alpha, op.blend);
      setMat(ctx, mul(base, op.transform));
      ctx.imageSmoothingEnabled = true;
      ctx.imageSmoothingQuality = "high";
      ctx.drawImage(atlas.image as CanvasImageSource, glyph.x, glyph.y, atlas.cell, atlas.cell, op.x, op.y, op.w, op.h);
      return;
    }
    case "gradient": {
      if (op.alpha <= 0 || op.stops.length === 0) return;
      prepare(ctx, op.alpha, op.blend);
      setMat(ctx, base);
      const g = op.kind === "linear" ? ctx.createLinearGradient(op.x0, op.y0, op.x1, op.y1)
        : ctx.createRadialGradient(op.x0, op.y0, Math.max(0, op.r0), op.x1, op.y1, Math.max(0, op.r1));
      for (const [at, color] of op.stops) g.addColorStop(Math.max(0, Math.min(1, at)), color);
      ctx.fillStyle = g;
      ctx.fillRect(op.rect[0], op.rect[1], op.rect[2], op.rect[3]);
      return;
    }
    case "grain": return drawGrain(ctx, op, host, base);
    case "scanlines": {
      if (op.alpha <= 0 || op.spacing < 1) return;
      const size = canvasSize(ctx);
      if (!size) return;
      prepare(ctx, op.alpha, "source-over");
      setMat(ctx, base);
      const sy = Math.abs(base[3]) || 1;
      const height = size.height / sy, width = size.width / (Math.abs(base[0]) || 1);
      const band = Math.max(1, op.spacing / 2);
      ctx.beginPath();
      for (let y = ((op.offset % op.spacing) + op.spacing) % op.spacing - op.spacing; y < height; y += op.spacing) ctx.rect(0, y, width, band);
      ctx.fillStyle = "#000000";
      ctx.fill();
      return;
    }
    case "pixelate": return drawPixelate(ctx, op, base);
    case "frame0": {
      const img = host.frame0();
      const size = canvasSize(ctx);
      if (!img || op.alpha <= 0) return;
      prepare(ctx, op.alpha, "source-over");
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(img as CanvasImageSource, 0, 0, size?.width ?? img.width, size?.height ?? img.height);
      return;
    }
  }
}

// ----------------------------------------------------------------------------------------------- text

function drawText(ctx: FxContext2D, op: TextOp, base: Mat2D) {
  if (op.alpha <= 0 || !op.text || op.px <= 0) return;
  const stroke = op.stroke && op.strokePx > 0 ? op.stroke : null;
  if (!stroke && !op.fill) return;
  if (op.clip) {
    if (op.clip[2] <= 0 || op.clip[3] <= 0) return;
    setMat(ctx, base);
    ctx.beginPath();
    ctx.rect(op.clip[0], op.clip[1], op.clip[2], op.clip[3]);
    ctx.clip();
  }
  prepare(ctx, op.alpha, op.blend);
  const m = mul(base, op.transform);
  setMat(ctx, m);
  ctx.textAlign = op.align;
  const y = snapBaseline(op.y + middleShift(ctx, op.font, op.px), m);
  ctx.lineJoin = op.join;
  const native = op.letterSpacing !== 0 && "letterSpacing" in ctx;
  if (native) ctx.letterSpacing = `${op.letterSpacing}px`;
  const manual = op.letterSpacing !== 0 && !native;
  let shadowPending = !!op.shadow;
  const setShadow = (on: boolean) => {
    if (on && op.shadow) {
      ctx.shadowColor = op.shadow.color;
      ctx.shadowBlur = op.shadow.blur * Math.abs(base[0] || 1);
      ctx.shadowOffsetX = op.shadow.dx * Math.abs(base[0] || 1);
      ctx.shadowOffsetY = op.shadow.dy * Math.abs(base[3] || 1);
    } else {
      ctx.shadowColor = "rgba(0,0,0,0)";
      ctx.shadowBlur = 0; ctx.shadowOffsetX = 0; ctx.shadowOffsetY = 0;
    }
  };
  const paint = (mode: "stroke" | "fill") => {
    setShadow(shadowPending);
    shadowPending = false;
    if (manual) drawSpaced(ctx, op, mode, y);
    else if (mode === "stroke") ctx.strokeText(op.text, op.x, y);
    else ctx.fillText(op.text, op.x, y);
  };
  if (stroke) {
    ctx.strokeStyle = stroke;
    ctx.lineWidth = op.strokePx * 2;
    paint("stroke");
  }
  if (op.fill) {
    ctx.fillStyle = op.fill;
    paint("fill");
  }
}

/**
 * Sets the alphabetic baseline and returns how far below the requested centre it sits: half of (font ascent −
 * descent), the same point @napi-rs/canvas uses for "middle". Measured at 1000 px and scaled, because Chrome rounds
 * font box metrics to whole pixels at the drawing size. Contexts without font box metrics (Firefox < 116) keep
 * textBaseline "middle" and get 0. Leaves ctx.font at `font` px.
 */
function middleShift(ctx: FxContext2D, family: string, px: number): number {
  ctx.textBaseline = "alphabetic";
  ctx.font = fontCss(family, METRIC_PX);
  const m = ctx.measureText("H");
  ctx.font = fontCss(family, px);
  const ascent = m.fontBoundingBoxAscent, descent = m.fontBoundingBoxDescent;
  if (typeof ascent === "number" && typeof descent === "number" && Number.isFinite(ascent + descent)) {
    return ((ascent - descent) / 2) * (px / METRIC_PX);
  }
  ctx.textBaseline = "middle";
  return 0;
}
const METRIC_PX = 1000;

/**
 * Chrome snaps an unrotated text baseline to whole device pixels and Skia under napi does not; snapping here makes
 * both draw the same pixels. Rotated or skewed text keeps its exact position.
 */
function snapBaseline(y: number, m: Mat2D): number {
  if (m[1] !== 0 || m[2] !== 0 || m[3] === 0) return y;
  return (Math.round(m[3] * y + m[5]) - m[5]) / m[3];
}

/** Letter spacing for contexts without ctx.letterSpacing: one glyph at a time, same advances. */
function drawSpaced(ctx: FxContext2D, op: TextOp, mode: "stroke" | "fill", y: number) {
  const chars = Array.from(op.text);
  const widths = chars.map((c) => ctx.measureText(c).width);
  const total = widths.reduce((s, w) => s + w, 0) + op.letterSpacing * (chars.length - 1);
  let x = op.align === "center" ? op.x - total / 2 : op.align === "right" ? op.x - total : op.x;
  const align = ctx.textAlign;
  ctx.textAlign = "left";
  chars.forEach((c, i) => {
    if (mode === "stroke") ctx.strokeText(c, x, y);
    else ctx.fillText(c, x, y);
    x += widths[i] + op.letterSpacing;
  });
  ctx.textAlign = align;
}

// ----------------------------------------------------------------------------------------------- paths

/** Polyline through points; smooth = quadratic curves through the midpoints (closed paths wrap around). */
export function tracePath(ctx: Pick<FxContext2D, "beginPath" | "moveTo" | "lineTo" | "quadraticCurveTo" | "closePath">,
  pts: number[], closed: boolean, smooth: boolean) {
  const n = Math.floor(pts.length / 2);
  ctx.beginPath();
  if (!smooth || n < 3) {
    ctx.moveTo(pts[0], pts[1]);
    for (let i = 1; i < n; i++) ctx.lineTo(pts[2 * i], pts[2 * i + 1]);
    if (closed) ctx.closePath();
    return;
  }
  const mid = (i: number, j: number): [number, number] => [(pts[2 * i] + pts[2 * j]) / 2, (pts[2 * i + 1] + pts[2 * j + 1]) / 2];
  if (closed) {
    const start = mid(n - 1, 0);
    ctx.moveTo(start[0], start[1]);
    for (let i = 0; i < n; i++) {
      const m = mid(i, (i + 1) % n);
      ctx.quadraticCurveTo(pts[2 * i], pts[2 * i + 1], m[0], m[1]);
    }
    ctx.closePath();
    return;
  }
  ctx.moveTo(pts[0], pts[1]);
  for (let i = 1; i < n - 1; i++) {
    const m = mid(i, i + 1);
    ctx.quadraticCurveTo(pts[2 * i], pts[2 * i + 1], m[0], m[1]);
  }
  ctx.lineTo(pts[2 * (n - 1)], pts[2 * (n - 1) + 1]);
}

function roundRectPath(ctx: FxContext2D, x: number, y: number, w: number, h: number, radius: number) {
  const r = Math.max(0, Math.min(radius, w / 2, h / 2));
  if (r <= 0) { ctx.rect(x, y, w, h); return; }
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + w - r, y);
  ctx.quadraticCurveTo(x + w, y, x + w, y + r);
  ctx.lineTo(x + w, y + h - r);
  ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  ctx.lineTo(x + r, y + h);
  ctx.quadraticCurveTo(x, y + h, x, y + h - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

// ----------------------------------------------------------------------------------------------- picture

const scratch = new WeakMap<FxHost, Map<string, FxCanvas>>();
function scratchCanvas(host: FxHost, name: string, w: number, h: number): FxCanvas {
  let pool = scratch.get(host);
  if (!pool) { pool = new Map(); scratch.set(host, pool); }
  let c = pool.get(name);
  if (!c || c.width !== w || c.height !== h) {
    c = host.createCanvas(w, h);
    pool.set(name, c);
  }
  return c;
}

const CHANNEL: Record<"r" | "g" | "b", string> = { r: "#FF0000", g: "#00FF00", b: "#0000FF" };

function clipRect(ctx: FxContext2D, clip: PictureOp["clip"], base: Mat2D) {
  if (!clip) return;
  setMat(ctx, base);
  ctx.beginPath();
  ctx.rect(clip[0], clip[1], clip[2], clip[3]);
  ctx.clip();
}

/** Largest blur(…px) radius in a CSS filter string (0 when none). */
export function filterBlur(filter: string): number {
  let max = 0;
  for (const m of filter.matchAll(/blur\(\s*([\d.]+)px\s*\)/g)) max = Math.max(max, Number(m[1]));
  return max;
}

/** A blur at least this wide (canvas px) is drawn at reduced resolution and scaled up (D9: a full-size blur filter
 *  is the most expensive draw there is; the result of a wide blur has no detail a quarter-size copy lacks). */
export const REDUCED_BLUR_MIN = 6;
/** Reduced-resolution factor for a blur of `px` canvas px: the blur stays ≥ 4 px wide at the small size, ≥ 1/4. */
export const blurReduction = (px: number): number => Math.min(1, Math.max(0.25, 4 / px));

function drawPicture(ctx: FxContext2D, op: PictureOp, host: FxHost, base: Mat2D) {
  if (op.alpha <= 0) return;
  const transform = mul(base, mul(translation(op.dx, op.dy), op.transform));
  const filterFallback = !!op.filter && !host.supportsFilter;
  const blurPx = host.supportsFilter ? filterBlur(op.filter) * (Math.abs(base[0]) || 1) : 0;
  if (blurPx >= REDUCED_BLUR_MIN && op.channel === "rgb") {
    const size = canvasSize(ctx);
    if (size) return drawReducedBlur(ctx, op, transform, blurPx, size, host, base);
  }
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  // Direct only when the picture is drawn opaque and normally: a host may draw the picture as several primitives
  // (video + B-roll + PiP), so a partial alpha or another blend must apply to the finished layer, not to each part.
  if (op.channel === "rgb" && !filterFallback && op.alpha >= 1 && op.blend === "source-over") {
    clipRect(ctx, op.clip, base);
    prepare(ctx, op.alpha, op.blend);
    host.drawPicture(ctx, { ...op, transform, dx: 0, dy: 0, clip: null });
    return;
  }
  // Scratch path: draw at canvas size with identity compositing, isolate the channel / apply the colour matrix,
  // then composite the result with the op's alpha, blend and clip.
  const size = canvasSize(ctx);
  if (!size) return;
  const layer = scratchCanvas(host, "picture", size.width, size.height);
  const sctx = layer.getContext("2d");
  sctx.save();
  sctx.setTransform(1, 0, 0, 1, 0, 0);
  sctx.globalAlpha = 1;
  sctx.globalCompositeOperation = "source-over";
  sctx.clearRect(0, 0, size.width, size.height);
  sctx.imageSmoothingEnabled = true;
  sctx.imageSmoothingQuality = "high";
  const flat = { ...op, transform, dx: 0, dy: 0, clip: null, alpha: 1, blend: "source-over" as const, channel: "rgb" as const };
  host.drawPicture(sctx, filterFallback ? { ...flat, filter: "" } : flat);
  sctx.restore();
  if (filterFallback) {
    const matrix = filterMatrix(op.filter);
    if (matrix) {
      const data = sctx.getImageData(0, 0, size.width, size.height);
      applyColorMatrix(data.data, matrix);
      sctx.putImageData(data, 0, 0);
    }
  }
  if (op.channel !== "rgb") {
    sctx.save();
    sctx.setTransform(1, 0, 0, 1, 0, 0);
    sctx.globalCompositeOperation = "multiply";
    sctx.fillStyle = CHANNEL[op.channel];
    sctx.fillRect(0, 0, size.width, size.height);
    if (op.cutout) {   // multiply paints where the layer was empty: keep only the person's pixels
      sctx.globalCompositeOperation = "destination-in";
      host.drawPicture(sctx, { ...flat, filter: "" });
    }
    sctx.restore();
  }
  clipRect(ctx, op.clip, base);
  prepare(ctx, op.alpha, op.blend);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.drawImage(layer as unknown as CanvasImageSource, 0, 0);
}

/**
 * A wide-blur picture (dream glow): the picture is first scaled down into a small layer with no filter, then drawn
 * into a second small layer with its colour filter and a proportionally smaller blur, and the result is scaled up.
 * Skia evaluates a filter on the source image before its transform, so filtering while downscaling would still pay
 * for a full-size filter; two passes keep every filter at the small size.
 */
function drawReducedBlur(ctx: FxContext2D, op: PictureOp, transform: Mat2D, blurPx: number,
  size: { width: number; height: number }, host: FxHost, base: Mat2D) {
  const k = blurReduction(blurPx);
  const w = Math.max(1, Math.ceil(size.width * k)), h = Math.max(1, Math.ceil(size.height * k));
  const small = scratchCanvas(host, "blur-src", w, h), blurred = scratchCanvas(host, "blur", w, h);
  const colour = op.filter.replace(/blur\(\s*[\d.]+px\s*\)/g, "").replace(/\s+/g, " ").trim();
  const blur = round3(blurPx * k);
  const reset = (c: FxContext2D) => {
    c.setTransform(1, 0, 0, 1, 0, 0);
    c.globalAlpha = 1;
    c.globalCompositeOperation = "source-over";
    c.filter = "none";
    c.clearRect(0, 0, w, h);
    c.imageSmoothingEnabled = true;
    c.imageSmoothingQuality = "low";   // bilinear, no mipmaps: the copy is blurred anyway
  };
  const sctx = small.getContext("2d");
  sctx.save();
  reset(sctx);
  host.drawPicture(sctx, { ...op, transform: mul([k, 0, 0, k, 0, 0], transform), filter: "", dx: 0, dy: 0, clip: null,
    alpha: 1, blend: "source-over" });
  sctx.restore();
  const bctx = blurred.getContext("2d");
  bctx.save();
  reset(bctx);
  bctx.filter = `${colour} blur(${blur}px)`.trim();
  bctx.drawImage(small as unknown as CanvasImageSource, 0, 0);
  bctx.restore();
  clipRect(ctx, op.clip, base);
  prepare(ctx, op.alpha, op.blend);
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "low";   // upscaling a blur: bilinear is exact enough
  ctx.drawImage(blurred as unknown as CanvasImageSource, 0, 0, w, h, 0, 0, w / k, h / k);
}

/** How many ops from i are draws of one picture (same transform and filter, full colour, no cutout, no vertical
 *  offset, normal blend) that differ only in horizontal offset, alpha and clip. */
function pictureRun(ops: DrawOp[], i: number, host: FxHost): number {
  const first = ops[i];
  if (first.op !== "picture") return 0;
  if (first.filter && (!host.supportsFilter || filterBlur(first.filter) >= REDUCED_BLUR_MIN)) return 0;
  const fits = (o: DrawOp | undefined): o is PictureOp => !!o && o.op === "picture" && o.channel === "rgb" && !o.cutout
    && o.dy === 0 && o.blend === "source-over" && o.filter === first.filter && o.alpha > 0
    && o.transform.every((v, k) => v === first.transform[k]);
  let n = 0;
  while (fits(ops[i + n])) n++;
  return n;
}

/** A run of one picture's draws: the picture is drawn once into a scratch layer, each op composites that layer at its
 *  own offset, alpha and clip (identical pixels to drawing the picture again for every op, up to resampling). */
function drawRun(ctx: FxContext2D, run: PictureOp[], host: FxHost, base: Mat2D) {
  const size = canvasSize(ctx);
  if (!size) { for (const op of run) { ctx.save(); try { drawPicture(ctx, op, host, base); } finally { ctx.restore(); } } return; }
  const layer = scratchCanvas(host, "run", size.width, size.height);
  const sctx = layer.getContext("2d");
  sctx.save();
  sctx.setTransform(1, 0, 0, 1, 0, 0);
  sctx.globalAlpha = 1;
  sctx.globalCompositeOperation = "source-over";
  sctx.clearRect(0, 0, size.width, size.height);
  sctx.imageSmoothingEnabled = true;
  sctx.imageSmoothingQuality = "high";
  host.drawPicture(sctx, { ...run[0], transform: mul(base, run[0].transform), dx: 0, dy: 0, alpha: 1, clip: null });
  sctx.restore();
  const scale = Math.abs(base[0]) || 1;
  for (const op of run) {
    ctx.save();
    try {
      clipRect(ctx, op.clip, base);
      prepare(ctx, op.alpha, "source-over");
      ctx.setTransform(1, 0, 0, 1, 0, 0);
      ctx.drawImage(layer as unknown as CanvasImageSource, op.dx * scale, 0);
    } finally {
      ctx.restore();
    }
  }
}

/** ops[i..i+2] are one picture split into R, G, B (layout.pictureOps): same transform, filter and clip-free, opaque,
 *  R drawn normally and G, B added with `lighter`, G unshifted, nothing but horizontal offsets differing. */
function splitTrio(ops: DrawOp[], i: number, host: FxHost): boolean {
  const [r, g, b] = [ops[i], ops[i + 1], ops[i + 2]];
  if (r?.op !== "picture" || g?.op !== "picture" || b?.op !== "picture") return false;
  if (g.channel !== "g" || b.channel !== "b") return false;
  if (!host.supportsFilter) return false;
  const same = (o: PictureOp) => o.filter === r.filter && o.alpha === 1 && !o.cutout && o.clip === null && o.dy === 0
    && o.transform.every((v, k) => v === r.transform[k]);
  return same(r) && same(g) && same(b) && r.blend === "source-over" && g.blend === "lighter" && b.blend === "lighter"
    && g.dx === 0 && filterBlur(r.filter) < REDUCED_BLUR_MIN;
}

/**
 * RGB split in one pass: the picture is drawn once (transform + filter) into a scratch layer, then red is read from
 * dxR px away and blue from dxB px away in place (red left → right, blue right → left, so every read is of an unmodified
 * pixel), and the result replaces the canvas. Same pixels as three channel draws summed with `lighter`, at a third
 * of the cost (offsets rounded to whole canvas px).
 */
function drawSplit(ctx: FxContext2D, r: PictureOp, _g: PictureOp, b: PictureOp, host: FxHost, base: Mat2D) {
  const size = canvasSize(ctx);
  if (!size) return;
  const { width: w, height: h } = size;
  const layer = scratchCanvas(host, "split", w, h);
  const sctx = layer.getContext("2d");
  sctx.save();
  sctx.setTransform(1, 0, 0, 1, 0, 0);
  sctx.globalAlpha = 1;
  sctx.globalCompositeOperation = "source-over";
  sctx.fillStyle = "#000000";
  sctx.fillRect(0, 0, w, h);
  sctx.imageSmoothingEnabled = true;
  sctx.imageSmoothingQuality = "high";
  host.drawPicture(sctx, { ...r, transform: mul(base, r.transform), dx: 0, dy: 0, channel: "rgb", clip: null });
  sctx.restore();
  const img = sctx.getImageData(0, 0, w, h);
  const d = img.data;
  const scale = Math.abs(base[0]) || 1;
  const sr = -Math.round(r.dx * scale), sb = -Math.round(b.dx * scale);   // out(x) reads in(x + s)
  shiftChannel(d, w, h, 0, sr);
  shiftChannel(d, w, h, 2, sb);
  ctx.putImageData(img, 0, 0);
}

/** In place: channel c of every row reads from s px to the right (s > 0) or left (s < 0), edges clamped. */
export function shiftChannel(d: Uint8ClampedArray | Uint8Array, w: number, h: number, c: number, s: number): void {
  if (s === 0) return;
  for (let y = 0; y < h; y++) {
    const row = y * w * 4 + c;
    if (s > 0) {
      for (let x = 0; x < w; x++) d[row + x * 4] = d[row + Math.min(w - 1, x + s) * 4];
    } else {
      for (let x = w - 1; x >= 0; x--) d[row + x * 4] = d[row + Math.max(0, x + s) * 4];
    }
  }
}

// ----------------------------------------------------------------------------------------------- colour matrices

/** 4×5 row-major colour matrix (RGBA rows; last column = offset in 0..1 units). */
export type ColorMatrix = number[];
export const IDENTITY_MATRIX: ColorMatrix = [1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1, 0];

/** a ∘ b: apply b first, then a. */
export function composeMatrix(a: ColorMatrix, b: ColorMatrix): ColorMatrix {
  const out = new Array<number>(20).fill(0);
  for (let r = 0; r < 4; r++) {
    for (let c = 0; c < 5; c++) {
      let v = c === 4 ? a[r * 5 + 4] : 0;
      for (let k = 0; k < 4; k++) v += a[r * 5 + k] * b[k * 5 + c];
      out[r * 5 + c] = v;
    }
  }
  return out;
}

/** W3C filter-effects matrices for one CSS filter function; null for functions with no colour matrix (blur). */
export function filterFunctionMatrix(name: string, amount: number): ColorMatrix | null {
  switch (name) {
    case "brightness": return [amount, 0, 0, 0, 0, 0, amount, 0, 0, 0, 0, 0, amount, 0, 0, 0, 0, 0, 1, 0];
    case "contrast": {
      const o = 0.5 - 0.5 * amount;
      return [amount, 0, 0, 0, o, 0, amount, 0, 0, o, 0, 0, amount, 0, o, 0, 0, 0, 1, 0];
    }
    case "saturate": {
      const s = amount;
      return [
        0.213 + 0.787 * s, 0.715 - 0.715 * s, 0.072 - 0.072 * s, 0, 0,
        0.213 - 0.213 * s, 0.715 + 0.285 * s, 0.072 - 0.072 * s, 0, 0,
        0.213 - 0.213 * s, 0.715 - 0.715 * s, 0.072 + 0.928 * s, 0, 0,
        0, 0, 0, 1, 0,
      ];
    }
    case "grayscale": {
      const g = 1 - Math.min(1, amount);
      return [
        0.2126 + 0.7874 * g, 0.7152 - 0.7152 * g, 0.0722 - 0.0722 * g, 0, 0,
        0.2126 - 0.2126 * g, 0.7152 + 0.2848 * g, 0.0722 - 0.0722 * g, 0, 0,
        0.2126 - 0.2126 * g, 0.7152 - 0.7152 * g, 0.0722 + 0.9278 * g, 0, 0,
        0, 0, 0, 1, 0,
      ];
    }
    case "sepia": {
      const s = 1 - Math.min(1, amount);
      return [
        0.393 + 0.607 * s, 0.769 - 0.769 * s, 0.189 - 0.189 * s, 0, 0,
        0.349 - 0.349 * s, 0.686 + 0.314 * s, 0.168 - 0.168 * s, 0, 0,
        0.272 - 0.272 * s, 0.534 - 0.534 * s, 0.131 + 0.869 * s, 0, 0,
        0, 0, 0, 1, 0,
      ];
    }
    case "hue-rotate": {
      const r = (amount * Math.PI) / 180, c = Math.cos(r), s = Math.sin(r);
      return [
        0.213 + c * 0.787 - s * 0.213, 0.715 - c * 0.715 - s * 0.715, 0.072 - c * 0.072 + s * 0.928, 0, 0,
        0.213 - c * 0.213 + s * 0.143, 0.715 + c * 0.285 + s * 0.14, 0.072 - c * 0.072 - s * 0.283, 0, 0,
        0.213 - c * 0.213 - s * 0.787, 0.715 - c * 0.715 + s * 0.715, 0.072 + c * 0.928 + s * 0.072, 0, 0,
        0, 0, 0, 1, 0,
      ];
    }
    case "invert": {
      const a = Math.min(1, amount), k = 1 - 2 * a;
      return [k, 0, 0, 0, a, 0, k, 0, 0, a, 0, 0, k, 0, a, 0, 0, 0, 1, 0];
    }
    default: return null;
  }
}

/** Compose a CSS filter string's colour functions into one matrix (blur/drop-shadow ignored); null when none. */
export function filterMatrix(filter: string): ColorMatrix | null {
  const re = /([a-z-]+)\(\s*(-?[\d.]+)\s*(%|deg|rad|turn|px)?\s*\)/g;
  let m: RegExpExecArray | null;
  let out: ColorMatrix | null = null;
  while ((m = re.exec(filter))) {
    let v = Number(m[2]);
    if (m[3] === "%") v /= 100;
    else if (m[3] === "rad") v = (v * 180) / Math.PI;
    else if (m[3] === "turn") v *= 360;
    const fm = filterFunctionMatrix(m[1], v);
    if (fm) out = out ? composeMatrix(fm, out) : fm;
  }
  return out;
}

/** In-place RGBA8 matrix application (CSS filters clamp after every function; one clamp here is the documented
 *  approximation, exact for single functions and within a few code values for grades). */
export function applyColorMatrix(data: Uint8ClampedArray | Uint8Array, m: ColorMatrix): void {
  const o0 = m[4] * 255, o1 = m[9] * 255, o2 = m[14] * 255, o3 = m[19] * 255;
  for (let i = 0; i < data.length; i += 4) {
    const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
    const nr = m[0] * r + m[1] * g + m[2] * b + m[3] * a + o0;
    const ng = m[5] * r + m[6] * g + m[7] * b + m[8] * a + o1;
    const nb = m[10] * r + m[11] * g + m[12] * b + m[13] * a + o2;
    const na = m[15] * r + m[16] * g + m[17] * b + m[18] * a + o3;
    data[i] = nr < 0 ? 0 : nr > 255 ? 255 : nr + 0.5 | 0;
    data[i + 1] = ng < 0 ? 0 : ng > 255 ? 255 : ng + 0.5 | 0;
    data[i + 2] = nb < 0 ? 0 : nb > 255 ? 255 : nb + 0.5 | 0;
    data[i + 3] = na < 0 ? 0 : na > 255 ? 255 : na + 0.5 | 0;
  }
}

// ----------------------------------------------------------------------------------------------- grain, pixelate

const TILE = 256;
const grainTiles = new WeakMap<FxHost, Map<number, FxCanvas>>();
function grainTile(host: FxHost, seed: number): FxCanvas {
  let tiles = grainTiles.get(host);
  if (!tiles) { tiles = new Map(); grainTiles.set(host, tiles); }
  let tile = tiles.get(seed);
  if (tile) return tile;
  tile = host.createCanvas(TILE, TILE);
  const tctx = tile.getContext("2d");
  const img = tctx.getImageData(0, 0, TILE, TILE);
  const rnd = mulberry32(seed >>> 0);
  for (let i = 0; i < img.data.length; i += 4) {
    // Triangular noise (sum of two uniforms) around mid grey: overlay leaves mid grey untouched.
    const v = 128 + (rnd() + rnd() - 1) * 110;
    img.data[i] = img.data[i + 1] = img.data[i + 2] = v;
    img.data[i + 3] = 255;
  }
  tctx.putImageData(img, 0, 0);
  tiles.set(seed, tile);
  return tile;
}

function drawGrain(ctx: FxContext2D, op: Extract<DrawOp, { op: "grain" }>, host: FxHost, base: Mat2D) {
  if (op.amount <= 0) return;
  const size = canvasSize(ctx);
  if (!size) return;
  const tile = grainTile(host, op.seed);
  prepare(ctx, op.amount, "overlay");
  ctx.setTransform(1, 0, 0, 1, 0, 0);
  const scale = Math.max(1, Math.round(Math.abs(base[0]) || 1));
  const step = TILE * scale;
  const ox = Math.floor(hash01(op.seed, op.frame * 2) * step), oy = Math.floor(hash01(op.seed, op.frame * 2 + 1) * step);
  for (let y = -oy; y < size.height; y += step) {
    for (let x = -ox; x < size.width; x += step) ctx.drawImage(tile as unknown as CanvasImageSource, x, y, step, step);
  }
}

function drawPixelate(ctx: FxContext2D, op: Extract<DrawOp, { op: "pixelate" }>, base: Mat2D) {
  const size = canvasSize(ctx);
  if (!size || op.w <= 0 || op.h <= 0) return;
  const sx = Math.abs(base[0]) || 1, sy = Math.abs(base[3]) || 1;
  const x0 = Math.max(0, Math.floor(op.x * sx + base[4])), y0 = Math.max(0, Math.floor(op.y * sy + base[5]));
  const x1 = Math.min(size.width, Math.ceil((op.x + op.w) * sx + base[4])), y1 = Math.min(size.height, Math.ceil((op.y + op.h) * sy + base[5]));
  const w = x1 - x0, h = y1 - y0;
  if (w <= 0 || h <= 0) return;
  const block = Math.max(2, Math.round(op.block * sx));
  const img = ctx.getImageData(x0, y0, w, h);
  const d = img.data;
  for (let by = 0; by < h; by += block) {
    for (let bx = 0; bx < w; bx += block) {
      const bw = Math.min(block, w - bx), bh = Math.min(block, h - by);
      let r = 0, g = 0, b = 0, n = 0;
      for (let y = by; y < by + bh; y++) for (let x = bx; x < bx + bw; x++) {
        const i = (y * w + x) * 4;
        r += d[i]; g += d[i + 1]; b += d[i + 2]; n++;
      }
      r = Math.round(r / n); g = Math.round(g / n); b = Math.round(b / n);
      for (let y = by; y < by + bh; y++) for (let x = bx; x < bx + bw; x++) {
        const i = (y * w + x) * 4;
        d[i] = r; d[i + 1] = g; d[i + 2] = b;
      }
    }
  }
  ctx.putImageData(img, x0, y0);
}
