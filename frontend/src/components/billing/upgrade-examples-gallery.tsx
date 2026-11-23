"use client";

import { useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useReducedMotion } from "motion/react";
import { ArrowUpRight } from "lucide-react";

import {
  buildGalleryColumns,
  LOCAL_EXAMPLE_CLIPS,
  type ExamplesGalleryPayload,
  type GalleryClip,
} from "@/lib/example-gallery";
import { cn } from "@/lib/utils";

export const examplesGalleryKey = ["examples-gallery"] as const;

/** The live catalog, or null (bundled clips stay) when it is missing, empty or fails. Never throws. */
async function loadRemoteGalleryClips(): Promise<GalleryClip[] | null> {
  try {
    const response = await fetch("/api/examples");
    if (!response.ok) return null;
    const data = (await response.json()) as Partial<ExamplesGalleryPayload> | null;
    return data?.source === "manifest" && Array.isArray(data.clips) && data.clips.length > 0 ? data.clips : null;
  } catch {
    return null;
  }
}

const COLUMN_CONFIG = [
  { direction: "up" as const, duration: "34s", delay: "-4s" },
  { direction: "down" as const, duration: "38s", delay: "-17s" },
] as const;

/**
 * Six overlapping progressive-blur bands per edge so the transition from crisp center
 * to soft blurred edge has zero visible step seams.
 */
const PROGRESSIVE_BLUR_LAYERS = [
  { heightClass: "h-[22%]", blurPx: 0.75, solidStop: "55%" },
  { heightClass: "h-[18%]", blurPx: 1.5, solidStop: "50%" },
  { heightClass: "h-[14.5%]", blurPx: 3, solidStop: "45%" },
  { heightClass: "h-[11%]", blurPx: 5, solidStop: "40%" },
  { heightClass: "h-[7.5%]", blurPx: 8, solidStop: "35%" },
  { heightClass: "h-[4.5%]", blurPx: 12, solidStop: "0%" },
] as const;

function ProgressiveEdgeBlur({ edge }: { edge: "top" | "bottom" }) {
  const gradientDir = edge === "top" ? "to bottom" : "to top";
  return (
    <>
      {PROGRESSIVE_BLUR_LAYERS.map((layer, index) => {
        const mask =
          layer.solidStop === "0%"
            ? `linear-gradient(${gradientDir}, black 0%, transparent 100%)`
            : `linear-gradient(${gradientDir}, black 0%, black ${layer.solidStop}, transparent 100%)`;
        return (
          <div
            key={`${edge}-${index}`}
            aria-hidden="true"
            className={cn(
              "pointer-events-none absolute inset-x-0",
              edge === "top" ? "top-0" : "bottom-0",
              layer.heightClass,
            )}
            style={{
              backdropFilter: `blur(${layer.blurPx}px)`,
              WebkitBackdropFilter: `blur(${layer.blurPx}px)`,
              maskImage: mask,
              WebkitMaskImage: mask,
            }}
          />
        );
      })}
    </>
  );
}

function GalleryVideoCard({
  clip,
  reduceMotion,
  decorative,
}: {
  clip: GalleryClip;
  reduceMotion: boolean;
  decorative?: boolean;
}) {
  const videoRef = useRef<HTMLVideoElement>(null);

  // Keep `muted` synced on the DOM node because React does not reliably reflect it as an attribute.
  useEffect(() => {
    const video = videoRef.current;
    if (reduceMotion || !video) return;
    video.muted = true;
    void Promise.resolve(video.play()).catch(() => undefined);
  }, [reduceMotion, clip.videoUrl]);

  return (
    <div
      data-testid={decorative ? undefined : "upgrade-gallery-card"}
      data-clip-id={clip.id}
      className="relative aspect-[9/16] w-full overflow-hidden rounded-2xl bg-black/5 shadow-sm ring-1 ring-border/60 dark:bg-white/5"
    >
      {reduceMotion ? (
        /* eslint-disable-next-line @next/next/no-img-element -- crisp poster fallback for reduced motion */
        <img
          src={clip.posterUrl}
          alt={decorative ? "" : clip.title}
          decoding="async"
          className="size-full object-cover"
        />
      ) : (
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
          className="size-full object-cover"
        />
      )}
    </div>
  );
}

/**
 * Moving 2-column collage of real live-playing SupoClip vertical videos shown in the upgrade modal.
 * Seeds instantly from bundled local clips, hydrates asynchronously from `/api/examples`,
 * pauses on hover, and falls back to static posters under `prefers-reduced-motion: reduce`.
 */
export function UpgradeExamplesGallery({ className }: { className?: string }) {
  const t = useTranslations("billing");
  const reduceMotion = Boolean(useReducedMotion());
  // Asked once per page load: reopening the upgrade dialog reuses the answer, a failure included.
  const { data: remoteClips } = useQuery({
    queryKey: examplesGalleryKey,
    queryFn: loadRemoteGalleryClips,
    staleTime: Infinity,
    gcTime: Infinity,
  });
  const clips = remoteClips ?? LOCAL_EXAMPLE_CLIPS;

  const columns = useMemo(() => buildGalleryColumns(clips, COLUMN_CONFIG.length, 3), [clips]);

  return (
    <div
      role="region"
      aria-label={t("gallery.regionLabel")}
      data-testid="upgrade-examples-gallery"
      className={cn("upgrade-gallery relative min-h-0 flex-1 overflow-hidden", className)}
    >
      <div className="upgrade-gallery-mask absolute inset-0 overflow-hidden px-5">
        <div className="grid h-full grid-cols-2 gap-3">
          {columns.map((columnClips, colIndex) => {
            const config = COLUMN_CONFIG[colIndex];
            const halves = reduceMotion ? (["primary"] as const) : (["primary", "duplicate"] as const);
            return (
              <div key={colIndex} className="min-w-0 overflow-hidden">
                <div
                  className={cn(
                    "upgrade-gallery-track flex flex-col",
                    !reduceMotion && (config.direction === "up" ? "upgrade-gallery-up" : "upgrade-gallery-down"),
                  )}
                  style={
                    reduceMotion
                      ? undefined
                      : ({
                          "--gallery-duration": config.duration,
                          animationDelay: config.delay,
                        } as React.CSSProperties)
                  }
                >
                  {halves.map((half) => (
                    <div
                      key={half}
                      aria-hidden={half === "duplicate" ? true : undefined}
                      className="flex flex-col gap-3 pb-3"
                    >
                      {columnClips.map((clip, itemIndex) => (
                        <GalleryVideoCard
                          key={`${colIndex}-${half}-${itemIndex}-${clip.id}`}
                          clip={clip}
                          reduceMotion={reduceMotion}
                          decorative={half === "duplicate"}
                        />
                      ))}
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>

        {/* Stepped 6-layer progressive blurs at top and bottom edges only (crisp left/right margins) */}
        <ProgressiveEdgeBlur edge="top" />
        <ProgressiveEdgeBlur edge="bottom" />
      </div>

      <Link
        href="/examples"
        target="_blank"
        rel="noopener noreferrer"
        className="absolute bottom-3 right-5 z-10 inline-flex items-center gap-1 rounded-full border border-border/80 bg-background/90 px-2.5 py-1 text-[11px] font-medium text-foreground shadow-sm backdrop-blur-md transition-colors hover:bg-background"
      >
        {t("gallery.viewAll")}
        <ArrowUpRight className="size-3 text-muted-foreground" aria-hidden />
      </Link>
    </div>
  );
}
