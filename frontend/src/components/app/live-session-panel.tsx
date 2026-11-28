"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Loader2, Square } from "lucide-react";
import { toast } from "sonner";

import { LivePill, streamClock } from "@/components/live-stream-panel";
import { VideoSourceIcon } from "@/components/video-source-icon";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export type LiveTaskInfo = {
  platform?: string;
  channel?: string;
  channel_url?: string;
  final?: boolean;
  session_id?: string;
  part_number?: number;
};

export type LiveSessionPart = {
  part_number: number;
  task_id: string | null;
  start_seconds: number;
  end_seconds: number;
  task_status: string | null;
  clips_count: number;
};

export type LiveSessionInfo = {
  id: string;
  platform: string;
  channel: string | null;
  title: string | null;
  status: "active" | "ended" | "stopped" | "failed";
  stop_reason: string | null;
  covered_until_seconds: number;
  parts_created: number;
  next_check_at: string | null;
  stream_started_at?: string | null;
  interval_seconds: number;
  parts?: LiveSessionPart[];
  part_number?: number;
};

const REFRESH_MS = 60_000;
type PartState = "done" | "working" | "failed";

function partState(part: LiveSessionPart): PartState {
  if (part.task_status === "completed") return "done";
  if (part.task_status === "error" || part.task_status === "cancelled") return "failed";
  return "working";
}

function isPlatform(value: string): value is "youtube" | "twitch" | "kick" {
  return value === "youtube" || value === "twitch" || value === "kick";
}

