"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { AlertCircle, CheckCircle2, Info, Wrench } from "lucide-react";

type ServiceStatus = {
  status: "operational" | "degraded" | "outage" | "maintenance";
  headline: string;
  description: string;
};

const presentation = {
  operational: {
    label: "Operational",
    icon: CheckCircle2,
    card: "border-emerald-200 bg-emerald-50/70 text-emerald-950",
    iconColor: "text-emerald-700",
  },
  degraded: {
    label: "Degraded",
    icon: AlertCircle,
    card: "border-amber-200 bg-amber-50/80 text-amber-950",
    iconColor: "text-amber-700",
  },
  outage: {
    label: "Outage",
    icon: AlertCircle,
    card: "border-red-200 bg-red-50/80 text-red-950",
    iconColor: "text-red-700",
  },
  maintenance: {
    label: "Maintenance",
    icon: Wrench,
    card: "border-blue-200 bg-blue-50/80 text-blue-950",
    iconColor: "text-blue-700",
  },
} as const;

function isServiceStatus(value: unknown): value is ServiceStatus {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.status === "string" && candidate.status in presentation
    && typeof candidate.headline === "string" && typeof candidate.description === "string";
}

export function VideoDownloadStatus() {
  const [component, setComponent] = useState<ServiceStatus | null>(null);
  const [unavailable, setUnavailable] = useState(false);

  useEffect(() => {
    let active = true;
    let controller: AbortController | null = null;

    const refresh = async () => {
      controller?.abort();
      const request = new AbortController();
      controller = request;
      try {
        const response = await fetch("/api/service-status/video-downloads", {
          cache: "no-store",
          signal: request.signal,
        });
        if (!response.ok) throw new Error("Status unavailable");
        const data: { component?: unknown } = await response.json();
        if (!isServiceStatus(data.component)) throw new Error("Invalid status response");
        if (active && !request.signal.aborted) {
          setComponent(data.component);
          setUnavailable(false);
        }
      } catch {
        if (active && !request.signal.aborted) {
          setComponent(null);
          setUnavailable(true);
        }
      }
    };

    void refresh();
    const interval = window.setInterval(() => { void refresh(); }, 60_000);
    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void refresh();
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    return () => {
      active = false;
      controller?.abort();
      window.clearInterval(interval);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  if (!component) {
    return (
      <div role="status" className="mt-6 flex items-center justify-between gap-3 rounded-xl border bg-background px-4 py-3 text-sm text-muted-foreground">
        <span className="flex items-center gap-2">
          <Info aria-hidden="true" className="size-4 shrink-0" />
          {unavailable ? "YouTube import status is temporarily unavailable." : "Checking YouTube import status…"}
        </span>
        <Link href="/status" className="shrink-0 font-medium underline underline-offset-2 hover:text-foreground">Status page</Link>
      </div>
    );
  }

  const style = presentation[component.status];
  const Icon = style.icon;
  const isOperational = component.status === "operational";
  return (
    <section role="status" aria-label="YouTube import status" className={`mt-6 rounded-xl border px-4 py-3.5 sm:px-5 ${style.card}`}>
      <div className="flex items-start gap-3">
        <Icon aria-hidden="true" className={`mt-0.5 size-5 shrink-0 ${style.iconColor}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <p className="text-sm font-semibold">YouTube imports · {style.label}</p>
            <Link href="/status" className="text-xs font-semibold underline underline-offset-2 hover:opacity-70">View status page</Link>
          </div>
          {!isOperational && (
            <>
              <p className="mt-2 text-sm font-semibold">{component.headline}</p>
              <p className="mt-1 text-sm leading-6 opacity-85">{component.description}</p>
            </>
          )}
        </div>
      </div>
    </section>
  );
}
