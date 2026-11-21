// Easing curves and keyframe helpers for fx-core drawers. Pure functions of a progress value; no clocks.

export type Ease = (x: number) => number;

export const clamp = (v: number, lo: number, hi: number): number => Math.min(hi, Math.max(lo, v));
export const clamp01 = (v: number): number => clamp(v, 0, 1);
export const lerp = (a: number, b: number, t: number): number => a + (b - a) * t;
/** Progress of t through [a, b], clamped to 0..1 (0 when the interval is empty and t < b). */
export const progress = (t: number, a: number, b: number): number => (b <= a ? (t >= b ? 1 : 0) : clamp01((t - a) / (b - a)));

export const linear: Ease = (x) => clamp01(x);
export const easeInQuad: Ease = (x) => { x = clamp01(x); return x * x; };
export const easeOutQuad: Ease = (x) => { x = clamp01(x); return 1 - (1 - x) * (1 - x); };
export const easeInOutQuad: Ease = (x) => { x = clamp01(x); return x < 0.5 ? 2 * x * x : 1 - (-2 * x + 2) ** 2 / 2; };
export const easeInCubic: Ease = (x) => { x = clamp01(x); return x * x * x; };
export const easeOutCubic: Ease = (x) => { x = clamp01(x); return 1 - (1 - x) ** 3; };
export const easeInOutCubic: Ease = (x) => { x = clamp01(x); return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2; };
export const easeInSine: Ease = (x) => 1 - Math.cos((clamp01(x) * Math.PI) / 2);
export const easeOutSine: Ease = (x) => Math.sin((clamp01(x) * Math.PI) / 2);
export const easeInOutSine: Ease = (x) => -(Math.cos(Math.PI * clamp01(x)) - 1) / 2;
export const easeOutExpo: Ease = (x) => { x = clamp01(x); return x === 1 ? 1 : 1 - 2 ** (-10 * x); };
export const easeInExpo: Ease = (x) => { x = clamp01(x); return x === 0 ? 0 : 2 ** (10 * x - 10); };
/** Overshoots past 1 then settles (s = 1.70158 ≈ 10 % overshoot). */
export const easeOutBack = (x: number, s = 1.70158): number => {
  x = clamp01(x) - 1;
  return 1 + (s + 1) * x * x * x + s * x * x;
};
export const easeOutElastic: Ease = (x) => {
  x = clamp01(x);
  if (x === 0 || x === 1) return x;
  return 2 ** (-10 * x) * Math.sin(((x * 10 - 0.75) * 2 * Math.PI) / 3) + 1;
};

export const EASES = {
  linear, easeInQuad, easeOutQuad, easeInOutQuad, easeInCubic, easeOutCubic, easeInOutCubic, easeInSine, easeOutSine,
  easeInOutSine, easeOutExpo, easeInExpo, easeOutElastic, easeOutBack: (x: number) => easeOutBack(x),
} as const satisfies Record<string, Ease>;
export type EaseName = keyof typeof EASES;

/** A keyframe: value at time t (seconds, local to the effect); `ease` shapes the segment that ENDS at this key. */
export interface Keyframe { t: number; v: number; ease?: Ease }

/**
 * Piecewise value through keyframes sorted by t; holds the first/last value outside them.
 * e.g. slam scale: keyframes([{t:0,v:1.6},{t:0.11,v:0.95,ease:easeOutQuad},{t:0.18,v:1,ease:easeOutQuad}], local).
 */
export function keyframes(keys: readonly Keyframe[], t: number): number {
  if (!keys.length) return 0;
  if (t <= keys[0].t) return keys[0].v;
  for (let i = 1; i < keys.length; i += 1) {
    const b = keys[i];
    if (t <= b.t) {
      const a = keys[i - 1];
      const span = b.t - a.t;
      const x = span <= 0 ? 1 : (t - a.t) / span;
      return lerp(a.v, b.v, (b.ease ?? linear)(x));
    }
  }
  return keys[keys.length - 1].v;
}

/**
 * Envelope for an effect that fades in over `inDur`, holds, and fades out over the last `outDur` of [0, dur].
 * Returns 0..1; 0 outside the window.
 */
export function inHoldOut(local: number, dur: number, inDur: number, outDur: number, ease: Ease = linear): number {
  if (local < 0 || local > dur) return 0;
  const a = inDur > 0 ? ease(local / inDur) : 1;
  const b = outDur > 0 ? ease((dur - local) / outDur) : 1;
  return clamp01(Math.min(a, b));
}

/** Step at a fixed rate (e.g. 8 fps "boil"): the index of the step containing t. */
export const stepIndex = (t: number, rate: number): number => Math.floor(t * rate + 1e-9);
