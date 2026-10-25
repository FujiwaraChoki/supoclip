"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { useLocale, useTranslations } from "next-intl";
import { AnimatePresence, motion, useInView, useMotionValue, useReducedMotion, type MotionValue } from "motion/react";
import {
  ArrowLeft, ArrowUp, Bot, Captions, ChartColumn, CheckCircle2, ChevronDown, Clapperboard, Clock, Download,
  ExternalLink, Film, Flame, Link2, Loader2, LogOut, MessageSquare, Paintbrush, Paperclip, Pause, Play, Plus, RefreshCw,
  Scissors, Search, Send, Settings, Settings2, Share2, Sparkles, Star, Trash2, TrendingUp, Wand2, X,
} from "lucide-react";
import Image from "next/image";

import { ClipCover, ScoreRing } from "@/components/app/clip-cover";
import { ProcessingPanel } from "@/components/app/clip-wall";
import { StatusBadge } from "@/components/app/status-badge";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Kbd } from "@/components/ui/kbd";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { VideoSourceIcon } from "@/components/video-source-icon";
import { DEMO_CLIPS, DEMO_SOURCE } from "@/lib/demo";
import { formatClipDuration } from "@/lib/generations";
import { cn } from "@/lib/utils";

/*
 * A scripted walkthrough of the real app, rendered at the app's own size
 * (CANVAS_W × CANVAS_H) and scaled to fit, so it stays pixel-accurate. One
 * clock drives everything: discrete UI state is derived from it, and the
 * cursor glides between elements measured from the live canvas.
 */

const CANVAS_W = 1280;
const CANVAS_H = 800;
const SOURCE_URL = "https://youtu.be/OpANpfEjNw8";
const ACCOUNT_EMAIL = "you@example.com";

const T = {
  typeStart: 1.2,
  typeEnd: 2.9,
  submit: 3.6,
  task: 4.4,
  progressStart: 4.7,
  firstClip: 8.2,
  secondClip: 9.6,
  progressEnd: 10,
  complete: 10.3,
  openClip: 11.6,
  publish: 13.6,
  sheet: 13.9,
  captionStart: 14.4,
  captionEnd: 15.8,
  toggle: 16.5,
  schedule: 17.6,
  toast: 18,
  end: 21.6,
};

const CLICKS = [T.submit, T.openClip - 0.15, T.publish, T.toggle, T.schedule];

export const FILM_CHAPTERS = [
  { key: "drop", start: 0 },
  { key: "gold", start: T.task },
  { key: "export", start: T.openClip - 0.4 },
] as const;

// Cursor path: glide to `to` during [start, end]. Targets are `data-film` elements.
type Waypoint = { start: number; end: number; to: string | { x: number; y: number } };
const PATH: Waypoint[] = [
  { start: 0.3, end: 1.1, to: "url" },
  { start: 3.0, end: 3.5, to: "generate" },
  { start: 4.6, end: 6.2, to: { x: 1010, y: 560 } },
  { start: 10.5, end: 11.4, to: "tile" },
  { start: 12.5, end: 13.5, to: "publish" },
  { start: 15.9, end: 16.4, to: "schedule-toggle" },
  { start: 16.8, end: 17.5, to: "schedule-submit" },
  { start: 18.4, end: 19.6, to: { x: 860, y: 520 } },
];
const CURSOR_HOME = { x: 900, y: 560 };

const SCORES = [
  { score: 92, hook: 24, engagement: 22, value: 23, shareability: 23 },
  { score: 87, hook: 22, engagement: 22, value: 21, shareability: 22 },
];

function ease(x: number) {
  return x < 0.5 ? 4 * x * x * x : 1 - (-2 * x + 2) ** 3 / 2;
}

function between(t: number, from: number, to: number) {
  return Math.min(1, Math.max(0, (t - from) / (to - from)));
}

