"use client";

import Link from "next/link";
import { useRef, useState } from "react";
import { useTranslations } from "next-intl";
import { Download, Loader2, Scissors } from "lucide-react";
import { sourceTimestamp, type WallClip } from "@/components/app/clip-wall";
import { Button } from "@/components/ui/button";
import { getClipUrl } from "@/lib/clip-actions";
import { formatClipDuration } from "@/lib/generations";
import { cn } from "@/lib/utils";
import type { TrailerBeatRole, WorkflowPlan } from "@/lib/workflows";

/** One entry in the recap's point list or the trailer's beat list. */
export type HeroMoment = {
  key: string;
  /** Point label, or the beat role for trailers. */
  label: string;
  kind?: "opener" | "point" | "closer";
  role?: TrailerBeatRole;
  /** Seconds into the rendered clip; null when the renderer did not report it. */
  outputStart: number | null;
  /** Seconds into the source video, when known (not on shared pages). */
  sourceStart: number | null;
};

function finite(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Points (recap) or beats (trailer) of a single-output clip. The clip's own
 * metadata wins; the task plan is the fallback for older or partial rows.
 */
export function heroMoments(workflow: "recap" | "trailer", clip: WallClip, plan?: WorkflowPlan | null): HeroMoment[] {
  const meta = clip.workflow_meta ?? {};
  const ranges = meta.ranges ?? [];
  const sourceOf = (rangeIndex: number) => finite(ranges[rangeIndex]?.[0]);
  if (workflow === "recap") {
    if (meta.points?.length) {
      return meta.points.map((point, i) => ({
        key: `p${i}`, label: point.label, kind: point.kind,
        outputStart: finite(point.output_start), sourceStart: sourceOf(point.range_index),
      }));
    }
    return (plan?.points ?? []).map((point, i) => ({
      key: `p${i}`, label: point.label,
      outputStart: finite(point.output_start), sourceStart: finite(point.ranges?.[0]?.[0]),
    }));
  }
  if (meta.beats?.length) {
    return meta.beats.map((beat, i) => ({
      key: `b${i}`, label: beat.role, role: beat.role,
      outputStart: finite(beat.output_start), sourceStart: sourceOf(beat.range_index),
    }));
  }
  return (plan?.beats ?? []).map((beat, i) => ({
    key: `b${i}`, label: beat.role, role: beat.role,
    outputStart: finite(beat.output_start), sourceStart: finite(beat.ranges?.[0]?.[0]),
  }));
}

/** The moment playing at `time`: the last one that started at or before it. */
function activeMoment(moments: HeroMoment[], time: number) {
  let active = -1;
  moments.forEach((moment, i) => {
    if (moment.outputStart !== null && moment.outputStart <= time + 0.05) active = i;
  });
  return active;
}

/** A recap or trailer: one centred 9:16 player with its points or beats. */
export function HeroClipView({ workflow, clip, plan, taskId, editable, busy, onDownload, actions }: {
  workflow: "recap" | "trailer";
  clip: WallClip;
  plan?: WorkflowPlan | null;
  taskId: string;
  editable: boolean;
  busy: boolean;
  onDownload: () => void;
  /** Extra actions (publishing). */
  actions?: React.ReactNode;
}) {
  const t = useTranslations("task");
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const [time, setTime] = useState(0);
  const [failed, setFailed] = useState(false);
  const moments = heroMoments(workflow, clip, plan);
  const playing = activeMoment(moments, time);
  const title = clip.hook_title || plan?.title || t(`workflowBadge.${workflow}`);
  const src = getClipUrl(clip.video_url, clip.filename);

  const seek = (seconds: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.currentTime = seconds;
    setTime(seconds);
    void video.play()?.catch(() => {});
  };

  const momentLabel = (moment: HeroMoment) => {
    if (moment.role) return t.has(`beatRoles.${moment.role}`) ? t(`beatRoles.${moment.role}`) : moment.role;
    return moment.label;
  };

  return (
    <section className="mx-auto grid max-w-4xl items-start justify-center gap-8 md:grid-cols-[minmax(0,22rem)_minmax(0,1fr)] md:gap-10">
      <div className="relative mx-auto w-full max-w-sm overflow-hidden rounded-2xl bg-stone-950 shadow-xl ring-1 ring-black/10">
        <video
          ref={videoRef}
          key={src}
          src={src}
          controls
          playsInline
          preload="metadata"
          onTimeUpdate={(event) => setTime(event.currentTarget.currentTime)}
          onError={() => setFailed(true)}
          onLoadedData={() => setFailed(false)}
          className="aspect-[9/16] max-h-[78dvh] w-full object-contain"
          aria-label={title}
        />
        {failed && (
          <div role="alert" className="absolute inset-0 flex flex-col items-center justify-center gap-3 bg-black/90 px-4 text-center text-sm text-white">
            <p>{t("clipLoadFailed")}</p>
            <Button variant="secondary" size="sm" onClick={() => { setFailed(false); videoRef.current?.load(); }}>{t("retryPlayback")}</Button>
          </div>
        )}
      </div>

      <div className="min-w-0">
        <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{t(`workflowBadge.${workflow}`)} · <span className="tabular-nums">{formatClipDuration(clip.duration)}</span></p>
        <h2 className="mt-1.5 font-display text-2xl font-bold leading-tight tracking-tight">{title}</h2>

        <div className="mt-4 flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={onDownload} disabled={busy}>
            {busy ? <Loader2 className="size-4 animate-spin" /> : <Download className="size-4" />}{t("download")}
          </Button>
          {editable && (
            <Button size="sm" variant="outline" asChild>
              <Link href={`/tasks/${taskId}/edit?clip=${clip.id}`}><Scissors className="size-4" />{t("openInEditor")}</Link>
            </Button>
          )}
          {actions}
        </div>

        {moments.length > 0 && (
          <div className="mt-8">
            <h3 className="text-sm font-semibold">
              {workflow === "recap" ? t("coversPoints", { count: moments.length }) : t("beatsTitle")}
            </h3>
            <ol className="mt-3 space-y-1">
              {moments.map((moment, i) => {
                const label = momentLabel(moment);
                const body = (
                  <>
                    <span className={cn("flex size-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold tabular-nums",
                      i === playing ? "bg-foreground text-background" : "bg-muted text-muted-foreground")}>{i + 1}</span>
                    <span className="min-w-0 flex-1">
                      <span className="flex flex-wrap items-baseline gap-x-2">
                        <span className="text-sm font-medium leading-snug">{label}</span>
                        {moment.kind && moment.kind !== "point" && (
                          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t(`pointKinds.${moment.kind}`)}</span>
                        )}
                      </span>
                      {moment.sourceStart !== null && (
                        <span className="mt-0.5 block text-xs tabular-nums text-muted-foreground">{t("sourceMoment", { time: sourceTimestamp(moment.sourceStart) })}</span>
                      )}
                    </span>
                  </>
                );
                return (
                  <li key={moment.key}>
                    {moment.outputStart !== null ? (
                      <button
                        type="button"
                        onClick={() => seek(moment.outputStart!)}
                        aria-label={`${label} · ${t("playFrom", { time: sourceTimestamp(moment.outputStart) })}`}
                        aria-current={i === playing ? "true" : undefined}
                        className={cn("flex w-full items-start gap-3 rounded-lg px-2 py-2 text-left transition-colors hover:bg-muted/70 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                          i === playing && "bg-muted/60")}
                      >
                        {body}
                      </button>
                    ) : (
                      <div className="flex items-start gap-3 px-2 py-2">{body}</div>
                    )}
                  </li>
                );
              })}
            </ol>
          </div>
        )}
      </div>
    </section>
  );
}
