"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { useLocale, useTranslations } from "next-intl";
import {
  ArrowRight, BarChart3, CalendarDays, Captions, Check, Clock, Download, ExternalLink, Film, Heart, Link2, ListOrdered,
  Loader2, Play, Scissors, Send, Sparkles, Target, Upload, Wand2, X, ListChecks, Brain, SlidersHorizontal,
} from "lucide-react";

import { ClipCover } from "@/components/app/clip-cover";
import { StatusBadge } from "@/components/app/status-badge";
import { SocialProviderIcon } from "@/components/social-provider-icon";
import { WorkflowGlyph } from "@/components/workflow-picker";
import { Button } from "@/components/ui/button";
import { intlLocale } from "@/i18n/config";
import { clipFileUrl, type AgentCard, type AgentJobCard, type AgentOption, type ApprovalItem, type CardPost, type OptionIcon } from "@/lib/agent";
import { coverClipUrl, formatClipDuration } from "@/lib/generations";
import { PROVIDER_LABELS, type SocialProviderId } from "@/lib/social";
import type { Workflow } from "@/lib/workflows";
import { cn } from "@/lib/utils";

const ICONS: Record<OptionIcon, typeof Sparkles> = {
  sparkles: Sparkles, list: ListOrdered, target: Target, recap: ListChecks, trailer: Play, calendar: CalendarDays,
  send: Send, scissors: Scissors, wand: Wand2, captions: Captions, chart: BarChart3, youtube: Play, tiktok: Play,
  instagram: Play, clock: Clock, check: Check, x: X, upload: Upload, link: Link2, film: Film, heart: Heart,
};
const WORKFLOW_ICONS: Partial<Record<OptionIcon, Workflow>> = {
  sparkles: "highlights", list: "series", target: "topic", recap: "recap", trailer: "trailer",
};
const PLATFORM_ICONS: Partial<Record<OptionIcon, SocialProviderId>> = { youtube: "youtube", tiktok: "tiktok", instagram: "instagram" };

function OptionGlyph({ icon, className }: { icon?: OptionIcon; className?: string }) {
  if (!icon) return null;
  const platform = PLATFORM_ICONS[icon];
  if (platform) return <SocialProviderIcon provider={platform} className={className} />;
  const Icon = ICONS[icon];
  return <Icon className={className} aria-hidden />;
}

function useFormats() {
  const locale = useLocale();
  return useMemo(() => ({
    day: new Intl.DateTimeFormat(intlLocale(locale), { weekday: "short", month: "short", day: "numeric" }),
    time: new Intl.DateTimeFormat(intlLocale(locale), { hour: "numeric", minute: "2-digit" }),
    weekday: new Intl.DateTimeFormat(intlLocale(locale), { weekday: "short" }),
    date: new Intl.DateTimeFormat(intlLocale(locale), { month: "short", day: "numeric" }),
  }), [locale]);
}

const shell = "rounded-2xl border bg-card text-card-foreground shadow-xs";

