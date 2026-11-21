/**
 * Example clips catalog (`/examples`). The clips, posters and manifest live in object storage and are
 * served read-only by a small Worker; the page reads the manifest on the server so the catalog is in the
 * HTML for search engines. A missing or empty manifest throws, so a build without it fails loudly.
 */

export const EXAMPLES_MANIFEST_URL =
  process.env.EXAMPLES_MANIFEST_URL?.trim() || "https://supoclip-examples.sami-c72.workers.dev/examples/v1/manifest.json";

export const EXAMPLE_GROUPS = ["interview", "gaming", "vlog"] as const;
export type ExampleGroup = (typeof EXAMPLE_GROUPS)[number];

export const EXAMPLE_CAPTION_STYLES = ["default", "hormozi", "mrbeast", "minimal", "tiktok", "neon", "podcast"] as const;
export type ExampleCaptionStyle = (typeof EXAMPLE_CAPTION_STYLES)[number];

export type ExampleClip = {
  id: string;
  title: string;
  durationSeconds: number;
  viralityScore: number;
  excerpt: string;
  videoUrl: string;
  posterUrl: string;
  width: number;
  height: number;
};

export type ExampleSource = {
  slug: string;
  group: ExampleGroup;
  title: string;
  channel: string;
  url: string;
  captionStyle: ExampleCaptionStyle;
  clips: ExampleClip[];
};

export type ExamplesCatalog = {
  generatedAt: string;
  sources: ExampleSource[];
};

class ManifestError extends Error {
  constructor(message: string) {
    super(`Examples manifest: ${message}`);
    this.name = "ManifestError";
  }
}

function record(value: unknown, where: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new ManifestError(`${where} is not an object`);
  return value as Record<string, unknown>;
}

function text(value: unknown, where: string): string {
  if (typeof value !== "string" || !value.trim()) throw new ManifestError(`${where} must be a non-empty string`);
  return value.trim();
}

function number(value: unknown, where: string, min: number, max: number): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < min || value > max) {
    throw new ManifestError(`${where} must be a number from ${min} to ${max}`);
  }
  return value;
}

function httpsUrl(value: unknown, where: string, hosts?: string[]): string {
  const raw = text(value, where);
  let url: URL;
  try {
    url = new URL(raw);
  } catch {
    throw new ManifestError(`${where} is not a URL`);
  }
  if (url.protocol !== "https:") throw new ManifestError(`${where} must use https`);
  if (hosts && !hosts.includes(url.hostname)) throw new ManifestError(`${where} points to an unexpected host`);
  return url.toString();
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], where: string): T {
  if (typeof value !== "string" || !(allowed as readonly string[]).includes(value)) {
    throw new ManifestError(`${where} must be one of ${allowed.join(", ")}`);
  }
  return value as T;
}

/** Validates the raw manifest and maps it to the page's shape. Throws on anything unexpected or empty. */
export function parseExamplesManifest(raw: unknown): ExamplesCatalog {
  const manifest = record(raw, "manifest");
  if (manifest.version !== 1) throw new ManifestError("unsupported version");
  const mediaHost = new URL(httpsUrl(manifest.base_url, "base_url")).hostname;
  if (!Array.isArray(manifest.videos) || manifest.videos.length === 0) throw new ManifestError("has no videos");

  const sources = manifest.videos.map((entry, index): ExampleSource => {
    const video = record(entry, `videos[${index}]`);
    const where = `videos[${index}]`;
    if (!Array.isArray(video.clips) || video.clips.length === 0) throw new ManifestError(`${where} has no clips`);
    return {
      slug: text(video.slug, `${where}.slug`),
      group: oneOf(video.group, EXAMPLE_GROUPS, `${where}.group`),
      title: text(video.source_title, `${where}.source_title`),
      channel: text(video.channel, `${where}.channel`),
      url: httpsUrl(video.source_url, `${where}.source_url`, ["www.youtube.com", "youtube.com", "youtu.be"]),
      captionStyle: oneOf(video.caption_template, EXAMPLE_CAPTION_STYLES, `${where}.caption_template`),
      clips: video.clips.map((item, clipIndex): ExampleClip => {
        const clip = record(item, `${where}.clips[${clipIndex}]`);
        const at = `${where}.clips[${clipIndex}]`;
        return {
          id: text(clip.id, `${at}.id`),
          title: text(clip.title, `${at}.title`),
          durationSeconds: number(clip.duration_seconds, `${at}.duration_seconds`, 1, 600),
          viralityScore: Math.round(number(clip.virality_score, `${at}.virality_score`, 0, 100)),
          excerpt: text(clip.transcript_excerpt, `${at}.transcript_excerpt`),
          videoUrl: httpsUrl(clip.video_url, `${at}.video_url`, [mediaHost]),
          posterUrl: httpsUrl(clip.poster_url, `${at}.poster_url`, [mediaHost]),
          width: number(clip.width, `${at}.width`, 1, 4096),
          height: number(clip.height, `${at}.height`, 1, 4096),
        };
      }),
    };
  });

  return { generatedAt: text(manifest.generated_at, "generated_at"), sources };
}

/** Loads the manifest once an hour. Unreachable or invalid fails the build (and keeps the last good page at runtime). */
export async function getExamplesCatalog(): Promise<ExamplesCatalog> {
  let response: Response;
  try {
    response = await fetch(EXAMPLES_MANIFEST_URL, { next: { revalidate: 3600 } });
  } catch (error) {
    throw new ManifestError(`could not fetch ${EXAMPLES_MANIFEST_URL}: ${error instanceof Error ? error.message : String(error)}`);
  }
  if (!response.ok) throw new ManifestError(`${EXAMPLES_MANIFEST_URL} returned HTTP ${response.status}`);
  return parseExamplesManifest(await response.json());
}

export function exampleClips(catalog: ExamplesCatalog) {
  return catalog.sources.flatMap((source) => source.clips.map((clip) => ({ source, clip })));
}

/** 37.4 -> "0:37" */
export function formatClipLength(seconds: number) {
  const total = Math.round(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

/** Media origin for preconnect, taken from the manifest's own URLs. */
export function examplesMediaOrigin(catalog: ExamplesCatalog) {
  return new URL(catalog.sources[0].clips[0].videoUrl).origin;
}
