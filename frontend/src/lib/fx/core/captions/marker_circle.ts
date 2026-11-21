// `marker_circle` (WP7, §11.1): clean bold captions with the spoken word in the accent colour, and hand-drawn marker
// accents on the key word: a circle (most beats), an underline swoosh (punchlines), a strike-through (negations) and a
// curved arrow (numbers). Every mark is a seeded polyline drawn in with a dash over 150–300 ms, then "boils" (re-jitters
// ±1.5u at 8 fps) like a felt-tip line on paper until it fades out in the last 120 ms. A soft offset shadow under each
// stroke keeps it readable over bright footage. All constants are in u = min(W, H) / 1080.
import type { DrawOp, Mat2D } from "../host";
import type { DrawerContext } from "../registry";
import type { AccentCue, AccentVariant } from "../types";
import type { Rect } from "../constants";
import { clamp01, easeOutCubic, lerp, progress, stepIndex } from "../easing";
import { mul, pathOp, safeRect, translation } from "../layout";
import { BASE_LOOKS, baseCaptionOps } from "./base";
import { registerAccent, registerCaptionStyle } from "../registry";
import { accentColor, anchorAt, type WordAnchor } from "./anchor";

/** §11.1 marker constants (px values in u). */
export const MARKER = {
  strokePx: 10,
  circleDrawIn: 0.25,
  underlineDrawIn: 0.25,
  strikeDrawIn: 0.15,
  arrowDrawIn: 0.3,
  fadeOut: 0.12,
  boilFps: 8,
  boilPx: 1.5,
  circlePoints: 48,
  circleTurns: 1.15,
  circleJitter: 0.04,
  circlePad: 0.18,
  underlineMinDeg: -6,
  underlineMaxDeg: 3,
  strikeDeg: -4,
  arrowHeadPx: 28,
  shadowPx: 4,
  shadowColor: "rgba(0,0,0,0.3)",
} as const;

const DEG = Math.PI / 180;

/** Draw-in progress (0 → 1, easeOutCubic) `local` seconds into a mark that draws in over `dur` seconds. */
export const drawInFraction = (local: number, dur: number): number => easeOutCubic(progress(local, 0, dur));

/** Opacity of a mark that fades out over the last MARKER.fadeOut seconds before `end` (edit time). */
export const fadeOutAlpha = (t: number, end: number, fade: number = MARKER.fadeOut): number => clamp01((end - t) / fade);

/** Polyline length of [x0, y0, x1, y1, ...]. */
export function polylineLength(points: readonly number[]): number {
  let length = 0;
  for (let i = 2; i + 1 < points.length; i += 2) length += Math.hypot(points[i] - points[i - 2], points[i + 1] - points[i - 1]);
  return length;
}

/** Hand-drawn boil: every point re-jittered by up to ±amp at 8 fps (deterministic from floor(t·8) and the cue seed). */
export function boil(points: readonly number[], t: number, amp: number, hash: (n: number) => number): number[] {
  const step = stepIndex(t, MARKER.boilFps);
  return points.map((v, i) => v + (hash(1000 + step * 211 + i) * 2 - 1) * amp);
}

/** The stroke at draw-in fraction k: a dash as long as the path, offset so only the first k of it shows. */
function strokeOps(points: number[], k: number, color: string, alpha: number, transform: Mat2D, u: number,
  smooth: boolean, width: number = MARKER.strokePx * u): DrawOp[] {
  if (k <= 0 || alpha <= 0 || points.length < 4) return [];
  // The smoothed curve is never longer than its control polyline, so a dash of that length covers it.
  const length = polylineLength(points) + width;
  const dash: [number, number] | null = k >= 1 ? null : [length, length];
  const dashOffset = k >= 1 ? 0 : length * (1 - k);
  const common = { points, smooth, strokePx: width, cap: "round" as const, dash, dashOffset, alpha };
  return [
    pathOp({ ...common, stroke: MARKER.shadowColor, transform: mul(translation(0, MARKER.shadowPx * u), transform) }),
    pathOp({ ...common, stroke: color, transform }),
  ];
}

const rotatePoints = (points: number[], rad: number, cx: number, cy: number): number[] => {
  const c = Math.cos(rad), s = Math.sin(rad), out: number[] = [];
  for (let i = 0; i + 1 < points.length; i += 2) {
    const x = points[i] - cx, y = points[i + 1] - cy;
    out.push(cx + x * c - y * s, cy + x * s + y * c);
  }
  return out;
};

const quadPoints = (x0: number, y0: number, cx: number, cy: number, x1: number, y1: number, n: number): number[] => {
  const out: number[] = [];
  for (let i = 0; i < n; i++) {
    const s = i / (n - 1), a = (1 - s) * (1 - s), b = 2 * (1 - s) * s, c = s * s;
    out.push(a * x0 + b * cx + c * x1, a * y0 + b * cy + c * y1);
  }
  return out;
};

// ----------------------------------------------------------------------------------------------- geometry

