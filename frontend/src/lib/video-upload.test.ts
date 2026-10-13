import { uploadVideoFile } from "./video-upload";

describe("S3 video uploads", () => {
  afterEach(() => vi.unstubAllGlobals());

  function authorization() {
    return { storage: "s3", video_path: "upload://s3/owner/id/video.mp4", uploadId: "upload",
      partSize: 3, parts: [{ partNumber: 1, url: "https://s3.test/1" }, { partNumber: 2, url: "https://s3.test/2" }] };
  }

  it("uploads parts directly and completes them in order", async () => {
    const fetchMock = vi.fn(async (url: string, init?: RequestInit) => {
      if (url === "/api/upload/authorization") return Response.json(authorization());
      if (url.startsWith("https://s3.test/")) return new Response(null, { headers: { ETag: url.endsWith("1") ? "first" : "second" } });
      if (url === "/api/upload/complete") {
        expect(JSON.parse(init?.body as string).parts).toEqual([{ partNumber: 1, etag: "first" }, { partNumber: 2, etag: "second" }]);
        return Response.json({ video_path: authorization().video_path });
      }
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["abcdef"], "video.mp4", { type: "video/mp4" });
    await expect(uploadVideoFile(file)).resolves.toBe(authorization().video_path);
    const puts = fetchMock.mock.calls.filter(([url]) => url.startsWith("https://s3.test/"));
    expect(puts).toHaveLength(2);
    expect(puts.every(([, init]) => init?.method === "PUT" && (init.body as Blob).size === 3)).toBe(true);
    expect(puts.every(([, init]) => !init?.headers)).toBe(true);
  });

  it("aborts the multipart upload when a part fails", async () => {
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/upload/authorization") return Response.json(authorization());
      if (url.startsWith("https://s3.test/")) return new Response(null, { status: 503 });
      if (url === "/api/upload/abort") return Response.json({ message: "cancelled" });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(uploadVideoFile(new File(["abcdef"], "video.mp4"))).rejects.toThrow("Video upload failed");
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/upload/abort")).toBe(true);
    expect(fetchMock.mock.calls.some(([url]) => url === "/api/upload/complete")).toBe(false);
  });
});
