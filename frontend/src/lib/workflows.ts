/**
 * Workflows: what a generation makes. Mirrors the backend contracts in
 * `backend/src/workflows/` (request options, `tasks.workflow_plan`,
 * `generated_clips.workflow_meta` and `GET /tasks/workflows`).
 */

export const WORKFLOWS = ["highlights", "series", "topic", "recap", "trailer"] as const;
export type Workflow = (typeof WORKFLOWS)[number];

export function isWorkflow(value: unknown): value is Workflow {
  return typeof value === "string" && WORKFLOWS.includes(value as Workflow);
}

/** Lenient read of a task's workflow: anything missing or unknown is highlights. */
export function taskWorkflow(task: { workflow?: unknown } | null | undefined): Workflow {
  return isWorkflow(task?.workflow) ? task.workflow : "highlights";
}

/** Workflows that make exactly one clip. */
export const SINGLE_OUTPUT: ReadonlySet<Workflow> = new Set<Workflow>(["recap", "trailer"]);
/** Workflows whose clips carry virality scores (score ring, "Top pick", score sort). */
export const SCORED: ReadonlySet<Workflow> = new Set<Workflow>(["highlights", "topic"]);

export const PART_COUNT_OPTIONS = ["auto", 2, 3, 4, 5, 6, 8, 10] as const;
export type PartCountOption = (typeof PART_COUNT_OPTIONS)[number];
export const SERIES_MIN_PARTS = 2;
export const SERIES_MAX_PARTS = 10;

export const TOPIC_MIN_CHARS = 2;
export const TOPIC_MAX_CHARS = 120;
export const INSTRUCTIONS_MAX_CHARS = 500;

// Mirrors `normalize_text` and `topic_is_searchable` in backend/src/workflows/options.py.
const TOPIC_LINE_BREAKS = /[\t\n\v\f\r\u0085\u2028\u2029]/g;
const TOPIC_INVISIBLE = /[\p{Cc}\p{Cs}\u00ad\u180e\u200b\u2060\ufeff\u202a-\u202e\u2066-\u2069]/gu;
const TOPIC_LETTER_OR_DIGIT = /[\p{L}\p{N}]/u;
// One Han, kana or Hangul character is already a word ("钱", "猫").
const ONE_CHARACTER_WORD = /^[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af\u{20000}-\u{3134f}]$/u;

/** The topic as the API stores it: one line, invisible characters removed, spaces collapsed. */
export function normalizeTopic(topic: string): string {
  return topic
    .normalize("NFC")
    .replace(TOPIC_LINE_BREAKS, " ")
    .replace(TOPIC_INVISIBLE, "")
    .replace(/\s+/g, " ")
    .trim();
}

/** The API's topic check, so the form can explain it before submitting. Lengths count characters, not UTF-16 units. */
export function topicIssue(topic: string): "TOPIC_REQUIRED" | "TOPIC_TOO_LONG" | null {
  const normalized = normalizeTopic(topic);
  const length = [...normalized].length;
  if (!TOPIC_LETTER_OR_DIGIT.test(normalized)) return "TOPIC_REQUIRED";
  if (length < TOPIC_MIN_CHARS && !ONE_CHARACTER_WORD.test(normalized)) return "TOPIC_REQUIRED";
  if (length > TOPIC_MAX_CHARS) return "TOPIC_TOO_LONG";
  return null;
}

/** Shortest source, in seconds, each workflow accepts. */
export const MIN_SOURCE_SECONDS: Record<Workflow, number> = {
  highlights: 0,
  series: 120,
  topic: 60,
  recap: 180,
  trailer: 180,
};

/** localStorage key for the last workflow picked on the create screen. */
export const WORKFLOW_STORAGE_KEY = "supoclip.workflow";

/**
 * `workflow_options` as sent to `POST /tasks/` and stored on the task. Send
 * `instructions` at the top level of the request; stored options include it.
 */
