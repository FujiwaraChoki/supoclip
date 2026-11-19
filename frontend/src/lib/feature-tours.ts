import type { Workflow } from "@/lib/workflows";

/**
 * Short tours on the real page: a welcome for new accounts, and new features for people
 * who already use SupoClip. Adding one: write an entry here and its text in messages/<locale>/tours.json,
 * and put `data-tour="<target>"` on what each step points at. See docs/feature-tours.md.
 */
export type TourStep = {
  /**
   * The `data-tour` value of what this step points at; the first visible match is used, so the sidebar
   * and the mobile tab bar can share one. Steps whose target isn't on the page or is disabled are skipped.
   */
  target: string;
  /** Key under `tours.<tour id>` holding `title` and `body`. */
  message: string;
  /** On narrow screens `left`/`right` become `top`: the targets there sit in the bottom tab bar. */
  side?: "top" | "bottom" | "left" | "right";
  /**
   * The page this step is on. The tour goes there first, so one tour can walk through several pages.
   * Without it, the step is on whatever page the tour started on.
   */
  path?: string;
  /**
   * A `data-tour` target that must be somewhere in the document (shown or not), checked before going to `path`:
   * a page that isn't open to this account (no Social link, no plan card) is passed over without a visit.
   */
  requires?: string;
  /** Select this workflow while the step shows, and draw what it makes on the card. */
  workflow?: Workflow;
  /** The opening step: offers "Show me" or "Not now" so nobody is walked through anything they didn't ask for. */
  intro?: boolean;
  /**
   * The target lives in a closed settings menu: the host asks the page to open this section first
   * (`FeatureTourHost.onSettingsSection`), e.g. the create screen's `edits` or `captions`.
   */
  settingsSection?: string;
  /** The target lives in an inspector tab (the clip studio): the host asks the page to open it first (`onInspectorTab`). */
  inspectorTab?: string;
};

/** Feature flags a tour can wait for (`GET /api/feature-flags`). */
export type TourFlag = "dynamic_edits";

export type FeatureTour = {
  id: string;
  /**
   * `existing` (default): a new feature, for accounts older than `releasedAt`; someone newer
   * never knew it any other way. `new`: onboarding, for accounts created on or after `releasedAt`.
   */
  audience?: "existing" | "new";
  /**
   * When the tour reached users. For a feature, the day it opens to them (a gated one included);
   * for onboarding, the day it shipped, so accounts from before it don't get a welcome.
   */
  releasedAt: string;
  /** Looping 2:1 video for the opening announcement, from `public/`: render it, don't screenshot it. */
  media?: {
    poster: string;
    videos: readonly { src: string; type: string }[];
    /** While choosing: candidate renders to compare in a preview (`?tour=<id>`), a JSON list of `{ slug, name }` beside `<slug>.webm|mp4|webp`. Remove once one is picked. */
    variants?: string;
  };
  /** Other tours this one already explains, so nobody gets them again. */
  covers?: readonly string[];
  /** Only for accounts with this feature flag on; the host checks `/api/feature-flags` once. */
  requiresFlag?: TourFlag;
  steps: readonly TourStep[];
};

/** Long enough to explain a real feature, short enough to finish. A test holds every tour to it. */
export const MAX_TOUR_STEPS = 8;
/** Onboarding walks through every page, a step or two each, so it gets more room. */
export const MAX_ONBOARDING_STEPS = 12;

export function maxSteps(tour: FeatureTour): number {
  return tour.audience === "new" ? MAX_ONBOARDING_STEPS : MAX_TOUR_STEPS;
}

/** Whether the tour visits other pages than the one it starts on. */
export function crossesPages(tour: FeatureTour): boolean {
  return tour.steps.some((step) => step.path !== undefined);
}
/** Never two tours close together, however many features ship. */
export const TOUR_COOLDOWN_MS = 24 * 60 * 60 * 1000;

