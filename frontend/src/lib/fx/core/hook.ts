// The hook title of a Dynamic edits clip: the AI-written headline shown for the first seconds, drawn as one white
// rounded card with heavy dark type, a marker box behind the numbers and power words, a quick pop in and a short fade
// out. Drawn here (browser and deno share this file) whenever the drawing stage runs; the ASS hook in
// media/captions.py remains only for the levels without it.
//
// Layout: balanced lines (one for a short title, two preferred, three before the type shrinks), at most 0.86 W wide.
// Placement: one position for the whole window, picked once per scene from the face track, the captions on screen and
// the layout hints, in this order: never over eyes or mouth, never on the captions, top safe area by default (the
// blurred band above a letterboxed picture when there is one), else right above the captions (a stacked two-shot's
// seam), else under them on the chest; never in the bottom 18 % the platforms cover. With `trim`, a title whose best
// position only holds until a camera cut leaves at that cut.
import type { DrawOp } from "./host";
import type { Rect } from "./constants";
import { clamp, clamp01, easeOutCubic, keyframes, easeOutQuad } from "./easing";
import { applyMat, overlapArea, pictureMatrix, placeAt, rectOp, round3, textOp, transformRect } from "./layout";
import { registerGlobal } from "./registry";
import type { PreparedScene } from "./scene";
import { rowCuts, type AccentCue, type FxHookTitle } from "./types";
import { pictureStateAt, type FxClock } from "./frame";
import { lookEmojiPlacement } from "./captions/emoji_drop";
import { activeLook, LOOK_GEOMETRY } from "./captions/looks";

/** Geometry in em of the title size unless stated. */
export const HOOK = {
  sizeU: 65,                 // px at a 1080 short side (0.060 W)
  minSizeU: 48.6,            // never below 0.045 W; only a single word wider than the card shrinks further
  lookSize: { subtle: 0.9, bold: 1, max: 1 },
  lineHeight: 1.18,
  maxWidth: 0.86,            // of W, the whole card
  padX: 0.5, padY: 0.32,
  radius: 0.28,              // × line height
  oneLine: 0.62,             // a title narrower than this share of the text width stays on one line
  markPadX: 0.1, markHeight: 1.1, markRadius: 0.16,
  maxMarks: 2,
  fill: "#FFFFFF", ink: "#0B0B0F", mark: "#FFE45C",
  shadow: { dy: 6, blur: 24, alpha: 0.35 },   // u px
  tiltDeg: { subtle: 0, bold: 0, max: -2 },
  inSeconds: 0.26, outSeconds: 0.2, fadeIn: 0.12,
  markDelay: 0.2, markSeconds: 0.18, markStagger: 0.08,
} as const;

/** Placement, as fractions of the frame height unless stated. */
export const HOOK_PLACE = {
  top: 0.105,                // card top in the top safe area
  topMin: 0.085,             // the platforms cover the top 8 %
  bottomMax: 0.8,            // and the bottom 18 %
  gap: 0.02,                 // between the card and captions
  bandGap: 0.022,            // between the card and a letterboxed picture below it
  minShown: 2.2,             // a trimmed title still shows this long (s)
  minFace: 0.05,             // faces smaller than this share of H are background people
} as const;

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
export interface HookLine { words: HookWord[]; text: string; w: number; y: number }   // y: line centre from the card centre
export interface HookLayout {
  text: string; family: string; px: number; lines: HookLine[];
  /** The card (output px, before tilt and motion). */
  w: number; h: number; cx: number; cy: number;
  start: number; end: number; tilt: number; look: FxHookTitle["look"]; mark: string;
  /** Which rule placed it (tests and debugging). */
  slot: "band" | "top" | "above" | "below" | "free";
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
export function hookBlock(title: string, basePx: number, minPx: number, maxCardW: number, measure: (text: string, px: number) => number): Block | null {
  const tokens = hookTokens(title);
  if (!tokens.length) return null;
  const marks = hookMarks(tokens);
  const at = (px: number) => {
    const m: Measure = (text) => measure(text, px);
    const widths = tokens.map((t) => m(t.text)), space = m(" ");
    const maxText = Math.max(1, maxCardW - 2 * HOOK.padX * px);
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
  const lineH = HOOK.lineHeight * px, count = fit.lines.length;
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
    return { words, text, w: total, y: (li - (count - 1) / 2) * lineH };
  });
  const w = Math.min(maxCardW, Math.max(...lines.map((l) => l.w)) + 2 * HOOK.padX * px);
  return { px, lines, w, h: count * lineH + 2 * HOOK.padY * px };
}

