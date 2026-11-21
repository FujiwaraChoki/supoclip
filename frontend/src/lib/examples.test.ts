import { afterEach, describe, expect, it, vi } from "vitest";

import fixture from "@/test/fixtures/examples-manifest.json";
import { exampleClips, formatClipLength, getExamplesCatalog, parseExamplesManifest } from "./examples";

const clone = () => structuredClone(fixture) as typeof fixture & Record<string, unknown>;

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("parseExamplesManifest", () => {
  it("maps the manifest to sources and clips", () => {
    const catalog = parseExamplesManifest(fixture);
    expect(catalog.sources.map((source) => [source.slug, source.group, source.captionStyle])).toEqual([
      ["how-drugs-get-into-prison", "interview", "hormozi"],
      ["gta-6-better-than-we-thought", "gaming", "tiktok"],
    ]);
    expect(exampleClips(catalog)).toHaveLength(3);
    expect(catalog.sources[0].clips[0]).toMatchObject({
      title: "What one smuggled phone actually pays",
      viralityScore: 92,
      videoUrl: "https://media.example.test/examples/v1/how-drugs-get-into-prison/clip-1.mp4",
      posterUrl: "https://media.example.test/examples/v1/how-drugs-get-into-prison/clip-1.jpg",
    });
  });

  it("refuses an empty manifest", () => {
    expect(() => parseExamplesManifest({ ...clone(), videos: [] })).toThrow(/has no videos/);
    const noClips = clone();
    noClips.videos[0].clips = [];
    expect(() => parseExamplesManifest(noClips)).toThrow(/has no clips/);
  });

  it("refuses media outside the manifest's host, plain http and unknown categories", () => {
    const elsewhere = clone();
    elsewhere.videos[0].clips[0].video_url = "https://elsewhere.example/clip.mp4";
    expect(() => parseExamplesManifest(elsewhere)).toThrow(/unexpected host/);

    const insecure = clone();
    insecure.videos[0].clips[0].poster_url = "http://media.example.test/examples/v1/a.jpg";
    expect(() => parseExamplesManifest(insecure)).toThrow(/https/);

    const category = clone();
    category.videos[0].group = "cooking";
    expect(() => parseExamplesManifest(category)).toThrow(/group/);

    const source = clone();
    source.videos[0].source_url = "https://example.com/watch";
    expect(() => parseExamplesManifest(source)).toThrow(/source_url/);
  });
});

describe("getExamplesCatalog", () => {
  it("fails loudly when the manifest is unreachable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));
    await expect(getExamplesCatalog()).rejects.toThrow(/could not fetch .*fetch failed/);
  });

  it("fails loudly on an HTTP error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("Not found", { status: 404 })));
    await expect(getExamplesCatalog()).rejects.toThrow(/HTTP 404/);
  });

  it("revalidates hourly", async () => {
    const fetchMock = vi.fn().mockResolvedValue(Response.json(fixture));
    vi.stubGlobal("fetch", fetchMock);
    await expect(getExamplesCatalog()).resolves.toMatchObject({ generatedAt: "2026-11-21" });
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/examples\/v1\/manifest\.json$/), { next: { revalidate: 3600 } });
  });
});

it("formats clip lengths as m:ss", () => {
  expect(formatClipLength(37.4)).toBe("0:37");
  expect(formatClipLength(59.6)).toBe("1:00");
  expect(formatClipLength(125)).toBe("2:05");
});
