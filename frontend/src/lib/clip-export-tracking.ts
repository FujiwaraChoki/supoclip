"use client";

import { attributionMetadata, getStoredAttribution } from "@/lib/attribution";
import { track } from "@/lib/datafast";

export type ClipExportSurface = "task" | "editor" | "editor_queue";

/**
 * Records a clip export. Every export sends `clip_exported`; the user's first
 * export also sends `first_clip_exported` with the server-stored attribution,
 * so activation can be credited to the campaign even across devices.
 */
export async function trackClipExport(details: { surface: ClipExportSurface; format: string }) {
  track("clip_exported", { ...details, ...attributionMetadata(getStoredAttribution()) });

  try {
    const response = await fetch("/api/attribution/clip-export", { method: "POST", keepalive: true });
    if (!response.ok) return;
    const result = await response.json();
    if (!result?.first_export) return;
    track("first_clip_exported", {
      ...details,
      ...attributionMetadata(result.attribution),
      hours_since_signup: result.hours_since_signup,
    });
  } catch {
    // Tracking must never interfere with the download itself.
  }
}
