"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { useTranslations } from "next-intl";
import {
  Check, ChevronLeft, ChevronRight, Download, Flame, Loader2, MessageSquare,
  Play, Scissors, Share2, Star, Trash2, TrendingUp,
} from "lucide-react";
import { ClipCover, ScoreRing } from "@/components/app/clip-cover";
import DynamicVideoPlayer from "@/components/dynamic-video-player";
import { TranscriptPreview } from "@/components/transcript-preview";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { getClipUrl } from "@/lib/clip-actions";
import { formatClipDuration } from "@/lib/generations";
import { cn } from "@/lib/utils";
import { SINGLE_OUTPUT, type SourceRange, type Workflow, type WorkflowClipFields } from "@/lib/workflows";

export interface WallClip extends WorkflowClipFields {
  id: string;
  filename: string;
  start_time: string;
  end_time: string;
  duration: number;
  text: string;
  relevance_score: number;
  clip_order: number;
  video_url: string;
  virality_score: number;
  hook_score: number;
  engagement_score: number;
  value_score: number;
  shareability_score: number;
  hook_type: string | null;
  hook_title: string | null;
}

type TaskT = ReturnType<typeof useTranslations<"task">>;

/** Parts, recaps and trailers are planned pieces, not scored picks. */
const UNSCORED_ROLES = new Set(["part", "recap", "trailer"]);

export function isPart(clip: WorkflowClipFields): clip is WorkflowClipFields & { part_number: number } {
  return clip.workflow_role === "part" && typeof clip.part_number === "number";
}

export function showsScore(clip: Pick<WallClip, "virality_score" | "workflow_role">) {
  return clip.virality_score > 0 && !UNSCORED_ROLES.has(clip.workflow_role ?? "");
}

// "Part 2: ", "Teil 2 – ", "第2部分：", "Parte 2 · " in front of a burned-in hook title.
const PART_PREFIX = /^\s*\S{0,12}?\s*(\d+)\s*\S{0,3}?\s*[:：–—\-·|]\s*/u;

/** A part's own title: the planned title, else its hook title without the "Part n:" label. */
export function partTitle(clip: { hook_title?: string | null } & Pick<WallClip, "part_number" | "workflow_meta">): string | null {
  const planned = clip.workflow_meta?.title?.trim();
  if (planned) return planned;
  const hook = clip.hook_title?.trim();
  if (!hook) return null;
  const match = PART_PREFIX.exec(hook);
  if (match && Number(match[1]) === clip.part_number) return hook.slice(match[0].length).trim() || null;
  return hook;
}

/**
 * The clip's title. Parts show their own title (their number is badged next
 * to it), else "Part n"; other clips their hook title, else "Clip n".
 */
export function clipTitle(clip: WallClip, t: TaskT) {
  if (isPart(clip)) return partTitle(clip) ?? t("partLabel", { n: clip.part_number });
  return clip.hook_title || t("clipNumber", { order: clip.clip_order });
}

/** Seconds as M:SS, or H:MM:SS past an hour, for pointing into the source video. */
export function sourceTimestamp(seconds: number) {
  const whole = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(whole / 3600);
  const mins = Math.floor((whole % 3600) / 60);
  const secs = (whole % 60).toString().padStart(2, "0");
  return hours ? `${hours}:${mins.toString().padStart(2, "0")}:${secs}` : `${mins}:${secs}`;
}

/** The planned source ranges of a clip stitched from several moments, else null. */
function sourceMoments(clip: WallClip): SourceRange[] | null {
  const ranges = clip.workflow_meta?.ranges;
  return Array.isArray(ranges) && ranges.length > 1 ? ranges : null;
}

