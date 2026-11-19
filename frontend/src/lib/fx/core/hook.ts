// The hook title of a Dynamic edits clip: the AI-written headline shown for the first seconds. Drawn here (browser and
// deno share this file) whenever the drawing stage runs; the ASS hook in media/captions.py remains only for the levels
// without it.
//
// Two styles, picked by the clip's caption template (HOOK_VARIANTS) so the title belongs to the captions under it:
//   stack    a white label that hugs every line (one silhouette, rounded where the lines differ in width), heavy dark
//            type and a marker box behind the numbers and power words;
//   outline  big type with a thick outline and a soft shadow straight on the picture, the numbers and power words in
//            the accent colour.
// Each template sets its own colours on top (MrBeast's yellow, Neon's glow, a sticker's white edge ...); the font is
// the scene's. Both pop in and fade out.
//
// Layout: balanced lines (one for a short title, two preferred, three before the type shrinks), at most 0.88 W wide.
// Placement: fixed. The title's top edge is at 11 % of the height on every clip, centred, whatever the layout, the
// faces or the captions; only the studio's offset moves it.
import type { DrawOp, Shadow } from "./host";
import type { Rect } from "./constants";
import { clamp, clamp01, easeOutCubic, keyframes, easeOutQuad } from "./easing";
import { pathOp, placeAt, rectOp, round3, textOp } from "./layout";
import { registerGlobal } from "./registry";
import type { PreparedScene } from "./scene";
import type { FxHookTitle } from "./types";

/** How a title is drawn. Lengths in em of the title size unless stated. */
export interface HookVariant {
  style: "stack" | "outline";
  sizeU: number;             // px at a 1080 short side
  minSizeU: number;          // the floor three lines shrink to; only a single word wider than the frame goes below
  lineHeight: number;        // stack: the height of one line's label
  padX: number; padY: number;   // stack: the label around the text; outline: the room the outline and shadow take
  fill: string;              // stack: the label; outline: the type
  ink: string;               // stack: the type; outline: the outline
  strokeEm: number;          // outline: the outline's width
  /** Numbers and power words: a marker box behind them, or the accent as their colour. */
  key: "box" | "color";
  /** Accent used when the scene's is unreadable here (null: the default yellow). */
  accent: string | null;
  /** Ink of a key word on its marker box (stack: the type's own; outline: this). */
  keyInk: string;
  shadow: { dy: number; blur: number; color: string };   // em
  tiltDeg: number;           // added to the look's tilt
}

const STACK: HookVariant = {
  style: "stack", sizeU: 80, minSizeU: 58, lineHeight: 1.4, padX: 0.42, padY: 0, fill: "#FFFFFF", ink: "#0B0B0F",
  strokeEm: 0, key: "box", accent: null, keyInk: "#0B0B0F", shadow: { dy: 0.07, blur: 0.34, color: "rgba(0,0,0,0.32)" }, tiltDeg: 0,
};
const OUTLINE: HookVariant = {
  style: "outline", sizeU: 92, minSizeU: 64, lineHeight: 1.14, padX: 0.16, padY: 0.12, fill: "#FFFFFF", ink: "#000000",
  strokeEm: 0.1, key: "color", accent: null, keyInk: "#FFFFFF", shadow: { dy: 0.07, blur: 0.16, color: "rgba(0,0,0,0.6)" }, tiltDeg: 0,
};

