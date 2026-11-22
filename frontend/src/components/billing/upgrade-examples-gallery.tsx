"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { ArrowUpRight } from "lucide-react";

import {
  buildGalleryColumns,
  LOCAL_EXAMPLE_CLIPS,
  type ExamplesGalleryPayload,
  type GalleryClip,
} from "@/lib/example-gallery";
import { formatClipLength } from "@/lib/examples";
import { cn } from "@/lib/utils";

/** Module-level cache so reopening the upgrade dialog reuses the fetched catalog without re-fetching. */
let cachedManifestClips: GalleryClip[] | null = null;
let hasFetchedCatalog = false;
let inflightCatalog: Promise<GalleryClip[] | null> | null = null;

/** Resets module-level gallery cache (for unit tests). */
export function resetUpgradeExamplesGalleryCache() {
  cachedManifestClips = null;
  hasFetchedCatalog = false;
  inflightCatalog = null;
}

async function loadRemoteGalleryClips(): Promise<GalleryClip[] | null> {
  if (cachedManifestClips) return cachedManifestClips;
  if (hasFetchedCatalog) return null;
  if (inflightCatalog) return inflightCatalog;

  inflightCatalog = fetch("/api/examples")
    .then(async (response) => {
      if (!response.ok) return null;
      const data = (await response.json()) as Partial<ExamplesGalleryPayload> | null;
      if (data?.source === "manifest" && Array.isArray(data.clips) && data.clips.length > 0) {
        cachedManifestClips = data.clips;
        return data.clips;
      }
      return null;
    })
    .catch(() => null)
    .finally(() => {
      hasFetchedCatalog = true;
      inflightCatalog = null;
    });

  return inflightCatalog;
}

