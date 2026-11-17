// Intensity caption looks (FxCaptionStyle.look subtle / bold / max) for the listed templates (highlighter, emoji drop,
// behind the head), measured from reference edits (docs/dynamic-edits.md "Reference edits"):
//   subtle  podcast clippers: sentence case, phrase groups of up to 4 words shown whole one frame before the first
//           word is spoken, soft shadow, no entrance motion;
//   bold    Hormozi: words added one at a time one frame after they are spoken, the line re-centring instantly, 0-frame
//           entrance, a dark pill (highlighter) or a soft shadow; the keyword in CAPS on an opaque rounded box
//           (highlighter) or in the accent colour;
//   max     MrBeast / clipper caps: like bold, ALL CAPS with a 0.09 em black outline and a hard shadow, each new word
//           popping in over 2 frames (0.85 scale at 60 % opacity, then 1.0; no overshoot).
// Font, size, colours, outline, pill and position come from the resolved FxCaptionStyle (edit_fx/caption_style.py);
// grouping, timing and keyword marks from the scene layout (scene.ts layoutLookCaptions). Nothing animates slowly.
import type { DrawOp, Shadow } from "../host";
import type { CaptionChunk, DrawerContext } from "../registry";
import type { PreparedScene } from "../scene";
import type { CaptionLook } from "../types";
import { rectOp, scaleAbout, textOp } from "../layout";
import { cssColor } from "./base";

export type ActiveLook = Exclude<CaptionLook, "template">;

export interface LookMotion {
  /** "word": each word appears at its own onset + lead; "group": the whole group at its first word's. */
  reveal: "word" | "group";
  /** Per-word (word reveal) or per-group entrance: none, or a 2-frame pop. */
  entrance: "none" | "pop";
  /** Strip sentence punctuation from the edges of words (bold and max captions carry none). */
  stripPunct: boolean;
  /** Shadow under unboxed words: soft (0.05 em down, 0.12 em blur, 60 %) or hard (0.05 em down, no blur). */
  shadow: "soft" | "hard";
  /** Keywords in capitals (they always are when the whole look is upper case). */
  keywordCaps: boolean;
}

export const LOOK_MOTION: Record<ActiveLook, LookMotion> = {
  subtle: { reveal: "group", entrance: "none", stripPunct: false, shadow: "soft", keywordCaps: false },
  bold: { reveal: "word", entrance: "none", stripPunct: true, shadow: "soft", keywordCaps: true },
  max: { reveal: "word", entrance: "pop", stripPunct: true, shadow: "hard", keywordCaps: true },
};

/** Spec §3.1/3.2 geometry, in em of the caption size. */
export const LOOK_GEOMETRY = {
  pillPadX: 0.3, pillPadY: 0.14, pillRadius: 0.25,       // radius as a fraction of the pill height
  boxPadX: 0.15, boxPadY: 0.08, boxRadius: 0.12,         // keyword box, radius as a fraction of the box height
  popScale: 0.85, popAlpha: 0.6,                          // max: the first frame of a new word
  shadowDy: 0.05, softBlur: 0.12, softAlpha: 0.6, hardAlpha: 0.85,
  maxWidth: 0.8, minShrink: 0.8,                          // a group never exceeds 0.80 W; shrink to 0.8× before wrapping
} as const;

const FRAME = 1 / 30;

/** The scene's active look, or null for template captions (hidden templates, legacy scenes). */
export function activeLook(p: Pick<PreparedScene, "scene">): ActiveLook | null {
  const look = p.scene.caption?.look;
  return look === "subtle" || look === "bold" || look === "max" ? look : null;
}

/** Keyword treatment: the highlighter boxes it, the others colour it. */
export const keywordBoxed = (template: string): boolean => template === "highlighter_swipe";

/**
 * Edge punctuation off ("crazy," → "crazy", "“yes”" → "yes"); currency, %, inner apostrophes and digits stay, and so
 * do the stars of a censored word ("n****." → "n****", never "n").
 */
