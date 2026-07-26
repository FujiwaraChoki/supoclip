import { GET } from "./route";
import { fetchBackend } from "@/server/backend-api";

vi.mock("@/server/backend-api", async () => {
  const actual = await vi.importActual<typeof import("@/server/backend-api")>(
    "@/server/backend-api",
  );
  return {
    ...actual,
    fetchBackend: vi.fn(),
  };
});

describe("/api/share/[...path]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("proxies shared results without requiring a session", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ source_title: "Shared result" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const response = await GET(
      new Request("http://localhost/api/share/token-1"),
      { params: Promise.resolve({ path: ["token-1"] }) },
    );

    expect(fetchBackend).toHaveBeenCalledWith(
      "/tasks/shared/token-1",
      expect.objectContaining({ method: "GET", cache: "no-store" }),
    );
    await expect(response.json()).resolves.toEqual({ source_title: "Shared result" });
  });

  it("forwards range headers for shared video playback", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response("video", {
        status: 206,
        headers: { "Content-Type": "video/mp4", "Content-Range": "bytes 0-4/5" },
      }),
    );

    await GET(
      new Request("http://localhost/api/share/token-1/clips/clip-1/file", {
        headers: { Range: "bytes=0-4" },
      }),
      { params: Promise.resolve({ path: ["token-1", "clips", "clip-1", "file"] }) },
    );

    expect(fetchBackend).toHaveBeenCalledWith(
      "/tasks/shared/token-1/clips/clip-1/file",
      expect.objectContaining({
        extraHeaders: expect.objectContaining({ Range: "bytes=0-4" }),
      }),
    );
  });

  it("rejects traversal segments without contacting the backend", async () => {
    const response = await GET(
      new Request("http://localhost/api/share/%2e%2e/%2e%2e/api-keys"),
      { params: Promise.resolve({ path: ["..", "..", "api-keys"] }) },
    );

    expect(response.status).toBe(404);
    expect(fetchBackend).not.toHaveBeenCalled();
  });
});