// ----------------------------------------------------------------------
// Options: the agent's tappable UI
// ----------------------------------------------------------------------
export function OptionsCard({ card, answered, disabled, onReply }: {
  card: Extract<AgentCard, { type: "options" }>;
  /** The person's next message, once they answered: the matching option stays highlighted. */
  answered: string | null;
  disabled?: boolean;
  onReply: (reply: string, label: string) => void;
}) {
  const t = useTranslations("agent");
  const [picked, setPicked] = useState<string[]>([]);
  const done = answered !== null;
  const chosen = (option: AgentOption) => done && (answered === option.reply || answered.includes(option.reply));
  const locked = done || disabled;
  const formats = useFormats();

  const choose = (option: AgentOption) => {
    if (locked) return;
    if (card.multiple) {
      setPicked((current) => current.includes(option.id) ? current.filter((id) => id !== option.id) : [...current, option.id]);
      return;
    }
    onReply(option.reply, option.label);
  };
  const confirm = () => {
    const selected = card.options.filter((option) => picked.includes(option.id));
    if (!selected.length) return;
    onReply(selected.map((option) => option.reply).join("\n"), selected.map((option) => option.label).join(", "));
  };
  const isOn = (option: AgentOption) => (card.multiple && !done ? picked.includes(option.id) : chosen(option));

  return (
    <div className={cn("space-y-2.5", done && "opacity-80")}>
      {card.question && <p className="text-sm font-medium">{card.question}</p>}

      {card.style === "chips" && (
        <div className="flex flex-wrap gap-2">
          {card.options.map((option) => (
            <button
              key={option.id}
              type="button"
              disabled={locked && !isOn(option)}
              aria-pressed={isOn(option)}
              onClick={() => choose(option)}
              className={cn(
                "inline-flex h-8 items-center gap-1.5 rounded-full border bg-background px-3 text-sm transition-all hover:-translate-y-px hover:border-foreground/30 hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-40",
                isOn(option) && "border-foreground bg-foreground text-background hover:bg-foreground",
                locked && "pointer-events-none",
              )}
            >
              <OptionGlyph icon={option.icon} className="size-3.5" />
              {option.label}
            </button>
          ))}
        </div>
      )}

      {card.style === "cards" && (
        <div className={cn("grid gap-2", card.options.length > 2 ? "sm:grid-cols-3" : "sm:grid-cols-2", card.options.length === 4 && "sm:grid-cols-2", card.options.length >= 5 && "grid-cols-2 sm:grid-cols-3")}>
          {card.options.map((option) => {
            const workflow = option.icon ? WORKFLOW_ICONS[option.icon] : undefined;
            const on = isOn(option);
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={on}
                disabled={locked && !on}
                onClick={() => choose(option)}
                className={cn(
                  "group/workflow relative flex flex-col rounded-xl border bg-background p-3 text-left transition-all hover:-translate-y-0.5 hover:border-foreground/30 hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40",
                  on && "border-foreground ring-1 ring-foreground",
                  locked && "pointer-events-none",
                )}
                aria-checked={on}
                role={card.multiple ? "checkbox" : undefined}
              >
                {workflow ? (
                  <WorkflowGlyph id={workflow} />
                ) : option.icon ? (
                  <span className="flex size-9 items-center justify-center rounded-lg bg-muted">
                    <OptionGlyph icon={option.icon} className="size-4" />
                  </span>
                ) : null}
                <span className={cn("block text-sm font-medium", option.icon && "mt-2")}>{option.label}</span>
                {option.description && <span className="mt-0.5 block text-xs leading-snug text-muted-foreground">{option.description}</span>}
                {option.recommended && !done && (
                  <span className="absolute right-2.5 top-2.5 rounded-full bg-brand-soft px-1.5 py-0.5 text-[10px] font-medium text-brand">{t("options.recommended")}</span>
                )}
                {on && <Check className="absolute right-2.5 top-2.5 size-4" aria-hidden />}
              </button>
            );
          })}
        </div>
      )}

      {card.style === "clips" && (
        <div className="-mx-1 flex snap-x gap-2.5 overflow-x-auto px-1 pb-1">
          {card.options.map((option) => {
            const on = isOn(option);
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={on}
                disabled={locked && !on}
                onClick={() => choose(option)}
                className={cn(
                  "group w-32 shrink-0 snap-start text-left focus-visible:outline-none disabled:opacity-40",
                  locked && "pointer-events-none",
                )}
              >
                <ClipCover
                  src={option.project_id && option.clip_id ? clipFileUrl(option.project_id, option.clip_id) : null}
                  className={cn("aspect-[9/16] rounded-xl ring-offset-2 ring-offset-background transition-all group-hover:-translate-y-0.5 group-focus-visible:ring-2 group-focus-visible:ring-ring", on && "ring-2 ring-foreground")}
                >
                  {typeof option.score === "number" && (
                    <span className="absolute left-1.5 top-1.5 rounded-md bg-black/60 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-white backdrop-blur">{option.score}</span>
                  )}
                  {on && <span className="absolute right-1.5 top-1.5 flex size-5 items-center justify-center rounded-full bg-white text-black"><Check className="size-3.5" /></span>}
                </ClipCover>
                <span className="mt-1.5 line-clamp-2 text-xs font-medium leading-snug">{option.label}</span>
                {option.description && <span className="line-clamp-1 text-[11px] text-muted-foreground">{option.description}</span>}
              </button>
            );
          })}
        </div>
      )}

      {card.style === "slots" && (
        <div className="flex flex-wrap gap-2">
          {card.options.map((option) => {
            const on = isOn(option);
            const when = option.when ? new Date(option.when) : null;
            return (
              <button
                key={option.id}
                type="button"
                aria-pressed={on}
                disabled={locked && !on}
                onClick={() => choose(option)}
                className={cn(
                  "flex min-w-24 flex-col items-start rounded-xl border bg-background px-3 py-2 text-left transition-all hover:-translate-y-px hover:border-foreground/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-40",
                  on && "border-foreground ring-1 ring-foreground",
                  locked && "pointer-events-none",
                )}
              >
                <span className="text-[11px] font-medium uppercase text-muted-foreground">{when ? formats.day.format(when) : option.label}</span>
                <span className="text-sm font-semibold tabular-nums">{when ? formats.time.format(when) : ""}</span>
                {option.description && <span className="text-[11px] text-muted-foreground">{option.description}</span>}
              </button>
            );
          })}
        </div>
      )}

      {card.multiple && !done && (
        <Button size="sm" onClick={confirm} disabled={!picked.length || disabled} className="rounded-full">
          {picked.length ? t("options.confirmCount", { count: picked.length }) : t("options.pickSome")}
        </Button>
      )}
    </div>
  );
}

