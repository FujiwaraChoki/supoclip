// Scene preparation (§4.3): everything frameOps() needs per render, computed once and pure given `measure`.
// Sorts and indexes cues, builds the face-track interpolation table, chunks and lays out FX-template captions in
// u units (words per line, wrap at W − 120u, studio placements by caption_key) and formats counter cues.
import { captionSafe, unit } from "./constants";
import type { CaptionChunk } from "./registry";
import { clamp } from "./easing";
import { captionRowY, isFxTemplate, rowCuts, type AccentCue, type FxCaptionStyle, type FxCaptionTemplate, type FxCue, type FxKind, type FxScene, type FxWord, type MatteWindow } from "./types";
import { warpFromJson, type Warp } from "./warp";
import type { FxHost } from "./host";
import { BASE_LOOKS, DEFAULT_BASE_LOOK, outlineGap } from "./captions/base";
import { groupedWords, LOOK_LEAD, phraseGroups } from "./captions/groups";
import { LOOK_GEOMETRY, LOOK_MOTION, keywordBoxed, stripEdgePunct, type ActiveLook } from "./captions/looks";
import { cueTokens, normWord } from "./captions/anchor";

/** A face box in OUTPUT px (before this frame's picture transform). landmarks = [rex,rey, lex,ley, nx,ny, rmx,rmy,
 *  lmx,lmy] in output px (YuNet order), or null when the track has none at t. */
export interface FaceBox { x: number; y: number; w: number; h: number; landmarks: number[] | null }

export interface CounterFormat {
  target: number; decimals: number; prefix: string; suffix: string;
  /** "$1,250.5"-style text for a value: thousands separators, the spoken number's decimals, prefix and suffix. */
  format(value: number): string;
}

export interface SceneMeasureHost extends Pick<FxHost, "fontFamily"> { measure(text: string, font: string, px: number): number }

export interface PreparedScene {
  readonly scene: FxScene;
  readonly width: number;
  readonly height: number;
  readonly u: number;                                    // min(W, H) / 1080
  readonly warp: Warp;
  readonly outFrames: number;                            // round(out_duration · 30)
  readonly cues: readonly FxCue[];                       // sorted by (start, id)
  readonly cuesByKind: ReadonlyMap<FxKind, readonly FxCue[]>;
  readonly captionFamily: string | null;                 // CSS family of the caption font (FX templates only)
  readonly chunks: readonly CaptionChunk[];              // FX-template caption chunks, in time order
  readonly counters: ReadonlyMap<string, CounterFormat>; // accent counter cue id → format
  /** CSS family for a bundled font stem (host.fontFamily). */
  family(stem: string): string;
  /** Advance width in px of `text` in `family` at `px` (memoised host measure; fontCss convention). */
  measure(text: string, family: string, px: number): number;
  /** Interpolated largest face at edit time t, output px, before the picture transform. */
  faceAt(t: number): FaceBox | null;
  /** The caption chunk on screen at edit time t. */
  chunkAt(t: number): CaptionChunk | null;
  /** The automatic caption centre (fraction of H) at edit time t: the caption row in effect (per-shot rows), else the
   *  style's y; null without FX captions. What overlays anchored to "the caption position" use when no chunk shows. */
  captionYAt(t: number): number | null;
  hookActive(t: number): boolean;
  /** The hook title is drawn apart from the frame (the studio's own text layer): frameOps leaves it out. */
  readonly hookDetached: boolean;
  inBroll(t: number): boolean;
  matteAt(t: number): MatteWindow | null;
}

/** Captions bridge gaps shorter than this to the next chunk instead of blinking off. */
export const CAPTION_BRIDGE = 0.3;
/** Face samples further apart than this are not interpolated across (the face was lost in between). */
export const FACE_MAX_GAP = 0.5;

const cueOrder = (a: FxCue, b: FxCue) => a.start - b.start || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0);

