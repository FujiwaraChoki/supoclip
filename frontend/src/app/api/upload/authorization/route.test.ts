import { headers } from "next/headers";

import { POST } from "./route";
import { auth } from "@/lib/auth";

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

describe("/api/upload/authorization", () => {
  beforeEach(() => {
    vi.resetAllMocks();
    vi.unstubAllEnvs();
    vi.mocked(headers).mockResolvedValue(new Headers());
  });

  it("returns 401 when unauthenticated", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue(null as never);

    const response = await POST();

    expect(response.status).toBe(401);
    await expect(response.json()).resolves.toEqual({ error: "Unauthorized" });
  });

  it("falls back to the server-side proxy when signed backend auth is unavailable", async () => {
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);

    const response = await POST();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      directUpload: false,
      reason: "signed_backend_auth_required",
    });
  });

  it("returns signed direct-upload headers when backend auth is configured", async () => {
    vi.stubEnv("BACKEND_AUTH_SECRET", "secret");
    vi.stubEnv("NEXT_PUBLIC_API_URL", "https://api.supoclip.com/");
    vi.spyOn(Date, "now").mockReturnValue(1_700_000_000_000);
    vi.mocked(auth.api.getSession).mockResolvedValue({
      user: { id: "user-1" },
    } as never);

    const response = await POST();

    expect(response.status).toBe(200);
    await expect(response.json()).resolves.toEqual({
      directUpload: true,
      uploadUrl: "https://api.supoclip.com/upload",
      headers: {
        "x-supoclip-user-id": "user-1",
        "x-supoclip-ts": "1700000000",
        "x-supoclip-signature":
          "cceb65b01012f5596122712d023d1def6663579f457362796a52133b3875c545",
      },
    });
  });
});