/** The title style of each caption template; a template not listed here gets the stack. */
export const HOOK_VARIANTS: Readonly<Record<string, HookVariant>> = {
  // Clean captions: the label.
  tiktok: STACK, minimal: STACK, podcast: STACK, behind_head: STACK, emoji_drop: STACK, highlighter_swipe: STACK,
  stat_counter: STACK,
  // Stroked, loud captions: outlined type in the template's own colours.
  default: OUTLINE,
  marker_circle: OUTLINE,
  face_doodles: OUTLINE,
  slam: { ...OUTLINE, sizeU: 98, strokeEm: 0.11 },
  kinetic_stack: { ...OUTLINE, sizeU: 98, strokeEm: 0.11 },
  hormozi: { ...OUTLINE, key: "box", accent: "#00D95A" },
  mrbeast: { ...OUTLINE, fill: "#FFE600", accent: "#FF3B30", strokeEm: 0.12 },
  neon: { ...OUTLINE, fill: "#7DF9FF", ink: "#06123A", accent: "#FF4DFF", shadow: { dy: 0, blur: 0.5, color: "rgba(255,0,255,0.75)" } },
  decode_glitch: { ...OUTLINE, ink: "#050816", accent: "#00F0FF", shadow: { dy: 0.05, blur: 0.02, color: "rgba(255,0,170,0.9)" } },
  // A sticker: dark type with a fat white edge, slightly turned.
  sticker_slap: { ...OUTLINE, fill: "#14121A", ink: "#FFFFFF", strokeEm: 0.14, accent: "#FF2E6C", tiltDeg: -3,
    shadow: { dy: 0.09, blur: 0.2, color: "rgba(0,0,0,0.5)" } },
};
/** The default style (tests). */
export const HOOK_STACK = STACK;
export const hookVariant = (template: string | null | undefined): HookVariant => HOOK_VARIANTS[template ?? ""] ?? STACK;

/** Shared geometry and motion, in em of the title size unless stated. */
export const HOOK = {
  lookSize: { subtle: 0.9, bold: 1, max: 1 },
  maxWidth: 0.88,            // of W, the whole title
  radius: 0.34,              // stack: corner radius
  snap: 0.5,                 // stack: neighbouring lines closer in half-width than this share one edge
  oneLine: 0.62,             // a title narrower than this share of the text width stays on one line
  markPadX: 0.12, markHeight: 1.08, markRadius: 0.18,
  maxMarks: 2,
  mark: "#FFE45C",
  tiltDeg: { subtle: 0, bold: 0, max: -2 },
  inSeconds: 0.26, outSeconds: 0.2, fadeIn: 0.12,
  markDelay: 0.2, markSeconds: 0.18, markStagger: 0.08,
} as const;

/** Placement: every title's top edge sits here (fraction of the frame height), under the platforms' top bar. */
export const HOOK_PLACE = { top: 0.11 } as const;

/** Superlatives, absolutes and hype words (== emoji_captions.POWER_WORDS). */
export const HOOK_POWER_WORDS: ReadonlySet<string> = new Set([
  "never", "always", "everything", "nothing", "everyone", "nobody", "anyone", "best", "worst", "most", "biggest",
  "huge", "massive", "tiny", "every", "only", "first", "last", "free", "now", "today", "instantly", "forever",
  "guaranteed", "proven", "secret", "truth", "fact", "literally", "actually", "exactly", "must", "need", "stop",
  "warning", "danger", "critical", "key", "important", "remember", "mistake", "wrong", "right", "perfect", "ultimate",
  "powerful", "insane", "crazy", "incredible", "amazing", "shocking", "viral", "million", "billion", "thousand",
  "percent", "double", "triple", "ten",
]);

const WIDE = /[⺀-鿿가-힯豈-﫿＀-￯\u{20000}-\u{2FA1F}]/u;
/** Closing punctuation that never starts a line. */
const NO_LINE_START = /^[、。，．！？：；）」』】〉》”’…,.!?:;)\]]/u;

export interface HookToken { text: string; glue: boolean }   // glue: a space before it (unless it starts a line)

/** Words split at spaces; text written without spaces (Han, kana, Hangul) breaks between characters. */
export function hookTokens(title: string): HookToken[] {
  const out: HookToken[] = [];
  for (const word of title.trim().split(/\s+/).filter(Boolean)) {
    let run = "", first = true;
    const flush = () => {
      if (!run) return;
      out.push({ text: run, glue: first && out.length > 0 });
      run = ""; first = false;
    };
    for (const ch of Array.from(word)) {
      if (!WIDE.test(ch)) { run += ch; continue; }
      if (NO_LINE_START.test(ch) && (run || !first)) {
        if (run) run += ch;
        else out[out.length - 1].text += ch;
        continue;
      }
      flush();
      out.push({ text: ch, glue: first && out.length > 0 });
      first = false;
    }
    flush();
  }
  return out;
}

