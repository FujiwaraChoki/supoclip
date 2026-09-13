import { POST } from "./route";
import { getServerSession } from "@/server/session";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/lib/backend-auth", () => ({
  buildBackendAuthHeaders: vi.fn(),
}));

describe("/api/tasks/create", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("fetch", vi.fn());
  });

  it("returns 401 when there's no session at all (e.g. REQUIRE_AUTH=true and no login)", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null as never);

    const response = await POST(
      new Request("http://localhost/api/tasks/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ source: { url: "https://example.com/video.mp4" } }),
      }),
    );

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "Unauthorized" });
  });

  it("proxies task creation to the backend", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    vi.mocked(buildBackendAuthHeaders).mockReturnValue({
      "x-supoclip-user-id": "user-1",
    });
    vi.mocked(fetch).mockResolvedValue(
      new Response(JSON.stringify({ task_id: "task-1" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );

    const payload = {
      source: { url: "https://www.youtube.com/watch?v=demo" },
    };

    const response = await POST(
      new Request("http://localhost/api/tasks/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      }),
    );

    expect(fetch).toHaveBeenCalledWith(
      "http://localhost:8000/tasks/",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify(payload),
        headers: expect.objectContaining({
          "Content-Type": "application/json",
          "x-supoclip-user-id": "user-1",
        }),
      }),
    );
    expect(response.status).toBe(200);
  });
});
