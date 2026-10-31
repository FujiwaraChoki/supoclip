"use client";

import Link from "next/link";
import { useRef } from "react";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { Copy, Download, Loader2, Play, RotateCcw, Scissors } from "lucide-react";
import { ClipCover } from "@/components/app/clip-cover";
import { isPart, partTitle, type WallClip } from "@/components/app/clip-wall";
import { Button } from "@/components/ui/button";
import { getClipUrl } from "@/lib/clip-actions";
import { useCopyText } from "@/lib/copy-text";
import { formatClipDuration } from "@/lib/generations";
import type { WorkflowPlan } from "@/lib/workflows";

export { partTitle };

/** Parts in story order; the halves of a split part stay together in clip order. */
export function orderedParts<T extends Pick<WallClip, "part_number" | "clip_order">>(clips: T[]): T[] {
  return [...clips].sort((a, b) =>
    (a.part_number ?? Number.MAX_SAFE_INTEGER) - (b.part_number ?? Number.MAX_SAFE_INTEGER)
    || a.clip_order - b.clip_order);
}

/** Parts the series was planned with, falling back to what was rendered. */
export function seriesPartTotal(plan: WorkflowPlan | null | undefined, clips: Pick<WallClip, "part_number" | "workflow_meta">[]) {
  const planned = plan?.parts?.length ?? 0;
  const fromMeta = clips.reduce((most, clip) => Math.max(most, clip.workflow_meta?.part_count ?? 0, clip.part_number ?? 0), 0);
  return Math.max(planned, fromMeta, clips.length > 0 ? 1 : 0);
}

/** Distinct parts that were rendered (a split part counts once). */
function renderedParts(clips: Pick<WallClip, "id" | "part_number">[]) {
  return new Set(clips.map((clip) => clip.part_number ?? `clip-${clip.id}`)).size;
}

/** A cancelled series that stopped before its last part, as { done, total }; else null. */
export function unfinishedSeries(
  status: string | undefined,
  plan: WorkflowPlan | null | undefined,
  clips: Pick<WallClip, "id" | "part_number" | "workflow_meta">[],
) {
  if (status !== "cancelled" || clips.length === 0) return null;
  const total = seriesPartTotal(plan, clips);
  const done = renderedParts(clips);
  return done < total ? { done, total } : null;
}

export function SeriesView<C extends WallClip>({
  clips, plan, taskId, status, editable, busyId, downloadingAll, toolbar,
  onOpen, onDownload, onDownloadAll, onResume, resumeDisabled, renderActions,
}: {
  clips: C[];
  plan?: WorkflowPlan | null;
  taskId: string;
  status: string;
  editable: boolean;
  /** The clip whose download or export is running. */
  busyId: string | null;
  downloadingAll: boolean;
  /** Controls placed next to "Download all parts" (e.g. the download format). */
  toolbar?: React.ReactNode;
  onOpen: (clip: C) => void;
  onDownload: (clip: C) => void;
  onDownloadAll?: () => void;
  onResume?: () => void;
  resumeDisabled?: boolean;
  /** Extra per-part actions (publishing). */
  renderActions?: (clip: C) => React.ReactNode;
}) {
  const t = useTranslations("task");
  const parts = orderedParts(clips);
  const total = seriesPartTotal(plan, clips);
  const unfinished = unfinishedSeries(status, plan, clips);

  return (
    <section aria-label={t("workflowBadge.series", { count: total })}>
      <div className="mb-5 flex flex-wrap items-center justify-between gap-3">
        {onDownloadAll && parts.length > 1 ? (
          <Button size="sm" variant="outline" onClick={onDownloadAll} disabled={downloadingAll || busyId !== null}>
            {downloadingAll ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}{t("downloadAll")}
          </Button>
        ) : <span />}
        {toolbar}
      </div>

      {unfinished && (
        <div role="status" className="mb-5 flex flex-wrap items-center justify-between gap-3 rounded-xl border border-dashed px-4 py-3 text-sm">
          <p className="text-muted-foreground">{t("incompleteSeries", unfinished)}</p>
          {onResume && (
            <Button size="sm" onClick={onResume} disabled={resumeDisabled}>
              <RotateCcw className="size-4" />{t("resume")}
            </Button>
          )}
        </div>
      )}

      <ol className="space-y-3">
        {parts.map((clip) => (
          <SeriesPart
            key={clip.id}
            clip={clip}
            total={total}
            taskId={taskId}
            editable={editable}
            busy={busyId === clip.id}
            onOpen={() => onOpen(clip)}
            onDownload={() => onDownload(clip)}
            actions={renderActions?.(clip)}
          />
        ))}
      </ol>
    </section>
  );
}

