import { describe, expect, it } from "vitest";
import type { GenerationSummary } from "./generations";
import { groupByOriginalVideo, originalFileName, originalVideoKey, youtubeVideoId } from "./library-groups";

function generation(overrides: Partial<GenerationSummary> & Pick<GenerationSummary, "id" | "created_at">): GenerationSummary {
  return {
    user_id: "user-1",
    source_id: `source-${overrides.id}`,
    source_title: "Untitled video",
    source_type: "youtube",
    status: "completed",
    clips_count: 2,
    updated_at: overrides.created_at,
    ...overrides,
  };
}

describe("youtubeVideoId", () => {
  it("reads the same id from watch, share, shorts, and embed urls", () => {
    const id = "dQw4w9WgXcQ";
    expect(youtubeVideoId(`https://www.youtube.com/watch?v=${id}&t=30`)).toBe(id);
    expect(youtubeVideoId(`https://youtu.be/${id}`)).toBe(id);
    expect(youtubeVideoId(`https://www.youtube.com/shorts/${id}`)).toBe(id);
    expect(youtubeVideoId(`https://www.youtube.com/embed/${id}`)).toBe(id);
    expect(youtubeVideoId(`https://m.youtube.com/live/${id}`)).toBe(id);
  });

  it("ignores non-youtube urls", () => {
    expect(youtubeVideoId("upload://s3/owner/file.mp4")).toBeNull();
    expect(youtubeVideoId("https://example.com/watch?v=dQw4w9WgXcQ")).toBeNull();
  });
});

describe("groupByOriginalVideo", () => {
  const video = "dQw4w9WgXcQ";

  it("clusters repeat runs of one youtube video and one stored upload", () => {
    const groups = groupByOriginalVideo([
      generation({ id: "old-yt", created_at: "2026-10-01T00:00:00Z", source_title: "Podcast", source_url: `https://youtu.be/${video}` }),
      generation({ id: "other", created_at: "2026-10-03T00:00:00Z", source_title: "Other show", source_url: "https://www.youtube.com/watch?v=abcdefghijk" }),
      generation({ id: "new-yt", created_at: "2026-10-04T00:00:00Z", source_title: "Podcast (recut)", source_url: `https://www.youtube.com/watch?v=${video}` }),
      generation({ id: "upload-a", created_at: "2026-10-02T00:00:00Z", source_type: "video_url", source_title: "Uploaded Video", source_url: "upload://s3/owner/a.mp4" }),
      generation({ id: "upload-b", created_at: "2026-10-02T01:00:00Z", source_type: "video_url", source_title: "Uploaded Video", source_url: "upload://s3/owner/a.mp4" }),
      generation({ id: "upload-c", created_at: "2026-10-02T02:00:00Z", source_type: "video_url", source_title: "Uploaded Video", source_url: "upload://s3/owner/b.mp4" }),
    ]);

    expect(groups.map((group) => group.tasks.map((task) => task.id))).toEqual([
      ["new-yt", "old-yt"],
      ["other"],
      ["upload-c"],
      ["upload-b", "upload-a"],
    ]);
    expect(groups[0]?.title).toBe("Podcast");
    expect(originalVideoKey(groups[0].tasks[0])).toBe(originalVideoKey(groups[0].tasks[1]));
  });

  it("heads a group with the original title, filename, or a file-specific id", () => {
    const titled = groupByOriginalVideo([
      generation({ id: "renamed", created_at: "2026-10-04T00:00:00Z", source_title: "Podcast (recut)", source_url: "https://youtu.be/dQw4w9WgXcQ" }),
      generation({ id: "original", created_at: "2026-10-01T00:00:00Z", source_title: "Podcast", source_url: "https://www.youtube.com/watch?v=dQw4w9WgXcQ" }),
    ]);
    expect(titled[0]?.title).toBe("Podcast");

    const namedFile = groupByOriginalVideo([
      generation({ id: "file", created_at: "2026-10-01T00:00:00Z", source_type: "video_url", source_title: "Uploaded Video", source_url: "upload://interview.mp4" }),
    ]);
    expect(namedFile[0]?.title).toBe("interview.mp4");
    expect(originalFileName("upload://s3/owner/abc/video.mp4")).toBeNull();

    const unnamed = groupByOriginalVideo([
      generation({
        id: "stored",
        created_at: "2026-10-01T00:00:00Z",
        source_type: "video_url",
        source_title: "Uploaded Video",
        source_url: "upload://s3/ownerhash/abc123def4567890/video.mp4",
      }),
      generation({
        id: "other-file",
        created_at: "2026-10-02T00:00:00Z",
        source_type: "video_url",
        source_title: "YouTube Video",
        source_url: "https://www.youtube.com/watch?v=abcdefghijk",
      }),
    ]);
    expect(unnamed.map((group) => group.title)).toEqual(["abcdefghijk", "abc123def4567890"]);
  });

  it("keeps generations without a url on their own source", () => {
    const groups = groupByOriginalVideo([
      generation({ id: "a", created_at: "2026-10-02T00:00:00Z", source_id: "same", source_title: "Uploaded Video" }),
      generation({ id: "b", created_at: "2026-10-01T00:00:00Z", source_id: "same", source_title: "Uploaded Video" }),
      generation({ id: "c", created_at: "2026-10-03T00:00:00Z", source_id: "other", source_title: "Uploaded Video" }),
    ]);

    expect(groups.map((group) => group.tasks.map((task) => task.id))).toEqual([["c"], ["a", "b"]]);
  });
});
