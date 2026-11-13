// Colour grades (§11.4, G15). A grade is a list of CSS filter functions (folded into the picture op's filter, so the
// browser, deno and the ImageData fallback all agree) plus optional extras drawn over the picture in the "pixel" layer:
// tints (a flat colour with a blend), a vignette, VHS scanlines and a dream glow (a blurred, screened copy).
//
// gradeOps(id)    → the CSS filter string for the picture op ("" for none).
// gradeMatrix(id) → the same ops composed into one 4×5 colour matrix (W3C filter-effects definitions). rasterize uses
//                   it through ImageData when the host has no ctx.filter; it is also the documented Plan B mapping for
//                   an ffmpeg drawer: rows R,G,B → colorchannelmixer rr:rg:rb, gr:gg:gb, br:bg:bb (+ offsets via lutrgb).
import type { Blend, DrawOp } from "./host";
import { gradientOp, pictureMatrix, rectOp, round3 } from "./layout";
import { composeMatrix, filterFunctionMatrix, IDENTITY_MATRIX, type ColorMatrix } from "./raster";
import { registerGlobal, registerPicture } from "./registry";
import type { GradeId } from "./types";

export type GradeOp = ["brightness" | "contrast" | "saturate" | "hue-rotate" | "sepia" | "grayscale", number];
export interface GradeSpec {
  ops: GradeOp[]; tint: { color: string; blend: Blend; alpha: number }[]; vignette: number; scanlines: number; glow: number;
}

const spec = (ops: GradeOp[], extra: Partial<Omit<GradeSpec, "ops">> = {}): GradeSpec => ({
  ops, tint: [], vignette: 0, scanlines: 0, glow: 0, ...extra,
});

/** §11.4, the authoritative table. */
export const GRADE_SPECS: Record<GradeId, GradeSpec> = {
  none: spec([]),
  punchy: spec([["contrast", 1.12], ["saturate", 1.25]], { vignette: 0.15 }),
  warm: spec([["sepia", 0.18], ["saturate", 1.1], ["brightness", 1.03]], {
    tint: [{ color: "#FFB070", blend: "soft-light", alpha: 0.12 }],
  }),
  teal_orange: spec([["contrast", 1.08], ["saturate", 1.15]], {
    tint: [{ color: "#008C9E", blend: "soft-light", alpha: 0.18 }, { color: "#FF8A3D", blend: "overlay", alpha: 0.08 }],
  }),
  noir: spec([["grayscale", 1], ["contrast", 1.3], ["brightness", 0.95]], { vignette: 0.3 }),
  vhs: spec([["saturate", 1.3], ["contrast", 0.92]], { scanlines: 0.06 }),
  dream: spec([["brightness", 1.06], ["contrast", 0.92], ["saturate", 1.1]], { glow: 0.25 }),
};

/** VHS chroma bleed: a constant, glitch-free RGB split of 2u px (§11.4). */
export const VHS_RGB_SPLIT = 2;
/** Dream glow blur radius in u. */
export const DREAM_GLOW_BLUR = 14;
/** VHS scanline period in u (a dark band every 4u px, half as tall). */
export const VHS_SCANLINE_SPACING = 4;

const gradeSpec = (id: GradeId | string): GradeSpec => GRADE_SPECS[id as GradeId] ?? GRADE_SPECS.none;
const fnText = ([name, amount]: GradeOp): string => (name === "hue-rotate" ? `${name}(${amount}deg)` : `${name}(${amount})`);

/** The grade's filter functions, one string each, in application order. */
export const gradeFilters = (id: GradeId): string[] => gradeSpec(id).ops.map(fnText);

/** The CSS filter string for the picture op ("" for none). */
export function gradeOps(id: GradeId): string {
  return gradeFilters(id).join(" ");
}

/** The grade's ops composed (first op applied first) into one row-major 4×5 matrix; offsets in 0..1 units. */
export function gradeMatrix(id: GradeId): number[] {
  let out: ColorMatrix = [...IDENTITY_MATRIX];
  for (const [name, amount] of gradeSpec(id).ops) {
    const m = filterFunctionMatrix(name, amount);
    if (m) out = composeMatrix(m, out);
  }
  return out;
}

/** True when the grade draws anything over the picture (tint, vignette, scanlines, glow). */
export const gradeHasExtras = (id: GradeId): boolean => {
  const s = gradeSpec(id);
  return s.tint.length > 0 || s.vignette > 0 || s.scanlines > 0 || s.glow > 0;
};

registerPicture({
  kind: "grade",
  apply: (state, _cue, dc) => {
    const id = dc.scene.scene.grade;
    const filters = gradeFilters(id);
    if (!filters.length && id !== "vhs") return state;
    return {
      ...state,
      filter: [...state.filter, ...filters],
      rgbSplit: id === "vhs" ? Math.max(state.rgbSplit, round3(VHS_RGB_SPLIT * dc.u)) : state.rgbSplit,
    };
  },
});

/** Grade extras, bottom → top: glow (blurred picture copy, screen), tints, vignette, scanlines. */
registerGlobal({
  id: "grade",
  layer: "pixel",
  active: (p) => gradeHasExtras(p.scene.grade),
  ops: (dc) => {
    const p = dc.scene, W = p.width, H = p.height, s = gradeSpec(p.scene.grade);
    const ops: DrawOp[] = [];
    if (s.glow > 0) {
      const blur = round3(DREAM_GLOW_BLUR * dc.u);
      ops.push({
        op: "picture", transform: pictureMatrix(dc.picture, W, H), filter: [...dc.picture.filter, `blur(${blur}px)`].join(" "),
        alpha: s.glow, blend: "screen", channel: "rgb", dx: 0, dy: 0, cutout: false, clip: null,
      });
    }
    for (const tint of s.tint) ops.push(rectOp({ x: 0, y: 0, w: W, h: H, fill: tint.color, alpha: tint.alpha, blend: tint.blend }));
    if (s.vignette > 0) {
      const r = Math.hypot(W, H) / 2;
      ops.push(gradientOp({
        kind: "radial", x0: W / 2, y0: H / 2, r0: 0, x1: W / 2, y1: H / 2, r1: r, rect: [0, 0, W, H], alpha: s.vignette,
        stops: [[0, "rgba(0,0,0,0)"], [0.5, "rgba(0,0,0,0)"], [0.8, "rgba(0,0,0,0.55)"], [1, "rgba(0,0,0,1)"]],
      }));
    }
    if (s.scanlines > 0) {
      ops.push({ op: "scanlines", alpha: s.scanlines, spacing: Math.max(2, round3(VHS_SCANLINE_SPACING * dc.u)), offset: 0 });
    }
    return ops;
  },
});