export function prepareScene(scene: FxScene, host: SceneMeasureHost, options: { detachHook?: boolean } = {}): PreparedScene {
  const width = scene.width, height = scene.height, u = unit(width, height);
  const warp = warpFromJson(scene.warp, scene.edit_duration);
  const cues = [...scene.cues].sort(cueOrder);
  const cuesByKind = new Map<FxKind, FxCue[]>();
  for (const c of cues) {
    const list = cuesByKind.get(c.kind) ?? [];
    list.push(c);
    cuesByKind.set(c.kind, list);
  }

  const measured = new Map<string, number>();
  const measure = (text: string, family: string, px: number) => {
    const key = `${family}\u0000${px}\u0000${text}`;
    let v = measured.get(key);
    if (v === undefined) {
      v = host.measure(text, family, px);
      measured.set(key, v);
    }
    return v;
  };
  const family = (stem: string) => host.fontFamily(stem);

  const style = scene.caption;
  const captionsDrawn = !!style && style.enabled && isFxTemplate(scene.template);
  const captionFamily = captionsDrawn ? family(style!.font) : null;
  const chunks = captionsDrawn ? layoutCaptions(scene, captionFamily!, measure) : [];

  const counters = new Map<string, CounterFormat>();
  for (const c of cues) {
    if (c.kind === "accent" && c.variant === "counter") counters.set(c.id, counterFormat(c));
  }

  const faceAt = faceInterpolator(scene);
  const hook = scene.hook_window;
  const broll = [...scene.broll_windows].sort((a, b) => a[0] - b[0]);
  const mattes = [...scene.matte_windows].sort((a, b) => a.start - b.start);

  return {
    scene, width, height, u, warp, cues, cuesByKind, captionFamily, chunks, counters,
    outFrames: Math.round(scene.out_duration * 30),
    hookDetached: !!options.detachHook,
    family, measure, faceAt,
    chunkAt: (t) => findSpan(chunks, t),
    captionYAt: (t) => (style ? captionRowY(style.rows, style.y, t) : null),
    hookActive: (t) => !!hook && t >= hook[0] && t < hook[1],
    inBroll: (t) => broll.some(([a, b]) => t >= a && t < b),
    matteAt: (t) => mattes.find((m) => t >= m.start && t < m.end) ?? null,
  };
}

