"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertCircle, Wrench } from "lucide-react";

type ServiceStatus = {
  status: "operational" | "degraded" | "outage" | "maintenance";
  headline: string;
  description: string;
};

const presentation = {
  degraded: {
    icon: AlertCircle,
    card: "border-amber-200 bg-amber-50/80 text-amber-950",
    iconColor: "text-amber-700",
  },
  outage: {
    icon: AlertCircle,
    card: "border-red-200 bg-red-50/80 text-red-950",
    iconColor: "text-red-700",
  },
  maintenance: {
    icon: Wrench,
    card: "border-blue-200 bg-blue-50/80 text-blue-950",
    iconColor: "text-blue-700",
  },
} as const;

function isServiceStatus(value: unknown): value is ServiceStatus {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Record<string, unknown>;
  return typeof candidate.status === "string" && (candidate.status === "operational" || candidate.status in presentation)
    && typeof candidate.headline === "string" && typeof candidate.description === "string";
}

export function VideoDownloadStatus() {
  const t = useTranslations("library");
  const [component, setComponent] = useState<ServiceStatus | null>(null);

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
        }
      } catch {
        if (active && !request.signal.aborted) {
          setComponent(null);
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

  if (!component || component.status === "operational") return null;

  const style = presentation[component.status];
  const Icon = style.icon;
  return (
    <section role="status" aria-label={t("youtube.aria")} className={`mt-6 rounded-xl border px-4 py-3.5 sm:px-5 ${style.card}`}>
      <div className="flex items-start gap-3">
        <Icon aria-hidden="true" className={`mt-0.5 size-5 shrink-0 ${style.iconColor}`} />
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1">
            <p className="text-sm font-semibold">{t("youtube.title", { label: t(`youtube.${component.status}`) })}</p>
            <Link href="/status" className="text-xs font-semibold underline underline-offset-2 hover:opacity-70">{t("youtube.viewStatus")}</Link>
          </div>
          <p className="mt-2 text-sm font-semibold">{component.headline}</p>
          <p className="mt-1 text-sm leading-6 opacity-85">{component.description}</p>
        </div>
      </div>
    </section>
  );
}
