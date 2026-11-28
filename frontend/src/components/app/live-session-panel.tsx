"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { Loader2 } from "lucide-react";
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
  stream_started_at?: string | null;
  interval_seconds: number;
  parts?: LiveSessionPart[];
  part_number?: number;
};

const REFRESH_MS = 60_000;

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
  const channel = session.channel || session.title || t("thisStream");
  const next = session.next_check_at ? new Date(session.next_check_at) : null;
  const detail = active
    ? next && next.getTime() > Date.now()
      ? t("nextPart", { time: next.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) })
      : t("nextPartSoon")
    : session.stop_reason ? t(`stopReason.${session.stop_reason}`) : null;
  const parts = session.parts ?? [];

  return (
    <section data-testid="live-session-panel" data-tour="live-session" className="mb-6 rounded-xl border px-4 py-3 text-sm">
      <div className="flex items-center gap-2">
        <span aria-hidden className={cn("size-2 shrink-0 rounded-full", active ? "bg-red-500" : "bg-muted-foreground/40")} />
        <p className="min-w-0 truncate">
          <span className="font-medium">{active ? t("title", { channel }) : t("titleStopped", { channel })}</span>
          {detail && <span className="text-muted-foreground"> · {detail}</span>}
        </p>
        {active && !readOnly && (
          <Button type="button" size="sm" variant="ghost" className="ml-auto h-7 px-2" onClick={() => void stop()} disabled={stopping}>
            {stopping ? <Loader2 className="size-3.5 animate-spin" /> : null}
            {t("stop")}
          </Button>
        )}
      </div>
      {parts.length > 1 && (
        <ol aria-label={t("partsLabel")} className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 pl-4 text-muted-foreground">
          {parts.map((part) => {
            const label = t("part", { number: part.part_number });
            const current = part.task_id === taskId;
            return (
              <li key={part.part_number}>
                {part.task_id && !current
                  ? <Link href={`/tasks/${part.task_id}`} className="hover:text-foreground hover:underline">{label}</Link>
                  : <span aria-current={current ? "page" : undefined} className={cn(current && "font-medium text-foreground")}>{label}</span>}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