export type WorkflowOptions = {
  topic?: string;
  part_count?: number;
  instructions?: string;
};

/** Plan notes, localized from `task.workflowNotes.<CODE>` with `params`. */
export type WorkflowNoteCode =
  | "PART_COUNT_REDUCED"
  | "STORY_ARC_SELECTED"
  | "NO_SINGLE_STORY"
  | "TOPIC_THIN"
  | "TARGET_SHORTENED"
  | "COARSE_TRANSCRIPT"
  | "SPONSOR_SKIPPED";

export type WorkflowNote = {
  code: WorkflowNoteCode;
  params?: Record<string, string | number>;
};

/** Worker failures: the task's `error_code` and `workflow_plan.error.code`. */
export type WorkflowErrorCode =
  | "source_too_short"
  | "no_story"
  | "topic_not_found"
  | "recap_unsupported"
  | "trailer_unbuildable"
  | "analysis_incomplete"
  | "plan_failed"
  | "render_incomplete"
  | "transcript_untimed";

/** `POST /tasks/` rejections (`detail.code`): 422, or 403 for WORKFLOW_NOT_AVAILABLE. */
export type WorkflowRequestErrorCode =
  | "INVALID_WORKFLOW"
  | "INVALID_WORKFLOW_OPTIONS"
  | "TOPIC_REQUIRED"
  | "TOPIC_TOO_LONG"
  | "INVALID_PART_COUNT"
  | "INSTRUCTIONS_TOO_LONG"
  | "WORKFLOW_NOT_AVAILABLE"
  | "SOURCE_TOO_SHORT_FOR_SERIES"
  | "TOO_MANY_PARTS"
  | "SOURCE_TOO_SHORT_FOR_RECAP"
  | "SOURCE_TOO_SHORT_FOR_TRAILER"
  | "SOURCE_TOO_SHORT_FOR_TOPIC";

/** `detail` of a rejected create. TOO_MANY_PARTS adds `max_parts`; the source minimums add `min_source_seconds`. */
export type WorkflowRequestError = {
  code: WorkflowRequestErrorCode;
  message: string;
  max_parts?: number;
  min_source_seconds?: number;
};

/** Seconds in the source video, as [start, end]. */
export type SourceRange = [number, number];

export type WorkflowPlanPart = {
  part_number: number;
  title: string;
  delivers?: string;
  ends_on?: string;
  post_caption?: string;
  first_unit?: number;
  last_unit?: number;
  ranges?: SourceRange[];
  seconds?: number;
};

export type WorkflowPlanPoint = {
  label: string;
  ranges?: SourceRange[];
  /** Seconds into the rendered recap where this point starts. */
  output_start?: number | null;
};

export type TrailerBeatRole = "cold_open" | "context" | "escalation" | "button";

export type WorkflowPlanBeat = {
  role: TrailerBeatRole;
  ranges?: SourceRange[];
  output_start?: number | null;
};

export type WorkflowPlanTopic = {
  query: string;
  canonical: string;
  coverage: "strong" | "thin";
  found: number;
  suggested_topics?: string[];
};

export type WorkflowPlanError = {
  code: WorkflowErrorCode;
  details: {
    suggested_topics?: string[];
    /** "MM:SS" timestamps where the topic is mentioned in passing. */
    mentions?: string[];
    [key: string]: unknown;
  };
};

/**
 * `tasks.workflow_plan`. Shared results (`GET /tasks/shared/{token}`) only
 * carry `version`, `workflow`, `title`, `parts[].{part_number,title}`,
 * `points[].label`, `beats[].role`, `topic.{query,canonical,coverage,found}`
 * and `notes`.
 */
