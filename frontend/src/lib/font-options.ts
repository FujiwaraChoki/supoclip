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
