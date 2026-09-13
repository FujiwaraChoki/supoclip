export type HookPosition = "top" | "center" | "bottom";
export type HookAnimation =
  | "fade_pop"
  | "fade"
  | "slide_down"
  | "zoom_punch"
  | "bounce"
  | "pulse"
  | "none";

// Content-type classification for a clip's hook. Mirrors
// backend/src/caption_templates.py's HOOK_TYPES/HOOK_TYPE_LABELS.
export type HookType =
  | "question"
  | "statement"
  | "statistic"
  | "story"
  | "contrast"
  | "callout"
  | "warning"
  | "none";

export const HOOK_TYPE_LABELS: Record<HookType, string> = {
  question: "Question Hook",
  statement: "Bold Statement",
  statistic: "Data/Stats",
  story: "Story Hook",
  contrast: "Contrast Hook",
  callout: "Callout",
  warning: "Warning",
  none: "No Hook",
};

export type HookTitleVariant = {
  id: string;
  text: string;
};

export type HookStyle = {
  hook_font_family: string | null;
  hook_font_size_scale: number | null;
  hook_font_color: string | null;
  hook_background_color: string | null;
  hook_stroke_color: string | null;
  hook_position: HookPosition | null;
  hook_duration_seconds: number | null;
  hook_animation: HookAnimation | null;
  hook_shadow: boolean | null;
  hook_highlight_color: string | null;
  hook_sfx: string | null;
};

// null fields mean "inherit from the caption template" — mirrors the
// hook_* keys in backend/src/caption_templates.py's TEMPLATE_DEFAULTS.
export const DEFAULT_HOOK_STYLE: HookStyle = {
  hook_font_family: null,
  hook_font_size_scale: null,
  hook_font_color: null,
  hook_background_color: null,
  hook_stroke_color: null,
  hook_position: null,
  hook_duration_seconds: null,
  hook_animation: null,
  hook_shadow: null,
  hook_highlight_color: null,
  hook_sfx: null,
};

/** Strips null (inherit) fields so the request only carries explicit overrides. */
export function hookStylePayload(style: HookStyle): Record<string, unknown> | null {
  const entries = Object.entries(style).filter(([, value]) => value !== null);
  if (entries.length === 0) return null;
  return Object.fromEntries(entries);
}
