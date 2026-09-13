import { getServerSession } from "@/server/session";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";

import { DELETE } from "./route";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/lib/backend-auth", () => ({
  buildBackendAuthHeaders: vi.fn(),
}));

describe("DELETE /api/fonts/[fontName]", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(buildBackendAuthHeaders).mockReturnValue({
      "x-supoclip-user-id": "user-1",
    });
    process.env.BACKEND_INTERNAL_URL = "http://backend:8000";
  });

  it("returns 401 without a session (e.g. REQUIRE_AUTH=true and no login)", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    const response = await DELETE(new Request("http://localhost/api/fonts/Inter"), {
      params: Promise.resolve({ fontName: "Inter" }),
    });

    expect(response.status).toBe(401);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("forwards an owner-authenticated delete request to the backend", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ message: "Font deleted successfully" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const response = await DELETE(new Request("http://localhost/api/fonts/Brand%20Font"), {
      params: Promise.resolve({ fontName: "Brand Font" }),
    });

    expect(fetchMock).toHaveBeenCalledWith(
      "http://backend:8000/fonts/Brand%20Font",
      expect.objectContaining({
        method: "DELETE",
        headers: { "x-supoclip-user-id": "user-1" },
      }),
    );
    expect(response.status).toBe(200);
  });
});
