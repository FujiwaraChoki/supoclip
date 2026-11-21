"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { ExternalLink, Play } from "lucide-react";

import { cn } from "@/lib/utils";
import { EXAMPLE_GROUPS, formatClipLength, type ExampleClip, type ExampleGroup, type ExampleSource } from "@/lib/examples";

type Filter = "all" | ExampleGroup;
type Playback = "idle" | "preview" | "playing";

function canHoverPreview() {
  if (typeof window === "undefined" || !window.matchMedia) return false;
  return window.matchMedia("(hover: hover) and (pointer: fine)").matches && !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

export function ExampleCard({ source, clip }: { source: ExampleSource; clip: ExampleClip }) {
  const t = useTranslations("examples");
  const templates = useTranslations("editor.captionTemplates.names");
  const [playback, setPlayback] = useState<Playback>("idle");
  const videoRef = useRef<HTMLVideoElement>(null);

  // `muted` is not reflected reliably as a React prop, so keep the element in sync by hand.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    video.muted = playback === "preview";
    // Older engines return nothing from play(); a rejected autoplay just leaves the poster up.
    void Promise.resolve(video.play()).catch(() => undefined);
  }, [playback]);

  return (
    <article id={clip.id} data-testid="example-card" className="group scroll-mt-24">
      <div
        className="relative mx-auto aspect-[9/16] w-full overflow-hidden rounded-xl bg-black shadow-lg ring-1 ring-border"
        onMouseEnter={() => {
          if (playback === "idle" && canHoverPreview()) setPlayback("preview");
        }}
        onMouseLeave={() => {
          if (playback === "preview") setPlayback("idle");
        }}
      >
        {/* eslint-disable-next-line @next/next/no-img-element -- remote 720x1280 poster, already sized for the web */}
        <img
          src={clip.posterUrl}
          alt=""
          width={clip.width}
          height={clip.height}
          loading="lazy"
          decoding="async"
          className="absolute inset-0 size-full object-cover"
        />
        {playback !== "idle" && (
          <video
            ref={videoRef}
            src={clip.videoUrl}
            poster={clip.posterUrl}
            preload="none"
            playsInline
            autoPlay
            muted={playback === "preview"}
            loop={playback === "preview"}
            controls={playback === "playing"}
            width={clip.width}
            height={clip.height}
            className="absolute inset-0 size-full object-cover"
            aria-label={t("card.videoLabel", { title: clip.title })}
          />
        )}
        {playback !== "playing" && (
          <button
            type="button"
            onClick={() => setPlayback("playing")}
            className="absolute inset-0 flex items-center justify-center bg-gradient-to-t from-black/45 via-transparent to-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
            aria-label={t("card.play", { title: clip.title })}
          >
            <span
              className={cn(
                "flex size-14 items-center justify-center rounded-full bg-white/90 text-black shadow-lg transition-opacity",
                playback === "preview" && "opacity-0",
              )}
            >
              <Play aria-hidden className="ml-0.5 size-6 fill-current" />
            </span>
            <span className="absolute bottom-3 right-3 rounded-md bg-black/70 px-1.5 py-0.5 font-mono text-xs tabular-nums text-white">
              {formatClipLength(clip.durationSeconds)}
            </span>
          </button>
        )}
      </div>

      <h3 className="mt-4 text-base font-semibold leading-snug">{clip.title}</h3>
      <dl className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        <div className="flex items-center gap-1">
          <dt className="sr-only">{t("card.lengthLabel")}</dt>
          <dd className="tabular-nums">{formatClipLength(clip.durationSeconds)}</dd>
        </div>
        <div className="flex items-center gap-1" title={t("card.viralityHint")}>
          <dt>{t("card.viralityLabel")}</dt>
          <dd className="rounded-full bg-emerald-500/10 px-1.5 py-0.5 font-semibold tabular-nums text-emerald-700 dark:text-emerald-400">
            {clip.viralityScore}
          </dd>
        </div>
        <div className="flex items-center gap-1">
          <dt>{t("card.captionsLabel")}</dt>
          <dd className="font-medium text-foreground">{templates(source.captionStyle)}</dd>
        </div>
      </dl>
      <p className="mt-2 line-clamp-2 text-sm italic leading-6 text-muted-foreground">“{clip.excerpt}”</p>
      <p className="mt-2 text-xs leading-5 text-muted-foreground">
        {t.rich("card.credit", {
          channel: source.channel,
          strong: (chunks) => <span className="font-medium text-foreground">{chunks}</span>,
        })}{" "}
        <a
          href={source.url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-foreground underline underline-offset-4"
        >
          {t("card.watchOriginal")} <ExternalLink aria-hidden className="size-3" />
          <span className="sr-only">{t("card.originalLabel", { title: source.title })}</span>
        </a>
      </p>
    </article>
  );
}

export function ExamplesCatalog({ sources }: { sources: ExampleSource[] }) {
  const t = useTranslations("examples");
  const [filter, setFilter] = useState<Filter>("all");
  const groups = EXAMPLE_GROUPS.filter((group) => sources.some((source) => source.group === group));
  const count = (group: Filter) =>
    sources.filter((source) => group === "all" || source.group === group).reduce((sum, source) => sum + source.clips.length, 0);
  const shown = filter === "all" ? groups : [filter];

  return (
    <div>
      <div role="group" aria-label={t("filters.label")} className="flex flex-wrap gap-2">
        {(["all", ...groups] as Filter[]).map((option) => (
          <button
            key={option}
            type="button"
            aria-pressed={filter === option}
            onClick={() => setFilter(option)}
            className={cn(
              "inline-flex items-center gap-2 rounded-full border px-4 py-1.5 text-sm transition-colors",
              filter === option ? "border-foreground bg-foreground text-background" : "text-muted-foreground hover:border-foreground/40 hover:text-foreground",
            )}
          >
            {option === "all" ? t("filters.all") : t(`groups.${option}.label`)}
            <span className="tabular-nums opacity-70">{count(option)}</span>
          </button>
        ))}
      </div>

      {shown.map((group) => (
        <section key={group} aria-labelledby={`examples-${group}`} className="mt-12 first-of-type:mt-10">
          <h2 id={`examples-${group}`} className="text-2xl font-bold tracking-tight">{t(`groups.${group}.label`)}</h2>
          <p className="mt-1 max-w-2xl text-sm leading-6 text-muted-foreground">{t(`groups.${group}.description`)}</p>
          <div className="mt-6 grid grid-cols-2 gap-x-4 gap-y-8 sm:gap-x-6 sm:gap-y-10 lg:grid-cols-3 xl:grid-cols-4">
            {sources
              .filter((source) => source.group === group)
              .flatMap((source) => source.clips.map((clip) => <ExampleCard key={clip.id} source={source} clip={clip} />))}
          </div>
        </section>
      ))}
    </div>
  );
}