export function ClipTile({ clip, taskId, busy, best, editable, partTotal, onOpen, onDownload, onDelete }: {
  clip: WallClip;
  taskId: string;
  busy: boolean;
  best: boolean;
  editable: boolean;
  /** Parts in the series, for the "Part n of N" badge. */
  partTotal?: number;
  onOpen: () => void;
  onDownload: () => void;
  onDelete?: () => void;
}) {
  const t = useTranslations("task");
  const title = clipTitle(clip, t);
  const why = clip.workflow_meta?.why;
  return (
    <article className="group @container animate-rise">
      <button
        type="button"
        onClick={onOpen}
        aria-label={t("preview", { title })}
        className="block w-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <ClipCover
          src={getClipUrl(clip.video_url, clip.filename)}
          className="aspect-[9/16] rounded-xl ring-1 ring-black/5 transition-shadow duration-200 group-hover:shadow-xl"
        >
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-black/25" />
          {isPart(clip) ? (
            <span className="absolute left-2 top-2 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-white backdrop-blur">
              {partTotal ? t("partOf", { n: clip.part_number, total: partTotal }) : t("partLabel", { n: clip.part_number })}
            </span>
          ) : best && (
            <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-brand px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-brand-foreground shadow">
              <Flame className="size-3" />{t("topPick")}
            </span>
          )}
          {showsScore(clip) && (
            <span className="absolute right-2 top-2 rounded-full bg-black/55 text-white backdrop-blur">
              <ScoreRing score={clip.virality_score} size={36} />
            </span>
          )}
          <span className="absolute inset-0 flex items-center justify-center opacity-0 transition-opacity group-hover:opacity-100">
            <span className="flex size-12 items-center justify-center rounded-full bg-white/90 text-black shadow-lg"><Play className="ml-0.5 size-5 fill-current" /></span>
          </span>
          <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">{formatClipDuration(clip.duration)}</span>
        </ClipCover>
      </button>
      <div className="mt-2.5 px-0.5">
        {why ? (
          <Tooltip>
            <TooltipTrigger asChild>
              <h3 tabIndex={0} className="line-clamp-2 cursor-default text-sm font-semibold leading-snug focus-visible:outline-none focus-visible:underline">{title}</h3>
            </TooltipTrigger>
            <TooltipContent className="max-w-64">{why}</TooltipContent>
          </Tooltip>
        ) : (
          <h3 className="line-clamp-2 text-sm font-semibold leading-snug">{title}</h3>
        )}
        <p className="mt-0.5 text-xs tabular-nums text-muted-foreground">{clip.start_time} – {clip.end_time}</p>
        <div className="mt-2 flex items-center gap-1">
          <Button size="sm" variant="outline" className="h-7 flex-1 px-2 text-xs" onClick={onDownload} disabled={busy} aria-label={t("downloadNamed", { title })}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}<span className="hidden @min-[190px]:inline">{t("download")}</span>
          </Button>
          {editable && (
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs" asChild>
              <Link href={`/tasks/${taskId}/edit?clip=${clip.id}`}><Scissors className="size-3.5" />{t("edit")}</Link>
            </Button>
          )}
          {onDelete && (
            <Button size="icon-sm" variant="ghost" className="size-7 text-muted-foreground hover:bg-red-50 hover:text-red-600" onClick={onDelete} aria-label={t("deleteClip")}>
              <Trash2 className="size-3.5" />
            </Button>
          )}
        </div>
      </div>
    </article>
  );
}

const BREAKDOWN = [
  { key: "hook_score", labelKey: "hook", icon: MessageSquare },
  { key: "engagement_score", labelKey: "engagement", icon: TrendingUp },
  { key: "value_score", labelKey: "value", icon: Star },
  { key: "shareability_score", labelKey: "shareability", icon: Share2 },
] as const;

