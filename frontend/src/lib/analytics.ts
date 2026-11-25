"use client";

import posthog from "posthog-js";

import type { UpgradeReason } from "@/components/billing/upgrade-prompt";
import type { ClipExportSurface } from "@/lib/clip-export-tracking";
import { isPostHogEnabled } from "@/lib/posthog-config";
import type { SupportedVideoSource } from "@/lib/video-upload";
import type { Workflow } from "@/lib/workflows";

/**
 * Browser product funnel events for PostHog. The catalog is in
 * docs/observability.md; billing events are sent by the server (`server/analytics.ts`)
 * and processing events by the backend (`backend/src/analytics.py`).
 *
 * Names and properties are a typed union so they stay small and stable.
 * Never put emails, names, URLs, instructions or file names in properties.
 */

export type SourceKind = SupportedVideoSource | "upload";

export type ProductEvents = {
  signup_completed: { method: "email" };
  /** A recognised link in the create composer, or a picked video file. */
  source_entered: { source_kind: SourceKind };
  generate_clicked: {
    workflow: Workflow;
    /** "unknown" when the link is not one we support; the click is still counted. */
    source_kind: SourceKind | "unknown";
    has_instructions: boolean;
    blocked_by_upgrade: boolean;
  };
  upgrade_dialog_shown: { reason: UpgradeReason["kind"]; plan: string; source: string };
  /** A video was refused for its length; the user was offered a part of it and/or a bigger plan. */
  video_too_long_shown: { source_kind: SourceKind | "unknown"; plan: string; duration_minutes: number; max_minutes: number; range_offered: boolean; upgrade_offered: boolean };
  /** A task was created for a part of a video that was over the limit. */
  source_range_chosen: { source_kind: SourceKind | "unknown"; plan: string; range_minutes: number; start_minutes: number; duration_minutes: number };
  /** "Upgrade to Creator" in the too-long prompt, before checkout opens. */
  video_too_long_upgrade_clicked: { plan: string; target_plan: string; duration_minutes: number };
  editor_opened: { clip_count: number };
  ai_edit_submitted: { outcome: "accepted" | "limit_reached" };
  clip_downloaded: { surface: ClipExportSurface; format: string };
  clips_zip_downloaded: { surface: ClipExportSurface };
  /** Agent's guided onboarding (docs/onboarding.md). */
  onboarding_step: { step: "intro" | "link" | "making" | "ready" | "paid" | "choose" | "done" };
  onboarding_makes_chosen: { makes: string };
  onboarding_examples_opened: { makes: string };
  onboarding_link_checked: { eligible: boolean; fits: boolean; duration_minutes: number; estimated_clips: number };
  preview_started: { duration_minutes: number };
  preview_ready: { found: number };
  onboarding_plans_shown: { suggested: string; where: "ready" | "link" | "refused" };
  onboarding_checkout_clicked: { plan: string; interval: string; suggested: boolean };
  preview_unlocked: { plan: string };
  onboarding_completed: { skipped: boolean; stage: string };
  pebble_hint_shown: { page: string };
};

export type ProductEventName = keyof ProductEvents;

/** Sends one product event. A no-op without a PostHog key; never throws. */
export function trackEvent<E extends ProductEventName>(event: E, properties: ProductEvents[E]): void {
  if (!isPostHogEnabled || typeof window === "undefined") return;
  try {
    posthog.capture(event, properties);
  } catch {
    // Analytics must never interrupt the product.
  }
}

