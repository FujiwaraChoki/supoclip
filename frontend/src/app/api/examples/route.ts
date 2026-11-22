import { NextResponse } from "next/server";

import {
  LOCAL_EXAMPLE_CLIPS,
  selectUpgradeGalleryClips,
  type ExamplesGalleryPayload,
} from "@/lib/example-gallery";
import { getExamplesCatalog } from "@/lib/examples";

export const revalidate = 3600;

/**
 * Lightweight cached feed of real SupoClip example clips for the upgrade modal gallery.
 * Gracefully falls back to bundled local clips if the remote examples manifest is unreachable.
 */
export async function GET() {
  try {
    const catalog = await getExamplesCatalog();
    const clips = selectUpgradeGalleryClips(catalog, 6);
    if (clips.length > 0) {
      const payload: ExamplesGalleryPayload = { clips, source: "manifest" };
      return NextResponse.json(payload, {
        headers: { "Cache-Control": "public, s-maxage=3600, stale-while-revalidate=86400" },
      });
    }
  } catch {
    // Fall back quietly so local dev, offline environments and tests never see a 500.
  }

  const fallback: ExamplesGalleryPayload = { clips: LOCAL_EXAMPLE_CLIPS, source: "fallback" };
  return NextResponse.json(fallback, {
    headers: { "Cache-Control": "public, max-age=60, s-maxage=60" },
  });
}
