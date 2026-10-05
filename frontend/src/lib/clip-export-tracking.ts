"use client";

import { attributionMetadata, getAccountAttribution } from "@/lib/attribution";
import { track } from "@/lib/datafast";

export type ClipExportSurface = "task" | "editor" | "editor_queue";

/**
 * Records a clip export. Every export sends `clip_exported`; the user's first
 * export also sends `first_clip_exported` with the server-stored attribution,
 * so activation can be credited to the campaign even across devices. Only the
 * exporting account's own attribution (confirmed, or claimed and pending) is attached.
 */
export async function trackClipExport({ userId, ...details }: { userId: string; surface: ClipExportSurface; format: string }) {
  const accountMetadata = attributionMetadata(getAccountAttribution(userId));
  track("clip_exported", { ...details, ...accountMetadata });

  try {
    const response = await fetch("/api/attribution/clip-export", { method: "POST", keepalive: true });
    if (!response.ok) return;
    const result = await response.json();
    if (!result?.first_export) return;
    // An export can land before sign-up attribution syncs; fall back to this account's claimed visit.
    const serverMetadata = attributionMetadata(result.attribution);
    track("first_clip_exported", {
      ...details,
      ...(Object.keys(serverMetadata).length > 0 ? serverMetadata : accountMetadata),
      hours_since_signup: result.hours_since_signup,
    });
  } catch {
    // Tracking must never interfere with the download itself.
  }
}