// ----------------------------------------------------------------------
// Library cards
// ----------------------------------------------------------------------
function ClipTile({ projectId, clipId, title, score, duration, caption }: {
  projectId: string; clipId: string; title: string | null; score: number | null; duration: number | null; caption?: string | null;
}) {
  return (
    <Link href={`/tasks/${projectId}?clip=${clipId}`} className="group w-28 shrink-0 snap-start sm:w-32">
      <ClipCover src={clipFileUrl(projectId, clipId)} className="aspect-[9/16] rounded-xl transition-transform group-hover:-translate-y-0.5">
        {typeof score === "number" && (
          <span className="absolute left-1.5 top-1.5 rounded-md bg-black/60 px-1.5 py-0.5 text-[11px] font-semibold tabular-nums text-white backdrop-blur">{score}</span>
        )}
        {typeof duration === "number" && duration > 0 && (
          <span className="absolute bottom-1.5 right-1.5 rounded-md bg-black/60 px-1.5 py-0.5 text-[10px] tabular-nums text-white backdrop-blur">{formatClipDuration(duration)}</span>
        )}
      </ClipCover>
      <span className="mt-1.5 line-clamp-2 text-xs font-medium leading-snug">{title}</span>
      {caption && <span className="line-clamp-1 text-[11px] text-muted-foreground">{caption}</span>}
    </Link>
  );
}

function Strip({ children }: { children: React.ReactNode }) {
  return <div className="-mx-1 flex snap-x gap-2.5 overflow-x-auto px-1 pb-1">{children}</div>;
}

function ClipsCard({ card }: { card: Extract<AgentCard, { type: "clips" }> }) {
  return (
    <Strip>
      {card.items.map((clip) => (
        <ClipTile key={clip.clip_id} projectId={clip.project_id} clipId={clip.clip_id} title={clip.title} score={clip.score} duration={clip.duration} caption={clip.project_title} />
      ))}
    </Strip>
  );
}

function ProjectCard({ card }: { card: Extract<AgentCard, { type: "project" }> }) {
  const t = useTranslations("agent");
  const { project } = card;
  return (
    <div className={cn(shell, "p-3")}>
      <div className="mb-2.5 flex items-center gap-2">
        <Link href={`/tasks/${project.id}`} className="min-w-0 flex-1 truncate text-sm font-medium hover:underline">{project.title}</Link>
        <StatusBadge status={project.status} />
      </div>
      {project.clips.length ? (
        <Strip>
          {project.clips.slice(0, 12).map((clip) => (
            <ClipTile key={clip.id} projectId={project.id} clipId={clip.id} title={clip.title} score={clip.score} duration={clip.duration} />
          ))}
        </Strip>
      ) : (
        <p className="text-xs text-muted-foreground">{t("cards.noClipsYet")}</p>
      )}
    </div>
  );
}

