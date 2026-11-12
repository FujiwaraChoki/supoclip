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
  editor_opened: { clip_count: number };
  ai_edit_submitted: { outcome: "accepted" | "limit_reached" };
  clip_downloaded: { surface: ClipExportSurface; format: string };
  clips_zip_downloaded: { surface: ClipExportSurface };
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
