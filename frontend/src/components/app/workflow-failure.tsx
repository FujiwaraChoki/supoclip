"use client";

import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertCircle, ArrowLeft, Clapperboard, Loader2, RotateCcw, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { buildFontOptionsPayload } from "@/lib/font-options";
import {
  MIN_SOURCE_SECONDS,
  normalizeTopic,
  topicIssue,
  type Workflow,
  type WorkflowErrorCode,
  type WorkflowOptions,
  type WorkflowPlan,
} from "@/lib/workflows";

export const WORKFLOW_ERROR_CODES: readonly WorkflowErrorCode[] = [
  "source_too_short", "no_story", "topic_not_found", "recap_unsupported", "trailer_unbuildable",
  "analysis_incomplete", "plan_failed", "render_incomplete", "transcript_untimed",
];

export function isWorkflowErrorCode(value: unknown): value is WorkflowErrorCode {
  return typeof value === "string" && WORKFLOW_ERROR_CODES.includes(value as WorkflowErrorCode);
}

/** Failures a retry can fix. The others fail the same way again, so they offer another workflow instead. */
const RETRYABLE: ReadonlySet<WorkflowErrorCode> = new Set(["analysis_incomplete", "plan_failed", "render_incomplete"]);

export function isRetryableWorkflowError(code: WorkflowErrorCode) {
  return RETRYABLE.has(code);
}

/** What to make from the same source after this failure: a new task, a retry, or nothing. */
export type FailureAction =
  | { kind: "create"; workflow: "highlights" | "recap" }
  | { kind: "resume" }
  | { kind: "home" };

export function failureAction(code: WorkflowErrorCode, sourceSeconds?: number | null): FailureAction {
  if (RETRYABLE.has(code)) return { kind: "resume" };
  if (code === "transcript_untimed") return { kind: "home" };
  if (code === "no_story" || code === "trailer_unbuildable") {
    // A recap needs a longer source than a series; fall back to highlights below that.
    const fitsRecap = !sourceSeconds || sourceSeconds >= MIN_SOURCE_SECONDS.recap;
    return { kind: "create", workflow: fitsRecap ? "recap" : "highlights" };
  }
  return { kind: "create", workflow: "highlights" };
}

/** The task fields a new generation from the same source reuses. */
export type RecreatableTask = {
  source_url?: string | null;
  source_title?: string | null;
  source_type?: string | null;
  font_family?: string | null;
  font_size?: number | null;
  font_color?: string | null;
  caption_template?: string | null;
  processing_mode?: string | null;
  output_format?: string | null;
  clip_length?: string | null;
  add_subtitles?: boolean | null;
  include_broll?: boolean | null;
  cut_long_pauses?: boolean | null;
  pause_threshold_ms?: number | null;
  remove_filler_words?: boolean | null;
  filtered_words?: string[] | null;
  workflow_options?: WorkflowOptions | null;
};

/**
 * `POST /api/tasks/create` body for a new generation from the same source with
 * the same look (captions, font, framing, cleanup) and instructions. Highlights
 * omit `workflow`, exactly like the create form.
 */
export function buildRecreateBody(task: RecreatableTask, next: { workflow: Workflow; topic?: string }) {
  const instructions = task.workflow_options?.instructions?.trim();
  return {
    source: {
      url: task.source_url,
      title: task.source_type === "upload" && task.source_title ? task.source_title.slice(0, 500) : null,
    },
    font_options: buildFontOptionsPayload(task.font_family ?? null, task.font_size ?? null, task.font_color ?? null),
    caption_template: task.caption_template || "default",
    processing_mode: task.processing_mode || "fast",
    ...(task.output_format ? { output_format: task.output_format } : {}),
    ...(task.clip_length ? { clip_length: task.clip_length } : {}),
    ...(typeof task.add_subtitles === "boolean" ? { add_subtitles: task.add_subtitles } : {}),
    include_broll: task.include_broll === true,
    ...(typeof task.cut_long_pauses === "boolean" ? { cut_long_pauses: task.cut_long_pauses } : {}),
    ...(typeof task.pause_threshold_ms === "number" ? { pause_threshold_ms: task.pause_threshold_ms } : {}),
    ...(typeof task.remove_filler_words === "boolean" ? { remove_filler_words: task.remove_filler_words } : {}),
    ...(Array.isArray(task.filtered_words) ? { filtered_words: task.filtered_words } : {}),
    ...(next.workflow === "highlights" ? {} : { workflow: next.workflow }),
    ...(next.workflow === "topic" && next.topic ? { workflow_options: { topic: next.topic } } : {}),
    ...(instructions ? { instructions } : {}),
  };
}

/** Pending-action id for starting `workflow` (and `topic`) from the failure screen. */
export function createActionId(workflow: Workflow, topic?: string) {
  return topic ? `create:${workflow}:${topic}` : `create:${workflow}`;
}