// ----------------------------------------------------------------------------------------------- placement

interface Obstacles {
  /** Per face sample in the window: eyes-to-mouth boxes (never covered) and whole face boxes (better left clear). */
  faces: { t: number; core: Rect[]; box: Rect[] }[];
  /** Captions and their emoji while the title shows. */
  captions: { start: number; end: number; rect: Rect }[];
  /** A letterboxed opening's picture, on screen until `until`: better left uncovered. */
  picture: { rect: Rect; until: number } | null;
}

/** A face row (normalised, output space) as px boxes after the frame's picture transform. */
function faceRects(row: readonly number[], W: number, H: number, m: ReturnType<typeof pictureMatrix>): { core: Rect; box: Rect } | null {
  if (row.length < 4 || row[3] < HOOK_PLACE.minFace) return null;
  const box = transformRect(m, { x: row[0] * W, y: row[1] * H, w: row[2] * W, h: row[3] * H });
  let top = box.y + 0.2 * box.h, bottom = box.y + 0.95 * box.h;
  if (row.length >= 15) {
    const pt = (i: number) => applyMat(m, row[5 + 2 * i] * W, row[6 + 2 * i] * H)[1];
    top = Math.min(pt(0), pt(1)) - 0.14 * box.h;       // eyebrows
    bottom = Math.max(pt(3), pt(4)) + 0.12 * box.h;    // lower lip
  }
  return { core: { x: box.x + 0.04 * box.w, y: top, w: 0.92 * box.w, h: Math.max(1, bottom - top) }, box };
}

function obstacles(p: PreparedScene, hook: FxHookTitle): Obstacles {
  const W = p.width, H = p.height, scene = p.scene;
  const faces: Obstacles["faces"] = [];
  for (const [t, rows] of scene.face_track?.samples ?? []) {
    if (t < hook.start - 0.2 || t > hook.end + 0.2) continue;
    const e = clamp(t, hook.start, Math.max(hook.start, hook.end - 1e-3));
    const clock: FxClock = { e, o: e, frame: Math.round(e * 30) };
    const m = pictureMatrix(pictureStateAt(p, clock, p.faceAt(e)), W, H);
    const core: Rect[] = [], box: Rect[] = [];
    for (const row of rows ?? []) {
      const r = faceRects(row, W, H, m);
      if (r) { core.push(r.core); box.push(r.box); }
    }
    if (core.length) faces.push({ t: e, core, box });
  }
  const captions: Obstacles["captions"] = [];
  const g = LOOK_GEOMETRY;
  for (const chunk of p.chunks) {
    if (chunk.end <= hook.start || chunk.start >= hook.end || !chunk.words.length) continue;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const w of chunk.words) {
      x0 = Math.min(x0, w.x); y0 = Math.min(y0, w.y); x1 = Math.max(x1, w.x + w.w); y1 = Math.max(y1, w.y + w.h);
    }
    const padX = g.pillPadX * chunk.px, padY = Math.max(g.pillPadY, 0.1) * chunk.px;
    captions.push({ start: chunk.start, end: chunk.end, rect: { x: x0 - padX, y: y0 - padY, w: x1 - x0 + 2 * padX, h: y1 - y0 + 2 * padY } });
  }
  if (activeLook(p)) {
    for (const cue of p.cuesByKind.get("accent") ?? []) {
      if (cue.kind !== "accent" || cue.variant !== "emoji" || cue.end <= hook.start || cue.start >= hook.end) continue;
      const at = lookEmojiPlacement(p, cue as AccentCue);
      if (at) captions.push({ start: cue.start, end: at.until ?? cue.end, rect: { x: at.x - at.size / 2, y: at.y - at.size / 2, w: at.size, h: at.size } });
    }
  }
  for (const [top, bottom] of hook.avoid ?? []) {
    if (bottom > top) captions.push({ start: hook.start, end: hook.end, rect: { x: 0, y: top * H, w: W, h: (bottom - top) * H } });
  }
  let picture: Obstacles["picture"] = null;
  if (hook.picture && hook.picture[1] > hook.picture[0]) {
    const cut = rowCuts(scene.caption?.rows).find((c) => c > hook.start);
    picture = { rect: { x: 0, y: hook.picture[0] * H, w: W, h: (hook.picture[1] - hook.picture[0]) * H }, until: cut ?? hook.end };
  }
  return { faces, captions, picture };
}