function SeriesPart({ clip, total, taskId, editable, busy, onOpen, onDownload, actions }: {
  clip: WallClip;
  total: number;
  taskId: string;
  editable: boolean;
  busy: boolean;
  onOpen: () => void;
  onDownload: () => void;
  actions?: React.ReactNode;
}) {
  const t = useTranslations("task");
  const copyText = useCopyText();
  const captionRef = useRef<HTMLParagraphElement | null>(null);
  const number = isPart(clip) ? clip.part_number : clip.clip_order;
  const label = t("partOf", { n: number, total });
  const title = partTitle(clip) ?? t("partLabel", { n: number });
  const endsOn = clip.workflow_meta?.ends_on?.trim();
  const caption = clip.workflow_meta?.post_caption?.trim();

  const copyCaption = async () => {
    if (!caption) return;
    if (await copyText(caption, captionRef.current) === "copied") toast.success(t("captionCopied"));
  };

  return (
    <li className="animate-rise flex gap-4 rounded-2xl border bg-background p-3 sm:gap-5 sm:p-4">
      <button
        type="button"
        onClick={onOpen}
        aria-label={t("preview", { title: `${label} · ${title}` })}
        className="group relative w-24 shrink-0 self-start rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 sm:w-32"
      >
        <ClipCover src={getClipUrl(clip.video_url, clip.filename)} className="aspect-[9/16] rounded-xl ring-1 ring-black/5">
          <span className="absolute inset-0 flex items-center justify-center bg-black/0 transition-colors group-hover:bg-black/25">
            <span className="flex size-10 items-center justify-center rounded-full bg-white/90 text-black opacity-0 shadow-lg transition-opacity group-hover:opacity-100"><Play className="ml-0.5 size-4 fill-current" /></span>
          </span>
          <span className="absolute bottom-1.5 right-1.5 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium tabular-nums text-white">{formatClipDuration(clip.duration)}</span>
        </ClipCover>
      </button>

      <div className="flex min-w-0 flex-1 flex-col">
        <p className="text-xs font-medium tabular-nums text-muted-foreground">{label}</p>
        <h3 className="mt-0.5 text-base font-semibold leading-snug">{title}</h3>
        {endsOn && <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">{t("endsOn", { text: endsOn })}</p>}

        {caption && (
          <div className="mt-3 rounded-lg bg-muted/50 px-3 py-2">
            <div className="flex items-center justify-between gap-2">
              <span className="text-xs font-medium text-muted-foreground">{t("postCaption")}</span>
              <Button size="sm" variant="ghost" className="-mr-1 h-7 px-2 text-xs" onClick={() => void copyCaption()}>
                <Copy className="size-3.5" />{t("copyCaption")}
              </Button>
            </div>
            <p ref={captionRef} className="mt-0.5 whitespace-pre-line text-sm">{caption}</p>
          </div>
        )}

        <div className="mt-auto flex flex-wrap items-center gap-2 pt-3">
          <Button size="sm" variant="outline" onClick={onDownload} disabled={busy} aria-label={t("downloadNamed", { title: label })}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}{t("download")}
          </Button>
          {editable && (
            <Button size="sm" variant="outline" asChild>
              <Link href={`/tasks/${taskId}/edit?clip=${clip.id}`}><Scissors className="size-4" />{t("edit")}</Link>
            </Button>
          )}
          {actions}
        </div>
      </div>
    </li>
  );
}
