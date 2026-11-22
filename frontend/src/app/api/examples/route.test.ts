import { afterEach, describe, expect, it, vi } from "vitest";

import fixture from "@/test/fixtures/examples-manifest.json";
import { LOCAL_EXAMPLE_CLIPS } from "@/lib/example-gallery";
import { GET } from "./route";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("GET /api/examples", () => {
  it("returns curated, interleaved clips from the examples manifest with cache headers", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(Response.json(fixture)));

    const response = await GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toContain("s-maxage=3600");

    const body = await response.json();
    expect(body.source).toBe("manifest");
    expect(body.clips).toHaveLength(3);
    expect(body.clips.map((clip: { group: string }) => clip.group)).toEqual(["gaming", "interview", "gaming"]);
    expect(body.clips[0]).toEqual({
      id: "gta-6-better-than-we-thought-1",
      title: "Who knew my landlord was my ex",
      group: "gaming",
      posterUrl: "https://media.example.test/examples/v1/gta-6-better-than-we-thought/clip-1.jpg",
      videoUrl: "https://media.example.test/examples/v1/gta-6-better-than-we-thought/clip-1.mp4",
      viralityScore: 92,
      durationSeconds: 45,
    });
  });

  it("falls back to bundled local clips instead of returning 500 when the manifest is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    const response = await GET();
    expect(response.status).toBe(200);
    expect(response.headers.get("cache-control")).toContain("max-age=60");

    const body = await response.json();
    expect(body).toEqual({ clips: LOCAL_EXAMPLE_CLIPS, source: "fallback" });
    expect(body.clips).toHaveLength(6);
  });

  it("falls back to bundled local clips on non-200 manifest responses", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Unavailable", { status: 503 })));

    const response = await GET();
    expect(response.status).toBe(200);
    const body = await response.json();
    expect(body.source).toBe("fallback");
    expect(body.clips).toEqual(LOCAL_EXAMPLE_CLIPS);
  });
});
