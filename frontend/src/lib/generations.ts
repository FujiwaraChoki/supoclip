export interface GenerationSummary {
  id: string;
  user_id: string;
  source_id: string;
  source_title: string;
  source_type: string;
  source_url?: string | null;
  status: string;
  clips_count: number;
  top_virality_score?: number | null;
  cover_clip_id?: string | null;
  created_at: string;
  updated_at: string;
}

export async function fetchGenerations(): Promise<GenerationSummary[]> {
  const response = await fetch("/api/tasks/", { cache: "no-store" });
  if (!response.ok) throw new Error(`Failed to fetch tasks: ${response.status}`);
  const data = await response.json();
  return (data.tasks || []) as GenerationSummary[];
}

export function coverClipUrl(generation: Pick<GenerationSummary, "id" | "cover_clip_id">) {
  return generation.cover_clip_id ? `/api/tasks/${generation.id}/clips/${generation.cover_clip_id}/file` : null;
}

export function formatClipDuration(seconds: number) {
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  return `${mins}:${secs.toString().padStart(2, "0")}`;
}

const relative = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const UNITS: [Intl.RelativeTimeFormatUnit, number][] = [
  ["year", 31_536_000], ["month", 2_592_000], ["week", 604_800],
  ["day", 86_400], ["hour", 3_600], ["minute", 60],
];

export function timeAgo(value: string) {
  const seconds = (new Date(value).getTime() - Date.now()) / 1000;
  for (const [unit, size] of UNITS) {
    if (Math.abs(seconds) >= size) return relative.format(Math.round(seconds / size), unit);
  }
  return "just now";
}

/** Tailwind classes for a 0–100 virality score. */
export function scoreTone(score: number) {
  if (score >= 80) return "text-brand";
  if (score >= 60) return "text-amber-600";
  return "text-muted-foreground";
}

export function greeting(name?: string | null) {
  const hour = new Date().getHours();
  const part = hour < 5 ? "Up late" : hour < 12 ? "Good morning" : hour < 18 ? "Good afternoon" : "Good evening";
  const first = name?.trim().split(/\s+/)[0];
  return first ? `${part}, ${first}` : part;
}
