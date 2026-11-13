// Shared geometry for fx-core drawers: 2D matrices, the picture transform, op builders with defaults, fonts, safe
// areas and collision-free placement. Pure (no host, no canvas); every drawer may import it.
import type { Blend, DrawOp, Mat2D, Shadow } from "./host";
import { coverScale, pictureSpreadX, type PictureState } from "./registry";
import { CAPTION_SAFE, captionSafe, HOOK_BAND, type Rect } from "./constants";

// ----------------------------------------------------------------------------------------------- matrices

export const IDENTITY: Mat2D = [1, 0, 0, 1, 0, 0];

/** m ∘ n: the matrix that applies n first, then m (canvas `setTransform(m); transform(n)`). */
export function mul(m: Mat2D, n: Mat2D): Mat2D {
  return [
    m[0] * n[0] + m[2] * n[1], m[1] * n[0] + m[3] * n[1],
    m[0] * n[2] + m[2] * n[3], m[1] * n[2] + m[3] * n[3],
    m[0] * n[4] + m[2] * n[5] + m[4], m[1] * n[4] + m[3] * n[5] + m[5],
  ];
}
export const translation = (x: number, y: number): Mat2D => [1, 0, 0, 1, x, y];
export const scaling = (sx: number, sy = sx): Mat2D => [sx, 0, 0, sy, 0, 0];
export const rotation = (rad: number): Mat2D => {
  const c = Math.cos(rad), s = Math.sin(rad);
  return [c, s, -s, c, 0, 0];
};
/** Scale by s about (cx, cy). */
export const scaleAbout = (s: number, cx: number, cy: number): Mat2D => [s, 0, 0, s, cx - s * cx, cy - s * cy];
/** Rotate by rad about (cx, cy). */
export const rotateAbout = (rad: number, cx: number, cy: number): Mat2D =>
  mul(translation(cx, cy), mul(rotation(rad), translation(-cx, -cy)));
/** Local → output for an element authored around its own origin: translate(x, y) · rotate(rot) · scale(s). */
export const placeAt = (x: number, y: number, rot = 0, s = 1): Mat2D => {
  const c = Math.cos(rot) * s, n = Math.sin(rot) * s;
  return [c, n, -n, c, x, y];
};
export function applyMat(m: Mat2D, x: number, y: number): [number, number] {
  return [m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5]];
}
export function invert(m: Mat2D): Mat2D {
  const det = m[0] * m[3] - m[1] * m[2];
  if (!det) return [...IDENTITY];
  const a = m[3] / det, b = -m[1] / det, c = -m[2] / det, d = m[0] / det;
  return [a, b, c, d, -(a * m[4] + c * m[5]), -(b * m[4] + d * m[5])];
}
/** Axis-aligned bounds of a rect after a transform. */
export function transformRect(m: Mat2D, r: Rect): Rect {
  const pts = [applyMat(m, r.x, r.y), applyMat(m, r.x + r.w, r.y), applyMat(m, r.x, r.y + r.h), applyMat(m, r.x + r.w, r.y + r.h)];
  const xs = pts.map((p) => p[0]), ys = pts.map((p) => p[1]);
  const x = Math.min(...xs), y = Math.min(...ys);
  return { x, y, w: Math.max(...xs) - x, h: Math.max(...ys) - y };
}
export const isIdentity = (m: Mat2D, eps = 1e-9): boolean => m.every((v, i) => Math.abs(v - IDENTITY[i]) <= eps);

// ----------------------------------------------------------------------------------------------- the picture

/** The final picture scale for a state: never below what keeps every frame corner covered (AR-M5). */
export const pictureScale = (state: PictureState, w: number, h: number): number => Math.max(state.scale, coverScale(state, w, h));

/**
 * Output-px matrix of the base picture for a composed PictureState: zoom about the anchor, rotate about the frame
 * centre, then translate. The scale is raised to coverScale and the anchor is clamped so no edge ever shows, even
 * when the face anchor sits near a frame edge (zoom `punch` toward a face in the corner).
 */
