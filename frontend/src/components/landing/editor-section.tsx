"use client";

import { useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import {
  ArrowLeft,
  AudioLines,
  Captions,
  Check,
  ChevronDown,
  Combine,
  Crop,
  Download,
  History,
  Palette,
  Pause,
  Play,
  Redo2,
  Scan,
  Scissors,
  SkipBack,
  SkipForward,
  SlidersHorizontal,
  Type,
  Undo2,
} from "lucide-react";

import { DEMO_CLIPS } from "@/lib/demo";
import { cn } from "@/lib/utils";
import { Reveal, SectionHeading } from "./reveal";

type Tab = "captions" | "framing" | "audio" | "effects";

const TABS: { id: Tab; icon: React.ComponentType<{ className?: string }> }[] = [
  { id: "captions", icon: Captions },
  { id: "framing", icon: Crop },
  { id: "audio", icon: AudioLines },
  { id: "effects", icon: Palette },
];

// Illustrative word timings for the transcript panel (words heard in clip 1).
const WORDS = [
  { word: "really", start: 3.2, end: 3.5 },
  { word: "work", start: 3.5, end: 3.8 },
  { word: "and", start: 3.8, end: 3.9 },
  { word: "reach", start: 3.9, end: 4.3 },
];

const WAVEFORM = Array.from({ length: 90 }, (_, i) => 0.2 + 0.8 * Math.abs(Math.sin(i * 0.9) * Math.cos(i * 0.23)));

function Slider({
  label,
  value,
  onChange,
  min = 0,
  max = 200,
  suffix = "%",
}: {
  label: string;
  value: number;
  onChange: (value: number) => void;
  min?: number;
  max?: number;
  suffix?: string;
}) {
  return (
    <label className="block">
      <span className="mb-1.5 flex items-center justify-between text-xs">
        <span>{label}</span>
        <span className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[10px] tabular-nums">
          {value}
          {suffix}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="h-1.5 w-full cursor-pointer accent-foreground"
      />
    </label>
  );
}

function Segmented<T extends string>({ options, value, onChange }: {
  options: { id: T; label: string }[];
  value: T;
  onChange: (value: T) => void;
}) {
  return (
    <div className="grid rounded-lg bg-muted p-0.5 text-xs" style={{ gridTemplateColumns: `repeat(${options.length}, 1fr)` }}>
      {options.map((option) => (
        <button
          key={option.id}
          type="button"
          onClick={() => onChange(option.id)}
          className={cn(
            "rounded-md px-2 py-1.5 font-medium transition-colors",
            value === option.id ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground",
          )}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}

function StudioMock() {
  const t = useTranslations("editor");
  const videoRef = useRef<HTMLVideoElement>(null);
  const [active, setActive] = useState(0);
  const [tab, setTab] = useState<Tab>("captions");
  const [playing, setPlaying] = useState(false);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(DEMO_CLIPS[0].previewSeconds);
  const [safeArea, setSafeArea] = useState(true);
  const [style, setStyle] = useState<"clean" | "highlight" | "pop">("highlight");
  const [aspect, setAspect] = useState<"vertical" | "square" | "original">("vertical");
  const [fit, setFit] = useState<"fill" | "contain">("fill");
  const [zoom, setZoom] = useState(100);
  const [volume, setVolume] = useState(100);
  const [muted, setMuted] = useState(true);
  const [effects, setEffects] = useState({ brightness: 100, contrast: 100, saturation: 100 });
  const clip = DEMO_CLIPS[active];

  // Play while the studio is on screen, like a live preview.
  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    const observer = new IntersectionObserver(([entry]) => {
      if (entry.isIntersecting) video.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
      else video.pause();
    }, { threshold: 0.4 });
    observer.observe(video);
    return () => observer.disconnect();
  }, [active]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = muted;
  }, [muted, active]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.volume = Math.min(volume, 100) / 100;
  }, [volume, active]);

  const togglePlay = () => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) video.play().then(() => setPlaying(true)).catch(() => setPlaying(false));
    else {
      video.pause();
      setPlaying(false);
    }
  };

  const progress = duration ? time / duration : 0;
  const format = (seconds: number) => `0:${seconds.toFixed(2).padStart(5, "0")}`;
  const filter = `brightness(${effects.brightness}%) contrast(${effects.contrast}%) saturate(${effects.saturation}%)`;

  return (
    <div className="rounded-[30px] border bg-card/60 p-1.5 shadow-[0_30px_80px_-30px_rgb(28_25_23/0.25)]">
      <div className="overflow-hidden rounded-[24px] border bg-background">
        {/* Top bar */}
        <div className="flex items-center gap-3 border-b px-4 py-3">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-lg border text-muted-foreground">
            <ArrowLeft className="size-4" />
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-[11px] text-muted-foreground">
              {t("workspace.generation")} / {t("clipLabel", { order: active + 1 })}
            </p>
            <p className="truncate text-sm font-semibold">{clip.hook}</p>
          </div>
          <span className="hidden items-center gap-1.5 text-xs text-muted-foreground md:flex">
            <Check className="size-3.5" />
            {t("status.saved")}
          </span>
          <span className="hidden items-center rounded-lg border sm:flex">
            <span className="flex size-8 items-center justify-center text-muted-foreground" title={t("workspace.undo")}>
              <Undo2 className="size-4" />
            </span>
            <span className="flex size-8 items-center justify-center border-l text-muted-foreground" title={t("workspace.redo")}>
              <Redo2 className="size-4" />
            </span>
          </span>
          <span className="flex items-center gap-1.5 rounded-lg bg-foreground px-3 py-1.5 text-xs font-medium text-background">
            <Download className="size-3.5" />
            {t("workspace.export")}
          </span>
        </div>

        <div className="grid lg:grid-cols-[200px_minmax(0,1fr)_290px]">
          {/* Clips */}
          <aside className="hidden border-r p-3 lg:block">
            <p className="mb-2 flex items-center justify-between px-1 text-sm font-medium">
              {t("workspace.clips")}
              <span className="rounded-full bg-muted px-2 text-xs tabular-nums">{DEMO_CLIPS.length}</span>
            </p>
            <div className="space-y-1.5">
              {DEMO_CLIPS.map((item, i) => (
                <button
                  key={item.id}
                  type="button"
                  onClick={() => {
                    setActive(i);
                    setTime(0);
                  }}
                  className={cn(
                    "flex w-full gap-2.5 rounded-xl p-2 text-left transition-colors",
                    i === active ? "bg-muted ring-1 ring-border" : "hover:bg-muted/50",
                  )}
                >
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={item.poster} alt="" className="h-14 w-8 shrink-0 rounded-md object-cover" />
                  <span className="min-w-0">
                    <span className="flex items-center justify-between gap-2 text-xs font-medium">
                      {t("clipLabel", { order: i + 1 })}
                      <span className="font-mono text-[10px] text-muted-foreground">{item.duration}</span>
                    </span>
                    <span className="mt-0.5 line-clamp-2 text-[11px] leading-snug text-muted-foreground">{item.hook}</span>
                  </span>
                </button>
              ))}
            </div>
            <p className="mt-4 border-t px-1 pt-3 text-[11px] leading-relaxed text-muted-foreground">{t("workspace.draftNote")}</p>
          </aside>

          {/* Preview + timeline */}
          <div className="min-w-0">
            <div className="flex items-center justify-between border-b px-4 py-2.5">
              <p className="text-sm font-medium">{t("preview.title")}</p>
              <button
                type="button"
                onClick={() => setSafeArea((on) => !on)}
                aria-pressed={safeArea}
                className={cn(
                  "flex items-center gap-1.5 rounded-md px-2 py-1 text-xs transition-colors",
                  safeArea ? "bg-muted text-foreground" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Scan className="size-3.5" />
                {t("preview.safeArea")}
              </button>
            </div>
            <div className="flex h-[340px] items-center justify-center bg-[#141210] sm:h-[400px]">
              <div
                className={cn(
                  "relative h-[300px] overflow-hidden rounded-lg bg-black shadow-2xl transition-all duration-500 sm:h-[360px]",
                  aspect === "vertical" && "aspect-[9/16]",
                  aspect === "square" && "aspect-square",
                  aspect === "original" && "aspect-video",
                )}
              >
                <video
                  key={clip.src}
                  ref={videoRef}
                  src={clip.src}
                  poster={clip.poster}
                  muted
                  loop
                  playsInline
                  preload="metadata"
                  aria-label={clip.hook}
                  onLoadedMetadata={(e) => setDuration(e.currentTarget.duration)}
                  onTimeUpdate={(e) => setTime(e.currentTarget.currentTime)}
                  className={cn("size-full transition-transform duration-300", fit === "fill" ? "object-cover" : "object-contain")}
                  style={{ filter, transform: `scale(${zoom / 100})` }}
                />
                {safeArea && (
                  <span className="pointer-events-none absolute inset-x-[8%] bottom-[18%] top-[9%] rounded-md border border-dashed border-white/60" />
                )}
              </div>
            </div>
            <div className="flex items-center justify-between border-b px-4 py-2 text-muted-foreground">
              <span className="font-mono text-[11px]">1×</span>
              <span className="flex items-center gap-3">
                <SkipBack className="size-4" />
                <button
                  type="button"
                  onClick={togglePlay}
                  aria-label={playing ? t("preview.pause") : t("preview.play")}
                  className="flex size-8 items-center justify-center rounded-full bg-foreground text-background"
                >
                  {playing ? <Pause className="size-3.5" /> : <Play className="ml-0.5 size-3.5" />}
                </button>
                <SkipForward className="size-4" />
              </span>
              <span className="font-mono text-[11px] tabular-nums text-foreground">
                {format(time)} <span className="text-muted-foreground">/ {format(duration)}</span>
              </span>
            </div>

            {/* Timeline */}
            <div className="px-4 py-3">
              <div className="mb-2.5 flex items-center justify-between">
                <p className="text-sm font-medium">
                  {t("timeline.title")}{" "}
                  <span className="text-xs font-normal text-muted-foreground">
                    {t("timeline.summaryOther", { count: 2, duration: clip.duration })}
                  </span>
                </p>
                <span className="flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs">
                  <Scissors className="size-3.5" />
                  <span className="hidden sm:inline">{t("timeline.split")}</span>
                </span>
              </div>
              <div className="relative">
                <div className="flex h-11 overflow-hidden rounded-t-md">
                  {Array.from({ length: 12 }, (_, i) => (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img key={i} src={clip.poster} alt="" className="h-full min-w-0 flex-1 object-cover" />
                  ))}
                </div>
                <div className="flex h-7 items-center gap-px rounded-b-md bg-foreground px-1" aria-hidden>
                  {WAVEFORM.map((height, i) => (
                    <span key={i} className="flex-1 rounded-full bg-background/50" style={{ height: `${Math.round(height * 75)}%` }} />
                  ))}
                </div>
                {/* A cut between the two segments */}
                <span className="absolute inset-y-0 left-[58%] w-[9%] bg-background/80 [background-image:repeating-linear-gradient(135deg,transparent_0_4px,var(--border)_4px_5px)]" />
                <span className="absolute inset-y-0 left-0 w-1.5 rounded-l-md bg-brand" />
                <span className="absolute inset-y-0 right-0 w-1.5 rounded-r-md bg-brand" />
                <span
                  className="absolute -bottom-1 -top-1 w-0.5 rounded-full bg-brand transition-[left] duration-200 ease-linear"
                  style={{ left: `${Math.min(progress, 1) * 100}%` }}
                />
              </div>
              <div className="mt-2.5 flex gap-2">
                {[
                  { label: t("timeline.segment", { n: 1 }), range: "0:00 – 0:32" },
                  { label: t("timeline.segment", { n: 2 }), range: "0:37 – 0:55" },
                ].map((segment, i) => (
                  <span
                    key={segment.label}
                    className={cn("rounded-lg border px-2.5 py-1 text-[11px]", i === 0 && "border-foreground/40 bg-muted")}
                  >
                    <span className="block text-muted-foreground">{segment.label}</span>
                    <span className="font-mono tabular-nums">{segment.range}</span>
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Inspector */}
          <div className="border-t lg:border-l lg:border-t-0">
            <div className="m-3 grid grid-cols-4 gap-1 rounded-xl bg-muted p-1">
              {TABS.map(({ id, icon: Icon }) => (
                <button
                  key={id}
                  type="button"
                  onClick={() => setTab(id)}
                  aria-pressed={tab === id}
                  className={cn(
                    "flex flex-col items-center gap-1 rounded-lg py-2 text-[11px] font-medium transition-colors",
                    tab === id ? "bg-background shadow-sm" : "text-muted-foreground hover:text-foreground",
                  )}
                >
                  <Icon className="size-4" />
                  {t(`tabs.${id}`)}
                </button>
              ))}
            </div>

            <div key={tab} className="animate-rise space-y-4 px-4 pb-5">
              {tab === "captions" && (
                <>
                  <div>
                    <p className="mb-2 text-sm font-medium">{t("captions.styleTitle")}</p>
                    <div className="grid grid-cols-3 gap-1.5">
                      {(["clean", "highlight", "pop"] as const).map((id) => (
                        <button
                          key={id}
                          type="button"
                          onClick={() => setStyle(id)}
                          className={cn(
                            "rounded-xl border p-1.5 text-left transition-colors",
                            style === id ? "border-foreground" : "hover:border-foreground/30",
                          )}
                        >
                          <span className="block rounded-lg bg-foreground px-1.5 py-2 text-center text-[11px] font-bold text-background">
                            {t("captions.sampleLead")}{" "}
                            <span className={cn(id === "highlight" && "text-yellow-300", id === "pop" && "text-lime-300")}>
                              {t("captions.sampleAccent")}
                            </span>
                          </span>
                          <span className="mt-1 block text-[11px]">{t(`captions.styles.${id}`)}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs">{t("captions.font")}</p>
                    <span className="flex items-center justify-between rounded-lg border px-3 py-2 text-sm">
                      Montserrat
                      <ChevronDown className="size-4 text-muted-foreground" />
                    </span>
                  </div>
                  <div>
                    <p className="mb-1.5 flex items-center justify-between text-sm font-medium">
                      {t("captions.transcript")}
                      <span className="text-xs font-normal text-muted-foreground">{t("captions.editScript")}</span>
                    </p>
                    <div className="divide-y rounded-xl border">
                      {WORDS.map((word, i) => (
                        <div key={word.word} className="flex items-center gap-2.5 px-3 py-1.5">
                          <Play className="size-3 text-muted-foreground" />
                          <span className={cn("flex-1 text-sm", i === 3 && "font-semibold")}>{word.word}</span>
                          <span className="font-mono text-[10px] tabular-nums text-muted-foreground">
                            {word.start.toFixed(1)} → {word.end.toFixed(1)}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>
                </>
              )}

              {tab === "framing" && (
                <>
                  <div>
                    <p className="mb-1.5 text-xs">{t("framing.aspect")}</p>
                    <Segmented
                      value={aspect}
                      onChange={setAspect}
                      options={(["vertical", "square", "original"] as const).map((id) => ({ id, label: t(`framing.aspects.${id}`) }))}
                    />
                  </div>
                  <div>
                    <p className="mb-1.5 text-xs">{t("framing.fit")}</p>
                    <Segmented
                      value={fit}
                      onChange={setFit}
                      options={[
                        { id: "fill", label: t("framing.fill") },
                        { id: "contain", label: t("framing.contain") },
                      ]}
                    />
                  </div>
                  <Slider label={t("framing.zoom")} value={zoom} onChange={setZoom} min={100} max={160} />
                  <span className="flex items-center justify-center gap-1.5 rounded-lg border py-2 text-xs font-medium">
                    <Scan className="size-3.5" />
                    {t("framing.center")}
                  </span>
                </>
              )}

              {tab === "audio" && (
                <>
                  <p className="text-xs leading-relaxed text-muted-foreground">{t("audioPanel.description")}</p>
                  <Slider label={t("audioPanel.volume")} value={volume} onChange={setVolume} />
                  <label className="flex cursor-pointer items-center justify-between text-sm">
                    {t("audioPanel.mute")}
                    <input
                      type="checkbox"
                      checked={muted}
                      onChange={(e) => setMuted(e.target.checked)}
                      className="size-4 accent-foreground"
                    />
                  </label>
                </>
              )}

              {tab === "effects" && (
                <>
                  <p className="flex items-center justify-between text-xs text-muted-foreground">
                    {t("effects.description")}
                    <button
                      type="button"
                      onClick={() => setEffects({ brightness: 100, contrast: 100, saturation: 100 })}
                      className="shrink-0 rounded-md border px-2 py-0.5 text-foreground hover:bg-muted"
                    >
                      {t("effects.reset")}
                    </button>
                  </p>
                  {(["brightness", "contrast", "saturation"] as const).map((key) => (
                    <Slider
                      key={key}
                      label={t(`effects.${key}`)}
                      value={effects[key]}
                      onChange={(value) => setEffects((prev) => ({ ...prev, [key]: value }))}
                      min={50}
                      max={150}
                    />
                  ))}
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const POINTS = [
  { key: "timeline", icon: Scissors },
  { key: "transcript", icon: Type },
  { key: "captions", icon: Captions },
  { key: "framing", icon: Crop },
  { key: "audio", icon: SlidersHorizontal },
  { key: "combine", icon: Combine },
  { key: "drafts", icon: History },
  { key: "export", icon: Download },
] as const;

export function EditorSection() {
  const t = useTranslations("marketing.editor");

  return (
    <section id="editor" className="scroll-mt-24 border-t bg-canvas py-24 md:py-32">
      <div className="mx-auto max-w-6xl px-6">
        <SectionHeading eyebrow={t("eyebrow")} title={t("title")} body={t("body")} />
        <Reveal delay={0.05} className="mt-14">
          <StudioMock />
          <p className="mt-4 text-center text-xs text-muted-foreground">{t("tryIt")}</p>
        </Reveal>

        <div className="mt-16 grid gap-x-8 gap-y-10 sm:grid-cols-2 lg:grid-cols-4">
          {POINTS.map(({ key, icon: Icon }, i) => (
            <Reveal key={key} delay={(i % 4) * 0.05}>
              <Icon className="mb-3 size-5 text-muted-foreground" />
              <h3 className="font-semibold tracking-tight">{t(`points.${key}.title`)}</h3>
              <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{t(`points.${key}.description`)}</p>
            </Reveal>
          ))}
        </div>
      </div>
    </section>
  );
}