function frameAt(t: number) {
  const progress = t < T.progressStart ? 3 : Math.round(3 + 97 * ease(between(t, T.progressStart, T.progressEnd)));
  return {
    scene: t < T.task ? "create" : "task",
    typed: Math.round(SOURCE_URL.length * between(t, T.typeStart, T.typeEnd)),
    submitting: t >= T.submit,
    progress,
    ready: t >= T.secondClip ? 2 : t >= T.firstClip ? 1 : 0,
    complete: t >= T.complete,
    focus: t >= T.openClip,
    sheet: t >= T.sheet && t < T.toast,
    // Share of the AI caption written so far, in 1/40 steps (-1 = still waiting for it).
    caption: t < T.captionStart ? -1 : Math.round(between(t, T.captionStart, T.captionEnd) * 40) / 40,
    scheduleOn: t >= T.toggle,
    scheduling: t >= T.schedule,
    toast: t >= T.toast,
  };
}
type Frame = ReturnType<typeof frameAt>;

function useCanvasScale(ref: React.RefObject<HTMLDivElement | null>) {
  const [scale, setScale] = useState(0.5);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    const update = () => setScale(el.clientWidth / CANVAS_W);
    update();
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref]);
  return scale;
}

/* ─── App chrome ─── */

function Sidebar({ active }: { active: "create" | "library" }) {
  const t = useTranslations("shell");
  const items = [
    { key: "create", icon: Sparkles },
    { key: "library", icon: Film },
    { key: "social", icon: Share2 },
    { key: "analytics", icon: ChartColumn },
    { key: "agents", icon: Bot },
    { key: "settings", icon: Settings },
  ] as const;
  return (
    <aside className="flex h-full w-60 shrink-0 flex-col border-r border-sidebar-border bg-sidebar px-3">
      <div className="flex h-14 items-center gap-2 px-2 font-display text-lg font-bold tracking-tight">
        <Image src="/logo.png" alt="" width={24} height={24} className="size-6" />
        SupoClip
      </div>
      <div className="mb-4 mt-1 flex h-9 w-full items-center gap-2.5 rounded-lg bg-foreground px-2.5 text-sm font-medium text-background">
        <Plus className="size-4" />
        <span className="flex-1">{t("nav.newGeneration")}</span>
        <span className="text-[11px] opacity-60">N</span>
      </div>
      <nav className="flex w-full flex-col gap-0.5">
        {items.map(({ key, icon: Icon }) => (
          <div
            key={key}
            className={cn(
              "flex h-9 w-full items-center gap-2.5 rounded-lg px-2.5 text-sm font-medium",
              active === key ? "bg-background text-foreground shadow-xs ring-1 ring-sidebar-border" : "text-muted-foreground",
            )}
          >
            <Icon className="size-4" />
            <span className="flex-1">{t(`nav.${key}`)}</span>
          </div>
        ))}
      </nav>
      <div className="mt-4 flex h-9 w-full items-center gap-2 rounded-lg border border-sidebar-border bg-background px-2.5 text-sm text-muted-foreground">
        <Search className="size-4 shrink-0" />
        <span className="flex-1 text-left">{t("search.label")}</span>
        <Kbd>⌘K</Kbd>
      </div>
      <div className="mt-auto w-full pb-3">
        <div className="flex items-center gap-2 rounded-lg px-1.5 py-1">
          <span className="flex size-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-brand to-amber-400 text-xs font-semibold text-white">Y</span>
          <div className="min-w-0 flex-1 leading-tight">
            <p className="truncate text-sm font-medium">{t("user.creator")}</p>
            <p className="truncate text-xs text-muted-foreground">{ACCOUNT_EMAIL}</p>
          </div>
          <span className="rounded-md p-1.5 text-muted-foreground"><LogOut className="size-4" /></span>
        </div>
      </div>
    </aside>
  );
}

function Chip({ active, children }: { active?: boolean; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs font-medium text-foreground/80",
        active && "border-foreground/30 bg-accent text-foreground",
      )}
    >
      {children}
    </span>
  );
}

/* ─── Scenes ─── */

