// Shared geometry and emoji constants (§11.3): mirror of backend/src/edit_fx/constants.py.
// Every drawer authors px constants in units of u = min(W, H) / 1080 so vertical, square and landscape all work.
import type { Tone } from "./types";

export interface Rect { x: number; y: number; w: number; h: number }

/** u = min(W, H) / 1080: one design pixel at the output size. */
export const unit = (w: number, h: number): number => Math.min(w, h) / 1080;

/** Fraction of the height blocked at the top while the hook title burns (hook_window). */
export const HOOK_BAND = 0.2;

/** Caption / overlay safe margins: x 6 % each side, top max(40u, 5 % H), bottom max(120u, 10 % H). */
export const CAPTION_SAFE = { side: 0.06, top: { u: 40, frac: 0.05 }, bottom: { u: 120, frac: 0.1 } } as const;

/** == caption_safe(w, h) in Python: `side` is a width fraction, `top` and `bottom` are px margins. */
export function captionSafe(w: number, h: number): { side: number; top: number; bottom: number } {
  const u = unit(w, h);
  return {
    side: CAPTION_SAFE.side,
    top: Math.max(CAPTION_SAFE.top.u * u, CAPTION_SAFE.top.frac * h),
    bottom: Math.max(CAPTION_SAFE.bottom.u * u, CAPTION_SAFE.bottom.frac * h),
  };
}

/** Reaction PiP: the largest 4:3 rect inside a 0.70W × 0.50H box, centred horizontally, top at 0.20H (AR-M7). */
// Same operation order as reaction_rect in Python, so both copies agree to the last bit.
const REACTION_BOX_W = 0.7;
const REACTION_BOX_H = 0.5;
const REACTION_TOP = 0.2;
const REACTION_ASPECT = 4 / 3;

export function reactionRect(w: number, h: number): Rect {
  const rw = Math.min(REACTION_BOX_W * w, REACTION_BOX_H * h * REACTION_ASPECT);
  const rh = rw / REACTION_ASPECT;
  return { x: (w - rw) / 2, y: REACTION_TOP * h, w: rw, h: rh };
}

/** Fallback emoji per beat tone (AP-H5). */
export const TONE_EMOJI: Record<Tone, string> = {
  hype: "🔥", funny: "😂", shocked: "😱", sad: "😢", angry: "😤",
  wholesome: "🥹", suspense: "👀", awkward: "😅", smug: "😏", neutral: "✨",
};

/** Emoji the beat model may pick (filtered again by the atlas keys). */
export const LLM_ALLOWED_EMOJI: readonly string[] = ["💰", "💸", "📈", "📉", "🚀", "💯", "🤯", "😭", "🙏", "👏", "💪", "🧠", "❤️",
  "💔", "🎯", "⏰", "🏆", "🥇", "🍕", "☕", "🎉", "🤡", "💀", "👑", "🐐", "⚡", "🌍", "📱", "💻", "🎮", "🎵", "📚",
  "🤝", "🙄", "🤔", "😍", "🥶", "🥵", "🚨", "✅", "❌", "⚠️"];