function ProjectsCard({ card }: { card: Extract<AgentCard, { type: "projects" }> }) {
  return (
    <div className={cn(shell, "divide-y overflow-hidden")}>
      {card.items.slice(0, 8).map((project) => (
        <Link key={project.id} href={`/tasks/${project.id}`} className="flex items-center gap-3 px-3 py-2 transition-colors hover:bg-accent">
          <ClipCover src={coverClipUrl(project)} className="h-11 w-8 shrink-0 rounded-md" />
          <span className="min-w-0 flex-1">
            <span className="block truncate text-sm font-medium">{project.title}</span>
            <span className="block text-xs text-muted-foreground">{project.clips_count} clips</span>
          </span>
          <StatusBadge status={project.status} />
        </Link>
      ))}
    </div>
  );
}

type TaskSnapshot = { status: string; progress?: number | null; progress_message?: string | null; source_title?: string | null; clips_count?: number };

function ProjectStartedCard({ card }: { card: Extract<AgentCard, { type: "project_started" }> }) {
  const t = useTranslations("agent");
  const { data } = useQuery({
    queryKey: ["agent-task-progress", card.project.id],
    queryFn: async (): Promise<TaskSnapshot> => {
      const response = await fetch(`/api/tasks/${card.project.id}`, { cache: "no-store" });
      if (!response.ok) throw new Error(String(response.status));
      return response.json();
    },
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && ["completed", "error", "cancelled"].includes(status) ? false : 4000;
    },
  });
  const status = data?.status ?? "queued";
  const progress = Math.max(4, Math.min(100, Number(data?.progress ?? (status === "completed" ? 100 : 4))));
  const finished = status === "completed";
  return (
    <Link href={`/tasks/${card.project.id}`} className={cn(shell, "group block p-3.5 transition-colors hover:bg-accent/50")}>
      <div className="flex items-center gap-3">
        <span className={cn("flex size-9 shrink-0 items-center justify-center rounded-xl", finished ? "bg-foreground text-background" : "bg-muted")}>
          {finished ? <Check className="size-4" /> : status === "error" ? <X className="size-4" /> : <Loader2 className="size-4 animate-spin" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium">{data?.source_title || card.project.title || t("cards.newProject")}</span>
          <span className="block truncate text-xs text-muted-foreground">
            {finished ? t("cards.projectReady", { count: data?.clips_count ?? 0 }) : status === "error" ? t("cards.projectFailed") : data?.progress_message || t("cards.projectQueued")}
          </span>
        </span>
        <ArrowRight className="size-4 text-muted-foreground transition-transform group-hover:translate-x-0.5" />
      </div>
      {!finished && status !== "error" && (
        <div className="mt-3 h-1 overflow-hidden rounded-full bg-muted">
          <div className="h-full rounded-full bg-foreground transition-[width] duration-700" style={{ width: `${progress}%` }} />
        </div>
      )}
    </Link>
  );
}

// ----------------------------------------------------------------------
// Posting
// ----------------------------------------------------------------------
function postTime(post: CardPost) {
  return post.scheduled_for || post.published_at || post.created_at;
}

const POST_DOT: Record<string, string> = {
  scheduled: "bg-foreground/50",
  queued: "bg-amber-500",
  publishing: "bg-amber-500 animate-pulse",
  published: "bg-emerald-500",
  failed: "bg-red-500",
  cancelled: "bg-muted-foreground/40",
};

