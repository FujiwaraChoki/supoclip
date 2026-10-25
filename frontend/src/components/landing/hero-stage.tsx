"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { motion } from "motion/react";
import { ArrowRight, CalendarClock, Captions, Check, Play, Target, Upload, Volume2, VolumeX } from "lucide-react";

import { SocialProviderIcon } from "@/components/social-provider-icon";
import { VIDEO_SOURCE_LABELS, VideoSourceIcon } from "@/components/video-source-icon";
import { DEMO_CLIPS, DEMO_SOURCE } from "@/lib/demo";
import { cn } from "@/lib/utils";

// The timeline only positions the moments; no source length is shown.
const TIMELINE_SPAN_SECONDS = 1500;
const BAR_COUNT = 48;
const BARS = Array.from({ length: BAR_COUNT }, (_, i) =>
  0.18 + 0.82 * Math.abs(Math.sin(i * 1.73) * Math.cos(i * 0.29 + 1)),
);
// Illustrative virality scores and publish slots for each demo clip (a Tuesday and a Thursday).
const SCORES = [92, 87];
const SCHEDULE_SLOTS = [new Date(2026, 9, 27, 18, 0), new Date(2026, 9, 29, 12, 30)];

function durationSeconds(duration: string) {
  const [minutes, seconds] = duration.split(":").map(Number);
  return minutes * 60 + seconds;
}

const MOMENTS = DEMO_CLIPS.map((clip) => {
  const length = durationSeconds(clip.duration);
  return {
    clip,
    left: clip.startSeconds / TIMELINE_SPAN_SECONDS,
    width: length / TIMELINE_SPAN_SECONDS,
  };
});

// Where a source can come from, fanned out like stickers above the hero headline.
const SOURCE_STICKERS = [
  { source: "youtube", className: "bg-[#ff0033] text-white", rotate: "-rotate-12", delay: "0s" },
  { source: "twitch", className: "bg-[#9146ff] text-white", rotate: "rotate-3", delay: "0.6s" },
  { source: "kick", className: "bg-[#53fc18] text-black", rotate: "-rotate-6", delay: "1.2s" },
  { source: "upload", className: "bg-foreground text-background", rotate: "rotate-12", delay: "1.8s" },
] as const;

export function SourceStickers({ className }: { className?: string }) {
  return (
    <div className={cn("group/stickers flex items-center justify-center", className)}>
      {SOURCE_STICKERS.map((sticker, i) => (
        <span
          key={sticker.source}
          title={VIDEO_SOURCE_LABELS[sticker.source]}
          className={cn(
            "flex size-12 animate-[landing-float_4s_ease-in-out_infinite] items-center justify-center rounded-2xl shadow-[0_12px_30px_-10px_rgb(28_25_23/0.45)] ring-4 ring-background transition-all duration-500 ease-out sm:size-16 sm:rounded-[20px]",
            i > 0 && "-ml-3 sm:-ml-4 group-hover/stickers:ml-1",
            sticker.rotate,
            "group-hover/stickers:rotate-0",
            sticker.className,
          )}
          style={{ zIndex: SOURCE_STICKERS.length - i, animationDelay: sticker.delay }}
        >
          {sticker.source === "upload" ? (
            <Upload className="size-5 sm:size-7" aria-label={VIDEO_SOURCE_LABELS.upload} />
          ) : (
            <VideoSourceIcon source={sticker.source} className="size-5 sm:size-7" />
          )}
        </span>
      ))}
    </div>
  );
}

/** A thin connector with a dot travelling along it, from one step to the next. */
function Connector({ delay }: { delay: number }) {
  return (
    <div aria-hidden className="relative hidden h-px min-w-10 flex-1 bg-border lg:block">
      <motion.span
        className="absolute -top-[3px] size-[7px] rounded-full bg-foreground"
        initial={{ left: "0%", opacity: 0 }}
        animate={{ left: ["0%", "100%"], opacity: [0, 1, 1, 0] }}
        transition={{ duration: 1.6, delay, repeat: Infinity, repeatDelay: 1.4, ease: "easeInOut" }}
      />
    </div>
  );
}

function StepLabel({ n, children }: { n: number; children: React.ReactNode }) {
  return (
    <p className="mt-4 flex items-center justify-center gap-2 text-sm text-muted-foreground">
      <span className="flex size-5 items-center justify-center rounded-full bg-foreground text-[11px] font-semibold text-background">{n}</span>
      {children}
    </p>
  );
}

