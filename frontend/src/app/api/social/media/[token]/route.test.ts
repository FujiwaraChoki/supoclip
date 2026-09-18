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

describe("/api/social/media/[token]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects malformed tokens without calling the backend", async () => {
    const response = await GET(new Request("http://localhost/api/social/media/x"), {
      params: Promise.resolve({ token: "../etc/passwd" }),
    });

    expect(response.status).toBe(404);
    expect(fetchBackend).not.toHaveBeenCalled();
  });

  it("streams the backend media response, forwarding range headers", async () => {
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response("video-bytes", {
        status: 206,
        headers: { "Content-Type": "video/mp4", "Content-Range": "bytes 0-10/11" },
      }),
    );
    const token = "abcdefghijklmnopqrstuvwxyz012345";

    const response = await GET(
      new Request(`http://localhost/api/social/media/${token}`, {
        headers: { Range: "bytes=0-10" },
      }),
      { params: Promise.resolve({ token }) },
    );

    expect(fetchBackend).toHaveBeenCalledWith(
      `/social/media/${token}`,
      expect.objectContaining({
        method: "GET",
        extraHeaders: expect.objectContaining({ Range: "bytes=0-10" }),
      }),
    );
    expect(response.status).toBe(206);
    expect(response.headers.get("content-range")).toBe("bytes 0-10/11");
    await expect(response.text()).resolves.toBe("video-bytes");
  });
});