/**
 * The circle: an ellipse around the word box padded by 18 %, sized to clear the box corners, as a 48-point polyline over
 * 1.15 turns (open end). Radius jitter is three seeded harmonics summing to ±4 %; a slight outward spiral (+5 % by the end) keeps the
 * overlapping tail from retracing the start (it only grows outward, never into the word), the way a quick marker loop never closes on itself.
 */
export function circlePoints(box: Rect, hash: (n: number) => number): number[] {
  const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
  const pw = box.w * (1 + MARKER.circlePad), ph = box.h * (1 + MARKER.circlePad);
  // Tight horizontally (neighbouring words stay mostly clear), roomy vertically so the word's corners stay inside.
  const rx = (pw / 2) * 1.06 + 0.14 * ph, ry = (ph / 2) * 1.55;
  const tilt = (hash(1) * 2 - 1) * 4 * DEG;
  const start = -0.75 * Math.PI + (hash(2) - 0.5) * 0.6;
  const weights = [hash(3), hash(4), hash(5)].map((v) => 0.3 + 0.7 * v);
  const total = weights.reduce((s, v) => s + v, 0);
  const amps = weights.map((v) => (MARKER.circleJitter * v) / total);
  const phases = [hash(6), hash(7), hash(8)].map((v) => v * 2 * Math.PI);
  const n = MARKER.circlePoints, pts: number[] = [];
  for (let i = 0; i < n; i++) {
    const s = i / (n - 1);
    const theta = start - s * MARKER.circleTurns * 2 * Math.PI;      // counter-clockwise on screen, like a right hand
    let r = 1;
    for (let k = 0; k < 3; k++) r += amps[k] * Math.sin((k + 1) * theta + phases[k]);
    r *= 1 + 0.05 * s;
    pts.push(cx + rx * r * Math.cos(theta), cy + ry * r * Math.sin(theta));
  }
  return rotatePoints(pts, tilt, cx, cy);
}

/** The underline: a quadratic swoosh under the word, dipping in the middle and flicking up at the end, tilted by a
 *  seeded angle in [−6°, +3°]. */
export function underlinePoints(box: Rect, u: number, hash: (n: number) => number): number[] {
  const x0 = box.x - 0.04 * box.w - 6 * u, x1 = box.x + box.w * 1.06 + 10 * u;
  const y = box.y + box.h * 1.12 + 8 * u;   // below the descenders
  const pts = quadPoints(x0, y, (x0 + x1) / 2 + (hash(11) - 0.5) * 0.2 * box.w, y + 0.16 * box.h, x1, y - 0.12 * box.h, 24);
  const angle = lerp(MARKER.underlineMinDeg, MARKER.underlineMaxDeg, hash(12)) * DEG;
  return rotatePoints(pts, angle, (x0 + x1) / 2, y);
}

/** The strike: a nearly straight line through the word, tilted −4°, with a slight seeded wobble. */
export function strikePoints(box: Rect, u: number, hash: (n: number) => number): number[] {
  const x0 = box.x - 12 * u, x1 = box.x + box.w + 12 * u, y = box.y + box.h * 0.52;
  const pts: number[] = [];
  for (let i = 0; i < 8; i++) {
    const s = i / 7;
    pts.push(lerp(x0, x1, s), y + Math.sin(s * Math.PI) * (hash(20) - 0.5) * 6 * u + (hash(21 + i) - 0.5) * 2 * u);
  }
  return rotatePoints(pts, MARKER.strikeDeg * DEG, (x0 + x1) / 2, y);
}

/**
 * The arrow: a curve from the frame side nearer the number to the number's box, ending in a 28u chevron along the
 * curve's final direction. When that side leaves too little room (a wide number), it comes down from above instead.
 * Returns the shaft polyline and the head [arm, tip, arm].
 */
export function arrowGeometry(box: Rect, w: number, h: number, u: number, hash: (n: number) => number): { shaft: number[]; head: number[] } {
  const safe = safeRect(w, h, false);
  const cx = box.x + box.w / 2, cy = box.y + box.h / 2;
  const left = cx < w / 2;
  const sign = left ? -1 : 1;
  const edgeX = left ? safe.x + 16 * u : safe.x + safe.w - 16 * u;
  let tip: [number, number], tail: [number, number], ctrl: [number, number];
  const sideTip: [number, number] = [left ? box.x - 18 * u : box.x + box.w + 18 * u, cy];
  if (Math.abs(sideTip[0] - edgeX) >= 140 * u) {
    tip = sideTip;
    const rise = Math.min(240 * u, Math.abs(tip[0] - edgeX) * 0.75) + (hash(30) - 0.5) * 40 * u;
    tail = [edgeX, Math.max(safe.y, tip[1] - rise)];
    ctrl = [lerp(tail[0], tip[0], 0.18), lerp(tail[1], tip[1], 0.96)];
  } else {
    tip = [box.x + box.w * (left ? 0.28 : 0.72), box.y - 16 * u];
    const tx = Math.min(safe.x + safe.w, Math.max(safe.x, tip[0] + sign * (170 + (hash(31) - 0.5) * 40) * u));
    tail = [tx, Math.max(safe.y, tip[1] - 210 * u)];
    ctrl = [lerp(tail[0], tip[0], 0.92), lerp(tail[1], tip[1], 0.12)];
  }
  const shaft = quadPoints(tail[0], tail[1], ctrl[0], ctrl[1], tip[0], tip[1], 20);
  let dx = tip[0] - ctrl[0], dy = tip[1] - ctrl[1];
  const len = Math.hypot(dx, dy) || 1;
  dx /= len; dy /= len;
  const arm = MARKER.arrowHeadPx * u, spread = 32 * DEG;
  const back = (a: number): [number, number] => {
    const c = Math.cos(a), s = Math.sin(a);
    return [tip[0] - arm * (dx * c - dy * s), tip[1] - arm * (dx * s + dy * c)];
  };
  const a1 = back(spread), a2 = back(-spread);
  return { shaft, head: [a1[0], a1[1], tip[0], tip[1], a2[0], a2[1]] };
}