export function PostRow({ post, compact }: { post: CardPost; compact?: boolean }) {
  const t = useTranslations("agent");
  const formats = useFormats();
  const when = postTime(post);
  const date = when ? new Date(when) : null;
  return (
    <div className="flex items-center gap-3 px-3 py-2">
      {post.task_id && post.clip_id ? (
        <ClipCover src={clipFileUrl(post.task_id, post.clip_id)} className="h-11 w-[1.65rem] shrink-0 rounded-md" />
      ) : <span className="h-11 w-[1.65rem] shrink-0 rounded-md bg-muted" />}
      <span className="min-w-0 flex-1">
        <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
          <SocialProviderIcon provider={post.provider} className="size-3" />
          <span className="truncate">{post.account.display_name || post.account.username || PROVIDER_LABELS[post.provider]}</span>
        </span>
        <span className="block truncate text-sm font-medium">{post.title || t("cards.untitledPost")}</span>
      </span>
      <span className="shrink-0 text-right">
        {date && <span className="block text-xs font-medium tabular-nums">{compact ? formats.time.format(date) : `${formats.day.format(date)} · ${formats.time.format(date)}`}</span>}
        <span className="flex items-center justify-end gap-1.5 text-[11px] text-muted-foreground">
          <span className={cn("size-1.5 rounded-full", POST_DOT[post.status] ?? "bg-muted-foreground")} />
          {t(`postStatus.${post.status}`)}
        </span>
      </span>
      {post.external_url && (
        <a href={post.external_url} target="_blank" rel="noreferrer" className="text-muted-foreground hover:text-foreground" aria-label={t("cards.viewPost")}>
          <ExternalLink className="size-3.5" />
        </a>
      )}
    </div>
  );
}

function PostsCard({ card }: { card: Extract<AgentCard, { type: "posts" }> }) {
  const t = useTranslations("agent");
  const formats = useFormats();
  const groups = useMemo(() => {
    const byDay = new Map<string, CardPost[]>();
    for (const post of card.items) {
      const when = postTime(post);
      const key = when ? formats.day.format(new Date(when)) : "—";
      byDay.set(key, [...(byDay.get(key) ?? []), post]);
    }
    return [...byDay.entries()];
  }, [card.items, formats]);
  return (
    <div className={cn(shell, "overflow-hidden")}>
      {card.items.length === 0 ? (
        <p className="px-3 py-4 text-sm text-muted-foreground">{t("cards.calendarEmpty")}</p>
      ) : groups.map(([day, posts]) => (
        <div key={day} className="border-b last:border-b-0">
          <p className="bg-muted/40 px-3 py-1.5 text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{day}</p>
          <div className="divide-y">{posts.map((post) => <PostRow key={post.id} post={post} compact />)}</div>
        </div>
      ))}
      <Link href="/calendar" className="flex items-center justify-between border-t px-3 py-2 text-xs font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-foreground">
        {t("cards.openCalendar")}<ArrowRight className="size-3.5" />
      </Link>
    </div>
  );
}

function PostCard({ card }: { card: Extract<AgentCard, { type: "post" }> }) {
  return <div className={cn(shell, "overflow-hidden")}><PostRow post={card.post} /></div>;
}

// ----------------------------------------------------------------------
// Background work
// ----------------------------------------------------------------------
const EDIT_STEPS = ["preparing", "ai", "render", "done"] as const;