export function ClipFocus({ clip, taskId, index, total, busy, editable, onNavigate, onClose, onDownload, onDelete, hookTypeLabel, actions, children }: {
  clip: WallClip | null;
  taskId: string;
  index: number;
  total: number;
  busy: boolean;
  editable: boolean;
  onNavigate: (delta: number) => void;
  onClose: () => void;
  onDownload: () => void;
  onDelete?: () => void;
  hookTypeLabel: (hookType: string | null) => string;
  /** Extra footer actions for the focused clip (e.g. publishing). */
  actions?: React.ReactNode;
  /** Extra detail content rendered below the transcript. */
  children?: React.ReactNode;
}) {
  const t = useTranslations("task");
  const moments = clip ? sourceMoments(clip) : null;
  const meta = clip?.workflow_meta;
  useEffect(() => {
    if (!clip) return;
    const onKey = (event: KeyboardEvent) => {
      if ((event.target as HTMLElement | null)?.closest("input, textarea")) return;
      if (event.key === "ArrowRight" || event.key === "j") { event.preventDefault(); onNavigate(1); }
      if (event.key === "ArrowLeft" || event.key === "k") { event.preventDefault(); onNavigate(-1); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [clip, onNavigate]);

  return (
    <Dialog open={clip !== null} onOpenChange={(open) => { if (!open) onClose(); }}>
      <DialogContent className="max-h-[92dvh] max-w-5xl gap-0 overflow-hidden p-0 md:grid-cols-[auto_1fr]">
        {clip && (
          <>
            <div className="relative flex items-center justify-center bg-stone-950 p-3 md:p-4">
              <DynamicVideoPlayer key={clip.id} src={getClipUrl(clip.video_url, clip.filename)} autoPlay className="h-[min(58dvh,560px)]! md:h-[min(80dvh,640px)]!" />
            </div>
            <div className="flex min-h-0 flex-col overflow-y-auto">
              <div className="flex items-center justify-between gap-2 border-b px-5 py-3 pr-12 text-xs text-muted-foreground">
                <span className="tabular-nums">{t("position", { current: index + 1, total })}</span>
                <div className="flex items-center gap-1">
                  <Button size="icon-sm" variant="ghost" onClick={() => onNavigate(-1)} aria-label={t("previous")}><ChevronLeft className="size-4" /></Button>
                  <Button size="icon-sm" variant="ghost" onClick={() => onNavigate(1)} aria-label={t("next")}><ChevronRight className="size-4" /></Button>
                </div>
              </div>
              <div className="space-y-5 p-5">
                <div>
                  {isPart(clip) && (
                    <p className="mb-1 text-xs font-medium tabular-nums text-muted-foreground">
                      {meta?.part_count ? t("partOf", { n: clip.part_number, total: meta.part_count }) : t("partLabel", { n: clip.part_number })}
                    </p>
                  )}
                  <DialogTitle className="font-display text-xl font-bold leading-tight">{clipTitle(clip, t)}</DialogTitle>
                  <DialogDescription className="mt-1 tabular-nums">
                    {moments ? formatClipDuration(clip.duration) : <>{clip.start_time} – {clip.end_time} · {formatClipDuration(clip.duration)}</>}
                  </DialogDescription>
                </div>

                {isPart(clip) && (meta?.delivers || meta?.ends_on) && (
                  <section className="space-y-1.5 text-sm">
                    {meta.delivers && <p>{meta.delivers}</p>}
                    {meta.ends_on && <p className="text-muted-foreground">{t("endsOn", { text: meta.ends_on })}</p>}
                  </section>
                )}

                {moments && (
                  <section>
                    <h3 className="text-xs font-medium text-muted-foreground">{t("sourceMoments")}</h3>
                    <ol className="mt-1.5 flex flex-wrap gap-1.5 text-xs tabular-nums">
                      {moments.map(([start, end], i) => (
                        <li key={`${start}-${i}`} className="rounded-md bg-muted px-2 py-1">{sourceTimestamp(start)} – {sourceTimestamp(end)}</li>
                      ))}
                    </ol>
                  </section>
                )}

                {showsScore(clip) && (
                  <section className="rounded-xl border p-4">
                    <div className="flex items-center gap-4">
                      <ScoreRing score={clip.virality_score} size={56} className="text-foreground [&>span]:text-base" />
                      <div>
                        <p className="text-sm font-semibold">{t("virality")}</p>
                        <p className="text-xs text-muted-foreground">
                          {clip.hook_type && clip.hook_type !== "none"
                            ? t("relevanceWithHook", { hook: hookTypeLabel(clip.hook_type), percent: Math.round(clip.relevance_score * 100) })
                            : t("relevance", { percent: Math.round(clip.relevance_score * 100) })}
                        </p>
                      </div>
                    </div>
                    {BREAKDOWN.some(({ key }) => clip[key] > 0) && <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3">
                      {BREAKDOWN.map(({ key, labelKey, icon: Icon }) => (
                        <div key={key} className="space-y-1.5">
                          <div className="flex items-center justify-between text-xs">
                            <span className="flex items-center gap-1.5 text-muted-foreground"><Icon className="size-3" />{t(`scores.${labelKey}`)}</span>
                            <span className="font-medium tabular-nums">{clip[key]}/25</span>
                          </div>
                          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                            <div className="h-full rounded-full bg-foreground transition-[width] duration-500" style={{ width: `${(clip[key] / 25) * 100}%` }} />
                          </div>
                        </div>
                      ))}
                    </div>}
                  </section>
                )}

                {clip.text && <TranscriptPreview text={clip.text} clipTitle={clipTitle(clip, t)} />}
                {children}
              </div>
              <div className="mt-auto flex flex-wrap items-center gap-2 border-t bg-muted/30 px-5 py-3">
                <Button size="sm" onClick={onDownload} disabled={busy}>
                  {busy ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}{t("download")}
                </Button>
                {editable && (
                  <Button size="sm" variant="outline" asChild>
                    <Link href={`/tasks/${taskId}/edit?clip=${clip.id}`}><Scissors className="size-4" />{t("openInEditor")}</Link>
                  </Button>
                )}
                {actions}
                {onDelete && (
                  <Button size="sm" variant="ghost" className="ml-auto text-muted-foreground hover:bg-red-50 hover:text-red-600" onClick={onDelete}>
                    <Trash2 className="size-4" />{t("delete")}
                  </Button>
                )}
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

/** Pipeline steps, the share of the progress bar each one covers, and the hue its glow takes. */
const STEPS = [
  { id: "fetching", until: 20, hue: 245 },
  { id: "transcribing", until: 45, hue: 305 },
  { id: "finding", until: 65, hue: 80 },
  { id: "rendering", until: 100, hue: 25 },
] as const;

function activeStep(status: string, progress: number, message: string) {
  if (status === "queued") return -1;
  const text = message.toLowerCase();
  // Workflow planning ("Mapping the story...", "Finding where '<topic>' comes up..."), checked
  // first: the quoted topic is the user's own words and may say anything.
  if (/^(reading|mapping|planning|picking|choosing|writing|checking|finding where)\b/.test(text)) return 2;
  if (/render|creating (clip|part|recap|trailer)|clip \d|saving/.test(text)) return 3;
  if (/analy|moment|segment|ai /.test(text)) return 2;
  if (/transcri/.test(text)) return 1;
  if (/download|fetch|preparing/.test(text)) return 0;
  const byProgress = STEPS.findIndex((step) => progress < step.until);
  return byProgress === -1 ? STEPS.length - 1 : byProgress;
}

/** "Creating clip 3/5..." or "Creating part 2/4..." → { current, total } */
export function clipProgressFromMessage(message: string) {
  // A topic search message quotes the user's topic, which could look like a counter.
  if (/^finding where/i.test(message)) return null;
  const match = /(?:clip|part|recap|trailer) (\d+)\s*\/\s*(\d+)/i.exec(message);
  return match ? { current: Number(match[1]), total: Number(match[2]) } : null;
}

function stepHue(step: number) {
  return step < 0 ? 270 : STEPS[Math.min(step, STEPS.length - 1)].hue;
}

/** Seconds since `since`, ticking every few seconds. */
function useElapsed(since: number | undefined) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (since === undefined) return;
    const id = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(id);
  }, [since]);
  return since === undefined ? 0 : Math.max(0, (now - since) / 1000);
}

export function ProcessingPanel({ status, progress, message, startedAt, elapsedSeconds, clipProgress, workflow = "highlights" }: {
  status: string;
  progress: number;
  message: string;
  /** Relabels the steps and the counter for what the generation makes. */
  workflow?: Workflow;
  /** Epoch ms the task started; drives the time-left estimate. */
  startedAt?: number;
  /** Overrides the clock (landing demo). */
  elapsedSeconds?: number;
  /** Overrides parsing "clip x/y" out of the message. */
  clipProgress?: { current: number; total: number } | null;
}) {
  const t = useTranslations("task");
  const ticking = useElapsed(elapsedSeconds === undefined ? startedAt : undefined);
  const elapsed = elapsedSeconds ?? ticking;
  const step = activeStep(status, progress, message);
  const clip = clipProgress === undefined ? clipProgressFromMessage(message) : clipProgress;
  const remaining = progress > 0 ? (elapsed * (100 - progress)) / progress : Infinity;
  const eta = status === "queued" ? t("eta.queued")
    : progress < 10 || elapsed < 5 ? t("eta.estimating")
      : remaining < 50 ? t("eta.underMinute")
        : t("eta.minutes", { count: Math.round(remaining / 60) });
  return (
    <section className="relative isolate" aria-live="polite">
      <div aria-hidden className="pointer-events-none absolute inset-x-0 -inset-y-16 -z-10 overflow-hidden [mask-image:radial-gradient(60%_70%_at_30%_50%,black,transparent)]">
        <div className="aura-blob absolute -top-10 left-[-5%] h-72 w-[60%] rounded-full opacity-45 blur-3xl"
          style={{ background: `radial-gradient(circle at 40% 50%, oklch(0.87 0.06 ${stepHue(step)}), transparent 65%)` }} />
      </div>
      <div className="flex items-baseline justify-between gap-6">
        <p className="font-display text-2xl font-bold tracking-tight tabular-nums">{eta}</p>
        {clip && !SINGLE_OUTPUT.has(workflow) && <p className="text-sm tabular-nums text-muted-foreground">{workflow === "series" ? t("partOf", { n: clip.current, total: clip.total }) : t("clipOf", clip)}</p>}
      </div>
      <div className="mt-4 h-2 rounded-full bg-stone-200/60 dark:bg-stone-800/60" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progress} aria-label={t("percentComplete", { progress })}>
        <div className="metal-flow h-full rounded-full transition-[width] duration-700 ease-out" style={{ width: `${Math.max(progress, 1)}%` }} />
      </div>
      <ol className="mt-2.5 grid text-xs" style={{ gridTemplateColumns: STEPS.map((s, i) => `${s.until - (i ? STEPS[i - 1].until : 0)}fr`).join(" ") }}>
        {STEPS.map((s, i) => (
          <li key={s.id} className={cn("flex items-center gap-1 truncate pr-2", i < step ? "text-foreground/60" : i === step ? "font-medium text-foreground" : "text-foreground/35")}>
            {i < step && <Check className="size-3 shrink-0" />}
            <span className="truncate">{stepLabel(t, workflow, s.id)}</span>
          </li>
        ))}
      </ol>
      <p className="mt-3 text-xs text-muted-foreground">{t("leavePage")}</p>
    </section>
  );
}

function stepLabel(t: TaskT, workflow: Workflow, step: (typeof STEPS)[number]["id"]) {
  const key = `stepsByWorkflow.${workflow}.${step}`;
  return t.has(key) ? t(key) : t(`steps.${step}`);
}

/** The clip grid while processing: finished clips, then 9:16 placeholders for the rest. */
export function ProcessingSlots({ status, progress, message, total, className, children }: {
  status: string;
  progress: number;
  message: string;
  total: number;
  className?: string;
  children: React.ReactNode[];
}) {
  const empty = Math.max(0, total - children.length);
  const hue = stepHue(activeStep(status, progress, message));
  return (
    <div className={cn("grid grid-cols-2 gap-x-4 gap-y-6 sm:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5", className)}>
      {children}
      {Array.from({ length: empty }, (_, i) => (
        <div key={`slot-${i}`} aria-hidden className="animate-rise" style={{ animationDelay: `${i * 60}ms` }}>
          <div className="relative aspect-[9/16] overflow-hidden rounded-xl bg-stone-100/70 ring-1 ring-black/[0.04] dark:bg-stone-900/60 dark:ring-white/5">
            {i === 0 && (
              <span className="absolute inset-[-30%] opacity-40 blur-2xl transition-[background] duration-1000"
                style={{ background: `radial-gradient(circle at 50% 60%, oklch(0.87 0.05 ${hue}), transparent 60%)` }} />
            )}
          </div>
          <div className="mt-2.5 space-y-1.5 px-0.5">
            <div className="h-3 w-4/5 rounded bg-muted" />
            <div className="h-2.5 w-2/5 rounded bg-muted/70" />
          </div>
        </div>
      ))}
    </div>
  );
}

export function EmptyState({ icon, title, body, tone, children }: { icon: React.ReactNode; title: string; body: string; tone?: "error"; children?: React.ReactNode }) {
  return (
    <div className="flex flex-col items-center rounded-2xl border border-dashed px-6 py-16 text-center">
      <span className={cn("flex size-14 items-center justify-center rounded-2xl", tone === "error" ? "bg-red-50 text-red-600" : "bg-muted text-muted-foreground")}>{icon}</span>
      <h2 className="mt-4 text-lg font-semibold">{title}</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">{body}</p>
      {children && <div className="mt-6 flex gap-2">{children}</div>}
    </div>
  );
}
