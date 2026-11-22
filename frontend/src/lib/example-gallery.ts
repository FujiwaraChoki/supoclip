import articleClipAssets from "@/lib/article-clip-assets.json";
import { DEMO_CLIPS } from "@/lib/demo";
import type { ExampleGroup, ExamplesCatalog } from "@/lib/examples";

/** Slim clip payload served by `/api/examples` and rendered in the upgrade modal gallery. */
export interface GalleryClip {
  id: string;
  title: string;
  group: ExampleGroup;
  posterUrl: string;
  videoUrl: string;
  viralityScore: number | null;
  durationSeconds: number;
}

export interface ExamplesGalleryPayload {
  clips: GalleryClip[];
  source: "manifest" | "fallback";
}

function parseMinutesSeconds(value: string): number {
  const [minutes, seconds] = value.split(":").map(Number);
  if (!Number.isFinite(minutes) || !Number.isFinite(seconds)) return 45;
  return minutes * 60 + seconds;
}

const localPodcastClips: GalleryClip[] = DEMO_CLIPS.map((clip, index) => ({
  id: `local-podcast-${index + 1}`,
  title: clip.hook,
  group: "interview",
  posterUrl: clip.poster,
  videoUrl: clip.src,
  viralityScore: null,
  durationSeconds: parseMinutesSeconds(clip.duration),
}));

const localArticleClips: GalleryClip[] = articleClipAssets.map((clip) => ({
  id: `local-${clip.id}`,
  title: clip.title,
  group: "interview",
  posterUrl: clip.poster,
  videoUrl: clip.src,
  viralityScore: null,
  durationSeconds: Math.round(clip.durationSeconds),
}));

/**
 * Bundled clips under `public/clips/` used to seed the upgrade modal immediately
 * with zero layout shift and as a graceful fallback when the remote manifest is unavailable.
 * Interleaved across source videos so adjacent cards look distinct.
 */
export const LOCAL_EXAMPLE_CLIPS: GalleryClip[] = [
  localPodcastClips[0],
  localArticleClips[0], // price-1
  localArticleClips[3], // ton-1
  localPodcastClips[1],
  localArticleClips[1], // price-2
  localArticleClips[4], // ton-2
  localArticleClips[2], // price-3
].filter((clip): clip is GalleryClip => Boolean(clip));

/** Group order that puts visual variety (gaming, podcasts, vlogs) into the opening viewport. */
const GALLERY_GROUP_ORDER: readonly ExampleGroup[] = ["gaming", "interview", "vlog"];

/**
 * Flattens a parsed `/examples` catalog into slim gallery clips, interleaved across
 * categories and source videos so every column shows a mix of gaming, podcasts and vlogs.
 */
export function flattenCatalogToGalleryClips(catalog: ExamplesCatalog): GalleryClip[] {
  const queuesByGroup = GALLERY_GROUP_ORDER.map((group) => {
    const sourceQueues = catalog.sources
      .filter((source) => source.group === group)
      .map((source) =>
        [...source.clips]
          .sort((a, b) => b.viralityScore - a.viralityScore)
          .map(
            (clip): GalleryClip => ({
              id: clip.id,
              title: clip.title,
              group: source.group,
              posterUrl: clip.posterUrl,
              videoUrl: clip.videoUrl,
              viralityScore: clip.viralityScore,
              durationSeconds: clip.durationSeconds,
            }),
          ),
      );

    // Round-robin across sources within the same group.
    const merged: GalleryClip[] = [];
    let added = true;
    while (added) {
      added = false;
      for (const queue of sourceQueues) {
        const next = queue.shift();
        if (next) {
          merged.push(next);
          added = true;
        }
      }
    }
    return merged;
  }).filter((queue) => queue.length > 0);

  // Round-robin across groups so every visible row mixes categories.
  const result: GalleryClip[] = [];
  let added = true;
  while (added) {
    added = false;
    for (const queue of queuesByGroup) {
      const next = queue.shift();
      if (next) {
        result.push(next);
        added = true;
      }
    }
  }
  return result;
}

/**
 * Distributes clips round-robin into `columnCount` vertical columns and pads any short
 * column to `minItemsPerColumn` so each duplicated marquee half exceeds the viewport height.
 */
export function buildGalleryColumns(clips: GalleryClip[], columnCount = 3, minItemsPerColumn = 4): GalleryClip[][] {
  if (clips.length === 0 || columnCount <= 0) return [];
  const columns: GalleryClip[][] = Array.from({ length: columnCount }, () => []);
  clips.forEach((clip, index) => {
    columns[index % columnCount].push(clip);
  });

  return columns.map((column, colIndex) => {
    const seed = column.length > 0 ? column : [clips[colIndex % clips.length]];
    const padded = [...seed];
    let cursor = 0;
    while (padded.length < minItemsPerColumn) {
      padded.push(seed[cursor % seed.length]);
      cursor += 1;
    }
    return padded;
  });
}
