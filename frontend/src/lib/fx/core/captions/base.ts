// Base captions for FX templates: one shared line drawer, tuned per template by a small set of look params.
// The resolved style (font, size, colours, stroke, shadow, box, uppercase) comes from the scene's FxCaptionStyle
// (edit_fx/caption_style.py); the params below add the motion a template's base captions have (§11.1 column 2).
// Template files (WP7/WP8) register a CaptionStyleDrawer that calls baseCaptionOps with their look, or their own.
import type { DrawOp, Shadow } from "../host";
import type { CaptionChunk, DrawerContext } from "../registry";
import type { FxCaptionTemplate } from "../types";
import { easeOutCubic, easeOutQuad, keyframes, progress } from "../easing";
import { IDENTITY, mul, rectOp, scaleAbout, textOp, translation } from "../layout";

export interface BaseLook {
  /** Line entrance: settle 0.92 → 1 in 140 ms (karaoke word pop); pop 0.88 → 1.06 → 1 over 250 ms; rise 14u up + fade
   *  in over 160 ms; none. */
  entrance: "none" | "settle" | "pop" | "rise";
  /** Spoken word: accent colour (karaoke), accent colour + 1.08 scale about the word, or no tracking. */
  active: "accent" | "accent-pop" | "none";
  /** Words flagged `highlight` (emphasis) take the accent colour. */
  emphasis: boolean;
  /** Shadow under the line: soft (blurred, 4u down), hard (0 blur, 6u down) or none; applied only when the style asks
   *  for a shadow or has no stroke and no box (so text never sits naked on the picture). */
  shadow: "soft" | "hard" | "none";
  /** Outline width as a fraction of px when the style names a stroke colour but no width. */
  strokeEm: number;
  /** Letter spacing in em. */
  tracking: number;
}

export const DEFAULT_BASE_LOOK: BaseLook = { entrance: "settle", active: "accent", emphasis: true, shadow: "soft", strokeEm: 0.12, tracking: 0 };

/** §11.1 base-caption column, as motion params. */
export const BASE_LOOKS: Record<FxCaptionTemplate, BaseLook> = {
  marker_circle: { entrance: "settle", active: "accent", emphasis: false, shadow: "soft", strokeEm: 0.12, tracking: 0 },
  emoji_drop: { entrance: "settle", active: "accent-pop", emphasis: false, shadow: "soft", strokeEm: 0.1, tracking: 0 },
  slam: { entrance: "pop", active: "none", emphasis: true, shadow: "hard", strokeEm: 0.1, tracking: 0.01 },
  behind_head: { entrance: "rise", active: "none", emphasis: true, shadow: "soft", strokeEm: 0, tracking: 0 },
  stat_counter: { entrance: "rise", active: "none", emphasis: true, shadow: "soft", strokeEm: 0, tracking: 0 },
  kinetic_stack: { entrance: "pop", active: "none", emphasis: true, shadow: "hard", strokeEm: 0.1, tracking: 0.01 },
  decode_glitch: { entrance: "none", active: "none", emphasis: true, shadow: "soft", strokeEm: 0.1, tracking: 0.04 },
  highlighter_swipe: { entrance: "rise", active: "none", emphasis: false, shadow: "none", strokeEm: 0, tracking: 0 },
  face_doodles: { entrance: "settle", active: "accent-pop", emphasis: false, shadow: "soft", strokeEm: 0.12, tracking: 0 },
};

/** Accent-pop size of the spoken word (D11): 1 → 1.06 over 80 ms, then held. */
export const ACCENT_POP = 1.06;
export const popScale = (local: number): number =>
  keyframes([{ t: 0, v: 1 }, { t: 0.08, v: ACCENT_POP, ease: easeOutQuad }], local);

/** Extra word gap (px) for outlined captions: the outlines on both sides of a gap eat 2 × stroke of the font's space,
 *  so give one stroke back (the gap reads like the font's own, and outlines of neighbouring words never merge). Used by
 *  the scene layout (wrap and positions) for every FX template. */
export function outlineGap(style: { stroke_color: string | null; stroke_px: number }, look: BaseLook, px: number): number {
  if (!style.stroke_color) return 0;
  return style.stroke_px > 0 ? style.stroke_px : look.strokeEm * px;
}

/** "#RRGGBBAA" → "rgba(r,g,b,a)" (both canvases read it; some Skia builds reject 8-digit hex); others unchanged. */
export function cssColor(color: string): string {
  const m = /^#([0-9a-fA-F]{6})([0-9a-fA-F]{2})$/.exec(color.trim());
  if (!m) return color;
  const n = parseInt(m[1], 16), a = parseInt(m[2], 16) / 255;
  return `rgba(${(n >> 16) & 255},${(n >> 8) & 255},${n & 255},${Math.round(a * 1000) / 1000})`;
}

/** Index of the spoken word in a chunk at t (the last word that has started; the first before any has). */
export function activeWordIndex(chunk: CaptionChunk, t: number): number {
  let index = 0;
  chunk.words.forEach((w, i) => { if (w.start <= t) index = i; });
  return index;
}