function CreateScene({ frame }: { frame: Frame }) {
  const t = useTranslations("home");
  const typed = SOURCE_URL.slice(0, frame.typed);
  const hasSource = frame.typed > 0;
  const typing = frame.typed > 0 && frame.typed < SOURCE_URL.length;
  return (
    <main className="mx-auto w-full max-w-5xl px-8 pb-20 pt-24">
      <div className="mx-auto max-w-3xl">
        <h1 className="text-center font-display text-[2.6rem] font-bold leading-tight tracking-tight">{t("greeting.goodEvening")}</h1>
        <p className="mt-2 text-center text-muted-foreground">{t("subtitle")}</p>
        <div
          className={cn(
            "relative mt-8 rounded-2xl border bg-background shadow-[0_1px_2px_rgb(0_0_0/0.04),0_12px_40px_-12px_rgb(0_0_0/0.12)] transition-all",
            frame.typed > 0 && "border-foreground/25 shadow-[0_1px_2px_rgb(0_0_0/0.04),0_16px_48px_-12px_rgb(0_0_0/0.18)]",
          )}
        >
          <div className="flex items-start gap-3 p-4 pb-2">
            <span className="mt-0.5 flex size-11 shrink-0 items-center justify-center rounded-xl bg-muted text-muted-foreground">
              {typed.includes("youtu") ? <VideoSourceIcon source="youtube" className="size-5" aria-hidden /> : <Link2 className="size-5" aria-hidden />}
            </span>
            <div data-film="url" className="flex h-11 min-w-0 flex-1 items-center text-base">
              {hasSource ? (
                <span className="truncate">{typed}</span>
              ) : (
                <span className="text-muted-foreground/80">{t("urlPlaceholder")}</span>
              )}
              {frame.typed < SOURCE_URL.length && frame.typed > 0 && (
                <span className={cn("ml-px h-5 w-px bg-foreground", !typing && "animate-pulse")} />
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-1.5 px-4 pb-3">
            <Chip><Paperclip className="size-3.5" />{t("upload")}</Chip>
            <Chip><Paintbrush className="size-3.5" />{t("defaultTemplate")}<ChevronDown className="size-3 opacity-60" /></Chip>
            <Chip><Captions className="size-3.5" />{t("captions")}</Chip>
            <Chip><Wand2 className="size-3.5" />{t("cleanup")}<ChevronDown className="size-3 opacity-60" /></Chip>
            <div className="ml-auto flex items-center gap-2">
              <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                {frame.submitting ? <span>{t("status.checkingLength")}</span> : hasSource ? <><Kbd>⌘</Kbd><Kbd>↵</Kbd></> : null}
              </span>
              <span
                data-film="generate"
                className={cn(
                  "flex size-9 items-center justify-center rounded-full shadow-sm transition-colors",
                  hasSource ? "bg-brand text-brand-foreground" : "bg-muted text-muted-foreground",
                )}
              >
                {frame.submitting ? <Loader2 className="size-4 animate-spin" /> : <ArrowUp className="size-4" />}
              </span>
            </div>
          </div>
        </div>
      </div>
    </main>
  );
}

function FilmTile({ index, onTarget }: { index: number; onTarget?: boolean }) {
  const t = useTranslations("task");
  const clip = DEMO_CLIPS[index];
  const scores = SCORES[index];
  return (
    <motion.article initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.35, ease: [0.16, 1, 0.3, 1] }}>
      <div data-film={onTarget ? "tile" : undefined} className="rounded-xl">
        <ClipCover src={clip.src} className="aspect-[9/16] rounded-xl ring-1 ring-black/5">
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-black/25" />
          {index === 0 && (
            <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-brand px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-brand-foreground shadow">
              <Flame className="size-3" />{t("topPick")}
            </span>
          )}
          <span className="absolute right-2 top-2 rounded-full bg-black/55 text-white backdrop-blur">
            <ScoreRing score={scores.score} size={36} />
          </span>
          <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">{clip.duration}</span>
        </ClipCover>
      </div>
      <div className="mt-2.5 px-0.5">
        <h3 className="line-clamp-2 text-sm font-semibold leading-snug">{clip.hook}</h3>
        <p className="mt-0.5 text-xs tabular-nums text-muted-foreground">{clip.range}</p>
        <div className="mt-2 flex items-center gap-1">
          <span className="inline-flex h-7 flex-1 items-center justify-center gap-1.5 rounded-md border bg-background px-2 text-xs font-medium shadow-xs">
            <Download className="size-3.5" />{t("download")}
          </span>
          <span className="inline-flex h-7 items-center gap-1.5 rounded-md border bg-background px-2 text-xs font-medium shadow-xs">
            <Scissors className="size-3.5" />{t("edit")}
          </span>
          <span className="inline-flex size-7 items-center justify-center text-muted-foreground"><Trash2 className="size-3.5" /></span>
        </div>
      </div>
    </motion.article>
  );
}