const tokenKey = (text: string) => text.toLocaleLowerCase().replace(/[^\p{L}\p{N}%]+/gu, "");

/** Indices of the tokens that get a marker: numbers first, then power words, at most `HOOK.maxMarks`, in text order. */
export function hookMarks(tokens: readonly HookToken[]): number[] {
  const numbers: number[] = [], power: number[] = [];
  tokens.forEach((t, i) => {
    const key = tokenKey(t.text);
    if (/\p{N}/u.test(key)) numbers.push(i);
    else if (HOOK_POWER_WORDS.has(key)) power.push(i);
  });
  return [...numbers, ...power].slice(0, HOOK.maxMarks).sort((a, b) => a - b);
}

/** x from the card centre; mark: marker order (-1 none); mx, mw: the marked part (edge punctuation left out). */
export interface HookWord { text: string; x: number; w: number; mark: number; mx: number; mw: number }
/** y: line centre from the block centre; hw: half the width of the line's label (stack). */
export interface HookLine { words: HookWord[]; text: string; w: number; y: number; hw: number }
export interface HookLayout {
  text: string; family: string; px: number; lines: HookLine[];
  /** The card (output px, before tilt and motion). */
  w: number; h: number; cx: number; cy: number;
  start: number; end: number; tilt: number; look: FxHookTitle["look"]; mark: string; variant: HookVariant;
}

type Measure = (text: string) => number;

/** The partition of `widths` into `count` consecutive lines with the narrowest widest line (ties: the fuller last line). */
function balance(tokens: readonly HookToken[], widths: readonly number[], space: number, count: number): number[][] | null {
  const n = tokens.length;
  if (count > n) return null;
  const lineW = (a: number, b: number) => {   // tokens a..b-1
    let w = 0;
    for (let i = a; i < b; i++) w += widths[i] + (i > a && tokens[i].glue ? space : 0);
    return w;
  };
  let best: number[][] | null = null, bestWidest = Infinity, bestLast = -1;
  const walk = (from: number, left: number, lines: number[][], widest: number) => {
    if (widest > bestWidest) return;
    if (left === 1) {
      const w = lineW(from, n), all = Math.max(widest, w);
      if (all < bestWidest - 1e-6 || (Math.abs(all - bestWidest) <= 1e-6 && w > bestLast)) {
        best = [...lines, range(from, n)]; bestWidest = all; bestLast = w;
      }
      return;
    }
    for (let to = from + 1; to <= n - (left - 1); to++) walk(to, left - 1, [...lines, range(from, to)], Math.max(widest, lineW(from, to)));
  };
  walk(0, count, [], 0);
  return best;
}
const range = (a: number, b: number) => Array.from({ length: b - a }, (_, i) => a + i);

interface Block { px: number; lines: HookLine[]; w: number; h: number }

