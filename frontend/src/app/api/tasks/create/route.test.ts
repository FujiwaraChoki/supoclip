import { headers } from "next/headers";

import { POST } from "./route";
import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

vi.mock("next/headers", () => ({
  headers: vi.fn(),
}));

vi.mock("@/lib/auth", () => ({
  auth: {
    api: {
      getSession: vi.fn(),
    },
  },
}));

vi.mock("@/lib/backend-auth", () => ({
  buildBackendAuthHeaders: vi.fn(),
}));

describe("/api/tasks/create", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.stubGlobal("fetch", vi.fn());
    vi.mocked(headers).mockResolvedValue(new Headers());
  });

  it("returns 401 when unauthenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as never);

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
    vi.mocked(auth.api.getSession).mockResolvedValue({
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