export function pictureMatrix(state: PictureState, w: number, h: number): Mat2D {
  const s = pictureScale(state, w, h);
  const cx = w / 2, cy = h / 2;
  const axp = state.ax * w, ayp = state.ay * h;
  // The picture point that lands on the frame centre (before dx/dy): zoom about the anchor keeps the anchor fixed.
  let p0x = axp + (cx - axp) / s, p0y = ayp + (cy - ayp) / s;
  const cos = Math.cos(state.rot), sin = Math.sin(state.rot), spread = pictureSpreadX(state);
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (const qx of [0, w]) for (const qy of [0, h]) for (const off of [-spread, spread]) {
    const vx = qx - cx - state.dx - off, vy = qy - cy - state.dy;
    const rx = (cos * vx + sin * vy) / s, ry = (-sin * vx + cos * vy) / s;   // R⁻¹ v / s
    minX = Math.min(minX, rx); maxX = Math.max(maxX, rx); minY = Math.min(minY, ry); maxY = Math.max(maxY, ry);
  }
  p0x = clampInto(p0x, -minX, w - maxX, cx);
  p0y = clampInto(p0y, -minY, h - maxY, cy);
  const a = s * cos, b = s * sin, c = -s * sin, d = s * cos;
  return [a, b, c, d, cx + state.dx - (a * p0x + c * p0y), cy + state.dy - (b * p0x + d * p0y)];
}
const clampInto = (v: number, lo: number, hi: number, fallback: number) => (lo > hi ? fallback : Math.min(hi, Math.max(lo, v)));

type PictureOp = Extract<DrawOp, { op: "picture" }>;
const picture = (transform: Mat2D, filter: string, extra: Partial<PictureOp> = {}): PictureOp => ({
  op: "picture", transform, filter, alpha: 1, blend: "source-over", channel: "rgb", dx: 0, dy: 0, cutout: false, clip: null,
  ...extra,
});

/** Motion-blur ghosts for blurX: equal-weight average of 7 shifted draws (draw i at alpha 1/(i+1)). */
const GHOSTS = [0, -1, 1, -2 / 3, 2 / 3, -1 / 3, 1 / 3];

/**
 * The picture ops for a state (§6.5 step 1): one draw, or 7 ghost draws for whip blur, or three channel draws
 * (`lighter`) for RGB split, followed by the glitch slices (each clipped to its band and shifted).
 */
export function pictureOps(state: PictureState, w: number, h: number): PictureOp[] {
  const m = pictureMatrix(state, w, h);
  const filter = state.filter.join(" ");
  const ops: PictureOp[] = [];
  if (state.rgbSplit > 0) {
    const f = state.blurX > 0 ? `${filter} blur(${round3(state.blurX / 4)}px)`.trim() : filter;
    ops.push(picture(m, f, { channel: "r", dx: -state.rgbSplit }));
    ops.push(picture(m, f, { channel: "g", blend: "lighter" }));
    ops.push(picture(m, f, { channel: "b", dx: state.rgbSplit, blend: "lighter" }));
  } else if (state.blurX > 0) {
    GHOSTS.forEach((k, i) => ops.push(picture(m, filter, { dx: round3((k * state.blurX) / 2), alpha: round6(1 / (i + 1)) })));
  } else {
    ops.push(picture(m, filter));
  }
  for (const s of state.slices) {
    if (s.h <= 0) continue;
    ops.push(picture(m, filter, { dx: s.dx, clip: [0, s.y, w, s.h] }));
  }
  return ops;
}

/** The person redrawn over behind text (cutout = alpha-masked person), with the frame's picture transform. */
export function cutoutOp(state: PictureState, w: number, h: number): PictureOp {
  return picture(pictureMatrix(state, w, h), state.filter.join(" "), { cutout: true });
}

/** True when a state leaves the picture exactly as it came in. */
export const isIdentityPicture = (s: PictureState): boolean =>
  s.scale === 1 && s.dx === 0 && s.dy === 0 && s.rot === 0 && s.blurX === 0 && s.filter.length === 0 && s.rgbSplit === 0
  && s.slices.length === 0;

// ----------------------------------------------------------------------------------------------- fonts

/** CSS family for a bundled font stem; the same on both hosts (§4.3). */
export const fxFamily = (stem: string): string => `FX-${stem}`;
/** System fallbacks for scripts the bundled Latin fonts lack (both hosts have Noto). */
export const FONT_FALLBACK = '"Noto Sans", "Noto Sans CJK SC", "Noto Sans Arabic", "Noto Sans Devanagari", "Noto Sans Thai", sans-serif';
/** The canvas `font` string for a family at px: used by rasterize AND by the hosts' measure, so they agree. */
export const fontCss = (family: string, px: number): string => `${round3(px)}px "${family}", ${FONT_FALLBACK}`;

// ----------------------------------------------------------------------------------------------- op builders

type TextOp = Extract<DrawOp, { op: "text" }>;
type PathOp = Extract<DrawOp, { op: "path" }>;
type RectOp = Extract<DrawOp, { op: "rect" }>;
type SpriteOp = Extract<DrawOp, { op: "sprite" }>;
type GradientOp = Extract<DrawOp, { op: "gradient" }>;

