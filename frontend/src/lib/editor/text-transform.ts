import { clamp, type TextTransform } from "./document";
import type { TextBounds } from "./render";

export type TextCorner = "nw" | "ne" | "sw" | "se";
export const TEXT_CORNERS: TextCorner[] = ["nw", "ne", "sw", "se"];

export function keepTextInFrame(transform: TextTransform, bounds: TextBounds, width: number, height: number): TextTransform {
  const shift = (start: number, length: number, limit: number) => length > limit - 16
    ? (limit - length) / 2 - start
    : clamp(start, 8, limit - length - 8) - start;
  return { ...transform, x: clamp(transform.x + shift(bounds.x, bounds.width, width) / width, -1, 1), y: clamp(transform.y + shift(bounds.y, bounds.height, height) / height, -1, 1) };
}

/** Uniform corner resize about the opposite corner, using both pointer axes. */
export function resizeTextScale(bounds: TextBounds, corner: TextCorner, dx: number, dy: number, initial: number) {
  const x = corner.endsWith("e") ? bounds.width : -bounds.width;
  const y = corner.startsWith("s") ? bounds.height : -bounds.height;
  const ratio = 1 + (dx * x + dy * y) / Math.max(1, x * x + y * y);
  return clamp(initial * ratio, 0.25, 4);
}