function TaskScene({ frame }: { frame: Frame }) {
  const t = useTranslations("task");
  const tFilm = useTranslations("marketing.film");
  const locale = useLocale();
  const status = frame.complete ? "completed" : "processing";
  const message =
    frame.progress < 20 ? tFilm("progress.downloading")
      : frame.progress < 45 ? tFilm("progress.transcribing")
      : frame.progress < 65 ? tFilm("progress.finding")
      : tFilm("progress.rendering", { current: Math.min(frame.ready + 1, 2), total: 2 });
  const total = DEMO_CLIPS.reduce((sum, clip) => {
    const [m, s] = clip.duration.split(":").map(Number);
    return sum + m * 60 + s;
  }, 0);
  return (
    <main className="mx-auto w-full max-w-7xl px-8 py-8">
      <span className="inline-flex items-center gap-1 text-sm text-muted-foreground"><ArrowLeft className="size-3.5" />{t("back")}</span>
      <header className="mt-3 flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <h1 className={cn("min-w-0 font-display text-3xl font-bold tracking-tight", !frame.complete && "shimmer")}>{DEMO_SOURCE.title}</h1>
          <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2 text-sm text-muted-foreground">
            <StatusBadge status={status} />
            <span className="flex items-center gap-1.5"><VideoSourceIcon source="youtube" className="size-3.5" aria-hidden />YouTube</span>
            <span className="flex items-center gap-1.5"><Clock className="size-3.5" />{new Intl.RelativeTimeFormat(locale, { numeric: "auto" }).format(0, "minute")}</span>
            {frame.complete && <span className="tabular-nums">{t("clipsTotal", { count: 2, duration: formatClipDuration(total) })}</span>}
          </div>
        </div>
        <div className="flex items-center gap-2">
          <span className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium shadow-xs"><ExternalLink className="size-4" />{t("originalVideo")}</span>
          {frame.complete ? (
            <>
              <span className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium shadow-xs"><Settings2 className="size-4" />{t("restyle")}</span>
              <span className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium shadow-xs"><Share2 className="size-4" />{t("copyShare")}</span>
              <span className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground"><Clapperboard className="size-4" />{t("openEditor")}</span>
            </>
          ) : (
            <span className="inline-flex h-8 items-center rounded-md border bg-background px-3 text-sm font-medium shadow-xs">{t("cancel")}</span>
          )}
          <span className="inline-flex size-8 items-center justify-center text-muted-foreground"><Trash2 className="size-4" /></span>
        </div>
      </header>

      <div className="mt-8">
        {frame.complete ? (
          <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.3 }}>
            <div className="mb-5 flex items-center justify-between gap-3">
              <div className="inline-flex rounded-lg border bg-background p-0.5">
                {(["score", "timeline"] as const).map((value) => (
                  <span key={value} className={cn("flex h-7 items-center rounded-md px-3 text-xs font-medium", value === "score" ? "bg-foreground text-background" : "text-muted-foreground")}>
                    {t(`sort.${value}`)}
                  </span>
                ))}
              </div>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>{t("downloadAs")}</span>
                <span className="flex h-8 min-w-[112px] items-center justify-between gap-2 rounded-md border bg-background px-3 text-sm text-foreground shadow-xs">
                  {t("original")}<ChevronDown className="size-4 opacity-50" />
                </span>
              </div>
            </div>
            <div className="grid grid-cols-5 gap-x-4 gap-y-7">
              <FilmTile index={0} onTarget />
              <FilmTile index={1} />
            </div>
          </motion.div>
        ) : (
          <div className="space-y-8">
            <ProcessingPanel status="processing" progress={frame.progress} message={message} readyCount={frame.ready} />
            {frame.ready > 0 && (
              <div className="grid grid-cols-5 gap-x-4 gap-y-6">
                {Array.from({ length: frame.ready }, (_, i) => <FilmTile key={i} index={i} />)}
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}

function FocusDialog() {
  const t = useTranslations("task");
  const tSocial = useTranslations("social");
  const clip = DEMO_CLIPS[0];
  const scores = SCORES[0];
  const breakdown = [
    { key: "hook", icon: MessageSquare },
    { key: "engagement", icon: TrendingUp },
    { key: "value", icon: Star },
    { key: "shareability", icon: Share2 },
  ] as const;
  return (
    <>
      <motion.div className="absolute inset-0 z-40 bg-black/50" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }} />
      <motion.div
        className="absolute left-1/2 top-1/2 z-40 grid w-[1024px] -translate-x-1/2 -translate-y-1/2 grid-cols-[auto_1fr] overflow-hidden rounded-xl border bg-background shadow-xl"
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.95 }}
        transition={{ duration: 0.2 }}
      >
        <div className="relative flex items-center justify-center bg-stone-950 p-4">
          <video src={clip.src} poster={clip.poster} autoPlay muted loop playsInline className="aspect-[9/16] h-[600px] rounded-lg object-cover" />
        </div>
        <div className="flex min-h-0 flex-col">
          <div className="flex items-center justify-between gap-2 border-b px-5 py-3 pr-12 text-xs text-muted-foreground">
            <span className="tabular-nums">{t("position", { current: 1, total: 2 })}</span>
            <X className="absolute right-4 top-4 size-4 opacity-70" />
          </div>
          <div className="space-y-5 p-5">
            <div>
              <h2 className="font-display text-xl font-bold leading-tight">{clip.hook}</h2>
              <p className="mt-1 text-sm tabular-nums text-muted-foreground">{clip.range} · {clip.duration}</p>
            </div>
            <section className="rounded-xl border p-4">
              <div className="flex items-center gap-4">
                <ScoreRing score={scores.score} size={56} className="text-foreground [&>span]:text-base" />
                <div>
                  <p className="text-sm font-semibold">{t("virality")}</p>
                  <p className="text-xs text-muted-foreground">{t("relevanceWithHook", { hook: t("hooks.statistic"), percent: 94 })}</p>
                </div>
              </div>
              <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3">
                {breakdown.map(({ key, icon: Icon }) => (
                  <div key={key} className="space-y-1.5">
                    <div className="flex items-center justify-between text-xs">
                      <span className="flex items-center gap-1.5 text-muted-foreground"><Icon className="size-3" />{t(`scores.${key}`)}</span>
                      <span className="font-medium tabular-nums">{scores[key]}/25</span>
                    </div>
                    <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                      <div className="h-full rounded-full bg-foreground" style={{ width: `${(scores[key] / 25) * 100}%` }} />
                    </div>
                  </div>
                ))}
              </div>
            </section>
          </div>
          <div className="mt-auto flex items-center gap-2 border-t bg-muted/30 px-5 py-3">
            <span className="inline-flex h-8 items-center gap-1.5 rounded-md bg-primary px-3 text-sm font-medium text-primary-foreground"><Download className="size-4" />{t("download")}</span>
            <span className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium shadow-xs"><Scissors className="size-4" />{t("openInEditor")}</span>
            <span data-film="publish" className="inline-flex h-8 items-center gap-1.5 rounded-md border bg-background px-3 text-sm font-medium shadow-xs"><Send className="size-4" />{tSocial("publish")}</span>
            <span className="ml-auto inline-flex h-8 items-center gap-1.5 px-3 text-sm font-medium text-muted-foreground"><Trash2 className="size-4" />{t("delete")}</span>
          </div>
        </div>
      </motion.div>
    </>
  );
}

