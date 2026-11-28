"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Loader2, Square } from "lucide-react";
import { toast } from "sonner";

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
  interval_seconds: number;
  parts?: LiveSessionPart[];
  part_number?: number;
};

const REFRESH_MS = 60_000;

function clock(seconds: number): string {
  const total = Math.max(0, Math.floor(seconds));
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  return hours > 0
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(rest).padStart(2, "0")}`
    : `${minutes}:${String(rest).padStart(2, "0")}`;
}

/** A continuous live clipping session on the task page of any of its parts. */
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

  return (
    <section data-testid="live-session-panel" data-tour="live-session" className="mb-6 rounded-xl border bg-muted/30 p-4">
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <p className={cn(
            "flex items-center gap-2 text-xs font-semibold uppercase tracking-wide",
            active ? "text-red-600 dark:text-red-400" : "text-muted-foreground",
          )}>
            <span aria-hidden className={cn("inline-flex size-2 rounded-full", active ? "bg-red-500" : "bg-muted-foreground/50")} />
            {active ? t("following") : t(`status.${session.status}`)}
          </p>
          <p className="mt-1 text-sm font-medium">{t("title", { channel })}</p>
          <p className="mt-0.5 text-xs text-muted-foreground">
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
            {stopping ? <Loader2 className="size-4 animate-spin" /> : <Square className="size-3.5" />}
            {t("stop")}
          </Button>
        )}
      </div>
      {parts.length > 0 && (
        <ol className="mt-3 flex flex-wrap gap-2" aria-label={t("partsLabel")}>
          {parts.map((part) => {
            const current = part.task_id === taskId;
            const label = t("part", { number: part.part_number, start: clock(part.start_seconds), end: clock(part.end_seconds) });
            const body = (
              <>
                <span className="font-medium">{label}</span>
                <span className="text-muted-foreground">
                  {part.task_status === "completed"
                    ? t("clips", { count: part.clips_count })
                    : part.task_status === "error" || part.task_status === "cancelled"
                      ? t("partFailed")
                      : t("partWorking")}
                </span>
              </>
            );
            const className = cn(
              "flex items-center gap-2 rounded-full border px-3 py-1 text-xs tabular-nums",
              current ? "border-foreground bg-background" : "bg-background/60 hover:bg-background",
            );
            return (
              <li key={part.part_number}>
                {part.task_id && !current
                  ? <Link href={`/tasks/${part.task_id}`} className={className}>{body}</Link>
                  : <span className={className} aria-current={current ? "page" : undefined}>{body}</span>}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
