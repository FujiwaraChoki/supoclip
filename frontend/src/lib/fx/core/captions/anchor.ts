// Where a caption accent lands (WP7): the on-screen box of the accent's key word(s) in the FX caption layout, and the
// chunk's entrance transform at t, so marks drawn around a word (circle, underline, strike, arrow) and things launched
// from it (emoji, bursts) move with the caption they belong to. Pure; memoised per prepared scene and cue.
//
// Matching: the cue's text tokens (lower-cased letters and digits) against the laid-out words of the chunks that
// overlap the cue, starting at a word that starts at the cue (±60 ms). Without a token match the word nearest the cue
// start (≤ 350 ms) is used. With no FX captions on screen at all (captions disabled) the accent gets a standalone
// keyword at the caption position instead, so the mark still has something to point at.
import type { DrawOp, Mat2D } from "../host";
import type { CaptionChunk } from "../registry";
import type { PreparedScene } from "../scene";
import type { AccentCue, FxCaptionTemplate } from "../types";
import { captionSafe, type Rect } from "../constants";
import { clamp, progress } from "../easing";
import { IDENTITY, isIdentity, mul, scaleAbout, textOp, translation } from "../layout";
import { BASE_LOOKS, DEFAULT_BASE_LOOK, entranceAt } from "./base";

/** Lower-cased letters and digits only ("300%" → "300", "Didn't" → "didnt"); unicode-aware. */
export const normWord = (text: string): string => text.toLocaleLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
/** The cue text as comparable tokens (empty tokens dropped). */
export const cueTokens = (text: string): string[] => text.split(/\s+/).map(normWord).filter(Boolean);

/** Reference size for width measurements: text width is linear in px, so measure once and scale. */
export const REF_PX = 100;
/** Advance width of `text` at `px`, via one memoised measurement at REF_PX. */
export const widthAt = (p: PreparedScene, text: string, family: string, px: number): number =>
  (p.measure(text, family, REF_PX) * px) / REF_PX;

export interface WordAnchor {
  /** Union of the key words' boxes (output px, before `transform`). */
  box: Rect;
  /** The caption chunk's entrance at t (scale about the line centre + rise); identity for a standalone keyword. */
  transform: Mat2D;
  /** Caption entrance opacity at t. */
  alpha: number;
  /** The chunk holding the key word; null for a standalone keyword. */
  chunk: CaptionChunk | null;
  /** Caption font size of the key word (px). */
  px: number;
  /** Ops that draw the standalone keyword (empty when the word is part of an on-screen caption). */
  textOps: DrawOp[];
}

interface Match { chunk: CaptionChunk; first: number; last: number }

const matches = new WeakMap<PreparedScene, WeakMap<AccentCue, Match | null>>();

/** The chunk and word range the cue marks, or null (memoised per scene and cue object). */
export function matchCue(p: PreparedScene, cue: AccentCue): Match | null {
  let byCue = matches.get(p);
  if (!byCue) { byCue = new WeakMap(); matches.set(p, byCue); }
  if (byCue.has(cue)) return byCue.get(cue)!;
  const found = findMatch(p, cue);
  byCue.set(cue, found);
  return found;
}

function findMatch(p: PreparedScene, cue: AccentCue): Match | null {
  const tokens = cueTokens(cue.text);
  const overlapping = p.chunks.filter((c) => c.end > cue.start - 0.06 && c.start <= Math.max(cue.end, cue.start + 0.06));
  // 1. A token run starting at a word that starts with the cue.
  if (tokens.length) {
    let best: Match | null = null, bestDelta = Infinity;
    for (const chunk of overlapping) {
      for (let i = 0; i < chunk.words.length; i++) {
        const w = chunk.words[i];
        if (w.start < cue.start - 0.06 || w.start > Math.max(cue.end, cue.start + 0.06)) continue;
        if (normWord(w.text) !== tokens[0]) continue;
        let last = i;
        for (let k = 1; k < tokens.length && last + 1 < chunk.words.length; k++) {
          if (normWord(chunk.words[last + 1].text) !== tokens[k]) break;
          last += 1;
        }
        const delta = Math.abs(w.start - cue.start);
        if (delta < bestDelta) { best = { chunk, first: i, last }; bestDelta = delta; }
      }
    }
    if (best) return best;
  }
  // 2. The word nearest the cue start.
  let best: Match | null = null, bestDelta = 0.35;
  for (const chunk of overlapping) {
    for (let i = 0; i < chunk.words.length; i++) {
      const delta = Math.abs(chunk.words[i].start - cue.start);
      if (delta < bestDelta || (delta === bestDelta && !best)) { best = { chunk, first: i, last: i }; bestDelta = delta; }
    }
  }
  return best;
}