/** The title's lines and card size for a base size (no position). */
export function hookBlock(title: string, basePx: number, minPx: number, maxCardW: number, measure: (text: string, px: number) => number,
  v: HookVariant = STACK): Block | null {
  const tokens = hookTokens(title);
  if (!tokens.length) return null;
  const marks = hookMarks(tokens);
  const at = (px: number) => {
    const m: Measure = (text) => measure(text, px);
    const widths = tokens.map((t) => m(t.text)), space = m(" ");
    const maxText = Math.max(1, maxCardW - 2 * v.padX * px);
    const widest = (lines: number[][]) => Math.max(...lines.map((l) => l.reduce((s, i, k) => s + widths[i] + (k && tokens[i].glue ? space : 0), 0)));
    const one = balance(tokens, widths, space, 1)!;
    let lines = one;
    if (widest(one) > HOOK.oneLine * maxText) {
      lines = one;
      for (const count of [2, 3]) {
        const split = balance(tokens, widths, space, count);
        if (!split) break;
        lines = split;
        if (widest(split) <= maxText) break;
      }
    }
    return { lines, widths, space, maxText, widest: widest(lines) };
  };
  let px = basePx, fit = at(px);
  // Three lines still too wide: shrink to the floor, then (an unbreakable word) as far as it takes.
  for (let pass = 0; pass < 5 && fit.widest > fit.maxText; pass++) {
    const next = (px * fit.maxText) / fit.widest * 0.995;
    px = pass === 0 ? Math.max(minPx, next) : next;
    fit = at(px);
  }
  const lineH = v.lineHeight * px, count = fit.lines.length;
  const lines: HookLine[] = fit.lines.map((idx, li) => {
    const total = idx.reduce((s, i, k) => s + fit.widths[i] + (k && tokens[i].glue ? fit.space : 0), 0);
    let x = -total / 2;
    const words = idx.map((i, k): HookWord => {
      if (k && tokens[i].glue) x += fit.space;
      const word: HookWord = { text: tokens[i].text, x, w: fit.widths[i], mark: marks.indexOf(i), mx: x, mw: fit.widths[i] };
      if (word.mark >= 0) {
        // The marker sits behind the word itself, not its comma or quotes.
        const lead = /^[\p{P}\p{Z}]*(?=[\p{L}\p{N}$€£¥#])/u.exec(word.text)?.[0] ?? "";
        const core = word.text.slice(lead.length).replace(/[^\p{L}\p{N}%]+$/u, "");
        if (core && core !== word.text) {
          word.mx = x + (lead ? measure(lead, px) : 0);
          word.mw = measure(core, px);
        }
      }
      x += fit.widths[i];
      return word;
    });
    const text = idx.map((i, k) => (k && tokens[i].glue ? " " : "") + tokens[i].text).join("");
    return { words, text, w: total, y: (li - (count - 1) / 2) * lineH, hw: total / 2 + v.padX * px };
  });
  // Neighbouring labels of nearly the same width share one edge (a step shorter than a corner reads as a mistake).
  const snap = HOOK.snap * px;
  for (let pass = 0; pass < lines.length; pass++) {
    for (let i = 0; i + 1 < lines.length; i++) {
      if (lines[i].hw !== lines[i + 1].hw && Math.abs(lines[i].hw - lines[i + 1].hw) < snap) {
        lines[i].hw = lines[i + 1].hw = Math.max(lines[i].hw, lines[i + 1].hw);
      }
    }
  }
  const w = Math.min(maxCardW, Math.max(...lines.map((l) => l.w)) + 2 * v.padX * px);
  return { px, lines, w, h: count * lineH + 2 * v.padY * px };
}

// ----------------------------------------------------------------------------------------------- placement

/** The top edge of every title, whatever is on screen: the same place on every clip (the studio offset adds to it). */
export function hookTop(H: number): number {
  return HOOK_PLACE.top * H;
}

// ----------------------------------------------------------------------------------------------- layout and drawing

const layouts = new WeakMap<PreparedScene, Map<string, HookLayout | null>>();

/** A readable marker colour: the hook's accent, unless it is too pale or too dark behind near-black type. */
export function hookMarkColor(accent: string | null | undefined): string {
  const m = /^#([0-9a-fA-F]{6})/.exec((accent ?? "").trim());
  if (!m) return HOOK.mark;
  const n = parseInt(m[1], 16), r = (n >> 16) & 255, g = (n >> 8) & 255, b = n & 255;
  const luma = (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
  const chroma = (Math.max(r, g, b) - Math.min(r, g, b)) / 255;
  return luma < 0.45 || (luma > 0.9 && chroma < 0.25) ? HOOK.mark : `#${m[1].toUpperCase()}`;
}

/**
 * The laid-out, placed title of a prepared scene (null without one). `text` replaces the title while the studio's
 * field is being typed in. Memoised per scene and text.
 */
export function hookLayout(p: PreparedScene, text?: string): HookLayout | null {
  const hook = p.scene.hook;
  if (!hook) return null;
  const title = (text ?? hook.text).trim();
  let cache = layouts.get(p);
  if (!cache) { cache = new Map(); layouts.set(p, cache); }
  if (cache.has(title)) return cache.get(title)!;
  const u = p.u, W = p.width, H = p.height;
  const look = hook.look === "subtle" || hook.look === "max" ? hook.look : "bold";
  const scale = clamp(hook.scale ?? 1, 0.2, 4);
  const family = p.family(hook.font);
  const variant = hookVariant(hook.template);
  const block = hookBlock(title, variant.sizeU * u * HOOK.lookSize[look] * scale, variant.minSizeU * u * HOOK.lookSize[look] * scale,
    HOOK.maxWidth * W, (t, px) => (p.measure(t, family, 100) * px) / 100, variant);
  let layout: HookLayout | null = null;
  if (block && hook.end > hook.start) {
    layout = {
      text: title, family, px: block.px, lines: block.lines, w: block.w, h: block.h,
      cx: W * (0.5 + (hook.x ?? 0)), cy: hookTop(H) + block.h / 2 + H * (hook.y ?? 0),
      start: hook.start, end: hook.end, tilt: ((HOOK.tiltDeg[look] + variant.tiltDeg) * Math.PI) / 180, look,
      mark: hookKeyColor(hook.accent, variant), variant,
    };
  }
  cache.set(title, layout);
  return layout;
}

/** The card's box on screen (output px), for the studio's selection frame; null outside a title. */
export function hookBounds(p: PreparedScene, text?: string): Rect | null {
  const l = hookLayout(p, text);
  return l ? { x: l.cx - l.w / 2, y: l.cy - l.h / 2, w: l.w, h: l.h } : null;
}

/** Scale and opacity `local` seconds into a title shown for `dur`. */
export function hookMotion(look: HookLayout["look"], local: number, dur: number): { scale: number; alpha: number } {
  const inS = Math.min(HOOK.inSeconds, dur / 3), outS = Math.min(HOOK.outSeconds, dur / 3);
  const left = dur - local;
  if (left < outS) {
    const k = clamp01(left / outS);
    return { scale: 0.96 + 0.04 * k, alpha: k };
  }
  const alpha = clamp01(local / Math.min(HOOK.fadeIn, inS));
  const scale = look === "subtle"
    ? keyframes([{ t: 0, v: 0.94 }, { t: inS, v: 1, ease: easeOutCubic }], local)
    : keyframes([{ t: 0, v: 0.86 }, { t: inS * 0.62, v: 1.035, ease: easeOutCubic }, { t: inS, v: 1, ease: easeOutQuad }], local);
  return { scale, alpha };
}

/** The accent of a variant: the scene's when it reads there, else the variant's own, else the default yellow. */
export function hookKeyColor(accent: string | null | undefined, v: HookVariant): string {
  if (v.accent) return v.accent;
  return hookMarkColor(accent);
}

/** The outline of a stack of centred labels (half-widths `hw`, each `lineH` tall, top at `top`), corners rounded. */
export function stackOutline(hw: readonly number[], lineH: number, top: number, radius: number): number[] {
  const right: [number, number][] = [];
  hw.forEach((w, i) => {
    const y0 = top + i * lineH, y1 = y0 + lineH;
    if (i === 0 || hw[i - 1] !== w) right.push([w, y0]);
    if (i === hw.length - 1 || hw[i + 1] !== w) right.push([w, y1]);
  });
  const poly: [number, number][] = [...right, ...right.slice().reverse().map(([x, y]): [number, number] => [-x, y])];
  const n = poly.length, out: number[] = [];
  const len = (a: [number, number], b: [number, number]) => Math.hypot(a[0] - b[0], a[1] - b[1]);
  for (let i = 0; i < n; i++) {
    const v = poly[i], prev = poly[(i + n - 1) % n], next = poly[(i + 1) % n];
    const dp = len(v, prev), dn = len(v, next);
    const r = Math.min(radius, dp / 2, dn / 2);
    const a: [number, number] = [v[0] + ((prev[0] - v[0]) / dp) * r, v[1] + ((prev[1] - v[1]) / dp) * r];
    const b: [number, number] = [v[0] + ((next[0] - v[0]) / dn) * r, v[1] + ((next[1] - v[1]) / dn) * r];
    // A quarter circle from a to b around the corner (the centre is opposite the vertex).
    const cx = a[0] + b[0] - v[0], cy = a[1] + b[1] - v[1];
    const a0 = Math.atan2(a[1] - cy, a[0] - cx);
    let sweep = Math.atan2(b[1] - cy, b[0] - cx) - a0;
    if (sweep > Math.PI) sweep -= 2 * Math.PI;
    if (sweep < -Math.PI) sweep += 2 * Math.PI;
    const steps = 8;
    for (let k = 0; k <= steps; k++) {
      const ang = a0 + (sweep * k) / steps;
      out.push(round3(cx + r * Math.cos(ang)), round3(cy + r * Math.sin(ang)));
    }
  }
  return out;
}

/** The title's ops at edit time `t` ([] outside its window). */
export function hookOps(p: PreparedScene, t: number, text?: string): DrawOp[] {
  const l = hookLayout(p, text);
  if (!l || t < l.start || t >= l.end) return [];
  const v = l.variant, local = t - l.start, dur = l.end - l.start;
  const { scale, alpha } = hookMotion(l.look, local, dur);
  if (alpha <= 0) return [];
  const transform = placeAt(round3(l.cx), round3(l.cy), l.tilt, scale);
  const lineH = v.lineHeight * l.px;
  const shadow: Shadow = { color: v.shadow.color, blur: v.shadow.blur * l.px, dx: 0, dy: v.shadow.dy * l.px };
  const ops: DrawOp[] = [];
  if (v.style === "stack") {
    const top = l.lines[0].y - lineH / 2;
    ops.push(pathOp({
      points: stackOutline(l.lines.map((line) => line.hw), lineH, top, HOOK.radius * l.px), closed: true, fill: v.fill,
      stroke: null, strokePx: 0, transform, alpha, shadow,
    }));
  }
  const inS = Math.min(HOOK.inSeconds, dur / 3);
  const markStart = Math.min(HOOK.markDelay, inS * 0.77);
  const boxed = new Set<HookWord>();
  if (v.key === "box") {
    for (const line of l.lines) {
      for (const w of line.words) {
        if (w.mark < 0) continue;
        const k = easeOutCubic(clamp01((local - markStart - w.mark * HOOK.markStagger) / HOOK.markSeconds));
        if (k <= 0) continue;
        boxed.add(w);
        const h = HOOK.markHeight * l.px, pad = HOOK.markPadX * l.px;
        ops.push(rectOp({
          x: w.mx - pad, y: line.y - h / 2, w: (w.mw + 2 * pad) * k, h, radius: HOOK.markRadius * l.px, fill: l.mark, transform, alpha,
          ...(v.style === "outline" ? { shadow } : {}),
        }));
      }
    }
  }
  const words = l.lines.flatMap((line) => line.words.map((w) => ({ w, y: line.y })));
  if (v.style === "outline") {
    // Every outline first, then every fill: a neighbour's outline never crosses a letter.
    const strokePx = v.strokeEm * l.px;
    for (const { w, y } of words) {
      if (boxed.has(w)) continue;
      ops.push(textOp({ text: w.text, font: l.family, px: l.px, x: w.x, y, align: "left", fill: null, stroke: v.ink, strokePx, join: "round", shadow, transform, alpha }));
    }
    for (const { w, y } of words) {
      const fill = boxed.has(w) ? v.keyInk : w.mark >= 0 && v.key === "color" ? l.mark : v.fill;
      ops.push(textOp({ text: w.text, font: l.family, px: l.px, x: w.x, y, align: "left", fill, transform, alpha }));
    }
    return ops;
  }
  for (const { w, y } of words) {
    ops.push(textOp({ text: w.text, font: l.family, px: l.px, x: w.x, y, align: "left", fill: v.ink, transform, alpha }));
  }
  return ops;
}

registerGlobal({
  id: "hook",
  layer: "hook",
  active: (p, clock) => !!p.scene.hook && !p.hookDetached && clock.e >= p.scene.hook.start && clock.e < p.scene.hook.end,
  ops: (dc) => hookOps(dc.scene, dc.t),
});