export type WorkflowPlan = {
  version: string;
  workflow: Workflow;
  language?: string | null;
  coarse?: boolean;
  mode?: "full" | "long";
  /** Series, recap or trailer title; the canonical topic for topic runs. */
  title?: string | null;
  summary?: string | null;
  key_topics?: string[];
  parts?: WorkflowPlanPart[];
  points?: WorkflowPlanPoint[];
  beats?: WorkflowPlanBeat[];
  topic?: WorkflowPlanTopic;
  notes?: WorkflowNote[];
  /** Present only when the task failed with a workflow error. */
  error?: WorkflowPlanError;
  stats?: { llm_calls?: number; repairs?: number; critique_fixes?: number; plan_seconds?: number };
};

export type WorkflowRole = "part" | "topic" | "recap" | "trailer";

export type WorkflowMetaPoint = {
  label: string;
  /** opener and closer are labelled "Intro" / "Takeaway" by the UI. */
  kind?: "opener" | "point" | "closer";
  range_index: number;
  output_start: number | null;
};

export type WorkflowMetaBeat = {
  role: TrailerBeatRole;
  range_index: number;
  output_start: number | null;
};

/**
 * `generated_clips.workflow_meta`: `{}` for highlights clips. Shared results
 * only carry `title`, `ends_on`, `points` and `beats`.
 */
export type WorkflowMeta = {
  planned?: boolean;
  ranges?: SourceRange[];
  junction_fades?: number[];
  max_seconds?: number;
  /** Set on both halves after a split. */
  split?: 1 | 2;
  // part
  series_title?: string;
  part_count?: number;
  title?: string;
  delivers?: string;
  ends_on?: string;
  post_caption?: string;
  first_unit?: number;
  last_unit?: number;
  // topic
  topic?: string;
  topic_relevance?: number;
  why?: string;
  // recap / trailer
  points?: WorkflowMetaPoint[];
  beats?: WorkflowMetaBeat[];
};

/** Workflow fields on a clip from `GET /tasks/{id}` and the SSE `clip_ready` payload. */
export type WorkflowClipFields = {
  workflow_role?: WorkflowRole | null;
  part_number?: number | null;
  workflow_meta?: WorkflowMeta;
};

/** Workflow fields on a task from `GET /tasks/{id}`. */
export type WorkflowTaskFields = {
  workflow?: Workflow;
  workflow_options?: WorkflowOptions;
  workflow_plan?: WorkflowPlan | null;
};

/** Workflow fields on a row of `GET /tasks/`. */
export type WorkflowSummaryFields = {
  workflow?: Workflow;
  workflow_title?: string | null;
  part_count?: number | null;
};

export type WorkflowInfo = {
  id: Workflow;
  available: boolean;
  min_source_seconds: number;
  /** series */
  part_count?: { min: number; max: number };
  /** series: part [min, max] seconds; recap: target seconds (auto scales: null); trailer: target seconds. */
  lengths?: Record<"auto" | "short" | "medium" | "long", [number, number] | number | null>;
};

/** `GET /tasks/workflows`. `enabled` false: highlights without instructions only. */
export type WorkflowsInfo = {
  enabled: boolean;
  workflows: WorkflowInfo[];
  topic_max_chars: number;
  instructions_max_chars: number;
};

export function defaultSort(workflow: Workflow): "score" | "timeline" {
  return workflow === "series" ? "timeline" : "score";
}

/** ~1 highlight per 5 minutes of source, capped like the backend's MAX_CLIPS default. */
export function expectedHighlightCount(sourceSeconds?: number | null): number {
  if (!sourceSeconds) return 4;
  return Math.min(10, Math.max(1, Math.round(sourceSeconds / 300)));
}

/** How many clips to hold space for while a generation runs. */
export function expectedOutputs(
  workflow: Workflow,
  sourceSeconds?: number | null,
  partCount?: number | null,
): number {
  if (SINGLE_OUTPUT.has(workflow)) return 1;
  if (workflow === "series") {
    if (partCount) return partCount;
    if (!sourceSeconds) return 4;
    return Math.min(6, Math.max(SERIES_MIN_PARTS, Math.round(sourceSeconds / 90)));
  }
  if (workflow === "topic") return 5;
  return expectedHighlightCount(sourceSeconds);
}