/** A continuous live clipping session, on the task page of any of its parts. */
export function LiveSessionPanel({ taskId, session: initial, readOnly }: {
  taskId: string;
  session: LiveSessionInfo;
  readOnly?: boolean;
}) {
  const t = useTranslations("task.liveSession");
  const [session, setSession] = useState<LiveSessionInfo>(initial);
  const [stopping, setStopping] = useState(false);
  useEffect(() => setSession(initial), [initial]);

  const refresh = useCallback(async () => {
    try {
      const response = await fetch(`/api/live-sessions/${session.id}`, { cache: "no-store" });
      if (response.ok) setSession({ ...(await response.json()), part_number: session.part_number });
    } catch {
      // A missed refresh keeps the last state on screen; the next one tries again.
    }
  }, [session.id, session.part_number]);

  useEffect(() => {
    if (session.status !== "active") return;
    const timer = window.setInterval(() => void refresh(), REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [refresh, session.status]);

  const stop = async () => {
    setStopping(true);
    try {
      const response = await fetch(`/api/live-sessions/${session.id}/stop`, { method: "POST" });
      if (!response.ok) throw new Error(String(response.status));
      setSession({ ...(await response.json()), part_number: session.part_number });
      toast.success(t("stopped"));
    } catch {
      toast.error(t("stopFailed"));
    } finally {
      setStopping(false);
    }
  };

  const active = session.status === "active";
  const next = session.next_check_at ? new Date(session.next_check_at) : null;
  const nextLabel = next && next.getTime() > Date.now()
    ? next.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
    : null;
  const channel = session.channel || session.title || t("thisStream");
  const parts = session.parts ?? [];
  const clips = parts.reduce((sum, part) => sum + part.clips_count, 0);

  return (
    <section data-testid="live-session-panel" data-tour="live-session" className="mb-6 overflow-hidden rounded-xl border bg-background">
      <div className="flex items-center gap-3 p-4">
        <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-muted">
          {isPlatform(session.platform)
            ? <VideoSourceIcon source={session.platform} className="size-4" aria-hidden />
            : null}
        </span>
        <div className="min-w-0 flex-1">
          <p className="flex min-w-0 items-center gap-2 text-sm font-semibold">
            <span className="truncate">{t("title", { channel })}</span>
            {active
              ? <LivePill label={t("live")} />
              : <span className="shrink-0 rounded-[5px] bg-muted px-1.5 py-px text-[11px] font-medium leading-4 text-muted-foreground">{t(`status.${session.status}`)}</span>}
          </p>
          <p className="truncate text-xs text-muted-foreground">
            {active
              ? nextLabel
                ? t("nextBatchAt", { time: nextLabel, minutes: Math.round(session.interval_seconds / 60) })
                : t("nextBatchSoon")
              : session.stop_reason
                ? t(`stopReason.${session.stop_reason}`)
                : null}
          </p>
        </div>
        {active && !readOnly && (
          <Button type="button" size="sm" variant="outline" onClick={() => void stop()} disabled={stopping}>
            {stopping ? <Loader2 className="size-4 animate-spin" /> : <Square className="size-3 fill-current" />}
            {t("stop")}
          </Button>
        )}
      </div>
      {parts.length > 0 && (
        <div className="border-t px-4 pb-3 pt-3.5">
          <PartsTimeline parts={parts} taskId={taskId} active={active} session={session} labels={{
            part: (part) => t("part", { number: part.part_number, start: streamClock(part.start_seconds), end: streamClock(part.end_seconds) }),
            state: (part) => partState(part) === "done" ? t("clips", { count: part.clips_count }) : partState(part) === "failed" ? t("partFailed") : t("partWorking"),
            short: (part) => t("partShort", { number: part.part_number }),
            next: t("nextBatch"),
            now: t("now"),
            list: t("partsLabel"),
          }} />
          <p className="mt-2 text-xs text-muted-foreground">{t("summary", { parts: parts.length, clips })}</p>
        </div>
      )}
    </section>
  );
}

function PartsTimeline({ parts, taskId, active, session, labels }: {
  parts: LiveSessionPart[];
  taskId: string;
  active: boolean;
  session: LiveSessionInfo;
  labels: {
    part: (part: LiveSessionPart) => string;
    state: (part: LiveSessionPart) => string;
    short: (part: LiveSessionPart) => string;
    next: string;
    now: string;
    list: string;
  };
}) {
  const start = Math.min(...parts.map((part) => part.start_seconds));
  const lastEnd = Math.max(...parts.map((part) => part.end_seconds));
  const startedAt = session.stream_started_at ? Date.parse(session.stream_started_at) : NaN;
  const nowSeconds = Number.isFinite(startedAt) ? (Date.now() - startedAt) / 1000 : lastEnd;
  // While live, the bar runs to "now" and keeps room for the batch being gathered.
  const end = active ? Math.max(nowSeconds, lastEnd + (lastEnd - start) * 0.18) : lastEnd;
  const span = Math.max(1, end - start);
  const at = (seconds: number) => ((seconds - start) / span) * 100;

  return (
    <div>
      <ol className="relative h-9" aria-label={labels.list}>
        {parts.map((part, index) => {
          const state = partState(part);
          // Parts overlap by a minute or so; each is drawn from where the one before it ended.
          const from = index > 0 ? Math.max(part.start_seconds, parts[index - 1].end_seconds) : part.start_seconds;
          const current = part.task_id === taskId;
          const description = `${labels.part(part)} · ${labels.state(part)}`;
          // The part on screen is solid; the others are light, so neighbours never merge into one bar.
          const className = cn(
            "@container absolute inset-y-0 flex min-w-0 items-center gap-1.5 overflow-hidden rounded-md border px-2 text-[11px] font-medium transition-colors",
            current
              ? "border-foreground bg-foreground text-background"
              : state === "failed"
                ? "border-red-200 bg-red-50 text-red-700 dark:border-red-900/60 dark:bg-red-950/40 dark:text-red-300"
                : state === "working"
                  ? "border-border bg-muted/60 text-foreground hover:bg-muted"
                  : "border-border bg-muted text-foreground hover:border-foreground/30",
          );
          const body = (
            <>
              {state === "working" && <Loader2 aria-hidden className={cn("size-3 shrink-0 animate-spin", current ? "text-background/70" : "text-muted-foreground")} />}
              <span className="hidden truncate @[3.5rem]:inline">{labels.short(part)}</span>
              <span className={cn("hidden truncate font-normal @[8rem]:inline", current ? "text-background/70" : "text-muted-foreground")}>{labels.state(part)}</span>
            </>
          );
          const style = { left: `calc(${at(from)}% + 1.5px)`, width: `calc(${at(part.end_seconds) - at(from)}% - 3px)` };
          return (
            <li key={part.part_number} className="contents">
              {part.task_id && !current
                ? <Link href={`/tasks/${part.task_id}`} title={description} aria-label={description} className={className} style={style}>{body}</Link>
                : <span title={description} aria-label={description} aria-current={current ? "page" : undefined} className={className} style={style}>{body}</span>}
            </li>
          );
        })}
        {active && (
          <li
            aria-label={labels.next}
            className="absolute inset-y-0 right-0 flex items-center justify-center overflow-hidden rounded-md border border-dashed border-foreground/25 bg-[repeating-linear-gradient(135deg,transparent_0_5px,var(--color-muted)_5px_10px)] px-1.5 text-[11px] text-muted-foreground"
            style={{ left: `calc(${at(lastEnd)}% + 3px)` }}
          >
            <span className="truncate">{labels.next}</span>
          </li>
        )}
      </ol>
      <div className="mt-1.5 flex justify-between text-[11px] tabular-nums text-muted-foreground">
        <span>{streamClock(start)}</span>
        {active
          ? <span className="flex items-center gap-1 font-medium text-foreground"><span aria-hidden className="size-1.5 rounded-full bg-red-600" />{labels.now}</span>
          : <span>{streamClock(end)}</span>}
      </div>
    </div>
  );
}