/** A text op with defaults: centred, white fill, no stroke, round join, identity transform. `strokePx` is the
 *  outline width outside the glyph edge (the canvas line is drawn twice as wide). Text is vertically centred on y. */
export const textOp = (o: Pick<TextOp, "text" | "font" | "px" | "x" | "y"> & Partial<TextOp>): TextOp => ({
  op: "text", align: "center", fill: "#FFFFFF", stroke: null, strokePx: 0, join: "round", shadow: null, letterSpacing: 0,
  transform: IDENTITY, alpha: 1, blend: "source-over", ...o,
});
export const pathOp = (o: Pick<PathOp, "points"> & Partial<PathOp>): PathOp => ({
  op: "path", closed: false, smooth: false, stroke: "#FFFFFF", strokePx: 6, fill: null, cap: "round", dash: null,
  dashOffset: 0, transform: IDENTITY, alpha: 1, blend: "source-over", ...o,
});
export const rectOp = (o: Pick<RectOp, "x" | "y" | "w" | "h"> & Partial<RectOp>): RectOp => ({
  op: "rect", radius: 0, fill: "#000000", stroke: null, strokePx: 0, transform: IDENTITY, alpha: 1, blend: "source-over", ...o,
});
export const spriteOp = (o: Pick<SpriteOp, "key" | "x" | "y" | "w" | "h"> & Partial<SpriteOp>): SpriteOp => ({
  op: "sprite", transform: IDENTITY, alpha: 1, blend: "source-over", ...o,
});
export const gradientOp = (o: Pick<GradientOp, "kind" | "stops" | "rect"> & Partial<GradientOp>): GradientOp => ({
  op: "gradient", x0: 0, y0: 0, r0: 0, x1: 0, y1: 0, r1: 0, alpha: 1, blend: "source-over", ...o,
});
export const shadow = (color: string, blur: number, dx: number, dy: number): Shadow => ({ color, blur, dx, dy });

/** Apply an extra transform (outer ∘ op.transform) and alpha factor to drawable ops; others pass through. */
export function transformOps(ops: DrawOp[], outer: Mat2D, alpha = 1, blend?: Blend): DrawOp[] {
  return ops.map((o) => {
    if (o.op === "text" || o.op === "path" || o.op === "rect" || o.op === "sprite" || o.op === "picture") {
      return { ...o, transform: mul(outer, o.transform), alpha: o.alpha * alpha, ...(blend ? { blend } : {}) };
    }
    return o;
  });
}

// ----------------------------------------------------------------------------------------------- safe areas

/** The area overlays may use: CAPTION_SAFE margins, minus the hook band while the hook title burns. */
export function safeRect(w: number, h: number, hookActive = false): Rect {
  const m = captionSafe(w, h), side = CAPTION_SAFE.side * w;
  const top = hookActive ? Math.max(m.top, HOOK_BAND * h) : m.top;
  return { x: side, y: top, w: w - 2 * side, h: h - top - m.bottom };
}
export const overlapArea = (a: Rect, b: Rect): number =>
  Math.max(0, Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x)) * Math.max(0, Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y));
export const inflate = (r: Rect, by: number): Rect => ({ x: r.x - by, y: r.y - by, w: r.w + 2 * by, h: r.h + 2 * by });
/** Clamp a w×h box centred at (cx, cy) into `area` (centred on the area when it does not fit). */
export function clampBox(cx: number, cy: number, w: number, h: number, area: Rect): Rect {
  const x = w >= area.w ? area.x + (area.w - w) / 2 : Math.min(area.x + area.w - w, Math.max(area.x, cx - w / 2));
  const y = h >= area.h ? area.y + (area.h - h) / 2 : Math.min(area.y + area.h - h, Math.max(area.y, cy - h / 2));
  return { x, y, w, h };
}
/**
 * Place a w×h box at the first candidate centre that (clamped into `area`) overlaps none of `avoid`; otherwise the
 * candidate with the least overlap. Deterministic: candidates are tried in order.
 */
export function placeAvoiding(w: number, h: number, candidates: readonly [number, number][], avoid: readonly Rect[], area: Rect): Rect {
  let best: Rect | null = null, bestOverlap = Infinity;
  for (const [cx, cy] of candidates) {
    const box = clampBox(cx, cy, w, h, area);
    const overlap = avoid.reduce((sum, r) => sum + overlapArea(box, r), 0);
    if (overlap === 0) return box;
    if (overlap < bestOverlap) { best = box; bestOverlap = overlap; }
  }
  return best ?? clampBox(area.x + area.w / 2, area.y + area.h / 2, w, h, area);
}

export const round3 = (v: number): number => Math.round(v * 1000) / 1000;
export const round6 = (v: number): number => Math.round(v * 1e6) / 1e6;
