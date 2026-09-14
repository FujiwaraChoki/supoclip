/**
 * Per-platform "safe zone" data for the editor's Safe Zone Overlay.
 *
 * Values are % of frame (portrait 9:16 base) covered by that platform's own
 * UI chrome (username/sound, caption/CTA, like/comment/share rail, nav bar).
 * Sourced from the product spec; re-verify against current platform specs
 * before changing — these are conservative estimates, not pixel-exact.
 *
 * To update: edit PLATFORM_SAFE_ZONES below. No code changes needed
 * elsewhere — the overlay, toggle, and export-preset suggestion all read
 * from this one table.
 */

export type SafeZonePlatformId =
  | "tiktok"
  | "reels"
  | "youtube_shorts"
  | "facebook_reels"
  | "threads";

export interface SafeZoneInsets {
  /** % of frame height covered by UI at the top. */
  top: number;
  /** % of frame height covered by UI at the bottom. */
  bottom: number;
  /** % of frame width covered by UI at the right. */
  right: number;
  /** % of frame width covered by UI at the left. */
  left: number;
}

export interface SafeZonePlatform {
  id: SafeZonePlatformId;
  label: string;
  insets: SafeZoneInsets;
}

export const PLATFORM_SAFE_ZONES: Record<SafeZonePlatformId, SafeZonePlatform> = {
  tiktok: {
    id: "tiktok",
    label: "TikTok",
    insets: { top: 10, bottom: 15, right: 12, left: 5 },
  },
  reels: {
    id: "reels",
    label: "Instagram Reels",
    insets: { top: 12, bottom: 20, right: 15, left: 5 },
  },
  youtube_shorts: {
    id: "youtube_shorts",
    label: "YouTube Shorts",
    insets: { top: 8, bottom: 15, right: 12, left: 5 },
  },
  facebook_reels: {
    id: "facebook_reels",
    label: "Facebook Reels",
    insets: { top: 12, bottom: 18, right: 14, left: 5 },
  },
  threads: {
    id: "threads",
    label: "Threads",
    insets: { top: 10, bottom: 15, right: 12, left: 5 },
  },
};

export const SAFE_ZONE_PLATFORM_IDS = Object.keys(PLATFORM_SAFE_ZONES) as SafeZonePlatformId[];

/** "all" overlays every platform at once; a single platform id shows just that one. */
export type SafeZoneSelection = "all" | SafeZonePlatformId;

/** Maps a backend export preset name (`EXPORT_PRESETS` in clip_editor.py) to a safe-zone platform. */
export function platformForExportPreset(presetName: string | null | undefined): SafeZonePlatformId | null {
  if (!presetName) return null;
  if (presetName in PLATFORM_SAFE_ZONES) return presetName as SafeZonePlatformId;
  if (presetName === "shorts") return "youtube_shorts";
  return null;
}

/** The common safe area across every platform (the largest inset on each side). */
export function intersectionInsets(): SafeZoneInsets {
  const platforms = Object.values(PLATFORM_SAFE_ZONES);
  return {
    top: Math.max(...platforms.map((p) => p.insets.top)),
    bottom: Math.max(...platforms.map((p) => p.insets.bottom)),
    right: Math.max(...platforms.map((p) => p.insets.right)),
    left: Math.max(...platforms.map((p) => p.insets.left)),
  };
}

/** Does a horizontal band (as % from top, e.g. a caption/hook block) overlap this platform's unsafe zones? */
export function bandOverlapsUnsafeZone(
  bandTopPct: number,
  bandBottomPct: number,
  insets: SafeZoneInsets,
): boolean {
  const unsafeTopEnd = insets.top;
  const unsafeBottomStart = 100 - insets.bottom;
  return bandTopPct < unsafeTopEnd || bandBottomPct > unsafeBottomStart;
}
