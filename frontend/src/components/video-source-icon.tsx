import type { SVGProps } from "react";
import { Upload } from "lucide-react";

import { cn } from "@/lib/utils";

export type VideoSourceKind = "youtube" | "twitch" | "kick" | "upload";

export const VIDEO_SOURCE_LABELS: Record<VideoSourceKind, string> = {
  youtube: "YouTube",
  twitch: "Twitch",
  kick: "Kick",
  upload: "Upload",
};

/** Maps a task's stored source_type to the source it was imported from. */
export function videoSourceKind(sourceType: string | null | undefined): VideoSourceKind {
  return sourceType === "youtube" || sourceType === "twitch" || sourceType === "kick" ? sourceType : "upload";
}

// Monochrome brand marks from Simple Icons (CC0), drawn in currentColor so they
// follow the surrounding text colour (and dark mode) like the lucide icons.
const PATHS: Record<Exclude<VideoSourceKind, "upload">, string> = {
  youtube:
    "M23.498 6.186a3.016 3.016 0 0 0-2.122-2.136C19.505 3.545 12 3.545 12 3.545s-7.505 0-9.377.505A3.017 3.017 0 0 0 .502 6.186C0 8.07 0 12 0 12s0 3.93.502 5.814a3.016 3.016 0 0 0 2.122 2.136c1.871.505 9.376.505 9.376.505s7.505 0 9.377-.505a3.015 3.015 0 0 0 2.122-2.136C24 15.93 24 12 24 12s0-3.93-.502-5.814zM9.545 15.568V8.432L15.818 12l-6.273 3.568z",
  twitch:
    "M11.571 4.714h1.715v5.143H11.57zm4.715 0H18v5.143h-1.714zM6 0L1.714 4.286v15.428h5.143V24l4.286-4.286h3.428L22.286 12V0zm14.571 11.143l-3.428 3.428h-3.429l-3 3v-3H6.857V1.714h13.714Z",
  kick:
    "M1.333 0h8v5.333H12V2.667h2.667V0h8v8H20v2.667h-2.667v2.666H20V16h2.667v8h-8v-2.667H12v-2.666H9.333V24h-8Z",
};

export function VideoSourceIcon({
  source,
  className,
  "aria-hidden": ariaHidden,
  ...props
}: { source: VideoSourceKind } & SVGProps<SVGSVGElement>) {
  const label = ariaHidden ? undefined : VIDEO_SOURCE_LABELS[source];
  if (source === "upload") {
    return <Upload className={cn("size-4 shrink-0", className)} aria-hidden={ariaHidden} aria-label={label} />;
  }
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      role={ariaHidden ? undefined : "img"}
      aria-hidden={ariaHidden}
      aria-label={label}
      data-source={source}
      className={cn("size-4 shrink-0", className)}
      {...props}
    >
      <path d={PATHS[source]} />
    </svg>
  );
}
