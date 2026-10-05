import { POST } from "./route";
import { getPrismaClient } from "@/server/prisma";
import { getServerSession } from "@/server/session";

vi.mock("@/server/session", () => ({
  getServerSession: vi.fn(),
}));

vi.mock("@/server/prisma", () => ({
  getPrismaClient: vi.fn(),
}));

const capturedAt = "2026-10-05T10:00:00.000Z";
const request = (body: unknown) =>
  new Request("http://localhost/api/attribution", { method: "POST", body: JSON.stringify(body) });

describe("POST /api/attribution", () => {
  const createMany = vi.fn();

  beforeEach(() => {
    vi.resetAllMocks();
    vi.mocked(getPrismaClient).mockReturnValue({ userAcquisition: { createMany } } as never);
  });

  it("returns 401 without a session", async () => {
    vi.mocked(getServerSession).mockResolvedValue(null);
    expect((await POST(request({ captured_at: capturedAt }))).status).toBe(401);
  });

  it("rejects invalid payloads", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    expect((await POST(request({ utm_source: "x" }))).status).toBe(400);
  });

  it("does not attribute accounts older than the sign-up window", async () => {
    vi.mocked(getServerSession).mockResolvedValue({
      user: { id: "user-1", createdAt: new Date(Date.now() - 2 * 24 * 60 * 60 * 1000) },
    } as never);

    const response = await POST(request({ captured_at: capturedAt, utm_source: "reddit" }));

    await expect(response.json()).resolves.toEqual({ stored: false, reason: "existing_user" });
    expect(createMany).not.toHaveBeenCalled();
  });

  it("stores sanitized first-touch data for new accounts without overwriting", async () => {
    vi.mocked(getServerSession).mockResolvedValue({ user: { id: "user-1", createdAt: new Date() } } as never);
    createMany.mockResolvedValue({ count: 1 });

    const response = await POST(
      request({ captured_at: capturedAt, utm_source: "Reddit", utm_campaign: "launch", landing_path: "/demo", is_admin: true }),
    );

    await expect(response.json()).resolves.toEqual({ stored: true });
    expect(createMany).toHaveBeenCalledWith({
      data: [{
        user_id: "user-1",
        utm_source: "reddit",
        utm_campaign: "launch",
        landing_path: "/demo",
        first_seen_at: new Date(capturedAt),
      }],
      skipDuplicates: true,
    });
  });
});
