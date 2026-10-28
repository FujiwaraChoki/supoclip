import type { GenerationSummary } from "@/lib/generations";
import { youtubeVideoId } from "@/lib/youtube-url";

export type LibrarySort = "newest" | "video";

export interface OriginalVideoGroup {
  key: string;
  title: string;
  tasks: GenerationSummary[];
}

/**
 * Identity of the source a generation was cut from.
 * Repeat YouTube runs share a video id. Repeat runs of one stored upload share its file URL.
 */
export function originalVideoKey(generation: Pick<GenerationSummary, "source_id" | "source_url">) {
  const url = generation.source_url?.trim();
  if (url) {
    const videoId = youtubeVideoId(url);
    if (videoId) return `yt:${videoId}`;
    return `url:${url}`;
  }
  return `source:${generation.source_id}`;
}

function createdAt(generation: GenerationSummary) {
  const time = Date.parse(generation.created_at);
  return Number.isNaN(time) ? 0 : time;
}

const GENERIC_TITLES = new Set(["uploaded video", "youtube video", "twitch video", "kick video", "untitled video", "untitled"]);
const UUID_STEM = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const HEX_STEM = /^[0-9a-f]{16,}$/i;

/** A human filename stored on an upload URL. Synthetic storage names are not one. */
export function originalFileName(url: string | null | undefined): string | null {
  if (!url?.startsWith("upload://")) return null;
  const name = url.slice("upload://".length).split("/").filter(Boolean).pop();
  if (!name) return null;
  const stem = name.replace(/\.[^.]+$/, "");
  if (!stem || stem.toLowerCase() === "video" || UUID_STEM.test(stem) || HEX_STEM.test(stem)) return null;
  try {
    return decodeURIComponent(name);
  } catch {
    return name;
  }
}

function uniqueVideoLabel(generation: GenerationSummary) {
  const url = generation.source_url?.trim() ?? "";
  const videoId = url ? youtubeVideoId(url) : null;
  if (videoId) return videoId;

  if (url.startsWith("upload://")) {
    const parts = url.slice("upload://".length).replace(/^s3\//, "").split("/").filter(Boolean);
    const file = parts[parts.length - 1];
    if (parts.length >= 2 && file && /^video\.[a-z0-9]+$/i.test(file)) {
      return parts[parts.length - 2] ?? generation.source_id;
    }
    if (file) return file.replace(/\.[^.]+$/, "");
  }

  return generation.source_id;
}

/** Video title, else the original filename, else an id that belongs to that file. */
export function originalVideoHeading(tasks: GenerationSummary[]) {
  const chronological = [...tasks].sort((a, b) => createdAt(a) - createdAt(b));
  for (const task of chronological) {
    const title = task.source_title?.trim();
    if (title && !GENERIC_TITLES.has(title.toLowerCase())) return title;
  }
  for (const task of chronological) {
    const filename = originalFileName(task.source_url);
    if (filename) return filename;
  }
  return chronological[0] ? uniqueVideoLabel(chronological[0]) : "Untitled video";
}

/**
 * Cluster generations of the same original. Groups follow the newest run in each
 * cluster; runs inside a cluster stay newest first.
 */
export function groupByOriginalVideo(tasks: GenerationSummary[]): OriginalVideoGroup[] {
  const buckets = new Map<string, GenerationSummary[]>();
  for (const task of tasks) {
    const key = originalVideoKey(task);
    const bucket = buckets.get(key);
    if (bucket) bucket.push(task);
    else buckets.set(key, [task]);
  }

  return [...buckets.entries()]
    .map(([key, items]) => {
      const sorted = [...items].sort((a, b) => createdAt(b) - createdAt(a));
      return {
        key,
        title: originalVideoHeading(sorted),
        tasks: sorted,
      };
    })
    .sort((a, b) => createdAt(b.tasks[0]) - createdAt(a.tasks[0]) || a.title.localeCompare(b.title));
}
