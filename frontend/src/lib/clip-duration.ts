export const CLIP_LENGTHS = ["auto", "short", "medium", "long"] as const;
export type ClipLength = (typeof CLIP_LENGTHS)[number];

export function isClipLength(value: unknown): value is ClipLength {
  return typeof value === "string" && CLIP_LENGTHS.includes(value as ClipLength);
}
