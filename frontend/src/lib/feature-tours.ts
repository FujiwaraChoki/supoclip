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
  /** Select this workflow while the step shows, and draw what it makes on the card. */
  workflow?: Workflow;
  /** The opening step: offers "Show me" or "Not now" so nobody is walked through anything they didn't ask for. */
  intro?: boolean;
};

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
  /** A picture drawn in code for the opening dialog, instead of `media`. */
  art?: "welcome";
  /** Looping video for the opening announcement, from `public/`: render it, don't screenshot it. */
  media?: {
    poster: string;
    videos: readonly { src: string; type: string }[];
    /** While choosing: candidate renders to compare in a preview (`?tour=<id>`), a JSON list of `{ slug, name }` beside `<slug>.webm|mp4|webp`. Remove once one is picked. */
    variants?: string;
  };
  /** Other tours this one already explains, so nobody gets them again. */
  covers?: readonly string[];
  steps: readonly TourStep[];
};

/** Long enough to explain a real feature, short enough to finish. A test holds every tour to it. */
export const MAX_TOUR_STEPS = 8;
/** Never two tours close together, however many features ship. */
export const TOUR_COOLDOWN_MS = 24 * 60 * 60 * 1000;

/** First match wins, so put what matters most first. */
export const FEATURE_TOURS: readonly FeatureTour[] = [
  {
    // First visit: what SupoClip makes, how to get a first clip, and what the other pages are for.
    id: "welcome",
    audience: "new",
    releasedAt: "2026-11-16T00:00:00Z",
    art: "welcome",
    covers: ["composer-settings"],
    steps: [
      { target: "composer", message: "intro", side: "bottom", intro: true },
      { target: "composer-source", message: "source", side: "bottom" },
      { target: "workflow-picker", message: "workflows", side: "bottom" },
      { target: "composer-settings", message: "settings", side: "top" },
      { target: "nav-library", message: "library", side: "right" },
      { target: "nav-social", message: "social", side: "right" },
      { target: "nav-agents", message: "agents", side: "right" },
      { target: "nav-settings", message: "settingsPage", side: "right" },
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
