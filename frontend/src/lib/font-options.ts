export interface FontOptionsPayload {
  font_family: string | null;
  font_size: number | null;
  font_color: string | null;
}

// Sentinel Select value for "use the caption template's own font" (null in state/payload).
// Shared between the create-task form and the per-task settings sheet so both
// UIs speak the same "Template default" convention.
export const FONT_TEMPLATE_DEFAULT_VALUE = "__template_default__";

// Font size is a simple segmented choice — null defers to the caption template's size.
export const FONT_SIZE_OPTIONS: Array<{ label: string; value: number | null }> = [
  { label: "Template default", value: null },
  { label: "Small", value: 18 },
  { label: "Medium", value: 28 },
  { label: "Large", value: 40 },
];

// Column defaults every user row starts with (see the users table). A saved
// default equal to one of these was never changed in Settings, so it keeps
// deferring to the caption template.
const STOCK_FONT_DEFAULTS = { fontFamily: "TikTokSans-Regular", fontSize: 24, fontColor: "#FFFFFF" };

/**
 * Turns the caption defaults saved in Settings (GET /api/preferences) into
 * create-form state. Fields the user customized become explicit overrides;
 * untouched or invalid fields stay null so the caption template decides.
 */
export function savedCaptionDefaults(preferences: {
  fontFamily?: unknown;
  fontSize?: unknown;
  fontColor?: unknown;
} | null | undefined): { fontFamily: string | null; fontSize: number | null; fontColor: string | null } {
  const { fontFamily, fontSize, fontColor } = preferences ?? {};
  return {
    fontFamily:
      typeof fontFamily === "string" && fontFamily.trim() && fontFamily !== STOCK_FONT_DEFAULTS.fontFamily
        ? fontFamily
        : null,
    fontSize:
      typeof fontSize === "number" && Number.isInteger(fontSize) && fontSize >= 12 && fontSize <= 48
        && fontSize !== STOCK_FONT_DEFAULTS.fontSize
        ? fontSize
        : null,
    fontColor:
      typeof fontColor === "string" && /^#[0-9A-Fa-f]{6}$/.test(fontColor)
        && fontColor.toUpperCase() !== STOCK_FONT_DEFAULTS.fontColor
        ? fontColor
        : null,
  };
}

/**
 * Builds the font_options payload sent to /api/tasks/create. Each field is
 * null unless the user explicitly customized it in "Customize captions" —
 * null tells the backend to fall back to the selected caption template's own
 * font/size/color instead of a hardcoded default.
 */
export function buildFontOptionsPayload(
  fontFamily: string | null,
  fontSize: number | null,
  fontColor: string | null,
): FontOptionsPayload {
  const normalizedColor =
    fontColor && /^#[0-9A-Fa-f]{6}$/.test(fontColor) ? fontColor : null;

  return {
    font_family: fontFamily,
    font_size: fontSize,
    font_color: normalizedColor,
  };
}