/** First match wins, so put what matters most first. */
export const FEATURE_TOURS: readonly FeatureTour[] = [
  {
    // First visit: how to make a first clip, then a walk through every page, ending back where they start.
    id: "welcome",
    audience: "new",
    releasedAt: "2026-11-17T00:00:00Z",
    media: {
      poster: "/tours/welcome.webp",
      videos: [
        { src: "/tours/welcome.webm", type: "video/webm" },
        { src: "/tours/welcome.mp4", type: "video/mp4" },
      ],
    },
    covers: ["composer-settings"],
    steps: [
      { path: "/", target: "composer", message: "intro", side: "bottom", intro: true },
      { path: "/", target: "composer-source", message: "source", side: "bottom" },
      { path: "/", target: "workflow-picker", message: "workflows", side: "bottom" },
      { path: "/", target: "composer-settings", message: "settings", side: "top" },
      { path: "/list", target: "library-header", message: "library", side: "bottom" },
      { path: "/list", target: "library-content", message: "libraryClips", side: "top" },
      { path: "/settings/social", requires: "nav-social", target: "social-connect", message: "social", side: "bottom" },
      { path: "/analytics", requires: "nav-analytics", target: "analytics-header", message: "analytics", side: "bottom" },
      { path: "/settings/api-keys", target: "assistant-connect", message: "agents", side: "bottom" },
      { path: "/settings", target: "settings-captions", message: "captions", side: "bottom" },
      { path: "/settings", requires: "usage", target: "settings-plan", message: "plan", side: "top" },
      { path: "/", target: "composer-source", message: "finish", side: "bottom" },
    ],
  },
  {
    id: "workflows",
    // Update to the day workflows open to everyone: accounts created until then should see this too.
    releasedAt: "2026-11-06T00:00:00Z",
    media: {
      poster: "/tours/workflows.webp",
      videos: [
        { src: "/tours/workflows.webm", type: "video/webm" },
        { src: "/tours/workflows.mp4", type: "video/mp4" },
      ],
    },
    covers: ["composer-settings"],
    steps: [
      { target: "workflow-picker", message: "intro", side: "bottom", intro: true },
      { target: "workflow-highlights", message: "highlights", side: "bottom", workflow: "highlights" },
      { target: "workflow-series", message: "series", side: "bottom", workflow: "series" },
      { target: "workflow-topic", message: "topic", side: "bottom", workflow: "topic" },
      { target: "workflow-recap", message: "recap", side: "bottom", workflow: "recap" },
      { target: "workflow-trailer", message: "trailer", side: "bottom", workflow: "trailer" },
      { target: "instructions", message: "instructions", side: "top" },
      { target: "composer-settings", message: "settings", side: "top" },
    ],
  },
  {
    id: "dynamic-edits",
    // Placeholder: set to the day the dynamic_edits flag opens on web (docs/dynamic-edits.md, "Rollout").
    releasedAt: "2026-12-01T00:00:00Z",
    requiresFlag: "dynamic_edits",
    // Rendered by backend/scripts/fx/render_tour_media.py through the real FX chain.
    media: {
      poster: "/tours/dynamic-edits.webp",
      videos: [
        { src: "/tours/dynamic-edits.webm", type: "video/webm" },
        { src: "/tours/dynamic-edits.mp4", type: "video/mp4" },
      ],
    },
    covers: ["composer-settings"],
    steps: [
      { target: "composer-settings", message: "intro", side: "top", intro: true },
      { target: "edit-intensity", message: "intensity", side: "right", settingsSection: "edits" },
      { target: "edit-options", message: "sound", side: "right", settingsSection: "edits" },
      { target: "caption-styles", message: "captions", side: "left", settingsSection: "captions" },
    ],
  },
  {
    id: "dynamic-edits-studio",
    releasedAt: "2026-12-01T00:00:00Z",
    requiresFlag: "dynamic_edits",
    steps: [
      { target: "studio-edits-tab", message: "intro", side: "left", intro: true, inspectorTab: "edits" },
      { target: "studio-edits-tab", message: "panel", side: "left", inspectorTab: "edits" },
      { target: "studio-fx-lane", message: "lane", side: "top" },
    ],
  },
  {
    id: "composer-settings",
    releasedAt: "2026-11-06T00:00:00Z",
    steps: [{ target: "composer-settings", message: "settings", side: "top" }],
  },
];

export function isFeatureTourId(value: unknown): value is string {
  return FEATURE_TOURS.some((tour) => tour.id === value);
}

/** Whether a tour is meant for an account created at `created` (ms). */
function forAccount(tour: FeatureTour, created: number): boolean {
  const released = Date.parse(tour.releasedAt);
  return tour.audience === "new" ? created >= released : created < released;
}

/** The tours this account should still see, most important first. */
export function dueTours(createdAt: string | null | undefined, seen: readonly string[]): FeatureTour[] {
  const created = createdAt ? Date.parse(createdAt) : NaN;
  if (Number.isNaN(created)) return [];
  const covered = new Set(FEATURE_TOURS.filter((tour) => seen.includes(tour.id)).flatMap((tour) => tour.covers ?? []));
  return FEATURE_TOURS.filter((tour) => forAccount(tour, created) && !seen.includes(tour.id) && !covered.has(tour.id));
}
