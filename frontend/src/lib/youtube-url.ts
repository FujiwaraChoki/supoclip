// The one YouTube URL parser on the web app. The backend mirrors it in
// `youtube_utils.get_youtube_video_id`, which canonicalizes accepted links.

const YOUTUBE_ID = /^[A-Za-z0-9_-]{11}$/;
const PATH_MARKERS = new Set(["embed", "shorts", "live", "v"]);

/** youtube.com, youtu.be and youtube-nocookie.com, with any subdomain (www, m, music). */
export function isYouTubeHost(hostname: string): boolean {
  const host = hostname.toLowerCase().replace(/^www\./, "");
  return host === "youtu.be"
    || host === "youtube.com"
    || host === "youtube-nocookie.com"
    || host.endsWith(".youtube.com")
    || host.endsWith(".youtube-nocookie.com");
}

/** Canonical 11-character id for watch, share, shorts, live, and embed URLs. */
export function youtubeVideoId(value: string): string | null {
  const input = value.trim();
  if (!input) return null;

  let parsed: URL;
  try {
    parsed = new URL(input);
  } catch {
    return null;
  }
  if (!["http:", "https:"].includes(parsed.protocol) || !isYouTubeHost(parsed.hostname)) return null;

  const parts = parsed.pathname.split("/").filter(Boolean);
  if (parsed.hostname.toLowerCase().replace(/^www\./, "") === "youtu.be") {
    return parts[0] && YOUTUBE_ID.test(parts[0]) ? parts[0] : null;
  }

  const fromSearch = parsed.searchParams.get("v");
  if (fromSearch && YOUTUBE_ID.test(fromSearch)) return fromSearch;

  const [marker, id] = parts;
  return marker && PATH_MARKERS.has(marker.toLowerCase()) && id && YOUTUBE_ID.test(id) ? id : null;
}

/** `https://www.youtube.com/watch?v=ID` for any supported YouTube link, else null. */
export function canonicalYouTubeUrl(value: string): string | null {
  const id = youtubeVideoId(value);
  return id ? `https://www.youtube.com/watch?v=${id}` : null;
}