function PublishSheet({ frame }: { frame: Frame }) {
  const t = useTranslations("social");
  const tFilm = useTranslations("marketing.film");
  const locale = useLocale();
  const clip = DEMO_CLIPS[0];
  const captionText = tFilm("caption");
  const caption = frame.caption < 0 ? "" : captionText.slice(0, Math.round(captionText.length * frame.caption));
  const generating = frame.caption < 1;
  const when = new Intl.DateTimeFormat(locale, { dateStyle: "short", timeStyle: "short" }).format(new Date(2026, 9, 27, 18, 0));
  return (
    <>
      <motion.div className="absolute inset-0 z-50 bg-black/50" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.3 }} />
      <motion.div
        className="absolute inset-y-0 right-0 z-50 flex h-full w-[512px] flex-col gap-4 border-l bg-background shadow-lg"
        initial={{ x: "100%" }}
        animate={{ x: 0 }}
        exit={{ x: "100%" }}
        transition={{ duration: 0.45, ease: [0.32, 0.72, 0, 1] }}
      >
        <X className="absolute right-4 top-4 size-4 opacity-70" />
        <div className="flex flex-col gap-1.5 p-4">
          <p className="font-semibold text-foreground">{t("publishTitle", { order: 1 })}</p>
          <p className="text-sm text-muted-foreground">{t("publishBody")}</p>
        </div>
        <div className="space-y-5 px-4 pb-4">
          <div className="space-y-2">
            <Label>{t("account")}</Label>
            <span className="flex h-9 w-full items-center gap-2 rounded-md border border-input px-3 text-sm shadow-xs">
              <SocialProviderIcon provider="youtube" />
              <span className="flex-1">YouTube · {tFilm("channel")}</span>
              <ChevronDown className="size-4 opacity-50" />
            </span>
          </div>
          <div className="space-y-2">
            <Label>{t("title")}</Label>
            <Input readOnly tabIndex={-1} value={clip.hook} className="text-sm" />
          </div>
          <div className="space-y-2">
            <div className="flex items-center justify-between gap-2">
              <Label>{t("caption")}</Label>
              <span className="inline-flex h-8 items-center gap-1.5 rounded-md px-3 text-sm font-medium">
                {generating ? <Loader2 className="size-4 animate-spin" /> : <RefreshCw className="size-4" />}
                {generating ? t("generatingCaption") : t("generateCaption")}
              </span>
            </div>
            <Textarea readOnly tabIndex={-1} value={caption} rows={4} className="min-h-24 resize-none text-sm" />
          </div>
          <div className="space-y-2">
            <Label>{t("hashtags")}</Label>
            <Input readOnly tabIndex={-1} value={tFilm("hashtags")} className="text-sm" />
          </div>
          <div className="flex items-center justify-between rounded-lg border p-3">
            <div>
              <p className="text-sm font-medium">{t("scheduleLater")}</p>
              <p className="text-xs text-muted-foreground">{t("scheduleHint")}</p>
            </div>
            <span data-film="schedule-toggle"><Switch checked={frame.scheduleOn} tabIndex={-1} /></span>
          </div>
          {frame.scheduleOn && (
            <motion.div className="space-y-2" initial={{ opacity: 0, y: -4 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.25 }}>
              <Label>{t("publishAt")}</Label>
              <Input readOnly tabIndex={-1} value={when} className="text-sm" />
            </motion.div>
          )}
        </div>
        <div className="mt-auto flex flex-col gap-2 p-4">
          <Button variant="outline" tabIndex={-1}>{t("cancel")}</Button>
          <Button tabIndex={-1} data-film="schedule-submit">
            {frame.scheduling ? <Loader2 className="size-4 animate-spin" /> : frame.scheduleOn ? <Clock className="size-4" /> : <Send className="size-4" />}
            {frame.scheduling ? t("scheduling") : frame.scheduleOn ? t("schedule") : t("publishNow")}
          </Button>
        </div>
      </motion.div>
    </>
  );
}