/** The hero in three steps: a long video, the clip SupoClip cut from it, the scheduled post. */
export function HeroStage() {
  const t = useTranslations("marketing.hero.stage");
  const locale = useLocale();
  const videoRefs = useRef<(HTMLVideoElement | null)[]>([]);
  const [active, setActive] = useState(0);
  const [muted, setMuted] = useState(true);
  const [playing, setPlaying] = useState(true);
  const [progress, setProgress] = useState(0);
  const slotFormat = new Intl.DateTimeFormat(locale, { weekday: "short", hour: "numeric", minute: "2-digit" });

  // Only the active clip plays; the others stay parked at frame zero.
  useEffect(() => {
    setProgress(0);
    videoRefs.current.forEach((video, i) => {
      if (!video) return;
      video.currentTime = 0;
      if (i !== active) {
        video.pause();
        return;
      }
      video
        .play()
        .then(() => setPlaying(true))
        .catch(() => setPlaying(false));
    });
  }, [active]);

  // React can drop the `muted` attribute on hydration, so set the property too.
  useEffect(() => {
    videoRefs.current.forEach((video) => {
      if (video) video.muted = muted;
    });
  }, [muted]);

  const togglePlay = () => {
    const video = videoRefs.current[active];
    if (!video) return;
    if (video.paused) {
      video
        .play()
        .then(() => setPlaying(true))
        .catch(() => setPlaying(false));
    } else {
      video.pause();
      setPlaying(false);
    }
  };

  const moment = MOMENTS[active];
  const clip = moment.clip;

  return (
    <div className="relative mx-auto w-full max-w-6xl overflow-hidden rounded-[32px] border bg-canvas px-5 py-10 sm:px-8 lg:px-10 lg:py-14">
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.06] [background-image:radial-gradient(circle_at_1px_1px,currentColor_1px,transparent_0)] [background-size:22px_22px] [mask-image:radial-gradient(ellipse_at_center,black,transparent_75%)]"
      />
      <div className="relative flex flex-col items-center gap-10 lg:flex-row lg:items-center lg:gap-0">

        {/* 1 · The long video */}
        <div className="w-full max-w-[320px] shrink-0">
          <a
            href={DEMO_SOURCE.url}
            target="_blank"
            rel="noopener noreferrer"
            className="block rounded-2xl border bg-background p-2.5 shadow-[0_20px_50px_-30px_rgb(28_25_23/0.35)] transition-colors hover:border-foreground/20"
          >
            <div className="relative aspect-video overflow-hidden rounded-xl bg-black">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src="/clips/source.jpg" alt="" className="size-full object-cover" />
              <span className="absolute bottom-2 left-2 flex items-center gap-1 rounded-md bg-black/70 px-1.5 py-0.5 text-[10px] font-medium text-white backdrop-blur">
                <SocialProviderIcon provider="youtube" className="size-3" />
                {t("longForm")}
              </span>
            </div>
            <p className="mt-3 line-clamp-2 px-1.5 text-[13px] font-medium leading-snug">{DEMO_SOURCE.title}</p>
            <div className="relative mx-1.5 mb-1.5 mt-8">
              <div className="flex h-10 items-end gap-[2px]" aria-hidden>
                {BARS.map((height, i) => {
                  const at = (i + 0.5) / BAR_COUNT;
                  const inMoment = at >= moment.left - 0.01 && at <= moment.left + moment.width + 0.01;
                  return (
                    <span
                      key={i}
                      className={cn("flex-1 rounded-full transition-colors duration-500", inMoment ? "bg-brand" : "bg-foreground/12")}
                      style={{ height: `${Math.round(height * 100)}%` }}
                    />
                  );
                })}
              </div>
              <span
                className="absolute -top-6 -translate-x-1/2 rounded-full bg-foreground px-1.5 py-px font-mono text-[10px] tabular-nums text-background transition-[left] duration-500"
                style={{ left: `${Math.round((moment.left + moment.width / 2) * 1000) / 10}%` }}
              >
                {clip.range.split(" ")[0]}
              </span>
            </div>
          </a>
          <StepLabel n={1}>{t("steps.source")}</StepLabel>
        </div>

        <Connector delay={0} />

        {/* 2 · The clip */}
        <div className="flex shrink-0 flex-col items-center lg:px-2">
          <div className="relative w-[220px] rounded-[2.1rem] bg-foreground p-[7px] shadow-2xl sm:w-[236px]">
            <span key={`score-${active}`} className="animate-rise absolute -left-6 top-16 z-10 flex items-center gap-1.5 rounded-full border bg-background py-1 pl-1.5 pr-2.5 text-xs font-medium shadow-lg sm:-left-14">
              <span className="flex size-6 items-center justify-center rounded-full bg-brand-soft text-brand"><Target className="size-3.5" /></span>
              <span className="tabular-nums">{SCORES[active]}</span>
              <span className="text-muted-foreground">/100</span>
            </span>
            <span className="absolute -right-6 top-[44%] z-10 flex items-center gap-1.5 rounded-full border bg-background py-1 pl-1.5 pr-2.5 text-xs font-medium shadow-lg sm:-right-16">
              <span className="flex size-6 items-center justify-center rounded-full bg-muted"><Captions className="size-3.5" /></span>
              {t("captions")}
            </span>
            <div className="relative overflow-hidden rounded-[1.65rem] bg-black" style={{ aspectRatio: "9/16" }}>
              {DEMO_CLIPS.map((item, i) => (
                <video
                  key={item.src}
                  ref={(el) => {
                    videoRefs.current[i] = el;
                  }}
                  src={item.src}
                  poster={item.poster}
                  muted
                  playsInline
                  preload={i === 0 ? "metadata" : "none"}
                  aria-label={item.hook}
                  className={cn(
                    "absolute inset-0 size-full object-cover transition-opacity duration-500",
                    i === active ? "opacity-100" : "opacity-0",
                  )}
                  onTimeUpdate={(e) => {
                    if (i !== active) return;
                    const el = e.currentTarget;
                    if (el.duration) setProgress(el.currentTime / el.duration);
                  }}
                  onEnded={() => setActive((prev) => (prev + 1) % DEMO_CLIPS.length)}
                />
              ))}
              <button
                type="button"
                onClick={togglePlay}
                aria-label={playing ? t("pause") : t("play")}
                className="absolute inset-0 flex items-center justify-center focus:outline-none"
              >
                <span
                  className={cn(
                    "flex size-12 items-center justify-center rounded-full bg-white/85 shadow-lg backdrop-blur-sm transition-opacity duration-200",
                    playing ? "opacity-0" : "opacity-100",
                  )}
                >
                  <Play className="ml-0.5 size-5 text-black" />
                </span>
              </button>
              <button
                type="button"
                onClick={() => setMuted((m) => !m)}
                aria-label={muted ? t("unmute") : t("mute")}
                className="absolute right-2.5 top-2.5 flex size-8 items-center justify-center rounded-full bg-black/45 text-white/90 backdrop-blur-sm transition-colors hover:bg-black/65"
              >
                {muted ? <VolumeX className="size-3.5" /> : <Volume2 className="size-3.5" />}
              </button>
              <div className="pointer-events-none absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent px-3 pb-3 pt-10">
                <div className="h-[3px] overflow-hidden rounded-full bg-white/25">
                  <div className="h-full bg-white" style={{ width: `${Math.min(progress * 100, 100)}%` }} />
                </div>
              </div>
            </div>
          </div>
          <StepLabel n={2}>{t("steps.clip")}</StepLabel>
          <div className="mt-3 flex items-center gap-3">
            <div className="flex gap-1.5">
              {DEMO_CLIPS.map((item, i) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setActive(i)}
                  aria-label={item.hook}
                  aria-pressed={i === active}
                  className={cn("h-1.5 rounded-full transition-all", i === active ? "w-5 bg-foreground" : "w-1.5 bg-foreground/25 hover:bg-foreground/50")}
                />
              ))}
            </div>
            <Link href="/demo" className="inline-flex items-center gap-1 text-xs text-muted-foreground transition-colors hover:text-foreground">
              {t("howMade")}
              <ArrowRight className="size-3" />
            </Link>
          </div>
        </div>

        <Connector delay={0.8} />

        {/* 3 · The scheduled post */}
        <div className="w-full max-w-[320px] shrink-0">
          <div className="rounded-2xl border bg-background p-4 shadow-[0_20px_50px_-30px_rgb(28_25_23/0.35)]">
            <div className="flex items-center gap-2 text-[13px] font-medium">
              <SocialProviderIcon provider="youtube" className="size-4 text-[#ff0033]" />
              <span className="flex-1">{t("youtubeShorts")}</span>
              <span className="flex size-4 items-center justify-center rounded-full bg-foreground text-background">
                <Check className="size-2.5" strokeWidth={3} />
              </span>
            </div>
            <div className="mt-3 space-y-1.5">
              {DEMO_CLIPS.map((item, i) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => setActive(i)}
                  className={cn(
                    "flex w-full items-center gap-3 rounded-xl p-2 text-left transition-all",
                    i === active ? "bg-muted ring-1 ring-border" : "opacity-60 hover:opacity-100",
                  )}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={item.poster} alt="" className="h-14 w-8 shrink-0 rounded-md object-cover" />
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2 text-[13px] leading-snug">{item.hook}</span>
                    <span className="mt-1 flex items-center gap-1.5 text-[11px] tabular-nums text-muted-foreground">
                      <CalendarClock className="size-3" />
                      {slotFormat.format(SCHEDULE_SLOTS[i % SCHEDULE_SLOTS.length])}
                    </span>
                  </span>
                </button>
              ))}
            </div>
            <div className="mt-3 flex items-center justify-between border-t px-1 pt-3 text-xs text-muted-foreground">
              <span>{t("queued", { count: DEMO_CLIPS.length })}</span>
              <span className="flex items-center gap-1 rounded-full bg-foreground px-2 py-0.5 text-[11px] font-medium text-background">
                <span className="size-1.5 rounded-full bg-brand" />
                {t("scheduled")}
              </span>
            </div>
          </div>
          <StepLabel n={3}>{t("steps.post")}</StepLabel>
        </div>
      </div>
    </div>
  );
}