export function EditJobCard({ job }: { job: AgentJobCard }) {
  const t = useTranslations("agent");
  const stepIndex = job.status === "completed" ? EDIT_STEPS.length - 1 : Math.max(0, EDIT_STEPS.indexOf((job.step === "start" || job.step === "edit" ? "preparing" : job.step) as typeof EDIT_STEPS[number]));
  const failed = job.status === "failed" || job.status === "cancelled";
  return (
    <div className={cn(shell, "p-3.5")}>
      <div className="flex gap-3">
        {job.project_id && job.clip_id && (
          <ClipCover src={clipFileUrl(job.project_id, job.clip_id)} className="h-[4.5rem] w-10 shrink-0 rounded-lg" />
        )}
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><Wand2 className="size-3.5" />{t("cards.studioEdit")}{job.clip_title ? ` · ${job.clip_title}` : ""}</p>
          <p className="mt-0.5 line-clamp-2 text-sm font-medium">“{job.instruction}”</p>
          {!failed && job.status !== "completed" && (
            <div className="mt-2.5 flex items-center gap-1.5">
              {EDIT_STEPS.slice(0, 3).map((step, index) => (
                <span key={step} className="flex items-center gap-1.5">
                  <span className={cn("h-1 w-8 rounded-full", index < stepIndex ? "bg-foreground" : index === stepIndex ? "bg-foreground/60 motion-safe:animate-pulse" : "bg-muted")} />
                </span>
              ))}
              <span className="ml-1 text-[11px] text-muted-foreground">{t(`editStep.${EDIT_STEPS[stepIndex] ?? "preparing"}`)}</span>
            </div>
          )}
          {job.status === "completed" && job.summary && <p className="mt-1.5 text-xs text-muted-foreground">{job.summary}</p>}
          {failed && <p className="mt-1.5 text-xs text-red-600 dark:text-red-400">{job.error || t("cards.editFailed")}</p>}
        </div>
      </div>
      {job.status === "completed" && job.project_id && job.clip_id && (
        <div className="mt-3 flex gap-2">
          <Button asChild size="sm" variant="outline" className="h-8 rounded-full">
            <Link href={`/tasks/${job.project_id}/edit?clip=${job.clip_id}`}><SlidersHorizontal className="size-3.5" />{t("cards.openStudio")}</Link>
          </Button>
          {job.export_url && (
            <Button asChild size="sm" variant="outline" className="h-8 rounded-full">
              <a href={job.export_url} download><Download className="size-3.5" />{t("cards.download")}</a>
            </Button>
          )}
        </div>
      )}
    </div>
  );
}

function FollowupCard({ card, job }: { card: Extract<AgentCard, { type: "followup" }>; job?: AgentJobCard }) {
  const t = useTranslations("agent");
  const done = job?.status === "completed";
  return (
    <div className={cn(shell, "flex items-start gap-3 p-3.5")}>
      <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-lg", done ? "bg-foreground text-background" : "bg-muted")}>
        {done ? <Check className="size-4" /> : <Clock className="size-4" />}
      </span>
      <span className="min-w-0">
        <span className="block text-xs text-muted-foreground">{t("cards.whenReady", { title: card.project_title || t("cards.thisProject") })}</span>
        <span className="block text-sm">{card.instruction}</span>
      </span>
    </div>
  );
}

function PerformanceCard({ card }: { card: Extract<AgentCard, { type: "performance" }> }) {
  const t = useTranslations("agent");
  const summary = card.summary as { published_posts?: number; views_last_30_days?: number | null; top_posts?: { title?: string; provider?: SocialProviderId; views?: number; external_url?: string }[] };
  const compact = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
  return (
    <div className={cn(shell, "p-3.5")}>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <p className="text-xs text-muted-foreground">{t("cards.views30")}</p>
          <p className="font-display text-2xl font-bold tabular-nums">{summary.views_last_30_days != null ? compact.format(summary.views_last_30_days) : "—"}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">{t("cards.published")}</p>
          <p className="font-display text-2xl font-bold tabular-nums">{summary.published_posts ?? 0}</p>
        </div>
      </div>
      {summary.top_posts?.length ? (
        <div className="mt-3 space-y-1.5 border-t pt-3">
          {summary.top_posts.slice(0, 3).map((post, index) => (
            <div key={index} className="flex items-center gap-2 text-sm">
              {post.provider && <SocialProviderIcon provider={post.provider} className="size-3.5 text-muted-foreground" />}
              <span className="min-w-0 flex-1 truncate">{post.title}</span>
              <span className="tabular-nums text-muted-foreground">{post.views != null ? compact.format(post.views) : "—"}</span>
            </div>
          ))}
        </div>
      ) : null}
      <Link href="/analytics" className="mt-3 flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">{t("cards.openAnalytics")}<ArrowRight className="size-3" /></Link>
    </div>
  );
}

function SmallNote({ icon: Icon, children }: { icon: typeof Brain; children: React.ReactNode }) {
  return (
    <div className="inline-flex max-w-full items-center gap-2 rounded-full border bg-background px-3 py-1.5 text-xs text-muted-foreground">
      <Icon className="size-3.5 shrink-0" /><span className="truncate">{children}</span>
    </div>
  );
}