interface Candidate { slot: HookLayout["slot"]; top: number; prefer: number }

/** How bad a card position is until `end` (0 = clear). */
function cost(card: Rect, o: Obstacles, start: number, end: number, H: number): number {
  let total = 0;
  const faces = o.faces.filter((f) => f.t >= start - 1e-6 && f.t < end);
  if (faces.length) {
    let hits = 0, depth = 0, soft = 0;
    for (const f of faces) {
      const core = f.core.reduce((s, r) => s + overlapArea(card, r) / Math.max(1, r.w * r.h), 0);
      if (core > 0) { hits += 1; depth += Math.min(1, core); }
      soft += f.box.reduce((s, r) => s + overlapArea(card, r), 0) / Math.max(1, card.w * card.h);
    }
    total += (hits ? 1000 + 1000 * (hits / faces.length) + 500 * (depth / faces.length) : 0) + 60 * (soft / faces.length);
  }
  const span = Math.max(1e-3, end - start);
  const gap = HOOK_PLACE.gap * H * 0.6;
  const padded = { x: card.x, y: card.y - gap, w: card.w, h: card.h + 2 * gap };
  for (const c of o.captions) {
    const shared = Math.min(end, c.end) - Math.max(start, c.start);
    if (shared <= 0 || overlapArea(padded, c.rect) <= 0) continue;
    total += 800 + 400 * Math.min(1, shared / span);
  }
  if (o.picture && o.picture.until > start) {
    total += 40 * (overlapArea(card, o.picture.rect) / Math.max(1, card.w * card.h)) * Math.min(1, (Math.min(end, o.picture.until) - start) / span);
  }
  if (card.y + card.h > HOOK_PLACE.bottomMax * H) total += 600 + (card.y + card.h - HOOK_PLACE.bottomMax * H);
  if (card.y < HOOK_PLACE.topMin * H) total += 600 + (HOOK_PLACE.topMin * H - card.y);
  return total;
}