/** Bounds of words [first, last] of a chunk. */
export function wordsBox(chunk: CaptionChunk, first: number, last: number): Rect {
  let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
  for (let i = first; i <= last; i++) {
    const w = chunk.words[i];
    x0 = Math.min(x0, w.x); y0 = Math.min(y0, w.y); x1 = Math.max(x1, w.x + w.w); y1 = Math.max(y1, w.y + w.h);
  }
  return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
}

/** Bounds of a whole chunk (all its lines). */
export const chunkBox = (chunk: CaptionChunk): Rect => wordsBox(chunk, 0, chunk.words.length - 1);

/** The entrance transform and opacity the template's line drawer applies to `chunk` at edit time t. */
export function chunkEntrance(p: PreparedScene, chunk: CaptionChunk, t: number): { transform: Mat2D; alpha: number } {
  if (chunk.space !== undefined) return { transform: IDENTITY, alpha: 1 };   // look captions: 0-frame entrances
  const look = BASE_LOOKS[p.scene.template as FxCaptionTemplate] ?? DEFAULT_BASE_LOOK;
  const ent = entranceAt(look.entrance, t - chunk.start, p.u);
  const transform = mul(translation(0, ent.dy), ent.scale === 1 ? IDENTITY : scaleAbout(ent.scale, chunk.centerX, chunk.centerY));
  return { transform: isIdentity(transform) ? IDENTITY : transform, alpha: ent.alpha };
}

/**
 * Where the accent sits at edit time t: the key word in the caption on screen, or a standalone keyword when the scene
 * draws no FX captions. null when the key word's caption is not on screen at t (the mark waits for, or leaves with,
 * its word) or the cue has no text to show.
 */
export function anchorAt(p: PreparedScene, cue: AccentCue, t: number, withText = true): WordAnchor | null {
  if (p.chunks.length) {
    const m = matchCue(p, cue);
    if (!m || p.chunkAt(t) !== m.chunk) return null;
    const { transform, alpha } = chunkEntrance(p, m.chunk, t);
    return { box: wordsBox(m.chunk, m.first, m.last), transform, alpha, chunk: m.chunk, px: m.chunk.px, textOps: [] };
  }
  return standaloneAnchor(p, cue, t, withText);
}

/** The cue's keyword drawn like a caption (white, black outline, soft shadow) at the caption position. */
export function standaloneAnchor(p: PreparedScene, cue: AccentCue, t: number, withText = true): WordAnchor | null {
  const style = p.scene.caption;
  const label = style?.uppercase ? cue.text.trim().toLocaleUpperCase() : cue.text.trim();
  const W = p.width, H = p.height, u = p.u;
  const family = p.family(style?.font || "TikTokSans-Bold");
  let px = Math.max(34 * u, style?.size_px ?? 78 * u);
  const maxW = Math.max(1, W - 120 * u);
  const width0 = label ? widthAt(p, label, family, px) : px;
  if (width0 > maxW) px *= maxW / width0;
  const w = label ? widthAt(p, label, family, px) : px;
  const safe = captionSafe(W, H);
  const cy = clamp(H * (p.captionYAt(cue.start) ?? style?.y ?? 0.7), safe.top + px / 2, Math.max(safe.top + px / 2, H - safe.bottom - px / 2));
  const box = { x: W / 2 - w / 2, y: cy - px / 2, w, h: px };
  // Same settle entrance as a caption line, timed from the cue.
  const ent = entranceAt("settle", t - cue.start, u);
  const transform = ent.scale === 1 ? IDENTITY : scaleAbout(ent.scale, W / 2, cy);
  const alpha = progress(t, cue.start - 1e-9, cue.start + 0.04);
  const textOps: DrawOp[] = [];
  if (withText && label) {
    const at = { text: label, font: family, px, x: W / 2, y: cy, align: "center" as const, transform, alpha };
    textOps.push(textOp({ ...at, fill: null, stroke: "#000000", strokePx: 0.12 * px,
      shadow: { color: "rgba(0,0,0,0.6)", blur: 12 * u, dx: 0, dy: 4 * u } }));
    textOps.push(textOp({ ...at, fill: "#FFFFFF" }));
  }
  return { box, transform, alpha, chunk: null, px, textOps };
}

/** The caption accent colour of the scene (the template's highlight colour). */
export const accentColor = (p: PreparedScene): string => p.scene.caption?.accent || "#FFD23F";