export function stripEdgePunct(text: string): string {
  const out = text.replace(/^[^\p{L}\p{N}$€£¥#]+/u, "").replace(/[^\p{L}\p{N}%'*]+$/u, "");
  return out || text;
}

type LaidWord = CaptionChunk["words"][number];

/** The words on screen at t, each re-centred on its line among the words already shown. */
export function visibleLayout(chunk: CaptionChunk, t: number): LaidWord[] {
  const shown = chunk.words.filter((w) => (w.shown ?? chunk.start) <= t + 1e-9);
  if (!shown.length) return [];
  const space = chunk.space ?? 0;
  const byLine = new Map<number, LaidWord[]>();
  for (const w of shown) byLine.set(w.line, [...(byLine.get(w.line) ?? []), w]);
  const out: LaidWord[] = [];
  for (const line of byLine.values()) {
    const total = line.reduce((sum, w) => sum + w.w, 0) + space * (line.length - 1);
    let x = chunk.centerX - total / 2;
    for (const w of line) {
      out.push({ ...w, x });
      x += w.w + space;
    }
  }
  return out;
}

/** Box of a laid-out keyword (the box is the word's advance; text sits inside its padding). */
export function keywordBox(w: Pick<LaidWord, "x" | "y" | "w" | "h">, px: number) {
  const padY = LOOK_GEOMETRY.boxPadY * px;
  const h = w.h + 2 * padY;
  return { x: w.x, y: w.y - padY, w: w.w, h, radius: LOOK_GEOMETRY.boxRadius * h };
}

/** Draws one look caption chunk at dc.t: pills, outlines (with the shadow), keyword boxes, then the fills. */
export function lookCaptionOps(chunk: CaptionChunk, dc: Omit<DrawerContext, "local" | "dur">): DrawOp[] {
  const p = dc.scene, style = p.scene.caption, look = activeLook(p);
  if (!style || !look || !p.captionFamily) return [];
  const motion = LOOK_MOTION[look], g = LOOK_GEOMETRY;
  const words = visibleLayout(chunk, dc.t);
  if (!words.length) return [];
  const px = chunk.px, family = p.captionFamily, boxed = keywordBoxed(p.scene.template);
  const strokePx = style.stroke_color ? (style.stroke_px > 0 ? style.stroke_px : 0.09 * px) : 0;
  const pill = style.background ? cssColor(style.background) : null;
  const shadow: Shadow | null = motion.shadow === "hard"
    ? { color: `rgba(0,0,0,${g.hardAlpha})`, blur: 0, dx: 0, dy: g.shadowDy * px }
    : { color: `rgba(0,0,0,${g.softAlpha})`, blur: g.softBlur * px, dx: 0, dy: g.shadowDy * px };

  // Entrance: max pops each new word for one frame (word reveal) or the whole group (group reveal).
  const popOf = (w: LaidWord) => {
    if (motion.entrance !== "pop") return null;
    const age = dc.t - (motion.reveal === "word" ? (w.shown ?? chunk.start) : chunk.start);
    return age >= -1e-9 && age < FRAME - 1e-9 ? { s: g.popScale, a: g.popAlpha } : null;
  };
  const ops: DrawOp[] = [];

  if (pill) {
    const lines = new Map<number, { x0: number; x1: number; y: number; h: number }>();
    for (const w of words) {
      const l = lines.get(w.line);
      if (!l) lines.set(w.line, { x0: w.x, x1: w.x + w.w, y: w.y, h: w.h });
      else { l.x0 = Math.min(l.x0, w.x); l.x1 = Math.max(l.x1, w.x + w.w); }
    }
    for (const l of lines.values()) {
      const h = l.h + 2 * g.pillPadY * px;
      ops.push(rectOp({ x: l.x0 - g.pillPadX * px, y: l.y - g.pillPadY * px, w: l.x1 - l.x0 + 2 * g.pillPadX * px, h,
        radius: g.pillRadius * h, fill: pill }));
    }
  }

  const textAt = (w: LaidWord) => {
    const pop = popOf(w);
    const inset = w.kw && boxed ? g.boxPadX * px : 0;
    const transform = pop ? scaleAbout(pop.s, w.x + w.w / 2, w.y + w.h / 2) : undefined;
    return {
      text: w.text, font: family, px, x: w.x + inset, y: w.y + w.h / 2, align: "left" as const,
      ...(transform ? { transform } : {}), alpha: pop ? pop.a : 1,
    };
  };

  // Outlines (they carry the shadow), never on boxed keywords.
  if (strokePx > 0) {
    for (const w of words) {
      if (w.kw && boxed) continue;
      ops.push(textOp({ ...textAt(w), fill: null, stroke: style.stroke_color, strokePx, shadow }));
    }
  }
  // Keyword boxes: opaque, rounded, there on the word's first frame and gone with its group.
  if (boxed) {
    for (const w of words) {
      if (!w.kw) continue;
      const pop = popOf(w);
      const box = keywordBox(w, px);
      ops.push(rectOp({ ...box, fill: style.accent,
        ...(pop ? { transform: scaleAbout(pop.s, w.x + w.w / 2, w.y + w.h / 2), alpha: pop.a } : {}) }));
    }
  }
  for (const w of words) {
    const kwBox = w.kw && boxed;
    const fill = kwBox ? "#000000" : w.kw ? style.accent : style.color;
    const sh = strokePx > 0 || kwBox || pill ? null : shadow;
    ops.push(textOp({ ...textAt(w), fill, shadow: sh }));
  }
  return ops;
}