/**
 * A workflow failure explained, with the next step: suggested topics, another
 * workflow on the same source, or a retry. Every one of these was refunded.
 */
export function WorkflowFailure({ code, plan, topic, sourceSeconds, canCreate, readOnly, pendingAction, onCreate, onResume }: {
  code: WorkflowErrorCode;
  plan?: WorkflowPlan | null;
  /** The topic that was searched, for topic_not_found. */
  topic?: string | null;
  sourceSeconds?: number | null;
  /** False when the source can't be reused (no stored URL). */
  canCreate: boolean;
  readOnly: boolean;
  pendingAction: string | null;
  onCreate: (workflow: Workflow, topic?: string) => void;
  onResume: () => void;
}) {
  const t = useTranslations("task");
  const details = plan?.error?.code === code ? plan.error.details : undefined;
  const detailTopic = typeof details?.topic === "string" ? details.topic : "";
  const shownTopic = topic || plan?.topic?.query || detailTopic;
  // Only topics the API would accept, each once, and never the one that just failed.
  const seen = new Set([normalizeTopic(shownTopic).toLocaleLowerCase()]);
  const suggestions = code === "topic_not_found"
    ? (details?.suggested_topics ?? []).flatMap((item) => {
      const suggestion = typeof item === "string" ? normalizeTopic(item) : "";
      const key = suggestion.toLocaleLowerCase();
      if (topicIssue(suggestion) !== null || seen.has(key)) return [];
      seen.add(key);
      return [suggestion];
    })
    : [];
  const mentions = code === "topic_not_found"
    ? (details?.mentions ?? []).filter((mention): mention is string => typeof mention === "string" && mention.trim() !== "")
    : [];
  let action = failureAction(code, sourceSeconds);
  if (action.kind === "create" && !canCreate) action = { kind: "home" };
  const busy = pendingAction !== null;
  // Each code's CTA names its usual next step; a source too short for a recap gets highlights instead.
  const usual = failureAction(code);
  const ctaLabel = action.kind === "create" && usual.kind === "create" && action.workflow !== usual.workflow
    ? t("workflowErrors.highlightsInstead")
    : t(`workflowErrors.${code}.cta`);

  const primary = readOnly ? null
    : action.kind === "resume" ? (
      <Button onClick={onResume} disabled={busy}>
        {pendingAction === "resume" ? <Loader2 className="size-4 animate-spin" /> : <RotateCcw className="size-4" />}{t(`workflowErrors.${code}.cta`)}
      </Button>
    ) : action.kind === "create" ? (
      <Button onClick={() => onCreate(action.workflow)} disabled={busy}>
        {pendingAction === createActionId(action.workflow) ? <Loader2 className="size-4 animate-spin" /> : <Clapperboard className="size-4" />}{ctaLabel}
      </Button>
    ) : null;

  return (
    <div className="flex flex-col items-center rounded-2xl border border-dashed px-6 py-14 text-center">
      <span className="flex size-14 items-center justify-center rounded-2xl bg-red-50 text-red-600"><AlertCircle className="size-7" /></span>
      <h2 className="mt-4 max-w-xl text-lg font-semibold text-balance">{t(`workflowErrors.${code}.title`, { topic: shownTopic })}</h2>
      <p className="mt-1 max-w-md text-sm text-muted-foreground">{t(`workflowErrors.${code}.body`)}</p>
      {mentions.length > 0 && (
        <p className="mt-1 max-w-md text-xs tabular-nums text-muted-foreground">{t("workflowErrors.topic_not_found.mentions", { times: mentions.slice(0, 5).join(", ") })}</p>
      )}

      {suggestions.length > 0 && !readOnly && canCreate && (
        <div className="mt-6 flex max-w-xl flex-col items-center gap-2.5">
          <p className="text-sm font-medium">{t("workflowErrors.topic_not_found.suggestionsLabel")}</p>
          <ul className="flex flex-wrap justify-center gap-2">
            {suggestions.slice(0, 6).map((suggestion) => (
              <li key={suggestion}>
                <Button size="sm" variant="outline" className="rounded-full" disabled={busy} onClick={() => onCreate("topic", suggestion)}>
                  {pendingAction === createActionId("topic", suggestion) ? <Loader2 className="size-3.5 animate-spin" /> : <Search className="size-3.5" />}{suggestion}
                </Button>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="mt-6 flex flex-wrap justify-center gap-2">
        {primary}
        <Button asChild variant={primary ? "ghost" : "outline"}>
          <Link href="/"><ArrowLeft className="size-4" />{code === "transcript_untimed" ? t("workflowErrors.transcript_untimed.cta") : t("backHome")}</Link>
        </Button>
      </div>
    </div>
  );
}
