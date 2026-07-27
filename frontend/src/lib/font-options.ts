export interface FontOptionsPayload {
  font_family: string | null;
  font_size: number | null;
  font_color: string | null;
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