function Toast() {
  const t = useTranslations("social");
  const tFilm = useTranslations("marketing.film");
  const locale = useLocale();
  const when = new Intl.DateTimeFormat(locale, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(2026, 9, 27, 18, 0));
  return (
    <motion.div
      className="absolute bottom-8 right-8 z-[60] flex w-[356px] items-start gap-2.5 rounded-lg border bg-popover p-4 text-[13px] text-popover-foreground shadow-lg"
      initial={{ opacity: 0, y: 24 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: 12 }}
      transition={{ duration: 0.4, ease: [0.16, 1, 0.3, 1] }}
    >
      <CheckCircle2 className="mt-px size-4 shrink-0 fill-foreground text-background" />
      <div>
        <p className="font-medium">{t("toasts.scheduled", { when })}</p>
        <p className="mt-0.5 text-muted-foreground">{t("toasts.scheduledDetail", { platform: "YouTube", account: tFilm("channel") })}</p>
      </div>
    </motion.div>
  );
}

function Cursor({ x, y, pressed }: { x: MotionValue<number>; y: MotionValue<number>; pressed: boolean }) {
  return (
    <motion.div className="pointer-events-none absolute left-0 top-0 z-[70]" style={{ x, y }}>
      <motion.svg
        width="22"
        height="26"
        viewBox="0 0 22 26"
        className="-translate-x-[3px] -translate-y-[2px] drop-shadow-[0_2px_3px_rgb(0_0_0/0.35)]"
        animate={{ scale: pressed ? 0.82 : 1 }}
        transition={{ duration: 0.12 }}
      >
        <path d="M3 2.5v18.2l4.9-4.6 3.1 7.1 3.2-1.4-3.1-7h6.8z" fill="#111" stroke="#fff" strokeWidth="1.6" strokeLinejoin="round" />
      </motion.svg>
      <AnimatePresence>
        {pressed && (
          <motion.span
            className="absolute -left-4 -top-4 size-8 rounded-full border-2 border-brand/70"
            initial={{ scale: 0.3, opacity: 0.9 }}
            animate={{ scale: 1.3, opacity: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.45 }}
          />
        )}
      </AnimatePresence>
    </motion.div>
  );
}

