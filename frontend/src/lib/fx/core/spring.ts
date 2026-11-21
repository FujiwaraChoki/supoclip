// Damped harmonic springs in closed form (no integration, so any frame can be evaluated on its own).
// x(t) is the displacement from the target; value(t) = to + x(t). ω = natural angular frequency (rad/s), ζ = damping.

export interface SpringParams {
  omega: number;        // rad/s, e.g. 18
  zeta: number;         // damping ratio: < 1 bounces, 1 critical, > 1 sluggish
  from: number;         // value at t = 0
  to: number;           // rest value
  velocity?: number;    // initial velocity (units/s), default 0
}

/** Spring value at t seconds (t ≤ 0 returns `from`). Exact for under-, critically and over-damped springs. */
export function spring(t: number, p: SpringParams): number {
  const { omega: w, zeta: z, from, to } = p;
  const v0 = p.velocity ?? 0;
  if (t <= 0) return from;
  const x0 = from - to;
  if (!(w > 0)) return from + v0 * t;
  let x: number;
  if (z < 1) {
    const wd = w * Math.sqrt(1 - z * z);
    const decay = Math.exp(-z * w * t);
    x = decay * (x0 * Math.cos(wd * t) + ((v0 + z * w * x0) / wd) * Math.sin(wd * t));
  } else if (z === 1) {
    x = Math.exp(-w * t) * (x0 + (v0 + w * x0) * t);
  } else {
    const s = Math.sqrt(z * z - 1);
    const r1 = -w * (z - s);
    const r2 = -w * (z + s);
    const c2 = (v0 - r1 * x0) / (r2 - r1);
    const c1 = x0 - c2;
    x = c1 * Math.exp(r1 * t) + c2 * Math.exp(r2 * t);
  }
  return to + x;
}

/** Velocity of the spring at t (units/s), by a central difference (enough for motion blur / squash). */
export function springVelocity(t: number, p: SpringParams, h = 1e-4): number {
  return (spring(t + h, p) - spring(Math.max(0, t - h), p)) / (t - h < 0 ? t + h : 2 * h);
}

/** Time after which |value - to| stays below `epsilon` × |from - to| (upper bound from the decay envelope). */
export function springSettleTime(p: Pick<SpringParams, "omega" | "zeta">, epsilon = 0.01): number {
  const { omega: w, zeta: z } = p;
  if (!(w > 0) || !(epsilon > 0)) return 0;
  const rate = z < 1 ? z * w : w * (z - Math.sqrt(z * z - 1));
  const margin = z < 1 ? 1 / Math.sqrt(1 - z * z) : z === 1 ? 4 : 1;
  return rate > 0 ? Math.log(margin / epsilon) / rate : Infinity;
}

/** A wobble that decays with time constant tau: amp · e^(−t/τ) · sin(2π f t + phase). */
export const decayingWobble = (t: number, amp: number, freq: number, tau: number, phase = 0): number =>
  t < 0 ? 0 : amp * Math.exp(-t / tau) * Math.sin(2 * Math.PI * freq * t + phase);

/** Peak overshoot ratio of a step response for damping ζ < 1: e^(−ζπ/√(1−ζ²)). 0 for ζ ≥ 1. */
export const springOvershoot = (zeta: number): number =>
  zeta >= 1 || zeta <= 0 ? (zeta <= 0 ? 1 : 0) : Math.exp((-zeta * Math.PI) / Math.sqrt(1 - zeta * zeta));
