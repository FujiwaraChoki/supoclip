// Music duck envelope: mirror of backend/src/edit_fx/duck.py (§9). Keep the two in lockstep. All times are F.
// The same breakpoints drive the server bus (numpy) and the browser preview (GainNode ramps in dB → linear).
//
// 1. Speech spans are clipped to [0, out]. Spans whose ramps would meet (gap ≤ attack + release) are joined.
// 2. Base curve: gain_db with no speech; per span a ramp from gain_db at start - attack to gain_db + duck_db at
//    start, held to end, back to gain_db at end + release.
// 3. Fades are an extra dB offset: (floor - gain) * (1 - t/fade_in) over the first fade_in seconds and
//    (floor - gain) * (1 - (out - t)/fade_out) over the last fade_out; scaled down together when they do not fit.
// 4. Breakpoints: 0, out, every base corner and both fade ends inside (0, out), valued max(floor, base + fade).

type Span = readonly [number, number];
export type Breakpoint = [number, number];   // (t, dB)

/** Word spans (F) joined into phrases: a gap of at most mergeGap seconds keeps one span. */
export function speechSpans(wordsFinal: readonly Span[], mergeGap = 0.35): [number, number][] {
  const spans: [number, number][] = [];
  const sorted = wordsFinal.map(([s, e]) => [Number(s), Number(e)] as [number, number])
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  for (const [start, end] of sorted) {
    if (end < start) continue;
    const last = spans[spans.length - 1];
    if (last && start - last[1] <= mergeGap) {
      if (end > last[1]) last[1] = end;
    } else {
      spans.push([start, end]);
    }
  }
  return spans;
}

function interp(points: readonly (readonly [number, number])[], t: number): number {
  const n = points.length;
  if (t <= points[0][0]) return points[0][1];
  if (t >= points[n - 1][0]) return points[n - 1][1];
  let lo = 0;           // bisect_right: first index with points[i].t > t
  let hi = n;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (t < points[mid][0]) hi = mid;
    else lo = mid + 1;
  }
  const [t0, v0] = points[lo - 1];
  const [t1, v1] = points[lo];
  if (t1 <= t0) return v1;
  return v0 + ((v1 - v0) * (t - t0)) / (t1 - t0);
}

/** The level (dB) at t: linear between points, held flat before the first and after the last. [] → 0. */
export function envelopeDbAt(points: readonly (readonly [number, number])[], t: number): number {
  if (!points.length) return 0;
  return interp(points, t);
}

/** duck_envelope's keyword arguments; a MusicBed plus out_duration fits (`{ ...music, out_duration }`). */
export interface DuckOptions {
  gain_db: number; duck_db: number; attack: number; release: number; fade_in: number; fade_out: number;
  out_duration: number; floor_db?: number;
}

/** Breakpoints (t, dB), strictly increasing t, linear in dB between points (see the header for the rules). */
export function duckEnvelope(spans: readonly Span[], opts: DuckOptions): Breakpoint[] {
  const { gain_db: gain, duck_db: duck, attack, release } = opts;
  const floor = opts.floor_db ?? -60;
  const out = Math.max(0, opts.out_duration);
  if (out <= 0) return [[0, floor]];
  let fadeIn = Math.max(0, opts.fade_in);
  let fadeOut = Math.max(0, opts.fade_out);
  if (fadeIn + fadeOut > out) {
    const scale = out / (fadeIn + fadeOut);
    fadeIn *= scale;
    fadeOut *= scale;
  }

  const joined: [number, number][] = [];
  const clipped = spans.map(([s, e]) => [Math.max(0, s), Math.min(out, e)] as [number, number])
    .sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  for (const [start, end] of clipped) {
    if (end <= start) continue;
    const last = joined[joined.length - 1];
    if (last && start - last[1] <= attack + release) {
      if (end > last[1]) last[1] = end;
    } else {
      joined.push([start, end]);
    }
  }

  const ducked = gain + duck;
  const base: Breakpoint[] = [[-1e9, gain]];
  for (const [start, end] of joined) {
    base.push([start - attack, gain], [start, ducked], [end, ducked], [end + release, gain]);
  }
  base.push([1e9, gain]);

  const times = new Set<number>([0, out]);
  for (const [t] of base) if (t > 0 && t < out) times.add(t);
  for (const t of [fadeIn, out - fadeOut]) if (t > 0 && t < out) times.add(t);

  const fadeOffset = (t: number) => {
    let offset = 0;
    if (fadeIn > 0 && t < fadeIn) offset += (floor - gain) * (1 - t / fadeIn);
    if (fadeOut > 0 && t > out - fadeOut) offset += (floor - gain) * (1 - (out - t) / fadeOut);
    return offset;
  };
  return [...times].sort((a, b) => a - b).map((t) => [t, Math.max(floor, interp(base, t) + fadeOffset(t))]);
}
