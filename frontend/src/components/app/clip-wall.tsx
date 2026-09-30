"use client";

import Link from "next/link";
import { useEffect } from "react";
import {
  AudioLines, Check, ChevronLeft, ChevronRight, Clapperboard, Download, Flame, Loader2, MessageSquare,
  Play, Scissors, Share2, Sparkles, Star, Trash2, TrendingUp,
} from "lucide-react";
import { ClipCover, ScoreRing } from "@/components/app/clip-cover";
import DynamicVideoPlayer from "@/components/dynamic-video-player";
import { TranscriptPreview } from "@/components/transcript-preview";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogTitle } from "@/components/ui/dialog";
import { getClipUrl } from "@/lib/clip-actions";
import { formatClipDuration } from "@/lib/generations";
import { cn } from "@/lib/utils";

export interface WallClip {
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

function clipTitle(clip: WallClip) {
  return clip.hook_title || `Clip ${clip.clip_order}`;
}

export function ClipTile({ clip, taskId, busy, best, editable, onOpen, onDownload, onDelete }: {
  clip: WallClip;
  taskId: string;
  busy: boolean;
  best: boolean;
  editable: boolean;
  onOpen: () => void;
  onDownload: () => void;
  onDelete: () => void;
}) {
  return (
    <article className="group @container animate-rise">
      <button
        type="button"
        onClick={onOpen}
        aria-label={`Preview ${clipTitle(clip)}`}
        className="block w-full rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
      >
        <ClipCover
          src={getClipUrl(clip.video_url, clip.filename)}
          className="aspect-[9/16] rounded-xl ring-1 ring-black/5 transition-shadow duration-200 group-hover:shadow-xl"
        >
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/75 via-transparent to-black/25" />
          {best && (
            <span className="absolute left-2 top-2 inline-flex items-center gap-1 rounded-full bg-brand px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-brand-foreground shadow">
              <Flame className="size-3" />Top pick
            </span>
          )}
          {clip.virality_score > 0 && (
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
        <h3 className="line-clamp-2 text-sm font-semibold leading-snug">{clipTitle(clip)}</h3>
        <p className="mt-0.5 text-xs tabular-nums text-muted-foreground">{clip.start_time} – {clip.end_time}</p>
        <div className="mt-2 flex items-center gap-1">
          <Button size="sm" variant="outline" className="h-7 flex-1 px-2 text-xs" onClick={onDownload} disabled={busy} aria-label={`Download ${clipTitle(clip)}`}>
            {busy ? <Loader2 className="size-3.5 animate-spin" /> : <Download className="size-3.5" />}<span className="hidden @min-[190px]:inline">Download</span>
          </Button>
          {editable && (
            <Button size="sm" variant="outline" className="h-7 px-2 text-xs" asChild>
              <Link href={`/tasks/${taskId}/edit?clip=${clip.id}`}><Scissors className="size-3.5" />Edit</Link>
            </Button>
          )}
          <Button size="icon-sm" variant="ghost" className="size-7 text-muted-foreground hover:bg-red-50 hover:text-red-600" onClick={onDelete} aria-label="Delete clip">
            <Trash2 className="size-3.5" />
          </Button>
        </div>
      </div>
    </article>
  );
}

const BREAKDOWN = [
  { key: "hook_score", label: "Hook", icon: MessageSquare },
  { key: "engagement_score", label: "Engagement", icon: TrendingUp },
  { key: "value_score", label: "Value", icon: Star },
  { key: "shareability_score", label: "Shareability", icon: Share2 },
] as const;

export function ClipFocus({ clip, taskId, index, total, busy, editable, onNavigate, onClose, onDownload, onDelete, hookTypeLabel }: {
  clip: WallClip | null;
  taskId: string;
  index: number;
  total: number;
  busy: boolean;
  editable: boolean;
  onNavigate: (delta: number) => void;
  onClose: () => void;
  onDownload: () => void;
  onDelete: () => void;
  hookTypeLabel: (hookType: string | null) => string;
}) {
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
                <span className="tabular-nums">Clip {index + 1} of {total}</span>
                <div className="flex items-center gap-1">
                  <Button size="icon-sm" variant="ghost" onClick={() => onNavigate(-1)} aria-label="Previous clip"><ChevronLeft className="size-4" /></Button>
                  <Button size="icon-sm" variant="ghost" onClick={() => onNavigate(1)} aria-label="Next clip"><ChevronRight className="size-4" /></Button>
                </div>
              </div>
              <div className="space-y-5 p-5">
                <div>
                  <DialogTitle className="font-display text-xl font-bold leading-tight">{clipTitle(clip)}</DialogTitle>
                  <DialogDescription className="mt-1 tabular-nums">
                    {clip.start_time} – {clip.end_time} · {formatClipDuration(clip.duration)}
                  </DialogDescription>
                </div>

                {clip.virality_score > 0 && (
                  <section className="rounded-xl border p-4">
                    <div className="flex items-center gap-4">
                      <ScoreRing score={clip.virality_score} size={56} className="text-foreground [&>span]:text-base" />
                      <div>
                        <p className="text-sm font-semibold">Virality score</p>
                        <p className="text-xs text-muted-foreground">
                          {clip.hook_type && clip.hook_type !== "none" ? `${hookTypeLabel(clip.hook_type)} · ` : ""}
                          {Math.round(clip.relevance_score * 100)}% relevance
                        </p>
                      </div>
                    </div>
                    <div className="mt-4 grid grid-cols-2 gap-x-5 gap-y-3">
                      {BREAKDOWN.map(({ key, label, icon: Icon }) => (
                        <div key={key} className="space-y-1.5">
                          <div className="flex items-center justify-between text-xs">
                            <span className="flex items-center gap-1.5 text-muted-foreground"><Icon className="size-3" />{label}</span>
                            <span className="font-medium tabular-nums">{clip[key]}/25</span>
                          </div>
                          <div className="h-1.5 overflow-hidden rounded-full bg-muted">
                            <div className="h-full rounded-full bg-foreground transition-[width] duration-500" style={{ width: `${(clip[key] / 25) * 100}%` }} />
                          </div>
                        </div>
                      ))}
                    </div>
                  </section>
                )}

                {clip.text && <TranscriptPreview text={clip.text} clipTitle={clipTitle(clip)} />}
              </div>
              <div className="mt-auto flex flex-wrap items-center gap-2 border-t bg-muted/30 px-5 py-3">
                <Button size="sm" onClick={onDownload} disabled={busy}>
                  {busy ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}Download
                </Button>
                {editable && (
                  <Button size="sm" variant="outline" asChild>
                    <Link href={`/tasks/${taskId}/edit?clip=${clip.id}`}><Scissors className="size-4" />Open in editor</Link>
                  </Button>
                )}
                <Button size="sm" variant="ghost" className="ml-auto text-muted-foreground hover:bg-red-50 hover:text-red-600" onClick={onDelete}>
                  <Trash2 className="size-4" />Delete
                </Button>
              </div>
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

const STEPS = [
  { label: "Fetching video", icon: Download, until: 20 },
  { label: "Transcribing", icon: AudioLines, until: 45 },
  { label: "Finding moments", icon: Sparkles, until: 65 },
  { label: "Rendering clips", icon: Clapperboard, until: 100 },
];

function stepFromMessage(message: string) {
  const text = message.toLowerCase();
  if (/render|clip \d|saving/.test(text)) return 3;
  if (/analy|moment|segment|ai /.test(text)) return 2;
  if (/transcri/.test(text)) return 1;
  if (/download|fetch/.test(text)) return 0;
  return null;
}

export function ProcessingPanel({ status, progress, message, readyCount }: { status: string; progress: number; message: string; readyCount: number }) {
  const byProgress = STEPS.findIndex((step) => progress < step.until);
  const activeStep = status === "queued" ? -1 : stepFromMessage(message) ?? (byProgress === -1 ? STEPS.length - 1 : byProgress);
  return (
    <section className="overflow-hidden rounded-2xl border bg-background" aria-live="polite">
      <div className="relative h-1 bg-muted">
        <div className="absolute inset-y-0 left-0 overflow-hidden bg-brand transition-[width] duration-700 ease-out progress-sheen" style={{ width: `${Math.max(progress, status === "queued" ? 2 : 6)}%` }} />
      </div>
      <div className="flex flex-col gap-6 p-5 sm:p-6 md:flex-row md:items-center md:justify-between">
        <div className="min-w-0">
          <p className="shimmer text-lg font-semibold">{message || (status === "queued" ? "Waiting in queue" : "Processing")}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {readyCount > 0 ? `${readyCount} clip${readyCount === 1 ? "" : "s"} ready below — more on the way.` : "Grab a coffee — clips appear here the moment they render."}
          </p>
        </div>
        <ol className="grid grid-cols-4 gap-2 md:flex md:gap-1">
          {STEPS.map((step, index) => {
            const done = activeStep > index;
            const active = activeStep === index;
            return (
              <li key={step.label} className="flex flex-col items-center gap-1.5 md:w-24">
                <span className={cn(
                  "flex size-9 items-center justify-center rounded-full border transition-colors",
                  done && "border-foreground bg-foreground text-background",
                  active && "border-brand bg-brand-soft text-brand",
                  !done && !active && "text-muted-foreground",
                )}>
                  {done ? <Check className="size-4" /> : active ? <step.icon className="size-4 motion-safe:animate-pulse" /> : <step.icon className="size-4" />}
                </span>
                <span className={cn("text-center text-[11px] leading-tight", active ? "font-medium text-foreground" : "text-muted-foreground")}>{step.label}</span>
              </li>
            );
          })}
        </ol>
      </div>
      {progress > 0 && <p className="sr-only">{progress}% complete</p>}
    </section>
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
