/** YouTube thumbnail URL derived client-side from a source URL — no backend work needed. */
export function youTubeThumbnailUrl(sourceUrl: string | null | undefined): string | null {
  if (!sourceUrl) return null;
  const match = sourceUrl.match(
    /(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{11})/,
  );
  return match ? `https://img.youtube.com/vi/${match[1]}/hqdefault.jpg` : null;
}
