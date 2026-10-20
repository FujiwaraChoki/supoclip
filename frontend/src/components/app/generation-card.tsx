"use client";

import Link from "next/link";
import { useLocale, useTranslations } from "next-intl";
import { AlertTriangle, Check, Loader2, Youtube, Upload } from "lucide-react";
import { ClipCover, ScoreRing } from "@/components/app/clip-cover";
import { StatusBadge, ACTIVE_TASK_STATUSES } from "@/components/app/status-badge";
import { coverClipUrl, timeAgo, type GenerationSummary } from "@/lib/generations";
import { cn } from "@/lib/utils";

export function GenerationCard({ generation, selected, onToggle, selectionDisabled, showStatus, className }: {
  generation: GenerationSummary;
  /** Always show the status badge (management views); otherwise only non-completed states. */
  showStatus?: boolean;
  selected?: boolean;
  onToggle?: () => void;
  selectionDisabled?: boolean;
  className?: string;
}) {
  const t = useTranslations("library");
  const locale = useLocale();
  const active = ACTIVE_TASK_STATUSES.includes(generation.status);
  const failed = generation.status === "error";
  const selectable = Boolean(onToggle);
  const SourceIcon = generation.source_type === "youtube" ? Youtube : Upload;

  return (
    <div className={cn("group relative", className)}>
      <Link href={`/tasks/${generation.id}`} className="block rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2">
        <ClipCover
          src={coverClipUrl(generation)}
          className={cn(
            "aspect-[3/4] rounded-xl ring-1 ring-black/5 transition-all duration-200 group-hover:shadow-lg group-hover:ring-black/10",
            selected && "ring-2 ring-foreground",
          )}
        >
          <div className="pointer-events-none absolute inset-0 bg-gradient-to-t from-black/70 via-black/0 to-black/20" />
          {active && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-stone-950/60 text-white">
              <Loader2 className="size-5 animate-spin" aria-hidden />
              <span className="text-xs font-medium">{generation.status === "queued" ? t("card.queued") : t("card.finding")}</span>
              <div className="progress-sheen relative mt-1 h-0.5 w-24 overflow-hidden rounded-full bg-white/20" />
            </div>
          )}
          {failed && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-stone-950/70 text-white">
              <AlertTriangle className="size-5 text-amber-300" aria-hidden />
              <span className="text-xs font-medium">{t("card.needsAttention")}</span>
            </div>
          )}
          {generation.top_virality_score ? (
            <div className="absolute right-2 top-2 rounded-full bg-black/55 text-white backdrop-blur">
              <ScoreRing score={generation.top_virality_score} size={34} />
            </div>
          ) : null}
          <div className="absolute inset-x-3 bottom-2.5 flex items-center justify-between text-[11px] font-medium text-white/90">
            <span className="flex items-center gap-1.5"><SourceIcon className="size-3.5" aria-hidden />{generation.source_type === "youtube" ? t("card.youtube") : t("card.upload")}</span>
            {generation.clips_count > 0 && <span className="tabular-nums">{t("card.clips", { count: generation.clips_count })}</span>}
          </div>
        </ClipCover>
        <div className="mt-2.5 space-y-1 px-0.5">
          <h3 className="line-clamp-2 text-sm font-semibold leading-snug">{generation.source_title}</h3>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>{timeAgo(generation.created_at, locale)}</span>
            {(showStatus || generation.status !== "completed") && <StatusBadge status={generation.status} />}
          </div>
        </div>
      </Link>
      {selectable && (
        <button
          type="button"
          role="checkbox"
          aria-checked={Boolean(selected)}
          aria-label={selected ? t("card.deselect", { title: generation.source_title }) : t("card.select", { title: generation.source_title })}
          disabled={selectionDisabled}
          onClick={onToggle}
          className={cn(
            "absolute left-1 top-1 z-10 flex size-10 items-center justify-center rounded-lg transition-opacity",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-white focus-visible:ring-offset-2 focus-visible:ring-offset-black/40",
            selected ? "opacity-100" : "opacity-100 md:opacity-0 md:group-hover:opacity-100 md:focus-within:opacity-100",
            "disabled:cursor-not-allowed disabled:opacity-40",
          )}
        >
          <span
            aria-hidden
            className={cn(
              "flex size-6 items-center justify-center rounded-[7px] border-2 border-white shadow-[0_1px_3px_rgba(0,0,0,0.45)]",
              selected ? "bg-white text-stone-950" : "bg-black",
            )}
          >
            <Check className={cn("size-3.5", selected ? "opacity-100" : "opacity-0")} strokeWidth={3} />
          </span>
        </button>
      )}
    </div>
  );
}
