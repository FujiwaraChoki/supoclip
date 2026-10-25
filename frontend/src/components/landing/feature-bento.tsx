"use client";

import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import { Film, ScanFace, Sparkles, Target, Type, Wand2 } from "lucide-react";

import { SocialProviderIcon } from "@/components/social-provider-icon";
import { DEMO_CLIPS } from "@/lib/demo";
import { cn } from "@/lib/utils";
import { Reveal, SectionHeading } from "./reveal";

function Tile({
  icon: Icon,
  title,
  description,
  children,
  className,
  delay = 0,
}: {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  description: string;
  children: React.ReactNode;
  className?: string;
  delay?: number;
}) {
  return (
    <Reveal delay={delay} className={cn("h-full", className)}>
      <div className="group flex h-full flex-col overflow-hidden rounded-[26px] border bg-card transition-shadow duration-500 hover:shadow-xl">
        <div className="relative flex min-h-[220px] flex-1 items-center justify-center overflow-hidden bg-canvas p-6">
          {children}
        </div>
        <div className="border-t p-6">
          <h3 className="flex items-center gap-2 font-semibold tracking-tight">
            <Icon className="size-4 text-muted-foreground" />
            {title}
          </h3>
          <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{description}</p>
        </div>
      </div>
    </Reveal>
  );
}

function ReframeVisual() {
  const clip = DEMO_CLIPS[0];
  return (
    <div className="relative aspect-video w-full max-w-[520px] overflow-hidden rounded-2xl bg-black shadow-lg">
      {/* The wide source, softened */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={clip.poster} alt="" className="absolute inset-0 size-full scale-110 object-cover blur-md" />
      <div className="absolute inset-0 bg-black/35" />
      {/* The 9:16 crop the tracker keeps on the speaker */}
      <div className="absolute inset-y-0 left-1/2 aspect-[9/16] -translate-x-1/2 animate-[landing-pan_9s_ease-in-out_infinite]">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img src={clip.poster} alt="" className="size-full object-cover" />
        <span className="absolute inset-0 ring-2 ring-white/90" />
        {(["left-0 top-0 border-l-2 border-t-2", "right-0 top-0 border-r-2 border-t-2", "bottom-0 left-0 border-b-2 border-l-2", "bottom-0 right-0 border-b-2 border-r-2"] as const).map((corner) => (
          <span key={corner} className={cn("absolute -m-1 size-4 border-white", corner)} />
        ))}
        <span className="absolute left-[36%] top-[19%] h-[14%] w-[30%] rounded-md border border-dashed border-white/90">
          <span className="absolute -bottom-4 left-0 rounded bg-white px-1 font-mono text-[9px] font-medium text-black">face</span>
        </span>
      </div>
      <span className="absolute bottom-3 left-3 rounded-full bg-white/15 px-2 py-0.5 font-mono text-[10px] text-white backdrop-blur">16:9</span>
      <span className="absolute bottom-3 right-3 rounded-full bg-white px-2 py-0.5 font-mono text-[10px] font-medium text-black">9:16</span>
    </div>
  );
}

function CaptionVisual({ sample }: { sample: string }) {
  const words = sample.split(/\s+/);
  const [current, setCurrent] = useState(0);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
    const id = window.setInterval(() => setCurrent((i) => (i + 1) % (words.length + 2)), 420);
    return () => window.clearInterval(id);
  }, [words.length]);

  return (
    <div className="relative flex aspect-[9/16] w-[150px] items-center justify-center overflow-hidden rounded-2xl bg-black px-3 shadow-lg">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={DEMO_CLIPS[1].poster} alt="" className="absolute inset-0 size-full scale-110 object-cover opacity-60 blur-[6px]" />
      <p className="relative text-center drop-shadow-[0_2px_4px_rgb(0_0_0/0.6)] text-[15px] font-extrabold uppercase leading-tight tracking-tight text-white">
        {words.map((word, i) => (
          <span
            key={i}
            className={cn(
              "inline-block px-0.5 transition-all duration-150",
              i === current ? "scale-110 text-yellow-300" : i < current ? "text-white" : "text-white/35",
            )}
          >
            {word}
          </span>
        ))}
      </p>
    </div>
  );
}