function canHoverPreview() {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return (
    window.matchMedia("(hover: hover) and (pointer: fine)").matches &&
    !window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

const COLUMN_CONFIG = [
  { direction: "up" as const, duration: "34s", delay: "-5s", visibilityClass: "" },
  { direction: "down" as const, duration: "42s", delay: "-14s", visibilityClass: "" },
  { direction: "up" as const, duration: "38s", delay: "-9s", visibilityClass: "hidden md:block" },
] as const;

interface GalleryClipCardProps {
  clip: GalleryClip;
  groupLabel: string;
  previewing: boolean;
  decorative?: boolean;
  onPreviewStart: () => void;
  onPreviewEnd: () => void;
}

function GalleryClipCard({
  clip,
  groupLabel,
  previewing,
  decorative,
  onPreviewStart,
  onPreviewEnd,
}: GalleryClipCardProps) {
  const videoRef = useRef<HTMLVideoElement>(null);

  // Keep `muted` synced on the DOM node because React does not reliably reflect it as an attribute.
  useEffect(() => {
    const video = videoRef.current;
    if (!previewing || !video) return;
    video.muted = true;
    void Promise.resolve(video.play()).catch(() => undefined);
  }, [previewing]);

  return (
    <div
      data-testid={decorative ? undefined : "upgrade-gallery-card"}
      data-clip-id={clip.id}
      className={cn(
        "relative aspect-[9/16] w-full overflow-hidden rounded-lg bg-zinc-950/90 shadow-sm ring-1 ring-black/10 transition-transform duration-200 dark:ring-white/10",
        previewing && "scale-[1.02] ring-foreground/35 shadow-md",
      )}
      onMouseEnter={onPreviewStart}
      onMouseLeave={onPreviewEnd}
    >
      {/* eslint-disable-next-line @next/next/no-img-element -- mix of local and remote 9:16 posters */}
      <img
        src={clip.posterUrl}
        alt={decorative ? "" : clip.title}
        decoding="async"
        className="absolute inset-0 size-full object-cover"
      />
      {previewing && (
        <video
          ref={videoRef}
          src={clip.videoUrl}
          poster={clip.posterUrl}
          autoPlay
          muted
          loop
          playsInline
          preload="metadata"
          aria-hidden="true"
          className="absolute inset-0 size-full object-cover"
        />
      )}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-0 h-14 bg-gradient-to-t from-black/75 via-black/25 to-transparent"
      />
      <div className="pointer-events-none absolute inset-x-1.5 bottom-1.5 flex items-center justify-between gap-1">
        <span className="truncate rounded-full bg-black/55 px-1.5 py-0.5 text-[9px] font-medium leading-tight text-white/90 backdrop-blur-xs">
          {groupLabel}
        </span>
        {clip.viralityScore !== null ? (
          <span className="shrink-0 rounded-full bg-emerald-500/25 px-1.5 py-0.5 text-[9px] font-semibold leading-tight tabular-nums text-emerald-200 ring-1 ring-emerald-400/35 backdrop-blur-xs">
            {clip.viralityScore}
          </span>
        ) : (
          <span className="shrink-0 rounded-full bg-black/55 px-1.5 py-0.5 font-mono text-[9px] leading-tight tabular-nums text-white/85 backdrop-blur-xs">
            {formatClipLength(clip.durationSeconds)}
          </span>
        )}
      </div>
    </div>
  );
}

/**
 * Moving 9:16 collage of real SupoClip clips shown in the upgrade modal.
 * Seeds instantly from bundled local clips, hydrates asynchronously from `/api/examples`,
 * pauses on hover, respects `prefers-reduced-motion`, and previews a single card's video on hover.
 */
export function UpgradeExamplesGallery({ className }: { className?: string }) {
  const t = useTranslations("billing");
  const [clips, setClips] = useState<GalleryClip[]>(() => cachedManifestClips ?? LOCAL_EXAMPLE_CLIPS);
  const [hoveredSlot, setHoveredSlot] = useState<string | null>(null);

  useEffect(() => {
    if (cachedManifestClips || hasFetchedCatalog) return;
    let cancelled = false;
    void loadRemoteGalleryClips().then((remote) => {
      if (!cancelled && remote) {
        setClips(remote);
      }
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const columns = useMemo(() => buildGalleryColumns(clips, COLUMN_CONFIG.length, 4), [clips]);

  return (
    <div
      role="region"
      aria-label={t("gallery.regionLabel")}
      data-testid="upgrade-examples-gallery"
      className={cn("upgrade-gallery relative min-h-0 flex-1 overflow-hidden", className)}
      onMouseLeave={() => setHoveredSlot(null)}
    >
      <div className="upgrade-gallery-mask absolute inset-0 px-3">
        <div className="grid h-full grid-cols-2 gap-2.5 md:grid-cols-3">
          {columns.map((columnClips, colIndex) => {
            const config = COLUMN_CONFIG[colIndex];
            return (
              <div key={colIndex} className={cn("min-w-0 overflow-hidden", config.visibilityClass)}>
                <div
                  className={cn(
                    "upgrade-gallery-track flex flex-col",
                    config.direction === "up" ? "upgrade-gallery-up" : "upgrade-gallery-down",
                  )}
                  style={
                    {
                      "--gallery-duration": config.duration,
                      animationDelay: config.delay,
                    } as React.CSSProperties
                  }
                >
                  {(["primary", "duplicate"] as const).map((half) => (
                    <div
                      key={half}
                      aria-hidden={half === "duplicate" ? true : undefined}
                      className="flex flex-col gap-2.5 pb-2.5"
                    >
                      {columnClips.map((clip, itemIndex) => {
                        const slotKey = `${colIndex}-${half}-${itemIndex}-${clip.id}`;
                        return (
                          <GalleryClipCard
                            key={slotKey}
                            clip={clip}
                            groupLabel={t(`gallery.groups.${clip.group}`)}
                            previewing={hoveredSlot === slotKey}
                            decorative={half === "duplicate"}
                            onPreviewStart={() => {
                              if (canHoverPreview()) setHoveredSlot(slotKey);
                            }}
                            onPreviewEnd={() => {
                              setHoveredSlot((current) => (current === slotKey ? null : current));
                            }}
                          />
                        );
                      })}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <Link
        href="/examples"
        target="_blank"
        rel="noopener noreferrer"
        className="absolute bottom-3 right-3 z-10 inline-flex items-center gap-1 rounded-full border border-border/80 bg-background/85 px-2.5 py-1 text-[11px] font-medium text-foreground shadow-sm backdrop-blur-md transition-colors hover:bg-background"
      >
        {t("gallery.viewAll")}
        <ArrowUpRight className="size-3 text-muted-foreground" aria-hidden />
      </Link>
    </div>
  );
}
