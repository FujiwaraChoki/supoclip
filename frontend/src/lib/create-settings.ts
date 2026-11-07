import { isClipLength, type ClipLength } from "@/lib/clip-duration";
import { isWorkflow, type Workflow, type WorkflowSettingValue } from "@/lib/workflows";

export const OUTPUT_FORMATS = ["vertical", "vertical_fit", "vertical_pan", "vertical_split", "original"] as const;
export type OutputFormat = (typeof OUTPUT_FORMATS)[number];
export const LENGTH_WORKFLOWS = ["series", "recap", "trailer"] as const;
export type LengthWorkflow = (typeof LENGTH_WORKFLOWS)[number];

/** What the create screen remembers between videos. Anything about one video (topic, instructions, part count) stays out. */
export type CreateSettings = {
  workflow?: Workflow;
  captionTemplate?: string;
  addSubtitles?: boolean;
  outputFormat?: OutputFormat;
  includeBroll?: boolean;
  cutLongPauses?: boolean;
  pauseThresholdMs?: number;
  removeFillerWords?: boolean;
  filteredWords?: string;
  lengths?: Partial<Record<LengthWorkflow, ClipLength>>;
  advanced?: Partial<Record<Workflow, Record<string, WorkflowSettingValue>>>;
};

const MAX_ADVANCED_SETTINGS = 40;
const SETTING_KEY = /^[a-z][a-z0-9_]{0,63}$/;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** Keeps only what is well formed: stored data can be older than this code, and the body of a request can be anything. */
export function parseCreateSettings(value: unknown): CreateSettings {
  if (!isRecord(value)) return {};
  const parsed: CreateSettings = {};
  if (isWorkflow(value.workflow)) parsed.workflow = value.workflow;
  if (typeof value.captionTemplate === "string" && value.captionTemplate && value.captionTemplate.length <= 64) parsed.captionTemplate = value.captionTemplate;
  if (typeof value.addSubtitles === "boolean") parsed.addSubtitles = value.addSubtitles;
  if (OUTPUT_FORMATS.includes(value.outputFormat as OutputFormat)) parsed.outputFormat = value.outputFormat as OutputFormat;
  if (typeof value.includeBroll === "boolean") parsed.includeBroll = value.includeBroll;
  if (typeof value.cutLongPauses === "boolean") parsed.cutLongPauses = value.cutLongPauses;
  if (typeof value.pauseThresholdMs === "number" && Number.isInteger(value.pauseThresholdMs) && value.pauseThresholdMs >= 250 && value.pauseThresholdMs <= 3000) parsed.pauseThresholdMs = value.pauseThresholdMs;
  if (typeof value.removeFillerWords === "boolean") parsed.removeFillerWords = value.removeFillerWords;
  if (typeof value.filteredWords === "string" && value.filteredWords.length <= 2000) parsed.filteredWords = value.filteredWords;
  if (isRecord(value.lengths)) {
    const lengths: CreateSettings["lengths"] = {};
    for (const workflow of LENGTH_WORKFLOWS) {
      const length = value.lengths[workflow];
      if (isClipLength(length)) lengths[workflow] = length;
    }
    if (Object.keys(lengths).length) parsed.lengths = lengths;
  }
  if (isRecord(value.advanced)) {
    const advanced: NonNullable<CreateSettings["advanced"]> = {};
    for (const [workflow, settings] of Object.entries(value.advanced)) {
      if (!isWorkflow(workflow) || !isRecord(settings)) continue;
      const kept: Record<string, WorkflowSettingValue> = {};
      for (const [key, setting] of Object.entries(settings).slice(0, MAX_ADVANCED_SETTINGS)) {
        const scalar = typeof setting === "boolean" || (typeof setting === "number" && Number.isFinite(setting)) || (typeof setting === "string" && setting.length <= 64);
        if (SETTING_KEY.test(key) && scalar) kept[key] = setting as WorkflowSettingValue;
      }
      if (Object.keys(kept).length) advanced[workflow] = kept;
    }
    if (Object.keys(advanced).length) parsed.advanced = advanced;
  }
  return parsed;
}