// ----------------------------------------------------------------------------------------------- drawers

type Geometry = (a: WordAnchor, cue: AccentCue, dc: DrawerContext) => { points: number[]; smooth: boolean; drawIn: number; head?: number[] };

const GEOMETRY: Record<"circle" | "underline" | "strike" | "arrow", Geometry> = {
  circle: (a, _c, dc) => ({ points: circlePoints(a.box, dc.hash), smooth: true, drawIn: MARKER.circleDrawIn }),
  underline: (a, _c, dc) => ({ points: underlinePoints(a.box, dc.u, dc.hash), smooth: true, drawIn: MARKER.underlineDrawIn }),
  strike: (a, _c, dc) => ({ points: strikePoints(a.box, dc.u, dc.hash), smooth: true, drawIn: MARKER.strikeDrawIn }),
  arrow: (a, _c, dc) => {
    const g = arrowGeometry(a.box, dc.scene.width, dc.scene.height, dc.u, dc.hash);
    return { points: g.shaft, head: g.head, smooth: true, drawIn: MARKER.arrowDrawIn };
  },
};

/** Ops of one marker mark at the drawer context's time. */
export function markerOps(variant: keyof typeof GEOMETRY, cue: AccentCue, dc: DrawerContext): DrawOp[] {
  const p = dc.scene, u = dc.u;
  const anchor = anchorAt(p, cue, dc.t);
  if (!anchor) return [];
  // The mark leaves with its word: it fades out before the cue ends or before the caption line changes.
  const end = anchor.chunk ? Math.min(cue.end, anchor.chunk.end) : cue.end;
  const fade = fadeOutAlpha(dc.t, end);
  const alpha = anchor.alpha * fade;
  // A standalone keyword (captions off) leaves with its mark instead of blinking off at the cue end.
  const textOps = fade >= 1 ? anchor.textOps : anchor.textOps.map((o) => (o.op === "text" ? { ...o, alpha: o.alpha * fade } : o));
  if (alpha <= 0) return [];
  const g = GEOMETRY[variant](anchor, cue, dc);
  const k = drawInFraction(dc.local, g.drawIn);
  const boiling = dc.local >= g.drawIn;
  const amp = MARKER.boilPx * u;
  const color = accentColor(p);
  if (!g.head) {
    const pts = boiling ? boil(g.points, dc.t, amp, dc.hash) : g.points;
    return [...textOps, ...strokeOps(pts, k, color, alpha, anchor.transform, u, g.smooth)];
  }
  // Arrow: the shaft takes the first 80 % of the draw-in, the head grows in over the rest.
  const shaftK = clamp01(k / 0.8), headK = clamp01((k - 0.8) / 0.2);
  const shaft = boiling ? boil(g.points, dc.t, amp, dc.hash) : g.points;
  const head = boiling ? boil(g.head, dc.t, amp, (n) => dc.hash(n + 5000)) : g.head;
  return [
    ...textOps,
    ...strokeOps(shaft, shaftK, color, alpha, anchor.transform, u, true),
    ...(headK > 0 ? strokeOps(scaleHead(head, headK), 1, color, alpha, anchor.transform, u, false) : []),
  ];
}

/** The head's arms grown from the tip to `k` of their length. */
function scaleHead(head: number[], k: number): number[] {
  const tx = head[2], ty = head[3];
  return [tx + (head[0] - tx) * k, ty + (head[1] - ty) * k, tx, ty, tx + (head[4] - tx) * k, ty + (head[5] - ty) * k];
}

registerCaptionStyle({
  template: "marker_circle",
  line: (chunk, dc) => baseCaptionOps(chunk, dc, BASE_LOOKS.marker_circle),
});

for (const variant of ["circle", "underline", "strike", "arrow"] as const satisfies readonly AccentVariant[]) {
  registerAccent({
    variant,
    layer: "caption",
    window: (cue) => [cue.start, cue.end],
    ops: (cue, dc) => markerOps(variant, cue, dc),
  });
}
