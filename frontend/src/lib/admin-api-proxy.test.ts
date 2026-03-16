import { proxyAdminApiRequest, requireAdminSession } from "./admin-api-proxy";
import { fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/backend-api", async () => {
  const actual = await vi.importActual<typeof import("@/server/backend-api")>(
    "@/server/backend-api",
  );
  return {
    ...actual,
    fetchBackend: vi.fn(),
  };
});

describe("admin-api-proxy", () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  it("rejects non-admin users", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1", is_admin: false },
    } as never);

    const result = await requireAdminSession();

    expect(result.error?.status).toBe(403);
  });

  it("proxies requests for admins", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "admin-1", is_admin: true },
    } as never);
    vi.mocked(fetchBackend).mockResolvedValue(
      new Response(JSON.stringify({ accounts: [] }), {
        status: 200,
        headers: {
          "Content-Type": "application/json",
          "x-trace-id": "trace-admin",
        },
      }),
    );

    const response = await proxyAdminApiRequest(
      new Request("http://localhost/api/admin/youtube-cookie-accounts"),
      ["youtube-cookie-accounts"],
    );

    expect(fetchBackend).toHaveBeenCalledWith(
      "/admin/youtube-cookie-accounts",
      expect.objectContaining({
        userId: "admin-1",
      }),
    );
    expect(response.status).toBe(200);
    expect(response.headers.get("x-trace-id")).toBe("trace-admin");
  });
});
