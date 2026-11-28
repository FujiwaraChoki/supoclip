"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Clock } from "lucide-react";

import {
  agentLimitFromError, agentLimitFromStatus, waitParts,
  type AgentLimit, type AgentLimitKind, type AgentStatus,
} from "@/lib/agent";
import { cn } from "@/lib/utils";

/** How long the "reply running in another chat" note stays when the server gave no wait. */
const CONCURRENT_NOTE_SECONDS = 8;

interface ActiveLimit {
  kind: AgentLimitKind;
  /** When it lifts (ms since epoch), or null when unknown (daily without a time). */
  until: number | null;
}

function activate(limit: AgentLimit, now: number): ActiveLimit {
  const seconds = limit.kind === "concurrent"
    ? Math.max(limit.retryAfter ?? 0, CONCURRENT_NOTE_SECONDS)
    : limit.retryAfter;
  return { kind: limit.kind, until: seconds ? now + seconds * 1000 : null };
}

/**
 * The agent limit a composer is under: from a 429 it just got, or from `/agent/status` (the daily cap,
 * so a reload at the limit shows it at once). Counts down and clears itself; `onDailyReset` runs when
 * the daily cap lifts so the status is fetched again.
 */
export function useAgentLimit(status: AgentStatus | null | undefined, onDailyReset?: () => void) {
  const [active, setActive] = useState<ActiveLimit | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const resetRef = useRef(onDailyReset);
  resetRef.current = onDailyReset;

  // Each fresh status is the server's word on the daily cap: set it, or lift a daily note it no longer backs.
  useEffect(() => {
    if (status === undefined) return;
    const fromStatus = agentLimitFromStatus(status);
    const at = Date.now();
    setNow(at);
    setActive((current) => {
      if (fromStatus) return activate(fromStatus, at);
      return current?.kind === "daily" ? null : current;
    });
  }, [status]);

  // Tick while something counts down: every second near the end, rarely while hours remain.
  useEffect(() => {
    if (!active?.until) return;
    const remaining = active.until - Date.now();
    if (remaining <= 0) {
      const kind = active.kind;
      setActive(null);
      if (kind === "daily") resetRef.current?.();
      return;
    }
    const step = remaining > 120_000 ? Math.min(30_000, remaining - 120_000 + 1000) : Math.min(1000, remaining);
    const timer = window.setTimeout(() => setNow(Date.now()), step);
    return () => window.clearTimeout(timer);
  }, [active, now]);

  /** Show the limit a failed send hit; null when the error is not a limit (toast it as before). */
  const report = useCallback((error: unknown): AgentLimit | null => {
    const limit = agentLimitFromError(error);
    if (!limit) return null;
    const at = Date.now();
    setNow(at);
    setActive(activate(limit, at));
    return limit;
  }, []);

  /** Trying again clears a note that doesn't block sending (the concurrent one). */
  const clearSoft = useCallback(() => {
    setActive((current) => (current?.kind === "concurrent" ? null : current));
  }, []);

  const remaining = active?.until ? Math.max(0, Math.ceil((active.until - now) / 1000)) : null;
  const visible = active && (active.until === null || remaining! > 0) ? active : null;
  return {
    kind: visible?.kind ?? null,
    remaining: visible ? remaining : null,
    /** Daily cap: the composer is off until it resets. */
    daily: visible?.kind === "daily",
    /** Daily cap or a burst cooldown: Send waits. */
    blocksSend: visible?.kind === "daily" || visible?.kind === "burst",
    report,
    clearSoft,
  };
}

/** One quiet line by the composer about the limit, in the same voice as the trial note. */
export function AgentLimitNote({ kind, remaining, className }: { kind: AgentLimitKind | null; remaining: number | null; className?: string }) {
  const t = useTranslations("agent.limit");
  if (!kind) return null;
  const time = remaining !== null ? (() => {
    const { unit, count } = waitParts(remaining);
    return t(`units.${unit}`, { count });
  })() : null;
  const [lead, detail] =
    kind === "daily" ? [t("daily"), time ? t("resetsIn", { time }) : t("resetsWithin")]
      : kind === "burst" ? [t("tooFast"), t("tryAgainIn", { time: time ?? t("units.seconds", { count: 10 }) })]
        : [t("concurrent"), null];
  return (
    <p
      role="status"
      aria-live="polite"
      data-limit={kind}
      className={cn("flex flex-wrap items-center justify-center gap-x-1.5 gap-y-1 text-center text-xs text-muted-foreground", className)}
    >
      <Clock className="size-3 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden />
      <span className="text-foreground/80">{lead}</span>
      {detail && (
        <>
          <span aria-hidden>·</span>
          <span className="tabular-nums">{detail}</span>
        </>
      )}
    </p>
  );
}