/** The automatic card centre for a block: `{cy, slot, end}` (output px; `end` trimmed to a cut when that frees a better slot). */
export function placeHook(p: PreparedScene, hook: FxHookTitle, block: Pick<Block, "w" | "h">): { cy: number; slot: HookLayout["slot"]; end: number } {
  const W = p.width, H = p.height;
  const o = obstacles(p, hook);
  const x = (W - block.w) / 2;
  const rect = (top: number): Rect => ({ x, y: top, w: block.w, h: block.h });
  const candidates: Candidate[] = [];
  const picture = hook.picture;
  if (picture && picture[0] > 0) {
    const bottom = picture[0] * H - HOOK_PLACE.bandGap * H;
    const top = Math.min(bottom - block.h, Math.max(HOOK_PLACE.top * H, (HOOK_PLACE.topMin * H + bottom - block.h) / 2));
    if (top >= HOOK_PLACE.topMin * H) candidates.push({ slot: "band", top, prefer: 0 });
  }
  // Top safe area: as low as 0.105 H, raised (never past the platform bar) to leave a forehead clear.
  let top = HOOK_PLACE.top * H;
  const heads = o.faces.flatMap((f) => f.box.filter((b) => b.x < x + block.w && b.x + b.w > x).map((b) => b.y));
  if (heads.length) top = clamp(Math.min(...heads) - 0.01 * H - block.h, HOOK_PLACE.topMin * H, top);
  candidates.push({ slot: "top", top, prefer: candidates.length ? 4 : 0 });
  if (o.captions.length) {
    const first = Math.min(...o.captions.map((c) => c.rect.y)), last = Math.max(...o.captions.map((c) => c.rect.y + c.rect.h));
    candidates.push({ slot: "above", top: first - HOOK_PLACE.gap * H - block.h, prefer: 12 });
    candidates.push({ slot: "below", top: last + HOOK_PLACE.gap * H, prefer: 22 });
  }
  for (let y = 0.14; y <= 0.72; y += 0.02) candidates.push({ slot: "free", top: y * H - block.h / 2, prefer: 30 + 20 * y });

  const ends: number[] = [hook.end];
  if (hook.trim) {
    for (const cut of rowCuts(p.scene.caption?.rows)) {
      if (cut - hook.start >= HOOK_PLACE.minShown && cut < hook.end - 0.1) ends.push(cut);
    }
  }
  let best = { cy: candidates[0].top + block.h / 2, slot: candidates[0].slot, end: hook.end }, bestCost = Infinity;
  for (const end of ends) {
    for (const c of candidates) {
      const value = cost(rect(c.top), o, hook.start, end, H) + c.prefer + (end < hook.end ? 8 + 4 * (hook.end - end) : 0);
      if (value < bestCost - 1e-9) { bestCost = value; best = { cy: c.top + block.h / 2, slot: c.slot, end }; }
    }
  }
  return best;
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
  const block = hookBlock(title, HOOK.sizeU * u * HOOK.lookSize[look] * scale, HOOK.minSizeU * u * HOOK.lookSize[look] * scale,
    HOOK.maxWidth * W, (t, px) => (p.measure(t, family, 100) * px) / 100);
  let layout: HookLayout | null = null;
  if (block && hook.end > hook.start) {
    const placed = placeHook(p, hook, block);
    layout = {
      text: title, family, px: block.px, lines: block.lines, w: block.w, h: block.h,
      cx: W * (0.5 + (hook.x ?? 0)), cy: placed.cy + H * (hook.y ?? 0),
      start: hook.start, end: placed.end, tilt: (HOOK.tiltDeg[look] * Math.PI) / 180, look, mark: hookMarkColor(hook.accent),
      slot: placed.slot,
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

/** The title's ops at edit time `t` ([] outside its window). */
export function hookOps(p: PreparedScene, t: number, text?: string): DrawOp[] {
  const l = hookLayout(p, text);
  if (!l || t < l.start || t >= l.end) return [];
  const u = p.u, local = t - l.start, dur = l.end - l.start;
  const { scale, alpha } = hookMotion(l.look, local, dur);
  if (alpha <= 0) return [];
  const transform = placeAt(round3(l.cx), round3(l.cy), l.tilt, scale);
  const lineH = HOOK.lineHeight * l.px;
  const ops: DrawOp[] = [rectOp({
    x: -l.w / 2, y: -l.h / 2, w: l.w, h: l.h, radius: Math.min(HOOK.radius * lineH, l.h / 2), fill: HOOK.fill, transform, alpha,
    shadow: { color: `rgba(0,0,0,${HOOK.shadow.alpha})`, blur: HOOK.shadow.blur * u, dx: 0, dy: HOOK.shadow.dy * u },
  })];
  const inS = Math.min(HOOK.inSeconds, dur / 3);
  const markStart = Math.min(HOOK.markDelay, inS * 0.77);
  for (const line of l.lines) {
    for (const w of line.words) {
      if (w.mark < 0) continue;
      const k = easeOutCubic(clamp01((local - markStart - w.mark * HOOK.markStagger) / HOOK.markSeconds));
      if (k <= 0) continue;
      const h = HOOK.markHeight * l.px, pad = HOOK.markPadX * l.px;
      ops.push(rectOp({ x: w.mx - pad, y: line.y - h / 2, w: (w.mw + 2 * pad) * k, h, radius: HOOK.markRadius * l.px, fill: l.mark, transform, alpha }));
    }
  }
  for (const line of l.lines) {
    for (const w of line.words) {
      ops.push(textOp({ text: w.text, font: l.family, px: l.px, x: w.x, y: line.y, align: "left", fill: HOOK.ink, transform, alpha }));
    }
  }
  return ops;
}

registerGlobal({
  id: "hook",
  layer: "hook",
  active: (p, clock) => !!p.scene.hook && !p.hookDetached && clock.e >= p.scene.hook.start && clock.e < p.scene.hook.end,
  ops: (dc) => hookOps(dc.scene, dc.t),
});
