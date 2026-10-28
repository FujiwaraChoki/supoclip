import { describe, expect, it } from "vitest";
import { canonicalYouTubeUrl, youtubeVideoId } from "./youtube-url";

// The Shorts link from issue #287, rejected by the Create form before the parsers were unified.
const id = "ai1gIWacf0I";

describe("youtubeVideoId", () => {
  it.each([
    `https://www.youtube.com/shorts/${id}`,
    `https://youtube.com/shorts/${id}?si=Q2s9xYz`,
    `https://m.youtube.com/shorts/${id}/`,
    `https://www.youtube.com/watch?v=${id}`,
    `https://www.youtube.com/watch?feature=share&v=${id}&t=30`,
    `https://youtube.com/watch?v=${id}&list=PL123`,
    `https://m.youtube.com/watch?v=${id}`,
    `https://music.youtube.com/watch?v=${id}&si=abc`,
    `https://youtu.be/${id}`,
    `https://youtu.be/${id}?si=abc&t=12`,
    `https://www.youtube.com/live/${id}?feature=share`,
    `https://www.youtube.com/embed/${id}?start=10`,
    `https://www.youtube-nocookie.com/embed/${id}`,
    `http://WWW.YOUTUBE.COM/shorts/${id}`,
    `  https://www.youtube.com/shorts/${id}  `,
  ])("reads the video id from %s", (url) => {
    expect(youtubeVideoId(url)).toBe(id);
    expect(canonicalYouTubeUrl(url)).toBe(`https://www.youtube.com/watch?v=${id}`);
  });

  it.each([
    "",
    "upload://s3/owner/file.mp4",
    `https://example.com/watch?v=${id}`,
    `https://example.com/?u=https://www.youtube.com/watch?v=${id}`,
    `https://notyoutube.com/shorts/${id}`,
    `ftp://www.youtube.com/watch?v=${id}`,
    "https://www.youtube.com/shorts/tooShort",
    "https://www.youtube.com/@creator",
    "https://www.youtube.com/watch?v=",
  ])("rejects %s", (url) => {
    expect(youtubeVideoId(url)).toBeNull();
    expect(canonicalYouTubeUrl(url)).toBeNull();
  });
});