function ScoreVisual({ labels }: { labels: Record<"hook" | "engagement" | "value" | "shareability", string> }) {
  const parts = [
    { key: "hook", value: 24 },
    { key: "engagement", value: 22 },
    { key: "value", value: 23 },
    { key: "shareability", value: 23 },
  ] as const;
  const total = parts.reduce((sum, p) => sum + p.value, 0);
  return (
    <div className="w-full max-w-[220px]">
      <div className="mb-4 flex items-baseline gap-1.5">
        <span className="font-display text-6xl font-bold tracking-tight">{total}</span>
        <span className="text-sm text-muted-foreground">/100</span>
      </div>
      <div className="space-y-2.5">
        {parts.map((part) => (
          <div key={part.key}>
            <div className="mb-1 flex justify-between text-[11px] text-muted-foreground">
              <span>{labels[part.key]}</span>
              <span className="font-mono tabular-nums">{part.value}</span>
            </div>
            <div className="h-1.5 overflow-hidden rounded-full bg-foreground/10">
              <div className="h-full rounded-full bg-foreground" style={{ width: `${(part.value / 25) * 100}%` }} />
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function HookVisual() {
  const clip = DEMO_CLIPS[1];
  return (
    <div className="relative aspect-[9/16] w-[150px] -rotate-3 overflow-hidden rounded-2xl shadow-xl ring-1 ring-black/10 transition-transform duration-500 group-hover:rotate-0">
      {/* The real clip, hook title burned in by SupoClip */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img src={clip.poster} alt={clip.hook} className="size-full object-cover" />
      <span className="absolute inset-x-3 top-[7%] h-[10%] rounded-md border border-dashed border-white/80" />
    </div>
  );
}

function TemplatesVisual({ ownFonts }: { ownFonts: string }) {
  const styles = [
    { name: "Default", className: "font-sans font-bold" },
    { name: "Minimal", className: "font-sans font-light tracking-wide" },
    { name: "Neon", className: "font-display font-bold text-yellow-300 bg-foreground" },
    { name: "Podcast", className: "font-serif italic" },
    { name: ownFonts, className: "font-mono text-muted-foreground border-dashed" },
  ];
  return (
    <div className="flex max-w-[260px] flex-wrap justify-center gap-2">
      {styles.map((style) => (
        <span
          key={style.name}
          className={cn("rounded-xl border bg-background px-3.5 py-2 text-sm shadow-sm", style.className)}
        >
          {style.name}
        </span>
      ))}
    </div>
  );
}

function ExportVisual({ labels }: { labels: { shorts: string; tiktok: string; reels: string; broll: string } }) {
  const presets = [
    { provider: "youtube", label: labels.shorts },
    { provider: "tiktok", label: labels.tiktok },
    { provider: "instagram", label: labels.reels },
  ] as const;
  return (
    <div className="flex w-full max-w-[520px] flex-col items-center gap-4">
      <div className="grid w-full grid-cols-3 gap-3">
        {presets.map((preset, i) => (
          <div
            key={preset.provider}
            className="flex flex-col items-center gap-3 rounded-2xl border bg-background p-3 shadow-sm sm:p-4 transition-transform duration-500 group-hover:-translate-y-1"
            style={{ transitionDelay: `${i * 60}ms` }}
          >
            <div className="flex aspect-[9/16] w-12 items-center justify-center rounded-lg bg-muted">
              <SocialProviderIcon provider={preset.provider} className="size-5" />
            </div>
            <span className="text-center text-xs font-medium">{preset.label}</span>
            <span className="font-mono text-[10px] text-muted-foreground">9:16 · H.264</span>
          </div>
        ))}
      </div>
      <span className="flex items-center gap-1.5 rounded-full border bg-background px-3 py-1 text-xs text-muted-foreground">
        <Film className="size-3.5" />
        {labels.broll}
      </span>
    </div>
  );
}

export function FeatureBento() {
  const t = useTranslations("marketing.features");
  const item = (key: string) => ({
    title: t(`items.${key}.title`),
    description: t(`items.${key}.description`),
  });

  return (
    <section id="features" className="scroll-mt-24 py-24 md:py-32">
      <div className="mx-auto max-w-6xl px-6">
        <SectionHeading eyebrow={t("eyebrow")} title={t("title")} body={t("body")} />

        <div className="mt-16 grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <Tile icon={ScanFace} {...item("reframe")} className="md:col-span-2">
            <ReframeVisual />
          </Tile>
          <Tile icon={Type} {...item("captions")} delay={0.05}>
            <CaptionVisual sample={t("captionSample")} />
          </Tile>
          <Tile icon={Target} {...item("virality")}>
            <ScoreVisual
              labels={{
                hook: t("scores.hook"),
                engagement: t("scores.engagement"),
                value: t("scores.value"),
                shareability: t("scores.shareability"),
              }}
            />
          </Tile>
          <Tile icon={Sparkles} {...item("hooks")} delay={0.05}>
            <HookVisual />
          </Tile>
          <Tile icon={Wand2} {...item("templates")} delay={0.1}>
            <TemplatesVisual ownFonts={t("ownFonts")} />
          </Tile>
          <Tile icon={Film} {...item("export")} className="md:col-span-2 lg:col-span-3">
            <ExportVisual
              labels={{
                shorts: t("presets.shorts"),
                tiktok: t("presets.tiktok"),
                reels: t("presets.reels"),
                broll: t("presets.broll"),
              }}
            />
          </Tile>
        </div>
      </div>
    </section>
  );
}