function findSpan<T extends { start: number; end: number }>(items: readonly T[], t: number): T | null {
  let lo = 0, hi = items.length - 1, hit = -1;
  while (lo <= hi) {   // last item with start <= t
    const mid = (lo + hi) >> 1;
    if (items[mid].start <= t) { hit = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return hit >= 0 && t < items[hit].end ? items[hit] : null;
}

// ----------------------------------------------------------------------------------------------- captions

type Measure = (text: string, family: string, px: number) => number;

/** Studio caption_key: JSON.stringify([run, firstWordId]) (document.ts captionKey). */
export const captionKey = (first: Pick<FxWord, "run" | "id">): string => JSON.stringify([first.run, first.id]);

/**
 * Chunks words exactly like the studio's captionGroups (fixed `words_per_line` groups over the whole word list) and
 * lays each chunk out: greedy wrap at W − 120u (render.ts wrapCaptionWords), lines 1.2 px apart, centred on the
 * caption position. Placements (studio caption transforms) offset the centre by (x·W, y·H) and scale the size; without
 * one the block is kept inside the safe area (top max(40u, 5 %), bottom max(120u, 10 %)) like the ASS renderer.
 */
export function layoutCaptions(scene: FxScene, family: string, measure: Measure): CaptionChunk[] {
  const style = scene.caption!;
  const captionLook = style.look;
  if (captionLook === "subtle" || captionLook === "bold" || captionLook === "max") {
    return layoutLookCaptions(scene, captionLook, family, measure);
  }
  const W = scene.width, H = scene.height, u = unit(W, H);
  const per = clamp(Math.round(style.words_per_line) || 1, 1, 8);
  // Group exactly like captionGroups (every word counts, so keys and boundaries match the studio's), then drop
  // words with no text from the layout; a group with nothing to show is skipped.
  const words = [...scene.words].sort((a, b) => a.start - b.start);
  const placements = new Map(style.placements.map((p) => [p.key, p]));
  const safe = captionSafe(W, H);
  // Letter spacing the template's line drawer adds (canvas letterSpacing), so words never collide.
  const look = BASE_LOOKS[scene.template as FxCaptionTemplate] ?? DEFAULT_BASE_LOOK;
  const tracking = look.tracking;
  const groups: { key: string; words: FxWord[] }[] = [];
  for (let i = 0; i < words.length; i += per) {
    const all = words.slice(i, i + per);
    const shown = all.filter((w) => w.text.trim() !== "");
    if (shown.length) groups.push({ key: captionKey(all[0]), words: shown });
  }

  const chunks = groups.map(({ key, words: group }): CaptionChunk => {
    const placement = placements.get(key);
    const scale = placement?.scale ?? 1;
    const label = (t: string) => (style.uppercase ? t.toLocaleUpperCase() : t);
    const maxWidth = Math.max(1, W - 120 * u);
    const wrap = (size: number) => {
      // A word gap is the font's space plus the outlines on both sides of it (thick outlines would close it up).
      const space = measure(" ", family, size) + outlineGap(style, look, size);
      const widths = group.map((w) => {
        const text = label(w.text);
        return measure(text, family, size) + tracking * size * Math.max(0, Array.from(text).length - 1);
      });
      const lines: number[][] = [];
      let line: number[] = [], length = 0;
      group.forEach((_, i) => {
        if (line.length && length + space + widths[i] > maxWidth) { lines.push(line); line = []; length = 0; }
        length += (line.length ? space : 0) + widths[i];
        line.push(i);
      });
      if (line.length) lines.push(line);
      const widest = Math.max(...lines.map((l) => l.reduce((sum, i) => sum + widths[i], 0) + space * (l.length - 1)));
      return { space, widths, lines, widest };
    };
    let px = Math.max(34 * u, style.size_px) * scale;
    let fit = wrap(px);
    // A single word wider than the frame: shrink the whole chunk until its widest line fits (like the ASS renderer).
    for (let pass = 0; pass < 3 && fit.widest > maxWidth; pass++) {
      px *= maxWidth / fit.widest;
      fit = wrap(px);
    }
    const { space, widths, lines } = fit;
    const lineH = px * 1.2, blockH = lineH * lines.length;
    const centerX = W * (0.5 + (style.x ?? 0) + (placement?.x ?? 0));
    const row = placement ? null : groupRow(style, group[0].start);
    let centerY = H * ((row ?? style.y) + (placement?.y ?? 0));
    if (!placement) centerY = clamp(centerY, safe.top + blockH / 2, Math.max(safe.top + blockH / 2, H - safe.bottom - blockH / 2));
    const laid: CaptionChunk["words"] = [];
    lines.forEach((idx, li) => {
      const total = idx.reduce((s, i) => s + widths[i], 0) + space * (idx.length - 1);
      const yc = centerY + (li - (lines.length - 1) / 2) * lineH;
      let x = centerX - total / 2;
      for (const i of idx) {
        laid.push({ ...group[i], text: label(group[i].text), x, y: yc - px / 2, w: widths[i], h: px, line: li });
        x += widths[i] + space;
      }
    });
    return { key, words: laid, start: group[0].start, end: Math.max(group[0].start, group[group.length - 1].end), px, centerX, centerY, scale,
      ...(row !== null ? { row } : {}) };
  });
  // Bridge short gaps so a caption does not blink off between chunks; never overlap the next one.
  for (let i = 0; i < chunks.length; i++) {
    const next = chunks[i + 1];
    if (!next) continue;
    if (next.start - chunks[i].end < CAPTION_BRIDGE) chunks[i].end = Math.max(chunks[i].start, next.start);
    else chunks[i].end = Math.min(chunks[i].end, next.start);
  }
  return chunks.filter((c) => c.end > c.start);
}

/**
 * Look captions (captions/looks.ts): phrase groups (captions/groups.ts) of up to `words_per_line` words, shown at
 * their onset + the look's lead (each word at its own onset with word reveal), one line unless the group is wider than
 * 0.80 W at 0.8× size. Keywords (the resolver's `highlight` accents) are measured in capitals and, for the
 * highlighter, with the box padding, so the line around them never moves when they appear. Bold and max strip edge
 * punctuation. Placements work as for template captions (keyed by the group's first word).
 */
export function layoutLookCaptions(scene: FxScene, look: ActiveLook, family: string, measure: Measure): CaptionChunk[] {
  const style = scene.caption!;
  const motion = LOOK_MOTION[look], g = LOOK_GEOMETRY;
  const W = scene.width, H = scene.height, u = unit(W, H);
  const lead = LOOK_LEAD[look];
  const words = groupedWords(scene.words);
  // Interview rows: each camera cut (a row change) ends the group on screen; no rows, no cuts, groups as always.
  const groups = phraseGroups(words, style.words_per_line, rowCuts(style.rows));
  const placements = new Map(style.placements.map((p) => [p.key, p]));
  const safe = captionSafe(W, H);
  const boxed = keywordBoxed(scene.template);
  const highlights = scene.cues.filter((c): c is AccentCue => c.kind === "accent" && c.variant === "highlight");
  const isKeyword = (w: FxWord) => highlights.some((c) => Math.abs(c.start - w.start) <= 0.06
    && (cueTokens(c.text)[0] ?? "") === normWord(w.text));
  const label = (w: FxWord, kw: boolean) => {
    let text = motion.stripPunct ? stripEdgePunct(w.text.trim()) : w.text.trim();
    if (style.uppercase || (kw && (motion.keywordCaps || boxed))) text = text.toLocaleUpperCase();
    return text;
  };
  const chunks: CaptionChunk[] = [];
  for (const group of groups) {
    const members = words.slice(group.first, group.last + 1);
    const key = captionKey(members[0]);
    const placement = placements.get(key);
    const scale = placement?.scale ?? 1;
    const kws = members.map(isKeyword);
    const texts = members.map((w, i) => label(w, kws[i]));
    const base = Math.max(30 * u, style.size_px) * scale;
    const maxWidth = Math.max(1, g.maxWidth * W);
    const fit = (size: number, wrapAt: number) => {
      const space = measure(" ", family, size) + (style.stroke_color ? (style.stroke_px > 0 ? style.stroke_px : 0.09 * size) : 0);
      const widths = texts.map((t, i) => measure(t, family, size) + (kws[i] && boxed ? 2 * g.boxPadX * size : 0));
      const lines: number[][] = [];
      let line: number[] = [], length = 0;
      widths.forEach((width, i) => {
        if (line.length && length + space + width > wrapAt) { lines.push(line); line = []; length = 0; }
        length += (line.length ? space : 0) + width;
        line.push(i);
      });
      if (line.length) lines.push(line);
      const widest = Math.max(...lines.map((l) => l.reduce((sum, i) => sum + widths[i], 0) + space * (l.length - 1)));
      return { space, widths, lines, widest };
    };
    let px = base;
    let laid = fit(px, Infinity);
    if (laid.widest > maxWidth) {
      px = Math.max(base * g.minShrink, (px * maxWidth) / laid.widest);
      laid = fit(px, maxWidth);
      for (let pass = 0; pass < 3 && laid.widest > W - 2 * CAPTION_SIDE * W; pass++) {
        px *= (W - 2 * CAPTION_SIDE * W) / laid.widest;
        laid = fit(px, maxWidth);
      }
    }
    const { space, widths, lines } = laid;
    const lineH = px * 1.25, blockH = lineH * lines.length;
    const centerX = W * (0.5 + (style.x ?? 0) + (placement?.x ?? 0));
    const row = placement ? null : groupRow(style, group.cut ?? group.start);
    let centerY = H * ((row ?? style.y) + (placement?.y ?? 0));
    if (!placement) centerY = clamp(centerY, safe.top + blockH / 2, Math.max(safe.top + blockH / 2, H - safe.bottom - blockH / 2));
    // A group opening at a camera cut shows from the cut (on the new shot's row), never a few frames before it.
    const start = group.cut !== undefined ? Math.max(group.start + lead, group.cut) : group.start + lead;
    const out: CaptionChunk["words"] = [];
    lines.forEach((idx, li) => {
      const total = idx.reduce((sum, i) => sum + widths[i], 0) + space * (idx.length - 1);
      const yc = centerY + (li - (lines.length - 1) / 2) * lineH;
      let x = centerX - total / 2;
      for (const i of idx) {
        const w = members[i];
        const shown = motion.reveal === "word" ? Math.max(start, w.start + lead) : start;
        out.push({ ...w, text: texts[i], x, y: yc - px / 2, w: widths[i], h: px, line: li, shown, kw: kws[i] });
        x += widths[i] + space;
      }
    });
    // A group still on screen at a cut leaves exactly at the cut.
    const end = group.until !== undefined ? group.until : group.end + lead;
    chunks.push({ key, words: out, start, end: Math.max(start, end), px, centerX, centerY, scale, space,
      ...(row !== null ? { row } : {}) });
  }
  for (let i = 0; i + 1 < chunks.length; i++) chunks[i].end = Math.min(chunks[i].end, chunks[i + 1].start);
  return chunks.filter((c) => c.end > c.start);
}

/**
 * The per-shot row of a caption group without a placement: the row in effect where it starts showing (look groups:
 * the cut they open at, else their first word's onset; groups never span a cut, phraseGroups ends them there). Null
 * without rows (the group sits at the style's y).
 */
export function groupRow(style: Pick<FxCaptionStyle, "rows" | "y">, shownFrom: number): number | null {
  return style.rows?.length ? captionRowY(style.rows, style.y, shownFrom) : null;
}

/** Side margin (fraction of W) a caption line never crosses. */
const CAPTION_SIDE = 0.06;

// ----------------------------------------------------------------------------------------------- counters

/** Number of decimals the spoken number had (2.5 → 1), capped at 2. */
export function decimalsOf(value: number): number {
  if (!Number.isFinite(value) || Number.isInteger(value)) return 0;
  const text = Math.abs(value).toFixed(6).replace(/0+$/, "");
  return Math.min(2, text.length - text.indexOf(".") - 1);
}

/** Thousands separators with a fixed number of decimals, locale-free (identical in Chrome and deno). */
export function formatNumber(value: number, decimals: number): string {
  const fixed = Math.abs(value).toFixed(decimals);
  const [int, frac] = fixed.split(".");
  const grouped = int.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${value < 0 && Number(fixed) !== 0 ? "-" : ""}${grouped}${frac ? `.${frac}` : ""}`;
}

export function counterFormat(cue: AccentCue): CounterFormat {
  let target = cue.value;
  if (target === null || !Number.isFinite(target)) {
    const digits = cue.text.replace(/,/g, "").match(/-?\d+(\.\d+)?/);
    target = digits ? Number(digits[0]) : 0;
  }
  // The spoken number's decimals: from the cue text when it shows the same number ("$2.50" keeps 2), else the value's.
  const shown = cue.text.replace(/,/g, "").match(/\d+(\.\d+)?/);
  const decimals = shown && Math.abs(Number(shown[0]) - Math.abs(target)) < 1e-9
    ? Math.min(2, shown[1] ? shown[1].length - 1 : 0)
    : decimalsOf(target);
  const value = target;
  return {
    target: value, decimals, prefix: cue.prefix, suffix: cue.suffix,
    format: (v: number) => `${cue.prefix}${formatNumber(v, decimals)}${cue.suffix}`,
  };
}

// ----------------------------------------------------------------------------------------------- face track

/**
 * Face track → FaceBox at t (largest face only). Linear between samples ≤ FACE_MAX_GAP apart, nearest sample when one
 * side has no face, holds the first/last face for up to FACE_MAX_GAP beyond the ends, null across longer gaps.
 * Rows are normalised to the output frame (scene face_track space "output").
 */
export function faceInterpolator(scene: Pick<FxScene, "face_track" | "width" | "height">): (t: number) => FaceBox | null {
  const track = scene.face_track;
  if (!track || !track.samples.length) return () => null;
  const W = scene.width, H = scene.height;
  const samples = [...track.samples].sort((a, b) => a[0] - b[0]);
  const times = samples.map((s) => s[0]);
  const faces = samples.map((s) => (s[1] && s[1].length && s[1][0].length >= 4 ? s[1][0] : null));
  const halfStep = 0.5 / (track.fps > 0 ? track.fps : 6);
  const toBox = (row: number[]): FaceBox => ({
    x: row[0] * W, y: row[1] * H, w: row[2] * W, h: row[3] * H,
    landmarks: row.length >= 15 ? row.slice(5, 15).map((v, i) => (i % 2 === 0 ? v * W : v * H)) : null,
  });
  const lerpRow = (a: number[], b: number[], k: number): number[] => {
    const n = a.length >= 15 && b.length >= 15 ? 15 : 5;
    const out: number[] = [];
    for (let i = 0; i < n; i++) out.push(a[i] + (b[i] - a[i]) * k);
    return out;
  };
  return (t: number) => {
    let lo = 0, hi = times.length - 1, i = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (times[mid] <= t) { i = mid; lo = mid + 1; } else hi = mid - 1;
    }
    if (i < 0) {
      const first = faces[0];
      return first && times[0] - t <= FACE_MAX_GAP ? toBox(first) : null;
    }
    if (i === times.length - 1) {
      const last = faces[i];
      return last && t - times[i] <= FACE_MAX_GAP ? toBox(last) : null;
    }
    const ta = times[i], tb = times[i + 1], fa = faces[i], fb = faces[i + 1];
    const gap = tb - ta;
    if (fa && fb && gap <= FACE_MAX_GAP) return toBox(lerpRow(fa, fb, gap > 0 ? (t - ta) / gap : 0));
    const nearA = t - ta <= tb - t;
    const near = nearA ? fa : fb, dist = nearA ? t - ta : tb - t;
    if (!near) return null;
    if (gap > FACE_MAX_GAP && dist > halfStep) return null;
    return toBox(near);
  };
}