/** A tool's card, or nothing for tools that only inform the agent. */
export function ToolCard({ card, jobs }: { card: AgentCard; jobs: Map<string, AgentJobCard> }) {
  const t = useTranslations("agent");
  switch (card.type) {
    case "projects": return <ProjectsCard card={card} />;
    case "project": return <ProjectCard card={card} />;
    case "clips": return <ClipsCard card={card} />;
    case "project_started": return <ProjectStartedCard card={card} />;
    case "posts": return <PostsCard card={card} />;
    case "post": return <PostCard card={card} />;
    case "edit": return <EditJobCard job={jobs.get(card.job.id) ?? card.job} />;
    case "followup": return <FollowupCard card={card} job={jobs.get(card.job_id)} />;
    case "performance": return <PerformanceCard card={card} />;
    case "memory": return <SmallNote icon={Brain}>{t("cards.remembered", { note: card.note })}</SmallNote>;
    case "defaults": return <SmallNote icon={SlidersHorizontal}>{t("cards.defaultsSaved")}</SmallNote>;
    case "project_restyled": return <SmallNote icon={Captions}>{t("cards.restyled", { title: card.title ?? "" })}</SmallNote>;
    case "clip": return card.project_id ? (
      <Strip><ClipTile projectId={card.project_id} clipId={card.clip_id} title={card.title} score={null} duration={null} caption={t("cards.trimmed")} /></Strip>
    ) : null;
    default: return null;
  }
}

// ----------------------------------------------------------------------
// Approval
// ----------------------------------------------------------------------
export function ApprovalCard({ items, busy, onAnswer }: { items: ApprovalItem[]; busy: boolean; onAnswer: (approved: boolean) => void }) {
  const t = useTranslations("agent");
  const publishing = items.filter((item) => item.tool === "publish_now");
  const deleting = items.filter((item) => item.tool === "delete_project");
  return (
    <div className="overflow-hidden rounded-2xl border border-foreground/15 bg-card shadow-sm">
      <div className="space-y-3 p-4">
        <p className="flex items-center gap-2 text-sm font-semibold">
          {publishing.length ? <Send className="size-4" /> : <X className="size-4" />}
          {publishing.length ? t("approval.publishTitle", { count: publishing.length }) : t("approval.deleteTitle", { count: deleting.length })}
        </p>
        {publishing.map((item) => (
          <div key={item.id} className="flex gap-3 rounded-xl bg-muted/50 p-2.5">
            {item.input.project_id && item.input.clip_id && (
              <ClipCover src={clipFileUrl(item.input.project_id, item.input.clip_id)} className="h-20 w-[2.8rem] shrink-0 rounded-lg" />
            )}
            <div className="min-w-0 text-sm">
              {item.input.platform && (
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground"><SocialProviderIcon provider={item.input.platform} className="size-3" />{PROVIDER_LABELS[item.input.platform]}{item.input.privacy ? ` · ${item.input.privacy}` : ""}</p>
              )}
              {item.input.title && <p className="font-medium">{item.input.title}</p>}
              {item.input.caption && <p className="line-clamp-3 text-xs text-muted-foreground">{item.input.caption}</p>}
              {item.input.hashtags?.length ? <p className="mt-0.5 text-xs text-muted-foreground">{item.input.hashtags.map((tag) => `#${tag.replace(/^#/, "")}`).join(" ")}</p> : null}
            </div>
          </div>
        ))}
        {deleting.length > 0 && <p className="text-sm text-muted-foreground">{t("approval.deleteBody")}</p>}
      </div>
      <div className="flex justify-end gap-2 border-t bg-muted/30 px-4 py-2.5">
        <Button size="sm" variant="ghost" disabled={busy} onClick={() => onAnswer(false)}>{t("approval.decline")}</Button>
        <Button size="sm" disabled={busy} onClick={() => onAnswer(true)} className={cn(deleting.length && !publishing.length && "bg-red-600 text-white hover:bg-red-600/90")}>
          {busy && <Loader2 className="size-3.5 animate-spin" />}
          {publishing.length ? t("approval.publish") : t("approval.delete")}
        </Button>
      </div>
    </div>
  );
}
