export type SocialOverlay = {
  enabled: boolean;
  username: string;
  verified: boolean;
  likes: string;
  comments: string;
  followers: string;
};

export const DEFAULT_SOCIAL_OVERLAY: SocialOverlay = {
  enabled: false,
  username: "",
  verified: true,
  likes: "",
  comments: "",
  followers: "",
};

export type BrollSettings = {
  enabled: boolean;
  maxInsertions: number;
  minGapSeconds: number;
};

export const DEFAULT_BROLL_SETTINGS: BrollSettings = {
  enabled: false,
  maxInsertions: 3,
  minGapSeconds: 6,
};

export type TargetDuration = 15 | 30 | 60 | null;

/** Strips to only the fields the backend accepts, and only if the overlay is meaningfully configured. */
export function socialOverlayPayload(overlay: SocialOverlay): Record<string, unknown> | null {
  if (!overlay.enabled) return null;
  const payload: Record<string, unknown> = { enabled: true, verified: overlay.verified };
  if (overlay.username.trim()) payload.username = overlay.username.trim();
  if (overlay.likes.trim()) payload.likes = overlay.likes.trim();
  if (overlay.comments.trim()) payload.comments = overlay.comments.trim();
  if (overlay.followers.trim()) payload.followers = overlay.followers.trim();
  return payload;
}

export function brollSettingsPayload(settings: BrollSettings): Record<string, unknown> | null {
  if (!settings.enabled) return { enabled: false };
  return {
    enabled: true,
    max_insertions: settings.maxInsertions,
    min_gap_seconds: settings.minGapSeconds,
  };
}
