"use client";

import { useTranslations } from "next-intl";
import { Sparkles } from "lucide-react";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** "Preview" on a clip that plays as a quick preview until its full-quality render is made. */
export function PreviewQualityBadge({ className }: { className?: string }) {
  const t = useTranslations("task");
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className={cn(
            "pointer-events-auto inline-flex items-center rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-white backdrop-blur focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
            className,
          )}
        >
          {t("previewQuality.badge")}
        </span>
      </TooltipTrigger>
      <TooltipContent className="max-w-64">{t("previewQuality.notice")}</TooltipContent>
    </Tooltip>
  );
}

/** One line above the clips while any of them is a preview. */
export function PreviewQualityNotice({ className }: { className?: string }) {
  const t = useTranslations("task");
  return (
    <p
      data-tour="preview-quality"
      role="note"
      className={cn("flex items-start gap-2 rounded-lg border bg-muted/40 px-3 py-2 text-xs text-muted-foreground", className)}
    >
      <Sparkles aria-hidden className="mt-0.5 size-3.5 shrink-0" />
      <span>{t("previewQuality.notice")}</span>
    </p>
  );
}

/** The download button's label: "Download", or the full-quality render's progress. */
export function useDownloadLabel() {
  const t = useTranslations("task");
  return (preparing: number | null | undefined) =>
    typeof preparing === "number" ? t("previewQuality.preparing", { progress: preparing }) : t("download");
}