/* ─── Player ─── */

export function ProductFilm() {
  const t = useTranslations("marketing");
  const reduceMotion = useReducedMotion();
  const wrapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const scale = useCanvasScale(wrapRef);
  const inView = useInView(wrapRef, { amount: 0.35 });
  const [userPaused, setUserPaused] = useState(false);
  const time = useRef(0);
  const [clock, setClock] = useState(0);
  const [frame, setFrame] = useState<Frame>(() => frameAt(0));
  const frameKey = useRef(JSON.stringify(frame));
  const cursorX = useMotionValue(CURSOR_HOME.x);
  const cursorY = useMotionValue(CURSOR_HOME.y);
  const positions = useRef(new Map<string, { x: number; y: number }>());
  const [pressed, setPressed] = useState(false);
  const playing = inView && !userPaused && !reduceMotion;

  const locate = useCallback((to: Waypoint["to"]) => {
    if (typeof to !== "string") return to;
    const canvas = canvasRef.current;
    const el = canvas?.querySelector(`[data-film="${to}"]`);
    if (canvas && el) {
      const c = canvas.getBoundingClientRect();
      const r = el.getBoundingClientRect();
      const s = c.width / CANVAS_W;
      // The URL field is aimed at its start, where the caret is; everything else at its centre.
      const x = to === "url" ? (r.left - c.left) / s + 40 : (r.left - c.left + r.width / 2) / s;
      positions.current.set(to, { x, y: (r.top - c.top + r.height / 2) / s });
    }
    return positions.current.get(to) ?? CURSOR_HOME;
  }, []);

  const render = useCallback((t: number) => {
    const next = frameAt(t);
    const key = JSON.stringify(next);
    if (key !== frameKey.current) {
      frameKey.current = key;
      setFrame(next);
    }
    setClock(Math.floor(t * 10) / 10);
    setPressed(CLICKS.some((click) => t >= click && t < click + 0.16));

    let from = CURSOR_HOME;
    let pos = CURSOR_HOME;
    for (const point of PATH) {
      if (t < point.start) break;
      const to = locate(point.to);
      pos = t >= point.end ? to : {
        x: from.x + (to.x - from.x) * ease(between(t, point.start, point.end)),
        y: from.y + (to.y - from.y) * ease(between(t, point.start, point.end)),
      };
      from = to;
    }
    cursorX.set(pos.x);
    cursorY.set(pos.y);
  }, [cursorX, cursorY, locate]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    const tick = (now: number) => {
      time.current = (time.current + Math.min(now - last, 100) / 1000) % T.end;
      last = now;
      render(time.current);
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, render]);

  // Reduced motion: hold a representative still (the finished clips) instead of playing.
  useEffect(() => {
    if (!reduceMotion) return;
    time.current = T.complete + 0.5;
    render(time.current);
  }, [reduceMotion, render]);

  const seek = (seconds: number) => {
    time.current = seconds;
    render(seconds);
  };

  const chapter = FILM_CHAPTERS.reduce((current, c, i) => (clock >= c.start ? i : current), 0);

  return (
    <div>
      <div ref={wrapRef} className="relative overflow-hidden rounded-[22px] border bg-card p-1.5 shadow-[0_30px_80px_-30px_rgb(28_25_23/0.3)]">
        <div className="relative overflow-hidden rounded-[16px] border" style={{ height: CANVAS_H * scale }}>
          <div
            ref={canvasRef}
            inert
            aria-hidden
            className="absolute left-0 top-0 flex origin-top-left select-none bg-canvas text-foreground"
            style={{ width: CANVAS_W, height: CANVAS_H, transform: `scale(${scale})` }}
          >
            <Sidebar active={frame.scene === "create" ? "create" : "library"} />
            <div className="relative min-w-0 flex-1 overflow-hidden">
              <AnimatePresence mode="wait" initial={false}>
                <motion.div
                  key={frame.scene}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.25 }}
                >
                  {frame.scene === "create" ? <CreateScene frame={frame} /> : <TaskScene frame={frame} />}
                </motion.div>
              </AnimatePresence>
            </div>
            <AnimatePresence>{frame.focus && <FocusDialog key="focus" />}</AnimatePresence>
            <AnimatePresence>{frame.sheet && <PublishSheet key="sheet" frame={frame} />}</AnimatePresence>
            <AnimatePresence>{frame.toast && <Toast key="toast" />}</AnimatePresence>
            {!reduceMotion && <Cursor x={cursorX} y={cursorY} pressed={pressed} />}
          </div>
        </div>
      </div>

      {/* Controls: play/pause and chapters that double as the scrubber */}
      <div className="mt-6 flex items-start gap-4">
        <button
          type="button"
          onClick={() => setUserPaused((paused) => !paused)}
          disabled={!!reduceMotion}
          aria-label={playing ? t("film.pause") : t("film.play")}
          className="mt-0.5 flex size-9 shrink-0 items-center justify-center rounded-full border bg-background transition-colors hover:bg-muted disabled:opacity-40"
        >
          {playing ? <Pause className="size-4" /> : <Play className="ml-0.5 size-4" />}
        </button>
        <div className="grid flex-1 gap-4 sm:grid-cols-3">
          {FILM_CHAPTERS.map((c, i) => {
            const end = FILM_CHAPTERS[i + 1]?.start ?? T.end;
            const fill = i < chapter ? 1 : i > chapter ? 0 : between(clock, c.start, end);
            return (
              <button
                key={c.key}
                type="button"
                onClick={() => seek(c.start + 0.01)}
                aria-current={i === chapter}
                className={cn("text-left transition-opacity", i === chapter ? "opacity-100" : "opacity-50 hover:opacity-80")}
              >
                <span className="block h-0.5 overflow-hidden rounded-full bg-foreground/10">
                  <span className="block h-full bg-foreground" style={{ width: `${Math.round(fill * 100)}%` }} />
                </span>
                <span className="mt-3 flex items-baseline gap-2">
                  <span className="font-mono text-xs text-muted-foreground">0{i + 1}</span>
                  <span className="font-semibold tracking-tight">{t(`steps.items.${c.key}.title`)}</span>
                </span>
                <span className={cn("mt-1 block text-sm leading-relaxed text-muted-foreground", i !== chapter && "max-sm:hidden")}>
                  {t(`steps.items.${c.key}.description`)}
                </span>
              </button>
            );
          })}
        </div>
      </div>
    </div>
  );
}
