/**
 * The first time someone in the Pebble onboarding group opens a page, Agent pops up beside one thing
 * on it and says what it's for (docs/onboarding.md). Shown once per account, recorded like a
 * feature tour (`user_feature_tours`, id `pebble-<page>`).
 */
export interface PebbleHint {
  id: string;
  /** Whether this hint belongs to the page at `pathname`. */
  matches: (pathname: string) => boolean;
  /** `data-tour` of what Agent points at. */
  target: string;
  /** Key in `onboarding.hints`. */
  message: string;
}

export const PEBBLE_HINTS: readonly PebbleHint[] = [
  { id: "pebble-library", matches: (path) => path === "/list", target: "library-header", message: "library" },
  { id: "pebble-calendar", matches: (path) => path === "/calendar", target: "calendar-grid", message: "calendar" },
  { id: "pebble-social", matches: (path) => path === "/settings/social", target: "social-connect", message: "social" },
  { id: "pebble-analytics", matches: (path) => path === "/analytics", target: "analytics-header", message: "analytics" },
  { id: "pebble-settings", matches: (path) => path === "/settings", target: "settings-captions", message: "settings" },
  { id: "pebble-assistants", matches: (path) => path === "/settings/api-keys", target: "assistant-connect", message: "assistants" },
  { id: "pebble-clips", matches: (path) => /^\/tasks\/[^/]+$/.test(path), target: "task-clips", message: "clips" },
  { id: "pebble-studio", matches: (path) => /^\/tasks\/[^/]+\/edit$/.test(path), target: "editor-ai", message: "studio" },
];

export function isPebbleHintId(value: unknown): value is string {
  return PEBBLE_HINTS.some((hint) => hint.id === value);
}
