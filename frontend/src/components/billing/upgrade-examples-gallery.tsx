"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { useReducedMotion } from "motion/react";
import { ArrowUpRight } from "lucide-react";

import { cn } from "@/lib/utils";

export const UPGRADE_COLLAGE_VIDEO_SRC = "/videos/upgrade-collage.mp4";
export const UPGRADE_COLLAGE_POSTER_SRC = "/videos/upgrade-collage-poster.jpg";

/**
 * Single programmatically rendered, seamlessly looping collage of real SupoClip
 * vertical clips shown beneath the monthly hours headline in the upgrade dialog.
 * Falls back to a crisp static poster under `prefers-reduced-motion: reduce`.
 */
export function UpgradeExamplesGallery({ className }: { className?: string }) {
  const t = useTranslations("billing");
  const reduceMotion = useReducedMotion();
  const videoRef = useRef<HTMLVideoElement>(null);

  // Keep `muted` synced on the DOM node because React does not reliably reflect it as an attribute.
  useEffect(() => {
    const video = videoRef.current;
    if (reduceMotion || !video) return;
    video.muted = true;
    void Promise.resolve(video.play()).catch(() => undefined);
  }, [reduceMotion]);

  return (
    <div
      role="region"
      aria-label={t("gallery.regionLabel")}
      data-testid="upgrade-examples-gallery"
      className={cn("relative min-h-0 flex-1 overflow-hidden", className)}
    >
      <div className="upgrade-gallery-mask absolute inset-0 overflow-hidden">
        {reduceMotion ? (
          /* eslint-disable-next-line @next/next/no-img-element -- static poster fallback for reduced motion */
          <img
            src={UPGRADE_COLLAGE_POSTER_SRC}
            alt=""
            decoding="async"
            className="size-full object-cover"
          />
        ) : (
          <video
            ref={videoRef}
            src={UPGRADE_COLLAGE_VIDEO_SRC}
            poster={UPGRADE_COLLAGE_POSTER_SRC}
            autoPlay
            muted
            loop
            playsInline
            preload="metadata"
            aria-hidden="true"
            className="size-full object-cover"
          />
        )}
        {/* Progressive edge blurs (top, bottom, and gentle sides) layered inside the opacity mask */}
        <div aria-hidden="true" className="upgrade-gallery-blur-top-soft pointer-events-none absolute inset-x-0 top-0 h-20" />
        <div aria-hidden="true" className="upgrade-gallery-blur-top-strong pointer-events-none absolute inset-x-0 top-0 h-14" />
        <div aria-hidden="true" className="upgrade-gallery-blur-bottom-soft pointer-events-none absolute inset-x-0 bottom-0 h-20" />
        <div aria-hidden="true" className="upgrade-gallery-blur-bottom-strong pointer-events-none absolute inset-x-0 bottom-0 h-14" />
        <div aria-hidden="true" className="upgrade-gallery-blur-left pointer-events-none absolute inset-y-0 left-0 w-7" />
        <div aria-hidden="true" className="upgrade-gallery-blur-right pointer-events-none absolute inset-y-0 right-0 w-7" />
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