/** Whole-line entrance at `local` seconds into the chunk: scale about the line centre, alpha and y offset. */
export function entranceAt(entrance: BaseLook["entrance"], local: number, u: number): { scale: number; alpha: number; dy: number } {
  switch (entrance) {
    case "settle": return { scale: keyframes([{ t: 0, v: 0.92 }, { t: 0.14, v: 1, ease: easeOutQuad }], local), alpha: 1, dy: 0 };
    case "pop": return {
      scale: keyframes([{ t: 0, v: 0.88 }, { t: 0.13, v: 1.06, ease: easeOutQuad }, { t: 0.25, v: 1, ease: easeOutQuad }], local),
      alpha: 1, dy: 0,
    };
    case "rise": {
      const k = easeOutCubic(progress(local, 0, 0.16));
      return { scale: 1, alpha: k, dy: (1 - k) * 14 * u };
    }
    default: return { scale: 1, alpha: 1, dy: 0 };
  }
}

/**
 * Draws one caption chunk: optional line boxes, then every word's outline (with the shadow), then every word's fill,
 * so a thick outline never covers a neighbouring glyph. Word boxes come from the scene layout (output px).
 */
export function baseCaptionOps(chunk: CaptionChunk, dc: Omit<DrawerContext, "local" | "dur">, look?: BaseLook): DrawOp[] {
  const p = dc.scene, style = p.scene.caption;
  if (!style || !p.captionFamily || !chunk.words.length) return [];
  const params = look ?? BASE_LOOKS[p.scene.template as FxCaptionTemplate] ?? DEFAULT_BASE_LOOK;
  const u = dc.u, px = chunk.px, family = p.captionFamily;
  const local = dc.t - chunk.start;
  const ent = entranceAt(params.entrance, local, u);
  const outer = mul(translation(0, ent.dy), ent.scale === 1 ? IDENTITY : scaleAbout(ent.scale, chunk.centerX, chunk.centerY));
  const strokeColor = style.stroke_color;
  const strokePx = strokeColor ? (style.stroke_px > 0 ? style.stroke_px : params.strokeEm * px) : 0;
  const boxed = !!style.background;
  const kind = params.shadow === "none" ? (style.shadow ? "soft" : "none") : params.shadow;
  const sh: Shadow | null = kind === "none" || !(style.shadow || (!strokePx && !boxed)) ? null
    : kind === "hard" ? { color: "rgba(0,0,0,0.55)", blur: 0, dx: 0, dy: 6 * u * ent.scale }
      : { color: "rgba(0,0,0,0.6)", blur: 12 * u, dx: 0, dy: 4 * u };
  const active = params.active === "none" ? -1 : activeWordIndex(chunk, dc.t);
  const letterSpacing = params.tracking * px;
  const ops: DrawOp[] = [];

  if (boxed) {
    const lines = new Map<number, { x0: number; x1: number; y: number }>();
    for (const w of chunk.words) {
      const l = lines.get(w.line);
      if (!l) lines.set(w.line, { x0: w.x, x1: w.x + w.w, y: w.y });
      else { l.x0 = Math.min(l.x0, w.x); l.x1 = Math.max(l.x1, w.x + w.w); }
    }
    const padX = px * 0.32, padY = px * 0.16;
    for (const l of lines.values()) {
      ops.push(rectOp({
        x: l.x0 - padX, y: l.y - padY, w: l.x1 - l.x0 + 2 * padX, h: px + 2 * padY, radius: px * 0.22,
        fill: cssColor(style.background!), transform: outer, alpha: ent.alpha,
      }));
    }
  }

  // Accent pop (D11): the spoken word grows to ACCENT_POP and its line neighbours slide apart by the extra width, so
  // the popped word never touches them (outlines included).
  const pop = params.active === "accent-pop" && active >= 0 ? popScale(dc.t - chunk.words[active].start) : 1;
  const popped = chunk.words[active];
  const push = pop > 1 ? (popped.w * (pop - 1)) / 2 + strokePx * (pop - 1) * 2 : 0;
  const wordTransform = (i: number) => {
    const w = chunk.words[i];
    if (pop <= 1 || w.line !== popped.line) return outer;
    if (i === active) return mul(outer, scaleAbout(pop, w.x + w.w / 2, w.y + w.h / 2));
    return mul(outer, translation(i < active ? -push : push, 0));
  };
  const at = (i: number) => {
    const w = chunk.words[i];
    return { text: w.text, font: family, px, x: w.x, y: w.y + w.h / 2, align: "left" as const, letterSpacing, transform: wordTransform(i), alpha: ent.alpha };
  };

  // Outline pass first (it carries the shadow), then fills; without an outline the fill carries the shadow.
  if (strokePx > 0) {
    chunk.words.forEach((_, i) => ops.push(textOp({ ...at(i), fill: null, stroke: strokeColor, strokePx, shadow: sh })));
  }
  chunk.words.forEach((w, i) => {
    const accent = i === active || (params.emphasis && w.highlight);
    ops.push(textOp({ ...at(i), fill: accent ? style.accent : style.color, shadow: strokePx > 0 ? null : sh }));
  });
  return ops;
}
